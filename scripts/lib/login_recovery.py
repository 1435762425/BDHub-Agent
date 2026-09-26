"""The single entry for account auth recovery (§9.20 / G15).

Every place that sees a lapsed login -- SDK sessions, inbox polls, selection and OECID stages -- classifies the
error here and asks for recovery here.  One lapse of one published identity generation has exactly one
maintenance intent, keyed by market/account/generation/errorFamily; the reporting source and role never enter
the key, so concurrent reporters join the same chain.  The intent always starts as ``refresh``; the maintenance
worker falls back to a visible re-login only when the refresh itself fails.  A failed or needs-verification
intent stays for a person: only a newly published generation earns another attempt.  Timeouts, quota and
business rejections are not auth failures and never reach this module.
"""
import os
import sqlite3
import subprocess
from pathlib import Path

from lib.account_identity import current_generation, intent_payload, request_maintenance
from lib.market_accounts import load_config
from lib.second_cycle import CycleError, digest

AUTH_REQUIRED = 16201010
AUTH_EXPIRED = "auth_expired"
# Project error codes that already mean "the platform says this login is gone".
AUTH_CODES = frozenset({"sdk_login_required", "market_identity_auth_required"})


def auth_family(code=None, platform_code=None):
    """``auth_expired`` for a lapsed login, otherwise None (timeouts, quota and rejections are not auth)."""
    if platform_code == AUTH_REQUIRED or code in AUTH_CODES:
        return AUTH_EXPIRED
    return None


def it_login_expired(report):
    """The IT auth probe records every read; a business rejection carrying 16201010 means the login is gone."""
    return any(isinstance(read, dict) and read.get("code") == AUTH_REQUIRED and read.get("errorCode") == "business_rejected"
               for read in report.get("authReads") or [])


def spawn_worker(root):
    log = Path(root) / "var/account-maintenance.log"
    with log.open("a", encoding="utf-8") as handle:
        subprocess.Popen([str(Path(root) / ".venv/bin/python"), str(Path(root) / "scripts/account-maintenance-worker.py")],
                         cwd=str(root), stdin=subprocess.DEVNULL, stdout=handle, stderr=subprocess.STDOUT,
                         start_new_session=True, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})


def _request_id(market, account, generation_id, family):
    return f"auth-{market}-{account}-" + digest([market, account, generation_id, family])[:24]


def _legacy_request_id(market, account, generation_id):
    # Before the single entry, inbox refreshes used this id; a lapse already handled under it stays handled.
    return f"inbox-login-{market}-{account}-" + digest([market, account, generation_id])[:24]


def _intent(store, request_id):
    return store.db.execute("SELECT * FROM account_maintenance_intent WHERE request_id=?", (request_id,)).fetchone()


def request_recovery(store, root, market, role, *, source, family=AUTH_EXPIRED, spawn=spawn_worker):
    """Join or open the one recovery chain for this market/account/generation/family; never raises.

    ``role`` names the account that actually failed (it selects the configured account, never a substitute).
    States: the intent's own state; ``waiting_active`` when another maintenance of the same account is already
    under way (it will publish or fail on its own); ``not_requested`` when facts are missing."""
    try:
        account = load_config(root)["markets"][market]["roles"][role]
        generation = current_generation(store, market, account)
        if not generation:
            return {"state": "not_requested", "reason": "identity_generation_missing", "account": account}
        generation_id = generation["generationId"]
        legacy = _intent(store, _legacy_request_id(market, account, generation_id)) if family == AUTH_EXPIRED else None
        if legacy is not None:
            intent = intent_payload(legacy) | {"duplicate": True}
        else:
            try:
                intent = request_maintenance(store, root, market=market, account=account, operation="refresh",
                                             request_id=_request_id(market, account, generation_id, family))
            except CycleError as error:
                if str(error) != "account_maintenance_active":
                    raise
                active = store.db.execute(
                    "SELECT intent_id,state FROM account_maintenance_intent WHERE account=? "
                    "AND state IN ('queued','draining','running') ORDER BY created_at LIMIT 1", (account,)).fetchone()
                return {"state": "waiting_active", "intentId": active["intent_id"] if active else None,
                        "account": account, "duplicate": True, "generationId": generation_id,
                        "family": family, "source": source, "errorCode": None}
    except (CycleError, KeyError, OSError, ValueError, sqlite3.Error) as error:
        return {"state": "not_requested", "reason": str(error)[:120] or type(error).__name__}
    if not intent["duplicate"]:
        # The scheduler also launches queued maintenance; the worker claims one intent at a time.
        try:
            spawn(root)
        except OSError:
            pass
    return {"state": intent["state"], "intentId": intent["intentId"], "account": account,
            "duplicate": intent["duplicate"], "generationId": generation_id, "family": family,
            "source": source, "errorCode": intent["errorCode"]}


def request_refresh(store, root, market, *, spawn=spawn_worker):
    """Inbox callers: the communications login of ``market`` has lapsed."""
    return request_recovery(store, root, market, "communications", source="inbox", spawn=spawn)
