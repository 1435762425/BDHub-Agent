"""账号浏览器身份的错峰检查、无感刷新与本机告警。"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable
from uuid import uuid4

from bdhub import config as cfgmod
from bdhub.account_policy import resolve_account_policy
from bdhub import scheduled_relogin as scheduled_relogin_svc


CHECK_INTERVAL_HOURS = 6
REFRESH_INTERVAL_HOURS = 24
RELOGIN_INTERVAL_HOURS = 48
REMINDER_INTERVAL_HOURS = 24
HEADLESS_LOGIN_TIMEOUT_SECONDS = 120
STATE_PATH = cfgmod.ROOT / "data" / "runtime" / "account-identity-lifecycle.json"


def _parse_time(value: object) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _read_state(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError):
        return {"accounts": {}}
    return payload if isinstance(payload, dict) and isinstance(payload.get("accounts"), dict) else {"accounts": {}}


def _write_state(path: Path, payload: dict) -> None:
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


def public_state(
    path: Path = STATE_PATH,
    *,
    scheduled_state_dir: Path | None = None,
) -> dict:
    state = _read_state(path)
    relogin_state_dir = (
        scheduled_relogin_svc.STATE_DIR
        if scheduled_state_dir is None and Path(path) == STATE_PATH
        else Path(scheduled_state_dir or (Path(path).parent / "scheduled-relogin"))
    )
    records = state.get("accounts", {})
    accounts = []
    try:
        cfg = cfgmod.load()
        configured = sorted(
            cfgmod.load_accounts(cfg), key=lambda item: item.name
        )
    except (OSError, TypeError, ValueError):
        configured = []
    if configured:
        rows = ((account.name, account) for account in configured)
    else:
        rows = ((str(name), None) for name in sorted(records))
    for name, account in rows:
        record = records.get(name, {})
        if not isinstance(record, dict):
            record = {}
        policy = resolve_account_policy(account) if account is not None else None
        scheduled_relogin = (
            scheduled_relogin_svc.public_account_state(
                account,
                state_dir=relogin_state_dir,
            )
            if account is not None
            else {}
        )
        if account is None:
            management_mode = "scheduled"
        elif not getattr(account, "enabled", True):
            management_mode = "disabled"
        elif policy.listener_pool:
            management_mode = "listener_resident"
        elif policy.share_link_pool:
            management_mode = "sharelink_resident"
        elif not policy.identity_lifecycle_enabled:
            management_mode = "disabled"
        else:
            management_mode = "scheduled"
        last_check_at = record.get("last_check_at")
        last_refresh_at = record.get("last_refresh_at")
        last_relogin_attempt_at = record.get("last_relogin_attempt_at")
        decision = record.get("decision")
        detail_code = record.get("detail_code")
        if scheduled_relogin.get("scheduled_relogin_state") == "attention":
            maintenance_state = "attention"
        elif management_mode in {"listener_resident", "sharelink_resident"}:
            maintenance_state = "resident"
        elif management_mode == "disabled":
            maintenance_state = "disabled"
        elif (
            decision in {"ready", "busy", "cooldown"}
            or detail_code in {"profile_busy", "profile_lease_busy"}
        ):
            maintenance_state = "healthy"
        elif decision:
            maintenance_state = "attention"
        else:
            maintenance_state = "pending"
        accounts.append({
            "account": str(name),
            "market": record.get("market") or (
                getattr(account, "market", None) if account is not None else None
            ),
            "management_mode": management_mode,
            "identity_lifecycle_enabled": (
                policy.identity_lifecycle_enabled if policy is not None else True
            ),
            "auto_relogin": (
                policy.auto_relogin if policy is not None else False
            ),
            "auto_relogin_available": bool(
                account is not None
                and getattr(account, "username", "")
                and getattr(account, "password", "")
            ),
            "last_action": record.get("last_action"),
            "maintenance_state": maintenance_state,
            "last_check_at": last_check_at,
            "last_check_attempt_at": record.get('last_check_attempt_at'),
            "last_refresh_at": last_refresh_at,
            "last_relogin_attempt_at": last_relogin_attempt_at,
            "last_relogin_at": record.get("last_relogin_at"),
            "next_check_at": _next_due_at(
                last_check_at, CHECK_INTERVAL_HOURS,
                enabled=management_mode == "scheduled",
            ),
            "next_refresh_at": _next_due_at(
                last_refresh_at, REFRESH_INTERVAL_HOURS,
                enabled=management_mode == "scheduled",
            ),
            "next_relogin_at": _next_due_at(
                last_relogin_attempt_at, RELOGIN_INTERVAL_HOURS,
                enabled=(
                    management_mode == "scheduled"
                    and bool(policy and policy.auto_relogin)
                ),
            ),
            "decision": decision,
            "detail_code": detail_code,
            "return_code": record.get("return_code"),
            **scheduled_relogin,
        })
    return {"updated_at": state.get("updated_at"), "accounts": accounts}


def _next_due_at(value: object, hours: int, *, enabled: bool) -> str | None:
    """Return the earliest next maintenance time without inventing expiry."""
    if not enabled:
        return None
    parsed = _parse_time(value)
    if parsed is None:
        return datetime.now(timezone.utc).isoformat()
    return (parsed + timedelta(hours=hours)).isoformat()


def _slot(account: str, interval: int) -> int:
    digest = hashlib.sha256(account.encode("utf-8")).digest()
    return int.from_bytes(digest[:2], "big") % interval


def _due(last: object, now: datetime, hours: int) -> bool:
    parsed = _parse_time(last)
    return parsed is None or now - parsed >= timedelta(hours=hours)


def _planned_action(account_name: str, record: dict, observed: datetime) -> str | None:
    """纯计划函数，执行和 dry-run 共用；错过时段最迟一小时后补跑。"""
    for action, interval, success_key, attempt_key in (
        ("refresh", REFRESH_INTERVAL_HOURS, "last_refresh_at", "last_refresh_attempt_at"),
        ("check", CHECK_INTERVAL_HOURS, "last_check_at", "last_check_attempt_at"),
    ):
        last = record.get(success_key)
        attempt = record.get(attempt_key)
        if not _due(last, observed, interval) or not _due(attempt, observed, 1):
            continue
        if (
            observed.hour % interval == _slot(account_name, interval)
            or _parse_time(attempt) is not None
            or (_parse_time(last) is not None and _due(last, observed, interval + 1))
            or (_parse_time(last) is None and _due(record.get("first_seen_at", observed.isoformat()), observed, 1))
        ):
            return action
    return None


def _run_command(argv: list[str]) -> subprocess.CompletedProcess[str]:
    from bdhub.auth_command import run_auth_command
    return run_auth_command(
        argv, cwd=cfgmod.ROOT,
        timeout=HEADLESS_LOGIN_TIMEOUT_SECONDS + 60 if "--signed-login" in argv else 900,
    )


def _report(process: subprocess.CompletedProcess[str]) -> dict:
    lines = [line.strip() for line in str(process.stdout or "").splitlines() if line.strip()]
    for line in reversed(lines):
        try:
            payload = json.loads(line)
        except ValueError:
            continue
        if isinstance(payload, list) and payload and isinstance(payload[0], dict):
            return dict(payload[0])
    return {}


def _execute(
    runner: Callable[[list[str]], subprocess.CompletedProcess[str]],
    argv: list[str],
) -> tuple[subprocess.CompletedProcess[str] | None, str, str | None, bool]:
    try:
        process = runner(argv)
        report = _report(process)
        decision = str(report.get("decision") or "unknown")
        detail_code = str(report.get("detail_code") or "") or None
        expected_account = argv[argv.index("--account") + 1] if "--account" in argv else None
        expected_market = argv[argv.index("--market") + 1] if "--market" in argv else None
        if report.get("account") not in (None, expected_account) or report.get("market") not in (None, expected_market):
            return process, "failed", "auth_report_identity_mismatch", False
        # 登录已验证并保存Partner身份时，独立Signed Find冷却不否定登录成功。
        success = decision == "ready" and (
            process.returncode == 0
            or ("--signed-login" in argv and report.get("capability") == "partner_http")
        )
        return process, decision, detail_code, success
    except (OSError, subprocess.SubprocessError) as error:
        return None, "failed", type(error).__name__, False


def _notify(title: str, message: str) -> None:
    clean_title = str(title).replace('"', "'")[:80]
    clean_message = str(message).replace('"', "'")[:180]
    subprocess.run(
        ["osascript", "-e", f'display notification "{clean_message}" with title "{clean_title}"'],
        capture_output=True,
        check=False,
        timeout=10,
    )


def _run_once(
    *,
    now: datetime | None = None,
    state_path: Path = STATE_PATH,
    runner: Callable[[list[str]], subprocess.CompletedProcess[str]] = _run_command,
    notifier: Callable[[str, str], None] = _notify,
    scheduled_state_dir: Path | None = None,
    scheduled_lock_path: Path | None = None,
) -> dict:
    observed = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    cfg = cfgmod.load()
    state = _read_state(state_path)
    relogin_state_dir = (
        scheduled_relogin_svc.STATE_DIR
        if scheduled_state_dir is None and Path(state_path) == STATE_PATH
        else Path(
            scheduled_state_dir
            or (Path(state_path).parent / "scheduled-relogin")
        )
    )
    relogin_lock_path = Path(
        scheduled_lock_path
        or (
            scheduled_relogin_svc.LOCK_PATH
            if Path(state_path) == STATE_PATH
            else Path(state_path).parent / "scheduled-relogin.lock"
        )
    )
    accounts_state = state.setdefault("accounts", {})
    results = []
    for account in sorted(cfgmod.load_accounts(cfg), key=lambda item: item.name):
        if now is None:
            observed = datetime.now(timezone.utc)
        if not getattr(account, "enabled", True) or not getattr(account, "profile_dir", None):
            continue
        policy = resolve_account_policy(account)
        scheduled_relogin_svc.initialize_account(
            account,
            now=observed,
            state_dir=relogin_state_dir,
        )
        if not policy.share_link_pool:
            scheduled_result = scheduled_relogin_svc.maintain_account(
                account,
                market=str(
                    getattr(account, "market", None)
                    or getattr(cfg, "market", "mx")
                ).lower(),
                now=observed if now is not None else None,
                runner=runner,
                state_dir=relogin_state_dir,
                lock_path=relogin_lock_path,
            )
            if scheduled_result.get("attempted") is True:
                success = scheduled_result.get("success") is True
                decision = str(
                    scheduled_result.get("decision") or "unknown"
                )
                detail_code = scheduled_result.get("detail_code")
                results.append({
                    "account": account.name,
                    "market": str(
                        getattr(account, "market", None)
                        or getattr(cfg, "market", "mx")
                    ).lower(),
                    "action": "scheduled_relogin",
                    "success": success,
                    "decision": decision,
                    "detail_code": detail_code,
                })
                record = dict(accounts_state.get(account.name) or {})
                completed_at = observed if now is not None else datetime.now(timezone.utc)
                record.update(
                    last_action="scheduled_relogin", decision=decision, detail_code=detail_code,
                    return_code=scheduled_result.get("return_code"),
                    last_relogin_attempt_at=completed_at.isoformat(),
                    last_check_attempt_at=completed_at.isoformat(),
                )
                if success:
                    for key in ("last_refresh_at", "last_check_at", "last_relogin_at"):
                        record[key] = completed_at.isoformat()
                    record.pop("last_notified_at", None)
                expected_busy = decision in ("busy", "locked") or detail_code in ("profile_busy", "profile_lease_busy")
                if not success and not expected_busy and _due(record.get("last_notified_at"), completed_at, REMINDER_INTERVAL_HOURS):
                    notifier(
                        "BDHub账号48小时登录维护需要处理",
                        f"{account.name}：{detail_code or decision}",
                    )
                    record["last_notified_at"] = completed_at.isoformat()
                accounts_state[account.name] = record
                state["updated_at"] = completed_at.isoformat()
                _write_state(state_path, state)
                # 48小时登录维护优先；同一次唤醒不再叠加普通深检查或刷新。
                continue
        if not policy.identity_lifecycle_enabled:
            continue
        if policy.listener_pool or policy.share_link_pool:
            # 监听和公开ShareLink常驻进程已经负责自身检查与定时浏览器回收；
            # 这里再次抢ProfileLease只会制造永久busy假告警。
            accounts_state.pop(account.name, None)
            continue
        record = dict(accounts_state.get(account.name) or {})
        record.setdefault("first_seen_at", observed.isoformat())
        accounts_state[account.name] = record
        action = _planned_action(account.name, record, observed)
        if action is None:
            continue
        record["last_check_attempt_at"] = observed.isoformat()
        if action == "refresh":
            record["last_refresh_attempt_at"] = observed.isoformat()
        accounts_state[account.name] = record
        state["updated_at"] = observed.isoformat()
        _write_state(state_path, state)
        market = str(getattr(account, "market", None) or getattr(cfg, "market", "mx")).lower()
        argv = [
            sys.executable,
            "-m",
            "bdhub.enrich.auth",
            "--account",
            account.name,
            "--market",
            market,
            *( ["--refresh-from-profile", "--json"] if action == "refresh" else ["--check", "--deep", "--json"] ),
        ]
        process, decision, detail_code, success = _execute(runner, argv)
        performed_action = action
        can_auto_relogin = bool(
            policy.auto_relogin
            and getattr(account, "username", "")
            and getattr(account, "password", "")
        )
        if (
            not success
            and decision in {"login_required", "not_logged_in"}
            and can_auto_relogin
            and _due(
                record.get("last_relogin_attempt_at"),
                observed,
                RELOGIN_INTERVAL_HOURS,
            )
        ):
            performed_action = "relogin"
            login_argv = [
                sys.executable,
                "-m",
                "bdhub.enrich.auth",
                "--account",
                account.name,
                "--market",
                market,
                "--signed-login",
                "--headless-login",
                "--json",
                "--timeout",
                str(HEADLESS_LOGIN_TIMEOUT_SECONDS),
            ]
            with scheduled_relogin_svc.maintenance_lock(relogin_lock_path) as acquired:
                if acquired:
                    record["last_relogin_attempt_at"] = observed.isoformat()
                    accounts_state[account.name] = record
                    state["updated_at"] = observed.isoformat()
                    _write_state(state_path, state)
                    process, decision, detail_code, success = _execute(runner, login_argv)
                    scheduled_relogin_svc.record_result(
                        account, {
                            "success": success, "decision": decision, "detail_code": detail_code,
                            "return_code": process.returncode if process else None,
                        }, now=observed if now is not None else None, state_dir=relogin_state_dir,
                    )
                else:
                    decision, detail_code, success = "busy", "maintenance_locked", False
                    performed_action = action
        if now is None:
            observed = datetime.now(timezone.utc)
        previous_decision = str(record.get("decision") or "")
        record.update({
            "market": market,
            "last_action": performed_action,
            "decision": decision,
            "detail_code": detail_code,
            "return_code": process.returncode if process is not None else -1,
        })
        if decision not in ("busy", "cooldown") and detail_code not in ("profile_busy", "profile_lease_busy", "maintenance_locked"):
            record["last_check_at"] = observed.isoformat()
        if performed_action in {"refresh", "relogin"} and success:
            record["last_refresh_at"] = observed.isoformat()
        if performed_action == "relogin" and success:
            record["last_relogin_at"] = observed.isoformat()
        accounts_state[account.name] = record
        expected_busy = decision in {"busy", "cooldown"} or detail_code in {
            "profile_busy", "profile_lease_busy",
        }
        reminder_due = _due(
            record.get("last_notified_at"),
            observed,
            REMINDER_INTERVAL_HOURS,
        )
        if (
            not success
            and not expected_busy
            and (decision != previous_decision or reminder_due)
        ):
            notifier(
                "BDHub账号需要处理",
                f"{account.name} 身份{performed_action}失败："
                f"{detail_code or decision}",
            )
            record["last_notified_at"] = observed.isoformat()
        elif success:
            record.pop("last_notified_at", None)
        results.append({
            "account": account.name,
            "market": market,
            "action": performed_action,
            "success": success,
            "decision": decision,
            "detail_code": detail_code,
        })
        # 按账号提交，后续账号被中断时不丢失前面已完成的身份维护记录。
        state["updated_at"] = observed.isoformat()
        _write_state(state_path, state)
    state["updated_at"] = observed.isoformat()
    _write_state(state_path, state)
    return {"status": "completed", "checked": len(results), "results": results}


def run_once(
    *,
    now: datetime | None = None,
    state_path: Path = STATE_PATH,
    runner: Callable[[list[str]], subprocess.CompletedProcess[str]] = _run_command,
    notifier: Callable[[str, str], None] = _notify,
    scheduled_state_dir: Path | None = None,
    scheduled_lock_path: Path | None = None,
    dry_run: bool = False,
) -> dict:
    """一次仅一个巡检进程；dry_run只读计划，不初始化状态或启动浏览器。"""
    if now is not None and now.tzinfo is None:
        raise ValueError("lifecycle_now_requires_timezone")
    if dry_run:
        observed = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        cfg = cfgmod.load()
        rows = []
        records = _read_state(Path(state_path))["accounts"]
        directory = scheduled_state_dir or (
            scheduled_relogin_svc.STATE_DIR if Path(state_path) == STATE_PATH
            else Path(state_path).parent / "scheduled-relogin"
        )
        for account in cfgmod.load_accounts(cfg):
            if not getattr(account, "enabled", True):
                continue
            policy = resolve_account_policy(account)
            ready = scheduled_relogin_svc.maintenance_ready(account, now=observed, state_dir=directory)
            if not getattr(account, "profile_dir", None):
                action = "no_profile"
            elif policy.share_link_pool:
                action = "resident_managed"
            elif ready:
                action = "scheduled_relogin"
            elif policy.listener_pool:
                action = "resident_managed"
            elif not policy.identity_lifecycle_enabled:
                action = "disabled"
            else:
                action = _planned_action(account.name, records.get(account.name) or {}, observed) or "wait"
            rows.append({
                "account": account.name,
                **scheduled_relogin_svc.public_account_state(account, now=observed, state_dir=directory),
                "scheduled_maintenance_ready": ready, "planned_action": action,
            })
        return {"status": "dry_run", "checked": 0, "results": rows}
    with scheduled_relogin_svc.maintenance_lock(Path(state_path).with_suffix(".run.lock")) as acquired:
        if not acquired:
            return {"status": "busy", "checked": 0, "results": []}
        return _run_once(
            now=now, state_path=state_path, runner=runner, notifier=notifier,
            scheduled_state_dir=scheduled_state_dir, scheduled_lock_path=scheduled_lock_path,
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="BDHub账号身份定时检查与无感刷新")
    parser.add_argument("--state-path", type=Path, default=STATE_PATH)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    result = run_once(state_path=args.state_path, dry_run=args.dry_run)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "CHECK_INTERVAL_HOURS",
    "HEADLESS_LOGIN_TIMEOUT_SECONDS",
    "RELOGIN_INTERVAL_HOURS",
    "REMINDER_INTERVAL_HOURS",
    "REFRESH_INTERVAL_HOURS",
    "public_state",
    "run_once",
]
