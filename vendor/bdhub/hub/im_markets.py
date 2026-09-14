"""IM 监听市场注册表。

这里只保存 IM 页面初始化和监听所需的现场真值。Find、富化、PID、商品搜索
和发送能力继续由 ``hub.markets`` 管理，禁止从本表推断。
"""
from __future__ import annotations

from dataclasses import dataclass


_PORTAL_SG = "https://partner.tiktokshop.com"
_API_SG = "https://api-partner-sg.tiktokshop.com"
_PORTAL_EU = "https://partner.eu.tiktokshop.com"
_SAFE_RELIABILITY = frozenset({"cross_checked", "observed_valid"})


@dataclass(frozen=True)
class ImMarket:
    """一个 Partner 市场的 IM identity 与监听证据。"""

    key: str
    label: str
    market_code: str
    portal_host: str
    api_host: str
    identity_partner_id: str
    aid: str
    reliability: str
    identity_verified: bool = True
    sdk_verified: bool = True
    listener_verified: bool = False

    def require_monitor(self) -> "ImMarket":
        """监听只要求 identity 与 SDK；历史会话和商品字段不参与判断。"""
        if self.reliability not in _SAFE_RELIABILITY:
            raise KeyError("im_market_evidence_conflict")
        if not self.market_code:
            raise KeyError("im_market_code_missing")
        if not self.portal_host or not self.api_host or not self.aid:
            raise KeyError("im_transport_truth_missing")
        if not self.identity_partner_id:
            raise KeyError("im_identity_missing")
        if not self.identity_verified:
            raise KeyError("im_identity_unverified")
        if not self.sdk_verified:
            raise KeyError("im_sdk_unverified")
        return self

    def im_page(self) -> str:
        self.require_monitor()
        return f"{self.portal_host}/partner/im?market={self.market_code}"


def _market(
    key: str,
    label: str,
    market_code: str,
    portal_host: str,
    api_host: str,
    identity_partner_id: str,
    aid: str = "360019",
    *,
    cross_checked: bool = False,
    listener_verified: bool = False,
) -> ImMarket:
    return ImMarket(
        key=key,
        label=label,
        market_code=market_code,
        portal_host=portal_host,
        api_host=api_host,
        identity_partner_id=identity_partner_id,
        aid=aid,
        reliability="cross_checked" if cross_checked else "observed_valid",
        listener_verified=listener_verified,
    )


IM_MARKETS: dict[str, ImMarket] = {
    "uk": _market(
        "uk",
        "英国",
        "3",
        _PORTAL_EU,
        _PORTAL_EU,
        "8652354317985613569",
        listener_verified=True,
    ),
    "th": _market(
        "th",
        "泰国",
        "5",
        _PORTAL_SG,
        _API_SG,
        "8677544489142290192",
        listener_verified=True,
    ),
    "my": _market(
        "my",
        "马来西亚",
        "6",
        _PORTAL_SG,
        _API_SG,
        "8677544525004441360",
        listener_verified=True,
    ),
    "vn": _market(
        "vn",
        "越南",
        "7",
        _PORTAL_SG,
        _API_SG,
        "8677546560201000720",
        listener_verified=True,
    ),
    "it": _market(
        "it",
        "意大利",
        "8",
        _PORTAL_EU,
        _PORTAL_EU,
        "8678985626083886865",
        listener_verified=True,
    ),
    "ph": _market(
        "ph",
        "菲律宾",
        "10",
        _PORTAL_SG,
        _API_SG,
        "8677546560200673040",
        listener_verified=True,
    ),
    "sg": _market(
        "sg",
        "新加坡",
        "13",
        _PORTAL_SG,
        _API_SG,
        "8677546560201656065",
        listener_verified=True,
    ),
    "br": _market(
        "br",
        "巴西",
        "16",
        _PORTAL_SG,
        _API_SG,
        "8678395489720895248",
        listener_verified=True,
    ),
    "de": _market(
        "de",
        "德国",
        "18",
        _PORTAL_EU,
        _PORTAL_EU,
        "8680177412768696065",
        listener_verified=True,
    ),
    "mx": _market(
        "mx",
        "墨西哥",
        "19",
        _PORTAL_SG,
        _API_SG,
        "8656292575915771649",
        cross_checked=True,
        listener_verified=True,
    ),
    "jp": _market(
        "jp",
        "日本",
        "20",
        _PORTAL_SG,
        _API_SG,
        "8676195488227886864",
        listener_verified=True,
    ),
    "be": _market(
        "be",
        "比利时",
        "22",
        _PORTAL_EU,
        _PORTAL_EU,
        "8678954887281608464",
        listener_verified=True,
    ),
    "nl": _market(
        "nl",
        "荷兰",
        "23",
        _PORTAL_EU,
        _PORTAL_EU,
        "8678978836393789185",
        listener_verified=True,
    ),
    "us": _market(
        "us",
        "美国",
        "100",
        "https://partner.us.tiktokshop.com",
        "https://partner.us.tiktokshop.com",
        "8649653026864662288",
        aid="359713",
        listener_verified=True,
    ),
}


def get(market: str) -> ImMarket:
    try:
        return IM_MARKETS[market]
    except KeyError:
        raise KeyError(
            f"未知 IM 市场 {market!r}；可用市场: {sorted(IM_MARKETS)}"
        ) from None


def im_page(market: str) -> str:
    return get(market).im_page()


def selftest() -> None:
    assert len(IM_MARKETS) == 14
    assert im_page("mx") == (
        "https://partner.tiktokshop.com/partner/im?market=19"
    )
    assert im_page("be") == (
        "https://partner.eu.tiktokshop.com/partner/im?market=22"
    )
    assert {
        key for key, market in IM_MARKETS.items()
        if market.listener_verified
    } == set(IM_MARKETS)
    for market in IM_MARKETS.values():
        market.require_monitor()


__all__ = ["IM_MARKETS", "ImMarket", "get", "im_page", "selftest"]
