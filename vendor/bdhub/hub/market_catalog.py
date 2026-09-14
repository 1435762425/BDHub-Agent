"""Dashboard 可见市场目录。

目录只描述稳定的运营元数据，不代表远端接口已经通过现场验收。真正允许
Find/Profile/IM 等外部动作的市场仍以 ``hub.markets.MARKETS`` 为准，避免
把尚未抓包确认的参数误当成生产真值。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True, slots=True)
class DashboardMarket:
    key: str
    label: str
    short_label: str
    currency: str
    locale: str
    template_lang: str
    rollout_group: str
    rollout_order: int
    reports_enabled: bool = False

    def public_dict(self) -> dict:
        return asdict(self)


MARKET_CATALOG: dict[str, DashboardMarket] = {
    "mx": DashboardMarket(
        key="mx",
        label="墨西哥",
        short_label="MX",
        currency="MXN",
        locale="es-MX",
        template_lang="es",
        rollout_group="current",
        rollout_order=0,
        reports_enabled=True,
    ),
    "br": DashboardMarket(
        key="br",
        label="巴西",
        short_label="BR",
        currency="BRL",
        locale="pt-BR",
        template_lang="pt",
        rollout_group="priority-1",
        rollout_order=10,
    ),
    "uk": DashboardMarket(
        key="uk",
        label="英国",
        short_label="UK",
        currency="GBP",
        locale="en-GB",
        template_lang="en",
        rollout_group="priority-1",
        rollout_order=20,
    ),
    "it": DashboardMarket(
        key="it",
        label="意大利",
        short_label="IT",
        currency="EUR",
        locale="it-IT",
        template_lang="it",
        rollout_group="priority-1",
        rollout_order=30,
    ),
    "jp": DashboardMarket(
        key="jp",
        label="日本",
        short_label="JP",
        currency="JPY",
        locale="ja-JP",
        template_lang="ja",
        rollout_group="priority-2",
        rollout_order=40,
    ),
    "us": DashboardMarket(
        key="us",
        label="美国",
        short_label="US",
        currency="USD",
        locale="en-US",
        template_lang="en",
        rollout_group="priority-2",
        rollout_order=50,
    ),
    "de": DashboardMarket(
        key="de",
        label="德国",
        short_label="DE",
        currency="EUR",
        locale="de-DE",
        template_lang="de",
        rollout_group="priority-2",
        rollout_order=60,
    ),
}


def get(market: str) -> DashboardMarket:
    clean_market = str(market or "").strip().lower()
    try:
        return MARKET_CATALOG[clean_market]
    except KeyError:
        raise KeyError(f"未知 Dashboard 市场 {clean_market!r}") from None


def public_catalog() -> list[dict]:
    """返回稳定排序的安全前端契约，并区分目录与远端运行就绪。"""
    from .im_markets import IM_MARKETS
    from .markets import MARKETS, capability_enabled

    return [
        {
            **market.public_dict(),
            "runtime_ready": (
                market.key in MARKETS
                and capability_enabled(market.key, "find")
                and capability_enabled(market.key, "profile")
                and capability_enabled(market.key, "pure_http")
            ),
            "im_evidence_ready": market.key in IM_MARKETS,
            "remote_capabilities": (
                MARKETS[market.key].capabilities.public_dict()
                if market.key in MARKETS
                else {}
            ),
        }
        for market in sorted(
            MARKET_CATALOG.values(),
            key=lambda item: item.rollout_order,
        )
    ]


__all__ = ["DashboardMarket", "MARKET_CATALOG", "get", "public_catalog"]
