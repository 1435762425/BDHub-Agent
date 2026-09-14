"""复用 kaladatagrab 浏览器，只读查询；逐目标落盘、遇登录/限流异常停整批。"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import importlib.util
from pathlib import Path
import re
import signal
import time

from bdhub import config
from .kalodata import HANDLE, PID, ResearchStore, atomic_json, evidence, now, number

BASE_URL = "https://www.kalodata.com"
PAGE_SIZE = 50
MAX_PAGES = 20


class ResearchError(RuntimeError):
    def __init__(self, message: str, *, details: dict | None = None):
        super().__init__(message)
        self.details = details or {}


class StopRequested(Exception):
    pass


def business_message(value: object) -> str:
    """只保留短业务提示，去掉链接和可能的认证字段。"""
    if not isinstance(value, str):
        return ""
    message = re.sub(r"https?://\S+", "[链接已省略]", value)
    message = re.sub(r"(?i)(session|cookie|authorization|token|password|secret|api_key)\s*[:=]\s*\S+", "[认证信息已省略]", message)
    message = re.sub(r"[A-Za-z0-9+/=_-]{32,}", "[标识已省略]", message)
    return re.sub(r"[\r\n\t<>]", " ", message).strip()[:200]


@contextmanager
def browser_client(project_dir: Path, query: dict):
    source = project_dir / "product_top50/collect.py"
    spec = importlib.util.spec_from_file_location("bdhub_kalodata_collect", source)
    if spec is None or spec.loader is None:
        raise ResearchError("未找到 kaladatagrab 查询程序。")
    core = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(core)
    seed = query["targets"][0] if query["mode"] == "pid" else ""
    browser = core.BrowserTransport(seed, query["market"].upper(), query["currency"])
    try:
        browser.__enter__()
        if query["mode"] == "creator":
            browser.page.goto(f"{BASE_URL}/creator?language=zh-CN&region={query['market'].upper()}&currency={query['currency']}", wait_until="domcontentloaded", timeout=60_000)
        yield BrowserClient(browser.page, query)
    finally:
        browser.__exit__(None, None, None)


@contextmanager
def research_client(project_dir: Path, query: dict):
    if query.get("transport") == "http":
        from .product_exploration_worker import HttpClient
        context = {**query, "country": "GB" if query["market"] == "uk" else query["market"].upper()}
        with HttpClient(context) as client:
            yield client
    else:
        with browser_client(project_dir, query) as client:
            yield client


class BrowserClient:
    def __init__(self, page, query: dict):
        self.page = page
        self.query = query
        self.last_request = 0.0

    def request(self, endpoint: str, payload: dict) -> dict:
        # 独立 Kalodata 账号顺序查询；不占用 TikTok 账号池。
        time.sleep(max(0, 1.0 - (time.monotonic() - self.last_request)))
        self.last_request = time.monotonic()
        response = self.page.evaluate("""async ({url, headers, payload}) => {
            const response = await fetch(url, {method: 'POST', headers,
                body: JSON.stringify(payload), credentials: 'include', cache: 'no-store',
                signal: AbortSignal.timeout(25000)});
            let body = null;
            try { body = await response.json(); } catch {}
            return {status: response.status, body};
        }""", {"url": BASE_URL + endpoint, "headers": {
            "content-type": "application/json", "country": self.query["market"].upper(),
            "currency": self.query["currency"], "language": "zh-CN",
        }, "payload": payload})
        status, body = response.get("status"), response.get("body")
        def safe_code(key):
            value = str(body.get(key) or "") if isinstance(body, dict) else ""
            return value if re.fullmatch(r"[A-Za-z0-9_.:-]{1,80}", value) else ""
        if status in {401, 403}:
            raise ResearchError("Kalodata 登录或访问权限失效，请在原项目登录器完成验证后新建探查。")
        if status in {429, 503}:
            raise ResearchError("Kalodata 限流或暂时不可用，已停止本批并保留结果。")
        if status != 200 or not isinstance(body, dict) or body.get("success") is not True:
            reason = business_message(body.get("message")) if isinstance(body, dict) else ""
            raise ResearchError(f"Kalodata 查询失败：{reason}。已停止本批。" if reason else "Kalodata 未返回明确成功的数据，已停止探查；不能视为空结果。", details={
                "http_status": status, "response_fields": sorted(body)[:20] if isinstance(body, dict) else [],
                "success": body.get("success") if isinstance(body, dict) and isinstance(body.get("success"), bool) else None,
                "numeric_code": body.get("code") if isinstance(body, dict) and type(body.get("code")) is int else None,
                "code": safe_code("code"), "category": safe_code("errorCategory"),
                "data_type": type(body.get("data")).__name__ if isinstance(body, dict) else None,
            })
        return body


def extract_items(body: dict, *, creator_search: bool = False) -> list[dict]:
    data = body.get("data")
    keys = ("creator", "creators", "items", "list") if creator_search else ("items", "list", "records", "rows")
    if isinstance(data, dict):
        data = next((data[key] for key in keys if isinstance(data.get(key), list)), None)
    if not isinstance(data, list) or any(not isinstance(item, dict) for item in data):
        raise ResearchError("Kalodata 返回的数据结构不完整，不能判定为无结果。")
    return data


def exact_creator(client, handle: str, market: str, check_stop) -> tuple[str, str] | None:
    requests = [
        ("/overview/fullText/search", {"country_code": market, "keyword": handle, "scope": [{"index": "creator", "pageNo": 1, "pageSize": 100}]}),
        ("/creator/searchCreatorByKeyword", {"type": "creator", "keyword": handle}),
    ]
    for endpoint, payload in requests:
        check_stop()
        candidates = extract_items(client.request(endpoint, payload), creator_search=True)
        matches = set()
        for item in candidates:
            candidate = str(item.get("creator_handle") or item.get("handle") or item.get("unique_id") or "").strip().lstrip("@").lower()
            identity = str(item.get("creator_uid") or item.get("id") or item.get("creator_id") or "").strip()
            if candidate == handle and identity:
                matches.add((identity, str(item.get("creator_nickname") or item.get("nickname") or "")))
        if len({item[0] for item in matches}) > 1:
            raise ResearchError("同一 Handle 返回多个 Kalodata 身份，请人工核对。")
        if matches:
            return sorted(matches)[0]
    return None


def query_target(client, query: dict, target: str, check_stop) -> tuple[dict, list[dict]]:
    common = {"startDate": query["start_date"], "endDate": query["end_date"], "authority": True, "pageSize": PAGE_SIZE, "sort": [{"field": "revenue", "type": "DESC"}]}
    if query["mode"] == "pid":
        check_stop()
        items = extract_items(client.request("/product/detail/creator/queryList", {**common, "id": target, "pageNo": 1}))
        rows, seen = [], set()
        for rank, item in enumerate(items, start=1):
            handle = str(item.get("handle") or "").strip().lstrip("@").lower()
            if not HANDLE.fullmatch(handle) or handle in seen or (number(item.get("sale")) or 0) <= 0:
                continue
            seen.add(handle)
            row = evidence(target, handle, item)
            row["product_title"] = str(query.get("product_context", {}).get(target, {}).get("title") or row["product_title"])
            row["rank"] = rank
            rows.append(row)
        rows = rows[:query["limit_per_pid"]]
        return {"target": target, "status": "success", "row_count": len(rows), "truncated": len(items) >= PAGE_SIZE, "note": "按 Kalodata GMV 排序的前 50 条中选取出单达人；不是全部达人。"}, rows

    identity = exact_creator(client, target, query["market"], check_stop)
    if not identity:
        return {"target": target, "status": "not_found", "row_count": 0, "truncated": False, "note": "未找到精确 Handle，未使用近似账号。"}, []
    creator_id, nickname = identity
    rows, seen = [], set()
    truncated = True
    for page in range(1, MAX_PAGES + 1):
        check_stop()
        items = extract_items(client.request("/creator/detail/searchProducts", {**common, "id": creator_id, "pageNo": page, "cateIds": [], "sellerId": None}))
        for item in items:
            pid = str(item.get("id") or item.get("product_id") or "").strip()
            if not PID.fullmatch(pid):
                raise ResearchError("商品结果缺少完整 PID，已停止以避免错误匹配。")
            if pid in seen:
                continue
            seen.add(pid)
            row = evidence(pid, target, item, creator_id=creator_id, nickname=nickname, reference_pids=query["reference_pids"])
            row["rank"] = len(rows) + 1
            rows.append(row)
        if len(items) < PAGE_SIZE:
            truncated = False
            break
    refs = set(query["reference_pids"])
    return {"target": target, "status": "success", "row_count": len(rows), "truncated": truncated,
            "matched_count": len(refs & seen), "unobserved_pids": sorted(refs - seen),
            "note": "已达每达人 1000 条上限；未匹配 PID 可能在后续页。" if truncated else "已读完所选统计窗口返回的商品；未出现不代表历史上从未带货。"}, rows


def run(store: ResearchStore, run_id: str, *, client_factory=research_client, project_dir: Path) -> dict:
    request = store.read(run_id)
    query = request["query"]
    directory = store.directory(run_id)
    result = {"state": "running", "started_at": now(), "updated_at": now(), "items": [], "rows": [], "error": ""}

    def save():
        result["updated_at"] = now()
        store.save_result(run_id, result)

    def check_stop():
        if (directory / "stop").exists():
            raise StopRequested()

    # 已开始过的快照不能被 CLI 或通用启动入口重复消费。
    with (directory / "claimed").open("x"):
        pass
    store.root.mkdir(parents=True, exist_ok=True)
    with (store.root / ".browser.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            result.update(state="failed", error="已有 Kalodata 探查占用浏览器，请等待完成。")
            save()
            return result
        save()
        target = ""
        try:
            check_stop()
            with client_factory(project_dir, query) as client:
                for target in query["targets"]:
                    check_stop()
                    result["current_target"] = target
                    save()
                    item, rows = query_target(client, query, target, check_stop)
                    result["items"].append(item)
                    result["rows"].extend(rows)
                    save()
            result["state"] = "completed"
        except StopRequested:
            result["state"] = "stopped"
        except Exception as exc:
            # 浏览器异常可能包含页面片段、URL 或凭据，禁止保存原始异常。
            message = str(exc) if isinstance(exc, ResearchError) else f"查询中断（{type(exc).__name__}），请检查 Kalodata 登录器和网络后新建探查。"
            result.update(state="failed", error=message)
            if isinstance(exc, ResearchError) and exc.details:
                result["error_details"] = exc.details
            if target:
                result["items"].append({"target": target, "status": "failed", "row_count": 0, "truncated": True, "note": message})
        result["finished_at"] = now()
        result.pop("current_target", None)
        save()
        return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Kalodata 二发线索只读探查")
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    store = ResearchStore(config.ROOT / "data/research/kalodata")
    directory = store.directory(args.run_id)
    signal.signal(signal.SIGTERM, lambda *_: (directory / "stop").touch())
    signal.signal(signal.SIGINT, lambda *_: (directory / "stop").touch())
    result = run(store, args.run_id, project_dir=config.load().kalodata.project_dir)
    print(f"[kalodata] state={result['state']} done={len(result['items'])} rows={len(result['rows'])}", flush=True)
    return 0 if result["state"] in {"completed", "stopped"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
