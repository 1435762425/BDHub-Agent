#!/usr/bin/env python3
"""只读导出固定意大利二发样本的准备事实；不会联网、刷新或发送。"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import yaml
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.pool import NullPool


PROJECT_ROOT = Path(__file__).resolve().parents[1]
LEGACY_ROOT = Path("/Users/bjn00003/BDHub/01-BDSystem-V2")
RUN = "kd_d1fae274877443e3b76f13256055b657"
POOL = "cp_4d99ed911758464aaea4b7b2649260bb"
CARD = "tl_4021fd4e9a4748aba709809526d1f04c"
PIDS = {
    "1729779362302171335", "1729480019490150432", "1729502070782139035",
    "1729584712875612996", "1729480472768911992",
}
PINNED_SOURCES = {
    f"data/research/kalodata/{RUN}/request.json": "e87e17fce7d5727d80ff22d2d2d7cde6b0ad30687d848da8bb4bc76092cdab72",
    f"data/research/kalodata/{RUN}/result.json": "c79ea4f1913bc313e794ee82e359fa725854e744a4c1e949708e391557b6c1d8",
    f"data/research/commerce-pool/{POOL}.json": "e5c92fa476853662ba7712612f48ca903ef58be5e5c9622d65243d9b39070b67",
    f"data/send/taplinks/{CARD}.json": "390c1b2ba6899ae530a12fd7c795eb4e1695cffb3d064d4e6b413655a1ac0aa4",
}
COVERAGE_TABLES = (
    "im_creator_identity", "im_conversation", "im_message", "im_auto_reply",
    "creator_im_state", "creator_relationship", "creator_task", "im_delivery_intent",
    "send_task", "reply_outbox", "outreach", "im_listener_state",
)


def canonical_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("源时间缺少时区")
    return parsed


def db_url(config_path: Path):
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    db = config.get("db", {})
    url = make_url(db["url"]) if db.get("url") else URL.create(
        "postgresql+psycopg", username=db.get("user", "bdhub"),
        password=str(db.get("password", "")), host=db.get("host", "127.0.0.1"),
        port=int(db.get("port", 5432)), database=db.get("dbname", "bdhub"),
    )
    if url.get_backend_name() != "postgresql" or url.host not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("此导出仅允许本机 PostgreSQL")
    return url


def source_record(relative: str, content: bytes, **extra) -> dict:
    return {"ref": f"legacy:{relative}", "sha256": sha(content), **extra}


def read_sources(root: Path) -> tuple[dict, list[dict]]:
    data, provenance = {}, []
    for relative, expected in PINNED_SOURCES.items():
        content = (root / relative).read_bytes()
        if sha(content) != expected:
            raise ValueError("固定源快照已变化，需复核后更新指纹")
        data[relative] = json.loads(content)
        provenance.append(source_record(relative, content))
    return data, provenance


def market_capabilities(root: Path, sources: list[dict]) -> dict:
    relative = "bdhub/hub/markets.py"
    content = (root / relative).read_bytes()
    for node in ast.walk(ast.parse(content)):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name) or node.func.id != "Market":
            continue
        fields = {item.arg: item.value for item in node.keywords}
        if not isinstance(fields.get("key"), ast.Constant) or fields["key"].value != "it":
            continue
        call = fields.get("capabilities")
        if not isinstance(call, ast.Call):
            raise ValueError("意大利能力定义结构变化")
        statuses = {item.arg: ast.literal_eval(item.value) for item in call.keywords}
        sources.append(source_record(relative, content))
        return {"market": "it", "statuses": statuses, "sourceRef": f"legacy:{relative}#MARKETS.it",
                "sourceSha256": sha(content), "evidenceKind": "source_code_declaration",
                "platformRechecked": False, "liveEligible": False}
    raise ValueError("未找到意大利市场能力定义")


def identities_and_coverage(root: Path, handles: list[str], extracted_at: str) -> tuple[list[dict], dict]:
    engine = create_engine(db_url(root / "config.yaml"), hide_parameters=True, poolclass=NullPool,
                           isolation_level="REPEATABLE READ", connect_args={"connect_timeout": 5,
                           "options": "-c default_transaction_read_only=on -c statement_timeout=15000"})
    try:
        with engine.connect() as connection:
            if connection.execute(text("SHOW transaction_read_only")).scalar() != "on":
                raise ValueError("数据库连接未确认只读")
            # 只读取目标 handle 的身份引用，不读取原消息、联系人或任意 raw JSON。
            rows = [dict(row) for row in connection.execute(text("""
                SELECT a.handle_key, a.oec_id, a.valid_from, a.valid_to, a.confirmed_snapshot_id,
                       s.captured_at, s.quality_status, i.current_handle_key, i.last_seen_at
                FROM creator_handle_alias a
                JOIN creator_profile_snapshot s ON s.snapshot_id = a.confirmed_snapshot_id
                    AND s.bd_market = a.bd_market AND s.oec_id = a.oec_id
                JOIN creator_identity i ON i.bd_market = a.bd_market AND i.oec_id = a.oec_id
                WHERE a.bd_market = 'it' AND a.handle_key = ANY(:handles)
                ORDER BY a.handle_key, s.captured_at, a.oec_id
            """), {"handles": handles}).mappings()]
            counts = {table: connection.execute(text(
                f"SELECT count(*) FROM {table} WHERE bd_market = 'it'"
            )).scalar_one() for table in COVERAGE_TABLES}
            coverage = {"market": "it", "scope": "all_local_it_rows", "queriedAt": extracted_at,
                        "transactionReadOnly": True, "isolation": "repeatable_read",
                        "tables": [{"table": table, "rowCount": count,
                                    "sourceRef": f"legacy:postgresql:{table}?bd_market=it"}
                                   for table, count in counts.items()],
                        "interpretation": "本地收录覆盖率；无记录不证明未联系、未拒联、无人工控制或无在途发送。",
                        "privateMessageBodiesRead": False, "relationshipPermissionEstablished": False}
    finally:
        engine.dispose()
    for row in rows:
        for key, value in row.items():
            if isinstance(value, datetime):
                row[key] = value.isoformat()
    return rows, coverage


def export(root: Path, output: Path) -> dict:
    if root != LEGACY_ROOT or not output.is_relative_to(PROJECT_ROOT / "var"):
        raise ValueError("固定旧项目只读，输出必须位于新项目 var 目录")
    data, sources = read_sources(root)
    request = data[f"data/research/kalodata/{RUN}/request.json"]
    result = data[f"data/research/kalodata/{RUN}/result.json"]
    if request.get("query", {}).get("market") != "it" or result.get("state") != "completed":
        raise ValueError("固定样本市场或完成状态异常")
    rows = result["rows"]
    if len(rows) != 50 or {row["pid"] for row in rows} != PIDS:
        raise ValueError("固定样本不是 5 PID / 50 边")
    creators: dict[str, dict] = {}
    for row in rows:
        external_id, handle = row["kalodata_creator_id"], row["handle"]
        if not isinstance(external_id, str) or not external_id.isdigit():
            raise ValueError("来源身份必须保持数字字符串")
        if external_id in creators and creators[external_id]["handle"] != handle:
            raise ValueError("同一来源身份出现冲突 handle")
        creators[external_id] = {"externalId": external_id, "externalSource": "kalodata", "handle": handle}
    if len(creators) != 48:
        raise ValueError("固定样本必须有 48 位来源达人")
    extracted_at = datetime.now(timezone.utc).isoformat()
    hints, coverage = identities_and_coverage(root, sorted({c["handle"].lower().lstrip("@") for c in creators.values()}), extracted_at)
    by_handle: dict[str, list[dict]] = defaultdict(list)
    for hint in hints:
        by_handle[hint["handle_key"]].append(hint)
    records = []
    for external_id, creator in sorted(creators.items()):
        candidates = by_handle[creator["handle"].lower().lstrip("@")]
        oecs = {row["oec_id"] for row in candidates}
        hint_records = [{"localOecHint": row["oec_id"], "matchType": "historical_handle_match",
                         "observedAt": row["captured_at"], "qualityStatus": row["quality_status"],
                         "aliasActiveInLocalStore": row["valid_to"] is None,
                         "isLocalCurrentHandle": row["current_handle_key"] == row["handle_key"],
                         "aliasValidFrom": row["valid_from"], "aliasValidTo": row["valid_to"],
                         "sourceRef": f"legacy:postgresql:creator_profile_snapshot:{row['confirmed_snapshot_id']}"}
                        for row in candidates]
        records.append({**creator, "market": "it", "verifiedOec": None,
                        "localOecHint": next(iter(oecs)) if len(oecs) == 1 else None,
                        "matchType": "historical_handle_match" if len(oecs) == 1 else "ambiguous_handle_match" if oecs else "unresolved",
                        "identityHints": hint_records,
                        "source": {"ref": f"legacy:data/research/kalodata/{RUN}/result.json#kalodata_creator_id={external_id}",
                                   "observedAt": result["finished_at"]},
                        "relationshipFacts": {"marketingStopped": None, "humanControl": None,
                                              "inFlightOrUnknown": None, "previouslyContacted": None,
                                              "status": "unknown", "sourceCoverageRef": "sourceCoverage"},
                        "liveEligible": False})
    pool = data[f"data/research/commerce-pool/{POOL}.json"]
    if pool.get("market") != "it" or pool.get("account") != "acc6" or pool.get("state") != "completed":
        raise ValueError("固定货盘范围或状态异常")
    offers = []
    for pid in sorted(PIDS):
        product = pool["products"][pid]
        candidate_offers = product["offers"]
        candidate_offers = list(candidate_offers.values()) if isinstance(candidate_offers, dict) else candidate_offers
        if len(candidate_offers) != 1:
            raise ValueError("固定已选货盘每 PID 应只有一个完整 Offer，不能自行合并")
        original = candidate_offers[0]
        offer, raw = original["offer"], original["raw"]
        campaign, raw_product = raw["campaign_info"], raw["campaign_product"]
        if offer["pid"] != pid or offer["market"] != "it" or str(raw_product["product_id"]) != pid:
            raise ValueError("Offer 的 PID 或市场不一致")
        if str(campaign["campaign_id"]) != offer["campaign_id"]:
            raise ValueError("Offer 活动来源不一致")
        for key, raw_key in [("total_commission", "total_commission_percent"), ("public_commission", "plan_commission_percent")]:
            if Decimal(offer[key]) != Decimal(raw_product[raw_key]) / 100:
                raise ValueError("佣金展示与原始基点不一致")
        if str(offer["stock"]) != str(raw_product["stock"]):
            raise ValueError("库存不是同一 Offer 来源")
        observation = {"market": "it", "accountRef": pool["account"], "pid": pid,
                       "campaignId": offer["campaign_id"], "campaignType": offer["campaign_type"],
                       "productName": offer["product_name"],
                       "priceMin": offer["price_min"], "priceMax": offer["price_max"], "currency": offer["currency"],
                       "publicCommissionPercent": offer["public_commission"], "totalCommissionPercent": offer["total_commission"],
                       "creatorCommissionPercent": None, "agencyMarginPercent": None,
                       "stock": str(offer["stock"]), "sampleQuota": offer.get("sample_quota"),
                       "hasSampleHint": offer.get("has_sample"),
                       "platformAvailable": offer["platform_available"], "productStatus": offer["product_status"],
                       "campaignState": offer["campaign_state"], "selected": offer["selected"], "joined": offer["joined"],
                       "campaignStartUnixMs": str(campaign["promotion_start_time"]),
                       "campaignEndUnixMs": str(campaign["promotion_end_time"]), "displayEndDate": offer["end_date"],
                       "observedAt": offer["observed_at"],
                       "sourceRef": f"legacy:data/research/commerce-pool/{POOL}.json#products.{pid}.offers",
                       "currentValidity": "not_reverified", "liveEligible": False}
        observation["version"] = sha(canonical_bytes(observation))
        offers.append(observation)
    card_source = data[f"data/send/taplinks/{CARD}.json"]
    card = card_source["card"]
    if card_source["market"] != "it" or card_source["account"] != "acc6" or card["product_id"] not in PIDS:
        raise ValueError("固定历史卡片范围异常")
    if card["source_campaign_id"] != card_source["offer"]["campaign_id"] or card["product_id"] != card_source["offer"]["pid"]:
        raise ValueError("历史卡片和来源 Offer 绑定不一致")
    card_observation = {"market": "it", "accountRef": "acc6", "pid": card["product_id"],
                        "bindingRef": CARD, "listId": card["list_id"], "cardCampaignId": card["campaign_id"],
                        "sourceCampaignId": card["source_campaign_id"], "state": card_source["state"],
                        "bindingEvidence": card["binding_evidence"],
                        "creatorCommissionPercent": card_source["creator_commission"],
                        "agencyMarginPercent": card_source["agency_margin"],
                        "observedAt": card_source["verified_at"], "recordUpdatedAt": card_source["updated_at"],
                        "sourceRef": f"legacy:data/send/taplinks/{CARD}.json",
                        "currentValidity": "not_reverified", "liveEligible": False}
    card_observation["version"] = sha(canonical_bytes(card_observation))
    capabilities = market_capabilities(root, sources)
    document = {"schemaVersion": 1, "market": "it", "mode": "historical_readiness_observations",
                "extractedAt": extracted_at, "records": records, "offerObservations": offers,
                "cardObservations": [card_observation], "sourceCoverage": coverage,
                "marketCapabilityObservation": capabilities,
                "identitySemantics": "Kalodata 身份不并入 OEC。历史同 handle 只提示人工核验，未证明原 TikTok UID 精确连接。",
                "liveEligible": False, "platformRequests": 0, "realSends": 0,
                "provenance": {"sources": sources, "recordsSha256": sha(canonical_bytes(records)),
                               "offerObservationsSha256": sha(canonical_bytes(offers)),
                               "cardObservationsSha256": sha(canonical_bytes([card_observation])),
                               "sourceCoverageSha256": sha(canonical_bytes(coverage)),
                               "transactionReadOnly": True,
                               "hashEncoding": "UTF-8 JSON ensure_ascii=False sort_keys=True separators=(',',':')"}}
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(output)
    return {"creators": len(records), "historicalHandleHints": sum(r["matchType"] == "historical_handle_match" for r in records),
            "verifiedOec": 0, "historicalOfferObservations": len(offers), "historicalCardObservations": 1,
            "localRelationshipRows": sum(t["rowCount"] for t in coverage["tables"]),
            "liveEligible": False, "output": str(output), "sha256": sha(output.read_bytes())}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=PROJECT_ROOT / "var/italy-second-readiness-20260912.json")
    arguments = parser.parse_args()
    try:
        print(json.dumps(export(LEGACY_ROOT, arguments.output.resolve()), ensure_ascii=False))
    except Exception as error:
        # SQLAlchemy / URL 错误可能含连接细节；仅输出异常类型，不打印异常对象。
        print(f"意大利二发只读导出失败（{type(error).__name__}）；未写旧库、未调用平台。", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
