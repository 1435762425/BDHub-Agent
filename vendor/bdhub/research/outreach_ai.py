"""货盘版本驱动的 AI 分类、选品和达人匹配任务。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from contextlib import contextmanager
import fcntl
from functools import lru_cache
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading

from sqlalchemy import create_engine, select
from sqlalchemy.pool import NullPool

from bdhub import config
from bdhub.aiops.matching import fingerprint, json_value
from bdhub.aiops.source import BusinessSource
from bdhub.hub.product_categories import CANONICAL_CATEGORIES, category_from_alias
from bdhub.hub.schema import creator_profile_current
from .campaign_catalog import CatalogStore, catalog_view, cutoff_date
from .kalodata import atomic_json, now

POLICY_VERSION = "catalog-outreach-agent-v2"
PLAN_RE = re.compile(r"oa_[a-f0-9]{32}\Z")
TERMINAL = {"completed", "failed", "stopped", "stale", "interrupted"}
_launch_lock = threading.Lock()


@contextmanager
def launch_guard(repository):
    repository.root.mkdir(parents=True, exist_ok=True)
    with (repository.root / ".launch.lock").open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def product_facts(row: dict) -> dict:
    return {key: row.get(key) for key in ("pid", "product_name", "sales", "price_min", "price_max", "currency", "rating", "review_count")}


def assessment_key(row: dict, model: str) -> str:
    return fingerprint({"policy": POLICY_VERSION, "model": model, "facts": product_facts(row)})


@lru_cache(maxsize=16000)
def _read_json(path: str, modified: int) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


class OutreachStore:
    def __init__(self, root: Path | None = None):
        self.root = root or config.ROOT / "data/research/outreach-ai"

    def directory(self, run_id: str) -> Path:
        if not PLAN_RE.fullmatch(run_id):
            raise ValueError("AI 任务编号无效。")
        return self.root / "runs" / run_id

    def create(self, request: dict) -> None:
        directory = self.directory(request["run_id"])
        directory.mkdir(parents=True, exist_ok=False)
        atomic_json(directory / "request.json", request)
        self.save(request["run_id"], {"state": "queued", "phase": "queued", "error": "", "classified": 0, "product_total": 0, "first": {"products": [], "leads": []}, "second": {"products": [], "leads": []}})
        atomic_json(self.root / f"{request['market']}.json", {"run_id": request["run_id"]})

    def save(self, run_id: str, result: dict) -> None:
        atomic_json(self.directory(run_id) / "result.json", {**result, "updated_at": now()})

    def read(self, run_id: str) -> dict:
        directory = self.directory(run_id)
        request = json.loads((directory / "request.json").read_text())
        result = json.loads((directory / "result.json").read_text())
        return {**request, **result}

    def latest(self, market: str) -> dict | None:
        if market not in {"br", "mx", "it", "uk", "us", "jp", "de"}:
            raise ValueError("该市场尚未接入货盘 AI。")
        pointer = self.root / f"{market}.json"
        return self.read(json.loads(pointer.read_text())["run_id"]) if pointer.exists() else None

    def cache_get(self, row: dict, model: str) -> dict | None:
        path = self.root / "assessments" / f"{assessment_key(row, model)}.json"
        try:
            return _read_json(str(path), path.stat().st_mtime_ns)
        except (OSError, ValueError):
            return None

    def cache_put(self, row: dict, model: str, assessment: dict) -> None:
        value = {**assessment, "model": model, "policy_version": POLICY_VERSION, "input_hash": assessment_key(row, model), "generated_at": now()}
        atomic_json(self.root / "assessments" / f"{assessment_key(row, model)}.json", value)


def annotate_products(items: list[dict], *, store: OutreachStore | None = None, model: str | None = None) -> list[dict]:
    repository = store or OutreachStore()
    model = model or config.load().reply.model
    return [{**row, "ai": repository.cache_get(row, model)} for row in items]


def plan_is_alive(plan: dict) -> bool:
    if plan["state"] in TERMINAL:
        return False
    # 运行端自身写入 PID；不存在 PID 时保留短暂启动状态。
    pid = plan.get("pid")
    if not pid:
        return (datetime.now(timezone.utc) - datetime.fromisoformat(plan["created_at"])).total_seconds() < 20
    try:
        probe = subprocess.run(["ps", "-p", str(int(pid)), "-o", "command="], capture_output=True, text=True, timeout=3)
        return probe.returncode == 0 and "bdhub.research.outreach_ai_worker" in probe.stdout and plan["run_id"] in probe.stdout
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return False


def public_plan(plan: dict | None, snapshot_id: str | None, *, purpose: str | None = None) -> dict | None:
    if plan is None:
        return None
    result = {key: value for key, value in plan.items() if key not in {"pid", "runtime_dir", "creator_evidence", "request_key"}}
    current = plan["snapshot_id"] == snapshot_id and plan.get("cutoff_date") == cutoff_date().isoformat()
    if plan.get("filter_version"):
        from .catalog_rules import filters_for, version
        current = current and plan["filter_version"] == version(filters_for(plan["market"]))
    result["current_version"] = current
    if result["state"] not in TERMINAL and not plan_is_alive(plan):
        result.update(state="interrupted", error="AI 进程已结束，已有分析已保存，可重新开始继续处理。")
    if not current:
        result["stale_reason"] = "货盘或有效期条件已更新，请以新版本重新匹配。"
    if purpose:
        result.pop("second" if purpose == "first" else "first", None)
    for key in ("first", "second"):
        if key in result:
            leads = []
            for row in result[key].get("leads", []):
                clean = {k: v for k, v in row.items() if k != "oec_id"}
                if clean.get("readiness") == "needs_profile":
                    clean["match_reason"] = "基于历史画像，需更新：" + str(clean.get("match_reason") or "")
                leads.append(clean)
            products = [{**row, "recommendation_reason": re.sub(r"(?<![A-Za-z])first(?![A-Za-z])", "一发", re.sub(r"(?<![A-Za-z])second(?![A-Za-z])", "二发", str(row.get("recommendation_reason") or "")))} for row in result[key].get("products", [])]
            result[key] = {**result[key], "leads": leads, "products": products}
    return result


def launch_plan(market: str, request_key: str, *, product_limit: int = 10, creator_limit: int = 10, days: int = 14, runtime_dir: str = "", automatic: bool = False, store: OutreachStore | None = None, source_kind: str = "catalog") -> dict | None:
    if market not in {"br", "mx", "it", "uk", "us", "jp", "de"} or not re.fullmatch(r"[a-zA-Z0-9_.:-]{1,200}", request_key):
        raise ValueError("AI 任务参数无效。")
    if type(product_limit) is not int or product_limit not in {5, 10, 20} or creator_limit not in {3, 10} or days not in {7, 14, 30}:
        raise ValueError("请选择 5/10/20 款商品、每款 3/10 位达人和 7/14/30 天统计窗口。")
    cfg = config.load()
    if not cfg.reply.api_key or not cfg.assist.python_path.is_file():
        raise ValueError("现有 AI 模型或运行环境未就绪。")
    from .commerce_links import recruitment_snapshot
    source = recruitment_snapshot(market) if source_kind == "ready" else CatalogStore().snapshot(market)
    from .campaign_catalog import inventory_view
    from .catalog_rules import filters_for, version
    filter_rules = filters_for(market)
    view = inventory_view(source, filter_rules)
    if not source or view["stale"]:
        raise ValueError("请先更新货盘，再执行 AI 匹配。")
    repository = store or OutreachStore()
    from dashboard import RUNTIME_DIR
    request = {"market": market, "snapshot_id": source["snapshot_id"], "request_key": request_key, "product_limit": product_limit, "creator_limit": creator_limit, "days": days, "model": cfg.reply.model, "policy_version": POLICY_VERSION, "cutoff_date": cutoff_date().isoformat(), "runtime_dir": runtime_dir or str(RUNTIME_DIR)}
    request.update(source_kind=source_kind, filter_rules=filter_rules, filter_version=version(filter_rules))
    request["run_id"] = "oa_" + fingerprint(request)[:32]
    with _launch_lock, launch_guard(repository):
        pause = repository.root / f"{market}.paused"
        if automatic and pause.exists():
            return None
        previous = repository.latest(market)
        if previous and previous["state"] not in TERMINAL and plan_is_alive(previous):
            if previous["snapshot_id"] == source["snapshot_id"]:
                if any(previous.get(key) != request[key] for key in ("product_limit", "creator_limit", "days")):
                    raise ValueError("已有 AI 任务运行，请停止或等待后再调整数量。")
                return previous
            (repository.directory(previous["run_id"]) / "stop").touch()
        if (repository.directory(request["run_id"]) / "request.json").exists():
            return repository.read(request["run_id"])
        if not automatic:
            pause.unlink(missing_ok=True)
        request["created_at"] = now()
        repository.create(request)
        atomic_json(repository.directory(request["run_id"]) / "catalog.json", source)
        try:
            subprocess.Popen([str(config.ROOT / ".venv/bin/python"), "-m", "bdhub.research.outreach_ai_worker", "--run-id", request["run_id"]], cwd=config.ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        except Exception:
            repository.save(request["run_id"], {"state": "failed", "phase": "queued", "error": "AI 进程未能启动。", "first": {"products": [], "leads": []}, "second": {"products": [], "leads": []}})
            raise
    return repository.read(request["run_id"])


def after_catalog_update(market: str, snapshot_id: str, *, runtime_dir: str = "") -> None:
    # 货盘同步与 AI 更新独立；仅由用户点击 AI 更新入口启动。
    return None


def read_creator_library(market: str) -> tuple[list[dict], dict]:
    cfg = config.load()
    p = creator_profile_current
    query = BusinessSource._creators_query(market).add_columns(p.c.followers, p.c.gmv_value, p.c.video_gmv, p.c.live_gmv, p.c.ec_video_cnt_30d, p.c.live_cnt_30d, p.c.price_range)
    engine = create_engine(cfg.db_url, hide_parameters=True, poolclass=NullPool, connect_args={"connect_timeout": 5, "options": "-c default_transaction_read_only=on -c statement_timeout=15000"})
    try:
        with engine.connect() as connection:
            rows = [dict(row) for row in connection.execute(query.limit(100001)).mappings()]
    finally:
        engine.dispose()
    if len(rows) > 100000:
        raise ValueError("达人库超过本次处理范围，请先缩小范围。")
    current = datetime.now(timezone.utc)
    records = []
    missing = stale = 0
    for row in rows:
        category = category_from_alias(row.get("main_category"))
        sources = row.get("field_sources") or {}
        observed = (sources.get("main_category") or {}).get("observed_at")
        try:
            at = datetime.fromisoformat(str(observed).replace("Z", "+00:00"))
            fresh = at.tzinfo is not None and timedelta(0) <= current - at <= timedelta(days=cfg.refresh_days)
        except (TypeError, ValueError):
            fresh = False
        if not category or not row.get("handle"):
            missing += 1
            continue
        if not fresh:
            stale += 1
        records.append(json_value({"candidate_id": "c_" + sha256(f"{market}:{row['oec_id']}".encode()).hexdigest()[:20], "oec_id": row["oec_id"], "handle": row["handle"], "category": category, "category_fresh": fresh, "category_observed_at": observed, "captured_at": row["captured_at"], **{key: row.get(key) for key in ("followers", "gmv_value", "video_gmv", "live_gmv", "ec_video_cnt_30d", "live_cnt_30d", "price_range")}}))
    return records, {"total": len(rows), "category_missing": missing, "category_stale": stale, "freshness_days": cfg.refresh_days, "read_at": now()}


def creator_shortlist(product: dict, creators: list[dict], *, limit: int = 40) -> list[dict]:
    # 类目与字段时效是事实初筛，最终人选交给模型；不把此排序称为 AI 匹配。
    rows = [row for row in creators if row["category"] == product.get("category")]
    def numeric(value):
        try:
            return float(value or 0)
        except (TypeError, ValueError):
            return 0.0
    rows.sort(key=lambda row: (not row["category_fresh"], -numeric(row.get("ec_video_cnt_30d")) - numeric(row.get("live_cnt_30d")), -numeric(row.get("gmv_value")), row["candidate_id"]))
    return rows[:limit]


def creator_model_facts(row: dict) -> dict:
    keys = ("candidate_id", "category", "category_fresh", "category_observed_at", "captured_at", "followers", "gmv_value", "video_gmv", "live_gmv", "ec_video_cnt_30d", "live_cnt_30d", "price_range")
    return {key: row.get(key) for key in keys}


def model_call(job: dict, *, timeout: float = 180) -> dict:
    cfg = config.load()
    result = subprocess.run([str(cfg.assist.python_path), "-m", "bdhub.research.outreach_model"], input=json.dumps(job, ensure_ascii=False).encode(), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, cwd=config.ROOT, timeout=timeout)
    if result.returncode or len(result.stdout) > 2_000_000:
        raise RuntimeError("AI 请求未完成，已有分析缓存保留。")
    value = json.loads(result.stdout)
    if value.get("error"):
        raise RuntimeError("AI 模型暂不可用，已有分析缓存保留。")
    return value
