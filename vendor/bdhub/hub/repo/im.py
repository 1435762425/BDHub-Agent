# -*- coding: utf-8 -*-
"""hub/repo/im.py —— IM 私信写库(原样搬自 im/monitor.py,只碰 DB;数据整形 _rollup 留在 monitor)。"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, case, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from ..schema import im_conversation, im_message
from .im_states import CreatorImStateRepo


def _now():
    return datetime.now(timezone.utc)


def _event_time(epoch_ms):
    value = int(epoch_ms or 0)
    if value <= 0:
        return None
    return datetime.fromtimestamp(value / 1000, tz=timezone.utc)


def _conv_set(ci, conv, live=False):
    """会话 upsert 的冲突更新映射(带守卫,Codex 审查修复):
    - creator_oec_id 用 COALESCE 保非空(别用 null 覆盖已有 join 键)
    - 时刻取 GREATEST(乱序不回退)；last_creator_reply 仅当新回复更晚才换文本
    - live 模式:每来一条达人回复,creator_reply_count +1(单条事件);
      BD Hub 已看状态由 creator_im_state 水位维护，不再写平台 unread_count
    - showcase:count 累加,at 取最近
    """
    ex = ci.excluded
    c = im_conversation.c
    g = lambda a, b: func.greatest(func.coalesce(a, 0), func.coalesce(b, 0))
    s = {}
    for k in conv:
        if k == "conversation_id":
            continue
        if k == "bd_market":
            continue    # 冲突时不改市场标签:保首写者的 bd_market,防跨市场同 id 误改(与 docstring 一致)
        if k == "creator_oec_id":
            s[k] = func.coalesce(ex.creator_oec_id, c.creator_oec_id)
        elif k == "last_active_ms":
            s[k] = g(c.last_active_ms, ex.last_active_ms)
        elif k == "last_mine_ms":
            s[k] = g(c.last_mine_ms, ex.last_mine_ms)
        elif k == "creator_reply_count":
            s[k] = (func.coalesce(c.creator_reply_count, 0) + 1) if live else g(c.creator_reply_count, ex.creator_reply_count)
        elif k == "last_creator_reply_ms":
            s[k] = g(c.last_creator_reply_ms, ex.last_creator_reply_ms)
        elif k == "last_creator_reply":
            # 仅【严格更晚】才换文本;同时刻或更早则保留旧(旧为空才用新),避免同 ts 空内容覆盖(Codex#6)
            s[k] = case((func.coalesce(ex.last_creator_reply_ms, 0) > func.coalesce(c.last_creator_reply_ms, 0),
                         ex.last_creator_reply),
                        else_=func.coalesce(func.nullif(c.last_creator_reply, ""), ex.last_creator_reply))
        elif k == "showcase_count":
            # live=单事件+1;pull(rollup)=窗口内计数,只能取大(窗口是最近N条,可能少于真实总数,绝不回退)
            s[k] = (func.coalesce(c.showcase_count, 0) + 1) if live else g(c.showcase_count, ex.showcase_count)
        elif k == "showcase_at":
            # 取大不回退(2026-07-20 cinmosqueda 实证:补捞落了新橱窗消息但列没跟上→展示陈旧)
            s[k] = func.greatest(func.coalesce(c.showcase_at, ex.showcase_at), ex.showcase_at)
        else:
            s[k] = ex[k]
    return s


def _msg_set(mi, keys):
    """消息 upsert 冲突更新:content/oec 用 COALESCE 保非空(重抓返空不覆盖好数据,Codex #9)。"""
    ex = mi.excluded
    c = im_message.c
    s = {}
    for k in keys:
        if k == "server_id":
            continue
        if k == "bd_market":
            continue    # 同 _conv_set:冲突时不改市场标签,防跨市场同 server_id 误改
        if k == "content":
            s[k] = func.coalesce(func.nullif(ex.content, ""), c.content)
        elif k in ("card_pid", "card_list"):
            s[k] = func.coalesce(func.nullif(ex[k], ""), c[k])
        elif k == "raw_json":
            s[k] = func.coalesce(ex.raw_json, c.raw_json)
        elif k == "creator_oec_id":
            s[k] = func.coalesce(ex.creator_oec_id, c.creator_oec_id)
        elif k in ("create_ms", "index_hi", "index_lo"):
            s[k] = func.greatest(func.coalesce(c[k], 0), func.coalesce(ex[k], 0))   # replay 空/0 不覆盖(Codex#7)
        elif k in ("message_type", "sender_role", "conversation_id"):
            s[k] = func.coalesce(ex[k], c[k])
        else:
            s[k] = ex[k]
    return s


def _uniform_message_rows(msg_rows: list[dict]) -> list[dict]:
    """补齐批量 INSERT 的可选列，避免商品卡/文本混批编译失败。"""
    keys: list[str] = []
    seen: set[str] = set()
    for row in msg_rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                keys.append(key)
    return [{key: row.get(key) for key in keys} for row in msg_rows]


def load_conversation_checkpoints(
    engine,
    conversation_ids,
    *,
    market="mx",
) -> dict[str, dict[str, int]]:
    """读取 resident monitor 的持久断点，重启后只补捞真正前进的会话。"""
    ids = list(dict.fromkeys(
        str(value or "").strip()
        for value in conversation_ids
        if str(value or "").strip()
    ))
    if not ids:
        return {}
    statement = select(
        im_conversation.c.conversation_id,
        im_conversation.c.last_active_ms,
        im_conversation.c.last_creator_reply_ms,
    ).where(
        im_conversation.c.bd_market == str(market or "").strip().lower(),
        im_conversation.c.conversation_id.in_(ids),
    )
    with engine.connect() as connection:
        rows = connection.execute(statement).mappings().all()
    return {
        str(row["conversation_id"]): {
            "last_active_ms": int(row["last_active_ms"] or 0),
            "last_creator_reply_ms": int(
                row["last_creator_reply_ms"] or 0
            ),
        }
        for row in rows
    }


def load_conversation_message_anchors(
    engine,
    conversation_ids,
    *,
    market="mx",
    per_conversation=5,
) -> dict[str, list[str]]:
    """读取每个会话最近已落库的消息 ID，供中断恢复判断历史是否接续。"""
    ids = list(dict.fromkeys(
        str(value or "").strip()
        for value in conversation_ids
        if str(value or "").strip()
    ))
    clean_limit = int(per_conversation)
    if not ids:
        return {}
    if clean_limit <= 0 or clean_limit > 20:
        raise ValueError("invalid_im_message_anchor_limit")
    ranked = select(
        im_message.c.conversation_id,
        im_message.c.server_id,
        func.row_number().over(
            partition_by=im_message.c.conversation_id,
            order_by=(
                im_message.c.create_ms.desc(),
                im_message.c.server_id.desc(),
            ),
        ).label("row_no"),
    ).where(
        im_message.c.bd_market == str(market or "").strip().lower(),
        im_message.c.conversation_id.in_(ids),
    ).subquery("im_message_anchor_ranked")
    statement = (
        select(
            ranked.c.conversation_id,
            ranked.c.server_id,
            ranked.c.row_no,
        )
        .where(ranked.c.row_no <= clean_limit)
        .order_by(ranked.c.conversation_id, ranked.c.row_no)
    )
    with engine.connect() as connection:
        rows = connection.execute(statement).mappings().all()
    anchors: dict[str, list[str]] = {}
    for row in rows:
        cid = str(row["conversation_id"] or "").strip()
        sid = str(row["server_id"] or "").strip()
        if cid and sid:
            anchors.setdefault(cid, []).append(sid)
    return anchors


def upsert_conv_msgs(
    engine,
    conv,
    msg_rows,
    market="mx",
):
    """market:市场维度(bd_market 普通列);默认 mx 行为不变。插入带 bd_market,冲突更新不动该列(保持简单)。"""
    conv = {**conv, "bd_market": market}
    msg_rows = (
        _uniform_message_rows(
            [{**row, "bd_market": market} for row in msg_rows]
        )
        if msg_rows else msg_rows
    )
    with engine.begin() as con:
        ci = pg_insert(im_conversation).values(**conv)
        con.execute(ci.on_conflict_do_update(index_elements=["conversation_id"],
                    set_=_conv_set(ci, conv, live=False)))
        if msg_rows:
            mi = pg_insert(im_message).values(msg_rows)
            stored = con.execute(
                mi.on_conflict_do_update(
                    index_elements=["server_id"],
                    set_=_msg_set(mi, msg_rows[0].keys()),
                ).returning(
                    im_message.c.creator_oec_id,
                    im_message.c.is_from_me,
                    im_message.c.sender_role,
                    im_message.c.create_ms,
                    text("(xmax = 0) AS inserted"),
                )
            ).mappings().all()
            newest_inbound = {}
            fallback_oec = str(conv.get("creator_oec_id") or "").strip()
            for row in stored:
                oec = str(row["creator_oec_id"] or fallback_oec).strip()
                if (
                    not row["inserted"]
                    or row["is_from_me"]
                    or int(row["sender_role"] or 0) not in {1, 3}
                ):
                    continue
                event_at = _event_time(row["create_ms"])
                if not oec or event_at is None:
                    continue
                previous = newest_inbound.get(oec)
                if previous is None or event_at > previous:
                    newest_inbound[oec] = event_at
            state_repo = CreatorImStateRepo(engine)
            for oec, inbound_at in newest_inbound.items():
                state_repo.mark_inbound(
                    market=market,
                    identity_key=f"oec:{oec}",
                    inbound_at=inbound_at,
                    connection=con,
                )


def upsert_live(engine, m, market="mx"):
    """实时捕获单条双向消息；只有达人入站会改变待回复状态。
    market:市场维度(bd_market 普通列);默认 mx 行为不变。"""
    now = _now()
    role = int(m.get("role") or 0)
    is_from_me = bool(m.get("me"))
    tms = int(m["t"] or 0)
    row = {
        "server_id": m["sid"], "conversation_id": m["cid"], "creator_oec_id": m.get("oec"),
        "is_from_me": is_from_me, "sender_role": role or None, "content": m["content"],
        "message_type": m.get("mtype"), "create_ms": tms,
        "index_hi": int(m.get("ihi") or 0), "index_lo": int(m.get("ilo") or 0), "captured_at": now,
        "bd_market": market,
    }
    if m.get("media"):
        row["raw_json"] = {"media": m["media"]}
    if m.get("card"):
        card = m["card"]
        row["card_pid"] = str(card.get("pid") or "").strip() or None
        row["card_list"] = str(card.get("list_id") or "").strip() or None
        raw = dict(row.get("raw_json") or {})
        raw["card"] = card
        row["raw_json"] = raw
    with engine.begin() as con:
        mi = pg_insert(im_message).values(**row)
        # RETURNING (xmax=0) 判断是不是【新插入】的消息:PG 里 xmax=0=INSERT,非0=冲突走了 UPDATE
        stmt = mi.on_conflict_do_update(index_elements=["server_id"], set_=_msg_set(mi, row.keys())) \
                 .returning(text("(xmax = 0) AS inserted"))
        inserted = bool(con.execute(stmt).scalar())
        if not inserted:
            return          # 消息已存在(replay/双触发/重启后重收) → 只幂等刷消息行,绝不重复累加会话计数(Codex#2)
        if is_from_me:
            conv = {"conversation_id": m["cid"], "creator_oec_id": m.get("oec"),
                    "last_mine_ms": tms, "last_active_ms": tms, "captured_at": now,
                    "bd_market": market}
        elif role == 1:              # 新的达人真人回复 → 会话摘要计数 +1，最后回复时间守卫
            conv = {"conversation_id": m["cid"], "creator_oec_id": m.get("oec"),
                    "last_creator_reply": m["content"], "last_creator_reply_ms": tms,
                    "last_active_ms": tms, "creator_reply_count": 1, "captured_at": now,
                    "bd_market": market}
        elif role == 3:              # 新的达人加橱窗 → 强 BD 信号:showcase_count 累加
            conv = {"conversation_id": m["cid"], "creator_oec_id": m.get("oec"),
                    "showcase_count": 1, "last_active_ms": tms, "captured_at": now,
                    "bd_market": market}
            showcase_at = _event_time(tms)
            if showcase_at is not None:
                conv["showcase_at"] = showcase_at
        else:
            return
        ci = pg_insert(im_conversation).values(**conv)
        con.execute(ci.on_conflict_do_update(index_elements=["conversation_id"], set_=_conv_set(ci, conv, live=True)))
        oec = str(m.get("oec") or "").strip()
        inbound_at = _event_time(tms)
        if not is_from_me and role in {1, 3} and oec and inbound_at is not None:
            CreatorImStateRepo(engine).mark_inbound(
                market=market,
                identity_key=f"oec:{oec}",
                inbound_at=inbound_at,
                connection=con,
            )


class OutboundMessageProjector:
    """把已获精确 server ID 的发送组件立即投影到 IM 消息流。"""

    def __init__(self, engine) -> None:
        self.engine = engine

    def project_sent_component(
        self,
        *,
        server_id: str,
        conversation_id: str,
        creator_oec_id: str | None,
        component_kind: str,
        text: str | None,
        pid: str | None,
        market: str,
        sent_at: datetime,
    ) -> None:
        kind = str(component_kind).strip().lower()
        if kind not in {"card", "text"}:
            raise ValueError(f"unsupported_component_kind:{kind}")
        tms = int(sent_at.timestamp() * 1000)
        row = {
            "server_id": str(server_id),
            "conversation_id": str(conversation_id),
            "creator_oec_id": creator_oec_id,
            "is_from_me": True,
            "sender_role": 4,
            "content": "[商品列表]" if kind == "card" else str(text or ""),
            "message_type": 1000,
            "create_ms": tms,
            "index_hi": 0,
            "index_lo": 0,
            "raw_json": {"source": "send_task", "component_kind": kind},
            "captured_at": _now(),
        }
        if kind == "card":
            row["card_pid"] = str(pid or "").strip() or None
        conv = {
            "conversation_id": str(conversation_id),
            "creator_oec_id": creator_oec_id,
            "last_mine_ms": tms,
            "last_active_ms": tms,
            "captured_at": _now(),
        }
        upsert_conv_msgs(
            self.engine,
            conv,
            [row],
            market=market,
        )


__all__ = [
    "OutboundMessageProjector",
    "load_conversation_checkpoints",
    "load_conversation_message_anchors",
    "upsert_conv_msgs",
    "upsert_live",
]
