# -*- coding: utf-8 -*-
"""TikTok Shop Partner 联盟 API 客户端：认证 + find 搜索 + 限流/失效识别。

注：用 secrets/headers.json 里的抓包 cookie 认证；cookie 过期会抛 AuthError，需重新抓包。
find 接口已实测可对任意冷达人返回 oec_id + 88 字段内联画像（无需动态签名）。
"""
from __future__ import annotations
import hashlib
import logging
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .identity_store import IdentityBundle, load_identity, validate_identity

_dbg = logging.getLogger("bdhub.enrich.client")   # 完整响应体只走 debug，默认不打印(防泄露后端敏感字段)

HOST = "https://api-partner-sg.tiktokshop.com"
FIND_URL = HOST + "/api/v1/oec/affiliate/creator/marketplace/4partner/find"
PROFILE_URL = HOST + "/api/v1/oec/affiliate/creator/marketplace/4partner/profile"
RELATION_URL = HOST + "/api/v1/affiliate/partner/relation/list"   # ⑤绑定关系(GET, 免签名)
CAMPAIGN_LIST_URL = HOST + "/api/v1/affiliate/partner/campaign/list"            # ⑦货盘(GET, 免签名)
CAMPAIGN_PRODUCT_URL = HOST + "/api/v1/affiliate/partner/campaign/product/list"  # ⑦单品(GET, 免签名)
# ⑦货盘走【样品·货盘账号 8648】而非 enrich 的联盟账号(cookie 同一份;三 partner_id 同属 MCN BJN)
CAMPAIGN_PARTNER_ID = "8648954657520060177"


class RateLimitError(RuntimeError):
    """命中 code=10000 风控/限流。"""

    def __init__(self, message: str, *, remote_code: str = "10000") -> None:
        super().__init__(message)
        self.remote_code = remote_code


class AuthError(RuntimeError):
    """cookie 失效 / 未登录。"""


class PartnerProtocolError(RuntimeError):
    """Partner 返回了可安全记录的非成功业务码。"""

    def __init__(self, message: str, *, remote_code: str | None = None) -> None:
        super().__init__(message)
        self.remote_code = remote_code


_INVALID_HEADERS_ERROR = "身份请求头无效"


def _with_default_content_type(headers: dict[str, str]) -> dict[str, str]:
    copied = dict(headers)
    if not any(name.casefold() == "content-type" for name in copied):
        copied["content-type"] = "application/json"
    return copied


def _load_headers(headers_json: Path) -> dict[str, str]:
    try:
        bundle = load_identity(Path(headers_json))
    except FileNotFoundError:
        raise
    except (TypeError, ValueError):
        raise AuthError(_INVALID_HEADERS_ERROR) from None
    return _with_default_content_type(bundle.headers)


def _load_headers_override(headers: object) -> dict[str, str]:
    bundle = IdentityBundle(headers=headers)  # type: ignore[arg-type]
    try:
        validate_identity(bundle)
    except (TypeError, ValueError):
        raise AuthError(_INVALID_HEADERS_ERROR) from None
    return _with_default_content_type(bundle.headers)


def _cookie_val(cookie_str: str, name: str) -> str:
    """从 cookie 串取某项的值(精确名匹配)。"""
    for part in str(cookie_str).split(";"):
        k, _, v = part.strip().partition("=")
        if k == name:
            return v
    return ""


def _derive_fp(cookie_str: str, seed: str) -> str:
    """设备指纹 fp = verifyFp 本体：优先取 cookie 里 s_v_web_id/verifyFp 真值(与浏览器自洽);
    缺失则按 seed(账号名)派生一个稳定值(每账号不同,不再全局共用硬编码 → 多账号不互相关联)。"""
    for n in ("s_v_web_id", "verifyFp", "ttwid"):
        v = _cookie_val(cookie_str, n)
        if v:
            return v
    return "verify_" + hashlib.sha1(seed.encode("utf-8")).hexdigest()[:20]


def _derive_device_id(cookie_str: str, seed: str) -> str:
    """device_id：优先用 cookie 的 odin_tt/uid_tt 稳定派生；否则按 seed 派生一个稳定 19 位数(不再 "0")。"""
    base = _cookie_val(cookie_str, "odin_tt") or _cookie_val(cookie_str, "uid_tt") or seed
    return str(int(hashlib.sha1(base.encode("utf-8")).hexdigest()[:15], 16))


class PartnerClient:
    def __init__(self, cfg, rate_limiter=None, pause_event=None, account=None,
                 headers_override: dict[str, str] | None = None,
                 market: str | None = None):
        from ..hub.markets import get as get_market, identity_for

        clean_market = str(
            market
            or getattr(account, "runtime_market", None)
            or "mx"
        ).strip().lower()
        market_meta = get_market(clean_market)
        identity = identity_for(clean_market, account, cfg)
        self.cfg = cfg
        self.market = clean_market
        self.host = market_meta.host.rstrip("/")
        self.find_url = self.host + "/api/v1/oec/affiliate/creator/marketplace/4partner/find"
        self.profile_url = self.host + "/api/v1/oec/affiliate/creator/marketplace/4partner/profile"
        self.relation_url = self.host + "/api/v1/affiliate/partner/relation/list"
        self.campaign_list_url = self.host + "/api/v1/affiliate/partner/campaign/list"
        self.campaign_product_url = self.host + "/api/v1/affiliate/partner/campaign/product/list"
        self.user_language = market_meta.user_language
        self.aid = market_meta.aid
        self.campaign_partner_id = market_meta.im_market_partner_id
        self.rl = rate_limiter        # 限速(令牌桶)，多账号时每账号一份
        self.pause = pause_event      # 暂停(限流退避时 clear)，多账号时每账号一份
        self.account = account        # None=单账号(用 cfg.headers_json/partner_id)；否则用该账号独立身份
        self.partner_id = identity.partner_id
        if headers_override is None:
            headers_path = account.headers_json if account is not None else cfg.headers_json
            self.headers = _load_headers(headers_path)
        else:
            self.headers = _load_headers_override(headers_override)
        # 每账号独立且与自身 cookie 自洽的设备指纹(抗风控:不共享硬编码 fp/device_id)
        cookie = next((v for k, v in self.headers.items() if str(k).lower() == "cookie"), "")
        seed = account.name if account is not None else "default"
        self.fp = (account.fp if account is not None and account.fp else "") or _derive_fp(cookie, seed)
        self.device_id = (account.device_id if account is not None and account.device_id else "") \
            or _derive_device_id(cookie, seed)
        self.session = requests.Session()
        # 去掉 408/429:这俩是限流信号,urllib3 在一个 acquire() 槽内静默重试3次会绕过全局限速器制造突发、加深软封。
        # 限流交给上层 runner 的 AIMD penalize+cooldown 处理;仅对真·瞬时 5xx 自动重试。
        retry = Retry(total=3, connect=3, read=3, backoff_factor=0.8,
                      status_forcelist=[500, 502, 503, 504],
                      allowed_methods=["GET", "POST"], raise_on_status=False)
        adapter = HTTPAdapter(max_retries=retry, pool_connections=40, pool_maxsize=40)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

    def _base_params(self) -> dict:
        return {
            "user_language": self.user_language,
            "partner_id": self.partner_id,
            "aid": self.aid,
            "app_name": "i18n_ecom_alliance", "device_id": self.device_id,
            "fp": self.fp,          # 每账号与自身 cookie 自洽的设备指纹(不再全局硬编码)
            "device_platform": "web", "cookie_enabled": "true",
            "screen_width": "1920", "screen_height": "1080",
            "browser_language": "zh-CN", "browser_platform": "Win32",
            "browser_name": "Mozilla", "browser_version": "5.0",
            "browser_online": "true", "timezone_name": "Asia/Shanghai",
        }

    def _gate(self, wait_pause: bool):
        """请求前过两道门：全局退避暂停 + 令牌桶限速(并发共享)。"""
        if wait_pause and self.pause is not None:
            self.pause.wait()        # 全局退避中则等待（恢复探针用 wait_pause=False 绕过，避免死锁）
        if self.rl is not None:
            self.rl.acquire()        # 全局限速

    def _decode(self, resp, tag: str) -> dict:
        """统一解码 + 风控/失效识别(POST/GET 共用)。"""
        status_code = resp.status_code
        if status_code in (401, 403):
            _dbg.debug("%s HTTP status=%s", tag, status_code)
            raise AuthError("Partner HTTP 认证失效")
        if status_code == 429:
            _dbg.debug("%s HTTP status=429", tag)
            raise RateLimitError("Partner HTTP 限流", remote_code="429")
        if status_code != 200:
            safe_status = status_code if type(status_code) is int else "invalid"
            _dbg.debug("%s HTTP status=%s", tag, safe_status)
            raise RuntimeError("Partner HTTP 协议错误")
        try:
            verification_required = "bdturing-verify" in resp.headers
        except Exception:
            verification_required = False
        if verification_required:
            _dbg.debug("%s verification header present", tag)
            raise RateLimitError("Partner 验证码拦截", remote_code="10000")
        data = resp.json()
        code = data.get("code")
        if code is None or (type(code) is int and code == 0):
            return data
        if type(code) is int:
            safe_code = str(code)
        elif type(code) is str and code.isascii() and code.isdigit() and len(code) <= 16:
            safe_code = code
        else:
            safe_code = "invalid"
        _dbg.debug("%s business code=%s", tag, safe_code)
        if safe_code in ("10000", "16901008"):
            raise RateLimitError("Partner 业务限流", remote_code=safe_code)
        if safe_code in ("10001", "98000001", "98000002"):
            raise AuthError("Partner 业务认证失效")
        raise PartnerProtocolError(
            "Partner 业务错误",
            remote_code=safe_code,
        )

    def _post(self, url: str, body: dict, tag: str, wait_pause: bool = True) -> dict:
        self._gate(wait_pause)
        resp = self.session.post(url, headers=self.headers,
                                 params=self._base_params(), json=body, timeout=30)
        return self._decode(resp, tag)

    def _get(self, url: str, extra_params: dict, tag: str, wait_pause: bool = True) -> dict:
        self._gate(wait_pause)
        params = {**self._base_params(), **(extra_params or {})}
        resp = self.session.get(url, headers=self.headers, params=params, timeout=30)
        return self._decode(resp, tag)

    def find(self, query: str, query_type: int = 1, page: int = 0, wait_pause: bool = True) -> dict:
        """按 handle 搜索达人（query_type=1）。返回原始 json（含 creator_profile_list，轻量卡）。
        wait_pause=False：跳过全局暂停门（仅供限流恢复探针使用，避免与暂停死锁）。"""
        from ..hub.markets import require_capability

        require_capability(self.market, "find")
        body = {
            "query": query,
            "pagination": {"size": self.cfg.find_page_size, "page": page},
            "query_type": query_type, "filter_params": {}, "algorithm": 1,
        }
        return self._post(self.find_url, body, "find", wait_pause=wait_pause)

    def profile(self, oec_id: str, profile_types: list[int]) -> dict:
        """按 oec_id 取指定 profile 分型（含 creator_profile）。必须分型单独调，合并调会缩水。"""
        from ..hub.markets import require_capability

        require_capability(self.market, "profile")
        body = {"creator_oec_id": str(oec_id), "profile_types": profile_types}
        return self._post(self.profile_url, body, "profile")

    def relation_list(self, cur_page: int = 1, page_size: int = 50, status: int = 0,
                      related_biz_types: str = "2,6", relation_tag: int = 0,
                      wait_pause: bool = True) -> dict:
        """⑤达人绑定关系列表(GET, 免签名, 复用 enrich cookie)。返回原始 json(含 data.relations)。
        默认 status=0(全状态)/related_biz_types=2,6(数据+佣金绑定)——探针实测能回全量。"""
        from ..hub.markets import require_capability

        require_capability(self.market, "relation")
        extra = {"status": status, "page_size": page_size, "cur_page": cur_page,
                 "related_biz_types": related_biz_types, "relation_tag": relation_tag}
        return self._get(self.relation_url, extra, "relation", wait_pause=wait_pause)

    def campaign_list(self, cur_page: int = 1, page_size: int = 50, crs_types: str = "1,4,6,2",
                      join_status_category: int = 1, online_status: int = 0,
                      wait_pause: bool = True) -> dict:
        """⑦货盘列表(GET, 免签名, 复用 cookie)。partner_id 覆盖为货盘账号。返回原始 json(data 内含货盘列表)。
        crs_types/join_status_category 沿用探针参数(实测能回全量 342)。"""
        from ..hub.markets import require_capability

        require_capability(self.market, "campaign")
        if not self.campaign_partner_id or self.campaign_partner_id.startswith("__"):
            raise PartnerProtocolError("市场货盘身份参数未配置")
        extra = {"partner_id": self.campaign_partner_id, "page_size": page_size, "cur_page": cur_page,
                 "campaign_join_status_category": join_status_category,
                 "crs_campaign_types": crs_types, "campaign_online_status": online_status}
        return self._get(self.campaign_list_url, extra, "campaign_list", wait_pause=wait_pause)

    def campaign_product_list(self, campaign_id: str, cur_page: int = 1, page_size: int = 20,
                              wait_pause: bool = True) -> dict:
        """⑦货盘下单品明细(GET, 免签名)。返回原始 json(data.campaign_product[] + total_num)。"""
        from ..hub.markets import require_capability

        require_capability(self.market, "campaign")
        if not self.campaign_partner_id or self.campaign_partner_id.startswith("__"):
            raise PartnerProtocolError("市场货盘身份参数未配置")
        extra = {"partner_id": self.campaign_partner_id, "campaign_id": str(campaign_id),
                 "page_size": page_size, "cur_page": cur_page}
        return self._get(self.campaign_product_url, extra, "campaign_product", wait_pause=wait_pause)
