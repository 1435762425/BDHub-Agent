#!/usr/bin/env python3
"""从固定意大利旧快照导出补全字段；只读本机数据库，不访问平台。

输出仅为新项目 var/italy-profile-field-completion-20260912.json。
每个目标必须精确对应原 3,978 人名单及其 captured_at，不补入其他版本。
原快照字段按白名单 SELECT，随即过滤媒体 URL、联系方式及其他无关内容。
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
LIB_PATH = PROJECT_ROOT / "scripts/lib/profile_completion.py"
_SPEC = importlib.util.spec_from_file_location("profile_completion", LIB_PATH)
_COMPLETION = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_COMPLETION)
PROFILE_FIELDS = _COMPLETION.PROFILE_FIELDS
summarize_profile = _COMPLETION.summarize_profile

COHORT_PATH = Path("data/research/outreach-ai/runs/oa_f9a4f4af84f3142fc119664a82de4be5/creator-snapshot.json")
COHORT_SHA256 = "d86c929587a712c7192b0e952bf855f6bab939bad7f1ca6a630e2dfaff08551d"
OUTPUT_PATH = PROJECT_ROOT / "var/italy-profile-field-completion-20260912.json"
STATES = ("absent", "no_value", "unauthorized", "error", "zero", "value")
AVAILABLE = frozenset({"zero", "value"})


def canonical_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def digest(value):
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def timestamp(value):
    result = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("源观测时间缺少时区")
    return result.astimezone(timezone.utc)


def load_cohort(legacy_root):
    payload = (legacy_root / COHORT_PATH).read_bytes()
    if hashlib.sha256(payload).hexdigest() != COHORT_SHA256:
        raise ValueError("固定源批次内容已改变，需先复核")
    cohort = json.loads(payload)
    expected = {str(row["oec_id"]): row for row in cohort["creators"]}
    if cohort.get("market") != "it" or len(expected) != 3978 or len(cohort["creators"]) != 3978:
        raise ValueError("固定意大利批次应为3,978个唯一OEC")
    return expected


def profile_select_sql():
    # All SQL identifiers come from the module's fixed, code-owned whitelist.
    # Presence columns preserve absent versus explicit JSON null faithfully.
    columns = ["snapshot_id", "oec_id", "captured_at"]
    for key in PROFILE_FIELDS:
        if not key.replace("_", "").isalnum():
            raise ValueError("画像字段白名单格式异常")
        columns.extend([f"raw_json -> '{key}' AS {key}",
                        f"raw_json::jsonb ? '{key}' AS {key}_present"])
    return ("SELECT " + ",\n       ".join(columns) + "\n"
            "FROM creator_profile_snapshot\n"
            "WHERE bd_market = 'it' AND quality_status = 'accepted'\n"
            "  AND oec_id IN :oec_ids\n"
            "  AND captured_at >= :cohort_start AND captured_at <= :cohort_end\n"
            "ORDER BY oec_id, captured_at, snapshot_id")


def read_profiles(config_path, expected):
    # Lazy dependencies allow pure validation tests to run in system Python.
    from sqlalchemy import bindparam, create_engine, text
    from sqlalchemy.pool import NullPool

    helper_path = PROJECT_ROOT / "scripts/export-italy-profile-signals.py"
    spec = importlib.util.spec_from_file_location("existing_profile_export", helper_path)
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    times = [timestamp(row["captured_at"]) for row in expected.values()]
    engine = create_engine(helper.db_url(config_path), hide_parameters=True, poolclass=NullPool,
                           isolation_level="REPEATABLE READ", connect_args={"connect_timeout": 5,
                           "options": "-c default_transaction_read_only=on -c statement_timeout=15000"})
    try:
        with engine.connect() as connection:
            if connection.execute(text("SHOW transaction_read_only")).scalar() != "on":
                raise ValueError("数据库连接未确认只读")
            if connection.execute(text("SHOW transaction_isolation")).scalar() != "repeatable read":
                raise ValueError("数据库读取隔离级别异常")
            query = text(profile_select_sql()).bindparams(bindparam("oec_ids", expanding=True))
            rows = list(connection.execute(query, {"oec_ids": sorted(expected),
                        "cohort_start": min(times), "cohort_end": max(times)}).mappings())
    finally:
        engine.dispose()
    # Exclude absent raw keys instead of converting them to explicit null.
    return [{"snapshot_id": row["snapshot_id"], "oec_id": str(row["oec_id"]),
             "captured_at": row["captured_at"],
             "profile": {key: row[key] for key in PROFILE_FIELDS if row[f"{key}_present"]}}
            for row in rows]


def build_document(expected, rows):
    if len(rows) != len(expected) or {row["oec_id"] for row in rows} != set(expected):
        raise ValueError("每个固定OEC必须精确对应一份快照，不能补入其他人或版本")
    states = {key: Counter({status: 0 for status in STATES}) for key in PROFILE_FIELDS}
    records = []
    for row in sorted(rows, key=lambda item: item["oec_id"]):
        source = expected[row["oec_id"]]
        if timestamp(row["captured_at"]) != timestamp(source["captured_at"]):
            raise ValueError("源快照观测时间不对应固定批次")
        summary = summarize_profile(row["profile"])
        identity = summary["identity"]
        if identity["oecId"] != row["oec_id"] or identity["market"] != "it":
            raise ValueError("源快照OEC或市场不对应固定批次")
        if identity["handle"] != source["handle"]:
            raise ValueError("源快照handle不对应固定批次")
        fields = summary["fields"]
        for key in PROFILE_FIELDS:
            states[key][fields[key]["status"]] += 1
        def available(key):
            return fields[key]["status"] in AVAILABLE
        brands = fields["partnered_brand"].get("value", {}).get("brands", [])
        coverage = {"availableFieldCount": len(summary["availableFields"]), "fieldCount": len(PROFILE_FIELDS),
                    "contentGroupsAvailable": available("content_groups"),
                    "topVideoCountAvailable": available("top_video_data"),
                    "brandsFieldAvailable": available("partnered_brand"), "brandsAvailable": bool(brands)}
        records.append({"oecId": row["oec_id"], "sourceSnapshotId": row["snapshot_id"],
                        "capturedAt": timestamp(row["captured_at"]).isoformat(),
                        "source": "existing_local_snapshot", "summary": summary, "coverage": coverage})
    coverage = {"recordCount": len(records), "fieldCount": len(PROFILE_FIELDS),
                "fieldStates": {key: dict(states[key]) for key in PROFILE_FIELDS},
                **{key: sum(record["coverage"][key] for record in records)
                   for key in ("contentGroupsAvailable", "topVideoCountAvailable", "brandsFieldAvailable", "brandsAvailable")}}
    return {"schemaVersion": 1, "market": "it", "source": "existing_local_snapshot",
            "liveProfileRefresh": False, "platformRequests": 0, "legacyDatabaseWrites": 0,
            "records": records, "coverage": coverage,
            "provenance": {"extractedAt": datetime.now(timezone.utc).isoformat(),
                           "sourceTable": "creator_profile_snapshot", "transactionReadOnly": True,
                           "transactionIsolation": "repeatable_read", "sourceCohortPath": str(COHORT_PATH),
                           "sourceCohortSha256": COHORT_SHA256, "fixedCohortCount": len(expected),
                           "sourceFields": list(PROFILE_FIELDS), "recordsSha256": digest(records),
                           "filterModuleSha256": hashlib.sha256(LIB_PATH.read_bytes()).hexdigest(),
                           "recordsHashEncoding": "UTF-8 JSON ensure_ascii=False sort_keys=True separators=(',',':')",
                           "interpretation": "7月30日已有本地快照的字段整理，不是实时抓取；未推断金额币种、统计窗口、内容类目权重含义。",
                           "privacy": "字段白名单；不输出bio/email/头像/联系方式/媒体URL/视频正文/token。top_video_data只留数量和结构键。"}}


def export(legacy_root, config_path, output):
    if output.resolve() != OUTPUT_PATH.resolve():
        raise ValueError("此导出仅允许指定新项目var文件")
    expected = load_cohort(legacy_root)
    rows = read_profiles(config_path, expected)
    document = build_document(expected, rows)
    # Source readback is independent of the SQL query and must remain exact.
    load_cohort(legacy_root)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".json.tmp")
    try:
        temporary.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        readback = json.loads(temporary.read_text(encoding="utf-8"))
        if digest(readback["records"]) != document["provenance"]["recordsSha256"]:
            raise ValueError("输出回读摘要不匹配")
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    return {"output": str(output), "records": len(document["records"]), "coverage": document["coverage"],
            "recordsSha256": document["provenance"]["recordsSha256"], "sourceCohortSha256": COHORT_SHA256,
            "platformRequests": 0, "legacyDatabaseWrites": 0, "source": "existing_local_snapshot"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--legacy-root", type=Path, default=Path("/Users/bjn00003/BDHub/01-BDSystem-V2"))
    parser.add_argument("--config", type=Path)
    parser.add_argument("--output", type=Path, default=OUTPUT_PATH)
    args = parser.parse_args()
    root = args.legacy_root.resolve()
    try:
        result = export(root, (args.config or root / "config.yaml").resolve(), args.output.resolve())
    except Exception as error:
        # Avoid SQL parameters, raw response data or connection information.
        print(json.dumps({"status": "failed", "errorType": type(error).__name__}), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
