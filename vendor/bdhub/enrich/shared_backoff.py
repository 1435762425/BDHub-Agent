"""同市场/Partner共享故障的持久退避；只处理明确的100000/system-error=3。"""
from __future__ import annotations

import hashlib
import json
import math
import os
import time
from pathlib import Path

from bdhub import config
from bdhub.scheduled_relogin import maintenance_lock

STATE_ROOT = config.ROOT / "data" / "runtime" / "shared-profile-backoff"
WINDOW_SECONDS = 120
DELAYS_SECONDS = (60, 120, 300)


def scope_key(identity: dict, market: str) -> str | None:
    partner = str(identity.get("partner_id") or "").strip()
    host = str(identity.get("api_host") or "").strip()
    if not partner or not host:
        return None
    return hashlib.sha256(f"{market}\0{host}\0{partner}".encode()).hexdigest()[:24]


def _read(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("invalid_state")
        failures = value.get("failures", {})
        if not isinstance(failures, dict) or any(type(at) not in (int, float) or not math.isfinite(at) for at in failures.values()):
            raise ValueError("invalid_state")
        for key in ("retry_at", "opened_at", "level"):
            field = value.get(key)
            if field is not None and (type(field) not in (int, float) or not math.isfinite(field) or field < 0):
                raise ValueError("invalid_state")
        return value
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        return {"invalid": True}


def snapshot(identity: dict, market: str, *, root: Path | None = None, now: float | None = None) -> dict:
    scope = scope_key(identity, market)
    observed = time.time() if now is None else now
    state = _read(Path(root or STATE_ROOT) / f"{scope}.json") if scope else {}
    try:
        retry_at = float(state.get("retry_at") or 0)
    except (TypeError, ValueError):
        state = {"invalid": True}
        retry_at = 0
    return {
        "open": bool(state.get("invalid") or retry_at > observed),
        "code": "shared_backoff_state_invalid" if state.get("invalid") else "shared_platform_cooldown",
        "retry_after_seconds": max(0, int(retry_at - observed + 0.999)),
        "retry_at": retry_at or None,
        "affected_account_count": len(state.get("failures") or {}),
    }


def observe(identity: dict, market: str, account: str, *, outcome: str, error_code: str,
            remote_code: str, root: Path | None = None, now: float | None = None) -> None:
    scope = scope_key(identity, market)
    shared_error = remote_code == "100000" and "system_error_3" in error_code
    if scope is None or (outcome != "ok" and not shared_error):
        return
    observed = time.time() if now is None else now
    directory = Path(root or STATE_ROOT)
    with maintenance_lock(directory / f"{scope}.lock") as acquired:
        if not acquired:
            return
        path = directory / f"{scope}.json"
        state = _read(path)
        if state.get("invalid"):
            return
        # 已在途请求允许收口，但不能不断延长已经开启的冷却。
        if float(state.get("retry_at") or 0) > observed:
            return
        failures = {
            name: at for name, at in (state.get("failures") or {}).items()
            if isinstance(at, (int, float)) and 0 <= observed - at <= WINDOW_SECONDS
        }
        if outcome == "ok":
            failures.pop(account, None)
        else:
            failures[account] = observed
        state["failures"] = failures
        if len(failures) >= 2:
            level = min(int(state.get("level") or 0), len(DELAYS_SECONDS) - 1)
            if observed - float(state.get("opened_at") or 0) > 1800:
                level = 0
            state.update(retry_at=observed + DELAYS_SECONDS[level], opened_at=observed, level=level + 1)
        elif not failures:
            state.update(level=0, retry_at=None)
        state.update(updated_at=observed, schema_version=1)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
