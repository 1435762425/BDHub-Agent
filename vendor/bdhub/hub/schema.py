# -*- coding: utf-8 -*-
"""数据获取层 typed schema(单一真相源):metadata + 全部 Table 定义。

【schema 单一真相源】当前画像、联系方式、IM、触达、日报和任务表的列与类型集中在此。
列清单复用 model.FIELDS（避免与模型漂移），类型按下方映射集覆盖，其余默认 TEXT。
后端固定 PostgreSQL（无 SQLite 回退）。并发靠连接池，调用方不再手写连接锁。

注：本项目历史上从 014-bd-hub 提取；当前 PostgreSQL 运行库由本项目负责，旧 014 不再读写。
曾属于旧项目的专用市场表和旧业务表已按迁移记录退役；当前只保留 MX 运行数据和通用 market/bd_market 扩展维度。

（原属 bdhub/db.py，2026-07-06 拆分迁移，逻辑逐字不变；engine 相关见 bdhub/hub/engine.py。）
"""
from __future__ import annotations

from sqlalchemy import (
    MetaData, Table, Column, Index,
    Text, Numeric, BigInteger, Integer, Boolean, DateTime, SmallInteger, Date, text,
    CheckConstraint, ForeignKeyConstraint, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB

from .models import FIELDS

# ---------------- schema ----------------
metadata = MetaData()

# creator_profile 列类型覆盖（其余 FIELDS 默认 TEXT）。
# 数值列统一 NUMERIC：模型给的是 float|None，NUMERIC 避免 float→int 强转边界，且支持范围查询+索引。
_CP_NUMERIC = {
    "followers", "gmv_value", "video_gmv", "live_gmv", "units_sold", "gpm", "live_gpm", "video_gpm",
    "female_pct", "video_cnt_30d", "ec_video_cnt_30d", "live_cnt_30d", "avg_view",
}
_CP_BOOL = {
    "is_our_mcn", "contact_available", "is_fast_growing", "is_active",
    "is_quickly_response", "is_high_sample_dispatch",
}


def _cp_col(name: str) -> Column:
    if name in _CP_NUMERIC:
        return Column(name, Numeric)
    if name in _CP_BOOL:
        return Column(name, Boolean)
    return Column(name, Text)


creator_profile = Table(
    "creator_profile", metadata,
    Column("handle_key", Text, primary_key=True),
    Column("bd_market", Text, nullable=False, server_default="mx", primary_key=True),  # 市场维度,与 handle_key 组成复合主键(Plan MK-A)
    *[_cp_col(c) for c in FIELDS],
    Column("raw_json", JSONB),                       # 完整原始字段(light~88/满血~97)，TOAST 自动压缩
    Column("captured_at", DateTime(timezone=True)),
    Index("ix_cp_gmv_value", "gmv_value"),           # GMV 筛人/排序热路径
    Index("ix_cp_followers", "followers"),
    Index("ix_cp_captured_at", "captured_at"),       # 新鲜度判断
    Index("ix_cp_bind_mcn", "bind_mcn"),
    Index("ix_creator_profile_bdmkt", "bd_market"),
    Index("ix_cp_oecid", "oec_id", "bd_market"),     # IM 按 oec 反查画像热路径(看板台账/线索;无它=7.7万行全表扫×每行)
)

miss_log = Table(
    "miss_log", metadata,
    Column("handle_key", Text, primary_key=True),
    Column("bd_market", Text, nullable=False, server_default="mx", primary_key=True),
    Column("missed_at", DateTime(timezone=True)),
    Column("n", Integer, server_default="1"),
    Index("ix_miss_log_bdmkt", "bd_market"),
)

# OEC 唯一身份：handle 只作为会变化的当前别名，不再承担实体主键职责。
creator_identity = Table(
    "creator_identity", metadata,
    Column("bd_market", Text, primary_key=True),
    Column("oec_id", Text, primary_key=True),
    Column("current_handle_key", Text),
    Column("first_seen_at", DateTime(timezone=True), nullable=False),
    Column("last_seen_at", DateTime(timezone=True), nullable=False),
    Column("last_light_at", DateTime(timezone=True)),
    Column("last_full_at", DateTime(timezone=True)),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Index("ix_creator_identity_handle", "bd_market", "current_handle_key"),
)

creator_handle_alias = Table(
    "creator_handle_alias", metadata,
    Column("alias_id", Text, primary_key=True),
    Column("bd_market", Text, nullable=False),
    Column("oec_id", Text, nullable=False),
    Column("handle_key", Text, nullable=False),
    Column("handle", Text, nullable=False),
    Column("valid_from", DateTime(timezone=True), nullable=False),
    Column("valid_to", DateTime(timezone=True)),
    Column("confirmed_snapshot_id", Text, nullable=False),
    Index("ix_creator_alias_oec", "bd_market", "oec_id"),
    Index(
        "uq_creator_alias_active_handle",
        "bd_market",
        "handle_key",
        unique=True,
        postgresql_where=text("valid_to is null"),
    ),
    Index(
        "uq_creator_alias_active_oec",
        "bd_market",
        "oec_id",
        unique=True,
        postgresql_where=text("valid_to is null"),
    ),
)

creator_profile_snapshot = Table(
    "creator_profile_snapshot", metadata,
    Column("snapshot_id", Text, primary_key=True),
    Column("idempotency_key", Text, nullable=False, unique=True),
    Column("bd_market", Text, nullable=False),
    Column("oec_id", Text, nullable=False),
    Column("observed_handle_key", Text, nullable=False),
    Column("observed_handle", Text, nullable=False),
    Column("capture_kind", Text, nullable=False),
    Column("route", Text, nullable=False),
    Column("quality_status", Text, nullable=False),
    Column("valid_fields", JSONB, nullable=False),
    Column("rejection_reasons", JSONB, nullable=False),
    *[
        _cp_col(column_name)
        for column_name in FIELDS
        if column_name not in {"handle", "oec_id"}
    ],
    Column("parsed_json", JSONB, nullable=False),
    Column("raw_json", JSONB, nullable=False),
    Column("payload_hash", Text, nullable=False),
    Column("schema_version", Integer, nullable=False, server_default="1"),
    Column("captured_at", DateTime(timezone=True), nullable=False),
    Index("ix_cps_oec_time", "bd_market", "oec_id", "captured_at"),
)

creator_profile_current = Table(
    "creator_profile_current", metadata,
    Column("bd_market", Text, primary_key=True),
    Column("oec_id", Text, primary_key=True),
    Column("handle_key", Text, nullable=False),
    Column("handle", Text, nullable=False),
    Column("peak_gmv_value", Numeric),
    Column("peak_gmv_at", DateTime(timezone=True)),
    *[
        _cp_col(column_name)
        for column_name in FIELDS
        if column_name not in {"handle", "oec_id"}
    ],
    Column("field_sources", JSONB, nullable=False),
    Column("captured_at", DateTime(timezone=True), nullable=False),
    Index("ix_cpc_handle", "bd_market", "handle_key"),
    Index("ix_cpc_gmv", "bd_market", "gmv_value"),
)


# Light 富化可恢复任务：运行、工作项和每次尝试分层持久。
# 只存结构化结果与耗时，禁止写 Cookie/header/远程 body 或 message。
enrich_run = Table(
    "enrich_run", metadata,
    Column("run_id", Text, primary_key=True),
    Column("bd_market", Text, nullable=False),
    Column("capture_kind", Text, nullable=False),
    Column("route", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("parameters", JSONB, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("started_at", DateTime(timezone=True)),
    Column("finished_at", DateTime(timezone=True)),
)

enrich_item = Table(
    "enrich_item", metadata,
    Column("item_id", Text, primary_key=True),
    Column("run_id", Text, nullable=False),
    Column("bd_market", Text, nullable=False),
    Column("oec_id", Text, nullable=False),
    Column("requested_handle", Text),
    Column("capture_kind", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("total_attempts", Integer, nullable=False, server_default="0"),
    Column("error_retries", Integer, nullable=False, server_default="0"),
    Column("persistence_retries", Integer, nullable=False, server_default="0"),
    Column("retry_at", DateTime(timezone=True)),
    Column("terminal_reason", Text),
    Column("snapshot_id", Text),
    Column("claimed_by", Text),
    Column("claimed_at", DateTime(timezone=True)),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint(
        "run_id", "bd_market", "oec_id", "capture_kind",
        name="uq_enrich_item_work",
    ),
    Index("ix_enrich_item_claim", "run_id", "status", "retry_at"),
)

enrich_attempt = Table(
    "enrich_attempt", metadata,
    Column("attempt_id", Text, primary_key=True),
    Column("item_id", Text, nullable=False),
    Column("account", Text, nullable=False),
    Column("route", Text, nullable=False),
    Column("outcome_kind", Text, nullable=False),
    Column("remote_code", Text),
    Column("detail_code", Text),
    Column("request_ms", Numeric),
    Column("limiter_wait_ms", Numeric),
    Column("parse_ms", Numeric),
    Column("field_count", Integer),
    Column("quality_status", Text),
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("finished_at", DateTime(timezone=True), nullable=False),
    Index("ix_enrich_attempt_item", "item_id", "started_at"),
)


# 手动补录源:BD 自己录入的 handle。主键 handle_key(=norm_handle,与 creator_profile 对齐 join)。
# 只是"入口 + 来源留痕",不存画像;落库后 diff 出不在画像库的 → 喂 enrich 富化成真值。
manual_seed = Table(
    "manual_seed", metadata,
    Column("handle_key", Text, primary_key=True),        # =norm_handle(raw):去@+小写,join creator_profile.handle_key
    Column("bd_market", Text, nullable=False, server_default="mx", primary_key=True),
    Column("raw_input", Text),                           # 用户原始输入(保留大小写/@,审计回溯)
    Column("note", Text),                                # 来源说明(BD 手填,如"客户指定"/"抖音私信来的")
    Column("region", Text),                              # 市场标注(可空；不作为当前市场启用开关)
    Column("added_at", DateTime(timezone=True)),         # 首次补录时刻(重复补录 COALESCE 保旧)
    Column("captured_at", DateTime(timezone=True)),      # 最近一次补录/更新
    Index("ix_manual_added", "added_at"),
    Index("ix_manual_region", "region"),
    Index("ix_manual_seed_bdmkt", "bd_market"),
)


# ==================== IM 会话/消息(历史上从 014 镜像；当前由本项目监听/发送链路读写) ====================
# 突破:partner IM 的会话/消息不走 REST,数据在页面 chatApi.sdkInstance 本地态。已登录 IM 页 page.evaluate 直读。
# 口径:达人回复=isFromMe=false 且 senderRole=1;我方未读=unread_count>0;出站已读回执平台不给。
im_conversation = Table(
    "im_conversation", metadata,
    Column("conversation_id", Text, primary_key=True),   # 会话雪花id(=shortId)
    Column("bd_market", Text, nullable=False, server_default="mx"),   # 市场维度(非PK,普通列;Plan MK-A)
    Column("creator_oec_id", Text),                      # originExt.creator_oec_id,贯穿七源的join键
    Column("unread_count", Integer),                     # 我方未读=达人发来还没看;>0=待回复
    Column("last_active_ms", BigInteger),                # updateTime(epoch毫秒),最后活跃
    Column("creator_reply_count", Integer),              # 达人真人消息条数(senderRole=1),0=从没回过
    Column("last_creator_reply", Text),                  # 达人最后一条回复正文(BD一眼看意向)
    Column("last_creator_reply_ms", BigInteger),         # 达人最后回复时刻(epoch毫秒)
    Column("last_mine_ms", BigInteger),                  # 我方最后发出时刻(判谁该接话)
    Column("collaboration_type", Text),                  # originExt.creator_collaboration_type
    Column("intent", Text),                              # 会话当前意向(最近一条达人回复意向码)
    Column("showcase_at", DateTime(timezone=True)),      # 达人把我方商品加橱窗最近时刻——强 BD 信号
    Column("showcase_count", Integer),                   # 累计橱窗"加商品"系统消息条数
    Column("raw_json", JSONB),
    Column("captured_at", DateTime(timezone=True)),
    Index("ix_imconv_oec", "creator_oec_id"),
    Index("ix_imconv_unread", "unread_count"),
    Index("ix_imconv_active", "last_active_ms"),
    Index("ix_imconv_intent", "intent"),
    Index("ix_imconv_showcase", "showcase_at"),
    Index("ix_im_conversation_bdmkt", "bd_market"),
)

# 官方 IM creator/mget 返回的 OEC→handle 观察值。
# 这不是画像真值；L1 验证成功后才由正常画像管线写入 creator_profile_current。
im_creator_identity = Table(
    "im_creator_identity", metadata,
    Column("bd_market", Text, primary_key=True),
    Column("creator_oec_id", Text, primary_key=True),
    Column("handle", Text, nullable=False),
    Column("nickname", Text),
    Column("handle_status", Text),
    Column("handle_authorized", Boolean),
    Column("source", Text, nullable=False, server_default="im_creator_mget"),
      Column("first_seen_at", DateTime(timezone=True), nullable=False),
      Column("last_seen_at", DateTime(timezone=True), nullable=False),
      Column("last_conversation_id", Text),
      CheckConstraint(
          "source = 'im_creator_mget'",
          name="ck_im_creator_identity_source",
      ),
      Index("ix_im_creator_identity_handle", "bd_market", "handle"),
    Index("ix_im_creator_identity_seen", "bd_market", "last_seen_at"),
)

im_message = Table(
    "im_message", metadata,
    Column("server_id", Text, primary_key=True),         # 消息服务端唯一id(幂等重抓不重复)
    Column("bd_market", Text, nullable=False, server_default="mx"),   # 市场维度(非PK,普通列;Plan MK-A)
    Column("conversation_id", Text),
    Column("creator_oec_id", Text),
    Column("is_from_me", Boolean),                        # true=我方BD发, false=对方
    Column("sender_role", SmallInteger),                  # 1达人/3系统/4我方机构
    Column("content", Text),
    Column("intent", Text),                              # 该条达人回复意向码
    Column("message_type", Integer),                     # 1000文本,其余卡片/图片按类型码
    Column("card_pid", Text),                            # 商品卡的 originExt.product_id
    Column("card_list", Text),
    Column("create_ms", BigInteger),
    Column("index_hi", Integer),
    Column("index_lo", Integer),
    Column("raw_json", JSONB),
    Column("captured_at", DateTime(timezone=True)),
    Index("ix_immsg_conv", "conversation_id"),
    Index("ix_immsg_oec", "creator_oec_id"),
    Index("ix_immsg_role", "sender_role"),
    Index("ix_immsg_time", "create_ms"),
    Index("ix_im_message_bdmkt", "bd_market"),
)


# ③'' im_auto_reply —— AI 自动回复状态机(bdhub/reply)。一行=一个会话的机器人状态。
#     got_wa/handoff=终态(此后全人工;人工放回自动手改 SQL)。每日计数由 count_date 派生,跨天自动恢复(无 capped 态)。
im_auto_reply = Table(
    "im_auto_reply", metadata,
    Column("conversation_id", Text, primary_key=True),
    Column("bd_market", Text, nullable=False, server_default="mx"),
    Column("creator_oec_id", Text),
    Column("state", Text, nullable=False, server_default="active"),   # active/got_wa/handoff
    Column("whatsapp", Text),                            # 达人在聊天里给的号(归一化 +52XXXXXXXXXX)
    Column("handoff_reason", Text),                      # 超纲转人工原因(intent 码)
    Column("last_intent", Text),
    Column("objection_count", Integer, server_default="0"),   # 连续异议应对条数(streak,非异议回复清零);≥2 → state='cooled'
    Column("link_sent_at", DateTime(timezone=True)),          # 社群链接已发时刻(状态位判重,不依赖可编辑文案/消息落库)
    Column("wa_added_at", DateTime(timezone=True)),           # 人工已把号加进 WhatsApp 群的时刻(台账工作队列勾销位)
    Column("handoff_done_at", DateTime(timezone=True)),       # 人工已处理完转接的时刻(台账工作队列勾销位)
    Column("ai_reply_count_today", Integer, server_default="0"),
    Column("count_date", Date),                          # UTC 日期,跨天重置计数
    Column("total_ai_replies", Integer, server_default="0"),
    Column("last_ai_reply_at", DateTime(timezone=True)),
    Column("updated_at", DateTime(timezone=True)),
    Index("ix_imar_state", "state"),
    Index("ix_imar_countdate", "count_date"),
)

# ③''' reply_template —— AI 回复可编辑固定文案(bdhub/reply)。一行=某市场某槽位的人写文案。
#     槽位 confirm_wa/fallback_business/safe_ask;库无行或 body 空 → engine 回退 prompts 硬编码默认。
#     这三段是【降级目标】(人写),不过 LLM 数字/词典护栏,允许含 URL/数字(如社群链接)。
reply_template = Table(
    "reply_template", metadata,
    Column("bd_market", Text, primary_key=True),
    Column("slot", Text, primary_key=True),
    Column("body", Text, nullable=False),
    Column("updated_at", DateTime(timezone=True)),
)

# ③'''' reply_outbox —— 看板人工消息出站队列(看板↔monitor 的 DB 队列,零新 IPC)。
#      看板聊天窗写 queued → monitor resident 每 tick 消费(≤3条)同页发送 → 回写 sent/failed。
#      与 AI 无关(监听在跑即可人工发);失败不自动重试(人工看到 error 后重发一条)。
im_delivery_intent = Table(
    "im_delivery_intent", metadata,
    Column("intent_id", Text, primary_key=True),
    Column("source_kind", Text, nullable=False),
    Column("source_id", Text, nullable=False),
    Column("bd_market", Text, nullable=False),
    Column("conversation_id", Text, nullable=False),
    Column("account_name", Text),
    Column("message_kind", Text, nullable=False),
    Column("body", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("process_id", Integer, nullable=False),
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("finished_at", DateTime(timezone=True)),
    Column("server_id", Text),
    Column("error_code", Text),
    Column("receipt", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    UniqueConstraint("source_kind", "source_id", name="uq_im_delivery_source"),
    CheckConstraint("source_kind in ('human','ai')", name="ck_im_delivery_source"),
    CheckConstraint("status in ('sending','sent','failed','unknown')", name="ck_im_delivery_status"),
    Index("ix_im_delivery_pending", "bd_market", "status", "started_at"),
)

reply_outbox = Table(
    "reply_outbox", metadata,
    Column("id", Text, primary_key=True),                              # uuid hex
    Column("bd_market", Text, nullable=False, server_default="mx"),
    Column("conversation_id", Text, nullable=False),
    Column("body", Text, nullable=False),
    Column("message_kind", Text, nullable=False, server_default="text"),
    Column("media_path", Text),
    Column("media_name", Text),
    Column("media_type", Text),
    Column("media_size", BigInteger),
    Column("status", Text, nullable=False, server_default="queued"),   # queued→sent/failed/cancelled
    Column("retry_no", Integer, nullable=False, server_default="0"),
    Column("error", Text),
    Column("server_id", Text),                                         # 发送回读的消息id
    Column("created_at", DateTime(timezone=True)),
    Column("sent_at", DateTime(timezone=True)),
    Index("ix_outbox_status", "status"),
    Index("ix_outbox_conv", "conversation_id"),
    CheckConstraint(
        "message_kind in ('text','image')",
        name="ck_reply_outbox_message_kind",
    ),
)


# MCN 官方关系档案：主表来自 Partner relation/list，画像表只按 OEC 补充经营数据。
# relation_id 是关系实体主键；不能用 OEC 代替，否则达人二次绑定时会覆盖旧档案。
mcn_creator_relation = Table(
    "mcn_creator_relation", metadata,
    Column("bd_market", Text, primary_key=True),
    Column("relation_id", Text, primary_key=True),
    Column("partner_id", Text),
    Column("partner_name", Text),
    Column("oec_id", Text, nullable=False),
    Column("creator_id", Text),
    Column("handle_key", Text, nullable=False),
    Column("handle", Text, nullable=False),
    Column("nickname", Text),
    Column("avatar_url", Text),
    Column("creator_level", Text),
    Column("status", SmallInteger, nullable=False),
    Column("status_key", Text, nullable=False),
    Column("data_relation_id", Text),
    Column("data_relation_status", SmallInteger),
    Column("data_page_link", Text),
    Column("commission_relation_id", Text),
    Column("commission_relation_status", SmallInteger),
    Column("commission_proportion", Numeric),
    Column("record_type", SmallInteger),
    Column("create_at", DateTime(timezone=True)),
    Column("effective_start_at", DateTime(timezone=True)),
    Column("effective_end_at", DateTime(timezone=True)),
    Column("expire_at", DateTime(timezone=True)),
    Column("extend_relation_id", Text),
    Column("extend_relation_status", SmallInteger),
    Column("extend_effective_start_at", DateTime(timezone=True)),
    Column("extend_effective_end_at", DateTime(timezone=True)),
    Column("first_seen_at", DateTime(timezone=True), nullable=False),
    Column("last_seen_at", DateTime(timezone=True), nullable=False),
    Column("raw_json", JSONB, nullable=False),
    Index("ix_mcn_relation_oec", "bd_market", "oec_id"),
    Index("ix_mcn_relation_status", "bd_market", "status_key"),
    Index("ix_mcn_relation_start", "bd_market", "effective_start_at"),
    Index("ix_mcn_relation_seen", "bd_market", "last_seen_at"),
)

mcn_relation_observation = Table(
    "mcn_relation_observation", metadata,
    Column("observation_id", Text, primary_key=True),
    Column("bd_market", Text, nullable=False),
    Column("relation_id", Text, nullable=False),
    Column("oec_id", Text, nullable=False),
    Column("status", SmallInteger, nullable=False),
    Column("status_key", Text, nullable=False),
    Column("payload_hash", Text, nullable=False),
    Column("observed_at", DateTime(timezone=True), nullable=False),
    Column("raw_json", JSONB, nullable=False),
    UniqueConstraint(
        "bd_market", "relation_id", "payload_hash",
        name="uq_mcn_relation_observation_payload",
    ),
    Index("ix_mcn_observation_relation", "bd_market", "relation_id", "observed_at"),
    Index("ix_mcn_observation_oec", "bd_market", "oec_id", "observed_at"),
)

mcn_relation_sync_run = Table(
    "mcn_relation_sync_run", metadata,
    Column("sync_id", Text, primary_key=True),
    Column("bd_market", Text, nullable=False),
    Column("official_total", Integer, nullable=False),
    Column("retrievable_total", Integer, nullable=False),
    Column("official_status_counts", JSONB, nullable=False),
    Column("observed_at", DateTime(timezone=True), nullable=False),
    Index("ix_mcn_sync_market_time", "bd_market", "observed_at"),
)

# ③'''''' reply_setting —— AI 回复运行参数的库内热覆盖(bdhub/reply)。config.yaml 刻意只读
# (手写注释会被程序 dump 清掉),看板改配额走这张表;engine 30s TTL 热读,无行回退 yaml 默认。
reply_setting = Table(
    "reply_setting", metadata,
    Column("bd_market", Text, primary_key=True, server_default="mx"),
    Column("key", Text, primary_key=True),
    Column("value", Text, nullable=False),
    Column("updated_at", DateTime(timezone=True)),
)

# ③''''' im_ai_dry_log —— AI 试运行(DRY)拟发流水(bdhub/reply)。dry 模式不真发也不写
# im_message/im_auto_reply,唯独把"本来要发什么"落这张 append-only 表 → 看板流水带 DRY 标
# 展示,供上线前人工评估生成质量。切真发后该表自然停增,只留评估期史料。
im_ai_dry_log = Table(
    "im_ai_dry_log", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("bd_market", Text, nullable=False, server_default="mx"),
    Column("conversation_id", Text, nullable=False),
    Column("creator_oec_id", Text),
    Column("intent", Text),
    Column("body", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Index("ix_iadl_market_time", "bd_market", "created_at"),
)


# ==================== BD 触达闭环(P1):PID雷达 + 话术库 + 触达台账 ====================
# 设计单一真相源见 docs/BD闭环建设计划.md。商业模式=佣金差价套利:给达人已带过的 PID 建高佣链接,
# IM 私信发【商品卡(按PID搜着发)+话术(占位符渲染)】让达人换我们的挂复投,吃佣金差价。
# 无发送闸门(owner 决策:平台5/30限制模糊动态,不做卡点)。

# ① creator_product —— PID 雷达(增量复抓,爬取带出)。一行 = 达人一条带货视频的一个商品。
#    product_name / commission_public / commission_ours 随 PID 爬取一起带出 → 发送时占位符自动渲染,零手填。
#    PK(handle_key, video_id, pid):同达人多视频、一视频可挂多品。复抓 diff 靠 video_id 集合。
creator_product = Table(
    "creator_product", metadata,
    Column("handle_key", Text, primary_key=True),        # 归一 handle,join creator_profile.handle_key
    Column("video_id", Text, primary_key=True),          # 哪条带货视频
    Column("pid", Text, primary_key=True),               # 商品 PID(发商品卡按它在面板搜)
    Column("bd_market", Text, nullable=False, server_default="mx"),   # 市场维度(非PK,普通列;Plan MK-A)
    Column("oec_id", Text),                              # 达人 oec_id(身份桥,扒取后回填)
    Column("product_name", Text),                        # 商品名 = 话术占位符 {product_name}
    Column("commission_public", Numeric),                # 公开佣金率 = {commission_from}
    Column("commission_ours", Numeric),                  # 我们能给的高佣档 = {commission_to}
    Column("video_publish_time", DateTime(timezone=True)),  # 达人发布时间(=时效性评级:近3/5/7天梯队)
    Column("video_views", BigInteger),                   # 该带货视频播放量(=评级5档:<500/500-1k/1k-2k/2k-5k/5k+ → 话术语气)
    Column("video_likes", BigInteger),                   # 点赞量(辅助)
    Column("first_seen_at", DateTime(timezone=True)),    # 我们首次扒到(复抓 COALESCE 保旧)
    Column("last_seen_at", DateTime(timezone=True)),     # 最近一次复抓仍在(增量水位)
    Column("raw_json", JSONB),                           # 扒取原始(想加列不用重抓,从这重解析)
    Index("ix_cp_handle", "handle_key"),
    Index("ix_cp_pid", "pid"),
    Index("ix_cp_oec", "oec_id"),
    Index("ix_cp_lastseen", "last_seen_at"),
    Index("ix_creator_product_bdmkt", "bd_market"),
)

# ② message_template —— 话术库(多套,为 A/B 组合预留)。一行 = 一套模板。
#    body 含占位符 {creator_id}{product_name}{commission_from}{commission_to},发送时 str.format 渲染。
message_template = Table(
    "message_template", metadata,
    Column("template_id", Text, primary_key=True),       # 稳定 id(如 v1_high_commission_es)
    Column("name", Text),                                # 人类可读名
    Column("lang", Text),                                # 语言(es/en...)
    Column("tag", Text),                                 # 场景/AB 分组标签
    Column("body", Text, nullable=False),                # 模板正文(含占位符)
    Column("enabled", Boolean),                          # 是否启用(store 默认 True)
    Column("market", Text),                              # 所属市场 mx/jp/uk/us(区分不同市场话术;NULL=通用/旧,向后兼容)
    Column("placeholders", JSONB),                       # 占位符声明清单 [{name,desc,required,default}](D1;NULL=无声明,向后兼容)
    Column("created_at", DateTime(timezone=True)),
    Column("updated_at", DateTime(timezone=True)),
    Index("ix_tpl_enabled", "enabled"),
    Index("ix_tpl_tag", "tag"),
)

# ③ outreach —— 触达台账(核心,扛多次发)。一行 = 一次触达事件(达人 × 商品PID × 一次发送)。
#    同 handle 可多行(A货一行、B货一行)。身份三件套 handle_key↔oec_id↔conversation_id 贯穿,解改名/回复对不上。
#    status 漏斗:queued→sent→replied→closed(+send_failed);outcome:no_reply/interested/converted/reposted/parked。
outreach = Table(
    "outreach", metadata,
    Column("outreach_id", Text, primary_key=True),       # 事件 id(handle:pid:epoch 或 uuid)
    Column("bd_market", Text, nullable=False, server_default="mx"),   # 市场维度(非PK,普通列;Plan MK-A)
    Column("handle_key", Text, nullable=False),          # 身份①
    Column("oec_id", Text),                              # 身份②(七源 join)
    Column("conversation_id", Text),                     # 身份③(IM 会话,发后回写)
    Column("pid", Text),                                 # 发哪个商品卡
    Column("video_id", Text),                            # 针对哪条视频(可空)
    Column("template_id", Text),                         # 用哪套话术
    Column("rendered_message", Text),                    # 发送时渲染定稿(留痕,占位符已填值)
    Column("send_account", Text),                        # 哪个账号发的(必须=监控账号)
    Column("status", Text),                              # queued/sent/replied/closed/send_failed
    Column("outcome", Text),                             # 业务结果(可空)
    Column("sent_at", DateTime(timezone=True)),          # 真实发出时刻(回读确认后写)
    Column("replied_at", DateTime(timezone=True)),       # 达人首次回复时刻
    Column("created_at", DateTime(timezone=True)),
    Column("updated_at", DateTime(timezone=True)),
    Column("raw_json", JSONB),                           # 扩展(占位符值快照/发送错误详情)
    Column("send_task_id", Text),                        # 新发送任务关联(兼容旧台账)
    Column("send_task_lead_id", Text),                   # 任务内线索关联
    Column("logical_touch_id", Text),                    # 逻辑触达标识(组件级发送归并)
    Index("ix_outreach_handle", "handle_key"),
    Index("ix_outreach_oec", "oec_id"),
    Index("ix_outreach_conv", "conversation_id"),
    Index("ix_outreach_pid", "pid"),
    Index("ix_outreach_status", "status"),
    Index("ix_outreach_sent", "sent_at"),
    Index("ix_outreach_handle_pid", "handle_key", "pid"),   # 去重/查"这达人这货发过没"(P2 自动补货防重复)
    Index("ix_outreach_bdmkt", "bd_market"),
    Index("ix_outreach_send_task", "send_task_id", "send_task_lead_id"),
)


# ④ creator_scan —— 达人视频采集水位表（增量复扫调度 + 去重不重扫）。一行 = 一个达人的最近一次扫描态。
#    next_due_at 到期才复扫（活跃带货勤扫、沉默缓扫）；last_status 分 ok/no_shoppable/fail 便于跳死号。
creator_scan = Table(
    "creator_scan", metadata,
    Column("handle_key", Text, primary_key=True),        # 归一 handle，join creator_product/creator_profile
    Column("bd_market", Text, nullable=False, server_default="mx", primary_key=True),
    Column("oec_id", Text),                              # 达人 oec_id（有则带上，发送身份桥）
    Column("sec_uid", Text),                             # TikTok secUid（抓取拿到，复用免重解析主页）
    Column("last_scanned_at", DateTime(timezone=True)),  # 上次扫描完成时刻
    Column("video_count", Integer),                     # 上次抓到视频数（≤20）
    Column("shoppable_count", Integer),                 # 其中带货视频数
    Column("pid_count", Integer),                       # 去重带货 PID 数
    Column("next_due_at", DateTime(timezone=True)),     # 下次该扫时刻（= last + 周期，增量调度水位）
    Column("last_status", Text),                        # ok / no_shoppable / fail（secUid 空=死号/挑战）
    Column("scan_count", Integer, server_default="0"),  # 累计扫描次数
    Index("ix_scan_due", "next_due_at"),
    Index("ix_scan_status", "last_status"),
    Index("ix_creator_scan_bdmkt", "bd_market"),
)


# ⑤ creator_contact —— 达人公开联系方式(WhatsApp/Email)。从 014 移植(2026-07-11);与 014 同库同表(oec_id 全局唯一,
#    联系方式与市场无关,故无 bd_market)。数据来自已登录 IM 页 fetch api_sens/cmp/contact?scene=11(绕滑块)。
#    ★该表已由 014 在共享库建好(1794 行);此处只补【模型定义】供 01 读写,init_db 的 create_all(checkfirst) 不会重建。
creator_contact = Table(
    "creator_contact", metadata,
    Column("oec_id", Text, primary_key=True),            # 达人 oec_id(全局唯一,联系方式主键)
    Column("handle", Text),                              # 原始 handle(留痕)
    Column("whatsapp", Text),                            # field=1(whatsapp/电话)
    Column("email", Text),                               # field=2(email)
    # None 表示“本次没有值”，必须绑定成 SQL NULL；JSON null 会绕过
    # COALESCE/IS NULL 判断并意外清空已有投影。
    Column("other_json", JSONB(none_as_null=True)),      # 其它 field
    Column("raw_json", JSONB(none_as_null=True)),        # 原始 contact_info
    Column("captured_at", DateTime(timezone=True)),
)

# 联系方式永久历史。creator_contact 继续作为最新非空投影；本表保留所有曾确认值。
creator_contact_point = Table(
    "creator_contact_point", metadata,
    Column("contact_point_id", Text, primary_key=True),
    Column("bd_market", Text, nullable=False),
    Column("oec_id", Text, nullable=False),
    Column("contact_type", Text, nullable=False),
    Column("contact_value", Text, nullable=False),
    Column("value_hash", Text, nullable=False),
    Column("first_seen_at", DateTime(timezone=True), nullable=False),
    Column("last_seen_at", DateTime(timezone=True), nullable=False),
    Column("is_current", Boolean, nullable=False, server_default="true"),
    Column("source", Text, nullable=False),
    UniqueConstraint(
        "bd_market",
        "oec_id",
        "contact_type",
        "value_hash",
        name="uq_creator_contact_point_value",
    ),
    Index(
        "ix_creator_contact_point_identity",
        "bd_market",
        "oec_id",
        "contact_type",
    ),
)

# 同一市场、达人和联系方式类型只能有一个当前值。普通 UniqueConstraint
# 无法表达 ``is_current=true`` 的条件，因此使用 PostgreSQL/SQLite 都支持的
# 部分唯一索引；历史值仍可无限追加。
Index(
    "uq_creator_contact_point_current",
    creator_contact_point.c.bd_market,
    creator_contact_point.c.oec_id,
    creator_contact_point.c.contact_type,
    unique=True,
    postgresql_where=creator_contact_point.c.is_current.is_(True),
    sqlite_where=creator_contact_point.c.is_current.is_(True),
)

# ⑥ creator_relationship —— 人工运营关系当前态。OEC+市场是身份，富化/日报不得覆盖这些人工字段。
creator_relationship = Table(
    "creator_relationship", metadata,
    Column("bd_market", Text, primary_key=True),
    Column("oec_id", Text, primary_key=True),
    Column("whatsapp", Text),
    Column("whatsapp_obtained_at", DateTime(timezone=True)),
    Column("wa_added_at", DateTime(timezone=True)),
    Column("group_invited_at", DateTime(timezone=True)),
    Column("group_joined_at", DateTime(timezone=True)),
    Column("deep_operation_at", DateTime(timezone=True)),
    Column("mcn_potential_at", DateTime(timezone=True)),
    Column("mcn_converted_at", DateTime(timezone=True)),
    Column("assignee", Text),
    Column("group_name", Text),
    Column("note", Text),
    Column("source", Text),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Index("ix_creator_relationship_assignee", "bd_market", "assignee"),
)


# ⑦ creator_task —— 员工执行任务。开放任务用 open_key 唯一约束做数据库级防重复。
creator_task = Table(
    "creator_task", metadata,
    Column("task_id", Text, primary_key=True),
    Column("bd_market", Text, nullable=False),
    Column("oec_id", Text, nullable=False),
    Column("task_type", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("priority", Integer, nullable=False, server_default="0"),
    Column("assignee", Text),
    Column("open_key", Text, unique=True),
    Column("source", Text),
    Column("due_at", DateTime(timezone=True)),
    Column("completion_note", Text),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("completed_at", DateTime(timezone=True)),
    Index("ix_creator_task_queue", "bd_market", "status", "priority"),
    Index("ix_creator_task_assignee", "bd_market", "assignee", "status"),
)


# ⑧ creator_event —— 达人关系与任务操作时间线，只追加、不覆盖。
creator_event = Table(
    "creator_event", metadata,
    Column("event_id", Text, primary_key=True),
    Column("bd_market", Text, nullable=False),
    Column("oec_id", Text, nullable=False),
    Column("event_type", Text, nullable=False),
    Column("actor", Text, nullable=False),
    Column("from_value", Text),
    Column("to_value", Text),
    Column("payload", JSONB),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Index("ix_creator_event_timeline", "bd_market", "oec_id", "created_at"),
)


# ==================== IM 运营任务数据地基 ====================

# 一项发送任务，配置与执行进度均以数据库为真相源。
send_task = Table(
    "send_task", metadata,
    Column("task_id", Text, primary_key=True),
    Column("bd_market", Text, nullable=False),
    Column("name", Text, nullable=False),
    Column("source_type", Text, nullable=False),
    Column("send_mode", Text, nullable=False),
    Column("template_id", Text),
    Column("text_snapshot", Text),
    Column("template_snapshot", JSONB),
    Column("default_pid", Text),
    Column(
        "card_limit_per_creator",
        Integer,
        nullable=False,
        server_default="2",
    ),
    Column("status", Text, nullable=False),
    Column("status_reason", Text),
    Column("revision", Integer, nullable=False, server_default="1"),
    Column("preflight_revision", Integer),
    Column("created_by", Text, nullable=False),
    Column("updated_by", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("started_at", DateTime(timezone=True)),
    Column("finished_at", DateTime(timezone=True)),
    CheckConstraint(
        "bd_market in ('mx','br','uk','it','jp','us','de')",
        name="ck_send_task_market",
    ),
    CheckConstraint(
        "send_mode in ('text_only','card_only','text_then_card','card_then_text')",
        name="ck_send_task_mode",
    ),
    CheckConstraint(
        "card_limit_per_creator between 1 and 4",
        name="ck_send_task_card_limit",
    ),
    CheckConstraint(
        "status in ('draft','preflighting','ready','running','paused',"
        "'completed','completed_with_errors','cancelled')",
        name="ck_send_task_status",
    ),
    Index("ix_send_task_market_status_created", "bd_market", "status", "created_at"),
)


# 导入时的原始 handle 不被身份解析改写；解析结论与预检快照另列保存。
send_task_lead = Table(
    "send_task_lead", metadata,
    Column("lead_id", Text, primary_key=True),
    Column("task_id", Text, nullable=False),
    Column("source_row_no", Integer, nullable=False),
    Column("raw_handle", Text, nullable=False),
    Column("creator_identity_key", Text),
    Column("oec_id", Text),
    Column("pid", Text),
    Column("input_fingerprint", Text, nullable=False),
    Column("rendered_text_snapshot", Text),
    Column("match_status", Text, nullable=False),
    Column("eligibility_status", Text, nullable=False),
    Column("exclusion_code", Text),
    Column("manual_excluded", Boolean, nullable=False, server_default="false"),
    Column(
        "source_snapshot",
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    ),
    Column(
        "gmv_tier_snapshot",
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    ),
    Column(
        "relationship_snapshot",
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    ),
    Column("status", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    ForeignKeyConstraint(["task_id"], ["send_task.task_id"]),
    UniqueConstraint("task_id", "input_fingerprint", name="uq_send_task_lead_input"),
    Index("ix_send_task_lead_eligibility", "task_id", "eligibility_status", "status"),
    Index("ix_send_task_lead_identity_pid", "task_id", "creator_identity_key", "pid"),
)


# 一条线索可拆成文字、商品卡两个有序逻辑组件；重启时按组件状态恢复。
send_task_component = Table(
    "send_task_component", metadata,
    Column("component_id", Text, primary_key=True),
    Column("lead_id", Text, nullable=False),
    Column("component_kind", Text, nullable=False),
    Column("sequence_no", Integer, nullable=False),
    Column("status", Text, nullable=False),
    Column("dedupe_key", Text, nullable=False, unique=True),
    Column("conversation_id", Text),
    Column("platform_message_id", Text),
    Column("attempt_count", Integer, nullable=False, server_default="0"),
    Column("sent_at", DateTime(timezone=True)),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    ForeignKeyConstraint(["lead_id"], ["send_task_lead.lead_id"]),
    UniqueConstraint("lead_id", "component_kind", name="uq_send_task_component_kind"),
    CheckConstraint("component_kind in ('text','card')", name="ck_send_component_kind"),
    Index("ix_send_task_component_status", "lead_id", "component_kind", "status"),
)


# 组件的每次传输尝试，保留失败信息而不覆盖逻辑组件的最终状态。
send_component_attempt = Table(
    "send_component_attempt", metadata,
    Column("attempt_id", Text, primary_key=True),
    Column("component_id", Text, nullable=False),
    Column("attempt_no", Integer, nullable=False),
    Column("status", Text, nullable=False),
    Column("send_account", Text),
    Column("worker_id", Text),
    Column("platform_message_id", Text),
    Column("conversation_id", Text),
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("finished_at", DateTime(timezone=True)),
    Column("error_code", Text),
    Column("error_detail", Text),
    Column(
        "transport_snapshot",
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    ),
    ForeignKeyConstraint(["component_id"], ["send_task_component.component_id"]),
    UniqueConstraint("component_id", "attempt_no", name="uq_send_component_attempt_no"),
    Index("ix_send_component_attempt_component", "component_id", "attempt_no"),
)


# 常驻发送进程只保存运行状态和账号配置名，不保存 cookie、密码或浏览器 profile 路径。
send_worker_state = Table(
    "send_worker_state", metadata,
    Column("worker_id", Text, primary_key=True),
    Column("bd_market", Text, nullable=False),
    Column("account_name", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("process_id", Integer),
    Column("current_task_id", Text),
    Column("current_lead_id", Text),
    Column("heartbeat_at", DateTime(timezone=True), nullable=False),
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("last_error_code", Text),
    Column("last_error_detail", Text),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    ForeignKeyConstraint(["current_task_id"], ["send_task.task_id"]),
    ForeignKeyConstraint(["current_lead_id"], ["send_task_lead.lead_id"]),
    CheckConstraint(
        "status in ('starting','ready','busy','paused',"
        "'auth_required','unhealthy','stopped')",
        name="ck_send_worker_state_status",
    ),
    Index(
        "ix_send_worker_state_market_status",
        "bd_market",
        "status",
        "updated_at",
    ),
    Index("ix_send_worker_state_heartbeat", "heartbeat_at"),
)


# ==================== MX 高佣 ShareLink 任务 ====================
# ShareLink 会在平台侧创建 plan_id，属于有状态写操作。任务、工作项、最终产物与
# 每次远端尝试分层保存；任何表都不得写 Cookie、签名值或完整请求头。
share_link_job = Table(
    "share_link_job", metadata,
    Column("job_id", Text, primary_key=True),
    Column("bd_market", Text, nullable=False),
    Column("name", Text, nullable=False),
    Column("commission_mode", Text, nullable=False),
    Column(
        "commission_config",
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    ),
    Column("account_name", Text),
    Column("operator_name", Text),
    Column("creator_name", Text),
    Column("operation_type", Text, nullable=False, server_default="create"),
    Column("status", Text, nullable=False),
    Column("phase", Text, nullable=False),
    Column("revision", Integer, nullable=False, server_default="1"),
    Column("source_name", Text),
    Column("source_sha256", Text),
    Column(
        "confirmed_snapshot",
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    ),
    Column("process_job_id", Text),
    Column("created_by", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("started_at", DateTime(timezone=True)),
    Column("finished_at", DateTime(timezone=True)),
    CheckConstraint(
        "bd_market in ('mx','br','uk','it','jp','us','de')",
        name="ck_share_link_job_market",
    ),
    CheckConstraint(
        "commission_mode in ('MX_AVERAGE','MAX_CREATOR','KEEP_MARGIN','BOOST_OVER_OPEN')",
        name="ck_share_link_job_commission_mode",
    ),
    CheckConstraint(
        "status in ('draft','previewing','ready','running','paused','completed',"
        "'completed_with_errors','needs_review','failed','cancelled')",
        name="ck_share_link_job_status",
    ),
    CheckConstraint(
        "phase in ('import','preview','confirm','generate','result')",
        name="ck_share_link_job_phase",
    ),
    CheckConstraint(
        "operation_type in ('create','commission_update','catalog_refresh')",
        name="ck_share_link_job_operation_type",
    ),
    Index("ix_share_link_job_market_status", "bd_market", "status", "updated_at"),
)


share_link_plan = Table(
    "share_link_plan", metadata,
    Column("plan_record_id", Text, primary_key=True),
    Column("bd_market", Text, nullable=False),
    Column("account_name", Text),
    Column("partner_identity_fingerprint", Text, nullable=False),
    Column("pid", Text, nullable=False),
    Column("campaign_id", Text, nullable=False),
    Column("creator_commission", Numeric, nullable=False),
    Column("request_fingerprint", Text, nullable=False, unique=True),
    Column("share_url", Text, nullable=False),
    Column("platform_plan_id", Text, nullable=False, unique=True),
    Column("status", Text, nullable=False),
    Column("source", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("last_verified_at", DateTime(timezone=True)),
    CheckConstraint(
        "bd_market in ('mx','br','uk','it','jp','us','de')",
        name="ck_share_link_plan_market",
    ),
    CheckConstraint(
        "status in ('active','legacy_imported')",
        name="ck_share_link_plan_status",
    ),
    Index("ix_share_link_plan_pid", "bd_market", "pid", "created_at"),
)


share_link_record = Table(
    "share_link_record", metadata,
    Column("record_id", Text, primary_key=True),
    Column("bd_market", Text, nullable=False),
    Column("pid", Text, nullable=False),
    Column("operator_name", Text),
    Column("creator_name", Text),
    Column("current_plan_record_id", Text),
    Column("latest_item_id", Text),
    Column("status", Text, nullable=False),
    Column("source", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    ForeignKeyConstraint(
        ["current_plan_record_id"], ["share_link_plan.plan_record_id"]
    ),
    CheckConstraint(
        "bd_market in ('mx','br','uk','it','jp','us','de')",
        name="ck_share_link_record_market",
    ),
    CheckConstraint(
        "status in ('pending','active','result_unknown','failed','cancelled')",
        name="ck_share_link_record_status",
    ),
    Index("ix_share_link_record_operator", "bd_market", "operator_name", "updated_at"),
    Index("ix_share_link_record_creator", "bd_market", "creator_name", "updated_at"),
    Index("ix_share_link_record_pid", "bd_market", "pid", "updated_at"),
)


# 商品信息会随平台销量、库存和评分变化；与不可变的 ShareLink Plan 分开保存当前值。
share_link_product_current = Table(
    "share_link_product_current", metadata,
    Column("bd_market", Text, primary_key=True),
    Column("pid", Text, primary_key=True),
    Column("product_name", Text),
    Column("shop_name", Text),
    Column("product_thumbnail", Text),
    Column("product_sales", BigInteger),
    Column("stock", BigInteger),
    Column("product_price", Numeric),
    Column("product_rating", Numeric),
    Column("product_review_count", BigInteger),
    Column("shop_score", Numeric),
    Column("shop_experience_score", Numeric),
    Column("selected_campaign_id", Text),
    Column("selected_campaign_name", Text),
    Column("total_commission", Numeric),
    Column("open_commission", Numeric),
    Column("creator_commission", Numeric),
    Column("agency_margin", Numeric),
    Column("campaign_end_at", DateTime(timezone=True)),
    Column("free_sample", Boolean),
    Column("source_job_id", Text),
    Column("captured_at", DateTime(timezone=True), nullable=False),
    Column("platform_captured_at", DateTime(timezone=True)),
    CheckConstraint(
        "bd_market in ('mx','br','uk','it','jp','us','de')",
        name="ck_share_link_product_current_market",
    ),
    Index(
        "ix_share_link_product_current_sales",
        "bd_market", "product_sales",
    ),
    Index(
        "ix_share_link_product_current_rating",
        "bd_market", "product_rating",
    ),
)


share_link_item = Table(
    "share_link_item", metadata,
    Column("item_id", Text, primary_key=True),
    Column("job_id", Text, nullable=False),
    Column("record_id", Text),
    Column("operation_type", Text, nullable=False, server_default="create"),
    Column("target_campaign_id", Text),
    Column("source_row_no", Integer, nullable=False),
    Column("pid", Text, nullable=False),
    Column("commission_override", Numeric),
    Column("input_fingerprint", Text, nullable=False),
    Column("selected_for_generation", Boolean, nullable=False, server_default="true"),
    Column("search_status", Text, nullable=False),
    Column("create_status", Text, nullable=False),
    Column(
        "candidate_snapshot",
        JSONB,
        nullable=False,
        server_default=text("'[]'::jsonb"),
    ),
    Column("selected_campaign_id", Text),
    Column("selected_campaign_name", Text),
    Column("product_name", Text),
    Column("shop_name", Text),
    Column("campaign_type", Integer),
    Column("campaign_end_at", DateTime(timezone=True)),
    Column("free_sample", Boolean),
    Column("total_commission", Numeric),
    Column("open_commission", Numeric),
    Column("creator_commission", Numeric),
    Column("agency_margin", Numeric),
    Column("plan_record_id", Text),
    Column("error_code", Text),
    Column("error_detail", Text),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    ForeignKeyConstraint(["job_id"], ["share_link_job.job_id"]),
    ForeignKeyConstraint(
        ["record_id"], ["share_link_record.record_id"],
        name="fk_share_link_item_record",
    ),
    ForeignKeyConstraint(["plan_record_id"], ["share_link_plan.plan_record_id"]),
    UniqueConstraint("job_id", "input_fingerprint", name="uq_share_link_item_input"),
    CheckConstraint(
        "search_status in ('pending','searching','ready','not_found','no_valid_campaign','failed')",
        name="ck_share_link_item_search_status",
    ),
    CheckConstraint(
        "create_status in ('not_requested','queued','creating','succeeded','reused',"
        "'result_unknown','failed','cancelled')",
        name="ck_share_link_item_create_status",
    ),
    CheckConstraint(
        "operation_type in ('create','commission_update','catalog_refresh')",
        name="ck_share_link_item_operation_type",
    ),
    Index("ix_share_link_item_job_status", "job_id", "search_status", "create_status"),
    Index("ix_share_link_item_pid", "pid"),
)


share_link_attempt = Table(
    "share_link_attempt", metadata,
    Column("attempt_id", Text, primary_key=True),
    Column("item_id", Text, nullable=False),
    Column("phase", Text, nullable=False),
    Column("attempt_no", Integer, nullable=False),
    Column("status", Text, nullable=False),
    Column("account_name", Text, nullable=False),
    Column("request_dispatched_at", DateTime(timezone=True)),
    Column("finished_at", DateTime(timezone=True)),
    Column("http_status", Integer),
    Column("platform_code", Text),
    Column("error_code", Text),
    Column("error_detail", Text),
    Column(
        "transport_snapshot",
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    ),
    Column("created_at", DateTime(timezone=True), nullable=False),
    ForeignKeyConstraint(["item_id"], ["share_link_item.item_id"]),
    UniqueConstraint("item_id", "phase", "attempt_no", name="uq_share_link_attempt_no"),
    CheckConstraint("phase in ('search','create')", name="ck_share_link_attempt_phase"),
    CheckConstraint(
        "status in ('started','succeeded','failed','result_unknown')",
        name="ck_share_link_attempt_status",
    ),
    Index("ix_share_link_attempt_item", "item_id", "phase", "attempt_no"),
)


public_share_link_request = Table(
    "public_share_link_request", metadata,
    Column("request_id", Text, primary_key=True),
    Column("token_hash", Text, nullable=False, unique=True),
    Column("bd_market", Text, nullable=False),
    Column("pid", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("job_id", Text),
    Column("record_id", Text),
    Column("ip_hash", Text, nullable=False),
    Column("country_code", Text),
    Column("claim_token", Text),
    Column("claimed_at", DateTime(timezone=True)),
    Column("error_code", Text),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    ForeignKeyConstraint(["job_id"], ["share_link_job.job_id"]),
    ForeignKeyConstraint(["record_id"], ["share_link_record.record_id"]),
    CheckConstraint("bd_market = 'mx'", name="ck_public_share_link_request_market"),
    CheckConstraint(
        "country_code is null or "
        "(length(country_code) = 2 and country_code = upper(country_code))",
        name="ck_public_share_link_request_country",
    ),
    CheckConstraint(
        "status in ('queued','verifying','generating','succeeded','ineligible',"
        "'result_unknown','failed')",
        name="ck_public_share_link_request_status",
    ),
    Index(
        "ix_public_share_link_request_status",
        "status", "created_at",
    ),
    Index(
        "ix_public_share_link_request_ip",
        "ip_hash", "created_at",
    ),
    Index(
        "ix_public_share_link_request_pid",
        "pid", "created_at",
    ),
    Index(
        "ix_public_share_link_request_country",
        "country_code", "created_at",
    ),
)


# 每个市场常驻 IM 监听进程的运行心跳。只保存运行元数据，不保存身份包或密码。
im_listener_state = Table(
    "im_listener_state", metadata,
    Column("listener_id", Text, primary_key=True),
    Column("bd_market", Text, nullable=False),
    Column("account_name", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("process_id", Integer),
    Column("heartbeat_at", DateTime(timezone=True), nullable=False),
    Column("last_sync_at", DateTime(timezone=True)),
    Column("last_event_at", DateTime(timezone=True)),
    Column("last_error_code", Text),
    Column("last_error_detail", Text),
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    CheckConstraint(
        "status in ('starting','online','degraded',"
        "'auth_required','unhealthy','stopped')",
        name="ck_im_listener_state_status",
    ),
    Index(
        "ix_im_listener_state_market_status",
        "bd_market",
        "status",
        "updated_at",
    ),
    Index("ix_im_listener_state_heartbeat", "heartbeat_at"),
)


# resident 历史恢复的持久退避状态。浏览器受控回收后仍保留失败次数、
# 下一次深拉时间和已观察 updateTime，避免每个新进程重复把同一会话扩到 1,000 条。
im_history_recovery_state = Table(
    "im_history_recovery_state", metadata,
    Column("bd_market", Text, primary_key=True),
    Column("conversation_id", Text, primary_key=True),
    Column("failure_count", Integer, nullable=False),
    Column("next_retry_at", DateTime(timezone=True), nullable=False),
    Column("quarantined", Boolean, nullable=False, server_default="false"),
    Column("observed_update_ms", BigInteger, nullable=False, server_default="0"),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    CheckConstraint(
        "failure_count >= 1",
        name="ck_im_history_recovery_failure_count",
    ),
    CheckConstraint(
        "observed_update_ms >= 0",
        name="ck_im_history_recovery_observed_update",
    ),
    Index(
        "ix_im_history_recovery_due",
        "bd_market", "quarantined", "next_retry_at",
    ),
)


# 人工维护的当前 IM 状态；入站消息只能推进为待回复，不能自动覆写人工拒绝。
# 前端按需打开/补拉单个会话的持久命令；由已持有浏览器租约的常驻监听消费。
im_conversation_sync_request = Table(
    "im_conversation_sync_request", metadata,
    Column("request_id", Text, primary_key=True),
    Column("bd_market", Text, nullable=False),
    Column("identity", Text, nullable=False),
    Column("oec_id", Text, nullable=False),
    Column("conversation_id", Text),
    Column("requested_count", Integer, nullable=False),
    Column("status", Text, nullable=False),
    Column("error", Text),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("started_at", DateTime(timezone=True)),
    Column("finished_at", DateTime(timezone=True)),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    CheckConstraint(
        "status in ('queued','running','succeeded','failed')",
        name="ck_im_conversation_sync_request_status",
    ),
    CheckConstraint(
        "requested_count between 1 and 1000",
        name="ck_im_conversation_sync_request_count",
    ),
    Index(
        "ix_im_conversation_sync_request_queue",
        "bd_market", "status", "created_at",
    ),
)


creator_im_state = Table(
    "creator_im_state", metadata,
    Column("bd_market", Text, primary_key=True),
    Column("creator_identity_key", Text, primary_key=True),
    Column("status", Text, nullable=False),
    Column("status_set_by", Text, nullable=False),
    Column("status_set_at", DateTime(timezone=True), nullable=False),
    Column("last_inbound_at", DateTime(timezone=True)),
    # BD Hub 本地已看水位；平台 unread_count 不参与工作台红点。
    Column("last_seen_interaction_at", DateTime(timezone=True)),
    Column("rejected_at", DateTime(timezone=True)),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    CheckConstraint(
        "status in ('pending_reply','processing','replied','rejected')",
        name="ck_creator_im_state_status",
    ),
    Index("ix_creator_im_state_queue", "bd_market", "status", "last_inbound_at"),
)


# 发送任务允许使用的执行账号池；Worker 只能领取显式分配给自己的任务。
send_task_account = Table(
    "send_task_account", metadata,
    Column("task_id", Text, primary_key=True),
    Column("account_name", Text, primary_key=True),
    Column("assigned_by", Text, nullable=False),
    Column("assigned_at", DateTime(timezone=True), nullable=False),
    ForeignKeyConstraint(["task_id"], ["send_task.task_id"], ondelete="CASCADE"),
    Index("ix_send_task_account_account", "account_name", "task_id"),
)


# 人工一对一回复模板；与批量发送模板和 AI 回复 slot 完全隔离。
human_reply_template = Table(
    "human_reply_template", metadata,
    Column("template_id", Text, primary_key=True),
    Column("bd_market", Text, nullable=False),
    Column("name", Text, nullable=False),
    Column("category", Text, nullable=False),
    Column("language", Text, nullable=False),
    Column("scope", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("body", Text, nullable=False),
    Column("placeholders", JSONB, nullable=False, server_default="[]"),
    Column("version", Integer, nullable=False, server_default="1"),
    Column("usage_count", Integer, nullable=False, server_default="0"),
    Column("last_used_at", DateTime(timezone=True)),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    CheckConstraint(
        "scope in ('conversation','creator_chat','both')",
        name="ck_human_reply_template_scope",
    ),
    CheckConstraint(
        "status in ('draft','active','disabled','archived')",
        name="ck_human_reply_template_status",
    ),
    Index(
        "ix_human_reply_template_market_status",
        "bd_market",
        "status",
        "updated_at",
    ),
)


# TAP 日报的正向达人+商品关系；后续日报缺席不得删除历史。
tap_creator_product_history = Table(
    "tap_creator_product_history", metadata,
    Column("bd_market", Text, primary_key=True),
    Column("creator_identity_key", Text, primary_key=True),
    Column("pid", Text, primary_key=True),
    Column("first_seen_report_date", Date, nullable=False),
    Column("last_seen_report_date", Date, nullable=False),
    Column("seen_day_count", Integer, nullable=False),
    Column("first_source_file_id", Text, nullable=False),
    Column("last_source_file_id", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Index("ix_tap_history_identity", "bd_market", "creator_identity_key"),
    Index("ix_tap_history_pid", "bd_market", "pid"),
)


# 商品卡成功后才激活归因窗口；同一线索只能有一条归因记录。
send_task_attribution = Table(
    "send_task_attribution", metadata,
    Column("attribution_id", Text, primary_key=True),
    Column("task_id", Text, nullable=False),
    Column("lead_id", Text, nullable=False, unique=True),
    Column("bd_market", Text, nullable=False),
    Column("creator_identity_key", Text, nullable=False),
    Column("pid", Text, nullable=False),
    Column("card_sent_at", DateTime(timezone=True)),
    Column("window_start_date", Date),
    Column("window_end_date", Date),
    Column("status", Text, nullable=False),
    Column("matched_report_date", Date),
    Column("matched_file_id", Text),
    Column("finalized_at", DateTime(timezone=True)),
    Column("ineligible_reason", Text),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    ForeignKeyConstraint(["task_id"], ["send_task.task_id"]),
    ForeignKeyConstraint(["lead_id"], ["send_task_lead.lead_id"]),
    Index("ix_send_attribution_due", "bd_market", "status", "window_end_date"),
    Index(
        "ix_send_attribution_pair",
        "bd_market",
        "creator_identity_key",
        "pid",
        "status",
    ),
)


# 任务时间线只追加，供执行审计和后续问题追踪。
send_task_event = Table(
    "send_task_event", metadata,
    Column("event_id", Text, primary_key=True),
    Column("task_id", Text, nullable=False),
    Column("lead_id", Text),
    Column("event_type", Text, nullable=False),
    Column("actor", Text, nullable=False),
    Column("payload", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    Column("created_at", DateTime(timezone=True), nullable=False),
    ForeignKeyConstraint(["task_id"], ["send_task.task_id"]),
    Index("ix_send_task_event_timeline", "task_id", "created_at"),
)


# ==================== Partner Center 机构日报同步 ====================
# 当前只接 MX 的 MCN/TAP 单日报表。旧 carry_report 粒度不同，不接收这里的数据。

report_contract = Table(
    "report_contract", metadata,
    Column("contract_id", Text, primary_key=True),
    Column("market", Text, nullable=False),
    Column("report_kind", Text, nullable=False),
    Column("version", Integer, nullable=False),
    Column("biz_role", Integer, nullable=False),
    Column("granularity", Text, nullable=False),
    Column("dimensions", JSONB, nullable=False),
    Column("filter_payload", JSONB, nullable=False),
    Column("require_summation", Boolean, nullable=False),
    Column("request_fingerprint", Text, nullable=False, unique=True),
    Column("status", Text, nullable=False),
    Column("effective_from", Date, nullable=False),
    Column("captured_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint(
        "market",
        "report_kind",
        "version",
        name="uq_report_contract_version",
    ),
    Index("ix_report_contract_lookup", "market", "report_kind", "status"),
)
Index(
    "uq_report_contract_active",
    report_contract.c.market,
    report_contract.c.report_kind,
    unique=True,
    postgresql_where=report_contract.c.status == "active",
)

report_sync_run = Table(
    "report_sync_run", metadata,
    Column("run_id", Text, primary_key=True),
    Column("market", Text, nullable=False),
    Column("account_name", Text),
    Column("mode", Text, nullable=False),
    Column("requested_dates", JSONB, nullable=False),
    Column("actor", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("queued_at", DateTime(timezone=True), nullable=False),
    Column("started_at", DateTime(timezone=True)),
    Column("completed_at", DateTime(timezone=True)),
    Column("success_count", Integer, nullable=False, server_default="0"),
    Column("waiting_count", Integer, nullable=False, server_default="0"),
    Column("failed_count", Integer, nullable=False, server_default="0"),
    Column("error_code", Text),
    Column("error_summary", Text),
    Index("ix_report_sync_run_queue", "market", "status", "queued_at"),
)

report_schema = Table(
    "report_schema", metadata,
    Column("schema_id", Text, primary_key=True),
    Column("report_kind", Text, nullable=False),
    Column("header_fingerprint", Text, nullable=False),
    Column("ordered_headers", JSONB, nullable=False),
    Column("mapping_version", Integer, nullable=False),
    Column("unknown_headers", JSONB, nullable=False),
    Column("missing_required_headers", JSONB, nullable=False),
    Column("status", Text, nullable=False),
    Column("first_seen_at", DateTime(timezone=True), nullable=False),
    Column("approved_at", DateTime(timezone=True)),
    Column("approved_by", Text),
    UniqueConstraint(
        "report_kind",
        "header_fingerprint",
        name="uq_report_schema_fingerprint",
    ),
    Index("ix_report_schema_status", "report_kind", "status"),
)

report_file = Table(
    "report_file", metadata,
    Column("file_id", Text, primary_key=True),
    Column("run_id", Text, nullable=False),
    Column("contract_id", Text, nullable=False),
    Column("market", Text, nullable=False),
    Column("report_kind", Text, nullable=False),
    Column("report_date", Date, nullable=False),
    Column("version", Integer, nullable=False),
    Column("status", Text, nullable=False),
    Column("relative_path", Text),
    Column("sha256", Text),
    Column("byte_size", BigInteger),
    Column("row_count", BigInteger),
    Column("schema_id", Text),
    Column("is_current", Boolean, nullable=False, server_default="false"),
    Column("supersedes_file_id", Text),
    Column("downloaded_at", DateTime(timezone=True)),
    Column("sealed_at", DateTime(timezone=True)),
    Column("failure_code", Text),
    Column("failure_summary", Text),
    UniqueConstraint(
        "market",
        "report_kind",
        "report_date",
        "version",
        name="uq_report_file_version",
    ),
    Index(
        "ix_report_file_date_status",
        "market",
        "report_date",
        "status",
    ),
    Index("ix_report_file_run", "run_id"),
    Index("ix_report_file_sha", "sha256"),
)
Index(
    "uq_report_file_current",
    report_file.c.market,
    report_file.c.report_kind,
    report_file.c.report_date,
    unique=True,
    postgresql_where=report_file.c.is_current.is_(True),
)

report_raw_row = Table(
    "report_raw_row", metadata,
    Column("file_id", Text, primary_key=True),
    Column("row_number", BigInteger, primary_key=True),
    Column("row_payload", JSONB, nullable=False),
    Column("row_hash", Text, nullable=False),
    Index("ix_report_raw_row_hash", "row_hash"),
)

partner_attribution_daily = Table(
    "partner_attribution_daily", metadata,
    Column("file_id", Text, primary_key=True),
    Column("row_number", BigInteger, primary_key=True),
    Column("report_date", Date, nullable=False),
    Column("market", Text, nullable=False),
    Column("report_kind", Text, nullable=False),
    Column("campaign_id", Text),
    Column("campaign_name", Text),
    Column("campaign_duration", Text),
    Column("creator_handle", Text),
    Column("creator_oec_id", Text),
    Column("creator_identity_key", Text),
    Column("creator_level", Text),
    Column("creator_followers", BigInteger),
    Column("product_id", Text),
    Column("product_name", Text),
    Column("shop_code", Text),
    Column("shop_id", Text),
    Column("shop_name", Text),
    Column("category_level_1", Text),
    Column("category_level_2", Text),
    Column("creator_attributed_gmv", Numeric),
    Column("live_attributed_gmv", Numeric),
    Column("video_attributed_gmv", Numeric),
    Column("creator_attributed_orders", BigInteger),
    Column("live_attributed_orders", BigInteger),
    Column("video_attributed_orders", BigInteger),
    Column("direct_gmv", Numeric),
    Column("live_direct_gmv", Numeric),
    Column("video_direct_gmv", Numeric),
    Column("product_card_direct_gmv", Numeric),
    Column("creator_attributed_units", BigInteger),
    Column("direct_orders", BigInteger),
    Column("live_direct_orders", BigInteger),
    Column("video_direct_orders", BigInteger),
    Column("product_card_orders", BigInteger),
    Column("live_attributed_units", BigInteger),
    Column("video_attributed_units", BigInteger),
    Column("product_card_units", BigInteger),
    Column("direct_refund_gmv", Numeric),
    Column("refunded_units", BigInteger),
    Column("watch_click_rate", Numeric),
    Column("ctor_sku_order_rate", Numeric),
    Column("service_provider_creator_attributed_gmv", Numeric),
    Column("service_provider_creator_attributed_orders", BigInteger),
    Column("live_likes", BigInteger),
    Column("video_likes", BigInteger),
    Column("video_views", BigInteger),
    Column("live_views", BigInteger),
    Column("live_count", BigInteger),
    Column("video_count", BigInteger),
    Column("showcase_product_count", BigInteger),
    Column("estimated_partner_commission", Numeric),
    Column("actual_partner_commission", Numeric),
    Column("estimated_creator_commission", Numeric),
    Column("actual_creator_commission", Numeric),
    Column("refund_gmv", Numeric),
    Column("settled_gmv", Numeric),
    Column("showcase_gmv", Numeric),
    Column("extra_metrics", JSONB, nullable=False),
    Index(
        "ix_partner_attribution_daily_date",
        "market",
        "report_kind",
        "report_date",
    ),
    Index(
        "ix_partner_attribution_daily_creator",
        "market",
        "creator_oec_id",
        "report_date",
    ),
    Index(
        "ix_partner_attribution_identity",
        "market",
        "report_kind",
        "report_date",
        "creator_identity_key",
    ),
    Index(
        "ix_partner_attribution_daily_analysis_identity",
        "market",
        "report_kind",
        "creator_identity_key",
        "report_date",
    ),
)

creator_report_observation = Table(
    "creator_report_observation", metadata,
    Column("file_id", Text, primary_key=True),
    Column("creator_handle", Text, primary_key=True),
    Column("market", Text, nullable=False),
    Column("report_date", Date, nullable=False),
    Column("report_kind", Text, nullable=False),
    Column("oec_id", Text),
    Column("creator_level", Text),
    Column("followers", BigInteger),
    Column("identity_status", Text, nullable=False),
    Column(
        "candidate_oec_ids",
        JSONB,
        nullable=False,
        server_default=text("'[]'::jsonb"),
    ),
    Column("observed_at", DateTime(timezone=True), nullable=False),
    Index(
        "ix_creator_report_observation_latest",
        "market",
        "oec_id",
        "report_date",
    ),
    Index(
        "ix_creator_report_observation_handle",
        "market",
        "creator_handle",
    ),
)


report_creator_identity = Table(
    "report_creator_identity", metadata,
    Column("market", Text, primary_key=True),
    Column("identity_key", Text, primary_key=True),
    Column("oec_id", Text),
    Column("current_handle_key", Text),
    Column("display_handle", Text),
    Column("identity_status", Text, nullable=False),
    Column("classifiable", Boolean, nullable=False),
    Column(
        "candidate_oec_ids",
        JSONB,
        nullable=False,
        server_default=text("'[]'::jsonb"),
    ),
    Column("link_source", Text, nullable=False),
    Column("first_report_date", Date, nullable=False),
    Column("last_report_date", Date, nullable=False),
    Column("last_seen_at", DateTime(timezone=True), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint(
        "market",
        "identity_key",
        "classifiable",
        name="uq_report_creator_identity_classifiable",
    ),
    UniqueConstraint(
        "market",
        "oec_id",
        name="uq_report_creator_identity_oec",
    ),
    CheckConstraint(
        """
        (
            identity_status = 'confirmed_oec'
            and oec_id is not null
            and classifiable is true
            and identity_key = 'oec:' || oec_id
        )
        or (
            identity_status = 'handle_only'
            and oec_id is null
            and current_handle_key is not null
            and classifiable is true
            and identity_key = 'handle:' || current_handle_key
        )
        or (
            identity_status = 'ambiguous'
            and oec_id is null
            and current_handle_key is not null
            and classifiable is false
            and identity_key = 'handle:' || current_handle_key
            and jsonb_array_length(candidate_oec_ids) >= 2
        )
        """,
        name="ck_report_creator_identity_state",
    ),
    Index(
        "ix_report_creator_identity_seen",
        "market",
        "last_report_date",
    ),
    Index(
        "ix_report_creator_identity_status",
        "market",
        "identity_status",
    ),
    Index(
        "ix_report_creator_identity_classifiable_key",
        "market",
        "classifiable",
        "identity_key",
    ),
)

report_creator_identity_handle = Table(
    "report_creator_identity_handle", metadata,
    Column("market", Text, primary_key=True),
    Column("handle_key", Text, primary_key=True),
    Column("identity_key", Text, nullable=False),
    Column("display_handle", Text),
    Column("first_report_date", Date, nullable=False),
    Column("last_report_date", Date, nullable=False),
    Column("is_current", Boolean, nullable=False, server_default="true"),
    Column("mapping_status", Text, nullable=False),
    Column("conflict_first_report_date", Date),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    ForeignKeyConstraint(
        ["market", "identity_key"],
        [
            "report_creator_identity.market",
            "report_creator_identity.identity_key",
        ],
        name="fk_report_creator_identity_handle_identity",
    ),
    CheckConstraint(
        "mapping_status in ('confirmed','conflicted')",
        name="ck_report_creator_identity_handle_status",
    ),
)

report_creator_identity_event = Table(
    "report_creator_identity_event", metadata,
    Column("event_id", Text, primary_key=True),
    Column("market", Text, nullable=False),
    Column("handle_key", Text),
    Column("from_identity_key", Text),
    Column("to_identity_key", Text),
    Column("event_type", Text, nullable=False),
    Column(
        "candidate_oec_ids",
        JSONB,
        nullable=False,
        server_default=text("'[]'::jsonb"),
    ),
    Column("evidence_source", Text, nullable=False),
    Column("report_file_id", Text),
    Column("report_date", Date),
    Column("actor", Text, nullable=False),
    Column("occurred_at", DateTime(timezone=True), nullable=False),
    CheckConstraint(
        """
        event_type in (
            'handle_registered',
            'oec_confirmed',
            'conflict_detected',
            'reconciled'
        )
        """,
        name="ck_report_creator_identity_event_type",
    ),
    Index(
        "ix_report_creator_identity_event_handle",
        "market",
        "handle_key",
        "occurred_at",
    ),
)

creator_classification = Table(
    "creator_classification", metadata,
    Column("classification_id", Text, primary_key=True),
    Column("market", Text, nullable=False),
    Column("name", Text, nullable=False),
    Column("name_key", Text, nullable=False),
    Column("color", Text, nullable=False),
    Column("sort_order", Integer, nullable=False),
    Column("description", Text),
    Column("active", Boolean, nullable=False, server_default="true"),
    Column("created_by", Text, nullable=False),
    Column("updated_by", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint(
        "market",
        "classification_id",
        name="uq_creator_classification_market_id",
    ),
    UniqueConstraint(
        "market",
        "name_key",
        name="uq_creator_classification_name",
    ),
    Index(
        "ix_creator_classification_active",
        "market",
        "active",
        "sort_order",
    ),
)

creator_classification_assignment = Table(
    "creator_classification_assignment", metadata,
    Column("market", Text, primary_key=True),
    Column("identity_key", Text, primary_key=True),
    # 一位达人可以同时拥有多个团队人工标签；classification_id 是归属行的一部分。
    Column("classification_id", Text, primary_key=True),
    Column("oec_id", Text),
    Column("handle_key", Text),
    Column("handle_snapshot", Text),
    Column(
        "classifiable_guard",
        Boolean,
        nullable=False,
        server_default="true",
    ),
    Column("note", Text),
    Column("source", Text, nullable=False),
    Column("assigned_by", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    ForeignKeyConstraint(
        ["market", "classification_id"],
        [
            "creator_classification.market",
            "creator_classification.classification_id",
        ],
        name="fk_creator_classification_assignment_classification",
    ),
    ForeignKeyConstraint(
        ["market", "identity_key", "classifiable_guard"],
        [
            "report_creator_identity.market",
            "report_creator_identity.identity_key",
            "report_creator_identity.classifiable",
        ],
        name="fk_creator_classification_assignment_identity",
    ),
    CheckConstraint(
        "classifiable_guard is true",
        name="ck_creator_classification_assignment_guard",
    ),
    CheckConstraint(
        "identity_key like 'oec:%' or identity_key like 'handle:%'",
        name="ck_creator_classification_assignment_key",
    ),
    Index(
        "ix_creator_classification_assignment_lookup",
        "market",
        "classification_id",
        "identity_key",
    ),
)

creator_classification_event = Table(
    "creator_classification_event", metadata,
    Column("event_id", Text, primary_key=True),
    Column("market", Text, nullable=False),
    Column("identity_key", Text, nullable=False),
    Column("from_classification_id", Text),
    Column("to_classification_id", Text),
    Column("event_type", Text, nullable=False),
    Column("source", Text, nullable=False),
    Column("import_id", Text),
    Column("actor", Text, nullable=False),
    Column("note", Text),
    Column("occurred_at", DateTime(timezone=True), nullable=False),
    CheckConstraint(
        """
        event_type in (
            'assign',
            'change',
            'clear',
            'reconcile',
            'identity_quarantined'
        )
        """,
        name="ck_creator_classification_event_type",
    ),
    Index(
        "ix_creator_classification_event_identity",
        "market",
        "identity_key",
        "occurred_at",
    ),
    Index("ix_creator_classification_event_import", "import_id"),
)

creator_classification_definition_event = Table(
    "creator_classification_definition_event", metadata,
    Column("event_id", Text, primary_key=True),
    Column("market", Text, nullable=False),
    Column("classification_id", Text, nullable=False),
    Column("event_type", Text, nullable=False),
    Column("before_payload", JSONB),
    Column("after_payload", JSONB),
    Column("actor", Text, nullable=False),
    Column("occurred_at", DateTime(timezone=True), nullable=False),
    CheckConstraint(
        """
        event_type in (
            'create',
            'rename',
            'recolor',
            'reorder',
            'activate',
            'deactivate',
            'delete',
            'update'
        )
        """,
        name="ck_creator_classification_definition_event_type",
    ),
    Index(
        "ix_creator_classification_definition_event",
        "market",
        "classification_id",
        "occurred_at",
    ),
)

classification_import_batch = Table(
    "classification_import_batch", metadata,
    Column("import_id", Text, primary_key=True),
    Column("market", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("file_name", Text, nullable=False),
    Column("sha256", Text, nullable=False),
    Column("row_count", BigInteger, nullable=False),
    Column("matched_count", BigInteger, nullable=False, server_default="0"),
    Column("unchanged_count", BigInteger, nullable=False, server_default="0"),
    Column("conflict_count", BigInteger, nullable=False, server_default="0"),
    Column("ambiguous_count", BigInteger, nullable=False, server_default="0"),
    Column("unknown_count", BigInteger, nullable=False, server_default="0"),
    Column("warning_count", BigInteger, nullable=False, server_default="0"),
    Column("skipped_count", BigInteger, nullable=False, server_default="0"),
    Column("invalid_count", BigInteger, nullable=False, server_default="0"),
    Column(
        "new_classification_names",
        JSONB,
        nullable=False,
        server_default=text("'[]'::jsonb"),
    ),
    Column(
        "replace_conflicts",
        Boolean,
        nullable=False,
        server_default="false",
    ),
    Column(
        "create_missing_classifications",
        Boolean,
        nullable=False,
        server_default="false",
    ),
    Column("created_by", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("started_at", DateTime(timezone=True)),
    Column("heartbeat_at", DateTime(timezone=True)),
    Column("attempt_id", Text),
    Column("lease_expires_at", DateTime(timezone=True)),
    Column("attempt_count", Integer, nullable=False, server_default="0"),
    Column("applied_at", DateTime(timezone=True)),
    Column("failure_summary", Text),
    Column("result_summary", JSONB),
    CheckConstraint(
        """
        status in (
            'previewed',
            'queued',
            'applying',
            'applied',
            'failed',
            'expired'
        )
        """,
        name="ck_classification_import_batch_status",
    ),
)

classification_import_row = Table(
    "classification_import_row", metadata,
    Column("import_id", Text, primary_key=True),
    Column("row_number", BigInteger, primary_key=True),
    Column("status", Text, nullable=False),
    Column("identity_key", Text),
    Column("oec_id", Text),
    Column("handle_key", Text),
    Column("requested_classification_name", Text),
    Column("resolved_classification_id", Text),
    Column("current_classification_id", Text),
    Column("note", Text),
    Column("error_code", Text),
    ForeignKeyConstraint(
        ["import_id"],
        ["classification_import_batch.import_id"],
        name="fk_classification_import_row_batch",
        ondelete="CASCADE",
    ),
    CheckConstraint(
        """
        status in (
            'matched_new',
            'matched_changed',
            'matched_handle_oec_unverified',
            'unchanged',
            'conflict',
            'identity_ambiguous',
            'unknown_creator',
            'unknown_classification',
            'duplicate_same',
            'duplicate_conflict',
            'skipped_unmatched',
            'skipped_blank',
            'invalid'
        )
        """,
        name="ck_classification_import_row_status",
    ),
    Index(
        "ix_classification_import_row_status",
        "import_id",
        "status",
        "row_number",
    ),
)

# 商家关系是商品推广的第一层业务门禁；Campaign 与样品额度只属于后续执行状态。
merchant = Table(
    "merchant", metadata,
    Column("bd_market", Text, primary_key=True),
    Column("seller_id", Text, primary_key=True),
    Column("shop_name", Text, nullable=False),
    Column("relationship_status", Text, nullable=False, server_default="unknown"),
    Column("relationship_source", Text, nullable=False, server_default="manual"),
    Column("primary_category", Text),
    Column("merchant_grade", Text),
    Column("rating", Numeric),
    Column("sales", BigInteger),
    Column("owner", Text),
    Column("email", Text),
    Column("whatsapp", Text),
    Column("phone", Text),
    Column("country_code", Text),
    Column("notes", Text),
    Column("source_file", Text),
    Column("created_by", Text, nullable=False),
    Column("updated_by", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    CheckConstraint(
        "bd_market in ('mx','br','uk','it','jp','us','de')",
        name="ck_merchant_market",
    ),
    CheckConstraint(
        "seller_id ~ '^[0-9]{10,32}$'",
        name="ck_merchant_seller_id",
    ),
    CheckConstraint(
        "relationship_status in ('connected','not_connected','unknown')",
        name="ck_merchant_relationship_status",
    ),
    CheckConstraint(
        "merchant_grade is null or merchant_grade in ('S','A','B','C')",
        name="ck_merchant_grade",
    ),
    CheckConstraint(
        "rating is null or (rating >= 0 and rating <= 5)",
        name="ck_merchant_rating",
    ),
    CheckConstraint("sales is null or sales >= 0", name="ck_merchant_sales"),
    Index(
        "ix_merchant_relationship",
        "bd_market",
        "relationship_status",
        "updated_at",
    ),
    Index("ix_merchant_owner", "bd_market", "owner", "updated_at"),
    Index(
        "ix_merchant_category",
        "bd_market",
        "primary_category",
        "merchant_grade",
    ),
)

merchant_event = Table(
    "merchant_event", metadata,
    Column("event_id", Text, primary_key=True),
    Column("bd_market", Text, nullable=False),
    Column("seller_id", Text, nullable=False),
    Column("event_type", Text, nullable=False),
    Column("before_payload", JSONB),
    Column("after_payload", JSONB),
    Column("source", Text, nullable=False),
    Column("import_id", Text),
    Column("actor", Text, nullable=False),
    Column("occurred_at", DateTime(timezone=True), nullable=False),
    CheckConstraint(
        "event_type in ('create','update','import_insert','import_update')",
        name="ck_merchant_event_type",
    ),
    Index(
        "ix_merchant_event_timeline",
        "bd_market",
        "seller_id",
        "occurred_at",
    ),
)

merchant_import_batch = Table(
    "merchant_import_batch", metadata,
    Column("import_id", Text, primary_key=True),
    Column("bd_market", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("file_name", Text, nullable=False),
    Column("sha256", Text, nullable=False),
    Column("row_count", BigInteger, nullable=False),
    Column("insert_count", BigInteger, nullable=False, server_default="0"),
    Column("update_count", BigInteger, nullable=False, server_default="0"),
    Column("unchanged_count", BigInteger, nullable=False, server_default="0"),
    Column("protected_count", BigInteger, nullable=False, server_default="0"),
    Column("error_count", BigInteger, nullable=False, server_default="0"),
    Column("created_by", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("applied_at", DateTime(timezone=True)),
    Column("result_summary", JSONB),
    CheckConstraint(
        "bd_market in ('mx','br','uk','it','jp','us','de')",
        name="ck_merchant_import_batch_market",
    ),
    CheckConstraint(
        "status in ('previewed','applied','failed','expired')",
        name="ck_merchant_import_batch_status",
    ),
)

merchant_import_row = Table(
    "merchant_import_row", metadata,
    Column("import_id", Text, primary_key=True),
    Column("row_number", BigInteger, primary_key=True),
    Column("status", Text, nullable=False),
    Column("seller_id", Text),
    Column("payload", JSONB),
    Column("error_code", Text),
    ForeignKeyConstraint(
        ["import_id"],
        ["merchant_import_batch.import_id"],
        name="fk_merchant_import_row_batch",
        ondelete="CASCADE",
    ),
    CheckConstraint(
        "status in ('insert','update','unchanged','protected_connected',"
        "'duplicate_same','duplicate_conflict','invalid')",
        name="ck_merchant_import_row_status",
    ),
    Index(
        "ix_merchant_import_row_status",
        "import_id",
        "status",
        "row_number",
    ),
)

# 推广网站只复用一个确定的 ShareLink Plan；普通高佣链接记录仍保持逐次创建。
promotion_sharelink_binding = Table(
    "promotion_sharelink_binding", metadata,
    Column("bd_market", Text, primary_key=True),
    Column("pid", Text, primary_key=True),
    Column("plan_record_id", Text, nullable=False),
    Column("status", Text, nullable=False, server_default="active"),
    Column("source", Text, nullable=False, server_default="promotion_site"),
    Column("created_by", Text, nullable=False),
    Column("updated_by", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("last_verified_at", DateTime(timezone=True)),
    ForeignKeyConstraint(
        ["plan_record_id"],
        ["share_link_plan.plan_record_id"],
        name="fk_promotion_sharelink_plan",
    ),
    CheckConstraint(
        "bd_market in ('mx','br','uk','it','jp','us','de')",
        name="ck_promotion_sharelink_market",
    ),
    CheckConstraint(
        "pid ~ '^[0-9]{10,32}$'",
        name="ck_promotion_sharelink_pid",
    ),
    CheckConstraint(
        "status in ('active','needs_review','result_unknown','disabled')",
        name="ck_promotion_sharelink_status",
    ),
    Index(
        "ix_promotion_sharelink_status",
        "bd_market",
        "status",
        "updated_at",
    ),
)

promotion_catalog_product = Table(
    "promotion_catalog_product", metadata,
    Column("bd_market", Text, primary_key=True),
    Column("catalog_key", Text, primary_key=True),
    Column("pid", Text, primary_key=True),
    Column("seller_id", Text, nullable=False),
    Column("campaign_id", Text),
    Column("status", Text, nullable=False, server_default="active"),
    Column("source_release_id", Text, nullable=False),
    Column("created_by", Text, nullable=False),
    Column("updated_by", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    CheckConstraint("bd_market = 'mx'", name="ck_promotion_catalog_market"),
    CheckConstraint(
        "catalog_key in ('viral','fashion','potential')",
        name="ck_promotion_catalog_key",
    ),
    CheckConstraint(
        "pid ~ '^[0-9]{10,32}$'",
        name="ck_promotion_catalog_pid",
    ),
    CheckConstraint(
        "seller_id ~ '^[0-9]{10,32}$'",
        name="ck_promotion_catalog_seller",
    ),
    CheckConstraint(
        "status in ('active','archived')",
        name="ck_promotion_catalog_status",
    ),
    Index("ix_promotion_catalog_pid", "bd_market", "pid", "status"),
)

sample_confirmation_rule = Table(
    "sample_confirmation_rule", metadata,
    Column("rule_id", Text, primary_key=True),
    Column("bd_market", Text, nullable=False),
    Column("scope_type", Text, nullable=False),
    Column("scope_value", Text, nullable=False),
    Column("requires_confirmation", Boolean, nullable=False),
    Column("status", Text, nullable=False, server_default="active"),
    Column("notes", Text),
    Column("created_by", Text, nullable=False),
    Column("updated_by", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint(
        "bd_market",
        "scope_type",
        "scope_value",
        name="uq_sample_confirmation_scope",
    ),
    CheckConstraint(
        "bd_market in ('mx','br','uk','it','jp','us','de')",
        name="ck_sample_confirmation_market",
    ),
    CheckConstraint(
        "scope_type in ('campaign','pid')",
        name="ck_sample_confirmation_scope",
    ),
    CheckConstraint(
        "status in ('active','inactive')",
        name="ck_sample_confirmation_status",
    ),
)

sample_sync_run = Table(
    "sample_sync_run", metadata,
    Column("run_id", Text, primary_key=True),
    Column("bd_market", Text, nullable=False),
    Column("trigger_type", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("created_by", Text, nullable=False),
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("finished_at", DateTime(timezone=True)),
    Column("new_count", BigInteger, nullable=False, server_default="0"),
    Column("updated_count", BigInteger, nullable=False, server_default="0"),
    Column("error_count", BigInteger, nullable=False, server_default="0"),
    Column("error_code", Text),
    Column("result_summary", JSONB),
    CheckConstraint("bd_market = 'mx'", name="ck_sample_sync_market"),
    CheckConstraint(
        "trigger_type in ('scheduled','manual')",
        name="ck_sample_sync_trigger",
    ),
    CheckConstraint(
        "status in ('running','succeeded','failed')",
        name="ck_sample_sync_status",
    ),
    Index(
        "ix_sample_sync_run_status",
        "bd_market",
        "status",
        "started_at",
    ),
)


sample_creator_eligibility = Table(
    "sample_creator_eligibility", metadata,
    Column("bd_market", Text, primary_key=True),
    Column("creator_oec_id", Text, primary_key=True),
    Column("creator_handle", Text),
    Column("status", Text, nullable=False, server_default="active"),
    Column("notes", Text),
    Column("created_by", Text, nullable=False),
    Column("updated_by", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    CheckConstraint("bd_market = 'mx'", name="ck_sample_creator_eligibility_market"),
    CheckConstraint(
        "creator_oec_id ~ '^[0-9]{10,32}$'",
        name="ck_sample_creator_eligibility_oec",
    ),
    CheckConstraint(
        "status in ('active','inactive')",
        name="ck_sample_creator_eligibility_status",
    ),
    Index("ix_sample_creator_eligibility_status", "bd_market", "status", "updated_at"),
)


sample_creator_eligibility_event = Table(
    "sample_creator_eligibility_event", metadata,
    Column("event_id", Text, primary_key=True),
    Column("bd_market", Text, nullable=False),
    Column("creator_oec_id", Text, nullable=False),
    Column("creator_handle", Text),
    Column("source_type", Text, nullable=False),
    Column("event_type", Text, nullable=False),
    Column("actor", Text, nullable=False),
    Column("notes", Text),
    Column("occurred_at", DateTime(timezone=True), nullable=False),
    CheckConstraint("bd_market = 'mx'", name="ck_sample_creator_event_market"),
    CheckConstraint(
        "source_type in ('manual_non_mcn','mcn_relation')",
        name="ck_sample_creator_event_source",
    ),
    CheckConstraint(
        "event_type in ('activated','deactivated','source_observed')",
        name="ck_sample_creator_event_type",
    ),
    Index(
        "ix_sample_creator_event_identity",
        "bd_market", "creator_oec_id", "occurred_at",
    ),
)


sample_workflow_import_batch = Table(
    "sample_workflow_import_batch", metadata,
    Column("import_id", Text, primary_key=True),
    Column("bd_market", Text, nullable=False),
    Column("import_type", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("file_name", Text, nullable=False),
    Column("sha256", Text, nullable=False),
    Column("row_count", BigInteger, nullable=False),
    Column("insert_count", BigInteger, nullable=False, server_default="0"),
    Column("update_count", BigInteger, nullable=False, server_default="0"),
    Column("unchanged_count", BigInteger, nullable=False, server_default="0"),
    Column("remove_count", BigInteger, nullable=False, server_default="0"),
    Column("conflict_count", BigInteger, nullable=False, server_default="0"),
    Column("error_count", BigInteger, nullable=False, server_default="0"),
    Column("import_metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    Column("created_by", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("applied_at", DateTime(timezone=True)),
    Column("result_summary", JSONB),
    CheckConstraint("bd_market = 'mx'", name="ck_sample_workflow_import_market"),
    CheckConstraint(
        "import_type in ('creator_eligibility','official_product_form','product_catalog')",
        name="ck_sample_workflow_import_type",
    ),
    CheckConstraint(
        "status in ('previewed','applying','applied','expired','failed')",
        name="ck_sample_workflow_import_status",
    ),
    CheckConstraint(
        "row_count >= 0 and insert_count >= 0 and update_count >= 0 and "
        "unchanged_count >= 0 and remove_count >= 0 and "
        "conflict_count >= 0 and error_count >= 0",
        name="ck_sample_workflow_import_counts",
    ),
    Index(
        "ix_sample_workflow_import_type_time",
        "bd_market", "import_type", "created_at",
    ),
)


sample_workflow_import_row = Table(
    "sample_workflow_import_row", metadata,
    Column("import_id", Text, primary_key=True),
    Column("row_number", BigInteger, primary_key=True),
    Column("status", Text, nullable=False),
    Column("entity_key", Text),
    Column("payload", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    Column("current_payload", JSONB),
    Column("error_code", Text),
    ForeignKeyConstraint(
        ["import_id"],
        ["sample_workflow_import_batch.import_id"],
        name="fk_sample_workflow_import_row_batch",
    ),
    CheckConstraint(
        "status in ('insert','update','unchanged','remove','conflict','invalid')",
        name="ck_sample_workflow_import_row_status",
    ),
    Index("ix_sample_workflow_import_row_status", "import_id", "status", "row_number"),
)


product_catalog_version = Table(
    "product_catalog_version", metadata,
    Column("version_id", Text, primary_key=True),
    Column("bd_market", Text, nullable=False),
    Column("catalog_key", Text, nullable=False),
    Column("version_label", Text, nullable=False),
    Column("source_name", Text, nullable=False),
    Column("source_sha256", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("row_count", BigInteger, nullable=False),
    Column("created_by", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("activated_at", DateTime(timezone=True)),
    CheckConstraint("bd_market = 'mx'", name="ck_product_catalog_version_market"),
    CheckConstraint(
        "catalog_key in ('viral','opportunity')",
        name="ck_product_catalog_version_key",
    ),
    CheckConstraint(
        "status in ('active','archived')",
        name="ck_product_catalog_version_status",
    ),
    UniqueConstraint(
        "bd_market", "catalog_key", "version_label",
        name="uq_product_catalog_version_label",
    ),
    Index(
        "uq_product_catalog_version_active",
        "bd_market", "catalog_key",
        unique=True,
        postgresql_where=text("status = 'active'"),
    ),
)


product_catalog_item = Table(
    "product_catalog_item", metadata,
    Column("version_id", Text, primary_key=True),
    Column("pid", Text, primary_key=True),
    Column("seller_id", Text),
    Column("source_pool_key", Text),
    Column("product_name", Text),
    Column("image_url", Text),
    Column("category", Text),
    Column("created_at", DateTime(timezone=True), nullable=False),
    ForeignKeyConstraint(
        ["version_id"], ["product_catalog_version.version_id"],
        name="fk_product_catalog_item_version",
    ),
    CheckConstraint("pid ~ '^[0-9]{10,32}$'", name="ck_product_catalog_item_pid"),
    CheckConstraint(
        "seller_id is null or seller_id ~ '^[0-9]{10,32}$'",
        name="ck_product_catalog_item_seller",
    ),
    CheckConstraint(
        "source_pool_key is null or source_pool_key in "
        "('full_managed','campaign','clue','durable','other')",
        name="ck_product_catalog_source_pool",
    ),
    Index("ix_product_catalog_item_pid", "pid", "version_id"),
    Index("ix_product_catalog_source_pool", "source_pool_key", "pid"),
)


product_source_pool_version = Table(
    "product_source_pool_version", metadata,
    Column("version_id", Text, primary_key=True),
    Column("bd_market", Text, nullable=False),
    Column("pool_key", Text, nullable=False),
    Column("version_label", Text, nullable=False),
    Column("source_name", Text, nullable=False),
    Column("source_sha256", Text, nullable=False),
    Column("source_metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    Column("status", Text, nullable=False),
    Column("row_count", BigInteger, nullable=False),
    Column("created_by", Text, nullable=False),
    Column("activated_by", Text),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("activated_at", DateTime(timezone=True)),
    CheckConstraint("bd_market = 'mx'", name="ck_product_source_pool_market"),
    CheckConstraint(
        "pool_key in ('full_managed','clue','campaign','durable','other')",
        name="ck_product_source_pool_key",
    ),
    CheckConstraint(
        "status in ('draft','active','archived','failed')",
        name="ck_product_source_pool_status",
    ),
    CheckConstraint("row_count >= 0", name="ck_product_source_pool_count"),
    UniqueConstraint(
        "bd_market", "pool_key", "version_label",
        name="uq_product_source_pool_label",
    ),
    Index(
        "uq_product_source_pool_active_campaign",
        "bd_market", "pool_key",
        unique=True,
        postgresql_where=text(
            "status = 'active' and pool_key = 'campaign'"
        ),
    ),
    Index(
        "ix_product_source_pool_time",
        "bd_market", "pool_key", "created_at",
    ),
)


product_source_pool_item = Table(
    "product_source_pool_item", metadata,
    Column("version_id", Text, primary_key=True),
    Column("pid", Text, primary_key=True),
    Column("product_name", Text),
    Column("image_url", Text),
    Column("shop_name", Text),
    Column("seller_id", Text),
    Column("category", Text),
    Column("category_l1", Text),
    Column("category_l2", Text),
    Column("category_l3", Text),
    Column("canonical_category_l1", Text, nullable=False, server_default="待归类"),
    Column("category_mapping_method", Text, nullable=False, server_default="unclassified"),
    Column("category_rule_version", Text, nullable=False, server_default="mx-canonical-v1"),
    Column("product_url", Text),
    Column("price_min", Numeric),
    Column("price_max", Numeric),
    Column("price_currency", Text),
    Column("list_price_text", Text),
    Column("sale_price_text", Text),
    Column("stock_quantity", BigInteger),
    Column("source_tags", JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    Column("source_refs", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    Column("source_attributes", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    Column("created_at", DateTime(timezone=True), nullable=False),
    ForeignKeyConstraint(
        ["version_id"], ["product_source_pool_version.version_id"],
        name="fk_product_source_pool_item_version",
    ),
    CheckConstraint(
        "pid ~ '^[0-9]{10,32}$'",
        name="ck_product_source_pool_item_pid",
    ),
    CheckConstraint(
        "seller_id is null or seller_id ~ '^[0-9]{10,32}$'",
        name="ck_product_source_pool_item_seller",
    ),
    CheckConstraint(
        "(price_min is null or price_min >= 0) and "
        "(price_max is null or price_max >= 0) and "
        "(price_min is null or price_max is null or price_max >= price_min)",
        name="ck_product_source_pool_price",
    ),
    CheckConstraint(
        "stock_quantity is null or stock_quantity >= 0",
        name="ck_product_source_pool_stock",
    ),
    CheckConstraint(
        "category_mapping_method in ("
        "'source_full_managed','source_alias','title_rule','unclassified',"
        "'ai_classified'"
        ")",
        name="ck_product_source_pool_category_method",
    ),
    CheckConstraint(
        "length(trim(canonical_category_l1)) > 0 and "
        "length(trim(category_rule_version)) > 0",
        name="ck_product_source_pool_canonical_category",
    ),
    Index("ix_product_source_pool_item_pid", "pid", "version_id"),
    Index(
        "ix_product_source_pool_item_category",
        "category_l1", "category_l2", "category_l3",
    ),
    Index("ix_product_source_pool_item_price", "price_min", "price_max"),
    Index(
        "ix_product_source_pool_item_canonical_category",
        "canonical_category_l1", "version_id",
    ),
)


promotion_site_page = Table(
    "promotion_site_page", metadata,
    Column("page_id", Text, primary_key=True),
    Column("bd_market", Text, nullable=False),
    Column("page_name", Text, nullable=False),
    Column("slug", Text, nullable=False),
    Column("page_type", Text, nullable=False),
    Column("status", Text, nullable=False, server_default="active"),
    Column("sort_order", BigInteger, nullable=False, server_default="0"),
    Column("created_by", Text, nullable=False),
    Column("updated_by", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    CheckConstraint("bd_market = 'mx'", name="ck_promotion_site_page_market"),
    CheckConstraint(
        "page_type in ('full_managed','campaign')",
        name="ck_promotion_site_page_type",
    ),
    CheckConstraint(
        "status in ('active','inactive')",
        name="ck_promotion_site_page_status",
    ),
    CheckConstraint(
        "slug ~ '^[a-z0-9][a-z0-9-]{1,79}$'",
        name="ck_promotion_site_page_slug",
    ),
    UniqueConstraint("bd_market", "slug", name="uq_promotion_site_page_slug"),
    Index(
        "uq_promotion_site_page_full_managed",
        "bd_market", "page_type",
        unique=True,
        postgresql_where=text("page_type = 'full_managed'"),
    ),
    Index(
        "ix_promotion_site_page_order",
        "bd_market", "status", "sort_order", "page_id",
    ),
)


promotion_site_release = Table(
    "promotion_site_release", metadata,
    Column("release_id", Text, primary_key=True),
    Column("bd_market", Text, nullable=False),
    Column("version_label", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("payload", JSONB, nullable=False),
    Column("source_sha256", Text, nullable=False),
    Column("page_count", BigInteger, nullable=False),
    Column("product_count", BigInteger, nullable=False),
    Column("validation_summary", JSONB, nullable=False),
    Column("remote_release_id", Text),
    Column("created_by", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("staged_at", DateTime(timezone=True)),
    Column("activated_at", DateTime(timezone=True)),
    CheckConstraint(
        "bd_market = 'mx'", name="ck_promotion_site_release_market",
    ),
    CheckConstraint(
        "status in ('staged','production','archived','failed')",
        name="ck_promotion_site_release_status",
    ),
    CheckConstraint(
        "page_count >= 0 and product_count >= 0",
        name="ck_promotion_site_release_counts",
    ),
    UniqueConstraint(
        "bd_market", "version_label",
        name="uq_promotion_site_release_label",
    ),
    Index(
        "uq_promotion_site_release_production",
        "bd_market",
        unique=True,
        postgresql_where=text("status = 'production'"),
    ),
    Index(
        "ix_promotion_site_release_status",
        "bd_market", "status", "created_at",
    ),
)


promotion_site_draft = Table(
    "promotion_site_draft", metadata,
    Column("draft_id", Text, primary_key=True),
    Column("bd_market", Text, nullable=False),
    Column("base_release_id", Text),
    Column("status", Text, nullable=False, server_default="open"),
    Column("revision", BigInteger, nullable=False, server_default="1"),
    Column("created_by", Text, nullable=False),
    Column("updated_by", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    ForeignKeyConstraint(
        ["base_release_id"], ["promotion_site_release.release_id"],
        name="fk_promotion_site_draft_release",
    ),
    CheckConstraint("bd_market = 'mx'", name="ck_promotion_site_draft_market"),
    CheckConstraint(
        "status in ('open','released','discarded')",
        name="ck_promotion_site_draft_status",
    ),
    CheckConstraint("revision >= 1", name="ck_promotion_site_draft_revision"),
    Index(
        "uq_promotion_site_draft_open",
        "bd_market",
        unique=True,
        postgresql_where=text("status = 'open'"),
    ),
)


promotion_site_draft_item = Table(
    "promotion_site_draft_item", metadata,
    Column("draft_id", Text, primary_key=True),
    Column("page_id", Text, primary_key=True),
    Column("pid", Text, primary_key=True),
    Column("source_pool_key", Text, nullable=False),
    Column("category_key", Text),
    Column("sort_order", BigInteger, nullable=False),
    Column("added_by", Text, nullable=False),
    Column("added_at", DateTime(timezone=True), nullable=False),
    ForeignKeyConstraint(
        ["draft_id"], ["promotion_site_draft.draft_id"],
        name="fk_promotion_site_draft_item_draft",
        ondelete="CASCADE",
    ),
    ForeignKeyConstraint(
        ["page_id"], ["promotion_site_page.page_id"],
        name="fk_promotion_site_draft_item_page",
    ),
    CheckConstraint(
        "pid ~ '^[0-9]{10,32}$'",
        name="ck_promotion_site_draft_item_pid",
    ),
    CheckConstraint(
        "source_pool_key in ('full_managed','campaign')",
        name="ck_promotion_site_draft_item_source",
    ),
    CheckConstraint(
        "sort_order >= 0", name="ck_promotion_site_draft_item_order",
    ),
    Index("ix_promotion_site_draft_item_pid", "draft_id", "pid", "page_id"),
    Index(
        "ix_promotion_site_draft_item_page_order",
        "draft_id", "page_id", "sort_order", "pid",
    ),
)


promotion_site_sharelink_batch = Table(
    "promotion_site_sharelink_batch", metadata,
    Column("batch_id", Text, primary_key=True),
    Column("draft_id", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("account_name", Text, nullable=False),
    Column("account_names", JSONB, nullable=False),
    Column("revision", BigInteger, nullable=False, server_default="1"),
    Column("created_by", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("confirmed_at", DateTime(timezone=True)),
    Column("finished_at", DateTime(timezone=True)),
    ForeignKeyConstraint(
        ["draft_id"], ["promotion_site_draft.draft_id"],
        name="fk_promotion_site_sharelink_batch_draft",
    ),
    CheckConstraint(
        "status in ('previewing','ready','running','completed',"
        "'completed_with_errors','needs_review','failed','cancelled')",
        name="ck_promotion_site_sharelink_batch_status",
    ),
    CheckConstraint(
        "revision >= 1", name="ck_promotion_site_sharelink_batch_revision",
    ),
    CheckConstraint(
        "jsonb_typeof(account_names) = 'array' and "
        "jsonb_array_length(account_names) between 1 and 3",
        name="ck_promotion_site_sharelink_batch_accounts",
    ),
    Index(
        "uq_promotion_site_sharelink_batch_active",
        "draft_id",
        unique=True,
        postgresql_where=text("status in ('previewing','ready','running')"),
    ),
    Index(
        "ix_promotion_site_sharelink_batch_status",
        "status", "updated_at", "batch_id",
    ),
)


promotion_site_sharelink_item = Table(
    "promotion_site_sharelink_item", metadata,
    Column("batch_id", Text, primary_key=True),
    Column("pid", Text, primary_key=True),
    Column("sort_order", BigInteger, nullable=False),
    Column("status", Text, nullable=False),
    Column("job_id", Text),
    Column("record_id", Text),
    Column("account_name", Text),
    Column("error_code", Text),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    ForeignKeyConstraint(
        ["batch_id"], ["promotion_site_sharelink_batch.batch_id"],
        name="fk_promotion_site_sharelink_item_batch", ondelete="CASCADE",
    ),
    ForeignKeyConstraint(
        ["job_id"], ["share_link_job.job_id"],
        name="fk_promotion_site_sharelink_item_job",
    ),
    ForeignKeyConstraint(
        ["record_id"], ["share_link_record.record_id"],
        name="fk_promotion_site_sharelink_item_record",
    ),
    CheckConstraint(
        "pid ~ '^[0-9]{10,32}$'",
        name="ck_promotion_site_sharelink_item_pid",
    ),
    CheckConstraint(
        "sort_order >= 1", name="ck_promotion_site_sharelink_item_order",
    ),
    CheckConstraint(
        "status in ('pending_preview','previewing','ready','generating',"
        "'succeeded','ineligible','failed','result_unknown','cancelled')",
        name="ck_promotion_site_sharelink_item_status",
    ),
    Index(
        "ix_promotion_site_sharelink_item_status",
        "batch_id", "status", "sort_order", "pid",
    ),
    Index(
        "ix_promotion_site_sharelink_item_account",
        "batch_id", "account_name", "status", "sort_order", "pid",
    ),
)


promotion_site_sample_request = Table(
    "promotion_site_sample_request", metadata,
    Column("request_id", Text, primary_key=True),
    Column("bd_market", Text, nullable=False),
    Column("creator_handle", Text, nullable=False),
    Column("creator_handle_key", Text, nullable=False),
    Column("pid", Text, nullable=False),
    Column("release_id", Text, nullable=False),
    Column("source_pool_key", Text, nullable=False, server_default="full_managed"),
    Column("page_slug", Text, nullable=False, server_default="tiktok-oficial"),
    Column("submitted_at", DateTime(timezone=True), nullable=False),
    Column("synced_at", DateTime(timezone=True), nullable=False),
    CheckConstraint("bd_market = 'mx'", name="ck_promotion_site_sample_request_market"),
    CheckConstraint(
        "creator_handle ~ '^[A-Za-z0-9._]{2,24}$'",
        name="ck_promotion_site_sample_request_handle",
    ),
    CheckConstraint(
        "creator_handle_key = lower(creator_handle_key)",
        name="ck_promotion_site_sample_request_handle_key",
    ),
    CheckConstraint(
        "pid ~ '^[0-9]{10,32}$'",
        name="ck_promotion_site_sample_request_pid",
    ),
    CheckConstraint(
        "source_pool_key in ('full_managed','campaign')",
        name="ck_promotion_site_sample_request_source_pool",
    ),
    Index(
        "ix_promotion_site_sample_request_time",
        "submitted_at", "request_id",
    ),
    Index(
        "ix_promotion_site_sample_request_handle",
        "creator_handle_key", "submitted_at",
    ),
)


promotion_site_sample_sync_state = Table(
    "promotion_site_sample_sync_state", metadata,
    Column("bd_market", Text, primary_key=True),
    Column("remote_origin", Text, nullable=False),
    Column("cursor_submitted_at", DateTime(timezone=True)),
    Column("cursor_request_id", Text),
    Column("last_synced_at", DateTime(timezone=True)),
    Column("last_error", Text),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    CheckConstraint(
        "bd_market = 'mx'", name="ck_promotion_site_sample_sync_state_market",
    ),
)


website_catalog_release = Table(
    "website_catalog_release", metadata,
    Column("release_id", Text, primary_key=True),
    Column("bd_market", Text, nullable=False),
    Column("catalog_key", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("payload", JSONB, nullable=False),
    Column("source_sha256", Text, nullable=False),
    Column("product_count", BigInteger, nullable=False),
    Column("validation_summary", JSONB, nullable=False),
    Column("remote_release_id", Text),
    Column("created_by", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("staged_at", DateTime(timezone=True)),
    Column("activated_at", DateTime(timezone=True)),
    CheckConstraint("bd_market = 'mx'", name="ck_website_catalog_release_market"),
    CheckConstraint(
        "catalog_key in ('viral','opportunity','official')",
        name="ck_website_catalog_release_key",
    ),
    CheckConstraint(
        "status in ('draft','staged','production','archived','failed')",
        name="ck_website_catalog_release_status",
    ),
    CheckConstraint("product_count >= 0", name="ck_website_catalog_release_count"),
    Index(
        "uq_website_catalog_release_production",
        "bd_market", "catalog_key",
        unique=True,
        postgresql_where=text("status = 'production'"),
    ),
    Index("ix_website_catalog_release_status", "bd_market", "status", "created_at"),
)


sample_official_product_form = Table(
    "sample_official_product_form", metadata,
    Column("form_version_id", Text, primary_key=True),
    Column("bd_market", Text, nullable=False),
    Column("version_label", Text, nullable=False),
    Column("source_name", Text, nullable=False),
    Column("source_sha256", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("row_count", BigInteger, nullable=False),
    Column("created_by", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("activated_at", DateTime(timezone=True)),
    CheckConstraint("bd_market = 'mx'", name="ck_sample_official_form_market"),
    CheckConstraint(
        "status in ('draft','active','archived')",
        name="ck_sample_official_form_status",
    ),
    CheckConstraint("row_count >= 0", name="ck_sample_official_form_count"),
    UniqueConstraint(
        "bd_market", "version_label",
        name="uq_sample_official_form_version_label",
    ),
    Index(
        "uq_sample_official_form_active",
        "bd_market",
        unique=True,
        postgresql_where=text("status = 'active'"),
    ),
)


sample_official_product_form_item = Table(
    "sample_official_product_form_item", metadata,
    Column("form_version_id", Text, primary_key=True),
    Column("pid", Text, primary_key=True),
    Column("seller_id", Text),
    Column("product_name", Text),
    Column("image_url", Text),
    Column("created_at", DateTime(timezone=True), nullable=False),
    ForeignKeyConstraint(
        ["form_version_id"],
        ["sample_official_product_form.form_version_id"],
        name="fk_sample_official_form_item_version",
    ),
    CheckConstraint(
        "pid ~ '^[0-9]{10,32}$'",
        name="ck_sample_official_form_item_pid",
    ),
    Index("ix_sample_official_form_item_pid", "pid", "form_version_id"),
)


sample_product_approval_route = Table(
    "sample_product_approval_route", metadata,
    Column("bd_market", Text, primary_key=True),
    Column("pid", Text, primary_key=True),
    Column("approval_route", Text, nullable=False),
    Column("source_kind", Text, nullable=False),
    Column("source_form_version_id", Text),
    Column("notes", Text),
    Column("created_by", Text, nullable=False),
    Column("updated_by", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    ForeignKeyConstraint(
        ["source_form_version_id"],
        ["sample_official_product_form.form_version_id"],
        name="fk_sample_product_route_form",
    ),
    CheckConstraint("bd_market = 'mx'", name="ck_sample_product_route_market"),
    CheckConstraint(
        "pid ~ '^[0-9]{10,32}$'",
        name="ck_sample_product_route_pid",
    ),
    CheckConstraint(
        "approval_route in ('official_managed','agency_managed','not_applicable')",
        name="ck_sample_product_route_value",
    ),
    CheckConstraint(
        "source_kind in ('manual','official_form','catalog')",
        name="ck_sample_product_route_source",
    ),
    Index(
        "ix_sample_product_route_value",
        "bd_market", "approval_route", "updated_at",
    ),
)


sample_request_current = Table(
    "sample_request_current", metadata,
    Column("request_id", Text, primary_key=True),
    Column("bd_market", Text, nullable=False),
    Column("creator_oec_id", Text),
    Column("platform_creator_id", Text, nullable=False),
    Column("creator_handle", Text),
    Column("campaign_id", Text, nullable=False),
    Column("campaign_name", Text),
    Column("pid", Text, nullable=False),
    Column("product_name", Text),
    Column("image_url", Text),
    Column("shop_name", Text),
    Column("seller_id", Text),
    Column("applied_at", DateTime(timezone=True), nullable=False),
    Column("review_deadline_at", DateTime(timezone=True)),
    Column("platform_status", Text, nullable=False),
    Column(
        "lifecycle_status",
        Text,
        nullable=False,
        server_default="to_review",
    ),
    Column("pool_type", Text, nullable=False, server_default="unclassified"),
    Column("gmv_mxn", Numeric),
    Column("post_rate", Numeric),
    Column("video_count", BigInteger),
    Column("average_views", BigInteger),
    Column("showcase_added", Boolean),
    Column("sample_stock", BigInteger),
    Column(
        "merchant_confirmation_status",
        Text,
        nullable=False,
        server_default="not_required",
    ),
    Column("review_status", Text, nullable=False, server_default="pending"),
    Column("latest_sync_run_id", Text, nullable=False),
    Column("source_payload", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    Column("first_observed_at", DateTime(timezone=True), nullable=False),
    Column("last_observed_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    ForeignKeyConstraint(
        ["latest_sync_run_id"],
        ["sample_sync_run.run_id"],
        name="fk_sample_request_sync",
    ),
    CheckConstraint("bd_market = 'mx'", name="ck_sample_request_market"),
    CheckConstraint(
        "pid ~ '^[0-9]{10,32}$'",
        name="ck_sample_request_pid",
    ),
    CheckConstraint(
        "platform_status in ('pending','approved','rejected','cancelled','expired')",
        name="ck_sample_request_platform_status",
    ),
    CheckConstraint(
        "lifecycle_status in ("
        "'to_review','ready_to_ship','shipped','content_pending','completed'"
        ")",
        name="ck_sample_request_lifecycle_status",
    ),
    CheckConstraint(
        "pool_type in ('viral','fashion','potential','unclassified')",
        name="ck_sample_request_pool",
    ),
    CheckConstraint(
        "merchant_confirmation_status in ("
        "'not_required','pending','sent','approved','rejected','no_response','overridden')",
        name="ck_sample_request_confirmation",
    ),
    CheckConstraint(
        "review_status in ("
        "'pending','ready','approved','rejected','result_unknown','failed')",
        name="ck_sample_request_review",
    ),
    CheckConstraint(
        "(gmv_mxn is null or gmv_mxn >= 0) and "
        "(post_rate is null or post_rate >= 0) and "
        "(video_count is null or video_count >= 0) and "
        "(average_views is null or average_views >= 0) and "
        "(sample_stock is null or sample_stock >= 0)",
        name="ck_sample_request_metrics",
    ),
    Index(
        "ix_sample_request_queue",
        "bd_market",
        "platform_status",
        "review_status",
        "review_deadline_at",
    ),
    Index(
        "ix_sample_request_lifecycle",
        "bd_market",
        "lifecycle_status",
        "review_deadline_at",
    ),
    Index("ix_sample_request_campaign", "bd_market", "campaign_id", "applied_at"),
    Index("ix_sample_request_pid", "bd_market", "pid", "applied_at"),
    Index(
        "ix_sample_request_creator_pid",
        "bd_market",
        "creator_oec_id",
        "pid",
        "applied_at",
    ),
)

sample_request_observation = Table(
    "sample_request_observation", metadata,
    Column("observation_id", Text, primary_key=True),
    Column("request_id", Text, nullable=False),
    Column("sync_run_id", Text, nullable=False),
    Column("observed_at", DateTime(timezone=True), nullable=False),
    Column("platform_status", Text, nullable=False),
    Column("payload_hash", Text, nullable=False),
    Column("snapshot", JSONB, nullable=False),
    ForeignKeyConstraint(
        ["request_id"],
        ["sample_request_current.request_id"],
        name="fk_sample_observation_request",
    ),
    ForeignKeyConstraint(
        ["sync_run_id"],
        ["sample_sync_run.run_id"],
        name="fk_sample_observation_run",
    ),
    UniqueConstraint(
        "request_id",
        "payload_hash",
        name="uq_sample_observation_payload",
    ),
    Index("ix_sample_observation_time", "request_id", "observed_at"),
)

sample_weekly_creator_snapshot = Table(
    "sample_weekly_creator_snapshot", metadata,
    Column("bd_market", Text, primary_key=True),
    Column("week_start", Date, primary_key=True),
    Column("creator_oec_id", Text, primary_key=True),
    Column("creator_handle", Text),
    Column("gmv_mxn", Numeric),
    Column("tier_key", Text),
    Column("mcn_bound", Boolean, nullable=False),
    Column("captured_at", DateTime(timezone=True), nullable=False),
    Column("source_updated_at", DateTime(timezone=True)),
    CheckConstraint("bd_market = 'mx'", name="ck_sample_weekly_market"),
    CheckConstraint(
        "gmv_mxn is null or gmv_mxn >= 0",
        name="ck_sample_weekly_gmv",
    ),
)

sample_quota_policy = Table(
    "sample_quota_policy", metadata,
    Column("policy_id", Text, primary_key=True),
    Column("bd_market", Text, nullable=False),
    Column("policy_version", Text, nullable=False),
    Column("pool_type", Text, nullable=False),
    Column("mcn_bound", Boolean, nullable=False),
    Column("min_gmv_mxn", Numeric, nullable=False),
    Column("max_gmv_mxn", Numeric),
    Column("weekly_limit", BigInteger, nullable=False),
    Column("status", Text, nullable=False, server_default="active"),
    Column("effective_from", Date, nullable=False),
    Column("effective_to", Date),
    Column(
        "created_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=text("current_timestamp"),
    ),
    Column("updated_by", Text, nullable=False, server_default="schema_v59"),
    Column(
        "updated_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=text("current_timestamp"),
    ),
    UniqueConstraint(
        "bd_market",
        "policy_version",
        "pool_type",
        "mcn_bound",
        "min_gmv_mxn",
        name="uq_sample_quota_policy",
    ),
    CheckConstraint("bd_market = 'mx'", name="ck_sample_quota_market"),
    CheckConstraint(
        "pool_type in ('viral','potential')",
        name="ck_sample_quota_pool",
    ),
    CheckConstraint(
        "min_gmv_mxn >= 0 and "
        "(max_gmv_mxn is null or max_gmv_mxn > min_gmv_mxn) and "
        "weekly_limit >= 0",
        name="ck_sample_quota_range",
    ),
    CheckConstraint(
        "status in ('active','inactive')",
        name="ck_sample_quota_status",
    ),
)


sample_quota_policy_event = Table(
    "sample_quota_policy_event", metadata,
    Column("event_id", Text, primary_key=True),
    Column("policy_id", Text, nullable=False),
    Column("old_weekly_limit", BigInteger, nullable=False),
    Column("new_weekly_limit", BigInteger, nullable=False),
    Column("actor", Text, nullable=False),
    Column("occurred_at", DateTime(timezone=True), nullable=False),
    ForeignKeyConstraint(
        ["policy_id"], ["sample_quota_policy.policy_id"],
        name="fk_sample_quota_policy_event_policy",
    ),
    CheckConstraint(
        "old_weekly_limit >= 0 and new_weekly_limit >= 0",
        name="ck_sample_quota_policy_event_limits",
    ),
    Index(
        "ix_sample_quota_policy_event_time",
        "policy_id", "occurred_at",
    ),
)

sample_quota_event = Table(
    "sample_quota_event", metadata,
    Column("event_id", Text, primary_key=True),
    Column("request_id", Text, nullable=False),
    Column("week_start", Date, nullable=False),
    Column("pool_type", Text, nullable=False),
    Column("event_type", Text, nullable=False),
    Column("reserved_delta", BigInteger, nullable=False),
    Column("consumed_delta", BigInteger, nullable=False),
    Column("actor", Text, nullable=False),
    Column("note", Text),
    Column("occurred_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint(
        "request_id",
        "event_type",
        name="uq_sample_quota_request_event",
    ),
    ForeignKeyConstraint(
        ["request_id"],
        ["sample_request_current.request_id"],
        name="fk_sample_quota_request",
    ),
    CheckConstraint(
        "pool_type in ('viral','potential')",
        name="ck_sample_quota_event_pool",
    ),
    CheckConstraint(
        "event_type in ('reserve','consume','release','override')",
        name="ck_sample_quota_event_type",
    ),
    Index("ix_sample_quota_event_week", "week_start", "pool_type", "occurred_at"),
)

sample_review_attempt = Table(
    "sample_review_attempt", metadata,
    Column("attempt_id", Text, primary_key=True),
    Column("request_id", Text, nullable=False),
    Column("action", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("actor", Text, nullable=False),
    Column("reason", Text),
    Column("request_dispatched_at", DateTime(timezone=True)),
    Column("finished_at", DateTime(timezone=True)),
    Column("http_status", BigInteger),
    Column("platform_code", Text),
    Column("error_code", Text),
    Column("response_metadata", JSONB),
    Column("created_at", DateTime(timezone=True), nullable=False),
    ForeignKeyConstraint(
        ["request_id"],
        ["sample_request_current.request_id"],
        name="fk_sample_review_request",
    ),
    CheckConstraint(
        "action in ('approve','reject')",
        name="ck_sample_review_action",
    ),
    CheckConstraint(
        "status in ("
        "'waiting_account','starting','started',"
        "'succeeded','result_unknown','failed'"
        ")",
        name="ck_sample_review_status",
    ),
    Index("ix_sample_review_attempt_request", "request_id", "created_at"),
)
Index(
    "uq_sample_review_active_request",
    sample_review_attempt.c.request_id,
    unique=True,
    postgresql_where=sample_review_attempt.c.status.in_((
        "waiting_account", "starting", "started",
    )),
    sqlite_where=sample_review_attempt.c.status.in_((
        "waiting_account", "starting", "started",
    )),
)


sample_review_batch = Table(
    "sample_review_batch", metadata,
    Column("batch_id", Text, primary_key=True),
    Column("bd_market", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("selected_count", BigInteger, nullable=False),
    Column("ready_count", BigInteger, nullable=False),
    Column("excluded_count", BigInteger, nullable=False),
    Column("succeeded_count", BigInteger, nullable=False, server_default="0"),
    Column("failed_count", BigInteger, nullable=False, server_default="0"),
    Column("result_unknown_count", BigInteger, nullable=False, server_default="0"),
    Column("preview_snapshot", JSONB, nullable=False),
    Column("sync_run_id", Text),
    Column("account_name", Text),
    Column("created_by", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("started_at", DateTime(timezone=True)),
    Column("finished_at", DateTime(timezone=True)),
    Column("error_code", Text),
    ForeignKeyConstraint(
        ["sync_run_id"], ["sample_sync_run.run_id"],
        name="fk_sample_review_batch_sync",
    ),
    CheckConstraint("bd_market = 'mx'", name="ck_sample_review_batch_market"),
    CheckConstraint(
        "status in ('previewed','syncing','ready','running','completed',"
        "'completed_with_errors','result_unknown','failed','expired')",
        name="ck_sample_review_batch_status",
    ),
    CheckConstraint(
        "selected_count >= 1 and ready_count >= 0 and excluded_count >= 0 "
        "and succeeded_count >= 0 and failed_count >= 0 "
        "and result_unknown_count >= 0",
        name="ck_sample_review_batch_counts",
    ),
    Index("ix_sample_review_batch_status", "bd_market", "status", "created_at"),
)


sample_review_batch_item = Table(
    "sample_review_batch_item", metadata,
    Column("batch_id", Text, primary_key=True),
    Column("request_id", Text, primary_key=True),
    Column("item_order", BigInteger, nullable=False),
    Column("status", Text, nullable=False),
    Column("blocking_reason", Text),
    Column("attempt_id", Text),
    Column("started_at", DateTime(timezone=True)),
    Column("finished_at", DateTime(timezone=True)),
    ForeignKeyConstraint(
        ["batch_id"], ["sample_review_batch.batch_id"],
        name="fk_sample_review_batch_item_batch",
    ),
    ForeignKeyConstraint(
        ["request_id"], ["sample_request_current.request_id"],
        name="fk_sample_review_batch_item_request",
    ),
    ForeignKeyConstraint(
        ["attempt_id"], ["sample_review_attempt.attempt_id"],
        name="fk_sample_review_batch_item_attempt",
    ),
    CheckConstraint(
        "status in ('ready','excluded','running','succeeded','failed',"
        "'result_unknown','cancelled')",
        name="ck_sample_review_batch_item_status",
    ),
    Index("ix_sample_review_batch_item_status", "batch_id", "status", "item_order"),
)

report_analysis_schema_revision = Table(
    "report_analysis_schema_revision", metadata,
    Column("revision", Text, primary_key=True),
    Column("status", Text, nullable=False),
    Column("market", Text),
    Column("last_file_id", Text),
    Column("last_report_date", Date),
    Column("result_summary", JSONB),
    Column("failure_summary", Text),
    Column("started_at", DateTime(timezone=True)),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("ready_at", DateTime(timezone=True)),
    CheckConstraint(
        "status in ('pending','backfilling','ready','failed')",
        name="ck_report_analysis_schema_revision_status",
    ),
)


def _report_daily_aggregate(
    name: str,
    *dimension_columns: str,
) -> Table:
    """创建报表日聚合表；四个分析维度共享同一组可加指标。"""
    return Table(
        name,
        metadata,
        Column("market", Text, primary_key=True),
        Column("report_kind", Text, primary_key=True),
        Column("report_date", Date, primary_key=True),
        Column("dimension_key", Text, primary_key=True),
        Column("dimension_label", Text),
        *(Column(column_name, Text) for column_name in dimension_columns),
        Column("row_count", BigInteger, nullable=False),
        # refresh 时写入该维度主 ID/handle 在原始事实行中的
        # 精确非空行数；旧聚合在显式 backfill 前允许为 NULL。
        Column("present_row_count", BigInteger),
        Column("creator_count", BigInteger, nullable=False),
        Column("product_count", BigInteger, nullable=False),
        Column("shop_count", BigInteger, nullable=False),
        Column("campaign_count", BigInteger, nullable=False),
        Column("creator_attributed_gmv", Numeric),
        Column("live_attributed_gmv", Numeric),
        Column("video_attributed_gmv", Numeric),
        Column("creator_attributed_orders", BigInteger),
        Column("live_attributed_orders", BigInteger),
        Column("video_attributed_orders", BigInteger),
        Column("creator_attributed_units", BigInteger),
        Column("refunded_units", BigInteger),
        Column("estimated_partner_commission", Numeric),
        Column("actual_partner_commission", Numeric),
        Column("refreshed_at", DateTime(timezone=True), nullable=False),
        Index(
            f"ix_{name}_date",
            "market",
            "report_kind",
            "report_date",
        ),
        Index(
            f"ix_{name}_dimension",
            "market",
            "dimension_key",
            "report_date",
        ),
    )


report_creator_daily = _report_daily_aggregate(
    "report_creator_daily",
    "creator_oec_id",
    "creator_handle",
)
report_product_daily = _report_daily_aggregate(
    "report_product_daily",
    "product_id",
    "product_name",
)
report_shop_daily = _report_daily_aggregate(
    "report_shop_daily",
    "shop_id",
    "shop_name",
)
report_campaign_daily = _report_daily_aggregate(
    "report_campaign_daily",
    "campaign_id",
    "campaign_name",
)

report_classification_object_daily = Table(
    "report_classification_object_daily",
    metadata,
    Column("market", Text, primary_key=True),
    Column("report_kind", Text, primary_key=True),
    Column("report_date", Date, primary_key=True),
    Column("dimension", Text, primary_key=True),
    Column("identity_key", Text, primary_key=True),
    Column("dimension_key", Text, primary_key=True),
    Column("label", Text),
    Column("row_count", BigInteger, nullable=False),
    Column("gmv", Numeric),
    Column("orders", BigInteger),
    Column("refreshed_at", DateTime(timezone=True), nullable=False),
    CheckConstraint(
        "dimension in ('product','shop','campaign')",
        name="ck_report_classification_object_daily_dimension",
    ),
    CheckConstraint(
        "report_kind in ('mcn','tap')",
        name="ck_report_classification_object_daily_kind",
    ),
    CheckConstraint(
        "report_kind = 'tap' or dimension != 'campaign'",
        name="ck_report_classification_object_daily_campaign",
    ),
    Index(
        "ix_report_classification_object_daily_identity",
        "market",
        "report_kind",
        "dimension",
        "identity_key",
        "report_date",
        "dimension_key",
    ),
    Index(
        "ix_report_classification_object_daily_object",
        "market",
        "report_kind",
        "dimension",
        "dimension_key",
        "report_date",
        "identity_key",
    ),
)


# AI 运营核心拥有计划/匹配/事项；既有达人、商品身份仍引用原业务事实。
ai_distribution_plan = Table(
    "ai_distribution_plan", metadata,
    Column("plan_id", Text, primary_key=True),
    Column("request_key", Text, nullable=False, unique=True),
    Column("market", Text, nullable=False),
    Column("name", Text, nullable=False),
    Column("parameters", JSONB, nullable=False),
    Column("source_fingerprint", Text, nullable=False),
    Column("products", JSONB, nullable=False),
    Column("creator_total", Integer, nullable=False),
    Column("status", Text, nullable=False),
    Column("profile_days", Integer, nullable=False),
    Column("ai_review", JSONB, nullable=False, server_default="{}"),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    CheckConstraint("status in ('matching','paused','complete','failed')", name="ck_ai_plan_status"),
)
ai_distribution_match = Table(
    "ai_distribution_match", metadata,
    Column("plan_id", Text, primary_key=True),
    Column("oec_id", Text, primary_key=True),
    Column("creator", JSONB, nullable=False),
    Column("status", Text, nullable=False),
    Column("result", JSONB, nullable=False, server_default="{}"),
    ForeignKeyConstraint(["plan_id"], ["ai_distribution_plan.plan_id"]),
    CheckConstraint("status in ('pending','matched','needs_profile','needs_product','no_match')", name="ck_ai_match_status"),
    Index("ix_ai_match_work", "plan_id", "status", "oec_id"),
)
ai_cooperation_case = Table(
    "ai_cooperation_case", metadata,
    Column("case_id", Text, primary_key=True),
    Column("plan_id", Text, nullable=False),
    Column("oec_id", Text, nullable=False),
    Column("pid", Text, nullable=False),
    Column("creator", JSONB, nullable=False),
    Column("product", JSONB, nullable=False),
    Column("stage", Text, nullable=False),
    Column("owner", Text, nullable=False),
    Column("next_step", Text, nullable=False),
    Column("next_check_at", DateTime(timezone=True)),
    Column("revision", Integer, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("plan_id", "oec_id", "pid", name="uq_ai_case_candidate"),
    ForeignKeyConstraint(["plan_id", "oec_id"], ["ai_distribution_match.plan_id", "ai_distribution_match.oec_id"]),
    CheckConstraint("stage in ('in_progress','waiting_creator','waiting_business','needs_operator','closed')", name="ck_ai_case_stage"),
    Index("ix_ai_case_due", "stage", "next_check_at"),
)
ai_cooperation_event = Table(
    "ai_cooperation_event", metadata,
    Column("event_id", Text, primary_key=True),
    Column("case_id", Text, nullable=False),
    Column("revision", Integer, nullable=False),
    Column("payload", JSONB, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    ForeignKeyConstraint(["case_id"], ["ai_cooperation_case.case_id"]),
    UniqueConstraint("case_id", "revision", name="uq_ai_case_event_revision"),
)
AI_OPERATIONS_TABLES = (ai_distribution_plan, ai_distribution_match, ai_cooperation_case, ai_cooperation_event)

__all__ = [
    "ai_distribution_plan", "ai_distribution_match", "ai_cooperation_case", "ai_cooperation_event", "AI_OPERATIONS_TABLES",
    "metadata",
    "creator_profile",
    "miss_log",
    "creator_identity",
    "creator_handle_alias",
    "creator_profile_snapshot",
    "creator_profile_current",
    "enrich_run",
    "enrich_item",
    "enrich_attempt",
    "manual_seed",
    "im_conversation",
    "im_creator_identity",
    "im_message",
    "im_auto_reply",
    "reply_template",
    "reply_outbox",
    "reply_setting",
    "im_ai_dry_log",
    "creator_product",
    "message_template",
    "outreach",
    "creator_scan",
    "creator_contact",
    "creator_contact_point",
    "creator_relationship",
    "creator_task",
    "creator_event",
    "send_task",
    "send_task_account",
    "send_task_lead",
    "send_task_component",
    "send_component_attempt",
    "send_worker_state",
    "share_link_job",
    "share_link_plan",
    "share_link_record",
    "share_link_product_current",
    "share_link_item",
    "share_link_attempt",
    "public_share_link_request",
    "im_listener_state",
    "im_history_recovery_state",
    "im_conversation_sync_request",
    "creator_im_state",
    "human_reply_template",
    "tap_creator_product_history",
    "send_task_attribution",
    "send_task_event",
    "report_contract",
    "report_sync_run",
    "report_schema",
    "report_file",
    "report_raw_row",
    "partner_attribution_daily",
    "creator_report_observation",
    "report_creator_identity",
    "report_creator_identity_handle",
    "report_creator_identity_event",
    "creator_classification",
    "creator_classification_assignment",
    "creator_classification_event",
    "creator_classification_definition_event",
    "classification_import_batch",
    "classification_import_row",
    "merchant",
    "merchant_event",
    "merchant_import_batch",
    "merchant_import_row",
    "promotion_sharelink_binding",
    "promotion_catalog_product",
    "promotion_site_page",
    "promotion_site_release",
    "promotion_site_draft",
    "promotion_site_draft_item",
    "promotion_site_sharelink_batch",
    "promotion_site_sharelink_item",
    "promotion_site_sample_request",
    "promotion_site_sample_sync_state",
    "sample_confirmation_rule",
    "sample_sync_run",
    "sample_request_current",
    "sample_request_observation",
    "sample_weekly_creator_snapshot",
    "sample_quota_policy",
    "sample_quota_policy_event",
    "sample_quota_event",
    "sample_review_attempt",
    "report_analysis_schema_revision",
    "report_creator_daily",
    "report_product_daily",
    "report_shop_daily",
    "report_campaign_daily",
    "report_classification_object_daily",
]
