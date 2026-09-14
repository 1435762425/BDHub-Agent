# -*- coding: utf-8 -*-
"""Partner 远端市场注册表与逐能力运行门禁。

市场存在只表示已经登记了现场身份参数，不代表每一种远端动作都允许运行。
``MarketCapabilities`` 把 Find、Profile、关系、Contact、商品与发送拆开验收，
调用方必须检查具体能力，禁止再用 ``market in MARKETS`` 一次性放开整条链路。

本文件只依赖 hub.schema / ..config / stdlib —— 不导入任何业务模块(enrich/discover/send/...)，
保持 hub 包"被业务模块依赖，不反向依赖业务模块"的方向。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from sqlalchemy import Table

from .schema import creator_profile, miss_log

_FIND_PATH = "/api/v1/oec/affiliate/creator/marketplace/4partner/find?"
_PLACEHOLDER = "__UNCONFIGURED__"   # 未启用/未完成抓包的市场占位符;identity 校验据此 fail-loud

CapabilityStatus = Literal["enabled", "canary", "pending", "unsupported"]
_CAPABILITY_STATUSES = frozenset({"enabled", "canary", "pending", "unsupported"})
_CAPABILITY_NAMES = (
    "find",
    "profile",
    "pure_http",
    "relation",
    "contact",
    "im",
    "product_search",
    "campaign",
    "share_link",
    "tap_link",
    "tap_link_cleanup",
    "product_select",
    "campaign_join",
    "send",
)


@dataclass(frozen=True, slots=True)
class MarketCapabilities:
    """一个市场的现场验收状态；只有 ``enabled`` 能进入正式运行入口。

    ``canary`` 只表示人工单条验证成功，不能据此开放批量生产能力。
    ``unsupported`` 表示平台明确不提供；``pending`` 表示仍需补验。
    """

    find: CapabilityStatus = "pending"
    profile: CapabilityStatus = "pending"
    pure_http: CapabilityStatus = "pending"
    relation: CapabilityStatus = "pending"
    contact: CapabilityStatus = "pending"
    im: CapabilityStatus = "pending"
    product_search: CapabilityStatus = "pending"
    campaign: CapabilityStatus = "pending"
    share_link: CapabilityStatus = "pending"
    # Product List 创建独立验收，不能从 Campaign 读取或 ShareLink 能力推导。
    tap_link: CapabilityStatus = "pending"
    tap_link_cleanup: CapabilityStatus = "pending"
    product_select: CapabilityStatus = "pending"
    campaign_join: CapabilityStatus = "pending"
    send: CapabilityStatus = "pending"

    def __post_init__(self) -> None:
        invalid = {
            name: getattr(self, name)
            for name in _CAPABILITY_NAMES
            if getattr(self, name) not in _CAPABILITY_STATUSES
        }
        if invalid:
            raise ValueError(f"market_capability_status_invalid:{invalid}")

    def status(self, capability: str) -> CapabilityStatus:
        if capability not in _CAPABILITY_NAMES:
            raise ValueError("market_capability_unknown")
        return getattr(self, capability)

    def enabled(self, capability: str) -> bool:
        return self.status(capability) == "enabled"

    def public_dict(self) -> dict[str, str]:
        return {name: self.status(name) for name in _CAPABILITY_NAMES}


class MarketCapabilityError(ValueError):
    """市场已登记，但指定远端能力尚不可进入正式运行。"""

    def __init__(self, market: str, capability: str, status: str) -> None:
        self.market = market
        self.capability = capability
        self.status = status
        super().__init__(
            f"market_capability_unavailable:{market}:{capability}:{status}"
        )


@dataclass(frozen=True)
class Market:
    """一个市场的全部元数据：find 基础参数 + 落库表 + IM/话术相关字段。"""
    key: str
    label: str
    host: str
    aid: str
    user_language: str
    default_partner_id: str | None
    warmup: str
    home: str
    canary: str                      # 金丝雀(必存达人):判反爬签名是否生效
    profile_tbl: Table
    miss_tbl: Table
    profile_name: str
    miss_name: str
    im_market: str                   # IM popup URL 的 market 参数
    shop_id: str                     # IM popup URL 的 shop_id
    im_market_partner_id: str        # 货盘商品搜索 API 的 partner_id(agency marketId)
    template_lang: str               # 该市场默认话术语言(message_template.lang)
    accounts: list[str]              # 该市场专用登录账号名单(空=全部可用账号)
    capabilities: MarketCapabilities = field(default_factory=MarketCapabilities)
    im_find_aid: str = ""            # send _JS_CREATOR_FIND 创作者搜索用的 aid(与 enrich 的 aid 不同)
    affiliate_partner_id: str = ""   # send _AFFILIATE_PARTNER_ID(创作者搜索 API partner_id)
    pw_profile_suffix: str = ""      # 浏览器持久化 profile 目录后缀(mx="_acc3" / us="_us" / jp="_jp")
    signer_region: str = "sg"        # 纯 HTTP 签名资源区：sg / eu / us；不代表能力已启用
    overrides: dict = field(default_factory=dict)


_ALL_ENABLED = MarketCapabilities(
    find="enabled",
    profile="enabled",
    pure_http="enabled",
    relation="enabled",
    contact="enabled",
    im="enabled",
    product_search="enabled",
    campaign="enabled",
    share_link="enabled",
    send="enabled",
)


MARKETS: dict[str, Market] = {
    "mx": Market(
        key="mx",
        label="墨西哥",
        host="https://api-partner-sg.tiktokshop.com",
        aid="360019",
        user_language="zh-CN",
        default_partner_id=None,          # None → 落回 cfg.partner_id(我方 MX MCN)
        warmup="https://partner.tiktokshop.com/affiliate-cmp/creator?market=19",
        home="https://partner.tiktokshop.com/home",
        canary="devoniastudio",
        profile_tbl=creator_profile, miss_tbl=miss_log,
        profile_name="creator_profile", miss_name="miss_log",
        im_market="19",
        shop_id="8656292575915771649",
        im_market_partner_id="8648954657520060177",
        template_lang="es",
        accounts=[],
        capabilities=_ALL_ENABLED,
        im_find_aid="359713",                 # send/dispatch.py _JS_CREATOR_FIND 里的 aid 现值
        affiliate_partner_id="8656292575915771649",  # send/dispatch.py _AFFILIATE_PARTNER_ID 现值
        pw_profile_suffix="_acc3",            # mx 使用 acc3 的独立 profile
        signer_region="sg",
    ),
    "br": Market(
        key="br", label="巴西",
        host="https://api-partner-sg.tiktokshop.com",
        aid="360019", user_language="zh-CN",
        default_partner_id="8678395489720895248",
        warmup="https://partner.tiktokshop.com/affiliate-cmp/creator?market=16",
        home="https://partner.tiktokshop.com/home",
        canary="__CANARY_REQUIRED__",
        profile_tbl=creator_profile, miss_tbl=miss_log,
        profile_name="creator_profile", miss_name="miss_log",
        im_market="16", shop_id="8678395489720895248",
        im_market_partner_id="8678404287775868688",
        template_lang="pt", accounts=[],
        capabilities=MarketCapabilities(
            find="enabled", profile="enabled", relation="enabled",
            im="enabled", product_search="enabled", campaign="enabled",
            share_link="unsupported",
        ),
        im_find_aid="360019",
        affiliate_partner_id="8678395489720895248",
        pw_profile_suffix="_br",
        signer_region="sg",
    ),
    "uk": Market(
        key="uk", label="英国",
        host="https://partner.eu.tiktokshop.com",
        aid="360019", user_language="zh-CN",
        default_partner_id="8652354317985613569",
        warmup="https://partner.eu.tiktokshop.com/affiliate-cmp/creator?market=3",
        home="https://partner.eu.tiktokshop.com/home",
        canary="chloecr3",
        profile_tbl=creator_profile, miss_tbl=miss_log,
        profile_name="creator_profile", miss_name="miss_log",
        im_market="3", shop_id="8652354317985613569",
        im_market_partner_id="8652354317985613569",
        template_lang="en", accounts=[],
        capabilities=MarketCapabilities(
            find="enabled", profile="enabled", pure_http="enabled",
            relation="unsupported",
            contact="enabled", im="enabled", product_search="enabled",
            campaign="unsupported", share_link="unsupported", send="canary",
        ),
        im_find_aid="360019",
        affiliate_partner_id="8652354317985613569",
        pw_profile_suffix="_uk",
        signer_region="eu",
    ),
    "it": Market(
        key="it", label="意大利",
        host="https://partner.eu.tiktokshop.com",
        aid="360019", user_language="zh-CN",
        default_partner_id="8678985626083886865",
        warmup="https://partner.eu.tiktokshop.com/affiliate-cmp/creator?market=8",
        home="https://partner.eu.tiktokshop.com/home",
        canary="__CANARY_REQUIRED__",
        profile_tbl=creator_profile, miss_tbl=miss_log,
        profile_name="creator_profile", miss_name="miss_log",
        im_market="8", shop_id="8678985626083886865",
        im_market_partner_id="8677957691320272641",
        template_lang="it", accounts=[],
        capabilities=MarketCapabilities(
            find="enabled", profile="enabled", pure_http="enabled",
            relation="enabled", im="enabled",
            product_search="enabled", campaign="unsupported",
            share_link="unsupported", send="canary",
            product_select="enabled", tap_link="canary", campaign_join="enabled",
        ),
        im_find_aid="360019",
        affiliate_partner_id="8678985626083886865",
        pw_profile_suffix="_it",
        signer_region="eu",
    ),
    "jp": Market(
        key="jp", label="日本",
        host="https://api-partner-sg.tiktokshop.com",
        aid="360019", user_language="zh-CN",
        default_partner_id="8676195488227886864",
        warmup="https://partner.tiktokshop.com/affiliate-cmp/creator?market=20",
        home="https://partner.tiktokshop.com/home",
        canary="__CANARY_REQUIRED__",
        profile_tbl=creator_profile, miss_tbl=miss_log,
        profile_name="creator_profile", miss_name="miss_log",
        im_market="20", shop_id="8676195488227886864",
        im_market_partner_id="8676195488228214544",
        template_lang="ja", accounts=[],
        capabilities=MarketCapabilities(
            find="enabled", profile="enabled", pure_http="enabled",
            relation="enabled",
            contact="enabled", im="enabled", product_search="enabled",
            campaign="unsupported", share_link="unsupported", send="canary",
        ),
        im_find_aid="360019",
        affiliate_partner_id="8676195488227886864",
        pw_profile_suffix="_jp",
        signer_region="sg",
    ),
    "us": Market(
        key="us", label="美国",
        host="https://partner.us.tiktokshop.com",
        aid="359713", user_language="zh-CN",
        default_partner_id="8649653026864662288",
        warmup="https://partner.us.tiktokshop.com/affiliate-cmp/creator?market=100",
        home="https://partner.us.tiktokshop.com/home",
        canary="__CANARY_REQUIRED__",
        profile_tbl=creator_profile, miss_tbl=miss_log,
        profile_name="creator_profile", miss_name="miss_log",
        im_market="100", shop_id="8649653026864662288",
        im_market_partner_id="8650467356296579846",
        template_lang="en", accounts=[],
        capabilities=MarketCapabilities(
            find="enabled", profile="enabled", pure_http="enabled",
            relation="enabled", im="enabled",
            product_search="enabled", campaign="unsupported",
            share_link="unsupported", send="canary",
        ),
        im_find_aid="359713",
        affiliate_partner_id="8649653026864662288",
        pw_profile_suffix="_us",
        signer_region="us",
    ),
    "de": Market(
        key="de", label="德国",
        host="https://partner.eu.tiktokshop.com",
        aid="360019", user_language="zh-CN",
        default_partner_id="8680177412768696065",
        warmup="https://partner.eu.tiktokshop.com/affiliate-cmp/creator?market=18",
        home="https://partner.eu.tiktokshop.com/home",
        canary="__CANARY_REQUIRED__",
        profile_tbl=creator_profile, miss_tbl=miss_log,
        profile_name="creator_profile", miss_name="miss_log",
        im_market="18", shop_id="8680177412768696065",
        im_market_partner_id="8680351387788740369",
        template_lang="de", accounts=[],
        capabilities=MarketCapabilities(
            find="enabled", profile="enabled", pure_http="enabled",
            relation="enabled", contact="enabled",
            im="enabled", campaign="unsupported", share_link="unsupported",
        ),
        im_find_aid="360019",
        affiliate_partner_id="8680177412768696065",
        pw_profile_suffix="_de",
        signer_region="eu",
    ),
}


def capability_status(market: str, capability: str) -> CapabilityStatus:
    return get(market).capabilities.status(capability)


def capability_enabled(market: str, capability: str) -> bool:
    return get(market).capabilities.enabled(capability)


def require_capability(market: str, capability: str) -> Market:
    selected = get(market)
    status = selected.capabilities.status(capability)
    if status != "enabled":
        raise MarketCapabilityError(selected.key, capability, status)
    return selected


def get(market: str) -> Market:
    """按 key 取市场元数据；不存在则报明确 KeyError(而非 None 静默通过)。"""
    try:
        return MARKETS[market]
    except KeyError:
        raise KeyError(f"未知市场 {market!r}；可用市场: {sorted(MARKETS)}") from None


def active(cli_market: str | None = None) -> str:
    """当前生效市场 key：显式 cli_market 优先 > config.yaml 的 market 字段 > 缺省 'mx'。"""
    if cli_market:
        return cli_market
    from .. import config as cfgmod
    cfg = cfgmod.load()
    return str(getattr(cfg, "market", None) or "mx")


def find_url(market: str, partner_id) -> str:
    """拼某市场的 find 端点(仅基础参数;反爬签名 X-Tts-Oec-Bsid 由浏览器页面 JS 自动补)。"""
    m = MARKETS[market]
    base = (f"user_language={m.user_language}&partner_id={partner_id}&aid={m.aid}"
            "&app_name=i18n_ecom_alliance&device_id=0&device_platform=web&cookie_enabled=true"
            "&browser_language=zh-CN&browser_platform=Win32&browser_name=Mozilla"
            "&browser_version=5.0&browser_online=true&timezone_name=Asia/Shanghai")
    return m.host + _FIND_PATH + base


def store_for(market: str):
    """按统一 ``bd_market`` 维度创建 ProfileStore；不在此推断远端能力。"""
    from .repo.creators import ProfileStore
    m = MARKETS[market]
    return ProfileStore(profile_tbl=m.profile_tbl, miss_tbl=m.miss_tbl,
                        profile_name=m.profile_name, miss_name=m.miss_name,
                        market=market)


def partner_id_for(market: str, override=None, cfg=None) -> str:
    """解析市场 partner_id：市场默认值 > 账号覆盖 > 配置文件。

    MX 没有市场默认值；其他市场的现场 partner identity 在注册表中显式登记。
    """
    m = MARKETS[market]
    if m.default_partner_id:
        return str(m.default_partner_id)
    if override:
        return str(override)
    if cfg is None:
        from .. import config as cfgmod
        cfg = cfgmod.load()
    return str(cfg.partner_id)


# ==================== 账号 × 市场 单一真相源(SSOT) ====================
# 目的:治"账号混乱"。当前身份被拆成三处——accounts.json(每账号一个 partner_id,全是 MX 8656)、
# markets.py(每市场一个 default_partner_id,全是主号)、以及各模块里硬编码的 shop_id/aid/host。
# identity_for(market, account) 把它们合成【一个】结构,任何模块要"哪个账号在哪个市场"的身份都从这里取。
# 纯新增:不改 partner_id_for / find_url / store_for 现有行为;MX 输出与现役硬编码逐字节一致(见 selftest)。

def _portal_host(m: "Market") -> str:
    """从 home 页推导该市场 partner 门户 host(scheme://netloc)。
    mx→partner.tiktokshop.com / us→partner.us.tiktokshop.com。IM/warmup 等门户页共用此 host。"""
    parts = urlsplit(m.home)
    return f"{parts.scheme}://{parts.netloc}"


def im_page(market: str) -> str:
    """该市场的 IM 页 URL(send/monitor 共用的单一真相源,取代 imbase.transport 里写死的 _IM_PAGE)。
    mx 输出与旧 _IM_PAGE 逐字节一致;im_market 为空/占位则 fail-loud。"""
    require_capability(market, "im")
    m = MARKETS[market]
    if not m.im_market or m.im_market == _PLACEHOLDER:
        raise KeyError(f"市场 {market!r} 的 im_market 未填真值(im_page 不可用);先抓包补 markets.py")
    return f"{_portal_host(m)}/partner/im?market={m.im_market}"


@dataclass(frozen=True)
class Identity:
    """(账号 × 市场) 的完整身份参数 —— 单一真相源。
    任何模块要"哪个账号在哪个市场"的 partner_id/shop_id/IM 参数/profile,都从 identity_for() 取,
    不再从 accounts.json 与 markets.py 各拿一半、更不在模块里硬编码 MX 值。"""
    market: str
    label: str
    account: str
    # --- find / enrich ---
    host: str
    aid: str
    user_language: str
    partner_id: str
    warmup: str
    home: str
    canary: str
    # --- IM / send(缺真值的市场经 require_im() fail-loud) ---
    im_market: str
    shop_id: str
    affiliate_partner_id: str
    im_market_partner_id: str
    im_find_aid: str
    # --- 话术 ---
    template_lang: str
    # --- 浏览器 / 凭据(来自账号;account=None 时为 None) ---
    pw_profile_suffix: str
    profile_dir: Path | None
    headers_json: Path | None
    # partner_id 是否派生自本账号自身(False = 回退套用了市场默认/别账号的 partner,身份不自洽、风控敏感)
    partner_id_is_own: bool

    def im_page(self) -> str:
        return im_page(self.market)

    def require_monitor(self) -> "Identity":
        """校验 IM 监听所需真值，不要求商品卡发送专用参数。"""
        require_capability(self.market, "im")
        missing = [
            key
            for key in ("im_market",)
            if getattr(self, key) in ("", _PLACEHOLDER)
        ]
        if missing:
            raise KeyError(
                f"市场 {self.market!r} 的 IM/monitor 真值未齐: {missing};"
                f"先抓包补 markets.py"
            )
        return self

    def require_product_search(self) -> "Identity":
        """校验商品卡搜索端点与独立货盘 partner 真值。"""
        require_capability(self.market, "product_search")
        missing = [
            key
            for key in ("host", "im_market_partner_id", "aid")
            if getattr(self, key) in ("", _PLACEHOLDER)
        ]
        if missing:
            raise KeyError(
                f"市场 {self.market!r} 的商品搜索真值未齐: {missing};"
                "禁止复用 MX 商品 partner"
            )
        return self

    def require_im(self) -> "Identity":
        """校验 IM/send 所需真值齐备(非占位/非空),缺则 fail-loud。
        未启用市场补齐真值前访问即抛，禁止拿 MX 的 shop_id/partner 去别市场发送。"""
        require_capability(self.market, "send")
        missing = [k for k in ("im_market", "shop_id", "affiliate_partner_id",
                               "im_market_partner_id", "im_find_aid")
                   if getattr(self, k) in ("", _PLACEHOLDER)]
        if missing:
            raise KeyError(f"市场 {self.market!r} 的 IM/send 真值未齐: {missing};"
                           f"先抓包补 markets.py,禁止用 MX 值代跑")
        return self


def identity_for(market: str, account=None, cfg=None) -> Identity:
    """合成 (账号 × 市场) 身份。account=config.Account | None(None=注册表默认,等价旧无账号池行为)。

    partner_id 解析(fail-loud 版,优先级):
      1) 账号显式的每市场覆盖 account.partner_ids[market] —— 真正的"账号×市场"矩阵,最高优先;
      2) 市场默认 default_partner_id(未来市场可显式配置);
      3) MX 无市场默认 → 账号自带 partner_id / cfg.partner_id。
    普通账号可跨市场复用，所以市场现场默认 partner 与账号历史 MX partner 不同
    不再视为身份冲突；listener 的固定国家约束由 ``imbase.account_binding`` 负责。
    """
    m = MARKETS[market]
    own = True
    over = dict(getattr(account, "partner_ids", {}) or {})
    if over.get(market):
        pid = str(over[market])
    elif m.default_partner_id:
        pid = str(m.default_partner_id)
    else:
        acc_pid = str(getattr(account, "partner_id", "") or "")
        if acc_pid:
            pid = acc_pid
        else:
            if cfg is None:
                from .. import config as cfgmod
                cfg = cfgmod.load()
            pid = str(cfg.partner_id)
    return Identity(
        market=market, label=m.label, account=str(getattr(account, "name", "") or "default"),
        host=m.host, aid=m.aid, user_language=m.user_language, partner_id=pid,
        warmup=m.warmup, home=m.home, canary=m.canary,
        im_market=m.im_market, shop_id=m.shop_id, affiliate_partner_id=m.affiliate_partner_id,
        im_market_partner_id=m.im_market_partner_id, im_find_aid=m.im_find_aid,
        template_lang=m.template_lang, pw_profile_suffix=m.pw_profile_suffix,
        profile_dir=getattr(account, "profile_dir", None),
        headers_json=getattr(account, "headers_json", None),
        partner_id_is_own=own,
    )


def selftest() -> None:
    """离线自测：七国身份完整，MX 现役参数保持一致。"""
    idm = identity_for("mx")
    assert idm.shop_id == "8656292575915771649", idm.shop_id                       # =dispatch._SHOP_ID
    assert idm.affiliate_partner_id == "8656292575915771649", idm.affiliate_partner_id  # =_AFFILIATE_PARTNER_ID
    assert idm.im_market_partner_id == "8648954657520060177", idm.im_market_partner_id  # =_IM_MARKET_PARTNER_ID
    assert idm.im_find_aid == "359713", idm.im_find_aid                            # =_JS_CREATOR_FIND aid
    assert idm.host == "https://api-partner-sg.tiktokshop.com", idm.host
    assert im_page("mx") == "https://partner.tiktokshop.com/partner/im?market=19", im_page("mx")  # =_IM_PAGE
    idm.require_im()                                                               # mx 真值齐 → 通过
    assert set(MARKETS) == {"mx", "br", "uk", "it", "jp", "us", "de"}
    assert capability_enabled("mx", "send")
    assert capability_status("uk", "send") == "canary"
    assert capability_status("uk", "relation") == "unsupported"
    print("markets SSOT selftest OK:七国身份 + 逐能力门禁")


__all__ = [
    "MARKETS",
    "CapabilityStatus",
    "Identity",
    "Market",
    "MarketCapabilities",
    "MarketCapabilityError",
    "active",
    "capability_enabled",
    "capability_status",
    "find_url",
    "get",
    "identity_for",
    "im_page",
    "partner_id_for",
    "require_capability",
    "selftest",
    "store_for",
]
