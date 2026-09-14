"""数据库驱动的 IM 发送任务执行器。

执行器只消费启动事务已经冻结的任务/线索快照；命令行不接收达人、
文字、PID 或发送模式，避免运行时内容越过预检。
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from bdhub.send.component_sender import (
    ComponentOutcome,
    ComponentSender,
    ConversationResolution,
    ConversationResolutionError,
)


class SendTaskRunner:
    """串行执行数据库已认领的逻辑组件。

    所有外部依赖均可注入，因此单元测试不需要数据库或浏览器。
    """

    def __init__(
        self,
        *,
        task_repo,
        sender,
        attribution_repo,
        outreach_store,
        message_projector=None,
        now: Callable[[], datetime] | None = None,
        closer: Callable[[], None] | None = None,
    ) -> None:
        self.task_repo = task_repo
        self.sender = sender
        self.attribution_repo = attribution_repo
        self.outreach_store = outreach_store
        self.message_projector = message_projector
        self.now = now or (lambda: datetime.now(timezone.utc))
        self._closer = closer

    def run(self, task_id: str) -> dict:
        """继续正常任务；只认领尚未尝试的 pending 组件。"""
        return self._drain(task_id, retry_failed=False)

    def retry_failed(self, task_id: str) -> dict:
        """显式重试可重试失败；sent/unknown 组件永不进入重试。"""
        return self._drain(task_id, retry_failed=True)

    def close(self) -> None:
        if self._closer is not None:
            closer, self._closer = self._closer, None
            closer()

    def _drain(self, task_id: str, *, retry_failed: bool) -> dict:
        while True:
            claim = self.task_repo.claim_next_component(
                task_id,
                retry_failed=retry_failed,
            )
            if claim is None:
                break
            result = self.process_claim(claim, task_id=task_id)
            outcome = result["outcome"]
            if outcome.status == "unknown":
                break
            # 一次显式重试对同一个失败组件只尝试一次，防止仓储重新
            # 认领刚刚再次失败的组件形成无限循环。
            if retry_failed and outcome.status != "sent":
                break

        return self.task_repo.finalize_task(task_id)

    def process_claim(
        self,
        claim: dict,
        *,
        task_id: str | None = None,
        market: str | None = None,
        send_account: str | None = None,
    ) -> dict:
        """处理一个已原子认领的组件，供任务 runner 与常驻 worker 共用。"""
        resolved_task_id = task_id or self._required(claim, "task_id")
        resolved_market = (
            str(market or self._optional(claim, "bd_market") or "mx")
            .strip()
            .lower()
        )
        if market is not None:
            claim_market = str(
                self._optional(claim, "bd_market") or ""
            ).strip().lower()
            if claim_market != resolved_market:
                raise ValueError("send_claim_market_mismatch")
        sender_market = str(
            getattr(self.sender, "market", "") or ""
        ).strip().lower()
        if sender_market and sender_market != resolved_market:
            raise ValueError("send_sender_market_mismatch")
        resolved_account = (
            str(
                send_account
                or self._optional(claim, "send_account")
                or ""
            ).strip()
            or None
        )
        outcome, cid = self._send_claim(claim)
        attempt_id = self._required(claim, "attempt_id")
        lead_id = self._required(claim, "lead_id")
        component_kind = self._required(claim, "component_kind")
        sent_at = self.now()

        # 归因是商品卡成功事实的核心账本，必须先于组件落 sent。
        # 如果这里失败，attempt 仍保持 sending，重启后会 fail-closed
        # 转为 unknown 并暂停，绝不会把卡片自动重发。
        if outcome.status == "sent" and component_kind == "card":
            self.attribution_repo.activate_card_success(
                lead_id=lead_id,
                sent_at=sent_at,
            )
        elif outcome.status == "failed_terminal":
            self.attribution_repo.release_unsent(
                lead_id=lead_id,
                reason=f"{component_kind}_failed_terminal",
            )

        self.task_repo.finish_attempt(
            attempt_id,
            status=outcome.status,
            platform_message_id=outcome.platform_message_id,
            conversation_id=cid,
            error_code=outcome.error_code,
            error_detail=None,
            transport_snapshot=outcome.transport,
        )

        if outcome.status == "sent":
            if self.message_projector is not None:
                try:
                    self.message_projector.project_sent_component(
                        server_id=outcome.platform_message_id,
                        conversation_id=cid,
                        creator_oec_id=self._optional(claim, "oec_id"),
                        component_kind=component_kind,
                        text=self._optional(claim, "rendered_text_snapshot"),
                        pid=self._optional(claim, "pid"),
                        market=resolved_market,
                        sent_at=sent_at,
                    )
                except Exception as exc:
                    # 发送事实已成立，投影失败只能告警，绝不能触发重发。
                    self.task_repo.append_event(
                        task_id=resolved_task_id,
                        lead_id=lead_id,
                        event_type="im_projection_failed",
                        actor="send_worker",
                        payload={
                            "component_id": self._required(
                                claim,
                                "component_id",
                            ),
                            "error_type": type(exc).__name__,
                        },
                    )
            try:
                self._log_legacy_outreach(
                    task_id=resolved_task_id,
                    claim=claim,
                    conversation_id=cid,
                    sent_at=sent_at,
                    outcome=outcome,
                    market=resolved_market,
                    send_account=resolved_account,
                )
            except Exception as exc:
                # 组件表是发送真相源；旧 outreach 只是兼容投影。
                # 投影失败要留审计并继续，避免已发送组件阻断整批任务。
                self.task_repo.append_event(
                    task_id=resolved_task_id,
                    lead_id=lead_id,
                    event_type="outreach_projection_failed",
                    actor="send_worker",
                    payload={
                        "component_id": self._required(
                            claim,
                            "component_id",
                        ),
                        "error_type": type(exc).__name__,
                    },
                )

        lead = self.task_repo.finalize_lead(lead_id)
        task = self.task_repo.finalize_task(resolved_task_id)
        return {
            "outcome": outcome,
            "lead": lead,
            "task": task,
            "attempt_id": attempt_id,
        }

    def _send_claim(self, claim: dict) -> tuple[ComponentOutcome, str | None]:
        component_kind = self._required(claim, "component_kind")
        handle = self._required(claim, "raw_handle")
        cid = self._optional(claim, "conversation_id")
        cid_was_provided = bool(str(cid or "").strip())
        resolver_transport = None
        if not cid:
            oec_id = self._optional(claim, "oec_id")
            if not oec_id:
                return (
                    ComponentOutcome(
                        status="failed_terminal",
                        platform_message_id=None,
                        error_code="missing_oec_snapshot",
                        transport={},
                    ),
                    None,
                )
            try:
                resolution = self.sender.resolve_conversation(oec_id)
                if isinstance(resolution, ConversationResolution):
                    cid = resolution.cid
                    resolver_transport = resolution.transport
                else:
                    # 鍏煎娴嬭瘯鎴栧閮ㄦ敞鍏ョ殑鏃у紡 sender銆?
                    cid = str(resolution or "").strip()
                    resolver_transport = {
                        "oec_id": oec_id,
                        "method": "legacy_sender",
                        "classification": "resolved",
                    }
                if not cid:
                    raise ConversationResolutionError(
                        "conversation_unresolved",
                        {
                            "oec_id": oec_id,
                            "classification": "empty_resolution",
                        },
                    )
            except ConversationResolutionError as exc:
                return (
                    ComponentOutcome(
                        status=(
                            "failed_terminal"
                            if exc.error_code == "conversation_unreachable"
                            else "failed_retryable"
                        ),
                        platform_message_id=None,
                        error_code=exc.error_code,
                        transport={"resolver": exc.transport},
                    ),
                    None,
                )
            except Exception as exc:
                return (
                    ComponentOutcome(
                        status="failed_retryable",
                        platform_message_id=None,
                        error_code="conversation_unresolved",
                        transport={"detail": str(exc)},
                    ),
                    None,
                )

        if cid_was_provided:
            ensure = getattr(self.sender, "ensure_conversation", None)
            if callable(ensure):
                try:
                    resolver_transport = ensure(
                        cid=str(cid),
                        oec_id=self._optional(claim, "oec_id"),
                    )
                except ConversationResolutionError as activation_error:
                    if activation_error.error_code == "im_page_unavailable":
                        return (ComponentOutcome(status="failed_retryable", platform_message_id=None,
                            error_code="im_page_unavailable", transport={"resolver":activation_error.transport}), str(cid))
                    oec_id = self._optional(claim, "oec_id")
                    if not str(oec_id or "").strip():
                        return (
                            ComponentOutcome(
                                status="failed_retryable",
                                platform_message_id=None,
                                error_code=activation_error.error_code,
                                transport={
                                    "resolver": activation_error.transport
                                },
                            ),
                            str(cid),
                        )
                    try:
                        resolve_after_activation = getattr(
                            self.sender,
                            "resolve_after_activation_failure",
                            None,
                        )
                        if callable(resolve_after_activation):
                            resolution = resolve_after_activation(
                                str(oec_id)
                            )
                        else:
                            resolution = self.sender.resolve_conversation(
                                str(oec_id)
                            )
                    except ConversationResolutionError as exc:
                        return (
                            ComponentOutcome(
                                status=(
                                    "failed_terminal"
                                    if exc.error_code
                                    == "conversation_unreachable"
                                    else "failed_retryable"
                                ),
                                platform_message_id=None,
                                error_code=exc.error_code,
                                transport={
                                    "resolver": {
                                        **exc.transport,
                                        "known_cid_activation": (
                                            activation_error.transport
                                        ),
                                    }
                                },
                            ),
                            str(cid),
                        )
                    if isinstance(resolution, ConversationResolution):
                        cid = resolution.cid
                        resolver_transport = {
                            **resolution.transport,
                            "known_cid_activation": (
                                activation_error.transport
                            ),
                        }
                    else:
                        cid = str(resolution or "").strip()
                        resolver_transport = {
                            "method": "legacy_sender",
                            "classification": "resolved",
                            "known_cid_activation": (
                                activation_error.transport
                            ),
                        }

        if component_kind == "text":
            text = self._optional(claim, "rendered_text_snapshot")
            if not str(text or "").strip():
                return (
                    ComponentOutcome(
                        status="failed_terminal",
                        platform_message_id=None,
                        error_code="missing_text_snapshot",
                        transport={},
                    ),
                    cid,
                )
            outcome = self.sender.send_text(
                cid=cid,
                handle=handle,
                text=text,
            )
            return self._with_resolver(outcome, resolver_transport), cid
        if component_kind == "card":
            pid = self._optional(claim, "pid")
            if not str(pid or "").strip():
                return (
                    ComponentOutcome(
                        status="failed_terminal",
                        platform_message_id=None,
                        error_code="missing_pid_snapshot",
                        transport={},
                    ),
                    cid,
                )
            outcome = self.sender.send_card(
                cid=cid,
                handle=handle,
                pid=pid,
            )
            return self._with_resolver(outcome, resolver_transport), cid
        raise ValueError(f"unsupported_component_kind:{component_kind}")

    @staticmethod
    def _with_resolver(
        outcome: ComponentOutcome,
        resolver_transport: dict | None,
    ) -> ComponentOutcome:
        if resolver_transport is None:
            return outcome
        return ComponentOutcome(
            status=outcome.status,
            platform_message_id=outcome.platform_message_id,
            error_code=outcome.error_code,
            transport={
                "resolver": resolver_transport,
                "send": outcome.transport,
            },
        )

    def _log_legacy_outreach(
        self,
        *,
        task_id: str,
        claim: dict,
        conversation_id: str | None,
        sent_at: datetime,
        outcome: ComponentOutcome,
        market: str,
        send_account: str | None,
    ) -> None:
        from bdhub.hub.keys import norm_handle

        lead_id = self._required(claim, "lead_id")
        handle = self._required(claim, "raw_handle")
        self.outreach_store.log_sent(
            handle_key=norm_handle(handle),
            market=market,
            rendered_message=(
                self._optional(claim, "rendered_text_snapshot") or ""
            ),
            oec_id=self._optional(claim, "oec_id"),
            pid=self._optional(claim, "pid"),
            conversation_id=conversation_id,
            send_account=send_account,
            raw_json={
                "component_kind": self._required(claim, "component_kind"),
                "platform_message_id": outcome.platform_message_id,
                "sent_at": sent_at.isoformat(),
            },
            send_task_id=task_id,
            send_task_lead_id=lead_id,
            logical_touch_id=lead_id,
        )

    @staticmethod
    def _optional(claim: dict, key: str):
        def present(value) -> bool:
            return value is not None and not (
                isinstance(value, str) and not value.strip()
            )

        if key in claim and present(claim[key]):
            return claim[key]
        for section in ("attempt", "component", "lead", "task"):
            snapshot = claim.get(section)
            if (
                isinstance(snapshot, dict)
                and key in snapshot
                and present(snapshot[key])
            ):
                return snapshot[key]
        return None

    @classmethod
    def _required(cls, claim: dict, key: str):
        value = cls._optional(claim, key)
        if value is None or (isinstance(value, str) and not value.strip()):
            raise ValueError(f"claim_missing:{key}")
        return value


def _compatibility_market(value: object) -> str:
    """旧 CLI 永久保持 MX-only；新市场只能走常驻 send-worker。"""
    market = str(value or "").strip().lower()
    if market != "mx":
        raise ValueError("send_runner_compatibility_mx_only")
    from bdhub.hub.markets import require_capability

    require_capability(market, "send")
    return market


def _require_compatibility_task(task_repo, task_id: str, market: str) -> dict:
    """在创建浏览器前核对任务存在且与兼容 CLI 的 MX 市场一致。"""
    task = task_repo.get_task(task_id)
    if task is None:
        raise LookupError("send_task_not_found")
    task_market = str(task.get("bd_market") or "").strip().lower()
    if task_market != market:
        raise ValueError("send_runner_task_market_mismatch")
    return task


def build_runner(
    *,
    profile: str,
    task_id: str,
    market: str = "mx",
) -> SendTaskRunner:  # pragma: no cover
    """创建旧 MX-only CLI 浏览器会话；正式入口只有常驻 send-worker。"""
    clean_market = _compatibility_market(market)
    from playwright.sync_api import sync_playwright

    from bdhub import config as cfgmod
    from bdhub.hub.engine import check_schema, get_engine
    from bdhub.hub.markets import im_page
    from bdhub.hub.repo.outreach import OutreachStore
    from bdhub.hub.repo.im import OutboundMessageProjector
    from bdhub.hub.repo.send_attribution import SendAttributionRepo
    from bdhub.hub.repo.send_tasks import SendTaskRepo
    from bdhub.imbase.transport import _load_all, _wait_sdk

    cfg = cfgmod.load()
    engine = get_engine(cfg)
    check_schema(engine)
    task_repo = SendTaskRepo(engine)
    _require_compatibility_task(task_repo, task_id, clean_market)
    profile_path = Path(profile)
    if not profile_path.is_absolute():
        profile_path = cfgmod.ROOT / profile_path

    playwright = sync_playwright().start()
    context = None
    try:
        context = playwright.chromium.launch_persistent_context(
            str(profile_path),
            headless=True,
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(
            im_page(clean_market),
            wait_until="domcontentloaded",
            timeout=60_000,
        )
        if "login" in page.url.casefold():
            raise RuntimeError("send_profile_not_logged_in")
        if not _wait_sdk(page):
            raise RuntimeError("im_sdk_not_ready")
        _load_all(page)

        def close_browser() -> None:
            try:
                context.close()
            finally:
                playwright.stop()

        return SendTaskRunner(
            task_repo=task_repo,
            sender=ComponentSender(page, market=clean_market),
            attribution_repo=SendAttributionRepo(engine),
            outreach_store=OutreachStore(engine),
            message_projector=OutboundMessageProjector(engine),
            closer=close_browser,
        )
    except Exception:
        if context is not None:
            context.close()
        playwright.stop()
        raise


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="执行已完成预检并锁定内容的 IM 发送任务"
    )
    parser.add_argument("--task-id", required=True)
    parser.add_argument(
        "--market",
        default="mx",
        choices=("mx",),
        help="兼容 CLI 固定为 mx；新市场必须使用 Dashboard send-worker",
    )
    parser.add_argument(
        "--profile",
        default="secrets/_pw_profile_send",
        help="已登录的 MX Partner 持久浏览器 profile",
    )
    args = parser.parse_args(argv)
    runner = build_runner(
        profile=args.profile,
        task_id=args.task_id,
        market=args.market,
    )
    try:
        result = runner.run(args.task_id)
        print(
            f"task_id={args.task_id} status={result.get('status', 'unknown')}"
        )
        return 0
    finally:
        runner.close()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = [
    "SendTaskRunner",
    "build_runner",
    "main",
]
