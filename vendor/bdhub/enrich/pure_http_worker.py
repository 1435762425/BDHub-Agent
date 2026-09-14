# -*- coding: utf-8 -*-
"""生产级纯 HTTP 多账号富化池。

每个账号使用独立子进程承载 signer、HTTP Session 与滑块状态。账号身份和目标名单
只经 stdin 传入，业务响应只由父进程捕获并在内存中校验、解析和入库。
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
import math
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Event, Lock, Timer
from typing import Any, Callable, Iterable

from sqlalchemy.exc import SQLAlchemyError

from .. import config as cfgmod
from .. import scheduled_relogin as scheduled_relogin_svc
from ..account_policy import resolve_account_policy
from ..handle_files import read_bounded_handles
from ..hub.keys import norm_handle
from ..hub.models import parse_find_item
from ..hub.outcome import ExactMissEvidence, Outcome, OutcomeKind
from ..hub.profile_observation import (
    CaptureKind,
    SnapshotQuality,
    new_capture_context,
    required_profile_fields,
)
from ..hub.engine import get_engine
from ..hub.repo.enrich_runs import EnrichRunStore
from ..hub.repo.profile_snapshots import (
    CreatorIdentityConflict,
    SnapshotIdentityMismatch,
    SnapshotReplayInvalid,
)
from .identity_store import IdentityBundle, load_identity
from . import shared_backoff
from .markets import store_for
from .profile_lease import ProfileLease
from .pure_http_canary import (
    account_stdin_payload,
    validate_runtime,
)
from .workflow import ItemState, RetryBudget, decide_transition


PRODUCTION_PROFILE_TYPES = (1, 2, 6)
DEFAULT_QPS = 3.0
DEFAULT_ACCOUNT_CONCURRENCY = 8
DEFAULT_CAPTCHA_ATTEMPTS = 3
DEFAULT_MAX_ITEM_ATTEMPTS = 2
# 单账号内部已经对带 ``x-tt-system-error: 3`` 的 100000 连续重试 3 次；
# 如果换账号后仍失败，说明短时间窗口内平台服务持续不稳定。此时不能把达人
# 写成最终未补全，而应释放 claim，按 durable item 的错误次数拉开重试间隔。
_TRANSIENT_RETRY_DELAYS_SECONDS = (30, 120, 300)
_AUTO_RETRY_REASONS = frozenset({
    OutcomeKind.AMBIGUOUS_EMPTY.value,
    OutcomeKind.NETWORK_ERROR.value,
    OutcomeKind.PROTOCOL_ERROR.value,
    "persistence_retry",
})
_INFRASTRUCTURE_ERROR_CODES = frozenset({
    "shared_platform_cooldown",
    "shared_backoff_state_invalid",
    "scheduled_relogin_due",
    "protocol_error",
    "runtime_dependency_incompatible",
    "runtime_dependency_missing",
    "runtime_io_error",
    "worker_configuration_invalid",
    "worker_error",
    "worker_failed",
    "worker_spawn_failed",
    "worker_start_failed",
    "worker_timeout",
})
# 进度按 durable 批次提交；100 位在双账号 3 QPS 下通常十几秒完成一次，
# 页面能持续看到 done 增长，暂停时最多只需回收这一小批 claim。
DURABLE_PROGRESS_BATCH_SIZE = 100
# Handle 入口也按小批及时入库。它没有已知 OEC 可预先建立 durable item，
# 但每 100 位提交一次后，中断重跑只会重复最后一个小批，不会丢掉整份名单。
HANDLE_PROGRESS_BATCH_SIZE = 100
_EXACT_MISS_EVIDENCE = ExactMissEvidence(
    source="pure_http_find",
    detail="two independent signed HTTP workers returned no exact handle",
)
_DEFAULT_RUNTIME = (
    cfgmod.ROOT
    / "data"
    / "runtime"
    / "attachment-analysis"
    / "code10000-http-20260805"
    / "TikTokShop_code10000_纯HTTP_完整版"
)


def _safe_remote_code(value: Any) -> str:
    if value is None or isinstance(value, bool):
        return ""
    normalized = str(value).strip()
    digits = normalized[1:] if normalized.startswith("-") else normalized
    if (
        digits
        and len(digits) <= 32
        and digits.isascii()
        and digits.isdigit()
    ):
        return normalized
    return ""


@dataclass(frozen=True, slots=True)
class HttpTarget:
    handle: str = ""
    oec_id: str = ""
    # 已知 OEC 路线的本地 handle 只用于 Profile 未返回 handle 时补全画像，
    # 不发送给接口，也不参与 OEC/改名校验。
    audit_handle: str = ""

    def __post_init__(self) -> None:
        handle = norm_handle(self.handle)
        oec_id = str(self.oec_id or "").strip()
        audit_handle = norm_handle(self.audit_handle)
        if not handle and not oec_id:
            raise ValueError("pure_http_target_identity_required")
        if oec_id and not oec_id.isdigit():
            raise ValueError("pure_http_target_oec_invalid")
        object.__setattr__(self, "handle", handle)
        object.__setattr__(self, "oec_id", oec_id)
        object.__setattr__(self, "audit_handle", audit_handle)

    @property
    def key(self) -> tuple[str, str]:
        return self.handle, self.oec_id


@dataclass(frozen=True, slots=True)
class PreparedHttpAccount:
    account: Any
    identity: dict[str, str]


@dataclass(frozen=True, slots=True)
class HttpItemResult:
    target: HttpTarget
    outcome: str
    profile: dict[str, Any] | None = None
    error_code: str = ""
    remote_code: str = ""
    attempt_count: int = 0
    account_count: int = 0
    exact_miss_account_count: int = 0


@dataclass(frozen=True, slots=True)
class HttpBatchResult:
    results: tuple[HttpItemResult, ...]
    request_count: int = 0
    challenge_count: int = 0
    challenge_success_count: int = 0


@dataclass(frozen=True, slots=True)
class HttpPoolResult:
    succeeded: tuple[HttpItemResult, ...]
    failed: tuple[HttpItemResult, ...]
    request_count: int = 0
    challenge_count: int = 0
    challenge_success_count: int = 0


@dataclass(frozen=True, slots=True)
class HttpWorkerStats:
    cooldown: bool = False
    cooldown_kind: str = ""
    detail_code: str = ""
    retry_at: str = ""


@dataclass(frozen=True, slots=True)
class HttpDurableResult:
    run_id: str
    enqueued: int
    counts: dict[str, int]
    status: str
    worker_stats: dict[str, HttpWorkerStats]
    worker_errors: tuple[str, ...]


def prepare_collection_accounts(
    cfg: Any,
    accounts: Iterable[Any],
    *,
    market: str = "mx",
    requested_names: Iterable[str] | None = None,
    load_bundle: Callable[[Path], IdentityBundle] = load_identity,
    refresh_identity: Callable[[Any, str], Any] | None = None,
) -> tuple[list[PreparedHttpAccount], dict[str, str]]:
    """准备独立账号身份；seed 缺失时由各自 SDK 进程完成 collector 自举。"""
    from ..hub.markets import get as get_market, identity_for

    market_meta = get_market(market)
    selected_names = None
    if requested_names is not None:
        selected_names = {
            str(name or "").strip()
            for name in requested_names
            if str(name or "").strip()
        }
        if not selected_names:
            raise ValueError("pure_http_accounts_required")
    del refresh_identity
    account_list = list(accounts)
    by_name = {str(account.name): account for account in account_list}
    if selected_names is not None:
        unknown = sorted(selected_names - set(by_name))
        if unknown:
            raise ValueError("pure_http_account_unknown")
        account_list = [
            account for account in account_list if account.name in selected_names
        ]

    prepared: list[PreparedHttpAccount] = []
    unavailable: dict[str, str] = {}
    for account in account_list:
        name = str(account.name)
        if getattr(account, "enabled", True) is False:
            unavailable[name] = "account_disabled"
            continue
        if not resolve_account_policy(account).collection_pool:
            unavailable[name] = "account_not_in_collection_pool"
            continue
        if scheduled_relogin_svc.maintenance_due(
            account, initialize=False, ignore_retry_throttle=True,
        ):
            unavailable[name] = "scheduled_relogin_due"
            continue
        try:
            bundle = load_bundle(account.headers_json)
        except (OSError, ValueError):
            unavailable[name] = "account_identity_unavailable"
            continue
        try:
            identity = account_stdin_payload(
                dict(bundle.headers),
                device_id=getattr(account, "device_id", ""),
                partner_id=identity_for(market, account, cfg).partner_id,
            )
            identity.update({
                "api_host": market_meta.host,
                "page_url": market_meta.warmup,
                "aid": market_meta.aid,
                "user_language": market_meta.user_language,
                "signer_region": market_meta.signer_region,
            })
        except ValueError:
            unavailable[name] = "account_identity_invalid"
            continue
        prepared.append(PreparedHttpAccount(account=account, identity=identity))
    return prepared, unavailable


def _validated_qps(qps: float) -> float:
    if (
        isinstance(qps, bool)
        or not isinstance(qps, (int, float))
        or not math.isfinite(float(qps))
        or not 0 < float(qps) <= 3.0
    ):
        raise ValueError("pure_http_qps_invalid")
    return float(qps)


def _worker_command(
    runtime_dir: Path,
    *,
    qps: float,
    profile_types: tuple[int, ...],
    captcha_attempts: int,
    concurrency: int,
) -> list[str]:
    if (
        not profile_types
        or any(type(value) is not int or value <= 0 for value in profile_types)
        or len(set(profile_types)) != len(profile_types)
    ):
        raise ValueError("pure_http_profile_types_invalid")
    if type(captcha_attempts) is not int or not 1 <= captcha_attempts <= 3:
        raise ValueError("pure_http_captcha_attempts_invalid")
    if type(concurrency) is not int or not 1 <= concurrency <= 16:
        raise ValueError("pure_http_concurrency_invalid")
    return [
        sys.executable,
        "-m",
        "bdhub.enrich.pure_http_worker_child",
        "--runtime-dir",
        str(runtime_dir.resolve()),
        "--qps",
        str(_validated_qps(qps)),
        "--profile-types",
        ",".join(str(value) for value in profile_types),
        "--captcha-attempts",
        str(captcha_attempts),
        "--concurrency",
        str(concurrency),
    ]


def _worker_env(runtime_dir: Path) -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    dependency_dir = runtime_dir.parent / "pydeps"
    # pydeps 只允许 Windows 运行时使用；POSIX 必须使用当前虚拟环境中
    # 安装的原生 wheel，避免加载 os.add_dll_directory 等平台专用初始化代码。
    if os.name == "nt" and dependency_dir.is_dir():
        current = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = str(dependency_dir) + (
            os.pathsep + current if current else ""
        )
    return env


def run_account_batch(
    *,
    runtime_dir: str | Path,
    identity: dict[str, str],
    targets: list[HttpTarget],
    qps: float = DEFAULT_QPS,
    profile_types: tuple[int, ...] = PRODUCTION_PROFILE_TYPES,
    required_fields: tuple[str, ...] = (),
    captcha_attempts: int = DEFAULT_CAPTCHA_ATTEMPTS,
    concurrency: int = DEFAULT_ACCOUNT_CONCURRENCY,
    on_result: Callable[[HttpItemResult], None] | None = None,
    should_stop: Callable[[], str | None] | None = None,
) -> HttpBatchResult:
    """运行一个账号批次；账号内部并发共享该账号自己的严格 QPS 节拍。"""
    if not targets:
        return HttpBatchResult(())
    runtime = Path(runtime_dir).resolve()
    command = _worker_command(
        runtime,
        qps=qps,
        profile_types=profile_types,
        captcha_attempts=captcha_attempts,
        concurrency=concurrency,
    )
    request = {
        "account": dict(identity),
        "required_fields": list(required_fields),
        "targets": [
            {
                "handle": target.handle,
                "oec_id": target.oec_id,
                **(
                    {"audit_handle": target.audit_handle}
                    if target.audit_handle
                    else {}
                ),
            }
            for target in targets
        ],
    }
    results_by_index: dict[int, HttpItemResult] = {}
    request_count = challenge_count = challenge_success_count = 0
    fatal_error_code = ""
    early_stop = ""

    def consume(lines: Iterable[str]) -> None:
        nonlocal request_count, challenge_count, challenge_success_count
        nonlocal fatal_error_code, early_stop
        for line in lines:
            try:
                event = json.loads(line)
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(event, dict):
                continue
            if event.get("type") == "fatal":
                code = str(event.get("error_code") or "")
                fatal_error_code = (
                    code
                    if code in _INFRASTRUCTURE_ERROR_CODES
                    else "worker_start_failed"
                )
                continue
            if event.get("type") == "summary":
                request_count = int(event.get("request_count") or 0)
                challenge_count = int(event.get("challenge_count") or 0)
                challenge_success_count = int(
                    event.get("challenge_success_count") or 0
                )
                continue
            if event.get("type") != "result":
                continue
            index = event.get("index")
            if type(index) is not int or not 1 <= index <= len(targets):
                continue
            if index in results_by_index:
                continue
            outcome = str(event.get("outcome") or "protocol_error")
            profile = event.get("profile")
            if profile is not None and not isinstance(profile, dict):
                profile = None
                outcome = "protocol_error"
            item = HttpItemResult(
                target=targets[index - 1],
                outcome=outcome,
                profile=profile,
                error_code=str(event.get("error_code") or ""),
                remote_code=_safe_remote_code(event.get("remote_code")),
            )
            results_by_index[index] = item
            delta = event.get("request_count_delta")
            if type(delta) is int and delta >= 0:
                request_count += delta
            if on_result is not None:
                on_result(item)
            if should_stop is not None:
                reason = should_stop()
                if reason in {"shared_platform_cooldown", "shared_backoff_state_invalid"}:
                    early_stop = reason
                    break

    timeout = max(180.0, len(targets) * 2.0)
    timed_out = False
    returncode = -1
    if on_result is None and should_stop is None:
        try:
            completed = subprocess.run(
                command,
                cwd=str(cfgmod.ROOT),
                env=_worker_env(runtime),
                input=json.dumps(request, ensure_ascii=False),
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            # subprocess.run超时仍携带已输出结果，保留完成项，只把未返回项
            # 标为超时，防止整批已成功画像被丢弃。
            partial = exc.stdout or ""
            if isinstance(partial, bytes):
                partial = partial.decode("utf-8", errors="replace")
            consume(partial.splitlines())
            timed_out = True
        else:
            consume(str(completed.stdout or "").splitlines())
            returncode = completed.returncode
    else:
        process = subprocess.Popen(
            command,
            cwd=str(cfgmod.ROOT),
            env=_worker_env(runtime),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        expired = Event()

        def terminate_on_timeout() -> None:
            expired.set()
            try:
                process.kill()
            except OSError:
                pass

        timer = Timer(timeout, terminate_on_timeout)
        timer.daemon = True
        try:
            if process.stdin is None or process.stdout is None:
                raise RuntimeError("pure_http_worker_pipe_unavailable")
            process.stdin.write(json.dumps(request, ensure_ascii=False))
            process.stdin.close()
            timer.start()
            consume(process.stdout)
            if early_stop and process.poll() is None:
                process.terminate()
                try:
                    returncode = process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    returncode = process.wait(timeout=5)
            else:
                returncode = process.wait()
            timed_out = expired.is_set()
        finally:
            timer.cancel()
            if process.poll() is None:
                process.kill()
                process.wait()
            if process.stdout is not None:
                process.stdout.close()

    if early_stop:
        fallback = early_stop
    elif timed_out:
        fallback = "worker_timeout"
    elif fatal_error_code:
        fallback = fatal_error_code
    elif returncode:
        fallback = "worker_failed"
    else:
        fallback = "protocol_error"
    results = tuple(
        results_by_index.get(
            index,
            HttpItemResult(target, fallback, error_code=fallback),
        )
        for index, target in enumerate(targets, 1)
    )
    return HttpBatchResult(
        results=results,
        request_count=request_count,
        challenge_count=challenge_count,
        challenge_success_count=challenge_success_count,
    )


def _partition_targets(
    targets: list[HttpTarget],
    count: int,
) -> list[list[HttpTarget]]:
    shards = [[] for _ in range(min(count, len(targets)))]
    for index, target in enumerate(targets):
        shards[index % len(shards)].append(target)
    return shards


def _run_prepared_batch(
    prepared: PreparedHttpAccount,
    targets: list[HttpTarget],
    *,
    runtime_dir: Path,
    qps: float,
    profile_types: tuple[int, ...],
    required_fields: tuple[str, ...],
    captcha_attempts: int,
    market: str,
    on_result: Callable[[HttpItemResult], None] | None = None,
) -> HttpBatchResult:
    account = prepared.account
    guard = shared_backoff.snapshot(prepared.identity, market)
    if guard["open"]:
        return HttpBatchResult(tuple(
            HttpItemResult(target, guard["code"], error_code=guard["code"])
            for target in targets
        ))

    backoff_write_failed = False

    def completed(item: HttpItemResult) -> None:
        nonlocal backoff_write_failed
        try:
            shared_backoff.observe(
                prepared.identity, market, str(account.name), outcome=item.outcome,
                error_code=item.error_code, remote_code=item.remote_code,
            )
        except (OSError, ValueError):
            backoff_write_failed = True
        if on_result is not None:
            on_result(item)

    def stop_reason() -> str | None:
        if backoff_write_failed:
            return "shared_backoff_state_invalid"
        current = shared_backoff.snapshot(prepared.identity, market)
        return current["code"] if current["open"] else None

    profile_dir = getattr(account, "profile_dir", None)
    if profile_dir is None:
        return run_account_batch(
            runtime_dir=runtime_dir,
            identity=prepared.identity,
            targets=targets,
            qps=qps,
            profile_types=profile_types,
            required_fields=required_fields,
            captcha_attempts=captcha_attempts,
            on_result=completed,
            should_stop=stop_reason,
        )
    lease = ProfileLease(
        profile_dir,
        account=str(account.name),
        market=market,
        operation="pure-http-worker",
    )
    with lease:
        if scheduled_relogin_svc.maintenance_due(
            account, initialize=False, ignore_retry_throttle=True,
        ):
            return HttpBatchResult(tuple(
                HttpItemResult(target, "scheduled_relogin_due", error_code="scheduled_relogin_due")
                for target in targets
            ))
        return run_account_batch(
            runtime_dir=runtime_dir,
            identity=prepared.identity,
            targets=targets,
            qps=qps,
            profile_types=profile_types,
            required_fields=required_fields,
            captcha_attempts=captcha_attempts,
            on_result=completed,
            should_stop=stop_reason,
        )


def run_pool_rounds(
    prepared_accounts: list[PreparedHttpAccount],
    targets: list[HttpTarget],
    *,
    runtime_dir: str | Path,
    qps: float = DEFAULT_QPS,
    profile_types: tuple[int, ...] = PRODUCTION_PROFILE_TYPES,
    captcha_attempts: int = DEFAULT_CAPTCHA_ATTEMPTS,
    max_item_attempts: int = DEFAULT_MAX_ITEM_ATTEMPTS,
    market: str = "mx",
    progress_callback: Callable[[int, int], None] | None = None,
    attempt_callback: Callable[[HttpItemResult, str], None] | None = None,
) -> HttpPoolResult:
    if not prepared_accounts:
        raise ValueError("pure_http_no_ready_accounts")
    if type(max_item_attempts) is not int or not 1 <= max_item_attempts <= 4:
        raise ValueError("pure_http_max_item_attempts_invalid")
    unique = list(dict.fromkeys(targets))
    pending = unique
    attempts: dict[tuple[str, str], int] = {}
    attempted_accounts: dict[tuple[str, str], set[str]] = {}
    exact_miss_accounts: dict[tuple[str, str], set[str]] = {}
    succeeded: dict[tuple[str, str], HttpItemResult] = {}
    failed: dict[tuple[str, str], HttpItemResult] = {}
    request_count = challenge_count = challenge_success_count = 0
    runtime = Path(runtime_dir).resolve()
    required_fields = tuple(sorted(
        required_profile_fields(market) - {"handle", "oec_id"}
    ))
    observed_targets: set[tuple[str, str]] = set()
    progress_lock = Lock()
    last_progress = 0

    def report_result(item: HttpItemResult) -> None:
        nonlocal last_progress
        if progress_callback is None:
            return
        with progress_lock:
            observed_targets.add(item.target.key)
            # 最终成功/失败还要经过重试和入库；实时进度最多到 total-1，
            # 真正的 100% 只由完成入库后的终态行给出。
            done = min(len(observed_targets), max(len(unique) - 1, 0))
            if done <= last_progress:
                return
            last_progress = done
        progress_callback(done, len(unique))

    round_number = 0
    while pending:
        rotation = round_number % len(prepared_accounts)
        round_accounts = (
            prepared_accounts[rotation:] + prepared_accounts[:rotation]
        )
        round_number += 1
        # 重试列表会缩短；简单旋转再分片会把原来分到acc2的唯一失败项
        # 再分到acc2。按每个目标的历史选择未尝试账号，再按负载分配。
        shards = [[] for _ in round_accounts]
        for target in pending:
            seen_accounts = attempted_accounts.get(target.key, set())
            index = min(range(len(round_accounts)), key=lambda i: (
                str(round_accounts[i].account.name) in seen_accounts,
                len(shards[i]), i,
            ))
            shards[index].append(target)
        assignments = [(account, shard) for account, shard in zip(round_accounts, shards) if shard]
        round_results: list[tuple[HttpItemResult, str]] = []
        with ThreadPoolExecutor(
            max_workers=len(assignments),
            thread_name_prefix="pure-http-worker",
        ) as executor:
            futures = {
                executor.submit(
                    _run_prepared_batch,
                    prepared,
                    shard,
                    runtime_dir=runtime,
                    qps=qps,
                    profile_types=profile_types,
                    required_fields=required_fields,
                    captcha_attempts=captcha_attempts,
                    market=market,
                    on_result=report_result if progress_callback else None,
                ): (prepared, shard)
                for prepared, shard in assignments
            }
            for future in as_completed(futures):
                prepared, shard = futures[future]
                try:
                    batch = future.result()
                except Exception:
                    batch = HttpBatchResult(tuple(
                        HttpItemResult(
                            target,
                            "worker_failed",
                            error_code="worker_failed",
                        )
                        for target in shard
                    ))
                request_count += batch.request_count
                challenge_count += batch.challenge_count
                challenge_success_count += batch.challenge_success_count
                round_results.extend(
                    (
                        item,
                        str(prepared.account.name),
                    )
                    for item in batch.results
                )

        next_pending: list[HttpTarget] = []
        for item, account_name in round_results:
            key = item.target.key
            if item.error_code in {"shared_platform_cooldown", "shared_backoff_state_invalid"}:
                failed[key] = item
                continue
            attempts[key] = attempts.get(key, 0) + 1
            attempted_accounts.setdefault(key, set()).add(account_name)
            if item.outcome == "find_exact_miss":
                exact_miss_accounts.setdefault(key, set()).add(account_name)
            item = HttpItemResult(
                target=item.target,
                outcome=item.outcome,
                profile=item.profile,
                error_code=item.error_code,
                remote_code=item.remote_code,
                attempt_count=attempts[key],
                account_count=len(attempted_accounts[key]),
                exact_miss_account_count=len(exact_miss_accounts.get(key, set())),
            )
            if attempt_callback is not None:
                attempt_callback(item, account_name)
            if item.outcome == "ok" and isinstance(item.profile, dict):
                succeeded[key] = item
                failed.pop(key, None)
                continue
            if attempts[key] < max_item_attempts:
                next_pending.append(item.target)
            else:
                failed[key] = item
        pending = list(dict.fromkeys(next_pending))

    return HttpPoolResult(
        succeeded=tuple(succeeded.values()),
        failed=tuple(failed.values()),
        request_count=request_count,
        challenge_count=challenge_count,
        challenge_success_count=challenge_success_count,
    )


def _pool_infrastructure_error(pool: HttpPoolResult) -> str | None:
    """识别“尚未发出任何请求就整批失败”的 worker/环境故障。"""
    if pool.succeeded or not pool.failed or pool.request_count:
        return None
    codes = {
        result.error_code or result.outcome
        for result in pool.failed
    }
    if not codes or not codes.issubset(_INFRASTRUCTURE_ERROR_CODES):
        return None
    if len(codes) == 1:
        return next(iter(codes))
    return "worker_infrastructure_failed"


def _verified_exact_miss(item: HttpItemResult) -> bool:
    return (
        item.outcome == "find_exact_miss"
        and item.attempt_count >= 2
        and item.exact_miss_account_count >= 2
        and bool(item.target.handle)
    )


def _failure_reason_counts(items: Iterable[HttpItemResult]) -> dict[str, int]:
    """只输出短错误码统计，不把达人、账号或响应正文写进任务日志。"""
    counts: Counter[str] = Counter()
    for item in items:
        if _verified_exact_miss(item):
            continue
        raw = _terminal_reason(item)
        code = (
            raw
            if raw.replace("_", "").replace("-", "").isalnum()
            and len(raw) <= 64
            else "pure_http_failed"
        )
        counts[code] += 1
    return dict(sorted(counts.items()))


def _terminal_reason(item: HttpItemResult) -> str:
    """生成可展示、可持久化且不包含响应正文的最终失败码。"""
    base = str(item.error_code or item.outcome or "pure_http_failed")
    remote = _safe_remote_code(item.remote_code)
    combined = f"{base}_{remote}" if remote else base
    return (
        combined
        if combined.replace("_", "").replace("-", "").isalnum()
        and len(combined) <= 64
        else "pure_http_failed"
    )


def _attempt_outcome_kind(item: HttpItemResult) -> OutcomeKind:
    code = str(item.error_code or item.outcome or "").casefold()
    remote_code = _safe_remote_code(item.remote_code)
    if item.outcome == "ok":
        return OutcomeKind.OK
    if item.outcome == "find_exact_miss":
        return OutcomeKind.EXACT_MISS
    if "auth" in code or remote_code in {"10001", "98000001", "98000002"}:
        return OutcomeKind.AUTH_EXPIRED
    if (
        "profile_wall" in code
        or "challenge" in code
        or remote_code == "10000"
    ):
        return OutcomeKind.PROFILE_WALL
    if "thrott" in code or remote_code in {"429", "16901008"}:
        return OutcomeKind.THROTTLED
    if any(marker in code for marker in ("request", "network", "timeout")):
        return OutcomeKind.NETWORK_ERROR
    return OutcomeKind.PROTOCOL_ERROR


def _durable_retry_budget(item: dict[str, Any]) -> RetryBudget:
    """把数据库计数转换为统一工作流预算；缺字段只兼容旧 run/测试桩。"""
    return RetryBudget(
        total_attempts=max(0, int(item.get("total_attempts") or 0)),
        error_retries=max(0, int(item.get("error_retries") or 0)),
        persistence_retries=max(
            0,
            int(item.get("persistence_retries") or 0),
        ),
    )


def _durable_retry_delay(item: dict[str, Any]) -> int:
    retry_index = min(
        max(0, int(item.get("error_retries") or 0)),
        len(_TRANSIENT_RETRY_DELAYS_SECONDS) - 1,
    )
    return _TRANSIENT_RETRY_DELAYS_SECONDS[retry_index]


def _persistence_retry_delay(item: dict[str, Any]) -> int:
    retry_index = min(
        max(0, int(item.get("persistence_retries") or 0)),
        len(_TRANSIENT_RETRY_DELAYS_SECONDS) - 1,
    )
    return _TRANSIENT_RETRY_DELAYS_SECONDS[retry_index]


def _persistence_decision(item: dict[str, Any]):
    return decide_transition(
        Outcome.failure(
            OutcomeKind.INVALID_INPUT,
            subject=str(item.get("oec_id") or ""),
            retryable=False,
            detail="persistence_error",
        ),
        _durable_retry_budget(item),
        failure_class="persistence",
    )


def _apply_persistence_failure(
    runs: Any,
    item: dict[str, Any],
    *,
    clock: Callable[[], datetime],
    worker_errors: list[str],
) -> None:
    """把画像/任务写库失败转成独立、有界的持久化重试。"""
    decision = _persistence_decision(item)
    observed_at = clock()
    if decision.state is ItemState.RETRY_WAIT:
        runs.retry(
            item["item_id"],
            decision,
            observed_at + timedelta(
                seconds=_persistence_retry_delay(item),
            ),
            now=observed_at,
        )
        return
    runs.terminate(
        item["item_id"],
        decision.state,
        decision.reason,
    )
    worker_errors.append(decision.reason)


def _reconcile_persisted_snapshot(
    profile_store: Any,
    idempotency_key: str,
    *,
    expected_oec: str,
):
    """兼容旧自定义 Store；正式 ProfileStore 始终提供幂等重放接口。"""
    reconcile = getattr(profile_store, "reconcile_snapshot", None)
    if not callable(reconcile):
        return None
    return reconcile(idempotency_key, expected_oec=expected_oec)


def _snapshot_quality(result: Any) -> SnapshotQuality | None:
    raw = getattr(result, "quality", None)
    try:
        return SnapshotQuality(str(raw))
    except (TypeError, ValueError):
        return None


def _next_durable_retry_at(runs: Any, run_id: str) -> datetime | None:
    """读取最早持久化重试时间；兼容不提供摘要的旧测试桩。"""
    retry_summary = getattr(runs, "retry_summary", None)
    if not callable(retry_summary):
        return None
    summary = retry_summary(run_id)
    raw = summary.get("retry_at") if isinstance(summary, dict) else None
    reason = (
        str(summary.get("pause_reason") or "").strip().casefold()
        if isinstance(summary, dict)
        else ""
    )
    if reason not in _AUTO_RETRY_REASONS:
        return None
    if not raw:
        return None
    if isinstance(raw, datetime):
        parsed = raw
    else:
        try:
            parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _wait_for_durable_retry(
    retry_at: datetime,
    *,
    stop_event: Event,
    clock: Callable[[], datetime],
    wait: Callable[[float], bool],
) -> bool:
    """无轮询地等到重试时间；返回 False 表示收到协作式取消。"""
    if stop_event.is_set():
        return False
    delay = max(0.0, (retry_at - clock()).total_seconds())
    if delay <= 0:
        return True
    interrupted = bool(wait(delay))
    return not interrupted and not stop_event.is_set()


def run_pairs(
    pairs: list[tuple[str, str]],
    selected_accounts: list[tuple[str, Any]],
    cfg: Any,
    *,
    qps: float = DEFAULT_QPS,
    label: str = "纯 HTTP Profile[1,2,6] 刷新",
    market: str = "mx",
    run_id: str | None = None,
    route: str = "oec_profile126_pure_http",
    engine: Any = None,
    quiet: bool = False,
    profile_types: tuple[int, ...] = PRODUCTION_PROFILE_TYPES,
    runtime_dir: str | Path = _DEFAULT_RUNTIME,
    retry_stop_event: Event | None = None,
    retry_wait: Callable[[float], bool] | None = None,
    utc_now: Callable[[], datetime] | None = None,
) -> HttpDurableResult:
    """消费已知 OEC durable run；handle 只作审计，网络请求以 OEC 为主键。"""
    from ..hub.markets import require_capability

    require_capability(market, "profile")
    require_capability(market, "pure_http")
    if not selected_accounts:
        raise ValueError("pure_http_accounts_required")
    runtime = validate_runtime(runtime_dir)
    eng = engine or get_engine(cfg)
    runs = EnrichRunStore(eng)
    clock = utc_now or (lambda: datetime.now(timezone.utc))
    stop_event = retry_stop_event or Event()
    wait_for_stop = retry_wait or stop_event.wait
    is_new_run = run_id is None
    if is_new_run:
        run_id = runs.create_run(
            market,
            CaptureKind.FULL,
            route,
            {
                "label": label,
                "qps": _validated_qps(qps),
                "accounts": [name for name, _account in selected_accounts],
                "source_count": len(pairs),
                "profile_types": list(profile_types),
                "route": route,
            },
        )
    else:
        existing = runs.get_run(run_id)
        if existing is None:
            raise ValueError("pure_http_run_not_found")
        if str(existing["bd_market"]) != market:
            raise ValueError("pure_http_run_market_mismatch")

    # 已知 OEC 是稳定实体主键；handle 只作为任务审计快照，不能参与 Profile
    # 请求或结果校验，否则达人改名后会被旧 handle 永久挡住。
    pair_by_oec: dict[str, tuple[str, str]] = {}
    for handle, oec_id in pairs:
        clean_oec = str(oec_id or "").strip()
        if not clean_oec:
            continue
        pair_by_oec.setdefault(clean_oec, (norm_handle(handle), clean_oec))
    clean_pairs = list(pair_by_oec.values())
    if any(not oec_id.isdigit() for _handle, oec_id in clean_pairs):
        raise ValueError("pure_http_target_oec_invalid")
    enqueued = runs.enqueue(
        run_id,
        [
            {
                "bd_market": market,
                "oec_id": oec_id,
                "requested_handle": handle or None,
                "capture_kind": CaptureKind.FULL,
            }
            for handle, oec_id in clean_pairs
        ],
    )
    now = clock()
    runs.release_stale_claims(
        run_id,
        older_than=now - timedelta(minutes=15),
        now=now,
    )
    runs.set_run_status(run_id, "running", now=now)

    requested = [account for _name, account in selected_accounts]
    prepared, unavailable = prepare_collection_accounts(
        cfg,
        requested,
        market=market,
        requested_names=[name for name, _account in selected_accounts],
    )
    if not prepared:
        runs.set_run_status(run_id, "paused")
        return HttpDurableResult(
            run_id=run_id,
            enqueued=enqueued,
            counts=runs.counts(run_id),
            status="paused",
            worker_stats={
                name: HttpWorkerStats(
                    cooldown=True,
                    cooldown_kind="account_unavailable",
                    detail_code=reason,
                )
                for name, reason in unavailable.items()
            },
            worker_errors=tuple(sorted(set(unavailable.values()))),
        )

    profile_store = store_for(market)
    worker_errors: list[str] = []
    request_count = 0
    challenge_count = 0
    challenge_success_count = 0
    try:
        while True:
            if stop_event.is_set():
                break
            claimed = runs.claim_batch(
                run_id,
                "pure_http_pool",
                clock(),
                limit=DURABLE_PROGRESS_BATCH_SIZE,
            )
            if not claimed:
                retry_at = _next_durable_retry_at(runs, run_id)
                if retry_at is not None and retry_at > clock():
                    if not quiet:
                        print(
                            f"  [{label}] run={run_id} retry_wait "
                            f"retry_at={retry_at.isoformat()}",
                            flush=True,
                        )
                    if _wait_for_durable_retry(
                        retry_at,
                        stop_event=stop_event,
                        clock=clock,
                        wait=wait_for_stop,
                    ):
                        continue
                break

            targets: list[HttpTarget] = []
            item_by_oec: dict[str, dict[str, Any]] = {}
            for item in claimed:
                item_oec = str(item.get("oec_id") or "")
                current_pair = pair_by_oec.get(item_oec, ("", item_oec))
                target = HttpTarget(
                    oec_id=item_oec,
                    audit_handle=str(
                        item.get("requested_handle")
                        or current_pair[0]
                        or ""
                    ),
                )
                if target.oec_id in item_by_oec:
                    raise RuntimeError("pure_http_durable_target_duplicate")
                item_by_oec[target.oec_id] = item

                # 每次认领都先核对固定 run_id:item_id：即使 succeeded 与随后
                # retry 连续写失败，stale claim 恢复时 persistence_retries 仍可能
                # 是 0；不能依赖计数判断是否已有完整提交的画像快照。
                idempotency_key = f"{run_id}:{item['item_id']}"
                try:
                    recovered = _reconcile_persisted_snapshot(
                        profile_store,
                        idempotency_key,
                        expected_oec=item_oec,
                    )
                    if recovered is not None:
                        recovered_quality = _snapshot_quality(recovered)
                        if recovered_quality is SnapshotQuality.PARTIAL:
                            runs.terminate(
                                item["item_id"],
                                ItemState.UNRESOLVED,
                                "snapshot_quality_partial",
                            )
                            worker_errors.append("snapshot_quality_partial")
                            continue
                        if recovered_quality is not SnapshotQuality.ACCEPTED:
                            runs.terminate(
                                item["item_id"],
                                ItemState.DEAD_LETTER,
                                "snapshot_quality_invalid",
                            )
                            worker_errors.append("snapshot_quality_invalid")
                            continue
                        runs.succeed(
                            item["item_id"],
                            recovered.snapshot_id,
                        )
                        continue
                except SnapshotIdentityMismatch:
                    runs.terminate(
                        item["item_id"],
                        ItemState.DEAD_LETTER,
                        "snapshot_identity_mismatch",
                    )
                    worker_errors.append("snapshot_identity_mismatch")
                    continue
                except SnapshotReplayInvalid:
                    runs.terminate(
                        item["item_id"],
                        ItemState.DEAD_LETTER,
                        "snapshot_replay_invalid",
                    )
                    worker_errors.append("snapshot_replay_invalid")
                    continue
                except SQLAlchemyError:
                    _apply_persistence_failure(
                        runs,
                        item,
                        clock=clock,
                        worker_errors=worker_errors,
                    )
                    continue

                # 首次画像事务没有提交时不会有可复用快照；此时才重新请求。
                targets.append(target)

            def persist_attempt(
                attempt_result: HttpItemResult,
                account_name: str,
            ) -> None:
                item = item_by_oec[attempt_result.target.oec_id]
                observed_at = clock()
                try:
                    runs.record_attempt(item["item_id"], {
                        "account": account_name,
                        "route": route,
                        "outcome_kind": _attempt_outcome_kind(attempt_result),
                        "remote_code": (
                            _safe_remote_code(attempt_result.remote_code) or None
                        ),
                        "detail_code": (
                            attempt_result.error_code
                            or attempt_result.outcome
                            or None
                        ),
                        "started_at": observed_at,
                        "finished_at": observed_at,
                    })
                except (OSError, RuntimeError, ValueError, SQLAlchemyError):
                    # 诊断明细失败不能吞掉已经取得的业务画像；任务终态仍以
                    # item/snapshot 为准，并暴露短错误码供运维处理。
                    worker_errors.append("attempt_persist_failed")

            pool = (
                run_pool_rounds(
                    prepared,
                    targets,
                    runtime_dir=runtime,
                    qps=qps,
                    profile_types=profile_types,
                    captcha_attempts=DEFAULT_CAPTCHA_ATTEMPTS,
                    market=market,
                    attempt_callback=persist_attempt,
                )
                if targets
                else HttpPoolResult(succeeded=(), failed=())
            )
            request_count += pool.request_count
            challenge_count += pool.challenge_count
            challenge_success_count += pool.challenge_success_count

            infrastructure_error = _pool_infrastructure_error(pool)
            if infrastructure_error:
                # 平台依赖、进程启动或协议级故障不是达人数据结论。释放本批
                # claim 并暂停断点，禁止把整库错误标成 unresolved。
                runs.pause_run(run_id)
                worker_errors.append(infrastructure_error)
                break

            for result in pool.succeeded:
                item = item_by_oec[result.target.oec_id]
                try:
                    snapshot_id = ingest_success(
                        result.target,
                        result.profile or {},
                        store=profile_store,
                        market=market,
                        idempotency_key=f"{run_id}:{item['item_id']}",
                    )
                except (ValueError, CreatorIdentityConflict) as error:
                    raw_reason = str(error or "")
                    reason = (
                        raw_reason
                        if raw_reason.isascii()
                        and raw_reason.replace("_", "").replace("-", "").isalnum()
                        and len(raw_reason) <= 64
                        else "ingest_failed"
                    )
                    runs.terminate(
                        item["item_id"],
                        ItemState.UNRESOLVED,
                        reason,
                    )
                    worker_errors.append(reason)
                    continue
                except SnapshotIdentityMismatch:
                    runs.terminate(
                        item["item_id"],
                        ItemState.DEAD_LETTER,
                        "snapshot_identity_mismatch",
                    )
                    worker_errors.append("snapshot_identity_mismatch")
                    continue
                except SQLAlchemyError:
                    _apply_persistence_failure(
                        runs,
                        item,
                        clock=clock,
                        worker_errors=worker_errors,
                    )
                    continue
                try:
                    runs.succeed(item["item_id"], snapshot_id)
                except SQLAlchemyError:
                    _apply_persistence_failure(
                        runs,
                        item,
                        clock=clock,
                        worker_errors=worker_errors,
                    )
            for result in pool.failed:
                if result.error_code in {"shared_platform_cooldown", "shared_backoff_state_invalid"}:
                    # 共享故障暂停任务，不消耗达人重试预算，也不写unresolved。
                    runs.pause_run(run_id)
                    worker_errors.append(result.error_code)
                    break
                item = item_by_oec[result.target.oec_id]
                safe_reason = _terminal_reason(result)
                outcome_kind = _attempt_outcome_kind(result)
                decision = decide_transition(
                    Outcome.failure(
                        outcome_kind,
                        subject=result.target.oec_id,
                        retryable=True,
                        remote_code=result.remote_code or None,
                        detail=safe_reason,
                    ),
                    _durable_retry_budget(item),
                )
                if decision.state is ItemState.RETRY_WAIT:
                    observed_at = clock()
                    runs.retry(
                        item["item_id"],
                        decision,
                        observed_at + timedelta(
                            seconds=_durable_retry_delay(item),
                        ),
                        now=observed_at,
                    )
                    continue
                runs.terminate(
                    item["item_id"],
                    decision.state,
                    safe_reason,
                )
                worker_errors.append(safe_reason)

            if any(item.error_code in {"shared_platform_cooldown", "shared_backoff_state_invalid"} for item in pool.failed):
                break

            counts = runs.counts(run_id)
            if not quiet:
                done = sum(
                    counts.get(state, 0)
                    for state in ("succeeded", "unresolved", "dead_letter")
                )
                total = done + sum(
                    counts.get(state, 0)
                    for state in ("queued", "running", "retry_wait")
                )
                print(
                    f"  [{label}] run={run_id} progress={done}/{total} "
                    f"ok={counts.get('succeeded', 0)} "
                    f"failed={counts.get('unresolved', 0) + counts.get('dead_letter', 0)}",
                    flush=True,
                )
    finally:
        profile_store.close()

    counts = runs.counts(run_id)
    remaining = sum(
        counts.get(state, 0)
        for state in ("queued", "running", "retry_wait")
    )
    run_reader = getattr(runs, "get_run", None)
    current_run = run_reader(run_id) if callable(run_reader) else None
    current_status = str((current_run or {}).get("status") or "")
    if current_status == "cancelled":
        status = "cancelled"
    else:
        status = "paused" if remaining else "completed"
        runs.set_run_status(run_id, status)
    if not quiet:
        print(
            f"  [{label}] run={run_id} status={status} "
            f"ok={counts.get('succeeded', 0)} "
            f"unresolved={counts.get('unresolved', 0)} "
            f"requests={request_count} sliders="
            f"{challenge_success_count}/{challenge_count}",
            flush=True,
        )
    return HttpDurableResult(
        run_id=run_id,
        enqueued=enqueued,
        counts=counts,
        status=status,
        worker_stats={
            prepared_account.account.name: HttpWorkerStats(
                detail_code="ready",
            )
            for prepared_account in prepared
        },
        worker_errors=tuple(sorted(set(worker_errors))),
    )


def ingest_success(
    target: HttpTarget,
    raw_profile: dict[str, Any],
    *,
    store: Any,
    market: str,
    idempotency_key: str | None = None,
    captured_at: datetime | None = None,
) -> str:
    normalized_raw = raw_profile
    handle_field = raw_profile.get("handle")
    returned_handle = (
        handle_field.get("value")
        if isinstance(handle_field, dict)
        else None
    )
    if target.audit_handle and not norm_handle(returned_handle):
        normalized_raw = dict(raw_profile)
        normalized_raw["handle"] = {
            **(handle_field if isinstance(handle_field, dict) else {}),
            "value": target.audit_handle,
        }
    try:
        profile = parse_find_item(normalized_raw)
    except Exception as exc:
        raise ValueError("pure_http_profile_parse_error") from exc
    handle = norm_handle(profile.handle)
    if (
        not handle
        or not profile.oec_id
        or (target.handle and handle != target.handle)
        or (target.oec_id and profile.oec_id != target.oec_id)
    ):
        raise ValueError("pure_http_identity_mismatch")
    capture = new_capture_context(
        profile,
        market=market,
        kind=CaptureKind.FULL,
        route=(
            "find_profile126_pure_http"
            if target.handle
            else "oec_profile126_pure_http"
        ),
        expected_oec=target.oec_id or profile.oec_id,
        idempotency_key=idempotency_key,
        captured_at=captured_at or datetime.now(timezone.utc),
    )
    result = store.upsert(handle, profile, capture=capture)
    quality = _snapshot_quality(result)
    if quality is SnapshotQuality.PARTIAL:
        raise ValueError("pure_http_snapshot_partial")
    if quality is not SnapshotQuality.ACCEPTED:
        raise ValueError("pure_http_snapshot_degraded")
    store.clear_miss(
        handle,
        observed_at=getattr(result, "captured_at", capture.captured_at),
    )
    return str(result.snapshot_id)


def _load_handle_targets(
    handles_file: str | Path,
    *,
    limit: int | None,
    offset: int,
) -> list[HttpTarget]:
    if type(offset) is not int or offset < 0:
        raise ValueError("handle_offset_invalid")
    if limit is not None and (type(limit) is not int or limit <= 0):
        raise ValueError("handle_limit_invalid")
    path = Path(handles_file)
    if not path.is_absolute():
        path = cfgmod.ROOT / path
    handles = read_bounded_handles(path)
    selected = handles[offset:(offset + limit) if limit else None]
    return [HttpTarget(handle=handle) for handle in selected]


def run(
    handles_file: str | Path,
    *,
    market: str = "mx",
    account_names: list[str] | None = None,
    qps: float = DEFAULT_QPS,
    refresh: bool = False,
    limit: int | None = None,
    offset: int = 0,
    runtime_dir: str | Path = _DEFAULT_RUNTIME,
    config_path: str | Path | None = None,
    retain_profiles: bool = True,
) -> HttpPoolResult:
    from ..hub.markets import require_capability

    require_capability(market, "find")
    require_capability(market, "profile")
    require_capability(market, "pure_http")
    _validated_qps(qps)
    targets = _load_handle_targets(handles_file, limit=limit, offset=offset)
    cfg = cfgmod.load(config_path)
    store = store_for(market)
    try:
        resolve_known = getattr(store, "resolved_current_identities", None)
        identities = resolve_known([target.handle for target in targets]) if callable(resolve_known) else {}
        if not refresh:
            fresh_reader = getattr(store, "complete_fresh_keys_for_handles", store.fresh_keys_for_handles)
            fresh = fresh_reader(
                [target.handle for target in targets],
                cfg.refresh_days,
            )
            missed_reader = getattr(store, "missed_keys", None)
            missed = (
                set(missed_reader([target.handle for target in targets]))
                if callable(missed_reader)
                else set()
            )
            targets = [
                target
                for target in targets
                if target.handle not in fresh and (target.handle not in missed or target.handle in identities)
            ]
        if not targets:
            print("[进度] 0/0 ok=0 miss=0 system_failed=0 requests=0 sliders=0/0 stage=profile2", flush=True)
            print("[handle-profile] run=handles status=completed stage=profile2", flush=True)
            return HttpPoolResult((), ())
        if callable(resolve_known):
            targets = list(dict.fromkeys(
                HttpTarget(oec_id=identities[target.handle]["oec_id"], audit_handle=identities[target.handle]["audit_handle"])
                if target.handle in identities else target for target in targets
            ))
        runtime = validate_runtime(runtime_dir)
        prepared, unavailable = prepare_collection_accounts(cfg, cfgmod.load_accounts(cfg), market=market, requested_names=account_names)
        if not prepared:
            print(f"[进度] 0/{len(targets)} ok=0 miss=0 system_failed={len(targets)} requests=0 sliders=0/0 stage=profile2", flush=True)
            print("[handle-profile] run=handles status=paused pause_reason=pure_http_no_ready_accounts stage=profile2", flush=True)
            return HttpPoolResult((), tuple(HttpItemResult(target, "pure_http_no_ready_accounts", error_code="pure_http_no_ready_accounts") for target in targets))
        print(
            "[纯HTTP多账号池] "
            f"待抓={len(targets)} 账号={len(prepared)} "
            f"QPS={_validated_qps(qps):g}/账号 Profile=[1,2,6]",
            flush=True,
        )
        progress_interval = max(1, math.ceil(len(targets) / 100))
        progress_state = {"last": 0}
        progress_lock = Lock()

        def emit_live_progress(done: int, total: int) -> None:
            with progress_lock:
                if done - progress_state["last"] < progress_interval:
                    return
                progress_state["last"] = done
            print(
                f"[实时进度] {done}/{total} stage=profile2",
                flush=True,
            )

        ingested_items: list[HttpItemResult] = []
        failed: list[HttpItemResult] = []
        request_count = 0
        challenge_count = 0
        challenge_success_count = 0
        for start in range(0, len(targets), HANDLE_PROGRESS_BATCH_SIZE):
            batch = targets[start:start + HANDLE_PROGRESS_BATCH_SIZE]
            pool = run_pool_rounds(
                prepared,
                batch,
                runtime_dir=runtime,
                qps=qps,
                market=market,
            )
            request_count += pool.request_count
            challenge_count += pool.challenge_count
            challenge_success_count += pool.challenge_success_count
            batch_failed = list(pool.failed)
            for item in batch_failed:
                if _verified_exact_miss(item):
                    store.mark_miss(item.target.handle, _EXACT_MISS_EVIDENCE)
            for item in pool.succeeded:
                try:
                    ingest_success(
                        item.target,
                        item.profile or {},
                        store=store,
                        market=market,
                    )
                    ingested_items.append(item if retain_profiles else replace(item, profile=None))
                except ValueError:
                    batch_failed.append(HttpItemResult(
                        item.target,
                        "ingest_failed",
                        error_code="ingest_failed",
                    ))
            failed.extend(batch_failed)
            paused_reason = next((item.error_code for item in batch_failed if item.error_code in {"shared_platform_cooldown", "shared_backoff_state_invalid"}), None) or _pool_infrastructure_error(pool)
            if paused_reason:
                failed.extend(HttpItemResult(target, paused_reason, error_code=paused_reason)
                              for target in targets[start + len(batch):])
                break
            # 远端响应与画像在本小批已经落库后再报告进度；Dashboard
            # 显示的完成数因此可由中断后的数据库状态恢复。
            emit_live_progress(len(ingested_items) + sum(_verified_exact_miss(item) for item in failed), len(targets))
        result = HttpPoolResult(
            succeeded=tuple(ingested_items),
            failed=tuple(failed),
            request_count=request_count,
            challenge_count=challenge_count,
            challenge_success_count=challenge_success_count,
        )
        missed = sum(1 for item in failed if _verified_exact_miss(item))
        system_failed = len(failed) - missed
        print(
            f"[进度] {len(ingested_items) + missed}/{len(targets)} "
            f"ok={len(ingested_items)} miss={missed} "
            f"system_failed={system_failed} "
            f"requests={request_count} sliders="
            f"{challenge_success_count}/{challenge_count} "
            f"unavailable_accounts={len(unavailable)} stage=profile2",
            flush=True,
        )
        failure_reasons = _failure_reason_counts(failed)
        if failure_reasons:
            print(
                "[失败原因] "
                + " ".join(
                    f"{code}={count}"
                    for code, count in failure_reasons.items()
                ),
                flush=True,
            )
        print(f"[handle-profile] run=handles status={'paused' if system_failed else 'completed'} stage=profile2", flush=True)
        return result
    finally:
        store.close()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="纯 HTTP 多账号 Find + Profile[1,2,6] 富化池")
    parser.add_argument("--handles-file", required=True)
    parser.add_argument("--market", default="mx")
    parser.add_argument("--accounts")
    parser.add_argument("--qps", type=float, default=DEFAULT_QPS)
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--runtime-dir", default=str(_DEFAULT_RUNTIME))
    # 兼容旧前端 enrich 命令；HTTP 生产链路固定执行完整闭环。
    parser.add_argument("--dimensions", default="l1,profile2")
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    dimensions = {
        item.strip().lower()
        for item in str(args.dimensions).split(",")
        if item.strip()
    }
    if not dimensions or dimensions - {"l1", "profile2"} or "l1" not in dimensions:
        raise ValueError("pure_http_dimensions_invalid")
    result = run(
        args.handles_file,
        market=args.market,
        account_names=(
            [item.strip() for item in args.accounts.split(",") if item.strip()]
            if args.accounts
            else None
        ),
        qps=args.qps,
        refresh=args.refresh,
        limit=args.limit,
        offset=args.offset,
        runtime_dir=args.runtime_dir,
        retain_profiles=False,
    )
    # 精确 miss / unresolved 是批次内终态，由日志和 durable 计数展示“部分完成”；
    # 只有未捕获的系统异常才让进程非零退出。
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "DEFAULT_QPS",
    "HttpBatchResult",
    "HttpDurableResult",
    "HttpItemResult",
    "HttpPoolResult",
    "HttpTarget",
    "HttpWorkerStats",
    "PRODUCTION_PROFILE_TYPES",
    "PreparedHttpAccount",
    "ingest_success",
    "prepare_collection_accounts",
    "run",
    "run_account_batch",
    "run_pairs",
    "run_pool_rounds",
]
