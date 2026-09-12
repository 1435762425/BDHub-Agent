"""Guarded IT/ACC6 HTTP runtime for an explicitly frozen sender binding.

The outer CLI must impose a whole-process deadline. Auth/IM use their proven
environment-proxy route; card reads retain their separately proven direct route.
Only the new system shares this sender FIFO. Read-only legacy conflict checks
are snapshots, never a claim of atomic coordination with the old project.
"""
from __future__ import annotations

from contextlib import contextmanager, redirect_stdout, redirect_stderr
import fcntl
import hashlib
import importlib.util
import io
import json
import math
import os
from pathlib import Path
import re
import stat
import sys
import time

from lib.italy_im_auth import ItalyImAuthContext, ItalyImAuthError, authenticate_it
from lib.italy_im_delivery import ItalyImDeliveryAdapter, ItalyVerifiedProductCard
from lib.italy_im_session import ItalyImReadSession
from lib.second_card_binding import refresh_card_binding

ROOT = Path(__file__).resolve().parents[2]
VAR = ROOT / "var"
LEGACY = Path("/Users/bjn00003/BDHub/01-BDSystem-V2")
LEGACY_GATE_DIRECTORY = LEGACY / "data/runtime/im-http-write-gates"
SAFE_CODES = frozenset({"live_sender_binding_required", "live_sender_binding_mismatch", "live_sender_invalid",
    "live_runtime_unavailable", "live_runtime_closed", "live_stopped", "live_maintenance_due",
    "live_guard_busy", "live_guard_invalid", "live_identity_changed", "live_interval_invalid",
    "live_var_invalid", "legacy_gate_busy", "legacy_gate_unknown", "legacy_gate_invalid",
    "legacy_recent_dispatch", "legacy_send_conflict", "legacy_state_unavailable", "live_gate_failed",
    "live_mark_reused", "live_market_send_unavailable"})


class SecondLiveRuntimeError(RuntimeError):
    def __init__(self, code):
        self.code = code if code in SAFE_CODES else "live_runtime_unavailable"
        super().__init__(self.code)


def _legacy_imports():
    sys.dont_write_bytecode = True
    if str(LEGACY) not in sys.path:
        sys.path.insert(0, str(LEGACY))


def _load_runtime():
    _legacy_imports()
    spec = importlib.util.spec_from_file_location("second_existing_it_im_probe", ROOT / "scripts/probe-italy-im.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.load_runtime()


def _resolve_policy(account):
    _legacy_imports()
    from bdhub.account_policy import resolve_account_policy
    return resolve_account_policy(account)


def _shared_gate(sender_id, **options):
    _legacy_imports()
    from bdhub.send.http_write_gate import shared_write_gate
    return shared_write_gate(sender_id, **options)


def _numeric(value):
    if not isinstance(value, str) or re.fullmatch(r"[0-9]{1,19}", value) is None or not 0 < int(value) <= (1 << 63) - 1:
        raise SecondLiveRuntimeError("live_sender_invalid")
    return value


def sender_binding_sha256(auth):
    """Canonical non-secret sender identity; no token, Cookie or full native context."""
    if (not isinstance(auth, ItalyImAuthContext) or auth.account_name != "acc6"
            or not isinstance(auth.native_context, dict) or auth.native_context.get("market_region") != "8"):
        raise SecondLiveRuntimeError("live_sender_invalid")
    binding = {"account": "acc6", "market": "it", "im_id": _numeric(auth.im_id),
               "market_id": _numeric(auth.native_context.get("market_id")),
               "partner_id": _numeric(auth.native_context.get("partner_id"))}
    return hashlib.sha256(json.dumps(binding, sort_keys=True, ensure_ascii=False,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _fingerprint(path):
    try:
        with Path(path).open("rb") as handle:
            return hashlib.sha256(handle.read()).digest()
    except (OSError, TypeError, ValueError):
        raise SecondLiveRuntimeError("live_identity_changed") from None


@contextmanager
def _authenticated(report, *, stopped):
    try:
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            account, identity, load_identity, guard, maintenance, capability = _load_runtime()
        if getattr(account, "name", None) != "acc6" or getattr(identity, "market", None) != "it":
            raise SecondLiveRuntimeError("live_sender_invalid")
        path = Path(account.headers_json)
        before = _fingerprint(path)
        if stopped():
            raise SecondLiveRuntimeError("live_stopped")
        guarded = guard(account)
        guarded.__enter__()
    except (SecondLiveRuntimeError, ItalyImAuthError):
        raise
    except BlockingIOError:
        raise SecondLiveRuntimeError("live_guard_busy") from None
    except RuntimeError as error:
        raise SecondLiveRuntimeError("live_guard_busy" if str(error) == "account_in_use" else "live_guard_invalid") from None
    except Exception:
        raise SecondLiveRuntimeError("live_runtime_unavailable") from None
    try:
        def available():
            if stopped():
                raise SecondLiveRuntimeError("live_stopped")
            if maintenance():
                raise SecondLiveRuntimeError("live_maintenance_due")
            if _fingerprint(path) != before:
                raise SecondLiveRuntimeError("live_identity_changed")
        available()
        try:
            bundle = load_identity(path)
            auth = authenticate_it(account, identity, bundle.headers, report,
                maintenance_due=maintenance, stopped=stopped, use_environment_proxy=True)
        except ItalyImAuthError:
            raise
        except Exception:
            raise SecondLiveRuntimeError("live_runtime_unavailable") from None
        available()
        report.update(canonicalGuard="existing_readonly_mutex", sendCapability=capability,
                      oldLeaseWrites=0, legacyDatabaseWrites=0, identityFileWrites=0,
                      outerWallDeadlineRequired=True)
        yield account, identity, bundle.headers, auth, maintenance, available
    finally:
        try:
            report["identityFileUnchanged"] = _fingerprint(path) == before
        finally:
            guarded.__exit__(None, None, None)


def read_sender_binding(report, *, stopped=lambda: False):
    """Only authenticate the sender under the read-only guard; no IM session/write adapter."""
    with _authenticated(report, stopped=stopped) as (_, _, _, auth, _, available):
        available()
        binding = sender_binding_sha256(auth)
        report["senderBindingHash"] = binding
        return binding


def _pid_alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        raise SecondLiveRuntimeError("legacy_gate_unknown") from None
    return True


def check_legacy_gate(sender_id, *, interval, directory=LEGACY_GATE_DIRECTORY, wall_time=time.time, pid_alive=_pid_alive):
    """Inspect the same-sender old FIFO without writing/cleaning it or its leases."""
    _numeric(sender_id)
    path = Path(directory) / (hashlib.sha256(sender_id.encode()).hexdigest() + ".lock")
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return {"state": "absent", "active": False}
    except OSError:
        raise SecondLiveRuntimeError("legacy_gate_invalid") from None
    with os.fdopen(descriptor, "rb") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise SecondLiveRuntimeError("legacy_gate_invalid")
        try:
            fcntl.flock(handle, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SecondLiveRuntimeError("legacy_gate_busy") from None
        try:
            raw = handle.read(65537)
            if len(raw) > 65536:
                raise ValueError()
            state = json.loads(raw) if raw else {"last_dispatch": 0.0, "queue": []}
            if not isinstance(state, dict) or not isinstance(state.get("queue", []), list):
                raise ValueError()
            queue = state.get("queue", [])
            if len(queue) > 64:
                raise ValueError()
            last = state.get("last_dispatch")
            if isinstance(last, bool) or not isinstance(last, (int, float)) or not math.isfinite(last) or last < 0 or last > wall_time() + 60:
                raise ValueError()
            dead_waiters = 0
            for entry in queue:
                if (not isinstance(entry, dict) or type(entry.get("pid")) is not int or entry["pid"] <= 0
                        or type(entry.get("active")) is not bool or not isinstance(entry.get("token"), str) or not entry["token"]):
                    raise ValueError()
                if entry["active"]:
                    raise SecondLiveRuntimeError("legacy_gate_busy" if pid_alive(entry["pid"]) else "legacy_gate_unknown")
                if pid_alive(entry["pid"]):
                    raise SecondLiveRuntimeError("legacy_gate_busy")
                dead_waiters += 1
            if last and wall_time() < last + interval:
                raise SecondLiveRuntimeError("legacy_recent_dispatch")
            return {"state": "idle", "active": False, "deadInactiveWaiters": dead_waiters}
        except SecondLiveRuntimeError:
            raise
        except (TypeError, ValueError, OSError, OverflowError):
            raise SecondLiveRuntimeError("legacy_gate_invalid") from None
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


# Source: legacy schema send_task/reply_outbox/im_delivery_intent and send_tasks.py.
# A paused parent does not make an unresolved child send safe.
LEGACY_CONFLICT_SQL = """
SELECT
 EXISTS (SELECT 1 FROM send_task WHERE bd_market = 'it' AND status = 'running') AS task_enabled,
 EXISTS (SELECT 1 FROM reply_outbox WHERE bd_market = 'it' AND status IN ('queued','unknown')) AS outbox_pending,
 EXISTS (SELECT 1 FROM im_delivery_intent WHERE bd_market = 'it' AND status IN ('sending','unknown')) AS delivery_pending,
 EXISTS (SELECT 1 FROM send_task_component c
         JOIN send_task_lead l ON l.lead_id = c.lead_id
         JOIN send_task t ON t.task_id = l.task_id
         WHERE t.bd_market = 'it' AND c.status IN ('sending','unknown')) AS component_pending,
 EXISTS (SELECT 1 FROM send_component_attempt a
         JOIN send_task_component c ON c.component_id = a.component_id
         JOIN send_task_lead l ON l.lead_id = c.lead_id
         JOIN send_task t ON t.task_id = l.task_id
         WHERE t.bd_market = 'it' AND (a.status = 'sending' OR (a.status = 'unknown'
           AND COALESCE(a.transport_snapshot #>> '{manual_resolution,resolution}', '')
               NOT IN ('confirmed_sent','confirmed_not_sent')))) AS attempt_pending
"""


@contextmanager
def _legacy_connection():
    """Independent read-only PostgreSQL connection; no legacy store/init/migration."""
    _legacy_imports()
    from bdhub import config
    from sqlalchemy import create_engine
    from sqlalchemy.engine import make_url
    from sqlalchemy.pool import NullPool
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        url = make_url(config.load().db_url)
    if url.get_backend_name() != "postgresql" or url.host not in {"localhost", "127.0.0.1", "::1"}:
        raise SecondLiveRuntimeError("legacy_state_unavailable")
    engine = create_engine(url, poolclass=NullPool, connect_args={"connect_timeout": 5,
        "options": "-c default_transaction_read_only=on -c statement_timeout=5000 -c lock_timeout=1000"})
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql("SET TRANSACTION READ ONLY")
            yield connection
    finally:
        engine.dispose()


def check_legacy_send_state(report, *, connection_factory=None):
    try:
        with (connection_factory or _legacy_connection)() as connection:
            row = dict(connection.exec_driver_sql(LEGACY_CONFLICT_SQL).mappings().one())
        if set(row) != {"task_enabled", "outbox_pending", "delivery_pending", "component_pending", "attempt_pending"} or any(type(value) is not bool for value in row.values()):
            raise ValueError()
    except Exception:
        raise SecondLiveRuntimeError("legacy_state_unavailable") from None
    report.setdefault("legacyConflictChecks", []).append(row)
    if any(row.values()):
        raise SecondLiveRuntimeError("legacy_send_conflict")
    return row


def _new_gate_directory(var_dir):
    try:
        directory = Path(var_dir).resolve()
        root = VAR.resolve()
        if directory != root and not directory.is_relative_to(root):
            raise ValueError()
        target = (directory / "im-http-write-gates").resolve()
        if not target.is_relative_to(root):
            raise ValueError()
        return target
    except (OSError, TypeError, ValueError):
        raise SecondLiveRuntimeError("live_var_invalid") from None


@contextmanager
def live_runtime(expected_sender_binding_hash, report, *, var_dir=VAR, stopped=lambda: False):
    if not isinstance(expected_sender_binding_hash, str) or re.fullmatch(r"[0-9a-f]{64}", expected_sender_binding_hash) is None:
        raise SecondLiveRuntimeError("live_sender_binding_required")
    gate_directory = _new_gate_directory(var_dir)
    with _authenticated(report, stopped=stopped) as (account, identity, headers, auth, maintenance, available):
        if report.get("sendCapability") not in {"canary", "enabled"}:
            raise SecondLiveRuntimeError("live_market_send_unavailable")
        binding = sender_binding_sha256(auth)
        if binding != expected_sender_binding_hash:
            raise SecondLiveRuntimeError("live_sender_binding_mismatch")
        policy = _resolve_policy(account)
        interval = policy.im_send_interval_seconds
        if (not policy.im_send_pool or isinstance(interval, bool) or not isinstance(interval, (int, float))
                or not math.isfinite(interval) or interval <= 0):
            raise SecondLiveRuntimeError("live_interval_invalid")
        closed = False
        def check():
            if closed:
                raise SecondLiveRuntimeError("live_runtime_closed")
            available()
            if sender_binding_sha256(auth) != expected_sender_binding_hash:
                raise SecondLiveRuntimeError("live_sender_binding_mismatch")
        def conflicts():
            check()
            gate_state = check_legacy_gate(auth.im_id, interval=interval)
            report["legacyWriteGate"] = gate_state
            check_legacy_send_state(report)
        conflicts()
        report.update(senderBindingHash=binding, senderWriteIntervalSeconds=interval,
            writeGateScope="new_system_sender_only", crossProjectAtomicCoordination=False,
            legacyGateWrites=0, legacyServicesStopped=0)
        @contextmanager
        def write_gate():
            conflicts()
            try:
                manager = _shared_gate(auth.im_id, interval=interval, stopped=lambda: closed or stopped(), directory=gate_directory)
                mark = manager.__enter__()
            except Exception:
                raise SecondLiveRuntimeError("live_gate_failed") from None
            try:
                conflicts()
                marked = False
                def mark_once():
                    nonlocal marked
                    if marked:
                        raise SecondLiveRuntimeError("live_mark_reused")
                    conflicts()
                    mark()
                    marked = True
                yield mark_once
            finally:
                manager.__exit__(None, None, None)
        def validate_card(previous):
            check()
            if not isinstance(previous, ItalyVerifiedProductCard):
                raise SecondLiveRuntimeError("live_sender_invalid")
            # The fixed collector does its own exact product/market checks.
            card_identity = identity.require_product_search()
            card_report = {}
            current, commercial, _ = refresh_card_binding(previous, account, card_identity, headers, card_report,
                maintenance_due=maintenance, stopped=lambda: closed or stopped())
            check()
            report.setdefault("cardRefreshes", []).append({"bindingSha256": current.binding_sha256,
                "evidenceSha256": current.evidence_sha256, "verifiedAt": current.verified_at,
                "commercialFacts": commercial, "readRequests": card_report.get("requests", [])})
            return current
        try:
            with ItalyImReadSession(auth, report, maintenance_due=maintenance,
                    stopped=lambda: closed or stopped(), use_environment_proxy=True) as reads:
                adapter = ItalyImDeliveryAdapter(auth, reads)
                check()
                yield {"adapter": adapter, "reads": reads, "write_gate": write_gate,
                       "validate_card": validate_card, "senderBindingHash": binding}
        finally:
            closed = True
