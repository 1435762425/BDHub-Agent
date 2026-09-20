#!/usr/bin/env python3
"""Bounded read-only Italy Find/Profile probe using the existing MX transport.

Uses the existing market-aware verification/replay implementation in memory.
No old database/identity writes, browser launch or sends. Only allowlisted
profile summaries enter new var/; transient SDK files are removed on exit.
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
from contextlib import closing, contextmanager, redirect_stdout, redirect_stderr
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
LEGACY = Path("/Users/bjn00003/BDHub/01-BDSystem-V2")
VAR = ROOT / "var"
sys.path.insert(0, str(ROOT / "scripts"))
from lib.legacy_runtime import configure_vendored_bdhub
STOP_CODES = {"10000", "10001", "98000001", "98000002", "16901008"}


class ProbeDeadline(BaseException):
    pass


def collect_counters(client) -> dict:
    if getattr(client,'cohort_counters',None) is not None:return dict(client.cohort_counters)
    def count(value):
        return value if type(value) is int and value >= 0 else 0
    result = {key: count(getattr(client, key, 0)) for key in (
        "request_count", "challenge_count", "captcha_success_count",
        "captcha_replay_response_count", "captcha_replay_code0_count", "code10000_missing_header_count")}
    stages = getattr(client, "captcha_by_stage", {})
    result["byStage"] = {stage: {key: count(stages.get(stage, {}).get(key, 0)) for key in (
        "challenges", "verify_successes", "replay_responses", "replay_code0")} for stage in ("find", "profile")}
    return result


def initialize_client(probe, child, identity, scratch, qps=1.0):
    child._configure_market_signer_runtime(probe, identity, scratch)
    child._configure_market_captcha_runtime(probe, identity, scratch)
    client = probe.PureHttpPartnerClient({"partner_id": identity["partner_id"], "qps": qps,
        "profile_types": [1, 2, 6], "request_timeout_seconds": 15, "trust_env": True,
        "business_retries": 2, "captcha_attempts": 3}, identity, scratch)
    # Assign actual bounds explicitly: legacy constructors use `value or 3`.
    client.business_retries = 2
    client.captcha_attempts = 3
    try:
        child._configure_market_transport(client, identity)
    except BaseException:
        client.session.close()
        raise
    return client


def normalize_target(target: dict) -> dict:
    """An explicit OEC wins over a mutable handle; historical hints do not."""
    if not isinstance(target, dict) or not isinstance(target.get("ref"), str) or not target["ref"]:
        raise ValueError("invalid_target_ref")
    oec = target.get("oecId")
    if oec is not None and (not isinstance(oec, str) or not oec.isascii() or not oec.isdigit() or len(oec) > 40):
        raise ValueError("invalid_oec_id")
    handle = target.get("handle")
    if handle is not None:
        if not isinstance(handle, str):
            raise ValueError("invalid_handle")
        handle = handle.strip().lstrip("@").lower()
        if not 1 <= len(handle) <= 100 or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789._" for c in handle):
            raise ValueError("invalid_handle")
    if not oec and not handle:
        raise ValueError("target_identity_required")
    external = target.get("externalId", f"oec:{oec}" if oec else None)
    if not isinstance(external, str) or not external:
        raise ValueError("target_source_ref_required")
    return {"ref": target["ref"], "inputKind": "known_oec" if oec else "handle_discovery",
            "oecId": oec, "handle": None if oec else handle, "auditHandle": handle if oec else None,
            "externalId": external, "historicalOecHint": target.get("historicalOecHint")}


def classify_response(status: int, headers: dict, payload) -> dict:
    raw_code = payload.get("code") if isinstance(payload, dict) else None
    code = None if raw_code is None else str(raw_code)
    if code is not None and (isinstance(raw_code, bool) or not code.isascii() or not code.lstrip("-").isdigit() or len(code) > 16):
        code = "invalid"
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
def readonly_guard(account, *, wait_seconds=0):
    from bdhub.enrich.profile_lease import ProfileLease
    lease = ProfileLease(account.profile_dir, account=account.name, market="it", operation="agent-readonly-profile-probe")
    fd = os.open(lease.mutex_path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            raise RuntimeError("guard_not_regular")
        deadline=time.monotonic()+wait_seconds
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB);break
            except BlockingIOError:
                if time.monotonic()>=deadline:raise
                time.sleep(0.25)
        current = lease.mutex_path.lstat()
        if stat.S_ISLNK(current.st_mode) or (current.st_dev, current.st_ino) != (before.st_dev, before.st_ino):
            raise RuntimeError("guard_changed")
        if lease.has_active_owner():
            raise RuntimeError("account_in_use")
        yield
    finally:
        os.close(fd)


def permits_readiness_canary(ready,enabled,identity_only,targets,cohort,stress=False):
    return bool(enabled is True and identity_only and (len(targets)==1 or stress) and not cohort and ready
                and not ready.get('manual_paused') and not ready.get('market_paused')
                and ready.get('blockers') and all(b.get('code')=='unchecked' for b in ready['blockers']))

def network_child(account_name: str, target_file: Path, output: Path) -> int:
    def expired(*_):
        raise ProbeDeadline("whole_probe_deadline")
    # The child has its own bound even if the supervising process is killed.
    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, 175)
    os.umask(0o077)
    sys.dont_write_bytecode = True
    configure_vendored_bdhub(root=ROOT, legacy_root=LEGACY)
    scratch = output / "temporary"
    scratch.mkdir(parents=True, exist_ok=True)
    tempfile.tempdir = str(scratch)
    os.environ["TMPDIR"] = str(scratch)
    from lib.profile_completion import summarize_profile, merge_profile_summaries
    from bdhub import config
    from bdhub.enrich import pure_http_worker as worker, pure_http_worker_child as child
    from bdhub.enrich.shared_backoff import snapshot as backoff_snapshot
    from bdhub.enrich.creator_profile import merge_profiles

    target_input=json.loads(target_file.read_text())
    if type(target_input.get("identityOnly",False)) is not bool:raise ValueError("invalid_identity_mode")
    identity_only=target_input.get("identityOnly",False)
    targets = [normalize_target(t) for t in target_input["targets"]]
    cohort=target_input.get('cohortId')
    stress=target_input.get('stressRun') is not None;stress_folder=None;stress_fingerprint=None;rate=3
    if stress:
        from lib.identity_stress import stress_case
        stress_folder,case,stress_fingerprint=stress_case(ROOT,target_input,account_name)
        rate=case['qps'];target_input['httpLanes']=case['lanes']
    soak=target_input.get('soakRun')
    if soak:
        if stress or not cohort or not identity_only:raise ValueError('invalid_soak_mode')
        from lib.identity_soak import read_soak
        soak_config=read_soak(ROOT,soak,cohort,account_name,check_members=True);rate=soak_config['qps'];target_input['httpLanes']=soak_config['lanes']
    runtime_acceptance=target_input.get('runtimeAcceptance')
    if runtime_acceptance:
        if stress or soak or not cohort:raise ValueError('invalid_runtime_policy_mode')
        from lib.identity_acceptance import production_policy
        policy=production_policy(ROOT,account_name)
        if policy['acceptanceId']!=runtime_acceptance:raise ValueError('identity_rate_not_published')
        rate=policy['qps'];target_input['httpLanes']=policy['lanes']
    lanes=target_input.get('httpLanes',3)
    canary=target_input.get('readinessCanary',False)
    if type(canary) is not bool:raise ValueError('invalid_canary_mode')
    if type(lanes) is not int or lanes not in (3,6,9):raise ValueError('invalid_cohort_lanes')
    if not cohort and not stress and lanes!=3:raise ValueError('cohort_required')
    if cohort:
        import sqlite3,re
        if not identity_only or not isinstance(cohort,str) or not re.fullmatch(r'discovery_cohort_[a-f0-9]{32}',cohort):raise ValueError('invalid_cohort')
        with closing(sqlite3.connect((VAR/'creator-discovery.sqlite').resolve().as_uri()+'?mode=ro',uri=True)) as db:
            row=db.execute("SELECT payload FROM discovery_cohort WHERE id=? AND state='running'",(cohort,)).fetchone()
            if not row:raise ValueError('cohort_not_active')
            registered={r['id']:r for r in json.loads(row[0])}
            if len(targets)!=len(registered) or {t['ref'] for t in targets}!=set(registered) or any(t['ref'] not in registered or t['handle']!=registered[t['ref']]['handle'] or t['externalId']!=t['ref'] for t in targets):raise ValueError('cohort_scope_mismatch')
    if not 1 <= len(targets) <= (40 if stress else 50 if cohort else 3):
        raise ValueError("bounded_target_count")
    report = {"schema": "bdhub.italy-profile-probe.v3", "market": "it", "account": account_name,
              "startedAt": datetime.now(timezone.utc).isoformat(), "mode": "live_readonly_profile", "requests": [], "targets": [],
              "qps": rate if stress or soak or runtime_acceptance else 3 if cohort else 1, "businessRetries": 2, "captchaAttempts": 3, "verificationMode": "existing_market_aware_http_pipeline",
              "oldDatabaseWrites": 0, "realSends": 0, "status": "starting", "identityOnly": identity_only}
    if runtime_acceptance:report.update(runtimeAcceptance=runtime_acceptance)
    if soak:report.update(soakRun=soak,mode='live_identity_stability_validation')
    if stress:report.update(stressRun=target_input['stressRun'],stressCase=target_input['stressCase'],mode='live_readonly_identity_stress')
    report_file = output / "report.private.json"
    save = lambda: write_json(report_file, report)
    save()
    client = None
    probe = None
    try:
        ready = next((r for r in get_readiness()["accounts"] if r["name"] == account_name), None)
        if (not ready or ready.get("startable") is not True) and not permits_readiness_canary(ready,canary or stress,identity_only,targets,cohort,stress=stress):
            report.update(status="blocked", reason="account_not_startable")
            return 2
        report['readinessCanary']=canary
        cfg = config.load()
        prepared, unavailable = worker.prepare_collection_accounts(cfg, config.load_accounts(cfg), market="it", requested_names=[account_name])
        if not prepared:
            report.update(status="blocked", reason="account_not_prepared")
            return 2
        selected = prepared[0]
        identity_file = Path(selected.account.headers_json)
        before_identity = hashlib.sha256(identity_file.read_bytes()).hexdigest()
        if stress and before_identity!=stress_fingerprint:raise ValueError('stress_identity_changed')
        runtime = worker.validate_runtime(worker._DEFAULT_RUNTIME)
        report["transport"] = {"host": selected.identity["api_host"], "signerRegion": selected.identity["signer_region"], "marketCode": 8, "aid": selected.identity["aid"], "runtimeManifestValidated": True}
        report["coordination"] = "Existing canonical guard locked read-only for this short probe; other lease acquisition may briefly wait. No lease file created."
        save()
        with (readonly_guard(selected.account,wait_seconds=15) if cohort or stress else readonly_guard(selected.account)):
            try:
                if hashlib.sha256(identity_file.read_bytes()).hexdigest() != before_identity:
                    report.update(status="blocked", reason="identity_changed_before_guard")
                    return 2
                if worker.scheduled_relogin_svc.maintenance_due(selected.account, initialize=False, ignore_retry_throttle=True):
                    report.update(status="blocked", reason="maintenance_due")
                    return 2
                if backoff_snapshot(selected.identity, "it")["open"]:
                    report.update(status="blocked", reason="shared_backoff")
                    return 2
                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    probe = child._load_runtime(runtime)
                    client = initialize_client(probe, child, selected.identity, scratch,qps=3.0 if cohort or stress else 1.0)
                report.update(businessRetries=client.business_retries, captchaAttempts=client.captcha_attempts)

                def request(stage: str, body: dict, target_ref: str):
                    if worker.scheduled_relogin_svc.maintenance_due(selected.account, initialize=False, ignore_retry_throttle=True) or backoff_snapshot(selected.identity, "it")["open"]:
                        report.update(status="blocked", reason="account_maintenance_or_shared_backoff")
                        save()
                        return None
                    started = time.monotonic()
                    entry = {"targetRef": target_ref, "stage": stage, "profileTypes": body.get("profile_types"), "status": "inflight", "attempts": [], "verificationAttempts": []}
                    report["requests"].append(entry)
                    save()
                    original_once = client._signed_post_once
                    original_solve = client._solve_captcha
                    def observed_once(inner_stage, inner_body):
                        response, payload = original_once(inner_stage, inner_body)
                        entry["attempts"].append(classify_response(response.status_code, response.headers, payload))
                        report["counters"] = collect_counters(client)
                        save()
                        return response, payload
                    def observed_solve(verify_data, attempt):
                        verification = {"attempt": attempt, "status": "inflight"}
                        entry["verificationAttempts"].append(verification)
                        save()
                        try:
                            result = original_solve(verify_data, attempt)
                            verification["status"] = "returned"
                            return result
                        except (Exception, ProbeDeadline) as error:
                            message=str(error)
                            category=("runtime" if "运行时" in message else "captcha_get" if "captcha/get" in message
                                      else "template_size" if "模板尺寸大于背景图" in message
                                      else "image_decode" if "图片解码失败" in message
                                      else "contour_missing" if "透明通道中未找到" in message
                                      else "contour_empty" if "轮廓为空" in message
                                      else "background_width" if "背景图宽度无效" in message
                                      else "verified_product_missing" if "验证后仍未返回商品结构化数据" in message
                                      else "image" if "图" in message or "轮廓" in message else "node" if "Node.js" in message
                                      else "verify_http" if message.startswith("slider_verify_failed_http_")
                                      else "network" if "HTTP 请求失败" in message else "solver")
                            verification.update(status="error", errorType=type(error).__name__,errorCategory=category)
                            raise
                        finally:
                            report["counters"] = collect_counters(client)
                            save()
                    client._signed_post_once = observed_once
                    client._solve_captcha = observed_solve
                    try:
                        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                            response, payload = client.post(stage, body)
                        decision = classify_response(response.status_code, response.headers, payload)
                        entry.update({k:v for k,v in decision.items() if k != "allowed"},
                                     durationMs=round((time.monotonic()-started)*1000, 1), status="returned")
                        if not decision["allowed"]:
                            report.update(status="blocked", reason="verification_required" if decision["verificationRequired"] else "remote_error")
                            entry["stopAccount"] = True
                            save()
                            return None
                        return payload
                    except (Exception, ProbeDeadline) as error:
                        entry.update(status="error", errorType=type(error).__name__, durationMs=round((time.monotonic()-started)*1000, 1), stopAccount=True)
                        report.update(status="bounded_timeout" if isinstance(error, ProbeDeadline) else "blocked", reason="whole_probe_deadline" if isinstance(error, ProbeDeadline) else "request_or_signer_error")
                        save()
                        return None
                    finally:
                        client._signed_post_once = original_once
                        client._solve_captcha = original_solve
                        report["counters"] = collect_counters(client)
                        save()

                if cohort or stress:
                    from lib.cohort_find import run_find_cohort
                    def allowed(ref):
                        if worker.scheduled_relogin_svc.maintenance_due(selected.account,initialize=False,ignore_retry_throttle=True) or backoff_snapshot(selected.identity,'it')['open']:return False
                        if soak:
                            try:read_soak(ROOT,soak,cohort,account_name)
                            except ValueError:return False
                        if stress:return not (stress_folder/'STOP').exists()
                        with closing(sqlite3.connect((VAR/'creator-discovery.sqlite').resolve().as_uri()+'?mode=ro',uri=True)) as db:
                            live=db.execute("SELECT i.status,b.status FROM discovery_item i JOIN discovery_batch b ON b.id=i.batch_id WHERE i.id=?",(ref,)).fetchone()
                        return bool(live and live[0]=='running' and live[1]!='paused')
                    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                        run_find_cohort(probe,child,selected.identity,scratch,targets,client,report,save,classify_response,summarize_profile,collect_counters,allowed,lanes=lanes,qps=rate if stress or soak or runtime_acceptance else 3)
                for target in ([] if cohort or stress else targets):
                    handle, oec = target["handle"], target["oecId"]
                    result = {"targetRef": target["ref"], "inputKind": target["inputKind"], "requestedHandle": handle,
                              "requestedOecId": oec, "auditHandle": target["auditHandle"], "externalId": target["externalId"],
                              "historicalOecHint": target.get("historicalOecHint"), "profiles": []}
                    report["targets"].append(result)
                    summaries, raw_profiles = [], []
                    if handle:
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
                        if not oec.isascii() or not oec.isdigit() or child._handle(exact).lower() != handle:
                            result.update(status="unresolved", reason="find_identity_invalid")
                            save()
                            continue
                        result.update(currentHandleResolved=True, historicalHintMatches=None if not target.get("historicalOecHint") else oec == target["historicalOecHint"], find=exact_summary)
                        summaries.append(exact_summary)
                        raw_profiles.append(exact)
                    result["oecId"] = oec
                    if identity_only and handle and exact_summary["identity"]["market"] == "it":
                        result.update(status="identity_verified",currentPlatformIdentityVerified=True,
                                      historicalCrossSourceIdentityProven=False,profileCollection="not_requested")
                        save()
                        continue
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
                    if (child._missing_required_fields(combined_raw, frozenset({"followers"})) or not child._handle(combined_raw)
                            or merge_profile_summaries(summaries)["identity"]["market"] is None):
                        payload = request("profile", {"creator_oec_id": oec, "profile_types": [1, 6]}, target["ref"])
                        if payload is None:
                            break
                        profile = payload.get("creator_profile")
                        if not isinstance(profile, dict) or child._oec(profile) != oec:
                            report.update(status="blocked", reason="supplement_identity_mismatch")
                            break
                        summary = summarize_profile(profile)
                        if summary["identity"]["market"] not in (None, "it"):
                            report.update(status="blocked", reason="supplement_market_mismatch")
                            break
                        result["profiles"].append({"profileTypes": [1, 6], "summary": summary})
                        summaries.append(summary)
                    merged = merge_profile_summaries(summaries)
                    current_handle = merged["identity"]["handle"]
                    if (handle and (current_handle or "").lower() != handle) or merged["identity"]["market"] != "it" or merged["identity"]["oecId"] != oec:
                        result.update(status="unresolved", reason="merged_identity_not_confirmed", merged=merged, currentHandleResolved=False)
                    else:
                        result.update(status="completed", merged=merged, currentPlatformIdentityVerified=True,
                                      currentHandleResolved=bool(current_handle), historicalCrossSourceIdentityProven=False,
                                      handleChangedFromAudit=None if not target["auditHandle"] or not current_handle else current_handle.lower() != target["auditHandle"],
                                      handleObservation="remote_observed" if current_handle else "not_returned_keep_stored_alias")
                    save()
                if report["status"] not in {"blocked", "bounded_timeout"}:
                    report["status"] = "completed"
                report["identityFileUnchanged"] = hashlib.sha256(identity_file.read_bytes()).hexdigest() == before_identity
                report["businessRequests"] = collect_counters(client).get("request_count",0)
                report["sdkBootstrapSeparate"] = True
                save()
            finally:
                # Keep the existing guard until the entire account SDK/session lifecycle ends.
                report["identityFileUnchanged"] = hashlib.sha256(identity_file.read_bytes()).hexdigest() == before_identity
                try:
                    if client is not None:
                        report["counters"] = collect_counters(client)
                        client.session.close()
                finally:
                    client = None
                    if probe is not None:
                        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                            probe.signer_impl.reset_signer()
                        probe = None
    except (Exception, ProbeDeadline) as error:
        report.update(status="bounded_timeout" if isinstance(error, ProbeDeadline) else "blocked",
                      reason="whole_probe_deadline" if isinstance(error, ProbeDeadline) else "probe_initialization_or_validation_error", errorType=type(error).__name__)
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
            child_process.wait(timeout=180)
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
              "completedTargets": sum(t.get("status") in ("completed","identity_verified") for t in report.get("targets", [])),
              "identityOnly":report.get("identityOnly",False),
              "profileCompletedTargets":sum(t.get("status")=="completed" for t in report.get("targets", [])),
              "requests": [{k:v for k,v in row.items() if k in {"targetRef", "stage", "profileTypes", "status", "httpStatus", "code", "verificationRequired", "systemError3", "durationMs", "errorType", "stopAccount", "attempts", "verificationAttempts"}} for row in report.get("requests", [])],
              "counters": report.get("counters"), "businessRetries": report.get("businessRetries"), "captchaAttempts": report.get("captchaAttempts"),
              "identityFileUnchanged": report.get("identityFileUnchanged"), "oldDatabaseWrites": 0, "realSends": 0,
              "privateReport": str(report_file)}
    write_json(output / "summary.json", public)
    print(json.dumps(public, ensure_ascii=False))
    return 0 if report.get("status") == "completed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
