"""版本化类目初筛；不把规则排序称为 AI 个性化推荐或发送许可。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json

from bdhub.hub.product_categories import category_from_alias

RULE_VERSION = "category-shortlist-v1"


def json_value(value):
    return json.loads(json.dumps(value, default=str, ensure_ascii=False))


def fingerprint(value) -> str:
    return sha256(json.dumps(json_value(value), sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def product_index(products: list[dict]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for item in products:
        category = category_from_alias(item.get("canonical_category_l1"), item.get("category_l1"))
        if category:
            grouped.setdefault(category, []).append(item)
    for items in grouped.values():
        # 只按准备程度及 PID 稳定排序；没有凭空估计转化率/收益。
        items.sort(key=lambda row: (not bool(row.get("selection_eligible")), str(row["pid"])))
    return grouped


def match_creator(creator: dict, index: dict, *, now: datetime, profile_days: int) -> dict:
    category = category_from_alias(creator.get("main_category"))
    observed = creator.get("category_observed_at")
    try:
        at = datetime.fromisoformat(str(observed).replace("Z", "+00:00"))
        fresh = at.tzinfo is not None and timedelta(0) <= now - at <= timedelta(days=profile_days)
    except (TypeError, ValueError):
        fresh = False
    base = {"rule_version": RULE_VERSION, "category": category, "candidates": [],
            "channel_and_link_check": "pending", "personalized_ai_review": "pending"}
    if not category or not fresh:
        return dict(base, status="needs_profile", reason="达人类目缺失、无法对应或观测已过期，需要补资料。")
    pool = index.get(category, [])
    if not pool:
        return dict(base, status="no_match", reason="所选货盘暂未找到同类目商品；不代表达人没有合作价值。")
    eligible = [item for item in pool if item.get("selection_eligible")]
    ready = bool(eligible)
    pool = eligible or pool
    # 同类目内按稳定身份分散候选，避免整批达人固定拿到同三个 PID。
    # 这是初筛分布，不估计偏好或替代后续样品额度/沟通频次约束。
    start = int(fingerprint(creator.get("oec_id", ""))[:12], 16) % len(pool)
    candidates = [pool[(start+i) % len(pool)] for i in range(min(3, len(pool)))]
    return dict(base, status="matched" if ready else "needs_product",
                reason="带货类目一致，列为初筛候选；尚未核实内容风格、近期合作和沟通渠道。" if ready
                else "类目相符，但当前商品资格需要准备或核实。",
                candidates=candidates)
