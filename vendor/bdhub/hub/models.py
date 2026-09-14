# -*- coding: utf-8 -*-
"""达人画像规范模型 + 从 TikTok find 返回项解析。

find 返回的每个达人项里，字段大多是 {"value":..., "is_authorized":..} 包装；
money 类（GMV/GPM）是双层 {"value":{"value":"123","format":"$1.2K"}}；
分布类（性别/年龄/地区/类目）是 {"value":[{"key","value"},...]}。
本模块把这套结构压平成一张干净的 CreatorProfile。

（原属 bdhub/model.py，2026-07-06 迁移至 bdhub/hub/models.py，逻辑逐字不变；
model.py 留兼容 shim 转发本模块。）
"""
from __future__ import annotations
from dataclasses import dataclass, field
import json
import re

OUR_MCN = "BJN"   # 我方机构名（绑定 MCN 名称匹配它即"已绑我方"）

# 落库/导出的规范列顺序
FIELDS = [
    "handle", "oec_id", "nickname", "market", "followers", "main_category",
    "bind_mcn", "is_our_mcn", "commission_rate",
    "gmv_display", "gmv_value", "video_gmv", "live_gmv", "units_sold", "gpm", "live_gpm", "video_gpm", "price_range",
    "video_cnt_30d", "ec_video_cnt_30d", "live_cnt_30d", "avg_view", "female_pct", "top_age", "top_region",
    "contact_available", "is_fast_growing", "is_active", "is_quickly_response",
    "is_high_sample_dispatch", "brands", "labels", "bio", "email", "avatar_url",
]


def _email_from(text: str) -> str:
    if not text:
        return ""
    m = re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", str(text))
    return m.group(0) if m else ""


# ---------- 解包小工具 ----------
def _u(x):
    """剥一层 {value:...} 包装。"""
    if isinstance(x, dict) and "value" in x:
        return x["value"]
    return x


def _s(x):
    """标量字符串：剥包装；若仍是 dict（多为"有壳无值"=未授权字段）视为空。"""
    v = _u(x)
    if isinstance(v, dict):
        return ""
    return "" if v is None else str(v)


def _optional_flag(x) -> bool | None:
    """只接受接口明确给出的布尔值；授权元数据不能冒充 ``False``。"""
    value = _u(x)
    return value if isinstance(value, bool) else None


def _num(x):
    try:
        return float(str(x).replace(",", "").strip())
    except Exception:
        return None


def _money_display(item, key):
    v = _u(item.get(key))
    if isinstance(v, dict):
        return v.get("format") or v.get("value") or ""
    return v if v is not None else ""


def _money_value(item, key):
    v = _u(item.get(key))
    if isinstance(v, dict):
        return _num(v.get("value"))
    return _num(v)


def _range_money_value(item, key):
    """解析 Profile [2] 的区间金额包装。

    ``ec_live_gpm`` / ``ec_video_gpm`` 使用 ``minimal`` / ``maximum``
    包装；当前接口返回的两端相同，等价于一个精确数值。若未来返回真正
    的区间，则不伪造单点数值，交给上层按缺失值处理，同时保留 raw 原文。
    """
    v = _u(item.get(key))
    if isinstance(v, dict):
        nested = v.get("value")
        if isinstance(nested, dict):
            v = nested
        if isinstance(v, dict):
            minimum = _num(v.get("minimal"))
            maximum = _num(v.get("maximum"))
            if minimum is not None and maximum is not None:
                return minimum if minimum == maximum else None
            return _num(v.get("value"))
    return _num(v)


def _list(item, key):
    v = _u(item.get(key))
    return v if isinstance(v, list) else []


def _ratio_of(item, key, want_key):
    for it in _list(item, key):
        if isinstance(it, dict) and str(it.get("key", "")).lower() == want_key:
            return _num(it.get("value"))
    return None


def _names(item, key):
    out = []
    for it in _list(item, key):
        if isinstance(it, dict):
            n = it.get("name") or it.get("key") or it.get("id")
            if n:
                out.append(str(n))
        elif it:
            out.append(str(it))
    return " | ".join(out)


def _brands(item):
    """partnered_brand.brand[{id,name}] 取 name。API 对部分品牌只给 id 不给 name → 只保留有名字的(ID跳过)。"""
    pb = item.get("partnered_brand")
    if not isinstance(pb, dict):
        return ""
    lst = pb.get("brand")
    if not isinstance(lst, list):
        return ""
    return " | ".join(str(b["name"]) for b in lst if isinstance(b, dict) and b.get("name"))


def _main_cat(item):
    mi = _u(item.get("main_industry"))
    if isinstance(mi, str) and mi:
        return mi
    groups = _list(item, "industry_groups")
    if groups and isinstance(groups[0], dict):
        return str(groups[0].get("name") or groups[0].get("key") or "")
    return ""


def _top_region(item):
    locs = _list(item, "follower_state_location")
    if locs and isinstance(locs[0], dict):
        return str(locs[0].get("key", ""))
    return ""


def _top_age(item):
    v = _u(item.get("top_follower_age"))
    if isinstance(v, list) and v and isinstance(v[0], dict):
        return str(v[0].get("key", ""))
    if isinstance(v, str) and v:
        return v
    ages = _u(item.get("top_follower_ages"))
    if isinstance(ages, list) and ages:
        return str(ages[0])
    return ""


def _avatar(item):
    v = _u(item.get("avatar"))
    if isinstance(v, str):
        return v
    if isinstance(v, dict):
        if isinstance(v.get("url"), str):
            return v["url"]
        ul = v.get("url_list")
        if isinstance(ul, list) and ul:
            return str(ul[0])
    return ""


@dataclass(frozen=True)
class CreatorProfile:
    handle: str = ""
    oec_id: str = ""
    nickname: str = ""
    market: str = ""
    followers: float | None = None
    main_category: str = ""
    bind_mcn: str = ""
    is_our_mcn: bool = False
    commission_rate: str = ""
    gmv_display: str = ""
    gmv_value: float | None = None
    video_gmv: float | None = None
    live_gmv: float | None = None
    units_sold: float | None = None
    gpm: float | None = None
    live_gpm: float | None = None
    video_gpm: float | None = None
    price_range: str = ""
    video_cnt_30d: float | None = None
    ec_video_cnt_30d: float | None = None
    live_cnt_30d: float | None = None
    avg_view: float | None = None
    female_pct: float | None = None
    top_age: str = ""
    top_region: str = ""
    # 平台画像提示，不是联系方式抓取真值。未明确返回布尔值时必须为 None。
    contact_available: bool | None = None
    is_fast_growing: bool = False
    is_active: bool = False
    is_quickly_response: bool = False
    is_high_sample_dispatch: bool = False
    brands: str = ""
    labels: str = ""
    bio: str = ""
    email: str = ""
    avatar_url: str = ""
    raw: dict = field(default_factory=dict, repr=False)

    def to_row(self) -> dict:
        row = {k: getattr(self, k) for k in FIELDS}
        row["raw_json"] = json.dumps(self.raw, ensure_ascii=False)
        return row


def parse_find_item(item: dict) -> CreatorProfile:
    def flag(key):
        v = _u(item.get(key))
        # "有壳无值"(未授权字段, 仍是 dict) → 视为未知=False，避免误判为 True
        return bool(v) if not isinstance(v, dict) else False

    bind = _s(item.get("creator_bind_mcn_name"))
    return CreatorProfile(
        handle=_s(item.get("handle")),
        oec_id=_s(item.get("creator_oecuid")),
        nickname=_s(item.get("nickname")),
        market=_s(item.get("selection_region")),
        followers=_num(_u(item.get("follower_cnt"))),
        main_category=_main_cat(item),
        bind_mcn=bind,
        is_our_mcn=(bind == OUR_MCN),
        commission_rate=_s(item.get("med_commission_rate_range")),
        gmv_display=_money_display(item, "med_gmv_revenue"),
        gmv_value=_money_value(item, "med_gmv_revenue"),
        video_gmv=_money_value(item, "video_gmv"),   # 存精确值(非$xxK显示)
        live_gmv=_money_value(item, "live_gmv"),
        units_sold=_num(_u(item.get("units_sold"))),
        gpm=_money_value(item, "gpm"),
        live_gpm=_range_money_value(item, "ec_live_gpm"),
        video_gpm=_range_money_value(item, "ec_video_gpm"),
        price_range=_s(item.get("product_price_range")),
        video_cnt_30d=_num(_u(item.get("video_publish_cnt_30d"))),
        ec_video_cnt_30d=_num(_u(item.get("ec_video_publish_cnt_30d"))),
        live_cnt_30d=_num(_u(item.get("live_streaming_cnt_30d"))),
        avg_view=_num(_u(item.get("video_avg_view_cnt"))),
        female_pct=_ratio_of(item, "follower_genders_v2", "female"),
        top_age=_top_age(item),
        top_region=_top_region(item),
        contact_available=_optional_flag(item.get("contact_info_available")),
        is_fast_growing=flag("is_fast_growing"),
        is_active=flag("is_active_creator"),
        is_quickly_response=flag("is_quickly_response"),
        is_high_sample_dispatch=flag("is_high_sample_dispatch_rate"),
        brands=_brands(item),
        labels=_names(item, "creator_labels") or _names(item, "sorted_creator_labels"),
        bio=_s(item.get("bio")),
        email=_email_from(_s(item.get("bio"))),
        avatar_url=_avatar(item),
        raw=item,
    )


__all__ = [
    "OUR_MCN",
    "FIELDS",
    "CreatorProfile",
    "parse_find_item",
]
