"""可测试的常驻 IM 发送 worker 循环。

本模块只负责编排数据库队列和已经创建好的发送器。浏览器启动、登录检查、
profile 互斥与 CLI 参数属于下一层启动适配器。
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import os
from pathlib import Path
import signal
import threading
import time
from collections.abc import Callable
from urllib.parse import urlencode, urlsplit

from bdhub import config as cfgmod
from bdhub import scheduled_relogin as scheduled_relogin_svc
from bdhub.enrich.profile_lease import ProfileLease
from bdhub.hub.engine import check_schema, get_engine
from bdhub.hub.markets import MARKETS, identity_for, im_page
from bdhub.hub.repo.im import OutboundMessageProjector
from bdhub.hub.repo.outreach import OutreachStore
from bdhub.hub.repo.send_attribution import SendAttributionRepo
from bdhub.hub.repo.send_tasks import SendTaskRepo
from bdhub.hub.repo.send_workers import SendWorkerRepo
from bdhub.imbase.account_binding import (
    normalize_registered_market,
    require_im_account,
)
from bdhub.imbase.transport import _load_all, _wait_sdk
from bdhub.send.component_sender import (
    ComponentSender,
    ConversationResolution,
    ConversationResolutionError,
)
from bdhub.send.runner import SendTaskRunner
from bdhub.send.transport_scripts import _JS_RESOLVE_TARGETED
from scripts.migrate_resident_send_workers import pending_changes


_PAUSED_STATUSES = frozenset({"paused", "auth_required", "unhealthy"})
_ACCOUNT_PAUSE_CODES = frozenset(
    {
        "auth_required",
        "captcha_required",
        "account_limited",
        "send_profile_not_logged_in",
        "im_sdk_not_ready",
        "im_page_unavailable",
    }
)
_ACTIVE_LEAD_STATUSES = frozenset({"pending", "review_required"})
_TARGETED_IM_LOCK_PATH = (
    cfgmod.ROOT / "data" / "runtime" / "targeted-im" / "global.lock"
)


@contextmanager
def _claim_liveness(repo, worker_id, *, on_progress=None, interval=10, max_seconds=360):
    """有界长等待期间只续心跳，不改状态、不在线程里操作Playwright。"""
    stopped = threading.Event()
    deadline = time.monotonic() + max_seconds
    def pulse():
        while not stopped.wait(interval):
            if time.monotonic() >= deadline:
                return
            try:
                repo.touch(worker_id)
                if on_progress is not None:
                    on_progress()
            except Exception:
                pass
    thread = threading.Thread(target=pulse, name="im-claim-liveness", daemon=True)
    thread.start()
    try:
        yield
    finally:
        stopped.set()
        thread.join(timeout=1)


class ResidentSendWorker:
    """一个固定市场、固定账号、内部串行的常驻发送消费者。"""

    def __init__(
        self,
        *,
        market: str,
        account_name: str,
        worker_id: str,
        process_id: int,
        worker_repo,
        task_repo,
        claim_processor,
        sleeper: Callable[[float], None] | None = None,
        idle_poll_seconds: float = 2,
        min_lead_interval_seconds: float = 5,
    ) -> None:
        clean_market = str(market or "").strip().lower()
        clean_account = str(account_name or "").strip()
        clean_worker_id = str(worker_id or "").strip()
        if not clean_market:
            raise ValueError("market_required")
        if not clean_account:
            raise ValueError("account_name_required")
        if ":" in clean_account:
            raise ValueError("invalid_account_name")
        if clean_worker_id != f"{clean_market}:{clean_account}":
            raise ValueError("worker_id_mismatch")
        if isinstance(process_id, bool) or int(process_id) <= 0:
            raise ValueError("invalid_process_id")
        if float(idle_poll_seconds) < 0:
            raise ValueError("invalid_idle_poll_seconds")
        if float(min_lead_interval_seconds) < 0:
            raise ValueError("invalid_min_lead_interval_seconds")

        self.market = clean_market
        self.account_name = clean_account
        self.worker_id = clean_worker_id
        self.process_id = int(process_id)
        self.worker_repo = worker_repo
        self.task_repo = task_repo
        self.claim_processor = claim_processor
        self.sleeper = sleeper or time.sleep
        self.idle_poll_seconds = float(idle_poll_seconds)
        self.min_lead_interval_seconds = float(
            min_lead_interval_seconds
        )
        self.maintenance_requested = False

    @staticmethod
    def _claim_value(claim: dict, key: str) -> str:
        for section in ("task", "lead", "component", "attempt"):
            row = claim.get(section)
            if isinstance(row, dict):
                value = str(row.get(key) or "").strip()
                if value:
                    return value
        value = str(claim.get(key) or "").strip()
        if not value:
            raise ValueError(f"claim_missing:{key}")
        return value

    def run(
        self,
        *,
        stop_requested: Callable[[], bool] | None = None,
        maintenance_requested: Callable[[], bool] | None = None,
        max_cycles: int | None = None,
    ) -> dict:
        """持续消费；``max_cycles`` 只用于测试和受控单步运行。"""
        if max_cycles is not None and max_cycles < 0:
            raise ValueError("invalid_max_cycles")
        should_stop = stop_requested or (lambda: False)
        should_maintain = maintenance_requested or (lambda: False)
        self.maintenance_requested = False
        summary = {
            "claimed": 0,
            "sent": 0,
            "failed": 0,
            "paused": False,
            "stopped": False,
        }
        cycles = 0
        current_task_id: str | None = None
        current_lead_id: str | None = None
        explicit_stop = False

        self.worker_repo.register(
            self.worker_id,
            self.market,
            self.account_name,
            self.process_id,
        )
        orphaned = (
            self.task_repo.recover_orphaned_attempt_for_worker(
                self.worker_id
            )
        )
        if orphaned is not None:
            current_task_id = str(orphaned["task_id"])
            current_lead_id = str(orphaned["lead_id"])
            self.worker_repo.heartbeat(
                self.worker_id,
                status="busy",
                task_id=current_task_id,
                lead_id=current_lead_id,
            )
            self.worker_repo.pause(
                self.worker_id,
                code="orphaned_worker_attempt",
                detail=(
                    "上次进程遗留 sending attempt，已转为 unknown，"
                    "需要人工复核"
                ),
            )
            summary["paused"] = True
        else:
            self.worker_repo.heartbeat(
                self.worker_id,
                status="ready",
            )

        while True:
            if should_stop():
                explicit_stop = True
                break
            if should_maintain():
                self.maintenance_requested = True
                explicit_stop = True
                break
            if max_cycles is not None and cycles >= max_cycles:
                break

            state = self.worker_repo.get(self.worker_id)
            if state is None:
                self.worker_repo.pause(
                    self.worker_id,
                    code="worker_state_missing",
                    detail=None,
                )
                summary["paused"] = True
                break

            state_status = str(state.get("status") or "")
            if state_status == "stopped":
                explicit_stop = True
                break
            if state_status in _PAUSED_STATUSES:
                summary["paused"] = True
                current_task_id = (
                    state.get("current_task_id") or current_task_id
                )
                current_lead_id = (
                    state.get("current_lead_id") or current_lead_id
                )
                self.worker_repo.touch(self.worker_id)
                cycles += 1
                self.sleeper(self.idle_poll_seconds)
                continue

            summary["paused"] = False
            claim = self.task_repo.claim_next_component_for_worker(
                market=self.market,
                account_name=self.account_name,
                worker_id=self.worker_id,
            )
            cycles += 1
            if claim is None:
                current_task_id = None
                current_lead_id = None
                self.worker_repo.heartbeat(
                    self.worker_id,
                    status="ready",
                )
                self.sleeper(self.idle_poll_seconds)
                continue

            current_task_id = self._claim_value(claim, "task_id")
            current_lead_id = self._claim_value(claim, "lead_id")
            summary["claimed"] += 1
            self.worker_repo.heartbeat(
                self.worker_id,
                status="busy",
                task_id=current_task_id,
                lead_id=current_lead_id,
            )

            try:
                with _claim_liveness(self.worker_repo, self.worker_id):
                    result = self.claim_processor.process_claim(
                        claim,
                        task_id=current_task_id,
                        market=self.market,
                        send_account=self.account_name,
                    )
            except Exception as exc:
                self.worker_repo.pause(
                    self.worker_id,
                    code="worker_exception",
                    detail=f"{type(exc).__name__}: {exc}",
                )
                summary["failed"] += 1
                summary["paused"] = True
                continue

            outcome = result["outcome"]
            if outcome.status == "sent":
                summary["sent"] += 1
            else:
                summary["failed"] += 1

            latest_state = self.worker_repo.get(self.worker_id)
            latest_status = str(
                (latest_state or {}).get("status") or ""
            )
            if latest_status == "stopped":
                explicit_stop = True
                break
            if latest_status in _PAUSED_STATUSES:
                summary["paused"] = True
                self.worker_repo.touch(self.worker_id)
                continue

            if (
                outcome.status == "unknown"
                or outcome.error_code in _ACCOUNT_PAUSE_CODES
            ):
                self.worker_repo.pause(
                    self.worker_id,
                    code=outcome.error_code or "platform_result_unknown",
                    detail=None,
                )
                summary["paused"] = True
                if outcome.error_code == "im_page_unavailable":
                    break  # 已崩溃页面不再处理后续达人；退出上下文释放浏览器。
                continue

            current_task_id = None
            current_lead_id = None
            self.worker_repo.heartbeat(
                self.worker_id,
                status="ready",
            )
            lead = result.get("lead") or {}
            if (
                str(lead.get("status") or "") not in _ACTIVE_LEAD_STATUSES
                and self.min_lead_interval_seconds > 0
            ):
                self.sleeper(self.min_lead_interval_seconds)

        if explicit_stop or not summary["paused"]:
            self.worker_repo.stop(self.worker_id)
            summary["paused"] = False
            summary["stopped"] = True
        return summary


class EmbeddedSendWorker:
    """复用监听页面的非阻塞发送消费者。

    每个 monitor tick 最多领取一个组件，不自行 sleep，也不再打开第二个
    Chromium Profile。这样监听、人工回复与批量触达共享同一登录会话。
    """

    def __init__(
        self,
        *,
        market: str,
        account_name: str,
        process_id: int,
        worker_repo,
        task_repo,
        claim_processor,
        min_lead_interval_seconds: float = 5,
        monotonic: Callable[[], float] | None = None,
        on_progress: Callable[[], None] | None = None,
    ) -> None:
        self.market = str(market or "").strip().lower()
        self.account_name = str(account_name or "").strip()
        self.worker_id = f"{self.market}:{self.account_name}"
        if not self.market:
            raise ValueError("market_required")
        if not self.account_name or ":" in self.account_name:
            raise ValueError("invalid_account_name")
        if isinstance(process_id, bool) or int(process_id) <= 0:
            raise ValueError("invalid_process_id")
        if float(min_lead_interval_seconds) < 0:
            raise ValueError("invalid_min_lead_interval_seconds")
        self.process_id = int(process_id)
        self.worker_repo = worker_repo
        self.task_repo = task_repo
        self.claim_processor = claim_processor
        self.min_lead_interval_seconds = float(min_lead_interval_seconds)
        self.monotonic = monotonic or time.monotonic
        self.on_progress = on_progress
        self._next_claim_at = 0.0
        self._started = False
        self._closed = False

    def start(self) -> None:
        if self._started:
            return
        self.worker_repo.register(
            self.worker_id,
            self.market,
            self.account_name,
            self.process_id,
        )
        orphaned = self.task_repo.recover_orphaned_attempt_for_worker(
            self.worker_id
        )
        if orphaned is not None:
            self.worker_repo.pause(
                self.worker_id,
                code="orphaned_worker_attempt",
                detail=(
                    "上次进程遗留 sending attempt，已转为 unknown，"
                    "需要人工复核"
                ),
            )
        else:
            self.worker_repo.heartbeat(self.worker_id, status="ready")
        self._started = True

    def tick(self) -> str:
        if not self._started or self._closed:
            raise RuntimeError("embedded_worker_not_running")
        state = self.worker_repo.get(self.worker_id)
        status = str((state or {}).get("status") or "")
        if not state:
            self.worker_repo.pause(
                self.worker_id,
                code="worker_state_missing",
                detail=None,
            )
            return "paused"
        if status == "stopped":
            return "stopped"
        if status in _PAUSED_STATUSES:
            self.worker_repo.touch(self.worker_id)
            return "paused"
        if self.monotonic() < self._next_claim_at:
            self.worker_repo.touch(self.worker_id)
            return "throttled"

        claim = self.task_repo.claim_next_component_for_worker(
            market=self.market,
            account_name=self.account_name,
            worker_id=self.worker_id,
        )
        if claim is None:
            self.worker_repo.heartbeat(self.worker_id, status="ready")
            return "idle"

        task_id = ResidentSendWorker._claim_value(claim, "task_id")
        lead_id = ResidentSendWorker._claim_value(claim, "lead_id")
        self.worker_repo.heartbeat(
            self.worker_id,
            status="busy",
            task_id=task_id,
            lead_id=lead_id,
        )
        try:
            with _claim_liveness(self.worker_repo, self.worker_id, on_progress=self.on_progress):
                result = self.claim_processor.process_claim(
                    claim,
                    task_id=task_id,
                    market=self.market,
                    send_account=self.account_name,
                )
        except Exception as exc:
            self.worker_repo.pause(
                self.worker_id,
                code="worker_exception",
                detail=f"{type(exc).__name__}: {exc}",
            )
            return "paused"

        outcome = result["outcome"]
        if (
            outcome.status == "unknown"
            or outcome.error_code in _ACCOUNT_PAUSE_CODES
        ):
            self.worker_repo.pause(
                self.worker_id,
                code=outcome.error_code or "platform_result_unknown",
                detail=None,
            )
            return "paused"

        self.worker_repo.heartbeat(self.worker_id, status="ready")
        lead = result.get("lead") or {}
        if str(lead.get("status") or "") not in _ACTIVE_LEAD_STATUSES:
            self._next_claim_at = (
                self.monotonic() + self.min_lead_interval_seconds
            )
        return "sent" if outcome.status == "sent" else "failed"

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._started:
            state = self.worker_repo.get(self.worker_id) or {}
            if str(state.get("status") or "") in _PAUSED_STATUSES:
                self.worker_repo.touch(self.worker_id)
            else:
                self.worker_repo.stop(self.worker_id)


def build_embedded_send_worker(
    *,
    page,
    engine,
    market: str,
    account_name: str,
    min_lead_interval_seconds: float = 5,
    on_progress: Callable[[], None] | None = None,
) -> EmbeddedSendWorker:
    """为已经通过登录检查的监听页面装配批量发送消费者。"""
    task_repo = SendTaskRepo(engine)
    processor = SendTaskRunner(
        task_repo=task_repo,
        sender=ComponentSender(
            page,
            market=market,
            account_name=account_name,
            conversation_loader=lambda: _load_all(
                page,
                rounds=6,
                settle=2,
                max_timeouts=2,
            ),
            targeted_conversation_resolver=(
                _build_targeted_conversation_resolver(
                    page.context,
                    market=market,
                )
            ),
        ),
        attribution_repo=SendAttributionRepo(engine),
        outreach_store=OutreachStore(engine),
        message_projector=OutboundMessageProjector(engine),
    )
    return EmbeddedSendWorker(
        market=market,
        account_name=account_name,
        process_id=os.getpid(),
        worker_repo=SendWorkerRepo(engine),
        task_repo=task_repo,
        claim_processor=processor,
        min_lead_interval_seconds=min_lead_interval_seconds,
        on_progress=on_progress,
    )


@contextmanager
def _targeted_im_gate(
    *,
    timeout_seconds: float = 300,
    poll_seconds: float = 0.25,
):
    """Serialize native targeted-IM bootstrap across account processes."""
    deadline = time.monotonic() + max(0.0, float(timeout_seconds))
    while True:
        with scheduled_relogin_svc.maintenance_lock(
            _TARGETED_IM_LOCK_PATH
        ) as acquired:
            if acquired:
                yield
                return
        if time.monotonic() >= deadline:
            raise ConversationResolutionError(
                "conversation_targeted_gate_timeout",
                {
                    "method": "targeted_im",
                    "classification": "global_gate_timeout",
                },
            )
        time.sleep(max(0.05, float(poll_seconds)))


def _build_targeted_conversation_resolver(
    browser_context,
    *,
    market: str,
    primary_page=None,
):
    """Resolve cold creators through TikTok's native targeted IM route.

    ``changeContact`` on a generic IM page can settle locally without issuing
    any network request.  The Creator Marketplace route establishes the
    target context first. Dedicated workers pass primary_page to reuse one
    SDK/page; embedded listeners keep their original page and use a separate
    reusable target page. The first message uses the verified target page.
    """
    market_meta = MARKETS[market]
    holder = {"page": None, "primed": False, "token_ready": False, "auth_failed": False, "responses": []}

    def read_bootstrap_responses():
        pending, holder["responses"] = holder["responses"], []
        for response in pending:
            try:
                payload = response.json()
                code = payload.get("code") if isinstance(payload, dict) else None
                if str(code) in {"16201010", "10001", "98000001", "98000002"}:
                    holder["auth_failed"] = True
                if urlsplit(response.url).path.endswith("/im/token/get") and response.status == 200 and type(code) is int and code == 0:
                    holder["token_ready"] = True
            except Exception:
                pass

    def close_targeted_page(page) -> None:
        if page is not primary_page:
            try:
                page.close()
            except Exception:
                pass
        holder["page"] = None
        holder["primed"] = False
        holder["token_ready"] = False
        holder["auth_failed"] = False
        holder["responses"] = []

    def resolution_from_response(
        page,
        response,
        *,
        method: str,
    ) -> ConversationResolution:
        if not isinstance(response, dict):
            raise ConversationResolutionError(
                "conversation_unresolved",
                {
                    "method": method,
                    "classification": "malformed_response",
                },
            )
        cid = str(response.get("cid") or "").strip()
        transport = {
            "method": method,
            "classification": str(
                response.get("classification") or "targeted_unresolved"
            ),
            "attempts": response.get("attempts"),
            "elapsed_ms": response.get("elapsed_ms"),
        }
        if not response.get("ok") or not cid:
            raise ConversationResolutionError(
                "conversation_unresolved",
                transport,
            )
        return ConversationResolution(
            cid=cid,
            method=method,
            transport=transport,
            page=page,
        )

    def resolve_locked(clean_oec: str) -> ConversationResolution:
        if not clean_oec:
            raise ConversationResolutionError(
                "conversation_oec_required",
                {
                    "method": "targeted_im",
                    "classification": "invalid_oec_id",
                },
            )
        page = holder.get("page")
        if page is None or page.is_closed():
            if primary_page is not None and primary_page.is_closed():
                raise ConversationResolutionError("im_page_unavailable", {"method":"targeted_im","classification":"primary_page_closed"})
            page = primary_page if primary_page is not None else browser_context.new_page()
            holder["page"] = page
            holder["primed"] = False
            holder["token_ready"] = False
            def observe_bootstrap(response):
                try:
                    parts = urlsplit(response.url)
                    if parts.hostname not in {"partner.tiktokshop.com", urlsplit(market_meta.host).hostname}:
                        return
                    if not parts.path.endswith(("/affiliate/partner/im/token/get", "/affiliate/partner/im/id/get")):
                        return
                    if response.status in {401, 403}:
                        holder["auth_failed"] = True
                        return
                    # 回调只记录元数据，主等待循环再读取正文，避免同步重入。
                    holder["responses"].append(response)
                except Exception:
                    pass
            subscribe = getattr(page, "on", None)
            if callable(subscribe):
                subscribe("response", observe_bootstrap)
        holder["token_ready"] = False
        holder["auth_failed"] = False
        query = urlencode({
            "shop_id": market_meta.shop_id,
            "creator_id": clean_oec,
            "market": market_meta.im_market,
            "enter_from": "find_creators",
        })
        try:
            page.goto(
                f"https://partner.tiktokshop.com/partner/im?{query}",
                # DOMContentLoaded can hang even when the IM application has
                # committed and will become SDK-ready.  Use commit as the
                # navigation gate and let the explicit SDK probe decide.
                wait_until="commit",
                timeout=60_000,
            )
        except Exception as exc:
            close_targeted_page(page)
            raise ConversationResolutionError(
                "conversation_targeted_navigation_failed",
                {
                    "method": "targeted_im",
                    "classification": "navigation_failed",
                    "detail": f"{type(exc).__name__}: {exc}",
                },
            ) from exc
        def auth_failed():
            read_bootstrap_responses()
            path = urlsplit(str(page.url)).path.casefold()
            return holder["auth_failed"] or any(marker in path for marker in ("login", "passport", "signin"))

        def fail_auth():
            close_targeted_page(page)
            raise ConversationResolutionError(
                "auth_required",
                {
                    "method": "targeted_im",
                    "classification": "login_required",
                },
            )
        if auth_failed():
            fail_auth()
        sdk_ready = _wait_sdk(page, tries=40, interval_ms=500, stop_when=auth_failed)
        # 只有同账号IM token接口已明确通过、SDK仍在初始化时才延长一次。
        # 全过程仍持有跨账号bootstrap门禁；没有认证进展的失败不增加等待。
        if not sdk_ready and holder["token_ready"] and not auth_failed():
            sdk_ready = _wait_sdk(page, tries=60, interval_ms=500, stop_when=auth_failed)
        if auth_failed():
            fail_auth()
        if not sdk_ready:
            close_targeted_page(page)
            raise ConversationResolutionError(
                "conversation_targeted_sdk_not_ready",
                {
                    "method": "targeted_im",
                    "classification": "sdk_not_ready",
                },
            )
        holder["primed"] = True
        try:
            response = page.evaluate(
                _JS_RESOLVE_TARGETED,
                {"oec": clean_oec},
            )
        except Exception as exc:
            raise ConversationResolutionError(
                "conversation_targeted_transport_exception",
                {
                    "method": "targeted_im",
                    "classification": "transport_exception",
                    "detail": f"{type(exc).__name__}: {exc}",
                },
            ) from exc
        return resolution_from_response(
            page,
            response,
            method="targeted_im",
        )

    def resolve(oec_id: str) -> ConversationResolution:
        clean_oec = str(oec_id or "").strip()
        page = holder.get("page")
        if (
            clean_oec
            and page is not None
            and not page.is_closed()
            and holder.get("primed")
            and _wait_sdk(page, tries=2, interval_ms=100)
        ):
            try:
                response = page.evaluate(
                    _JS_RESOLVE_TARGETED,
                    {"oec": clean_oec},
                )
                return resolution_from_response(
                    page,
                    response,
                    method="targeted_im_reuse",
                )
            except ConversationResolutionError:
                pass
            except Exception:
                close_targeted_page(page)
        with _targeted_im_gate():
            return resolve_locked(clean_oec)

    return resolve


class WorkerRuntime:
    """持有 worker 全生命周期需要的浏览器和 profile lease。"""

    def __init__(
        self,
        *,
        worker: ResidentSendWorker,
        browser_context,
        playwright,
        lease,
        lease_token: str,
    ) -> None:
        self.worker = worker
        self.browser_context = browser_context
        self.playwright = playwright
        self.lease = lease
        self.lease_token = lease_token
        self._closed = False

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        errors: list[BaseException] = []
        try:
            self.browser_context.close()
        except BaseException as exc:
            errors.append(exc)
        try:
            self.playwright.stop()
        except BaseException as exc:
            errors.append(exc)
        try:
            self.lease.release(self.lease_token)
        except BaseException as exc:
            errors.append(exc)
        if errors:
            raise errors[0]

    def __enter__(self) -> ResidentSendWorker:
        return self.worker

    def __exit__(self, exc_type, _exc, _traceback) -> bool:
        try:
            self.close()
        except BaseException:
            if exc_type is None:
                raise
        return False


def _start_playwright():  # pragma: no cover - 真实浏览器适配层
    from playwright.sync_api import sync_playwright

    return sync_playwright().start()


def _select_account(cfg, account_name: str):
    clean_name = str(account_name or "").strip()
    if not clean_name:
        raise ValueError("account_name_required")
    accounts = {
        account.name: account
        for account in cfgmod.load_accounts(cfg)
    }
    account = accounts.get(clean_name)
    if account is None:
        raise ValueError("send_account_not_found")
    if not account.enabled:
        raise ValueError("send_account_disabled")
    return account


def _profile_path(account, profile: str | Path | None) -> Path:
    bound_path = Path(account.profile_dir)
    if not bound_path.is_absolute():
        bound_path = cfgmod.ROOT / bound_path
    bound_path = bound_path.resolve()
    if profile is None:
        return bound_path

    requested_path = Path(profile)
    if not requested_path.is_absolute():
        requested_path = cfgmod.ROOT / requested_path
    requested_path = requested_path.resolve()
    if requested_path != bound_path:
        raise ValueError("send_profile_account_mismatch")
    return bound_path


def _cleanup_partial_runtime(
    *,
    browser_context,
    playwright,
    lease,
    lease_token: str | None,
) -> None:
    if browser_context is not None:
        try:
            browser_context.close()
        except BaseException:
            pass
    if playwright is not None:
        try:
            playwright.stop()
        except BaseException:
            pass
    if lease is not None and lease_token is not None:
        try:
            lease.release(lease_token)
        except BaseException:
            pass


def build_worker_runtime(
    *,
    market: str,
    account_name: str,
    profile: str | Path | None = None,
    min_lead_interval_seconds: float | None = None,
    idle_poll_seconds: float = 2,
    headless: bool = True,
) -> WorkerRuntime:
    """创建真实长驻页面；构建失败时完整释放浏览器与 profile lease。"""
    clean_market = normalize_registered_market(market)
    # Dashboard 可登记尚未完成发送抓包验收的市场；真实 worker 必须在读取
    # 账号身份、数据库或浏览器前按生产市场注册表 fail-closed。
    from bdhub.hub.markets import capability_enabled

    if (
        clean_market not in MARKETS
        or not capability_enabled(clean_market, "send")
    ):
        raise ValueError("unsupported_market")
    cfg = cfgmod.load()
    account = _select_account(cfg, account_name)
    from bdhub.account_policy import resolve_account_policy

    effective_send_interval = (
        float(min_lead_interval_seconds)
        if min_lead_interval_seconds is not None
        else resolve_account_policy(account).im_send_interval_seconds
    )
    try:
        require_im_account(
            account,
            requested_market=clean_market,
            required_role="sender",
        )
    except ValueError as exc:
        if str(exc) == "im_account_market_mismatch":
            raise ValueError("send_account_market_mismatch") from None
        raise
    identity = identity_for(
        clean_market,
        account=account,
        cfg=cfg,
    ).require_im()
    if not identity.partner_id_is_own:
        raise ValueError("send_account_identity_mismatch")
    profile_path = _profile_path(account, profile)
    target_url = im_page(clean_market)

    engine = get_engine(cfg)
    missing = pending_changes(engine)
    if missing:
        raise RuntimeError(
            "resident_worker_migration_required:" + ",".join(missing)
        )
    check_schema(engine)

    lease = ProfileLease(
        profile_path,
        account=account.name,
        market=clean_market,
        operation="resident_send_worker",
    )
    lease_token: str | None = None
    playwright = None
    browser_context = None
    try:
        owner = lease.acquire()
        lease_token = owner.token
        playwright = _start_playwright()
        browser_context = playwright.chromium.launch_persistent_context(
            str(profile_path),
            headless=bool(headless),
        )
        page = (
            browser_context.pages[0]
            if browser_context.pages
            else browser_context.new_page()
        )
        page.goto(
            target_url,
            wait_until="domcontentloaded",
            timeout=60_000,
        )
        if "login" in str(page.url).casefold():
            raise RuntimeError("send_profile_not_logged_in")
        if not _wait_sdk(page):
            raise RuntimeError("im_sdk_not_ready")

        task_repo = SendTaskRepo(engine)
        processor = SendTaskRunner(
            task_repo=task_repo,
            sender=ComponentSender(
                page,
                market=clean_market,
                account_name=account.name,
                conversation_loader=lambda: _load_all(
                    page,
                    rounds=6,
                    settle=2,
                    max_timeouts=2,
                ),
                targeted_conversation_resolver=(
                    _build_targeted_conversation_resolver(
                        browser_context,
                        market=clean_market,
                        primary_page=page,
                    )
                ),
            ),
            attribution_repo=SendAttributionRepo(engine),
            outreach_store=OutreachStore(engine),
            message_projector=OutboundMessageProjector(engine),
        )
        worker = ResidentSendWorker(
            market=clean_market,
            account_name=account.name,
            worker_id=f"{clean_market}:{account.name}",
            process_id=os.getpid(),
            worker_repo=SendWorkerRepo(engine),
            task_repo=task_repo,
            claim_processor=processor,
            idle_poll_seconds=idle_poll_seconds,
            min_lead_interval_seconds=effective_send_interval,
        )
        return WorkerRuntime(
            worker=worker,
            browser_context=browser_context,
            playwright=playwright,
            lease=lease,
            lease_token=lease_token,
        )
    except BaseException:
        _cleanup_partial_runtime(
            browser_context=browser_context,
            playwright=playwright,
            lease=lease,
            lease_token=lease_token,
        )
        raise


@contextmanager
def _stop_requested_context():
    event = threading.Event()
    previous = {}

    def request_stop(_signum, _frame):
        event.set()

    for signal_name in ("SIGINT", "SIGTERM"):
        current_signal = getattr(signal, signal_name, None)
        if current_signal is None:
            continue
        previous[current_signal] = signal.getsignal(current_signal)
        signal.signal(current_signal, request_stop)
    try:
        yield event.is_set
    finally:
        for current_signal, old_handler in previous.items():
            signal.signal(current_signal, old_handler)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="启动固定市场/账号的常驻 IM 发送 worker",
    )
    parser.add_argument("--market", required=True, choices=sorted(MARKETS))
    parser.add_argument("--account", required=True)
    parser.add_argument("--http-canary-task-id")
    parser.add_argument("--http-canary-revision",type=int)
    parser.add_argument('--http-trial-run-id')
    parser.add_argument(
        "--profile",
        help=(
            "可选兼容参数；必须与 accounts.json 中该账号的 "
            "profile_dir 完全一致"
        ),
    )
    parser.add_argument(
        "--min-lead-interval",
        type=float,
        default=None,
        help="覆盖账号设置中的达人发送间隔秒数",
    )
    parser.add_argument(
        "--idle-poll",
        type=float,
        default=2,
        help="无任务或暂停状态下的轮询秒数",
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        help="显示浏览器窗口；默认无头运行",
    )
    args = parser.parse_args(argv)

    if args.http_canary_task_id:
        if args.http_trial_run_id:
            from bdhub.send.http_trial import run_trial
            return run_trial(account_name=args.account,market=args.market,task_id=args.http_canary_task_id,
                             revision=args.http_canary_revision,run_id=args.http_trial_run_id)
        from bdhub.send.http_canary import run_canary
        return run_canary(account_name=args.account,market=args.market,
                          task_id=args.http_canary_task_id,revision=args.http_canary_revision)

    cfg = cfgmod.load()
    account = _select_account(cfg, args.account)
    with _stop_requested_context() as stop_requested:
        while not stop_requested():
            while scheduled_relogin_svc.maintenance_due(
                account,
                ignore_retry_throttle=True,
            ):
                if stop_requested():
                    return 0
                if not scheduled_relogin_svc.in_maintenance_window():
                    # 兼容维护策略入口；当前全天允许。等待期间不持有Profile，
                    # 不领取新组件，维护完成后恢复同一worker进程。
                    time.sleep(30)
                    continue
                maintained = scheduled_relogin_svc.maintain_account(
                    account,
                    market=args.market,
                    ignore_retry_throttle=True,
                )
                if maintained.get("attempted") is not True:
                    time.sleep(2)
                    continue
                if maintained.get("success") is not True:
                    print(
                        "worker_scheduled_relogin_failed "
                        f"account={account.name} "
                        f"decision={str(maintained.get('decision') or 'unknown')[:64]}",
                        flush=True,
                    )
                    return 1

            runtime = build_worker_runtime(
                market=args.market,
                account_name=args.account,
                profile=args.profile,
                min_lead_interval_seconds=args.min_lead_interval,
                idle_poll_seconds=args.idle_poll,
                headless=not args.headed,
            )
            with runtime as worker:
                summary = worker.run(
                    stop_requested=stop_requested,
                    maintenance_requested=lambda: (
                        scheduled_relogin_svc.maintenance_due(
                            account,
                            ignore_retry_throttle=True,
                        )
                    ),
                )
            print(
                "worker_finished "
                f"claimed={summary['claimed']} sent={summary['sent']} "
                f"failed={summary['failed']} paused={summary['paused']} "
                f"stopped={summary['stopped']} "
                f"maintenance={worker.maintenance_requested}",
                flush=True,
            )
            if not worker.maintenance_requested:
                break
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = [
    "EmbeddedSendWorker",
    "ResidentSendWorker",
    "WorkerRuntime",
    "build_embedded_send_worker",
    "build_worker_runtime",
    "main",
]
