#!/usr/bin/env python3
"""Bounded read-only Italy Find/Profile probe using the existing MX transport.

No old database writes, cookie refresh, CAPTCHA solving, browser launch or sends.
Credentials remain in memory. Only allowlisted profile summaries enter new var/.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import signal
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import urllib.request
from contextlib import contextmanager, redirect_stdout, redirect_stderr
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
LEGACY = Path("/Users/bjn00003/BDHub/01-BDSystem-V2")
VAR = ROOT / "var"
STOP_CODES = {"10000", "10001", "98000001", "98000002", "16901008"}


class ProbeDeadline(TimeoutError):
    pass


def classify_response(status: int, headers: dict, payload) -> dict:
    code = str(payload.get("code")) if isinstance(payload, dict) else None
    verification = bool(headers.get("bdturing-verify")) or code == "10000"
    system_error = headers.get("x-tt-system-error") == "3"
    stop = verification or code in STOP_CODES or status in (401, 403, 429) or (code == "100000" and system_error)
    allowed = not stop and status == 200 and isinstance(payload, dict) and type(payload.get("code")) is int and payload["code"] == 0
    return {"httpStatus": status, "code": code, "verificationRequired": verification, "systemError3": system_error, "allowed": allowed}


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(path)


def get_readiness() -> dict:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open("http://127.0.0.1:8787/api/account-readiness?market=it&capability=collect&transport=pure_http", timeout=10) as response:
        return json.load(response)


@contextmanager
def readonly_guard(account):
    from bdhub.enrich.profile_lease import ProfileLease
    lease = ProfileLease(account.profile_dir, account=account.name, market="it", operation="agent-readonly-profile-probe")
    fd = os.open(lease.mutex_path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            raise RuntimeError("guard_not_regular")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        current = lease.mutex_path.lstat()
        if stat.S_ISLNK(current.st_mode) or (current.st_dev, current.st_ino) != (before.st_dev, before.st_ino):
            raise RuntimeError("guard_changed")
        if lease.has_active_owner():
            raise RuntimeError("account_in_use")
        yield
    finally:
        os.close(fd)


def network_child(account_name: str, target_file: Path, output: Path) -> int:
    def expired(*_):
        raise ProbeDeadline("whole_probe_deadline")
    # The child has its own bound even if the supervising process is killed.
    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, 53)
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(LEGACY))
    sys.path.insert(0, str(ROOT / "scripts"))
    scratch = output / "temporary"
    scratch.mkdir(parents=True, exist_ok=True)
    tempfile.tempdir = str(scratch)
    os.environ["TMPDIR"] = str(scratch)
    from lib.profile_completion import summarize_profile, merge_profile_summaries
    from bdhub import config
    from bdhub.enrich import pure_http_worker as worker, pure_http_worker_child as child
    from bdhub.enrich.shared_backoff import snapshot as backoff_snapshot
    from bdhub.enrich.creator_profile import merge_profiles

    targets = json.loads(target_file.read_text())["targets"]
    if not 1 <= len(targets) <= 3:
        raise ValueError("bounded_target_count")
    report = {"schema": "bdhub.italy-profile-probe.v1", "market": "it", "account": account_name,
              "startedAt": datetime.now(timezone.utc).isoformat(), "mode": "live_readonly_profile", "requests": [], "targets": [],
              "qps": 1, "businessRetries": 0, "captchaAttempts": 0, "oldDatabaseWrites": 0, "realSends": 0, "status": "starting"}
    report_file = output / "report.private.json"
    save = lambda: write_json(report_file, report)
    save()
    client = None
    probe = None
    try:
        ready = next((r for r in get_readiness()["accounts"] if r["name"] == account_name), None)
        if not ready or ready.get("startable") is not True:
            report.update(status="blocked", reason="account_not_startable")
            return 2
        cfg = config.load()
        prepared, unavailable = worker.prepare_collection_accounts(cfg, config.load_accounts(cfg), market="it", requested_names=[account_name])
        if not prepared:
            report.update(status="blocked", reason=unavailable.get(account_name, "account_not_prepared"))
            return 2
        selected = prepared[0]
        identity_file = Path(selected.account.headers_json)
        before_identity = hashlib.sha256(identity_file.read_bytes()).hexdigest()
        runtime = worker.validate_runtime(worker._DEFAULT_RUNTIME)
        report["transport"] = {"host": selected.identity["api_host"], "signerRegion": selected.identity["signer_region"], "marketCode": 8, "aid": selected.identity["aid"], "runtimeManifestValidated": True}
        report["coordination"] = "Existing canonical guard locked read-only for this short probe; other lease acquisition may briefly wait. No lease file created."
        save()
        with readonly_guard(selected.account):
            try:
                if worker.scheduled_relogin_svc.maintenance_due(selected.account, initialize=False, ignore_retry_throttle=True):
                    report.update(status="blocked", reason="maintenance_due")
                    return 2
                if backoff_snapshot(selected.identity, "it")["open"]:
                    report.update(status="blocked", reason="shared_backoff")
                    return 2
                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    probe = child._load_runtime(runtime)
                    child._configure_market_signer_runtime(probe, selected.identity, scratch)
                    client = probe.PureHttpPartnerClient({"partner_id": selected.identity["partner_id"], "qps": 1.0,
                        "profile_types": [1, 2, 6], "request_timeout_seconds": 15, "trust_env": True}, selected.identity, scratch)
                    child._configure_market_transport(client, selected.identity)

                def request(stage: str, body: dict, target_ref: str):
                    if worker.scheduled_relogin_svc.maintenance_due(selected.account, initialize=False, ignore_retry_throttle=True) or backoff_snapshot(selected.identity, "it")["open"]:
                        report.update(status="blocked", reason="account_maintenance_or_shared_backoff")
                        save()
                        return None
                    started = time.monotonic()
                    entry = {"targetRef": target_ref, "stage": stage, "profileTypes": body.get("profile_types"), "status": "inflight"}
                    report["requests"].append(entry)
                    save()
                    try:
                        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                            response, payload = client._signed_post_once(stage, body)
                        decision = classify_response(response.status_code, response.headers, payload)
                        entry.update({k:v for k,v in decision.items() if k != "allowed"},
                                     durationMs=round((time.monotonic()-started)*1000, 1), status="returned")
                        if not decision["allowed"]:
                            report.update(status="blocked", reason="verification_required" if decision["verificationRequired"] else "remote_error")
                            entry["stopAccount"] = True
                            save()
                            return None
                        return payload
                    except Exception as error:
                        entry.update(status="error", errorType=type(error).__name__, durationMs=round((time.monotonic()-started)*1000, 1), stopAccount=True)
                        report.update(status="bounded_timeout" if isinstance(error, ProbeDeadline) else "blocked", reason="whole_probe_deadline" if isinstance(error, ProbeDeadline) else "request_or_signer_error")
                        save()
                        return None
                    finally:
                        save()

                for target in targets:
                    handle = str(target["handle"]).strip().lstrip("@").lower()
                    if not handle or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789._" for c in handle):
                        raise ValueError("invalid_handle")
                    result = {"targetRef": target["ref"], "requestedHandle": handle, "externalId": target["externalId"], "historicalOecHint": target.get("historicalOecHint"), "profiles": []}
                    report["targets"].append(result)
                    found = request("find", {"query": handle, "pagination": {"size": 12, "page": 0}, "query_type": 1, "filter_params": {}, "algorithm": 1}, target["ref"])
                    if found is None:
                        break
                    exact = probe.find_exact(found, handle)
                    if not isinstance(exact, dict):
                        result.update(status="unresolved", reason="no_exact_handle", currentHandleResolved=False)
                        save()
                        continue
                    exact_summary = summarize_profile(exact)
                    if exact_summary["identity"]["market"] not in (None, "it"):
                        report.update(status="blocked", reason="find_market_mismatch")
                        break
                    oec = child._oec(exact)
                    if not oec.isdigit() or child._handle(exact).lower() != handle:
                        result.update(status="unresolved", reason="find_identity_invalid")
                        save()
                        continue
                    result.update(oecId=oec, currentHandleResolved=True, historicalHintMatches=None if not target.get("historicalOecHint") else oec == target["historicalOecHint"], find=exact_summary)
                    summaries = [exact_summary]
                    raw_profiles = [exact]
                    # Explicit controlled comparison, including [2] even if IT's minimal completeness check passed.
                    for profile_types in ([1, 2, 6], [2]):
                        payload = request("profile", {"creator_oec_id": oec, "profile_types": profile_types}, target["ref"])
                        if payload is None:
                            break
                        profile = payload.get("creator_profile")
                        if not isinstance(profile, dict) or child._oec(profile) != oec:
                            report.update(status="blocked", reason="profile_identity_mismatch")
                            break
                        summary = summarize_profile(profile)
                        if summary["identity"]["market"] not in (None, "it"):
                            report.update(status="blocked", reason="profile_market_mismatch")
                            break
                        result["profiles"].append({"profileTypes": profile_types, "summary": summary})
                        summaries.append(summary)
                        raw_profiles.append(profile)
                    if report["status"] in {"blocked", "bounded_timeout"}:
                        break
                    combined_raw = merge_profiles(raw_profiles)
                    if child._missing_required_fields(combined_raw, frozenset({"followers"})) or not child._handle(combined_raw):
                        payload = request("profile", {"creator_oec_id": oec, "profile_types": [1, 6]}, target["ref"])
                        if payload is None:
                            break
                        profile = payload.get("creator_profile")
                        if not isinstance(profile, dict) or child._oec(profile) != oec:
                            report.update(status="blocked", reason="supplement_identity_mismatch")
                            break
                        summary = summarize_profile(profile)
                        result["profiles"].append({"profileTypes": [1, 6], "summary": summary})
                        summaries.append(summary)
                    merged = merge_profile_summaries(summaries)
                    if (merged["identity"]["handle"] or "").lower() != handle or merged["identity"]["market"] != "it":
                        result.update(status="unresolved", reason="merged_identity_not_confirmed", merged=merged, currentHandleResolved=False)
                    else:
                        result.update(status="completed", merged=merged, currentPlatformIdentityVerified=True, historicalCrossSourceIdentityProven=False)
                    save()
                if report["status"] not in {"blocked", "bounded_timeout"}:
                    report["status"] = "completed"
                report["identityFileUnchanged"] = hashlib.sha256(identity_file.read_bytes()).hexdigest() == before_identity
                report["businessRequests"] = client.request_count
                report["sdkBootstrapSeparate"] = True
                save()
            finally:
                # Keep the existing guard until the entire account SDK/session lifecycle ends.
                if client is not None:
                    client.session.close()
                    client = None
                if probe is not None:
                    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                        probe.signer_impl.reset_signer()
                    probe = None
    except Exception as error:
        report.update(status="blocked", reason="probe_initialization_or_validation_error", errorType=type(error).__name__)
    finally:
        if client is not None:
            client.session.close()
        if probe is not None:
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                probe.signer_impl.reset_signer()
        report["finishedAt"] = datetime.now(timezone.utc).isoformat()
        save()
        signal.setitimer(signal.ITIMER_REAL, 0)
    return 0 if report["status"] == "completed" else 2


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--account", default="acc6")
    parser.add_argument("--targets", type=Path, default=VAR / "italy-profile-probe-targets.json")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--network-child", action="store_true")
    args = parser.parse_args()
    output = (args.output or VAR / ("italy-profile-probe-" + datetime.now().strftime("%Y%m%d-%H%M%S"))).resolve()
    if not output.is_relative_to(VAR.resolve()) or not args.targets.resolve().is_relative_to(VAR.resolve()):
        raise ValueError("Probe input and output must stay in new project var.")
    output.mkdir(parents=True, exist_ok=True)
    if args.network_child:
        return network_child(args.account, args.targets.resolve(), output)
    if (output / "report.private.json").exists():
        raise ValueError("Existing probe evidence is immutable; select a new output directory.")
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    command = [sys.executable, str(Path(__file__).resolve()), "--network-child", "--account", args.account, "--targets", str(args.targets.resolve()), "--output", str(output)]
    child_process = subprocess.Popen(command, env=env, start_new_session=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    timeout = False
    def cancelled(*_):
        raise KeyboardInterrupt()
    previous_term = signal.signal(signal.SIGTERM, cancelled)
    try:
        try:
            child_process.wait(timeout=55)
        except subprocess.TimeoutExpired:
            timeout = True
    finally:
        # Also clean up on user cancellation/parent exceptions, not just a timeout.
        try:
            os.killpg(child_process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            child_process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(child_process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            child_process.wait()
        signal.signal(signal.SIGTERM, previous_term)
        shutil.rmtree(output / "temporary", ignore_errors=True)
    report_file = output / "report.private.json"
    report = json.loads(report_file.read_text()) if report_file.exists() else {"status": "blocked", "reason": "child_no_report"}
    if timeout:
        report.update(status="bounded_timeout", reason="whole_probe_deadline")
        write_json(report_file, report)
    public = {"status": report.get("status"), "reason": report.get("reason"), "account": args.account,
              "completedTargets": sum(t.get("status") == "completed" for t in report.get("targets", [])),
              "requests": [{k:v for k,v in row.items() if k not in {"payload", "body"}} for row in report.get("requests", [])],
              "identityFileUnchanged": report.get("identityFileUnchanged"), "oldDatabaseWrites": 0, "realSends": 0,
              "privateReport": str(report_file)}
    write_json(output / "summary.json", public)
    print(json.dumps(public, ensure_ascii=False))
    return 0 if report.get("status") == "completed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
