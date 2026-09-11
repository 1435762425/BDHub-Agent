#!/usr/bin/env python3
"""只读导出固定意大利画像批次的最小分析字段，不访问远端或输出连接信息。

使用旧项目现有 Python 环境运行；输出只能位于本新项目的 var 目录。
类目权重保留原文及未知 -1 项，不声称是 GMV、订单或内容份额。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import yaml
from sqlalchemy import bindparam, create_engine, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.pool import NullPool


PROJECT_ROOT = Path(__file__).resolve().parents[1]
COHORT_PATH = Path("data/research/outreach-ai/runs/oa_f9a4f4af84f3142fc119664a82de4be5/creator-snapshot.json")
COHORT_SHA256 = "d86c929587a712c7192b0e952bf855f6bab939bad7f1ca6a630e2dfaff08551d"

QUERY = """
SELECT snapshot_id, oec_id, captured_at, followers, units_sold, avg_view,
       raw_json -> 'med_gmv_revenue' AS money,
       raw_json -> 'industry_groups' AS categories,
       raw_json -> 'selection_region' AS region,
       raw_json -> 'sales_performance_end_time' AS performance_end
FROM creator_profile_snapshot
WHERE bd_market = 'it' AND quality_status = 'accepted'
  AND oec_id IN :oec_ids
  AND captured_at >= :cohort_start AND captured_at <= :cohort_end
ORDER BY oec_id, captured_at, snapshot_id
"""


def canonical_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def unwrap(value: object) -> object:
    if not isinstance(value, dict) or value.get("is_authorized") is not True or value.get("status") != 0:
        raise ValueError("源字段没有明确授权值")
    return value.get("value")


def nonnegative_count(value: object) -> int | None:
    if value is None:
        return None
    number = Decimal(str(value))
    if not number.is_finite() or number < 0 or number != number.to_integral() or number > 2**53 - 1:
        raise ValueError("源计数字段异常")
    return int(number)


def db_url(config_path: Path):
    # 只读取 db 段，不导入旧业务模块，不复制凭证到导出文件或日志。
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    db = config.get("db", {})
    if db.get("url"):
        url = make_url(db["url"])
    else:
        url = URL.create("postgresql+psycopg", username=db.get("user", "bdhub"),
                         password=str(db.get("password", "")), host=db.get("host", "127.0.0.1"),
                         port=int(db.get("port", 5432)), database=db.get("dbname", "bdhub"))
    if url.get_backend_name() != "postgresql" or url.host not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("此导出仅允许本机 PostgreSQL")
    return url


def export(legacy_root: Path, config_path: Path, output: Path) -> dict:
    if not output.is_relative_to(PROJECT_ROOT / "var"):
        raise ValueError("输出必须在新项目 var 目录")
    cohort_bytes = (legacy_root / COHORT_PATH).read_bytes()
    if hashlib.sha256(cohort_bytes).hexdigest() != COHORT_SHA256:
        raise ValueError("固定源批次内容已改变，需先复核")
    cohort = json.loads(cohort_bytes)
    expected = {row["oec_id"]: row for row in cohort["creators"]}
    if cohort["market"] != "it" or len(expected) != 3978 or len(cohort["creators"]) != 3978:
        raise ValueError("固定批次身份数量异常")
    cohort_times = [datetime.fromisoformat(row["captured_at"].replace("Z", "+00:00")) for row in expected.values()]
    engine = create_engine(db_url(config_path), hide_parameters=True, poolclass=NullPool,
                           connect_args={"connect_timeout": 5,
                                         "options": "-c default_transaction_read_only=on -c statement_timeout=15000"})
    try:
        with engine.connect() as connection:
            if connection.execute(text("SHOW transaction_read_only")).scalar() != "on":
                raise ValueError("数据库连接未确认只读")
            rows = list(connection.execute(text(QUERY).bindparams(bindparam("oec_ids", expanding=True)),
                                           {"oec_ids": sorted(expected), "cohort_start": min(cohort_times),
                                            "cohort_end": max(cohort_times)}).mappings())
    finally:
        engine.dispose()
    if len(rows) != len(expected) or {row["oec_id"] for row in rows} != set(expected):
        raise ValueError("每个固定 OEC 必须仅有一份 accepted 观测，不能默选其他版本")

    records = []
    end_times: Counter[str] = Counter()
    symbols: Counter[str] = Counter()
    for row in rows:
        baseline = expected[row["oec_id"]]
        baseline_time = datetime.fromisoformat(baseline["captured_at"].replace("Z", "+00:00"))
        if row["captured_at"] != baseline_time:
            raise ValueError("观测时间不对应固定源批次")
        if unwrap(row["region"]) != "IT":
            raise ValueError("原始画像市场不匹配")
        money = unwrap(row["money"])
        if not isinstance(money, dict) or money.get("symbol") != "€":
            raise ValueError("GMV 币种不能从原始符号确证")
        gmv = money.get("value")
        if not isinstance(gmv, str) or not Decimal(gmv).is_finite() or Decimal(gmv) < 0:
            raise ValueError("GMV 原始数值异常")
        if Decimal(gmv) != Decimal(baseline["gmv_value"]):
            raise ValueError("GMV 与固定源批次不一致")
        categories = unwrap(row["categories"])
        if not isinstance(categories, list) or not categories:
            raise ValueError("类目权重缺失")
        exported_categories = []
        seen = set()
        for category in categories:
            category_id, weight = category.get("key"), category.get("value")
            if not isinstance(category_id, str) or category_id in seen or not isinstance(weight, str):
                raise ValueError("类目身份或权重异常")
            value = Decimal(weight)
            if not value.is_finite() or value < 0 or value > 1:
                raise ValueError("来源类目权重超出 0–1")
            seen.add(category_id)
            exported_categories.append({"name": category.get("name"), "weight": weight, "categoryId": category_id})
        if abs(sum(Decimal(item["weight"]) for item in exported_categories) - 1) > Decimal("0.0002"):
            raise ValueError("类目权重总和异常；不会自动归一化")
        symbols[money["symbol"]] += 1
        end_times[str(unwrap(row["performance_end"]))] += 1
        records.append({"oecId": row["oec_id"], "capturedAt": row["captured_at"].isoformat(),
                        "rawSnapshotId": row["snapshot_id"], "followers": nonnegative_count(row["followers"]),
                        "unitsSold": nonnegative_count(row["units_sold"]), "avgViews": nonnegative_count(row["avg_view"]),
                        "gmv": {"value": gmv, "symbol": "€", "currency": "EUR", "period": None},
                        "categories": exported_categories})

    document = {"schemaVersion": 1, "market": "it", "records": records,
                "provenance": {"extractedAt": datetime.now(timezone.utc).isoformat(),
                               "sourceTable": "creator_profile_snapshot", "transactionReadOnly": True,
                               "querySummary": "accepted IT snapshots restricted to exact 3978 OEC cohort; explicit field whitelist; one snapshot per OEC",
                               "cohortSha256": COHORT_SHA256, "recordCount": len(records),
                               "recordsSha256": hashlib.sha256(canonical_bytes(records)).hexdigest(),
                               "recordsHashEncoding": "UTF-8 JSON ensure_ascii=False sort_keys=True separators=(',',':')",
                               "gmvSymbols": dict(symbols), "salesPerformanceEndTimeUnixSeconds": dict(end_times),
                               "metricWindow": "Start unavailable; shared endpoint end time does not establish a duration.",
                               "categoryWeightMeaning": "Source industry_groups weight; no assertion about GMV, sales or content share. Unknown category -1 is preserved."}}
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(output)
    return {"records": len(records), "categoryEdges": sum(len(row["categories"]) for row in records),
            "recordsSha256": document["provenance"]["recordsSha256"], "output": str(output)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--legacy-root", type=Path, default=Path("/Users/bjn00003/BDHub/01-BDSystem-V2"))
    parser.add_argument("--config", type=Path, help="仅读取此 config.yaml 的 db 段")
    parser.add_argument("--output", type=Path, default=PROJECT_ROOT / "var/italy-profile-signals-20260912.json")
    arguments = parser.parse_args()
    root = arguments.legacy_root.resolve()
    try:
        result = export(root, (arguments.config or root / "config.yaml").resolve(), arguments.output.resolve())
    except Exception as error:
        # DB 异常可能携带连接参数；只报告异常类型，不打印异常对象或堆栈。
        print(f"导出失败（{type(error).__name__}）；请检查只读连接、源批次和字段契约。", file=sys.stderr)
        raise SystemExit(1) from None
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
