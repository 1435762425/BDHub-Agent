"""Project-owned headed login, identity capture and read-only capability validation.

The legacy account file is read only for saved username/password and stable account metadata. New
profiles and identity bundles are created under ``var/account-identities``. Nothing in the legacy
repository is copied or modified.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from lib.account_identity import ROLE_RESPONSIBILITIES
from lib.legacy_runtime import configure_vendored_bdhub, project_identity_paths
from lib.second_cycle import digest


PID_CANARY = "1729779362302171335"
INFO = "/api/v1/affiliate/partner/info"
IM_ID = "/api/v1/affiliate/partner/im/id/get"
IM_TOKEN = "/api/v1/affiliate/partner/im/token/get"
CARD_LIST = "/api/v1/affiliate/partner/im/product_list/list"
SELECTED = "/api/v1/affiliate/partner/product/pick_up/list"
COOKIE_ROOTS = ("tiktok.com", "tiktokshop.com")
SESSION_COOKIES = frozenset({"sessionid", "sid_tt", "sid_guard", "sessionid_ss"})
COOKIE_NAME = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")


def _utc_now():
    return datetime.now(timezone.utc).isoformat()


def _safe_account(root, name):
    config = configure_vendored_bdhub(root=root, legacy_root=Path(root).parent / "01-BDSystem-V2")
    cfg = config.load()
    account = next((row for row in config.load_accounts(cfg) if row.name == name), None)
    if account is None:
        raise ValueError("account_config_missing")
    return config, cfg, account


def _candidate_bundle(context, page):
    from bdhub.enrich.identity_store import IdentityBundle, validate_identity

    cookie_parts, browser_cookies = [], []
    for raw in context.cookies():
        if not isinstance(raw, dict):
            continue
        domain = str(raw.get("domain") or "").strip().casefold().lstrip(".")
        if not any(domain == root or domain.endswith("." + root) for root in COOKIE_ROOTS):
            continue
        name, value = raw.get("name"), raw.get("value")
        if not isinstance(name, str) or not COOKIE_NAME.fullmatch(name) or not isinstance(value, str):
            continue
        if ";" in value or any(ord(character) < 0x20 or ord(character) == 0x7f for character in value):
            continue
        cookie_parts.append(f"{name}={value}")
        browser_cookies.append(dict(raw))
    if not any(str(row.get("name")) in SESSION_COOKIES and row.get("value") for row in browser_cookies):
        raise ValueError("browser_session_missing")
    user_agent = page.evaluate("() => navigator.userAgent")
    bundle = IdentityBundle({"cookie": "; ".join(cookie_parts), "user-agent": user_agent,
                             "content-type": "application/json"},
                            browser_cookies=tuple(browser_cookies))
    validate_identity(bundle)
    return bundle


def _visible(scope, selector):
    try:
        locator = scope.locator(selector)
        return [locator.nth(index) for index in range(min(locator.count(), 12))
                if locator.nth(index).is_visible()]
    except Exception:
        return []


def _scopes(page, context):
    pages = [page]
    try:
        pages.extend(reversed(list(context.pages)))
    except Exception:
        pass
    output, seen = [], set()
    for candidate in pages:
        if id(candidate) in seen:
            continue
        seen.add(id(candidate)); output.append(candidate)
        try:
            output.extend(list(candidate.frames))
        except Exception:
            pass
    return output


def _autofill(page, context, username, password, timeout_seconds=45):
    """Fill only a unique username/password pair in the same page or frame."""
    username_selectors = (
        'input[type="email"]', 'input[autocomplete="username"]',
        'input[autocomplete="email"]',
        'input[name*="email" i], input[id*="email" i], input[name*="user" i], input[id*="user" i]',
        ('input:not([type="password"]):not([type="hidden"]):not([type="checkbox"]):'
         'not([type="radio"]):not([type="search"]):not([type="tel"]):'
         'not([autocomplete="one-time-code"])'),
    )
    password_selectors = ('#password_input', 'input[type="password"]')
    entry_selectors = (
        'button:has-text("Log in")', 'a:has-text("Log in")',
        'button:has-text("Sign in")', 'a:has-text("Sign in")',
        'button:has-text("登录")', 'a:has-text("登录")',
    )
    started = time.monotonic()
    deadline, entry_clicked = started + timeout_seconds, False
    while time.monotonic() < deadline:
        for scope in _scopes(page, context):
            password_fields = []
            for selector in password_selectors:
                password_fields = _visible(scope, selector)
                if len(password_fields) == 1:
                    break
            if len(password_fields) != 1:
                continue
            for selector in username_selectors:
                username_fields = _visible(scope, selector)
                if len(username_fields) != 1:
                    continue
                username_fields[0].fill(username, timeout=5_000)
                password_fields[0].fill(password, timeout=5_000)
                try:
                    password_fields[0].press("Enter", timeout=3_000)
                except Exception:
                    buttons = _visible(scope, 'button[type="submit"]')
                    if buttons:
                        buttons[0].click(timeout=3_000)
                return True
        if not entry_clicked and time.monotonic() - started >= min(8, timeout_seconds / 2):
            for scope in _scopes(page, context):
                for selector in entry_selectors:
                    buttons = _visible(scope, selector)
                    if buttons:
                        try:
                            buttons[0].click(timeout=3_000); entry_clicked = True
                        except Exception:
                            pass
                        break
                if entry_clicked:
                    break
        page.wait_for_timeout(500)
    return False


def _validate_candidate(cfg, account, role, candidate_id):
    from bdhub.hub.markets import identity_for
    from bdhub.research.commerce_transport import CommerceTransport

    class Reader(CommerceTransport):
        READ_ENDPOINTS = frozenset({(INFO, "GET"), (IM_ID, "GET"), (IM_TOKEN, "GET"),
                                    (CARD_LIST, "GET"), (SELECTED, "POST")})
        WRITE_ENDPOINTS = frozenset()

    identity = identity_for("it", account=account, cfg=cfg).require_product_search()
    reader = Reader(identity, account, allow_write=False)
    def data_of(payload):
        nested = payload.get("data") if isinstance(payload, dict) else None
        return nested if isinstance(nested, dict) else payload
    evidence = f"project-identity:{candidate_id}"
    capabilities = {
        "browser_session": {"state": "verified", "evidenceRef": evidence},
        "partner_http": {"state": "verified", "evidenceRef": evidence},
    }
    for responsibility in ROLE_RESPONSIBILITIES[role]:
        capabilities.setdefault(responsibility, {"state": "not_tested", "evidenceRef": evidence})
    try:
        params = {"aid": identity.aid, "partner_id": str(identity.partner_id)}
        reader.session.trust_env = True
        info_result = reader._xhr(method="GET", path=INFO, params=params | {"partner_type": 1},
                                  payload=None, write=False)
        info = data_of(reader.require_read(info_result))
        markets = (info.get("partner_biz_role_info") or {}).get("market_list", [])
        matches = [market for market in markets if str(market.get("market_region")) == "8" and
                   any(str(item.get("partner_id")) == str(identity.partner_id)
                       for item in market.get("type_list", []))]
        if len(matches) != 1:
            raise ValueError("market_institution_not_verified")
        market_id = str(matches[0].get("market_id") or "")
        if not market_id.isdigit():
            raise ValueError("market_id_missing")
        capabilities["institution_market"] = {"state": "verified", "evidenceRef": evidence}
        im_result = reader._xhr(method="GET", path=IM_ID,
                                params=params | {"user_id": market_id, "type": 0},
                                payload=None, write=False)
        im_id = str(data_of(reader.require_read(im_result)).get("im_id") or "")
        if not im_id.isdigit():
            raise ValueError("im_id_missing")
        token_result = reader._xhr(method="GET", path=IM_TOKEN, params=params | {"im_id": im_id},
                                   payload=None, write=False)
        token = data_of(reader.require_read(token_result))
        if not token.get("token"):
            raise ValueError("im_token_missing")
        capabilities["im_identity"] = {"state": "verified", "evidenceRef": evidence}
        reader.session.trust_env = False
        if role == "communications":
            card_result = reader._xhr(method="GET", path=CARD_LIST,
                                      params=reader._params() | {"cur_page": 1, "page_size": 20,
                                      "version": 1, "search_type": 2, "key_word": PID_CANARY},
                                      payload=None, write=False)
            card_data = data_of(reader.require_read(card_result))
            if type(card_data.get("total")) is not int or card_data["total"] < 0:
                raise ValueError("im_card_search_invalid")
            capabilities["im_card_search"] = {"state": "verified", "evidenceRef": evidence}
        else:
            reader.selected_page(1)
            capabilities["catalog_read"] = {"state": "verified", "evidenceRef": evidence}
        return {"institutionFingerprint": digest([str(identity.im_market_partner_id), market_id]),
                "capabilities": capabilities}
    finally:
        reader.session.close()


class ProjectAccountIdentityAdapter:
    def __init__(self, root):
        self.root = Path(root).resolve()

    def drain(self, account):
        # Candidates use isolated profiles and the global maintenance queue is single-concurrency.
        # Existing task drain/fence remains enforced by each business worker before it can write.
        return None

    def refresh(self, account):
        return self._capture(account, headed=False, timeout_seconds=60)

    def relogin(self, account):
        return self._capture(account, headed=True, timeout_seconds=600)

    def _candidate_paths(self, account):
        candidate_id = uuid4().hex
        generation = self.root / "var/account-identities" / account / "generations" / candidate_id
        if not generation.resolve().is_relative_to((self.root / "var/account-identities").resolve()):
            raise ValueError("project_identity_path_invalid")
        generation.mkdir(parents=True, mode=0o700)
        for directory in (self.root / "var/account-identities",
                          self.root / "var/account-identities" / account,
                          self.root / "var/account-identities" / account / "generations", generation):
            os.chmod(directory, 0o700)
        return candidate_id, generation, generation / "profile", generation / "headers.json"

    def _prepare_profile(self, account, target):
        current = project_identity_paths(self.root, account)
        if current is None:
            target.mkdir(mode=0o700)
        else:
            shutil.copytree(current["profileDir"], target, symlinks=False,
                            ignore=shutil.ignore_patterns("SingletonLock", "SingletonCookie", "SingletonSocket",
                                                           ".bdhub.lease", ".bdhub.lease.guard"))
        os.chmod(target, 0o700)
        guard = target / ".bdhub.lease.guard"
        descriptor = os.open(guard, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                              getattr(os, "O_NOFOLLOW", 0), 0o600)
        os.close(descriptor)

    def _capture(self, account_name, *, headed, timeout_seconds):
        config, cfg, base_account = _safe_account(self.root, account_name)
        del config
        if headed and (not base_account.username or not base_account.password):
            return {"ok": False, "errorCode": "saved_credentials_missing"}
        role = "communications" if account_name == "acc6" else "supply"
        candidate_id, generation, profile, headers = self._candidate_paths(account_name)
        result = None
        context = None
        playwright = None
        try:
            self._prepare_profile(account_name, profile)
            candidate_account = replace(base_account, profile_dir=profile, headers_json=headers,
                                        market="it")
            from bdhub.enrich.identity_store import (IdentityMeta, write_verified_browser_cookies,
                                                      write_verified_identity)
            from bdhub.hub.markets import identity_for
            from playwright.sync_api import sync_playwright

            identity = identity_for("it", account=candidate_account, cfg=cfg)
            playwright = sync_playwright().start()
            context = playwright.chromium.launch_persistent_context(
                str(profile), headless=not headed, no_viewport=headed,
                args=["--start-maximized"] if headed else [],
            )
            page = context.pages[0] if context.pages else context.new_page()
            try:
                page.goto(identity.home if headed else identity.warmup,
                          wait_until="domcontentloaded", timeout=60_000)
            except Exception:
                if not headed:
                    return {"ok": False, "errorCode": "account_refresh_failed",
                            "candidateDir": str(generation)}
            deadline, last_fingerprint, last_attempt = time.monotonic() + timeout_seconds, None, 0.0
            autofill_submitted = False
            last_navigation = 0.0
            while time.monotonic() < deadline:
                active_page = context.pages[-1] if context.pages else page
                if headed and not autofill_submitted:
                    try:
                        current_url = str(active_page.url or "")
                        if (current_url.startswith("chrome://") or current_url in {"", "about:blank"}) \
                                and time.monotonic() - last_navigation >= 8:
                            active_page.goto(identity.home, wait_until="domcontentloaded", timeout=60_000)
                            last_navigation = time.monotonic()
                        autofill_submitted = _autofill(
                            active_page, context, base_account.username, base_account.password,
                            timeout_seconds=4,
                        )
                    except Exception:
                        pass
                try:
                    bundle = _candidate_bundle(context, active_page)
                except Exception:
                    active_page.wait_for_timeout(1000)
                    continue
                fingerprint = hashlib.sha256(bundle.cookie.encode("utf-8")).hexdigest()
                if fingerprint == last_fingerprint and time.monotonic() - last_attempt < 15:
                    active_page.wait_for_timeout(1000)
                    continue
                last_fingerprint, last_attempt = fingerprint, time.monotonic()
                write_verified_identity(headers, bundle, IdentityMeta(
                    account=account_name, market="it", verified_at=_utc_now(),
                    verification_method="project_browser_login_readonly_validation"))
                write_verified_browser_cookies(profile, account_name, "it", list(bundle.browser_cookies))
                try:
                    validation = _validate_candidate(cfg, candidate_account, role, candidate_id)
                except Exception:
                    active_page.wait_for_timeout(1500)
                    continue
                result = {"ok": True, "candidateId": candidate_id,
                          "candidateDir": str(generation),
                          "identity": {"browserRef": f"project-browser:{candidate_id}",
                                       "httpRef": f"project-http:{candidate_id}",
                                       "imRef": f"project-im:{candidate_id}",
                                       "institutionFingerprint": validation["institutionFingerprint"]},
                          "capabilities": validation["capabilities"]}
                break
            if result is None:
                return {"ok": False,
                        "errorCode": "account_login_timeout" if headed else "account_refresh_failed",
                        "candidateDir": str(generation)}
            return result
        except Exception:
            return {"ok": False, "errorCode": "account_browser_login_failed",
                    "candidateDir": str(generation)}
        finally:
            if context is not None:
                try:
                    context.close()
                except Exception:
                    pass
            if playwright is not None:
                try:
                    playwright.stop()
                except Exception:
                    pass
            if result is None:
                shutil.rmtree(generation, ignore_errors=True)

    def validate(self, account, result):
        if not result.get("ok") or not isinstance(result.get("capabilities"), dict):
            raise ValueError("account_candidate_not_validated")
        return result["capabilities"]

    def reconnect_inbox(self, account, previous, generated):
        # Publishing a generation does not start a disabled inbox worker. A running worker resolves
        # the latest project identity on its next controlled restart.
        return False

    def discard(self, result):
        candidate_id = result.get("candidateId") if isinstance(result, dict) else None
        if not isinstance(candidate_id, str):
            return
        for account in ("acc6", "acc9"):
            current = project_identity_paths(self.root, account)
            if current and current["candidateId"] == candidate_id:
                return
        path = self.root / "var/account-identities"
        for account in ("acc6", "acc9"):
            candidate = path / account / "generations" / candidate_id
            if candidate.resolve().is_relative_to(path.resolve()):
                shutil.rmtree(candidate, ignore_errors=True)
