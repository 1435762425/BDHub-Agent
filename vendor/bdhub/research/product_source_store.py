"""推品商品源的批次与已选商品观测，不改写原 Campaign 快照。"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path

from bdhub import config
from bdhub.hub.file_intents import FileIntentStore, atomic_json, now


class SourceStore(FileIntentStore):
    def __init__(self, root: Path | None = None):
        super().__init__(root or config.ROOT / "data/research/product-sources", prefix="ps", error_prefix="source")

    def remember_selected(self, job, raw):
        path = self.root / "selected" / f"{job['market']}-{job['account']}.json"
        prior = json.loads(path.read_text()) if path.exists() else {"market": job["market"], "account": job["account"], "items": {}}
        key = str(raw["campaign_product"]["product_id"]) + ":" + str(raw["campaign_info"]["campaign_id"])
        prior["items"][key] = {"raw": raw, "observed_at": now(), "job_id": job["job_id"]}
        atomic_json(path, prior)

    def selected_snapshot(self, market, account=None):
        if market not in {"mx", "br"}:
            raise ValueError("source_market_invalid")
        index, versions, stamps = {}, [], []
        for path in sorted((self.root / "selected").glob(f"{market}-*.json")):
            data = json.loads(path.read_text())
            if account and data["account"] != account:
                continue
            for entry in data["items"].values():
                age = datetime.now(timezone.utc) - datetime.fromisoformat(entry["observed_at"])
                if not timedelta(0) <= age <= timedelta(hours=36):
                    continue
                raw = deepcopy(entry["raw"])
                raw["_source_pool"] = "selected"
                raw["_source_accounts"] = [data["account"]]
                raw["_source_observed_at"] = entry["observed_at"]
                pid = str(raw["campaign_product"]["product_id"])
                index.setdefault(pid, []).append(raw)
                stamps.append(entry["observed_at"])
                versions.append([data["account"], entry])
        if not index:
            return None
        version = hashlib.sha256(json.dumps(versions, sort_keys=True).encode()).hexdigest()[:24]
        return {"snapshot_id": "selected:" + version, "market": market, "generated_at": min(stamps), "index": index}


def merge_selected_snapshot(base, market, *, store=None):
    selected = (store or SourceStore()).selected_snapshot(market)
    if not selected:
        return base
    # 原快照超过期限时不借新导入的时间重新变成新鲜数据。
    base_fresh = base and timedelta(0) <= datetime.now(timezone.utc) - datetime.fromisoformat(base["generated_at"]) <= timedelta(hours=36)
    index = deepcopy(base["index"]) if base_fresh else {}
    for pid, rows in selected["index"].items():
        for raw in rows:
            campaign = str(raw["campaign_info"]["campaign_id"])
            match = next((r for r in index.get(pid, []) if str(r["campaign_info"]["campaign_id"]) == campaign), None)
            if match is None:
                index.setdefault(pid, []).append(raw)
            else:
                match["_source_accounts"] = sorted(set(match.get("_source_accounts", []) + raw["_source_accounts"]))
                match["_has_selected_source"] = True
    version = hashlib.sha256(f"{base['snapshot_id'] if base_fresh else ''}:{selected['snapshot_id']}".encode()).hexdigest()[:24]
    return {**(base or {}), "snapshot_id": "sources:" + version, "market": market, "index": index,
            "generated_at": min(base["generated_at"], selected["generated_at"]) if base_fresh else selected["generated_at"]}
