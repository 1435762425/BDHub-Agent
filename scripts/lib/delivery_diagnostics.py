"""Read-only, aggregate delivery throughput and recorded timing evidence."""
from collections import Counter, defaultdict
from contextlib import closing
import json
import math
from pathlib import Path
import sqlite3
import time


def distribution(values):
    ordered = sorted(values)
    if not ordered:
        return {"samples": 0, "median": None, "p90": None, "maximum": None}
    def percentile(fraction):
        position = (len(ordered) - 1) * fraction
        lo, hi = math.floor(position), math.ceil(position)
        return round(ordered[lo] + (ordered[hi] - ordered[lo]) * (position - lo), 3)
    return {"samples": len(ordered), "median": percentile(.5), "p90": percentile(.9),
            "maximum": round(ordered[-1], 3)}


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _throughput(rows, since):
    rows = [row for row in rows if row["started"] >= since]
    minutes = Counter(int(row["started"] // 60) for row in rows)
    timestamps = [row["started"] for row in rows]
    elapsed = max(timestamps) - min(timestamps) if len(timestamps) > 1 else 0
    return {"completedDeliveries": len(rows),
            "uniqueCreators": len({row["creator"] for row in rows}),
            "firstStartedAt": min(timestamps) if timestamps else None,
            "lastStartedAt": max(timestamps) if timestamps else None,
            "observedSpanMinutes": round(elapsed / 60, 3),
            "meanPerMinuteAcrossObservedSpan": round((len(rows) - 1) * 60 / elapsed, 3) if elapsed else None,
            "deliveriesPerOccupiedUtcMinute": distribution(list(minutes.values()))}


def diagnose(root, *, since=0, clock=time.time):
    """Only reads explicit production ledger/log paths; never imports a store initializer.

    Throughput uses confirmed full deliveries at their earliest component START time.
    Readback latency includes waiting/recovery and is not isolated HTTP request time.
    Output omits creator IDs, message bodies, request IDs and platform credentials.
    """
    if not _number(since) or since < 0:
        raise ValueError("delivery_diagnostics_since_invalid")
    root = Path(root)
    now = float(clock())
    path = root / "var/second-cycle.sqlite"
    deliveries, components, latencies = defaultdict(list), Counter(), defaultdict(list)
    missing_checks = Counter()
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=10)) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        checks = {}
        for row in db.execute("SELECT delivery_id,kind,checked,payload FROM cycle_delivery_check"):
            try:
                payload = json.loads(row["payload"])
            except (TypeError, ValueError):
                continue
            if isinstance(payload, dict) and payload.get("status") == "confirmed" and _number(row["checked"]):
                key = (row["delivery_id"], row["kind"])
                checks[key] = min(checks.get(key, row["checked"]), row["checked"])
        starts = {}
        query = """SELECT p.market,d.id,d.creator_id,d.state AS delivery_state,
                          x.kind,x.started,x.state AS component_state
                   FROM cycle_delivery d JOIN plan p ON p.id=d.plan_id
                   JOIN cycle_delivery_part x ON x.delivery_id=d.id"""
        for row in db.execute(query):
            started = row["started"]
            if row["component_state"] != "confirmed" or not _number(started):
                continue
            market = row["market"]
            if started >= since:
                components[market] += 1
                checked = checks.get((row["id"], row["kind"]))
                if checked is None or checked < started:
                    missing_checks[market] += 1
                else:
                    latencies[market].append((checked - started) * 1000)
            if row["delivery_state"] == "confirmed":
                key = (market, row["id"], row["creator_id"])
                starts[key] = min(starts.get(key, started), started)
        for (market, _, creator), started in starts.items():
            deliveries[market].append({"creator": creator, "started": started})
    timing, timing_sources, log_evidence = defaultdict(lambda: defaultdict(list)), set(), []
    for market in ("it", "br", "my", "uk"):
        names = ["continuous-send.log"] if market == "it" else [f"market-send-worker-{market}.log", f"market-send-worker-{market}.json"]
        for name in names:
            log = root / "var" / name
            if not log.is_file():
                continue
            log_evidence.append({"path": "var/" + name, "bytes": log.stat().st_size})
            # State JSON is one document; log is newline-delimited JSON. Never emit raw content.
            with log.open(encoding="utf-8", errors="replace") as stream:
                documents = [stream.read()] if log.suffix == ".json" else stream
                for line in documents:
                    try:
                        document = json.loads(line)
                    except ValueError:
                        continue
                    if not isinstance(document, dict):
                        continue
                    result = document.get("result", document)
                    if not isinstance(result, dict):
                        continue
                    values = result.get("timingMs")
                    observed = document.get("checkedAt", document.get("seenAt"))
                    if not isinstance(values, dict) or not _number(observed) or observed < since:
                        continue
                    if result.get("market", market) != market:
                        continue
                    identity = (market, result.get("requestId"), observed)
                    if identity in timing_sources:
                        continue
                    timing_sources.add(identity)
                    for stage in ("preAuth", "auth", "initial", "candidate", "conversation", "preflight", "card", "text"):
                        value = values.get(stage)
                        if _number(value) and value >= 0:
                            timing[market][stage].append(value)
    markets = {}
    for market in sorted(set(deliveries) | {"it", "br", "my", "uk"}):
        markets[market] = {"throughput": _throughput(deliveries[market], since),
                           "last24Hours": _throughput(deliveries[market], max(since, now - 86400)),
                           "confirmedComponents": components[market],
                           "missingOrInvalidConfirmedCheckTime": missing_checks[market],
                           "componentStartToFirstConfirmedReadbackMs": distribution(latencies[market]),
                           "recordedStageTimingMs": {key: distribution(values) for key, values in timing[market].items()}}
    return {"schema": "bdhub.delivery-diagnostics.v1", "generatedAt": now, "since": since,
            "readOnly": True, "platformCalls": 0, "markets": markets, "logEvidence": log_evidence,
            "limitations": ["Throughput counts confirmed full deliveries by first component start time; includes all historical plans.",
                            "Occupied UTC minute counts omit idle minutes and are not sustained capacity; first/last buckets can be partial.",
                            "Readback duration includes dispatch, waiting and recovery; it does not isolate authentication or HTTP latency.",
                            "Stage timing reflects only retained structured records; missing timing cannot establish an authentication bottleneck.",
                            "Card/text stage timings combine send and readback; no isolated readback network duration is stored."]}
