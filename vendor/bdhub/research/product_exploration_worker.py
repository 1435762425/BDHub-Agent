"""商品探索独立 HTTP 读取；复用原 Kalodata 登录文件，不复制凭据、不隐式回退浏览器。"""
from __future__ import annotations

import argparse
import fcntl
import importlib.util
import signal
import time

from bdhub import config
from .kalodata_worker import ResearchError, StopRequested, business_message, extract_items
from .product_exploration import ExplorationStore, project_product, enrich_product


class HttpClient:
    def __init__(self, query):
        self.query, self.last_request = query, 0.0

    def __enter__(self):
        from curl_cffi import requests
        source = config.load().kalodata.project_dir / "product_top50/collect.py"
        spec = importlib.util.spec_from_file_location("bdhub_kalodata_product_http", source)
        core = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(core)
        self.session = requests.Session(impersonate="chrome")
        self.headers = core.build_headers(core.read_cookie(), "", self.query["country"], self.query["currency"])
        self.headers["referer"] = f"https://www.kalodata.com/product?region={self.query['country']}&language=zh-CN&currency={self.query['currency']}"
        self.proxy = core.load_proxy_url()
        return self

    def __exit__(self, *_):
        self.session.close()

    def request(self, endpoint, payload):
        if endpoint not in {"/product/queryList", "/product/searchList", "/product/detail", "/product/detail/total", "/product/detail/creator/queryList", "/shop/detail"}:
            raise ValueError("只允许商品探索读取接口。")
        time.sleep(max(0, 1 - (time.monotonic() - self.last_request)))
        self.last_request = time.monotonic()
        response = self.session.post("https://www.kalodata.com" + endpoint, headers=self.headers, json=payload, timeout=25,
                                     proxies={"http": self.proxy, "https": self.proxy} if self.proxy else None)
        try:
            body = response.json()
        except ValueError:
            body = {}
        if response.status_code != 200 or not isinstance(body, dict) or body.get("success") is not True:
            message = business_message(body.get("message")) if isinstance(body, dict) else ""
            raise ResearchError(f"Kalodata 读取未成功（HTTP {response.status_code}）{('：' + message) if message else ''}。已保留结果，请检查登录或平台额度。")
        return body


def run(job_id, *, store=None, factory=HttpClient, lock_path=None):
    repository = store or ExplorationStore()
    job = repository.read(job_id)
    if job["state"] != "queued":
        return job
    with repository.path(job_id).with_suffix(".claimed").open("x"):
        pass
    path = lock_path or config.ROOT / "data/research/kalodata/.browser.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    def save():
        repository.save(job)
    def check_stop():
        if repository.path(job_id).with_suffix(".stop").exists():
            raise StopRequested()
    with path.open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            job.update(state="running", phase="ranking")
            save()
            query, seen = job["query"], set()
            with factory(query) as client:
                job["truncated"] = True
                exact = query.get("mode") == "pids"
                for page in range(1, 1 if exact else query["limit"] // 50 + 1):
                    check_stop()
                    payload = {"country": query["country"], "startDate": query["start_date"], "endDate": query["end_date"],
                               "cateIds": [], "pageNo": page, "pageSize": 50, "sort": [{"field": query["sort"], "type": "DESC"}], "authority": True}
                    if query["keyword"]:
                        payload["query"] = query["keyword"]
                    endpoint = "/product/searchList" if query["keyword"] else "/product/queryList"
                    items = extract_items(client.request(endpoint, payload))
                    added = 0
                    for item in items:
                        row = project_product(item, query, len(job["rows"]) + 1)
                        if row["pid"] not in seen:
                            seen.add(row["pid"])
                            job["rows"].append(row)
                            added += 1
                    job["pages_done"] = page
                    save()
                    if len(items) < 50:
                        job["truncated"] = False
                        break
                    if not added:
                        raise ResearchError("平台分页重复，已停止；当前结果不代表完整榜单。")
                job["phase"] = "details"
                save()
                targets = [{"pid": pid} for pid in query["targets"]] if exact else job["rows"][:query["detail_limit"]]
                for row in targets:
                    check_stop()
                    detail = client.request("/product/detail", {"id": row["pid"], "authority": True}).get("data")
                    if not isinstance(detail, dict):
                        raise ResearchError("Kalodata 商品详情不完整，已有榜单结果保留。")
                    if exact:
                        if detail.get("id") != row["pid"]:
                            raise ResearchError("商品详情 PID 不一致，未合并其他商品的信息。")
                        check_stop()
                        total = client.request("/product/detail/total", {"id": row["pid"], "startDate": query["start_date"], "endDate": query["end_date"], "authority": True}).get("data")
                        if not isinstance(total, dict) or (total.get("id") is not None and str(total["id"]) != row["pid"]):
                            raise ResearchError("商品统计返回不完整或 PID 不一致，已停止核对。")
                        window_fields = {key: total[key] for key in ("sale", "revenue", "creator_num", "unit_price") if key in total}
                        row = project_product({**detail, **window_fields}, query, len(job["rows"]) + 1)
                        job["rows"].append(row)
                    enrich_product(row, detail, query)
                    if exact:
                        row["evidence_source"] = "Kalodata 精确 PID 商品详情 + 窗口统计"
                    job["details_done"] += 1
                    save()
                if exact:
                    job["truncated"] = False
            job.update(state="completed", phase="completed")
        except StopRequested:
            job.update(state="stopped", error="探索已停止，已读取商品保留。")
        except BlockingIOError:
            job.update(state="failed", error="已有 Kalodata 查询正在运行，请结束后重新探索。")
        except Exception as exc:
            job.update(state="failed", error=str(exc) if isinstance(exc, ResearchError) else "Kalodata 查询或商品字段校验中断，请检查原项目登录和网络。已有结果已保留。")
        save()
    return job


def main():
    parser = argparse.ArgumentParser(description="第三方平台商品探索，只读")
    parser.add_argument("--job-id", required=True)
    args = parser.parse_args()
    store = ExplorationStore()
    signal.signal(signal.SIGTERM, lambda *_: store.path(args.job_id).with_suffix(".stop").touch())
    result = run(args.job_id, store=store)
    print(f"[product-exploration] state={result['state']} products={len(result['rows'])}")
    return 0 if result["state"] in {"completed", "stopped"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
