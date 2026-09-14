"""所有账号每48小时一次的登录维护，不限制执行时段。"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterator
from uuid import uuid4

from bdhub import config as cfgmod


INTERVAL_HOURS = 48
RETRY_INTERVAL_HOURS = 1
BUSY_RETRY_MINUTES = 10
WINDOW_TIMEZONE = "America/Mexico_City"
WINDOW_START_HOUR = 0
WINDOW_END_HOUR = 24
LOGIN_TIMEOUT_SECONDS = 120
STATE_DIR = cfgmod.ROOT / "data" / "runtime" / "scheduled-relogin"
LOCK_PATH = cfgmod.ROOT / "data" / "runtime" / "scheduled-relogin.lock"


def _aware_utc(value: datetime | None = None) -> datetime:
    observed = value or datetime.now(timezone.utc)
    if observed.tzinfo is None:
        raise ValueError("scheduled_relogin_now_requires_timezone")
    return observed.astimezone(timezone.utc)


def _parse_time(value: object) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def in_maintenance_window(now: datetime | None = None) -> bool:
    """兼容resident/worker调用入口；维护已改为全天可执行。"""
    _aware_utc(now)
    return True


def _state_path(account_name: str, state_dir: Path = STATE_DIR) -> Path:
    return Path(state_dir) / f"{cfgmod.validate_account_name(account_name)}.json"


def _read_state(account_name: str, state_dir: Path = STATE_DIR) -> dict:
    try:
        payload = json.loads(
            _state_path(account_name, state_dir).read_text(encoding="utf-8")
        )
    except (FileNotFoundError, OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_state(account_name: str, payload: dict, state_dir: Path = STATE_DIR) -> None:
    path = _state_path(account_name, state_dir)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_suffix(f".{uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2),
            encoding="utf-8",
        )
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _identity_file_time(account) -> datetime | None:
    try:
        modified = Path(account.headers_json).stat().st_mtime
        return datetime.fromtimestamp(modified, tz=timezone.utc)
    except (AttributeError, OSError, OverflowError, ValueError):
        return None


def initialize_account(
    account,
    *,
    now: datetime | None = None,
    state_dir: Path = STATE_DIR,
) -> dict:
    observed = _aware_utc(now)
    current = _read_state(account.name, state_dir)
    if _parse_time(current.get("baseline_at")) is not None:
        return current
    baseline = _identity_file_time(account) or observed
    payload = {
        "schema_version": 1,
        "account": account.name,
        "baseline_at": baseline.isoformat(),
        "last_attempt_at": current.get("last_attempt_at"),
        "last_success_at": current.get("last_success_at"),
        "decision": current.get("decision"),
        "detail_code": current.get("detail_code"),
        "updated_at": observed.isoformat(),
    }
    _write_state(account.name, payload, state_dir)
    return payload


def _latest_success_or_baseline(account, state: dict, now: datetime) -> datetime:
    return (
        _parse_time(state.get("last_success_at"))
        or _parse_time(state.get("baseline_at"))
        or _identity_file_time(account)
        or now
    )


def maintenance_due(
    account,
    *,
    now: datetime | None = None,
    state_dir: Path = STATE_DIR,
    initialize: bool = True,
    ignore_retry_throttle: bool = False,
) -> bool:
    observed = _aware_utc(now)
    state = (
        initialize_account(account, now=observed, state_dir=state_dir)
        if initialize
        else _read_state(account.name, state_dir)
    )
    latest = _latest_success_or_baseline(account, state, observed)
    if observed - latest < timedelta(hours=INTERVAL_HOURS):
        return False
    last_attempt = _parse_time(state.get("last_attempt_at"))
    last_success = _parse_time(state.get("last_success_at"))
    if (
        not ignore_retry_throttle
        and last_attempt is not None
        and (last_success is None or last_attempt > last_success)
        and observed - last_attempt < _retry_interval(state)
    ):
        return False
    return True


def _retry_interval(state: dict) -> timedelta:
    if state.get("decision") in ("busy", "locked") or state.get("detail_code") in ("profile_busy", "profile_lease_busy"):
        return timedelta(minutes=BUSY_RETRY_MINUTES)
    return timedelta(hours=RETRY_INTERVAL_HOURS)


def _next_window_at(due_at: datetime) -> datetime:
    return _aware_utc(due_at)


def planned_maintenance_at(due_at: datetime) -> datetime:
    """到期即进入维护，不提前或延后到某个时段。"""
    return _aware_utc(due_at)


def maintenance_ready(
    account, *, now: datetime | None = None, state_dir: Path = STATE_DIR,
    ignore_retry_throttle: bool = False,
) -> bool:
    """全天按到期与失败退避执行；业务到期门禁仍忽略重试节流。"""
    observed = _aware_utc(now)
    if not in_maintenance_window(observed):
        return False
    state = _read_state(account.name, state_dir)
    latest = _latest_success_or_baseline(account, state, observed)
    if observed < planned_maintenance_at(latest + timedelta(hours=INTERVAL_HOURS)):
        return False
    attempt = _parse_time(state.get("last_attempt_at"))
    success = _parse_time(state.get("last_success_at"))
    if not ignore_retry_throttle and attempt and (not success or attempt > success):
        if observed - attempt < _retry_interval(state):
            return False
    return True


def public_account_state(
    account,
    *,
    now: datetime | None = None,
    state_dir: Path = STATE_DIR,
) -> dict:
    observed = _aware_utc(now)
    state = _read_state(account.name, state_dir)
    latest = _latest_success_or_baseline(account, state, observed)
    due_at = latest + timedelta(hours=INTERVAL_HOURS)
    last_attempt = _parse_time(state.get("last_attempt_at"))
    last_success = _parse_time(state.get("last_success_at"))
    decision = str(state.get("decision") or "") or None
    attention = bool(
        last_attempt
        and (last_success is None or last_attempt > last_success)
        and decision not in {None, "ready", "busy", "locked"}
    )
    due = observed >= due_at
    next_attempt = max(planned_maintenance_at(due_at), observed)
    if last_attempt and (last_success is None or last_attempt > last_success):
        next_attempt = max(next_attempt, last_attempt + _retry_interval(state))
    return {
        "scheduled_relogin_interval_hours": INTERVAL_HOURS,
        "scheduled_relogin_timezone": WINDOW_TIMEZONE,
        "scheduled_relogin_window": "00:00-24:00",
        "last_scheduled_relogin_at": (
            last_success.isoformat() if last_success is not None else None
        ),
        "last_scheduled_relogin_attempt_at": (
            last_attempt.isoformat() if last_attempt is not None else None
        ),
        "scheduled_relogin_due_at": due_at.isoformat(),
        "next_scheduled_relogin_at": _next_window_at(next_attempt).isoformat(),
        "scheduled_relogin_state": (
            "attention" if attention else "due" if due else "healthy"
        ),
        "scheduled_relogin_decision": decision,
        "scheduled_relogin_detail_code": state.get("detail_code"),
    }


@contextmanager
def maintenance_lock(lock_path: Path = LOCK_PATH) -> Iterator[bool]:
    path = Path(lock_path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    handle = os.fdopen(descriptor, "r+b", buffering=0)
    acquired = False
    try:
        if os.name == "nt":
            import msvcrt

            if os.fstat(handle.fileno()).st_size == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                acquired = True
            except OSError:
                acquired = False
        else:
            import fcntl

            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except BlockingIOError:
                acquired = False
        yield acquired
    finally:
        if acquired:
            try:
                if os.name == "nt":
                    import msvcrt

                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        handle.close()


def _run_command(argv: list[str]) -> subprocess.CompletedProcess[str]:
    from bdhub.auth_command import run_auth_command
    return run_auth_command(argv, cwd=cfgmod.ROOT, timeout=LOGIN_TIMEOUT_SECONDS + 60)


def _public_report(process: subprocess.CompletedProcess[str]) -> dict:
    for line in reversed(str(process.stdout or "").splitlines()):
        try:
            payload = json.loads(line)
        except ValueError:
            continue
        if isinstance(payload, list) and payload and isinstance(payload[0], dict):
            return dict(payload[0])
    return {}


def execute_login(
    account,
    *,
    market: str = "mx",
    runner: Callable[[list[str]], subprocess.CompletedProcess[str]] = _run_command,
) -> dict:
    market = str(market or "mx").lower()
    if not getattr(account, "username", "") or not getattr(account, "password", ""):
        return {
            "success": False,
            "decision": "manual_required",
            "detail_code": "saved_credentials_missing",
            "return_code": None,
        }
    argv = [
        sys.executable,
        "-m",
        "bdhub.enrich.auth",
        "--account",
        account.name,
        "--market",
        str(market or "mx").lower(),
        "--signed-login",
        "--headless-login",
        "--json",
        "--timeout",
        str(LOGIN_TIMEOUT_SECONDS),
    ]
    try:
        process = runner(argv)
        report = _public_report(process)
        if (
            report.get("account") not in (None, account.name)
            or report.get("market") not in (None, market)
            or report.get("capability") not in (None, "partner_http", "signed_find")
        ):
            return {
                "success": False, "decision": "failed",
                "detail_code": "auth_report_identity_mismatch",
                "return_code": process.returncode,
            }
        decision = str(report.get("decision") or "unknown")
        return {
            # Signed Find 可以独立处于冷却；只要真实 Partner HTTP 身份已
            # 验证并落盘，重登维护目标就算成功，因此不绑定 CLI exit code。
            "success": decision == "ready" and (
                process.returncode == 0 or report.get("capability") == "partner_http"
            ),
            "decision": decision,
            "detail_code": str(report.get("detail_code") or "") or None,
            "return_code": process.returncode,
        }
    except (OSError, subprocess.SubprocessError) as exc:
        return {
            "success": False,
            "decision": "failed",
            "detail_code": type(exc).__name__,
            "return_code": None,
        }


def record_result(
    account,
    result: dict,
    *,
    now: datetime | None = None,
    state_dir: Path = STATE_DIR,
) -> dict:
    observed = _aware_utc(now)
    state = initialize_account(account, now=observed, state_dir=state_dir)
    previous = _parse_time(state.get("last_attempt_at"))
    if previous is not None and previous > observed:
        return state
    state.update({
        "last_attempt_at": observed.isoformat(),
        "decision": str(result.get("decision") or "unknown")[:64],
        "detail_code": (
            str(result.get("detail_code") or "")[:100] or None
        ),
        "return_code": result.get("return_code"),
        "updated_at": observed.isoformat(),
    })
    if result.get("success") is True:
        state["last_success_at"] = observed.isoformat()
    _write_state(account.name, state, state_dir)
    return state


def maintain_account(
    account,
    *,
    market: str = "mx",
    now: datetime | None = None,
    runner: Callable[[list[str]], subprocess.CompletedProcess[str]] = _run_command,
    state_dir: Path = STATE_DIR,
    lock_path: Path = LOCK_PATH,
    force: bool = False,
    ignore_retry_throttle: bool = False,
) -> dict:
    observed = _aware_utc(now)
    initialize_account(account, now=observed, state_dir=state_dir)
    if not force and not in_maintenance_window(observed):
        return {"account": account.name, "attempted": False, "reason": "outside_window"}
    if not force and not maintenance_ready(
        account,
        now=observed,
        state_dir=state_dir,
        ignore_retry_throttle=ignore_retry_throttle,
    ):
        return {"account": account.name, "attempted": False, "reason": "not_due"}
    with maintenance_lock(lock_path) as acquired:
        if not acquired:
            return {"account": account.name, "attempted": False, "reason": "locked"}
        if not force and not maintenance_ready(
            account,
            now=observed,
            state_dir=state_dir,
            ignore_retry_throttle=ignore_retry_throttle,
        ):
            return {"account": account.name, "attempted": False, "reason": "not_due"}
        result = execute_login(account, market=market, runner=runner)
        record_result(account, result, now=observed if now is not None else None, state_dir=state_dir)
        return {
            "account": account.name,
            "attempted": True,
            **result,
        }


def initialize_all(*, now: datetime | None = None, state_dir: Path = STATE_DIR) -> list[dict]:
    observed = _aware_utc(now)
    cfg = cfgmod.load()
    rows = []
    for account in sorted(cfgmod.load_accounts(cfg), key=lambda item: item.name):
        if not getattr(account, "enabled", True):
            continue
        initialize_account(account, now=observed, state_dir=state_dir)
        rows.append({"account": account.name, **public_account_state(
            account, now=observed, state_dir=state_dir
        )})
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="48小时账号重登维护状态")
    parser.add_argument("--initialize", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if args.initialize and not args.dry_run:
        rows = initialize_all()
    else:
        cfg = cfgmod.load()
        rows = [
            {"account": a.name, **public_account_state(a)}
            for a in cfgmod.load_accounts(cfg) if a.enabled
        ]
    print(json.dumps({"accounts": rows}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "INTERVAL_HOURS",
    "LOCK_PATH",
    "LOGIN_TIMEOUT_SECONDS",
    "RETRY_INTERVAL_HOURS",
    "STATE_DIR",
    "WINDOW_END_HOUR",
    "WINDOW_START_HOUR",
    "WINDOW_TIMEZONE",
    "execute_login",
    "in_maintenance_window",
    "initialize_account",
    "initialize_all",
    "maintain_account",
    "maintenance_due",
    "maintenance_ready",
    "planned_maintenance_at",
    "maintenance_lock",
    "public_account_state",
    "record_result",
]
