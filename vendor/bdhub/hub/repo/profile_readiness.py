"""批量画像完成/新鲜度事实；身份可解析不等于完整画像已完成。"""
from datetime import datetime, timezone

from sqlalchemy import exists, select

from bdhub.hub.profile_observation import required_profile_fields, _value_is_usable
from bdhub.hub.schema import creator_profile_current as current, creator_profile_snapshot as snapshot

PROFILE_REFRESH_ROUTES = (
    "find_profile126_browser", "find_profile126_pure_http", "oec_profile126_pure_http",
    "mcn_relation_browser_126", "known_oec_browser_126", "resolved_oec_browser_126",
)


def required_sources_are_fresh(field_sources, *, market="mx", cutoff=None):
    if not isinstance(field_sources, dict):
        return False
    for name in required_profile_fields(market) - {"handle", "oec_id"}:
        source = field_sources.get(name)
        if not isinstance(source, dict) or not str(source.get("snapshot_id") or "").strip():
            return False
        observed = source.get("observed_at")
        try:
            value = observed if isinstance(observed, datetime) else datetime.fromisoformat(str(observed or "").replace("Z", "+00:00"))
            value = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
        except (TypeError, ValueError):
            return False
        if cutoff is not None and value < cutoff:
            return False
    return True


def complete_profile_oecs(engine, market, oec_ids, *, cutoff=None):
    keys = list(dict.fromkeys(str(key) for key in oec_ids if key))
    required = sorted(required_profile_fields(market))
    qualified = set()
    if not keys:
        return qualified
    conditions = [snapshot.c.bd_market == market, snapshot.c.oec_id == current.c.oec_id,
                  snapshot.c.route.in_(PROFILE_REFRESH_ROUTES), snapshot.c.quality_status.in_(("accepted", "partial"))]
    if cutoff is not None:
        conditions.append(snapshot.c.captured_at >= cutoff)
    with engine.connect() as connection:
        for start in range(0, len(keys), 2000):
            rows = connection.execute(select(current.c.oec_id, current.c.field_sources, *(current.c[name] for name in required)).where(
                current.c.bd_market == market, current.c.oec_id.in_(keys[start:start + 2000]),
                exists(select(1).where(*conditions)),
            ))
            for row in rows:
                if profile_is_complete(dict(zip(required,row[2:])),row[1],market=market,cutoff=cutoff):
                    qualified.add(str(row[0]))
    return qualified


def profile_is_complete(values, field_sources, *, market="mx", cutoff=None):
    """预览、缓存、补全共用同一判断：值可用且每个必需字段都有可信新鲜来源。"""
    return required_sources_are_fresh(field_sources,market=market,cutoff=cutoff) and all(
        _value_is_usable(name,values.get(name)) for name in required_profile_fields(market))
