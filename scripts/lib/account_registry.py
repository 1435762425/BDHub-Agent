"""Project-owned TikTok account registry: ``private-data/secrets/accounts.json``.

This file is the only place this project keeps account login records; the original project's
accounts are never read.  Secrets stay inside this module: ``listing`` reports only whether a
username/password is set.  Which market and role an account serves stays in
``config/market-accounts.json``; registering an account does not assign it.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import tempfile

from lib.legacy_runtime import private_root

ACCOUNT = re.compile(r"acc[1-9][0-9]{0,5}")
MARKET = re.compile(r"[a-z]{2}")
PARTNER_ID = re.compile(r"[0-9]{1,30}")
SECRET = re.compile(r"[^\x00-\x1f\x7f]{1,256}")


class AccountRegistryError(ValueError):
    pass


def registry_path(root):
    return private_root(root) / "secrets/accounts.json"


def _read(path):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"accounts": []}
    except (OSError, ValueError):
        raise AccountRegistryError("account_registry_unreadable") from None
    if not isinstance(value, dict) or not isinstance(value.get("accounts"), list) or \
            not all(isinstance(row, dict) and ACCOUNT.fullmatch(str(row.get("name", ""))) for row in value["accounts"]):
        raise AccountRegistryError("account_registry_invalid")
    return value


@contextmanager
def _locked(root):
    path = registry_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    with open(path.parent / ".accounts.lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield path


def _write(path, value):
    descriptor, temporary = tempfile.mkstemp(prefix=".accounts.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def _assignments(root):
    try:
        markets = json.loads((Path(root) / "config/market-accounts.json").read_text(encoding="utf-8"))["markets"]
    except (OSError, ValueError, KeyError, TypeError):
        return {}
    return {name: {"market": market, "role": role}
            for market, pair in markets.items() for role, name in (pair.get("roles") or {}).items()}


def listing(root):
    assigned = _assignments(root)
    rows = [{"account": row["name"], "market": row.get("market"), "partnerId": row.get("partner_id"),
             "usernameSet": bool(row.get("username")), "passwordSet": bool(row.get("password")),
             "assignment": assigned.get(row["name"])}
            for row in _read(registry_path(root))["accounts"]]
    return {"accounts": sorted(rows, key=lambda row: int(row["account"][3:])), "secretsReturned": 0}


def _secret(value, code):
    if not isinstance(value, str) or not SECRET.fullmatch(value) or value != value.strip():
        raise AccountRegistryError(code)
    return value


def add(root, *, account, market, partner_id, username, password):
    if not isinstance(account, str) or not ACCOUNT.fullmatch(account):
        raise AccountRegistryError("account_name_invalid")
    if not isinstance(market, str) or not MARKET.fullmatch(market):
        raise AccountRegistryError("account_market_invalid")
    if not isinstance(partner_id, str) or not PARTNER_ID.fullmatch(partner_id):
        raise AccountRegistryError("account_partner_id_invalid")
    row = {"name": account, "market": market, "partner_id": partner_id,
           "username": _secret(username, "account_username_invalid"),
           "password": _secret(password, "account_password_invalid"), "enabled": True}
    with _locked(root) as path:
        value = _read(path)
        if any(existing["name"] == account for existing in value["accounts"]):
            raise AccountRegistryError("account_exists")
        value["accounts"].append(row)
        _write(path, value)
    return listing(root)


def update_credentials(root, *, account, username=None, password=None):
    if username is None and password is None:
        raise AccountRegistryError("account_credentials_missing")
    with _locked(root) as path:
        value = _read(path)
        row = next((existing for existing in value["accounts"] if existing["name"] == account), None)
        if row is None:
            raise AccountRegistryError("account_missing")
        if username is not None:
            row["username"] = _secret(username, "account_username_invalid")
        if password is not None:
            row["password"] = _secret(password, "account_password_invalid")
        _write(path, value)
    return listing(root)
