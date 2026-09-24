"""One project-owned refresh when an inbox finds its communications login gone (platform code 16201010)."""
import os
import sqlite3
import subprocess
from pathlib import Path

from lib.account_identity import current_generation, request_maintenance
from lib.market_accounts import load_config
from lib.second_cycle import CycleError, digest

AUTH_REQUIRED = 16201010


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


def request_refresh(store, root, market, *, spawn=spawn_worker):
    """Queue one refresh of the market's communications account for its current identity generation.

    The request id names that generation, so every later failure of the same generation finds the same intent
    instead of logging in again; a refresh that fails or needs verification stays for a person.  Only a newly
    published generation can earn another attempt.  It never raises: the inbox that calls it keeps running."""
    try:
        account = load_config(root)["markets"][market]["roles"]["communications"]
        generation = current_generation(store, market, account)
        if not generation:
            return {"state": "not_requested", "reason": "identity_generation_missing", "account": account}
        request_id = f"inbox-login-{market}-{account}-" + digest([market, account, generation["generationId"]])[:24]
        intent = request_maintenance(store, root, market=market, account=account, operation="refresh",
                                     request_id=request_id)
    except (CycleError, KeyError, OSError, ValueError, sqlite3.Error) as error:
        return {"state": "not_requested", "reason": str(error)[:120] or type(error).__name__}
    if not intent["duplicate"]:
        # The scheduler also launches queued maintenance; the worker claims one intent at a time.
        try:
            spawn(root)
        except OSError:
            pass
    return {"state": intent["state"], "intentId": intent["intentId"], "account": account,
            "duplicate": intent["duplicate"], "generationId": generation["generationId"],
            "errorCode": intent["errorCode"]}
