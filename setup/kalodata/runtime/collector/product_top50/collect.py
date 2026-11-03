#!/usr/bin/env python3
"""Collect Kalodata product live/video creator Top 50 data."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
import json
import math
from pathlib import Path
import re
import sys
import time
from typing import Any

from curl_cffi import requests


PROJECT_DIR = Path(__file__).resolve().parent.parent
LOGIN_DIR = PROJECT_DIR / "Kalodata登录器独立版"
COOKIE_PATH = PROJECT_DIR / "Kalodata登录器独立版" / "latest_cookie.txt"
EXTENSION_RUNTIME_PATH = PROJECT_DIR / "Kalodata登录器独立版" / "extension_runtime.json"
DETAIL_CACHE_PATH = Path(__file__).resolve().parent / ".video_detail_cache.json"
BASE_URL = "https://www.kalodata.com"
BLOCKED_STATUS_CODES = {401, 403, 429, 503}
UNIT_MULTIPLIERS = {"亿": 100_000_000, "万": 10_000, "千": 1_000, "k": 1_000, "K": 1_000, "w": 10_000, "W": 10_000, "m": 1_000_000, "M": 1_000_000}


class KalodataRequestError(RuntimeError):
    pass


_browser_transport: "BrowserTransport | None" = None


class BrowserTransport:
    """Run authenticated API requests inside the extension-enabled browser."""

    def __init__(self, pid: str, country: str, currency: str) -> None:
        self.pid = pid
        self.country = country
        self.currency = currency
        self.playwright = None
        self.context = None
        self.page = None
        self.extension_id = ""
        self.session_refreshed = False
        self._refresh_extension_login = None

    def __enter__(self) -> "BrowserTransport":
        if str(LOGIN_DIR) not in sys.path:
            sys.path.insert(0, str(LOGIN_DIR))
        from cookie_launcher import (
            launch_context,
            parse_cookie_header,
            read_saved_cookie,
            refresh_extension_login,
            wait_for_extension_id,
        )
        from playwright.sync_api import sync_playwright

        self.playwright = sync_playwright().start()
        self.context = launch_context(self.playwright)
        self.extension_id = wait_for_extension_id(self.context)
        self._refresh_extension_login = refresh_extension_login
        if not self.extension_id:
            raise KalodataRequestError("专用浏览器未加载 Kalodata 扩展。")
        saved_cookie = read_saved_cookie()
        parsed_cookies = parse_cookie_header(saved_cookie) if saved_cookie else []
        if parsed_cookies:
            self.context.add_cookies(parsed_cookies)
            print(f"已加载本地现有 Cookie：{len(parsed_cookies)} 项（未重新登录）。")
        self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
        self.page.goto(
            f"{BASE_URL}/product/detail?id={self.pid}&language=zh-CN"
            f"&currency={self.currency}&region={self.country}",
            wait_until="domcontentloaded",
            timeout=60_000,
        )
        self.page.wait_for_timeout(1_000)
        print("已连接扩展专用浏览器，API 将在浏览器登录态内执行。")
        return self

    def refresh_session_once(self) -> bool:
        if self.session_refreshed or self.context is None or not self.extension_id:
            return False
        self.session_refreshed = True
        if self._refresh_extension_login is None:
            return False
        print("现有 Cookie 返回401，调用扩展刷新登录态一次……")
        return bool(self._refresh_extension_login(self.context, self.extension_id))

    def __exit__(self, _exc_type: Any, _exc: Any, _traceback: Any) -> None:
        if self.context is not None:
            try:
                self.context.close()
            except Exception:
                pass
        if self.playwright is not None:
            try:
                self.playwright.stop()
            except Exception:
                pass

    def post_json(self, url: str, headers: dict[str, str], payload: dict[str, Any]) -> tuple[int, dict[str, Any] | None]:
        if self.page is None:
            raise KalodataRequestError("浏览器传输尚未启动。")
        safe_headers = {
            key: value
            for key, value in headers.items()
            if key.lower() not in {"cookie", "origin", "referer", "content-length"}
        }
        result = self.page.evaluate(
            """async ({url, headers, payload}) => {
                const response = await fetch(url, {
                    method: 'POST',
                    headers,
                    body: JSON.stringify(payload),
                    credentials: 'include',
                    cache: 'no-store',
                });
                return {status: response.status, text: await response.text()};
            }""",
            {"url": url, "headers": safe_headers, "payload": payload},
        )
        try:
            body = json.loads(result.get("text") or "")
        except json.JSONDecodeError:
            body = None
        return int(result.get("status") or 0), body if isinstance(body, dict) else None


def parse_compact_number(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "")
    match = re.search(r"([-+]?\d+(?:\.\d+)?)([亿万千kKwWmM]?)", text)
    return float(match.group(1)) * UNIT_MULTIPLIERS.get(match.group(2), 1) if match else 0.0


def normalize_int(value: Any) -> int:
    return int(round(parse_compact_number(value)))


def normalize_handle(value: Any) -> str:
    return str(value or "").strip().lstrip("@")


def parse_date(value: Any) -> str:
    text = str(value or "").strip().replace("/", "-")
    return text[:10] if re.match(r"^\d{4}-\d{2}-\d{2}", text) else ""


def read_cookie(path: Path = COOKIE_PATH) -> str:
    try:
        value = path.read_text(encoding="utf-8-sig").strip()
    except OSError as exc:
        raise KalodataRequestError(f"Cookie 文件不存在：{path}") from exc
    if not value or "\r" in value or "\n" in value:
        raise KalodataRequestError("Cookie 文件为空或格式无效。")
    return value


def load_proxy_url() -> str:
    try:
        payload = json.loads(EXTENSION_RUNTIME_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    return str(payload.get("proxy_url") or "").strip() if isinstance(payload, dict) else ""


def load_detail_cache() -> dict[str, dict[str, Any]]:
    try:
        data = json.loads(DETAIL_CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {
        str(key): value
        for key, value in data.items()
        if isinstance(value, dict) and normalize_handle(value.get("handle"))
    } if isinstance(data, dict) else {}


def save_detail_cache(cache: dict[str, dict[str, Any]]) -> None:
    temporary = DETAIL_CACHE_PATH.with_name(f".{DETAIL_CACHE_PATH.name}.tmp")
    temporary.write_text(json.dumps(cache, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(DETAIL_CACHE_PATH)


def seed_detail_cache_from_output(output: Path) -> None:
    if not output.is_file():
        return
    try:
        previous = json.loads(output.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    cache = load_detail_cache()
    changed = False
    for key in ("video_sales_top50", "video_published_top50"):
        rows = previous.get(key, []) if isinstance(previous, dict) else []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            video_id = str(row.get("id") or "")
            handle = normalize_handle(row.get("handle"))
            if video_id and handle and video_id not in cache:
                cache[video_id] = {
                    "id": video_id,
                    "handle": handle,
                    "release_time": row.get("create_time", ""),
                    "duration": row.get("duration", ""),
                    "views": row.get("views", 0),
                }
                changed = True
    if changed:
        save_detail_cache(cache)


def build_headers(cookie: str, pid: str, country: str, currency: str) -> dict[str, str]:
    return {
        "content-type": "application/json",
        "country": country,
        "currency": currency,
        "language": "zh-CN",
        "origin": BASE_URL,
        "referer": f"{BASE_URL}/product/detail?id={pid}&language=zh-CN&currency={currency}&region={country}",
        "cookie": cookie,
    }


def request_json(url: str, headers: dict[str, str], payload: dict[str, Any], retries: int = 2) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        if attempt:
            time.sleep(min(4.0, 0.75 * (2**attempt)))
        try:
            if _browser_transport is not None:
                status_code, data = _browser_transport.post_json(url, headers, payload)
                if status_code == 401 and _browser_transport.refresh_session_once():
                    status_code, data = _browser_transport.post_json(url, headers, payload)
            else:
                proxy_url = load_proxy_url()
                proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None
                response = requests.post(
                    url,
                    json=payload,
                    headers=headers,
                    timeout=25,
                    impersonate="chrome",
                    proxies=proxies,
                )
                status_code = response.status_code
                data = response.json()
            if status_code in BLOCKED_STATUS_CODES:
                raise KalodataRequestError(f"Kalodata 拒绝请求，HTTP {status_code}")
            if status_code != 200:
                raise KalodataRequestError(f"Kalodata 返回 HTTP {status_code}")
            if not isinstance(data, dict) or data.get("success") is False:
                raise KalodataRequestError("Kalodata 未返回成功数据，Cookie 可能已失效或请求过快。")
            return data
        except KalodataRequestError as exc:
            last_error = exc
            if attempt >= retries:
                raise
        except Exception as exc:
            last_error = exc
    raise KalodataRequestError(f"网络请求失败：{type(last_error).__name__ if last_error else 'unknown'}")


def build_payload(pid: str, start_date: str, end_date: str, page_no: int, page_size: int) -> dict[str, Any]:
    return {
        "id": pid,
        "startDate": start_date,
        "endDate": end_date,
        "authority": True,
        "pageNo": page_no,
        "pageSize": page_size,
        "sort": [{"field": "sale", "type": "DESC"}],
    }


def fetch_count(kind: str, headers: dict[str, str], payload: dict[str, Any]) -> int:
    data = request_json(f"{BASE_URL}/product/detail/{kind}/count", headers, payload).get("data", 0)
    return normalize_int(data)


def fetch_list(kind: str, headers: dict[str, str], payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = request_json(f"{BASE_URL}/product/detail/{kind}/queryList", headers, payload).get("data", [])
    if not isinstance(data, list):
        raise KalodataRequestError(f"{kind} 列表格式异常。")
    return [item for item in data if isinstance(item, dict)]


def fetch_product(pid: str, start_date: str, end_date: str, headers: dict[str, str]) -> dict[str, Any]:
    payload = build_payload(pid, start_date, end_date, 1, 1)
    try:
        data = request_json(f"{BASE_URL}/product/detail", headers, payload).get("data", {})
    except KalodataRequestError:
        # The current Kalodata frontend itself may receive HTTP 411 here while
        # list/count APIs remain healthy. Product metadata is non-essential.
        return {}
    return data if isinstance(data, dict) else {}


def fetch_video_detail(video_id: str, start_date: str, end_date: str, headers: dict[str, str]) -> dict[str, Any]:
    payload = {"id": video_id, "startDate": start_date, "endDate": end_date, "authority": True}
    try:
        retries = 0 if _browser_transport is not None else 2
        data = request_json(f"{BASE_URL}/video/detail", headers, payload, retries=retries).get("data", {})
        return data if isinstance(data, dict) else {}
    except KalodataRequestError:
        return {}


def enrich_videos(items: list[dict[str, Any]], start_date: str, end_date: str, headers: dict[str, str], workers: int) -> list[dict[str, Any]]:
    unique_items: dict[str, dict[str, Any]] = {
        str(item.get("id")): item for item in items if str(item.get("id") or "").strip()
    }
    cache = load_detail_cache()
    details: dict[str, dict[str, Any]] = {
        video_id: cache[video_id] for video_id in unique_items if video_id in cache
    }
    uncached_ids = [video_id for video_id in unique_items if video_id not in details]
    if _browser_transport is not None:
        for video_id in uncached_ids:
            detail = fetch_video_detail(video_id, start_date, end_date, headers)
            details[video_id] = detail
            if normalize_handle(detail.get("handle")):
                cache[video_id] = detail
                save_detail_cache(cache)
            time.sleep(1.5)
    else:
        with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
            futures = {
                executor.submit(fetch_video_detail, video_id, start_date, end_date, headers): video_id
                for video_id in uncached_ids
            }
            for future in as_completed(futures):
                video_id = futures[future]
                detail = future.result()
                details[video_id] = detail
                if normalize_handle(detail.get("handle")):
                    cache[video_id] = detail

    if any(normalize_handle(detail.get("handle")) for detail in details.values()):
        save_detail_cache(cache)

    # Kalodata may transiently reject bursts even when the Cookie remains valid.
    # Retry missing handles serially so a rate-limit blip does not become a data gap.
    missing_ids = [
        video_id
        for video_id in unique_items
        for detail in [details.get(video_id, {})]
        if not normalize_handle(detail.get("handle"))
    ]
    if missing_ids and _browser_transport is None:
        print(f"视频详情有 {len(missing_ids)} 条需冷却后补偿，等待 10 秒……")
        time.sleep(10)
        for video_id in missing_ids:
            time.sleep(0.75)
            retry_detail = fetch_video_detail(video_id, start_date, end_date, headers)
            if normalize_handle(retry_detail.get("handle")):
                details[video_id] = retry_detail
                cache[video_id] = retry_detail
                save_detail_cache(cache)

    results: list[dict[str, Any]] = []
    for video_id, item in unique_items.items():
        detail = details.get(video_id, {})
        handle = normalize_handle(detail.get("handle") or item.get("handle"))
        create_time = str(item.get("create_time") or detail.get("release_time") or detail.get("create_time") or "")
        results.append(
            {
                "id": video_id,
                "handle": handle,
                "sale": normalize_int(item.get("sale", detail.get("sale", 0))),
                "revenue": str(item.get("revenue") or detail.get("revenue") or ""),
                "create_time": create_time,
                "duration": str(item.get("duration") or detail.get("duration") or ""),
                "views": normalize_int(item.get("views", detail.get("views", 0))),
                "description": str(item.get("description") or detail.get("description") or "").strip(),
                "video_url": f"https://www.tiktok.com/@{handle}/video/{video_id}" if handle else "",
                "creator_url": f"https://www.tiktok.com/@{handle}" if handle else "",
            }
        )
    return sorted(results, key=lambda row: (-row["sale"], row["id"]))


def normalize_lives(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in items:
        handle = normalize_handle(item.get("handle"))
        rows.append(
            {
                "id": str(item.get("id") or ""),
                "handle": handle,
                "sale": normalize_int(item.get("sale", 0)),
                "revenue": str(item.get("revenue") or ""),
                "create_time": str(item.get("create_time") or ""),
                "finish_time": str(item.get("finish_time") or ""),
                "duration": str(item.get("duration") or ""),
                "views": normalize_int(item.get("views", 0)),
                "live_url": str(item.get("shortUrl") or ""),
                "creator_url": f"https://www.tiktok.com/@{handle}" if handle else "",
            }
        )
    return sorted(rows, key=lambda row: (-row["sale"], row["id"]))


def aggregate_handles(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        handle = normalize_handle(row.get("handle")) or "（未获取）"
        current = grouped.setdefault(handle, {"handle": handle, "sale": 0, "content_count": 0, "max_sale": 0})
        sale = normalize_int(row.get("sale", 0))
        current["sale"] += sale
        current["content_count"] += 1
        current["max_sale"] = max(current["max_sale"], sale)
    return sorted(grouped.values(), key=lambda row: (-row["sale"], row["handle"]))


def collect(pid: str, country: str, currency: str, days: int, limit: int, workers: int) -> dict[str, Any]:
    end_dt = datetime.now() - timedelta(days=2)
    start_dt = end_dt - timedelta(days=days - 1)
    start_date, end_date = start_dt.strftime("%Y-%m-%d"), end_dt.strftime("%Y-%m-%d")
    cookie = read_cookie()
    headers = build_headers(cookie, pid, country, currency)
    base_payload = build_payload(pid, start_date, end_date, 1, max(50, limit))

    product = fetch_product(pid, start_date, end_date, headers)
    live_count = fetch_count("live", headers, base_payload)
    video_count = fetch_count("video", headers, base_payload)
    lives = normalize_lives(fetch_list("live", headers, base_payload))[:limit]
    sales_video_items = fetch_list("video", headers, base_payload)[:limit]

    published_candidates: list[dict[str, Any]] = []
    max_pages = min(100, max(1, math.ceil(video_count / 50)))
    pages_scanned = 0
    for page_no in range(1, max_pages + 1):
        page_items = fetch_list("video", headers, build_payload(pid, start_date, end_date, page_no, 50))
        pages_scanned = page_no
        for item in page_items:
            published = parse_date(item.get("create_time") or item.get("createTime"))
            if start_date <= published <= end_date:
                published_candidates.append(item)
        if len(published_candidates) >= limit or not page_items:
            break

    all_video_items = sales_video_items + published_candidates[:limit]
    enriched_all = enrich_videos(all_video_items, start_date, end_date, headers, workers)
    by_id = {row["id"]: row for row in enriched_all}
    sales_videos = [by_id[str(item.get("id"))] for item in sales_video_items if str(item.get("id")) in by_id][:limit]
    published_videos = [by_id[str(item.get("id"))] for item in published_candidates if str(item.get("id")) in by_id]
    published_videos = sorted(published_videos, key=lambda row: (-row["sale"], row["id"]))[:limit]

    source_url = (
        f"{BASE_URL}/product/detail?id={pid}&language=zh-CN&currency={currency}"
        f"&region={country}&dateRange=%5B%22{start_date}%22%2C%22{end_date}%22%5D&cateValue=%5B%5D"
    )
    return {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "query": {
            "pid": pid,
            "country": country,
            "currency": currency,
            "days": days,
            "limit": limit,
            "start_date": start_date,
            "end_date": end_date,
            "data_delay_days": 2,
            "source_url": source_url,
        },
        "product": {
            "title": str(product.get("product_title") or ""),
            "shop": str(product.get("shop_name") or product.get("store_name") or ""),
            "price": str(product.get("unit_price") or ""),
        },
        "source_counts": {"live": live_count, "video": video_count, "published_pages_scanned": pages_scanned},
        "live_top50": lives,
        "video_sales_top50": sales_videos,
        "video_published_top50": published_videos,
        "live_handle_summary": aggregate_handles(lives),
        "video_sales_handle_summary": aggregate_handles(sales_videos),
        "video_published_handle_summary": aggregate_handles(published_videos),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="按 PID 抓取近7天直播/视频销量 Top50 达人。")
    parser.add_argument("pid")
    parser.add_argument("--country", default="BR")
    parser.add_argument("--currency", default="IDR")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--transport", choices=["browser", "direct"], default="browser")
    parser.add_argument("--output", required=True)
    return parser.parse_args()


def main() -> int:
    global _browser_transport
    args = parse_args()
    output = Path(args.output).expanduser().resolve()
    seed_detail_cache_from_output(output)
    pid = str(args.pid).strip()
    country = str(args.country).strip().upper()
    currency = str(args.currency).strip().upper()
    if args.transport == "browser":
        with BrowserTransport(pid, country, currency) as transport:
            _browser_transport = transport
            try:
                result = collect(
                    pid=pid,
                    country=country,
                    currency=currency,
                    days=max(1, args.days),
                    limit=max(1, min(args.limit, 100)),
                    workers=1,
                )
            finally:
                _browser_transport = None
    else:
        result = collect(
            pid=pid,
            country=country,
            currency=currency,
            days=max(1, args.days),
            limit=max(1, min(args.limit, 100)),
            workers=max(1, min(args.workers, 12)),
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"已保存：{output}")
    print(
        f"直播 {len(result['live_top50'])} 条；视频销量口径 {len(result['video_sales_top50'])} 条；"
        f"近7天发布口径 {len(result['video_published_top50'])} 条。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
