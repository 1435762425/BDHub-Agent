# -*- coding: utf-8 -*-
"""配置加载：读项目根 config.yaml，解析路径/密钥/富化参数为不可变 Config。"""
from __future__ import annotations
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
import json
import re
import yaml

ROOT = Path(__file__).resolve().parent.parent  # 项目根：01-BDSystem


@dataclass(frozen=True)
class ReplyConfig:
    """AI 自动回复(bdhub/reply)。api_key 空=未配置(--auto-reply 启动 fail-loud 拒绝)。"""
    api_key: str = ""
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-chat"
    llm_timeout_s: int = 20
    min_delay_s: int = 15                # 回复前随机延迟下限(拟人)
    max_delay_s: int = 45
    max_replies_per_conv_day: int = 3    # 每会话每日 AI 上限(防对喷循环)
    daily_cap: int = 200                 # 全局每日 AI 外发上限(风控闸)


@dataclass(frozen=True, slots=True)
class AssistConfig:
    """人工触发的只读建议；不控制既有自动回复或任何发送入口。"""

    python_path: Path = ROOT / "data/runtime/ai-assist/.venv/bin/python"
    sample_max_age_hours: int = 24
    link_max_age_hours: int = 24


@dataclass(frozen=True, slots=True)
class CampaignResearchConfig:
    """当前已完成 BR Campaign 只读取证的采集账号。"""

    account_name: str = "acc6"
    ai_enabled: bool = False
    ai_product_limit: int = 10
    ai_creator_limit: int = 10
    ai_days: int = 14
    ai_workers: int = 4


@dataclass(frozen=True, slots=True)
class CatalogWorkspaceConfig:
    filters: dict = field(default_factory=dict)
    link_rules: dict = field(default_factory=dict)
    seller_campaigns: dict = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class KalodataConfig:
    """二发研究复用独立项目的浏览器，不使用 TikTok 业务账号。"""

    project_dir: Path = Path.home() / "kaladatagrab/乘丰 对标查找"


@dataclass(frozen=True, slots=True)
class ReportSyncConfig:
    """机构日报同步配置；路径必须由当前项目持有。"""

    storage_root: Path
    raw_dir: Path
    lookback_days: int = 4
    empty_confirm_days: int = 7
    navigation_timeout_ms: int = 90_000
    export_timeout_s: int = 180
    download_timeout_s: int = 300


@dataclass(frozen=True, slots=True)
class ImOperationsConfig:
    """IM 运营层的固定近似换算参数。"""

    mxn_per_usd: Decimal = Decimal("18")


@dataclass(frozen=True, slots=True)
class PublicShareLinkConfig:
    """公开升佣站配置；默认关闭，密钥不完整时拒绝启动写入口。"""

    enabled: bool = False
    account_name: str = "acc5"
    account_names: tuple[str, ...] = ("acc5",)
    operator_name: str = "Sitio MX"
    commission_mode: str = "MX_AVERAGE"
    average_policy: str = "HALF_OR_PLUS_POINT_TWO"
    origin_token: str = ""
    dashboard_url: str = "http://127.0.0.1:8787"
    allowed_origins: tuple[str, ...] = ("https://bjntiktok.com",)
    allowed_hosts: tuple[str, ...] = ("commission-api.bjntiktok.com", "127.0.0.1")
    max_requests_per_ip_hour: int = 5
    max_requests_per_pid_hour: int = 3
    max_queue_size: int = 50
    request_ttl_hours: int = 24
    poll_interval_seconds: float = 2.0


@dataclass(frozen=True, slots=True)
class SampleReviewConfig:
    """样品管理默认关闭；启用后只使用显式样品账号和北京时间。"""

    enabled: bool = False
    account_name: str = "acc1"
    timezone: str = "Asia/Shanghai"
    schedule_times: tuple[str, ...] = ("08:30", "14:00")
    page_size: int = 50
    max_pages: int = 20


@dataclass(frozen=True)
class Config:
    headers_json: Path
    partner_id: str
    leads_xlsx: Path
    leads_handle_column: str
    cache_dir: Path
    db_url: str
    db_pool_size: int
    db_max_overflow: int
    output_xlsx: Path
    request_interval_seconds: float
    rate_limit_sleep_base: int
    max_retry: int
    find_page_size: int
    refresh_days: int
    priority_handles_file: str
    concurrency: int
    qps: float
    miss_skip_days: int
    report_sync: "ReportSyncConfig"
    im_operations: "ImOperationsConfig" = field(
        default_factory=ImOperationsConfig
    )
    public_share_link: "PublicShareLinkConfig" = field(
        default_factory=PublicShareLinkConfig
    )
    sample_review: "SampleReviewConfig" = field(
        default_factory=SampleReviewConfig
    )
    reply: "ReplyConfig" = field(default_factory=lambda: ReplyConfig())
    assist: "AssistConfig" = field(default_factory=AssistConfig)
    kalodata: KalodataConfig = field(default_factory=KalodataConfig)
    campaign_research: CampaignResearchConfig = field(default_factory=CampaignResearchConfig)
    catalog_workspace: CatalogWorkspaceConfig = field(default_factory=CatalogWorkspaceConfig)
    market: str = "mx"        # 当前生产市场；只有 hub.markets 中的注册项才可调度
    oec_light_route: str = "browser_16"
    profile_strategy: str = "split_identity_metrics"


@dataclass(frozen=True)
class Account:
    """一个独立的联盟登录账号(多账号并发的最小单元)。每账号=独立 cookie + 独立浏览器 profile +
    独立 partner_id + 独立设备指纹 → 独立令牌桶,自洽且互不关联(抗风控)。"""
    name: str
    partner_id: str
    headers_json: Path        # 该账号的身份包(cookie+UA+sec-*),独立文件
    profile_dir: Path         # 该账号的持久化浏览器 profile(登录/刷新用)
    username: str = ""        # 半自动登录用(可空→纯人工登录)
    password: str = ""
    fp: str = ""              # 设备指纹(verifyFp);空→运行时从该账号 cookie 的 s_v_web_id 派生
    device_id: str = ""       # device_id;空→从 name 稳定派生(每账号不同,不再全局 "0")
    partner_ids: dict = field(default_factory=dict)   # 该账号【每市场】的 partner_id 覆盖 {market:pid};
    # 监听绑定市场；只有监听账号受此字段约束。抓取、Contact、关系读取与
    # 普通 IM 发送账号可跨市场复用，旧配置缺字段时兼容为 MX。
    market: str = "mx"
    im_role: str = "sender"    # 历史默认用途提示；不构成 monitor/sender 权限门禁
    enabled: bool = True
    # 账号池为调度策略，不代表平台权限。None 表示旧配置，运行时做兼容映射。
    listener_pool: bool | None = None
    listener_priority: int = 100
    im_send_pool: bool | None = None
    collection_pool: bool | None = None
    report_pool: bool | None = None
    share_link_pool: bool | None = None
    sample_review_pool: bool | None = None
    sample_review_priority: int = 100
    # 普通账号由本机生命周期任务定时深检查、无感刷新；全部启用账号每48小时
    # 在墨西哥03:00-05:00执行有界登录维护。常驻监听/ShareLink账号由各自
    # resident 进程逐个维护，不会被通用任务强抢 ProfileLease。
    identity_lifecycle_enabled: bool = True
    auto_relogin: bool = True
    data_qps: float | None = None
    data_concurrency: int | None = None
    im_send_interval_seconds: float | None = None
    #   空={}=只有 partner_id(隐含 MX)。其他仍启用市场的差异参数按账号显式填写；停用市场不得新增绑定。
    #   markets.identity_for() 优先取它,避免非 MX 市场回退套用主号 partner_id(身份不自洽)。


_ACCOUNT_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
IM_ACCOUNT_ROLES = frozenset({"monitor", "sender"})
PROFILE_STRATEGIES = frozenset({"split_identity_metrics", "combined_126"})


def validate_account_name(name: str) -> str:
    """校验可安全用于文件名和日志标识的稳定账号名。"""
    if not isinstance(name, str) or not _ACCOUNT_NAME_RE.fullmatch(name):
        raise ValueError(
            "账号名不安全：仅允许 1-64 位英文字母、数字、点、下划线或连字符，不能使用邮箱地址。"
        )
    return name


def validate_im_role(value: object) -> str:
    """校验历史 IM 默认用途字段；显式空值或未知值一律拒绝。"""
    if not isinstance(value, str):
        raise ValueError("IM 账号角色必须是 monitor 或 sender。")
    role = value.strip().lower()
    if role not in IM_ACCOUNT_ROLES:
        raise ValueError("IM 账号角色必须是 monitor 或 sender。")
    return role


def validate_profile_strategy(value: object) -> str:
    """Profile 请求形状与传输层解耦；只接受已实现且可审计的策略。"""
    if not isinstance(value, str):
        raise ValueError("enrich.profile_strategy 必须是字符串")
    strategy = value.strip().lower()
    if strategy not in PROFILE_STRATEGIES:
        raise ValueError(
            "enrich.profile_strategy 必须是 "
            "split_identity_metrics 或 combined_126"
        )
    return strategy


def _resolve(root: Path, value: str) -> Path:
    p = Path(value)
    return p if p.is_absolute() else (root / p)


def _storage_child(storage_root: Path, value: object) -> Path:
    """解析稳定存储根目录内的报表路径，并拒绝目录穿越。"""
    raw_value = str(value or "").strip()
    if not raw_value:
        raise ValueError("report_sync.raw_dir 不能为空")
    candidate = _resolve(storage_root, raw_value).resolve()
    resolved_root = storage_root.resolve()
    if not candidate.is_relative_to(resolved_root):
        raise ValueError(
            f"report_sync.raw_dir 必须位于 storage_root 内: {resolved_root}"
        )
    return candidate


def _bounded_int(
    raw: dict,
    key: str,
    default: int,
    *,
    minimum: int,
    maximum: int | None = None,
) -> int:
    try:
        value = int(raw.get(key, default))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"report_sync.{key} 必须是整数") from exc
    if value < minimum or (maximum is not None and value > maximum):
        upper = f"..{maximum}" if maximum is not None else "以上"
        raise ValueError(
            f"report_sync.{key} 必须在 {minimum}{upper} 范围内"
        )
    return value


def _report_sync_cfg(root: Path, raw: object) -> ReportSyncConfig:
    if raw is None:
        values: dict = {}
    elif isinstance(raw, dict):
        values = raw
    else:
        raise ValueError("report_sync 必须是 YAML 对象")
    storage_value = str(values.get("storage_root", ".") or "").strip()
    if not storage_value:
        raise ValueError("report_sync.storage_root 不能为空")
    storage_root = _resolve(root, storage_value).resolve()
    return ReportSyncConfig(
        storage_root=storage_root,
        raw_dir=_storage_child(
            storage_root,
            values.get("raw_dir", "data/reports/raw"),
        ),
        lookback_days=_bounded_int(
            values,
            "lookback_days",
            4,
            minimum=1,
            maximum=31,
        ),
        empty_confirm_days=_bounded_int(
            values,
            "empty_confirm_days",
            7,
            minimum=1,
            maximum=90,
        ),
        navigation_timeout_ms=_bounded_int(
            values,
            "navigation_timeout_ms",
            90_000,
            minimum=1,
        ),
        export_timeout_s=_bounded_int(
            values,
            "export_timeout_s",
            180,
            minimum=1,
        ),
        download_timeout_s=_bounded_int(
            values,
            "download_timeout_s",
            300,
            minimum=1,
        ),
    )


def _im_operations_cfg(raw: object) -> ImOperationsConfig:
    if raw is None:
        values: dict = {}
    elif isinstance(raw, dict):
        values = raw
    else:
        raise ValueError("im_operations 必须是 YAML 对象")
    try:
        factor = Decimal(str(values.get("mxn_per_usd", "18")))
    except InvalidOperation as exc:
        raise ValueError("im_operations.mxn_per_usd 必须是数字") from exc
    if factor < Decimal("1") or factor > Decimal("100"):
        raise ValueError("im_operations.mxn_per_usd 必须在 1..100 范围内")
    return ImOperationsConfig(mxn_per_usd=factor)


def _string_tuple(values: object, *, key: str, default: tuple[str, ...]) -> tuple[str, ...]:
    if values is None:
        return default
    if not isinstance(values, list):
        raise ValueError(f"public_share_link.{key} 必须是字符串列表")
    cleaned = tuple(str(value or "").strip() for value in values)
    if not cleaned or any(not value for value in cleaned):
        raise ValueError(f"public_share_link.{key} 不能包含空值")
    return cleaned


def _public_share_link_cfg(raw: object) -> PublicShareLinkConfig:
    if raw is None:
        values: dict = {}
    elif isinstance(raw, dict):
        values = raw
    else:
        raise ValueError("public_share_link 必须是 YAML 对象")
    defaults = PublicShareLinkConfig()
    enabled = values.get("enabled", defaults.enabled)
    if type(enabled) is not bool:
        raise ValueError("public_share_link.enabled 必须是布尔值")
    origin_token = str(values.get("origin_token", "") or "").strip()
    if enabled and len(origin_token) < 32:
        raise ValueError("public_share_link.origin_token 启用时至少 32 位")
    account_name = validate_account_name(
        str(values.get("account_name", defaults.account_name) or "").strip()
    )
    raw_account_names = _string_tuple(
        values.get("account_names"),
        key="account_names",
        default=(account_name,),
    )
    account_names = tuple(dict.fromkeys(
        validate_account_name(value) for value in raw_account_names
    ))
    if len(account_names) > 3:
        raise ValueError("public_share_link.account_names 最多配置三个账号")
    commission_mode = str(
        values.get("commission_mode", defaults.commission_mode) or ""
    ).strip().upper()
    if commission_mode not in {
        "MX_AVERAGE", "MAX_CREATOR", "KEEP_MARGIN", "BOOST_OVER_OPEN",
    }:
        raise ValueError("public_share_link.commission_mode 无效")
    average_policy = str(
        values.get("average_policy", defaults.average_policy) or ""
    ).strip().upper()
    if average_policy not in {"FLOOR_INTEGER", "HALF_OR_PLUS_POINT_TWO"}:
        raise ValueError("public_share_link.average_policy 无效")
    dashboard_url = str(
        values.get("dashboard_url", defaults.dashboard_url) or ""
    ).strip().rstrip("/")
    if not dashboard_url.startswith("http://127.0.0.1:"):
        raise ValueError("public_share_link.dashboard_url 必须是本机回环 HTTP 地址")
    def bounded(key: str, default: int, minimum: int, maximum: int) -> int:
        try:
            value = int(values.get(key, default))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"public_share_link.{key} 必须是整数") from exc
        if value < minimum or value > maximum:
            raise ValueError(
                f"public_share_link.{key} 必须在 {minimum}..{maximum} 范围内"
            )
        return value
    try:
        poll_interval = float(
            values.get("poll_interval_seconds", defaults.poll_interval_seconds)
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("public_share_link.poll_interval_seconds 必须是数字") from exc
    if poll_interval < 0.5 or poll_interval > 60:
        raise ValueError(
            "public_share_link.poll_interval_seconds 必须在 0.5..60 范围内"
        )
    return PublicShareLinkConfig(
        enabled=enabled,
        account_name=account_names[0],
        account_names=account_names,
        operator_name=str(
            values.get("operator_name", defaults.operator_name) or ""
        ).strip() or defaults.operator_name,
        commission_mode=commission_mode,
        average_policy=average_policy,
        origin_token=origin_token,
        dashboard_url=dashboard_url,
        allowed_origins=_string_tuple(
            values.get("allowed_origins"),
            key="allowed_origins",
            default=defaults.allowed_origins,
        ),
        allowed_hosts=_string_tuple(
            values.get("allowed_hosts"),
            key="allowed_hosts",
            default=defaults.allowed_hosts,
        ),
        max_requests_per_ip_hour=bounded(
            "max_requests_per_ip_hour", defaults.max_requests_per_ip_hour, 1, 100
        ),
        max_requests_per_pid_hour=bounded(
            "max_requests_per_pid_hour", defaults.max_requests_per_pid_hour, 1, 20
        ),
        max_queue_size=bounded("max_queue_size", defaults.max_queue_size, 1, 1000),
        request_ttl_hours=bounded(
            "request_ttl_hours", defaults.request_ttl_hours, 1, 168
        ),
        poll_interval_seconds=poll_interval,
    )


def _sample_review_cfg(raw: object) -> SampleReviewConfig:
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError("sample_review 必须是 YAML 对象")
    defaults = SampleReviewConfig()
    enabled = raw.get("enabled", defaults.enabled)
    if type(enabled) is not bool:
        raise ValueError("sample_review.enabled 必须是布尔值")
    account_name = validate_account_name(
        str(raw.get("account_name", defaults.account_name) or "").strip()
    )
    timezone_name = str(
        raw.get("timezone", defaults.timezone) or ""
    ).strip()
    if timezone_name != "Asia/Shanghai":
        raise ValueError("sample_review.timezone 必须是 Asia/Shanghai")
    schedule_times = _string_tuple(
        raw.get("schedule_times"),
        key="schedule_times",
        default=defaults.schedule_times,
    )
    if len(schedule_times) != 2 or tuple(sorted(schedule_times)) != schedule_times:
        raise ValueError("sample_review.schedule_times 必须是两个升序时间")
    for value in schedule_times:
        try:
            hour, minute = (int(part) for part in value.split(":"))
        except (TypeError, ValueError):
            raise ValueError("sample_review.schedule_times 必须使用 HH:MM") from None
        if not 0 <= hour <= 23 or not 0 <= minute <= 59 or value != f"{hour:02d}:{minute:02d}":
            raise ValueError("sample_review.schedule_times 必须使用 HH:MM")
    page_size = int(raw.get("page_size", defaults.page_size))
    max_pages = int(raw.get("max_pages", defaults.max_pages))
    if not 1 <= page_size <= 100:
        raise ValueError("sample_review.page_size 必须在 1..100")
    if not 1 <= max_pages <= 100:
        raise ValueError("sample_review.max_pages 必须在 1..100")
    return SampleReviewConfig(
        enabled=enabled,
        account_name=account_name,
        timezone=timezone_name,
        schedule_times=schedule_times,
        page_size=page_size,
        max_pages=max_pages,
    )


def _db_url(db: dict) -> str:
    """构造 SQLAlchemy(psycopg3) 连接串：db.url 优先，否则用 host/port/user/password/dbname 拼。"""
    url = str(db.get("url", "") or "").strip()
    if url:
        return url
    from urllib.parse import quote_plus
    user = quote_plus(str(db.get("user", "bdhub")))
    pwd = quote_plus(str(db.get("password", "")))
    host = str(db.get("host", "127.0.0.1"))
    port = int(db.get("port", 5432))
    name = str(db.get("dbname", "bdhub"))
    return f"postgresql+psycopg://{user}:{pwd}@{host}:{port}/{name}"


def _reply_cfg(rp: dict) -> ReplyConfig:
    d = ReplyConfig()
    return ReplyConfig(
        api_key=str(rp.get("api_key", "") or ""),
        base_url=str(rp.get("base_url", d.base_url)).rstrip("/"),
        model=str(rp.get("model", d.model)),
        llm_timeout_s=int(rp.get("llm_timeout_s", d.llm_timeout_s)),
        min_delay_s=int(rp.get("min_delay_s", d.min_delay_s)),
        max_delay_s=int(rp.get("max_delay_s", d.max_delay_s)),
        max_replies_per_conv_day=int(rp.get("max_replies_per_conv_day", d.max_replies_per_conv_day)),
        daily_cap=int(rp.get("daily_cap", d.daily_cap)),
    )


def _assist_cfg(root: Path, raw: dict) -> AssistConfig:
    runtime = Path(str(raw.get("python_path") or "data/runtime/ai-assist/.venv/bin/python"))
    ages = [raw.get("sample_max_age_hours", 24), raw.get("link_max_age_hours", 24)]
    if any(type(value) is not int or not 1 <= value <= 168 for value in ages):
        raise ValueError("assist 的样品和链接证据窗口必须是 1–168 小时的整数")
    return AssistConfig(
        python_path=runtime if runtime.is_absolute() else root / runtime,
        sample_max_age_hours=ages[0], link_max_age_hours=ages[1],
    )


def load(path: str | Path | None = None) -> Config:
    cfg_path = Path(path) if path else (ROOT / "config.yaml")
    if not cfg_path.exists():
        raise FileNotFoundError(
            f"找不到配置 {cfg_path}\n请从 config.example.yaml 复制为 config.yaml 后按本地路径修改。"
        )
    raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    root = cfg_path.resolve().parent
    auth = raw.get("auth", {}) or {}
    paths = raw.get("paths", {}) or {}
    en = raw.get("enrich", {}) or {}
    report_sync = _report_sync_cfg(root, raw.get("report_sync"))
    im_operations = _im_operations_cfg(raw.get("im_operations"))
    public_share_link = _public_share_link_cfg(raw.get("public_share_link"))
    sample_review = _sample_review_cfg(raw.get("sample_review"))
    db = raw.get("db", {})
    if db is None or "db" not in raw:
        # PG-only：缺 db 段几乎必是迁移后漏配，fail fast 给明确指引（而非默认连一个不存在的库）
        raise ValueError(
            "config.yaml 缺少 db 段（PostgreSQL 连接配置）。\n"
            "请参照 config.example.yaml 补一个 db: {host,port,user,password,dbname}，"
            "或 db.url 直接给连接串；本机可先 `docker compose up -d` 起库。"
        )
    return Config(
        headers_json=_resolve(root, auth.get("headers_json", "secrets/headers.json")),
        partner_id=str(auth.get("partner_id", "")),
        leads_xlsx=_resolve(root, paths.get("leads_xlsx", "data/leads/总表.xlsx")),
        leads_handle_column=str(paths.get("leads_handle_column", "handle")),
        cache_dir=_resolve(root, paths.get("cache_dir", "data/cache/raw")),
        db_url=_db_url(db),
        db_pool_size=int(db.get("pool_size", 10)),
        db_max_overflow=int(db.get("max_overflow", 20)),
        output_xlsx=_resolve(root, paths.get("output_xlsx", "data/output/达人信息库.xlsx")),
        request_interval_seconds=float(en.get("request_interval_seconds", 1.5)),
        rate_limit_sleep_base=int(en.get("rate_limit_sleep_base", 60)),
        max_retry=int(en.get("max_retry", 3)),
        find_page_size=int(en.get("find_page_size", 12)),
        refresh_days=int(en.get("refresh_days", 30)),
        priority_handles_file=str(en.get("priority_handles_file", "") or ""),
        concurrency=int(en.get("concurrency", 2)),
        qps=float(en.get("qps", 2)),          # 单账号硬顶≤2/s(超过即软封)，兜底默认与文档一致
        miss_skip_days=int(en.get("miss_skip_days", 14)),
        report_sync=report_sync,
        im_operations=im_operations,
        public_share_link=public_share_link,
        sample_review=sample_review,
        reply=_reply_cfg(raw.get("reply", {}) or {}),
        assist=_assist_cfg(root, raw.get("assist", {}) or {}),
        kalodata=KalodataConfig(project_dir=_resolve(
            root, (raw.get("kalodata") or {}).get("project_dir", str(KalodataConfig().project_dir)),
        )),
        campaign_research=CampaignResearchConfig(
            account_name=str((raw.get("campaign_research") or {}).get("account_name", "acc6")),
            ai_enabled=False,
            ai_product_limit=int((raw.get("campaign_research") or {}).get("ai_product_limit", 10)),
            ai_creator_limit=int((raw.get("campaign_research") or {}).get("ai_creator_limit", 10)),
            ai_days=int((raw.get("campaign_research") or {}).get("ai_days", 14)),
            ai_workers=max(1, min(4, int((raw.get("campaign_research") or {}).get("ai_workers", 4)))),
        ),
        catalog_workspace=CatalogWorkspaceConfig(
            filters=dict((raw.get("catalog_workspace") or {}).get("filters") or {}),
            link_rules=dict((raw.get("catalog_workspace") or {}).get("link_rules") or {}),
            seller_campaigns=dict((raw.get("catalog_workspace") or {}).get("seller_campaigns") or {}),
        ),
        market=str(raw.get("market") or "mx"),
        oec_light_route=str(
            en.get("oec_light_route", "browser_16")
            or "browser_16"
        ),
        profile_strategy=validate_profile_strategy(
            en.get("profile_strategy", "split_identity_metrics")
        ),
    )


def _standalone_account(cfg: Config) -> Account:
    """无 accounts.json 时使用显式命名的单账号身份。"""
    return Account(
        name="acc1",
        partner_id=cfg.partner_id,
        headers_json=cfg.headers_json,
        profile_dir=ROOT / "secrets" / "_pw_profile_acc1",
        market="mx",
    )


def load_accounts(cfg: Config | None = None, path: str | Path | None = None) -> list[Account]:
    """读 secrets/accounts.json → Account 列表；文件不存在则回退单账号(现有 auth 段)。

    accounts.json 格式:
      {"accounts": [
         {"name":"acc1","partner_id":"...","username":"...","password":"...",
          "headers_json":"secrets/headers_acc1.json","profile_dir":"secrets/_pw_profile_acc1"},
         ...]}
    每字段可省:headers_json 默认 secrets/headers_<name>.json；profile_dir 默认 secrets/_pw_profile_<name>；
    partner_id 默认主号。多账号=独立令牌桶,并发提速。
    """
    cfg = cfg or load()
    acc_path = Path(path) if path else (ROOT / "secrets" / "accounts.json")
    if not acc_path.exists():
        return [_standalone_account(cfg)]
    raw = json.loads(acc_path.read_text(encoding="utf-8")) or {}
    items = raw.get("accounts") or []
    out: list[Account] = []
    profile_owners: dict[Path, str] = {}
    for i, a in enumerate(items):
        if not isinstance(a, dict):
            continue
        raw_name = a["name"] if "name" in a else f"acc{i + 1}"
        candidate_name = raw_name.strip() if isinstance(raw_name, str) else raw_name
        name = validate_account_name(candidate_name)
        enabled = a.get("enabled", True)
        if not isinstance(enabled, bool):
            raise ValueError(f"账号 {name} 的 enabled 必须是 JSON 布尔值 true/false。")
        if "market" in a:
            raw_market = a["market"]
            if not isinstance(raw_market, str) or not raw_market.strip():
                raise ValueError(f"账号 {name} 的 market 必须是非空字符串。")
            account_market = raw_market.strip().lower()
        else:
            account_market = "mx"
        if "im_role" in a:
            try:
                im_role = validate_im_role(a["im_role"])
            except ValueError:
                raise ValueError(
                    f"账号 {name} 的 im_role 必须是 monitor 或 sender。"
                ) from None
        else:
            im_role = "sender"
        bool_fields = (
            "listener_pool",
            "im_send_pool",
            "collection_pool",
            "report_pool",
            "share_link_pool",
            "sample_review_pool",
            "identity_lifecycle_enabled",
            "auto_relogin",
        )
        for field_name in bool_fields:
            if field_name in a and type(a[field_name]) is not bool:
                raise ValueError(
                    f"账号 {name} 的 {field_name} 必须是 JSON 布尔值 true/false。"
                )
        sample_review_priority = a.get("sample_review_priority", 100)
        if type(sample_review_priority) is not int or not 1 <= sample_review_priority <= 999:
            raise ValueError(
                f"账号 {name} 的 sample_review_priority 必须在 1~999。"
            )
        if a.get("listener_pool") is True and a.get("collection_pool") is True:
            raise ValueError(f"账号 {name} 不能同时加入监听池和数据抓取池。")
        if a.get("listener_pool") is True and a.get("report_pool") is True:
            raise ValueError(f"账号 {name} 不能同时加入监听池和报表池。")
        if a.get("listener_pool") is True and a.get("share_link_pool") is True:
            raise ValueError(f"账号 {name} 不能同时加入监听池和 ShareLink 池。")
        if a.get("listener_pool") is True and a.get("sample_review_pool") is True:
            raise ValueError(f"账号 {name} 不能同时加入监听池和样品审批池。")
        if a.get("share_link_pool") is True and a.get("sample_review_pool") is True:
            raise ValueError(f"账号 {name} 不能同时加入 ShareLink 池和样品审批池。")
        hj = a.get("headers_json") or f"secrets/headers_{name}.json"
        pd = a.get("profile_dir") or f"secrets/_pw_profile_{name}"
        profile_dir = _resolve(ROOT, pd).resolve()
        if profile_dir.name == "_pw_profile":
            raise ValueError(
                f"账号 {name} 必须使用带账号名的独立 profile，"
                f"例如 secrets/_pw_profile_{name}。"
            )
        previous_owner = profile_owners.get(profile_dir)
        if previous_owner is not None:
            raise ValueError(
                f"账号 {name} 与账号 {previous_owner} 不能共用 profile："
                f"{profile_dir.name}。"
            )
        profile_owners[profile_dir] = name
        out.append(Account(
            name=name,
            partner_id=str(a.get("partner_id") or cfg.partner_id),
            headers_json=_resolve(ROOT, hj),
            profile_dir=profile_dir,
            username=str(a.get("username") or ""),
            password=str(a.get("password") or ""),
            fp=str(a.get("fp") or ""),
            device_id=str(a.get("device_id") or ""),
            partner_ids=dict(a.get("partner_ids") or {}),   # 每市场 partner_id 覆盖；缺省使用市场现场默认 identity
            market=account_market,
            im_role=im_role,
            enabled=enabled,
            listener_pool=a.get("listener_pool"),
            listener_priority=int(a.get("listener_priority", 100)),
            im_send_pool=a.get("im_send_pool"),
            collection_pool=a.get("collection_pool"),
            report_pool=a.get("report_pool"),
            share_link_pool=a.get("share_link_pool"),
            sample_review_pool=a.get("sample_review_pool"),
            sample_review_priority=sample_review_priority,
            identity_lifecycle_enabled=a.get(
                "identity_lifecycle_enabled", True
            ),
            auto_relogin=a.get("auto_relogin", True),
            data_qps=(float(a["data_qps"]) if a.get("data_qps") is not None else None),
            data_concurrency=(int(a["data_concurrency"]) if a.get("data_concurrency") is not None else None),
            im_send_interval_seconds=(
                float(a["im_send_interval_seconds"])
                if a.get("im_send_interval_seconds") is not None else None
            ),
        ))
    return out or [_standalone_account(cfg)]
