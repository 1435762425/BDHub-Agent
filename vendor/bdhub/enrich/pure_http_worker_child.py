# -*- coding: utf-8 -*-
"""纯 HTTP worker 子进程协议；仅由 ``pure_http_worker`` 启动。"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import shutil
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from pathlib import Path
from queue import Empty, Queue
from threading import Lock
from types import MethodType
from typing import Any, Callable
from urllib.parse import parse_qsl, urlencode, urlsplit

from ..hub.models import parse_find_item
from .creator_profile import merge_profiles


_SUPPORTED_REQUIRED_FIELDS = frozenset({"followers", "gmv_value"})


_SIGNER_REGIONS = {
    # SG 保留已经通过 MX 生产验收的附件组合和 TTP collector 上下文。
    "sg": {
        "loader": "",
        "core": "",
        "web_region": "ttp",
        "unisec_region": "ttp",
        "ms_endpoint": "https://mssdk.tiktokw.us/web/resource?eq=",
        "solver_region": "sg",
        "verification_host": "api-verification.tiktokshop.com",
    },
    # EU loader 会读取 api-verification.tiktokshops.eu / js_url_v2.eu。
    "eu": {
        "loader": "eu_loader.js",
        "core": "eu_core.js",
        "web_region": "eu-ttp",
        "unisec_region": "eu",
        "ms_endpoint": "https://mssdk.tiktokw.eu/web/resource?eq=",
        "solver_region": "eu",
        "verification_host": "api-verification.tiktokshops.eu",
    },
    # 美国 loader 会读取 api-verification.tiktokshops.us / js_url_v2.ttp。
    "us": {
        "loader": "us_loader.js",
        "core": "us_core.js",
        "web_region": "ttp",
        "unisec_region": "ttp",
        "ms_endpoint": "https://mssdk.tiktokw.us/web/resource?eq=",
        "solver_region": "us",
        "verification_host": "api-verification.tiktokshops.us",
    },
}


def _emit(payload: dict[str, Any]) -> None:
    sys.__stdout__.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.__stdout__.flush()


def _load_runtime(runtime_dir: Path):
    sys.path.insert(0, str(runtime_dir))
    return importlib.import_module("pure_http_probe")


def _safe_code(error: Exception) -> str:
    detail = str(error).casefold()
    if "captcha exceeded max attempts" in detail or "slider_verify_failed_http_" in detail:
        return "profile_wall_verification_failed"
    if "code10000" in detail:
        return "profile_wall_verification_required"
    name = type(error).__name__.casefold()
    if "timeout" in name:
        return "request_timeout"
    if "request" in name:
        return "request_error"
    return "worker_error"


def _safe_remote_code(value: Any) -> str:
    """只暴露短远端状态码，不允许 message/body 混入父进程。"""
    if value is None or isinstance(value, bool):
        return ""
    normalized = str(value).strip()
    digits = normalized[1:] if normalized.startswith("-") else normalized
    if (
        digits
        and len(digits) <= 32
        and digits.isascii()
        and digits.isdigit()
    ):
        return normalized
    return ""


def _mark_payload_failure(
    event: dict[str, Any],
    payload: Any,
    profile: Any,
    *,
    stage: str,
) -> dict[str, Any]:
    """把 Profile 失败压缩成可持久化的阶段、类型和远端 code。"""
    event["outcome"] = "profile_failed"
    remote_code = _safe_remote_code(
        payload.get("code") if isinstance(payload, dict) else None
    )
    if remote_code and remote_code != "0":
        event["error_code"] = (
            f"{stage}_transient_system_error_3"
            if remote_code == "100000"
            and payload.get("_bdhub_system_error_3") is True
            else f"{stage}_remote_error"
        )
        event["remote_code"] = remote_code
    elif not isinstance(payload, dict):
        event["error_code"] = f"{stage}_invalid_response"
    elif not isinstance(profile, dict):
        event["error_code"] = f"{stage}_missing"
    else:
        event["error_code"] = f"{stage}_empty"
    return event


def _attach_response_diagnostics(client: Any) -> None:
    """保留运行包内部重试后的固定响应头诊断，不外送其它响应数据。"""
    original = getattr(client, "post", None)
    if not callable(original):
        return

    def observed_post(stage: str, body: dict[str, Any]):
        response, payload = original(stage, body)
        headers = getattr(response, "headers", {})
        header_get = getattr(headers, "get", None)
        if (
            isinstance(payload, dict)
            and str(payload.get("code")) == "100000"
            and callable(header_get)
            and str(header_get("x-tt-system-error")) == "3"
        ):
            payload = {**payload, "_bdhub_system_error_3": True}
        return response, payload

    client.post = observed_post


def _configure_market_signer_context(
    probe: Any,
    account: dict[str, Any],
) -> tuple[str, str, str] | None:
    """把页面 Origin/Referer 同步到哈希运行包的 Bsid 签名器。

    运行包的业务 URL 和 ``requests.Session`` 可以由外层改写，但它内部的
    ``signer_impl._make_signer_input`` 仍会为 Node SDK 构造固定 MX 页面上下文。
    新市场若只改 HTTP 头，verifyV2 虽然成功，重放签名仍会绑定到 MX 页面并
    再次触发挑战。这里仅覆盖公开页面上下文，不修改或复制任何账号身份。
    """
    api_host = str(account.get("api_host") or "").strip().rstrip("/")
    page_url = str(account.get("page_url") or "").strip()
    aid = str(account.get("aid") or "").strip()
    if not api_host and not page_url and not aid:
        return None
    if not page_url.startswith("https://") or not aid.isdigit():
        raise ValueError("market_page_url_invalid")
    page_parts = urlsplit(page_url)
    if not page_parts.netloc or not page_parts.path:
        raise ValueError("market_page_url_invalid")
    portal_origin = f"{page_parts.scheme}://{page_parts.netloc}"

    signer_module = getattr(probe, "signer_impl", None)
    original = getattr(
        signer_module,
        "_bdhub_original_make_signer_input",
        None,
    )
    if original is None:
        original = getattr(signer_module, "_make_signer_input", None)
        if not callable(original):
            raise ValueError("market_signer_context_unsupported")
        setattr(
            signer_module,
            "_bdhub_original_make_signer_input",
            original,
        )

    def market_make_signer_input(payload: dict[str, Any]) -> dict[str, Any]:
        signer_input = original(payload)
        if not isinstance(signer_input, dict):
            raise ValueError("market_signer_input_invalid")
        headers = signer_input.get("headers")
        if not isinstance(headers, dict):
            raise ValueError("market_signer_headers_invalid")
        headers.update({
            "Origin": portal_origin,
            "Referer": page_url,
        })
        signer_input["page_url"] = page_url
        return signer_input

    signer_module._make_signer_input = market_make_signer_input
    return page_url, portal_origin, aid


def _market_signer_source(source: str, *, mx_partner_scope: bool = False) -> str:
    """从已校验的运行包生成只含公开市场上下文的临时入口。"""
    page_marker = (
        "const initialPageUrl = new URL(\n"
        "  (signerInput && signerInput.page_url)"
    )
    if source.count(page_marker) != 1:
        raise ValueError("market_signer_page_marker_invalid")
    patched = source.replace(
        page_marker,
        (
            "const initialPageUrl = new URL(\n"
            "  process.env.BDHUB_SIGNER_PAGE_URL\n"
            "    || (signerInput && signerInput.page_url)"
        ),
        1,
    )
    aid_marker = "aid: 360019,"
    if patched.count(aid_marker) != 3:
        raise ValueError("market_signer_aid_marker_invalid")
    patched = patched.replace(
        aid_marker,
        'aid: Number(process.env.BDHUB_SIGNER_AID || "360019"),',
    )
    origin_marker = '"Origin": "https://partner.tiktokshop.com",'
    if patched.count(origin_marker) != 1:
        raise ValueError("market_signer_origin_marker_invalid")
    patched = patched.replace(
        origin_marker,
        '"Origin": initialPageUrl.origin,',
        1,
    )
    region_marker = 'region: "ttp",'
    if patched.count(region_marker) != 3:
        raise ValueError("market_signer_region_marker_invalid")
    patched = patched.replace(
        region_marker,
        'region: process.env.BDHUB_SIGNER_WEB_REGION || "ttp",',
        1,
    )
    patched = patched.replace(
        region_marker,
        'region: process.env.BDHUB_SIGNER_UNISEC_REGION || "ttp",',
        2,
    )
    endpoint_marker = (
        'nodeFetch("https://mssdk.tiktokw.us/web/resource?eq=")'
    )
    if patched.count(endpoint_marker) != 1:
        raise ValueError("market_signer_ms_endpoint_marker_invalid")
    patched = patched.replace(
        endpoint_marker,
        "nodeFetch(process.env.BDHUB_SIGNER_MS_ENDPOINT "
        '|| "https://mssdk.tiktokw.us/web/resource?eq=")',
        1,
    )
    referrer_marker = 'referrer: "https://shop.tiktok.com/",'
    if patched.count(referrer_marker) != 1:
        raise ValueError("market_signer_referrer_marker_invalid")
    patched = patched.replace(
        referrer_marker,
        'referrer: `${initialPageUrl.origin}/`,',
        1,
    )
    blob_marker = (
        'static createObjectURL() { return '
        '"blob:https://partner.tiktokshop.com/mock"; }'
    )
    if patched.count(blob_marker) != 1:
        raise ValueError("market_signer_blob_marker_invalid")
    patched = patched.replace(
        blob_marker,
        "static createObjectURL() { return "
        '`${initialPageUrl.origin.replace("https:", "blob:https:")}/mock`; }',
        1,
    )
    if mx_partner_scope:
        # 2026-09-06原生页面首个Web SDK配置；Find/Profile由Unisec负责。
        # 把Web SDK也应用到所有API会额外改写签名，合法请求因此触发验证。
        marker = 'enablePathList: ["/api/*"],'
        if patched.count(marker) != 1:
            raise ValueError("market_signer_web_scope_marker_invalid")
        paths = [
            "/api/v.*/insights/partner",
            "/api/v.*/partner/profile/submit_entity_form",
            "/api/v.*/partner/profile/edit_entity_form",
            "/api/v.*/partner/profile/batch_submit_form",
            "/api/v.*/partner/profile/edit_form",
            "/api/v.*/partner/profile/submit_form",
            "/passport/(?!(web/logout)).",
        ]
        patched = patched.replace(marker, "enablePathList: " + json.dumps(paths) + ",", 1)
    return patched


def _is_mx_partner_context(account: dict) -> bool:
    page = urlsplit(str(account.get("page_url") or ""))
    api = urlsplit(str(account.get("api_host") or ""))
    return (
        api.scheme == "https" and api.hostname == "api-partner-sg.tiktokshop.com"
        and page.scheme == "https" and page.path == "/affiliate-cmp/creator"
        and page.hostname == "partner.tiktokshop.com"
        and dict(parse_qsl(page.query)).get("market") == "19"
        and str(account.get("aid")) == "360019"
    )


def _market_captcha_signer_source(source: str) -> str:
    """参数化附件验证码签名入口中的公开市场上下文。"""
    patched = source
    aid_marker = "aid: 360019,"
    if patched.count(aid_marker) != 3:
        raise ValueError("market_captcha_signer_aid_marker_invalid")
    patched = patched.replace(
        aid_marker,
        'aid: Number(process.env.BDHUB_SIGNER_AID || "360019"),',
    )
    region_marker = 'region: "sg",'
    if patched.count(region_marker) != 3:
        raise ValueError("market_captcha_signer_region_marker_invalid")
    patched = patched.replace(
        region_marker,
        'region: process.env.BDHUB_SIGNER_WEB_REGION || "sg",',
        1,
    )
    patched = patched.replace(
        region_marker,
        'region: process.env.BDHUB_SIGNER_UNISEC_REGION || "sg",',
        2,
    )
    endpoint_marker = (
        'nodeFetch("https://mssdk-sg.byteoversea.com/web/resource?eq=")'
    )
    if patched.count(endpoint_marker) != 1:
        raise ValueError("market_captcha_signer_ms_endpoint_marker_invalid")
    patched = patched.replace(
        endpoint_marker,
        "nodeFetch(process.env.BDHUB_SIGNER_MS_ENDPOINT "
        '|| "https://mssdk-sg.byteoversea.com/web/resource?eq=")',
        1,
    )
    replacements = (
        (
            'referrer: "https://partner.tiktokshop.com/",',
            'referrer: `${initialPageUrl.origin}/`,',
            "market_captcha_signer_referrer_marker_invalid",
        ),
        (
            'static createObjectURL() { return '
            '"blob:https://partner.tiktokshop.com/mock"; }',
            "static createObjectURL() { return "
            '`${initialPageUrl.origin.replace("https:", "blob:https:")}/mock`; }',
            "market_captcha_signer_blob_marker_invalid",
        ),
        (
            '"Origin": "https://partner.tiktokshop.com",',
            '"Origin": initialPageUrl.origin,',
            "market_captcha_signer_origin_marker_invalid",
        ),
    )
    for marker, replacement, error_code in replacements:
        if patched.count(marker) != 1:
            raise ValueError(error_code)
        patched = patched.replace(marker, replacement, 1)
    return patched


def _configure_market_captcha_runtime(
    probe: Any,
    account: dict[str, Any],
    temporary: str | Path,
) -> None:
    """为 EU/US 生成临时验证码 SDK 组合；SG 保留已验收原链。"""
    region = str(account.get("signer_region") or "").strip().casefold()
    if not region and not any(
        str(account.get(key) or "").strip()
        for key in ("api_host", "page_url", "aid")
    ):
        return
    region_config = _SIGNER_REGIONS.get(region)
    if region_config is None:
        raise ValueError("market_signer_region_invalid")
    if region == "sg":
        return

    signer_module = getattr(probe, "signer_impl", None)
    runtime_root = Path(str(
        getattr(signer_module, "_bdhub_runtime_root", "")
    )).resolve()
    if not runtime_root.is_dir():
        raise ValueError("market_captcha_runtime_root_missing")
    captcha_source = runtime_root / "captcha_signer_sg.js"
    sdk_root = runtime_root / "sdk_runtime"
    if not captcha_source.is_file():
        raise ValueError("market_captcha_signer_source_missing")
    try:
        captcha_text = captcha_source.read_text(encoding="utf-8")
    except OSError as error:
        raise ValueError("market_captcha_signer_source_unreadable") from error

    slider_class = getattr(probe, "PureRequestSolver", None)
    options_class = getattr(probe, "SolverOptions", None)
    if not isinstance(slider_class, type) or not callable(options_class):
        raise ValueError("market_captcha_runtime_unsupported")
    slider_module = sys.modules.get(str(getattr(slider_class, "__module__", "")))
    ensure_embedded = getattr(slider_module, "ensure_embedded_sdk_runtime", None)
    if slider_module is None or not callable(ensure_embedded):
        raise ValueError("market_captcha_runtime_unsupported")
    embedded_source = Path(ensure_embedded()).resolve()
    if not embedded_source.is_dir():
        raise ValueError("market_captcha_embedded_runtime_missing")

    temporary_root = Path(temporary) / "captcha-market-runtime"
    script_root = temporary_root / "script"
    selected_sdk = script_root / "sdk_runtime"
    embedded_root = temporary_root / "embedded"
    selected_sdk.mkdir(parents=True, exist_ok=True)
    embedded_root.mkdir(parents=True, exist_ok=True)

    captcha_target = script_root / "captcha_signer_sg.js"
    captcha_target.write_text(
        _market_captcha_signer_source(captcha_text),
        encoding="utf-8",
    )
    captcha_target.chmod(0o600)
    resources = (
        (sdk_root / "sg_webmssdk.js", selected_sdk / "sg_webmssdk.js"),
        (
            sdk_root / str(region_config["loader"]),
            selected_sdk / "sg_loader.js",
        ),
    )
    for source, destination in resources:
        if not source.is_file():
            raise ValueError("market_captcha_region_resource_missing")
        shutil.copy2(source, destination)
    for source in embedded_source.iterdir():
        if source.is_file() and source.name != "unisec_core.js":
            shutil.copy2(source, embedded_root / source.name)
    regional_core = sdk_root / str(region_config["core"])
    if not regional_core.is_file():
        raise ValueError("market_captcha_region_resource_missing")
    shutil.copy2(regional_core, embedded_root / "unisec_core.js")

    page_url = str(account.get("page_url") or "").strip()
    page_parts = urlsplit(page_url)
    if not page_parts.netloc:
        raise ValueError("market_page_url_invalid")
    portal_origin = f"{page_parts.scheme}://{page_parts.netloc}"
    verification_host = str(region_config["verification_host"])
    solver_region = str(region_config["solver_region"])

    class MarketRequestSolver(slider_class):
        @property
        def shop_base(self) -> str:
            return portal_origin

        @property
        def verification_host(self) -> str:
            return verification_host

        def build_signer_task(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
            task = super().build_signer_task(*args, **kwargs)
            task["page_url"] = page_url
            payload = task.get("payload")
            if isinstance(payload, dict):
                payload["lo"] = {
                    "referer": "",
                    "location": page_url,
                    "topref": page_url,
                    "frame": 0,
                }
            return task

    def MarketSolverOptions(*args: Any, **kwargs: Any) -> Any:
        kwargs["region"] = solver_region
        return options_class(*args, **kwargs)

    probe.PureRequestSolver = MarketRequestSolver
    probe.SolverOptions = MarketSolverOptions
    slider_module.SCRIPT_DIR = script_root
    slider_module.ensure_embedded_sdk_runtime = lambda: embedded_root


def _configure_market_signer_runtime(
    probe: Any,
    account: dict[str, Any],
    temporary: str | Path,
) -> None:
    """让 server-mode SDK 在读取首个 stdin 身份前取得正确公开页面上下文。"""
    market_context = _configure_market_signer_context(probe, account)
    if market_context is None:
        return
    page_url, _portal_origin, aid = market_context
    region = str(account.get("signer_region") or "").strip().casefold()
    region_config = _SIGNER_REGIONS.get(region)
    if region_config is None:
        raise ValueError("market_signer_region_invalid")
    signer_module = getattr(probe, "signer_impl", None)
    source_path = Path(str(getattr(signer_module, "SIGNER_SCRIPT", "")))
    if not source_path.is_file():
        raise ValueError("market_signer_source_missing")
    setattr(signer_module, "_bdhub_runtime_root", source_path.parent.resolve())
    try:
        source = source_path.read_text(encoding="utf-8")
    except OSError as error:
        raise ValueError("market_signer_source_unreadable") from error
    temporary_root = Path(temporary)
    temporary_root.mkdir(parents=True, exist_ok=True)
    patched_path = temporary_root / "partner_request_signer.market.js"
    patched_path.write_text(
        _market_signer_source(source, mx_partner_scope=_is_mx_partner_context(account)),
        encoding="utf-8",
    )
    patched_path.chmod(0o600)
    signer_module.SIGNER_SCRIPT = patched_path
    node_modules = (source_path.parent / "node_modules").resolve()
    if not node_modules.is_dir():
        raise ValueError("market_signer_node_modules_missing")
    current_node_path = os.environ.get("NODE_PATH", "")
    os.environ["NODE_PATH"] = str(node_modules) + (
        os.pathsep + current_node_path if current_node_path else ""
    )
    sdk_root = (source_path.parent / "sdk_runtime").resolve()
    for attribute, key in (
        ("UNISEC_LOADER", "loader"),
        ("UNISEC_CORE", "core"),
    ):
        relative = str(region_config[key])
        if not relative:
            continue
        resource = (sdk_root / relative).resolve()
        if not resource.is_relative_to(sdk_root) or not resource.is_file():
            raise ValueError("market_signer_region_resource_missing")
        setattr(signer_module, attribute, resource)
    os.environ["BDHUB_SIGNER_PAGE_URL"] = page_url
    os.environ["BDHUB_SIGNER_AID"] = aid
    os.environ["BDHUB_SIGNER_WEB_REGION"] = str(
        region_config["web_region"]
    )
    os.environ["BDHUB_SIGNER_UNISEC_REGION"] = str(
        region_config["unisec_region"]
    )
    os.environ["BDHUB_SIGNER_MS_ENDPOINT"] = str(
        region_config["ms_endpoint"]
    )


def _configure_market_transport(client: Any, account: dict[str, Any]) -> None:
    """在不修改哈希锁定运行包的前提下注入现场市场传输参数。

    附件运行包最初只为 MX 构建，内部固定了 SG Host、360019 和 MX
    Referer。签名器、滑块求解和请求节拍继续完全复用原实现；这里只改写
    URL/Origin/Referer 与验证码上下文中的公开市场参数。
    """
    api_host = str(account.get("api_host") or "").strip().rstrip("/")
    page_url = str(account.get("page_url") or "").strip()
    aid = str(account.get("aid") or "").strip()
    user_language = str(account.get("user_language") or "zh-CN").strip()
    if not api_host and not page_url and not aid:
        # 兼容历史 MX 单元测试与旧的受控调用；生产父进程总会显式注入。
        return
    if (
        not api_host.startswith("https://")
        or not page_url.startswith("https://")
        or not aid.isdigit()
        or not user_language
    ):
        raise ValueError("market_transport_invalid")
    page_parts = urlsplit(page_url)
    if not page_parts.netloc or not page_parts.path:
        raise ValueError("market_page_url_invalid")
    portal_origin = f"{page_parts.scheme}://{page_parts.netloc}"

    original_unsigned_url = client._unsigned_url

    def market_unsigned_url(self: Any, stage: str) -> str:
        original = urlsplit(original_unsigned_url(stage))
        params = parse_qsl(original.query, keep_blank_values=True)
        rewritten = [
            (
                name,
                aid if name == "aid" else (
                    user_language if name == "user_language" else value
                ),
            )
            for name, value in params
        ]
        return (
            f"{api_host}{original.path}?"
            f"{urlencode(rewritten)}"
        )

    client._unsigned_url = MethodType(market_unsigned_url, client)
    client.session.headers.update({
        "Origin": portal_origin,
        "Referer": page_url,
    })

    original_captcha_context = client._captcha_context

    def market_captcha_context(self: Any, verify_data: dict[str, Any]) -> dict[str, Any]:
        context = original_captcha_context(verify_data)
        headers = context.setdefault("headers", {})
        headers["Origin"] = portal_origin
        headers["Referer"] = page_url
        params = context.setdefault("captcha_get_params", {})
        params["aid"] = aid
        params["refer_path"] = page_parts.path + (
            f"?{page_parts.query}" if page_parts.query else ""
        )
        payload_extra = context.setdefault("payload_extra", {})
        if isinstance(payload_extra, dict):
            payload_extra["lo"] = {
                "referer": "",
                "location": page_url,
                "topref": page_url,
                "frame": 0,
            }
        return context

    client._captcha_context = MethodType(market_captcha_context, client)


def _fatal_code(error: Exception) -> str:
    """只向父进程暴露白名单错误类别，绝不透传账号或请求正文。"""
    if isinstance(error, (ModuleNotFoundError, ImportError)):
        return "runtime_dependency_missing"
    if isinstance(error, AttributeError):
        return "runtime_dependency_incompatible"
    if isinstance(error, OSError):
        return "runtime_io_error"
    if isinstance(error, (TypeError, ValueError)):
        return "worker_configuration_invalid"
    return "worker_start_failed"


def _target(value: Any) -> tuple[str, str, str]:
    if not isinstance(value, dict):
        raise ValueError("target_invalid")
    handle = str(value.get("handle") or "").strip().lstrip("@").casefold()
    oec_id = str(value.get("oec_id") or "").strip()
    audit_handle = (
        str(value.get("audit_handle") or "")
        .strip()
        .lstrip("@")
        .casefold()
    )
    if not handle and not oec_id:
        raise ValueError("target_identity_required")
    if oec_id and not oec_id.isdigit():
        raise ValueError("target_oec_invalid")
    return handle, oec_id, audit_handle


def _oec(item: dict[str, Any]) -> str:
    value = item.get("creator_oecuid")
    if isinstance(value, dict):
        value = value.get("value")
    return str(value or "").strip()


def _handle(item: dict[str, Any]) -> str:
    value = item.get("handle")
    if isinstance(value, dict):
        value = value.get("value")
    return str(value or "").strip().lstrip("@").casefold()


def _profile_with_types(
    client: Any,
    oec_id: str,
    profile_types: tuple[int, ...],
) -> dict[str, Any]:
    """在同一账号 lane 内临时切换 Profile 分型，调用后恢复默认分型。"""
    original = getattr(client, "profile_types", None)
    if not isinstance(original, (list, tuple)):
        raise ValueError("profile_types_switch_unsupported")
    client.profile_types = list(profile_types)
    try:
        return client.profile(oec_id)
    finally:
        client.profile_types = original


def _missing_required_fields(
    profile: dict[str, Any],
    required_fields: frozenset[str],
) -> frozenset[str]:
    """按规范解析结果判断核心字段是否真实可用；0 仍是有效值。"""
    if not required_fields:
        return frozenset()
    try:
        parsed = parse_find_item(profile)
    except Exception:
        return required_fields
    missing = {
        field_name
        for field_name in required_fields
        if getattr(parsed, field_name, None) is None
    }
    return frozenset(missing)


def _execute_target(
    client: Any,
    probe: Any,
    index: int,
    raw_target: Any,
    *,
    required_fields: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    event: dict[str, Any] = {"type": "result", "index": index}
    try:
        handle, expected_oec, audit_handle = _target(raw_target)
        if handle:
            find_payload = client.find(handle)
            exact = probe.find_exact(find_payload, handle)
            if find_payload.get("code") != 0:
                event["outcome"] = "find_failed"
                event["error_code"] = "find_remote_error"
                remote_code = _safe_remote_code(find_payload.get("code"))
                if remote_code:
                    event["remote_code"] = remote_code
                return event
            if not isinstance(exact, dict):
                event["outcome"] = "find_exact_miss"
                return event
            actual_oec = _oec(exact)
            if expected_oec and actual_oec != expected_oec:
                event["outcome"] = "oec_mismatch"
                return event
            profile_payload = client.profile(actual_oec)
            profile = profile_payload.get("creator_profile")
            if (
                profile_payload.get("code") != 0
                or not isinstance(profile, dict)
                or not profile
            ):
                return _mark_payload_failure(
                    event,
                    profile_payload,
                    profile,
                    stage="profile",
                )
            merged = merge_profiles([exact, profile])
        else:
            profile_payload = client.profile(expected_oec)
            profile = profile_payload.get("creator_profile")
            if (
                profile_payload.get("code") != 0
                or not isinstance(profile, dict)
                or not profile
            ):
                return _mark_payload_failure(
                    event,
                    profile_payload,
                    profile,
                    stage="profile",
                )
            merged = dict(profile)
            actual_oec = _oec(merged)

        # 组合 [1,2,6] 在生产响应中可能只返回身份或经营字段的一侧。
        # 保留完整组合的单请求快路；只有真实缺失时才按同一账号、同一 OEC
        # 定向补抓 [1,6] / [2]，并继续受该账号请求级 pacer 约束。
        missing_fields = _missing_required_fields(merged, required_fields)
        needs_identity = not handle and not audit_handle and not _handle(merged)
        supplements: list[tuple[tuple[int, ...], str]] = []
        if needs_identity or "followers" in missing_fields:
            supplements.append(((1, 6), "identity"))
        if "gmv_value" in missing_fields:
            supplements.append(((2,), "metrics"))
        for profile_types, stage in supplements:
            supplemental_payload = _profile_with_types(
                client,
                actual_oec or expected_oec,
                profile_types,
            )
            supplemental_profile = supplemental_payload.get("creator_profile")
            if (
                supplemental_payload.get("code") != 0
                or not isinstance(supplemental_profile, dict)
                or not supplemental_profile
            ):
                return _mark_payload_failure(
                    event,
                    supplemental_payload,
                    supplemental_profile,
                    stage=stage,
                )
            if _oec(supplemental_profile) != (actual_oec or expected_oec):
                event["outcome"] = "identity_mismatch"
                return event
            merged = merge_profiles([merged, supplemental_profile])
            actual_oec = _oec(merged)

        if _missing_required_fields(merged, required_fields):
            event["outcome"] = "profile_incomplete"
            event["error_code"] = "profile_required_fields_missing"
            return event
        if (
            (handle and _handle(merged) != handle)
            or (not handle and not audit_handle and not _handle(merged))
            or not actual_oec
            or (expected_oec and actual_oec != expected_oec)
        ):
            event["outcome"] = "identity_mismatch"
        else:
            event["outcome"] = "ok"
            event["profile"] = merged
    except Exception as error:
        event["outcome"] = "error"
        event["error_code"] = _safe_code(error)
    return event


def execute_targets(
    probe: Any,
    config: dict[str, Any],
    account: dict[str, Any],
    targets: list[Any],
    temporary: str | Path,
    *,
    concurrency: int,
    event_sink: Callable[[dict[str, Any]], None] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """在单账号独立 3 QPS pacer 下并发隐藏网络等待，不跨账号共享额度。"""
    if type(concurrency) is not int or not 1 <= concurrency <= 16:
        raise ValueError("concurrency_invalid")
    if not targets:
        return [], {
            "request_count": 0,
            "challenge_count": 0,
            "challenge_success_count": 0,
        }

    lane_count = min(concurrency, len(targets))
    output_root = Path(temporary)
    account_pacer = probe.StrictRequestPacer(float(config.get("qps") or 3.0))
    _configure_market_signer_runtime(probe, account, output_root)
    _configure_market_captcha_runtime(probe, account, output_root)
    clients = []
    for lane in range(lane_count):
        lane_dir = output_root / f"lane-{lane + 1:02d}"
        lane_dir.mkdir(parents=True, exist_ok=True)
        client = probe.PureHttpPartnerClient(config, account, lane_dir)
        _configure_market_transport(client, account)
        _attach_response_diagnostics(client)
        # 每个 lane 有独立 Session/滑块状态，但请求起点必须经过本账号唯一 pacer。
        client.pacer = account_pacer
        clients.append(client)

    raw_required_fields = config.get("required_fields", [])
    if (
        not isinstance(raw_required_fields, list)
        or any(not isinstance(field_name, str) for field_name in raw_required_fields)
    ):
        raise ValueError("required_fields_invalid")
    required_fields = frozenset(raw_required_fields)
    if not required_fields.issubset(_SUPPORTED_REQUIRED_FIELDS):
        raise ValueError("required_fields_invalid")

    pending: Queue[tuple[int, Any]] = Queue()
    for index, target in enumerate(targets, 1):
        pending.put((index, target))
    events: list[dict[str, Any]] = []
    events_lock = Lock()

    def consume(client: Any) -> None:
        while True:
            try:
                index, target = pending.get_nowait()
            except Empty:
                return
            requests_before = int(client.request_count)
            event = _execute_target(
                client,
                probe,
                index,
                target,
                required_fields=required_fields,
            )
            event["request_count_delta"] = max(0, int(client.request_count) - requests_before)
            with events_lock:
                events.append(event)
                if event_sink is not None:
                    event_sink(event)
            pending.task_done()

    with ThreadPoolExecutor(
        max_workers=lane_count,
        thread_name_prefix="pure-http-account-lane",
    ) as executor:
        futures = [executor.submit(consume, client) for client in clients]
        for future in futures:
            future.result()

    events.sort(key=lambda event: int(event["index"]))
    summary = {
        "request_count": sum(int(client.request_count) for client in clients),
        "challenge_count": sum(int(client.challenge_count) for client in clients),
        "challenge_success_count": sum(
            int(client.captcha_success_count) for client in clients
        ),
    }
    return events, summary


def run(args: argparse.Namespace) -> int:
    runtime = Path(args.runtime_dir).resolve()
    probe = _load_runtime(runtime)
    try:
        request = json.loads(sys.stdin.read())
    except json.JSONDecodeError:
        return 2
    if not isinstance(request, dict):
        return 2
    account = request.get("account")
    targets = request.get("targets")
    required_fields = request.get("required_fields", [])
    if (
        not isinstance(account, dict)
        or not isinstance(targets, list)
        or not isinstance(required_fields, list)
    ):
        return 2
    try:
        profile_types = [
            int(value.strip())
            for value in args.profile_types.split(",")
            if value.strip()
        ]
    except ValueError:
        return 2
    config = probe.read_json(runtime / "config.json")
    config.update({
        "qps": float(args.qps),
        "captcha_attempts": int(args.captcha_attempts),
        "profile_types": profile_types,
        "required_fields": required_fields,
    })
    if str(account.get("partner_id") or "").strip():
        config["partner_id"] = str(account["partner_id"]).strip()

    with tempfile.TemporaryDirectory(prefix="bdhub_http_worker_") as temporary:
        with redirect_stdout(sys.stderr):
            events, summary = execute_targets(
                probe,
                config,
                account,
                targets,
                Path(temporary),
                concurrency=int(args.concurrency),
                event_sink=_emit,
            )
        _emit({
            "type": "summary",
            **summary,
        })
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--runtime-dir", required=True)
    parser.add_argument("--qps", type=float, required=True)
    parser.add_argument("--profile-types", required=True)
    parser.add_argument("--captcha-attempts", type=int, required=True)
    parser.add_argument("--concurrency", type=int, required=True)
    return parser


if __name__ == "__main__":
    try:
        raise SystemExit(run(_parser().parse_args()))
    except Exception as error:
        # 子进程不得把异常正文、请求体或账号身份透传给父任务日志；但必须
        # 返回可诊断的白名单类别，否则整批平台依赖错误只会变成 worker_failed。
        _emit({"type": "fatal", "error_code": _fatal_code(error)})
        raise SystemExit(1)
