#!/usr/bin/env python3
"""Supervised read-only ACC6 Italy IM authentication probe; no conversation/send."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
from contextlib import redirect_stdout, redirect_stderr
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
LEGACY = Path("/Users/bjn00003/BDHub/01-BDSystem-V2")
VAR = ROOT / "var"
sys.path.insert(0, str(ROOT / "scripts"))
from lib.italy_im_auth import ItalyImAuthError, ImProbeDeadline, PARTNER_HOST, authenticate_it
from lib.italy_im_session import ItalyImReadError, ItalyImReadSession
from lib.legacy_runtime import configure_vendored_bdhub


def write_report(path, value):
    temporary = path.with_suffix(".tmp")
    fd = os.open(temporary, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as output:
        json.dump(value, output, ensure_ascii=False, indent=2)
        output.write("\n")
    temporary.replace(path)


def load_runtime():
    sys.dont_write_bytecode = True
    configure_vendored_bdhub(root=ROOT, legacy_root=LEGACY)
    from bdhub import config, scheduled_relogin
    from bdhub.account_policy import resolve_account_policy
    from bdhub.enrich.identity_store import load_identity
    from bdhub.hub import im_markets, markets
    from bdhub.imbase.account_binding import require_im_account
    cfg = config.load()
    account = next((item for item in config.load_accounts(cfg) if item.name == "acc6"), None)
    if account is None:
        raise ItalyImAuthError("account_invalid")
    try:
        require_im_account(account, requested_market="it", required_role="sender")
    except ValueError as error:
        raise ItalyImAuthError("account_disabled" if str(error) == "im_account_disabled" else "identity_invalid") from None
    if not resolve_account_policy(account).im_send_pool:
        raise ItalyImAuthError("account_not_in_send_pool")
    # require_im() is a production send gate (IT is canary). This probe only
    # checks the independently registered read-only IM/SDK capability.
    identity = markets.identity_for("it", account=account, cfg=cfg).require_monitor()
    im_meta = im_markets.get("it").require_monitor()
    if im_meta.api_host != PARTNER_HOST or im_meta.portal_host != PARTNER_HOST or im_meta.market_code != "8" or im_meta.aid != "360019":
        raise ItalyImAuthError("identity_invalid")
    spec = importlib.util.spec_from_file_location("italy_existing_readonly_guard", ROOT / "scripts/probe-italy-profile.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return account, identity, load_identity, module.readonly_guard, lambda: scheduled_relogin.maintenance_due(account, initialize=False, ignore_retry_throttle=True), markets.capability_status("it", "send")


def network_child(output, *, runtime_loader=load_runtime, authenticate=authenticate_it, im_read=False, verify_targets=None, read_session=ItalyImReadSession):
    report = {"schema": "bdhub.italy-im-auth-probe.v1", "market": "it", "account": "acc6", "status": "starting",
              "startedAt": datetime.now(timezone.utc).isoformat(), "authReads": [], "legacyDatabaseWrites": 0,
              "identityFileWrites": 0, "browserInitializations": 0, "conversationCreateRequests": 0, "sendRequests": 0,
              "wallDeadlineSeconds": 60, "environmentProxy": True}
    path = output / "report.json"
    save = lambda: write_report(path, report)
    identity_path, identity_before = None, None
    phase = "runtime"
    old_handler = signal.getsignal(signal.SIGALRM)
    def expired(*_):
        raise ImProbeDeadline()
    save()
    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, 60)
    try:
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            account, identity, load_identity, guard, maintenance_due, send_capability = runtime_loader()
            phase = "identity"
            identity_path = Path(account.headers_json)
            identity_before = hashlib.sha256(identity_path.read_bytes()).digest()
            report.update(sendCapability=send_capability, capabilityUsed="im_readonly", canonicalGuard="existing_readonly_mutex")
            save()
            phase = "guard"
            with guard(account):
                phase = "identity"
                if hashlib.sha256(identity_path.read_bytes()).digest() != identity_before:
                    raise ItalyImAuthError("identity_file_changed")
                if maintenance_due():
                    raise ItalyImAuthError("maintenance_due")
                bundle = load_identity(identity_path)
                phase = "auth"
                sensitive = authenticate(account, identity, bundle.headers, report, maintenance_due=maintenance_due,
                                         on_update=save, use_environment_proxy=True)
                if im_read:
                    with read_session(sensitive, report, maintenance_due=maintenance_due, on_update=save, use_environment_proxy=True) as session:
                        initial = session.initialize()
                        private = {"schema": "bdhub.italy-im-conversation-observations.v1", "market": "it", "account": "acc6",
                                   "observedAt": datetime.now(timezone.utc).isoformat(), **private_initial(initial), "verified": []}
                        private_path = output / "conversations.private.json"
                        write_report(private_path, private)
                        report["privateConversationFile"] = private_path.name
                        for target in verify_targets or []:
                            conversation = session.conversation(target["conversationId"], target["oecId"], conversation_type=target["conversationType"])
                            history = session.history_summary(conversation)
                            private["verified"].append({**target, "oecAndTicketVerified": True,
                                                        "history": {key: history[key] for key in ("messageCount", "hasMore", "identityVerified", "messageBodiesStored")}})
                            write_report(private_path, private)
                # The auth token and native context deliberately never cross the
                # process boundary or enter the report file.
                del sensitive
                report["status"] = "completed"
                report["identityFileUnchanged"] = hashlib.sha256(identity_path.read_bytes()).digest() == identity_before
                if not report["identityFileUnchanged"]:
                    raise ItalyImAuthError("identity_file_changed")
    except ImProbeDeadline:
        report.update(status="bounded_timeout", errorCode="wall_timeout")
    except ItalyImAuthError as error:
        report.update(status="blocked", errorCode=error.code)
    except ItalyImReadError as error:
        report.update(status="blocked", errorCode=error.code)
    except FileNotFoundError:
        report.update(status="blocked", errorCode="guard_missing" if phase == "guard" else "identity_invalid" if phase == "identity" else "runtime_unavailable")
    except BlockingIOError:
        report.update(status="blocked", errorCode="guard_busy")
    except RuntimeError as error:
        report.update(status="blocked", errorCode={"account_in_use": "guard_busy", "guard_changed": "guard_invalid", "guard_not_regular": "guard_invalid"}.get(str(error), "probe_failed"))
    except Exception:
        report.update(status="blocked", errorCode="probe_failed")
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old_handler)
        if identity_path is not None and identity_before is not None:
            try:
                report["identityFileUnchanged"] = hashlib.sha256(identity_path.read_bytes()).digest() == identity_before
            except Exception:
                report["identityFileUnchanged"] = False
        report["finishedAt"] = datetime.now(timezone.utc).isoformat()
        save()
    return 0 if report["status"] == "completed" else 2


def output_path(value):
    path = Path(value).resolve()
    if not path.is_relative_to(VAR.resolve()) or path == VAR.resolve():
        raise ValueError("output must be an unused directory inside new project var")
    return path


def private_initial(initial):
    """Persist only the read adapter's allowed identity/page fields."""
    rows = initial.get("conversations")
    if not isinstance(rows, list):
        raise ItalyImReadError("im_response_invalid")
    conversations = []
    for row in rows:
        if not isinstance(row, dict):
            raise ItalyImReadError("im_response_invalid")
        for key in ("conversationId", "oecId"):
            value = row.get(key)
            if not isinstance(value, str) or not value.isascii() or not value.isdigit() or not 1 <= len(value) <= 19:
                raise ItalyImReadError("im_response_invalid")
        if (type(row.get("conversationType")) is not int or not 0 <= row["conversationType"] <= 10
                or type(row.get("ticketPresent")) is not bool or row.get("identitySource") != "conversation_core.creator_oec_id"):
            raise ItalyImReadError("im_response_invalid")
        conversations.append({key: row[key] for key in ("conversationId", "oecId", "conversationType", "ticketPresent", "identitySource")})
    if (type(initial.get("hasMore")) is not bool or initial.get("pageOnly") is not True or initial.get("completeInbox") is not False
            or not isinstance(initial.get("nextCursor"), str) or not initial["nextCursor"].isascii() or not initial["nextCursor"].isdigit() or len(initial["nextCursor"]) > 19
            or any(type(initial.get(key)) is not int or initial[key] < 0 for key in ("invalidConversations", "otherMarketConversations", "messageBodiesDiscarded"))):
        raise ItalyImReadError("im_response_invalid")
    return {"conversations": conversations, **{key: initial[key] for key in ("hasMore", "nextCursor", "invalidConversations", "otherMarketConversations", "messageBodiesDiscarded", "pageOnly", "completeInbox")}}


def read_verify_file(path):
    path = output_path(path)
    if not path.is_file() or path.stat().st_size > 8192:
        raise ValueError("verification input must be a bounded local file")
    value = json.loads(path.read_text())
    if not isinstance(value, dict) or set(value) != {"market", "conversations"} or value["market"] != "it" or not isinstance(value["conversations"], list) or not 1 <= len(value["conversations"]) <= 3:
        raise ValueError("verification input must contain one to three Italy conversations")
    result, seen = [], set()
    for item in value["conversations"]:
        if not isinstance(item, dict) or set(item) - {"conversationId", "oecId", "conversationType"}:
            raise ValueError("invalid verification target")
        for key in ("conversationId", "oecId"):
            text = item.get(key)
            if not isinstance(text, str) or not text.isascii() or not text.isdigit() or not 1 <= len(text) <= 19 or not 0 < int(text) <= (1 << 63) - 1:
                raise ValueError("verification target requires exact numeric string identities")
        kind = item.get("conversationType", 2)
        if type(kind) is not int or not 0 <= kind <= 10 or item["conversationId"] in seen:
            raise ValueError("invalid or duplicate verification target")
        seen.add(item["conversationId"])
        result.append({"conversationId": item["conversationId"], "oecId": item["oecId"], "conversationType": kind})
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--account", choices=["acc6"], default="acc6")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--network-child", action="store_true")
    parser.add_argument("--im-read", action="store_true")
    parser.add_argument("--verify-file", type=Path)
    args = parser.parse_args(argv)
    output = output_path(args.output or VAR / ("italy-im-auth-" + datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:8]))
    if args.verify_file and not args.im_read:
        raise ValueError("conversation verification requires --im-read")
    targets = read_verify_file(args.verify_file) if args.verify_file else []
    if args.network_child:
        if not output.is_dir() or (output / "report.json").exists():
            raise ValueError("child output is missing or already used")
        return network_child(output, im_read=args.im_read, verify_targets=targets)
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    command = [sys.executable, str(Path(__file__).resolve()), "--network-child", "--account", "acc6", "--output", str(output)]
    if args.im_read:
        command.append("--im-read")
    if args.verify_file:
        command.extend(["--verify-file", str(args.verify_file.resolve())])
    process = subprocess.Popen(command, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}, start_new_session=True,
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    timed_out = False
    previous_term = signal.getsignal(signal.SIGTERM)
    def cancelled(*_):
        raise KeyboardInterrupt()
    signal.signal(signal.SIGTERM, cancelled)
    try:
        try:
            process.wait(timeout=65)
        except subprocess.TimeoutExpired:
            timed_out = True
    finally:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
        signal.signal(signal.SIGTERM, previous_term)
    path = output / "report.json"
    report = json.loads(path.read_text()) if path.exists() else {"status": "blocked", "errorCode": "child_no_report"}
    if timed_out:
        report.update(status="bounded_timeout", errorCode="wall_timeout")
        write_report(path, report)
    elif not path.exists():
        write_report(path, report)
    print(json.dumps({"status": report.get("status"), "errorCode": report.get("errorCode"), "apiHost": report.get("apiHost"),
                      "authRequests": len(report.get("authReads", [])), "sendRequests": 0, "conversationCreateRequests": 0,
                      "imReadRequests": len(report.get("imReads", [])), "initialConversations": report.get("initialConversationCount"),
                      "report": str(path)}, ensure_ascii=False))
    return 0 if report.get("status") == "completed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
