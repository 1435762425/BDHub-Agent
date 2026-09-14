# -*- coding: utf-8 -*-
"""hub/repo/outreach.py —— 触达闭环存储层：话术库 / 触达台账 + 占位符渲染(原样搬自 outreach/store.py)。

- 无发送闸门(owner 决策:平台 5/30 限制模糊动态,不做卡点)。
- 身份三件套 handle_key ↔ oec_id ↔ conversation_id 贯穿 outreach。
- 占位符值(product_name/commission_from/commission_to)随 PID 爬取带出,发送时渲染,零手填。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select, update, func, and_
from sqlalchemy.dialects.postgresql import insert as pg_insert

from ..engine import check_schema, get_engine
from ..schema import message_template, outreach


# ---------------- 渲染 ----------------
# 固定占位符集合(要加新占位符在此登记即可)。用 str.replace 而非 str.format:
# 对模板里的表情/字面花括号健壮,不会因未知占位符或散花括号报错。
PLACEHOLDERS = ("creator_id", "product_name", "commission_from", "commission_to")


def render_message(body: str, **values) -> str:
    """把模板 body 里的 {placeholder} 用 values 替换;None → 空串;未提供的占位符原样保留。"""
    out = body or ""
    for key in PLACEHOLDERS:
        if key in values:
            v = values[key]
            out = out.replace("{" + key + "}", "" if v is None else str(v))
    return out


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _rows(result) -> list[dict]:
    return [dict(r._mapping) for r in result]


# ---------------- ① 话术库 ----------------
class TemplateStore:
    def __init__(self, engine=None):
        self.eng = engine or get_engine()
        check_schema(self.eng)

    def add(self, template_id: str, body: str, *, name=None, lang="es",
            tag=None, enabled=True, placeholders=None, market=None) -> str:
        """新增/更新一套话术(upsert by template_id)。market=所属市场(区分不同市场话术)。
        placeholders=占位符声明清单(D1)。tag 传 None 时【不覆盖】原值(护 send --seed-templates 播的 tag)。"""
        now = _now()
        row = dict(template_id=template_id, name=name, lang=lang, tag=tag,
                   body=body, enabled=enabled, market=market, placeholders=placeholders,
                   created_at=now, updated_at=now)
        ins = pg_insert(message_template).values(**row)
        upd = {k: ins.excluded[k] for k in ("name", "lang", "body", "enabled", "market", "placeholders", "updated_at")}
        upd["tag"] = func.coalesce(ins.excluded.tag, message_template.c.tag)   # tag=None 保留原(话术页不管 tag,别清掉 seed 的)
        with self.eng.begin() as c:
            c.execute(ins.on_conflict_do_update(index_elements=["template_id"], set_=upd))
        return template_id

    def delete(self, template_id: str, *, market: str | None = None) -> int:
        """删除一套话术(硬删)。返回删除行数。"""
        from sqlalchemy import delete as _delete
        condition = message_template.c.template_id == template_id
        if market is not None:
            condition = and_(
                condition,
                message_template.c.market == market,
            )
        with self.eng.begin() as c:
            r = c.execute(_delete(message_template).where(condition))
        return r.rowcount

    def get(
        self,
        template_id: str,
        *,
        market: str | None = None,
    ) -> dict | None:
        condition = message_template.c.template_id == template_id
        if market is not None:
            condition = and_(
                condition,
                message_template.c.market == market,
            )
        with self.eng.connect() as c:
            r = c.execute(
                select(message_template).where(condition)
            ).first()
        return dict(r._mapping) if r else None

    def list(
        self,
        *,
        market: str | None = None,
        enabled_only: bool = True,
    ) -> list[dict]:
        q = select(message_template)
        if market is not None:
            q = q.where(message_template.c.market == market)
        if enabled_only:
            q = q.where(message_template.c.enabled.is_(True))
        q = q.order_by(message_template.c.template_id)
        with self.eng.connect() as c:
            return _rows(c.execute(q))


# ---------------- ③ 触达台账 ----------------
_OPEN_STATUSES = ("queued", "sent", "replied")   # 未关闭 = 还在进行中


class OutreachStore:
    def __init__(self, engine=None):
        self.eng = engine or get_engine()
        check_schema(self.eng)
        self.templates = TemplateStore(self.eng)

    def already_open(self, handle_key: str, pid: str, market: str = "mx") -> bool:
        """该达人这个 PID 是否已有【未关闭】触达(去重:防对同一货重复建 queued)。market 隔离:默认 mx。"""
        with self.eng.connect() as c:
            r = c.execute(select(func.count()).select_from(outreach).where(and_(
                outreach.c.handle_key == handle_key,
                outreach.c.pid == pid,
                outreach.c.status.in_(_OPEN_STATUSES),
                outreach.c.bd_market == market,
            ))).scalar()
        return bool(r)

    def create(self, handle_key: str, pid: str, template_id: str, *,
               oec_id=None, video_id=None, product_name=None,
               commission_from=None, commission_to=None, creator_id=None,
               send_account=None, dedupe=True, raw_json=None, market: str = "mx") -> str | None:
        """建一条 queued 触达事件(渲染定稿话术)。dedupe=True 时同 handle+pid(+market)已有未关闭事件则跳过返回 None。
        market:市场维度(bd_market 普通列,非 PK);默认 mx 行为不变。"""
        if dedupe and self.already_open(handle_key, pid, market=market):
            return None
        tpl = self.templates.get(template_id)
        if not tpl:
            raise ValueError(f"话术模板不存在: {template_id}")
        msg = render_message(
            tpl["body"], creator_id=creator_id or handle_key, product_name=product_name,
            commission_from=commission_from, commission_to=commission_to)
        oid = uuid.uuid4().hex
        now = _now()
        row = dict(
            outreach_id=oid, handle_key=handle_key, oec_id=oec_id, pid=str(pid),
            video_id=(str(video_id) if video_id is not None else None),
            template_id=template_id, rendered_message=msg, send_account=send_account,
            status="queued", created_at=now, updated_at=now, raw_json=raw_json,
            bd_market=market,
        )
        with self.eng.begin() as c:
            c.execute(pg_insert(outreach).values(**row))
        return oid

    def _set(self, outreach_id: str, **fields) -> None:
        fields["updated_at"] = _now()
        with self.eng.begin() as c:
            c.execute(update(outreach).where(
                outreach.c.outreach_id == outreach_id).values(**fields))

    def mark_sent(self, outreach_id: str, *, conversation_id=None, sent_at=None) -> None:
        self._set(outreach_id, status="sent", conversation_id=conversation_id,
                  sent_at=sent_at or _now())

    def log_sent(self, handle_key: str, *, market: str, rendered_message: str,
                 oec_id=None, pid=None, conversation_id=None, template_id=None,
                 send_account=None, raw_json=None, send_task_id=None,
                 send_task_lead_id=None, logical_touch_id=None) -> str:
        """composer(手动定向发送)发成功后直接落一条 status='sent' 台账(增量标记的 durable 腿)。
        与 create()(队列语义:渲染固定占位符+queued)不同:正文由 composer 以列名占位符渲染定稿,原样入库。"""
        oid = uuid.uuid4().hex
        now = _now()
        with self.eng.begin() as c:
            c.execute(pg_insert(outreach).values(
                outreach_id=oid, handle_key=handle_key, oec_id=oec_id,
                pid=(str(pid) if pid else None), template_id=template_id,
                rendered_message=rendered_message, send_account=send_account,
                conversation_id=conversation_id,
                status="sent", sent_at=now, created_at=now, updated_at=now,
                raw_json=raw_json, bd_market=market,
                send_task_id=send_task_id,
                send_task_lead_id=send_task_lead_id,
                logical_touch_id=logical_touch_id))
        return oid

    def get(self, outreach_id: str) -> dict | None:
        """按触达事件 ID 获取一行，供新任务关联和审计读取。"""
        with self.eng.connect() as connection:
            row = connection.execute(
                select(outreach).where(outreach.c.outreach_id == outreach_id)
            ).mappings().first()
        return dict(row) if row else None

    def mark_failed(self, outreach_id: str, error: str | None = None) -> None:
        self._set(outreach_id, status="send_failed",
                  raw_json={"error": error} if error else None)

    def mark_replied(self, outreach_id: str, *, replied_at=None) -> None:
        self._set(outreach_id, status="replied", replied_at=replied_at or _now())

    def set_outcome(self, outreach_id: str, outcome: str, *, close=False) -> None:
        fields = {"outcome": outcome}
        if close:
            fields["status"] = "closed"
        self._set(outreach_id, **fields)

    def queued(self, limit: int | None = None, send_account: str | None = None,
               market: str = "mx") -> list[dict]:
        """待发清单(status=queued)。send 模块(P2)从这里取。market 默认 mx 行为不变。"""
        q = select(outreach).where(and_(
            outreach.c.status == "queued", outreach.c.bd_market == market))
        if send_account:
            q = q.where(outreach.c.send_account == send_account)
        q = q.order_by(outreach.c.created_at)
        if limit:
            q = q.limit(limit)
        with self.eng.connect() as c:
            return _rows(c.execute(q))

    def by_handle(self, handle_key: str, market: str = "mx") -> list[dict]:
        with self.eng.connect() as c:
            return _rows(c.execute(select(outreach).where(and_(
                outreach.c.handle_key == handle_key,
                outreach.c.bd_market == market)).order_by(outreach.c.created_at)))

    def counts(self, market: str = "mx") -> dict:
        """各 status 计数(概览)。market 默认 mx 过滤。"""
        with self.eng.connect() as c:
            r = c.execute(select(outreach.c.status, func.count()).where(
                outreach.c.bd_market == market).group_by(outreach.c.status))
            return {row[0]: row[1] for row in r}


__all__ = ["render_message", "TemplateStore", "OutreachStore", "PLACEHOLDERS"]
