"""Kalodata 二发研究的输入、结果和本地快照契约。"""
from __future__ import annotations

import csv
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from io import StringIO
import json
from pathlib import Path
import re
from typing import Any
from uuid import uuid4

MARKETS = {"mx": "MXN", "br": "BRL", "it": "EUR", "uk": "GBP", "us": "USD", "de": "EUR", "jp": "JPY"}
RUN_ID = re.compile(r"kd_[a-f0-9]{32}\Z")
PID = re.compile(r"[0-9]{19}\Z")
HANDLE = re.compile(r"[a-z0-9_.]{1,24}\Z")
TERMINAL = {"completed", "stopped", "failed", "interrupted"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def tokens(value: Any, *, kind: str, limit: int, optional: bool = False) -> list[str]:
    if not isinstance(value, str) or len(value) > 25_000:
        raise ValueError("请输入文本名单，每行一个值。")
    values = list(dict.fromkeys(
        part.strip().lstrip("@").lower() if kind == "handle" else part.strip()
        for part in re.split(r"[\s,，;；]+", value.strip()) if part.strip()
    ))
    pattern = HANDLE if kind == "handle" else PID
    if not values and not optional:
        raise ValueError("请先填写探查名单。")
    if len(values) > limit:
        raise ValueError(f"每批最多 {limit} 个不同的{ ' Handle' if kind == 'handle' else ' PID'}。")
    if any(not pattern.fullmatch(item) for item in values):
        raise ValueError("Handle 只接受字母、数字、下划线和点，最长 24 位。" if kind == "handle" else "PID 必须为完整的 19 位数字，请勿使用科学计数法。")
    return values


def normalize_request(body: dict, *, today: date | None = None) -> dict:
    market = str(body.get("market") or "").lower()
    if market not in MARKETS:
        raise ValueError("不支持该研究市场。")
    mode = body.get("mode")
    if mode not in {"pid", "creator"}:
        raise ValueError("请选择按 PID 找达人或按达人查商品。")
    transport = body.get("transport", "browser")
    if transport not in {"browser", "http"} or (transport == "http" and mode != "pid"):
        raise ValueError("HTTP 探查目前只支持按 PID 找达人。")
    days = body.get("days", 7)
    limit = body.get("limit_per_pid", 10)
    if type(days) is not int or days not in {7, 14, 30}:
        raise ValueError("统计窗口只支持 7 天、14 天或 30 天。")
    if type(limit) is not int or limit not in {3, 10, 50}:
        raise ValueError("每 PID 线索数只支持 3、10、50。")
    end = (today or date.today()) - timedelta(days=2)
    return {
        "market": market, "currency": MARKETS[market], "mode": mode, "transport": transport,
        "targets": tokens(body.get("targets", ""), kind="pid" if mode == "pid" else "handle", limit=100),
        "reference_pids": tokens(body.get("reference_pids", ""), kind="pid", limit=1000, optional=True) if mode == "creator" else [],
        "days": days, "start_date": (end - timedelta(days=days - 1)).isoformat(),
        "end_date": end.isoformat(), "limit_per_pid": limit,
    }


def number(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    raw = str(value).strip().replace(",", "")
    match = re.fullmatch(r"(?:R\$|M\$|MX\$|US\$|Rp|RM|[$€£¥￥₫฿]|MXN|BRL|USD|EUR|GBP|JPY|IDR|VND|MYR|THB)?\s*([+-]?\d+(?:\.\d+)?)\s*([kKmMwW万亿千]?)", raw)
    if not match:
        return None
    try:
        return Decimal(match[1]) * {"": 1, "k": 1000, "K": 1000, "m": 1000000, "M": 1000000, "w": 10000, "W": 10000, "万": 10000, "亿": 100000000, "千": 1000}[match[2]]
    except InvalidOperation:
        return None


def evidence(pid: str, handle: str, item: dict, *, creator_id: str = "", nickname: str = "", reference_pids: list[str] | None = None) -> dict:
    live, video = number(item.get("live_revenue")), number(item.get("video_revenue"))
    if live is None or video is None:
        channel = "unknown"
    elif live > 0 and video > 0:
        channel = "both"
    elif live > 0:
        channel = "live"
    elif video > 0:
        channel = "video"
    else:
        channel = "unknown"
    sale = number(item.get("sale"))
    return {
        "pid": pid, "handle": handle, "nickname": nickname or str(item.get("nickname") or ""),
        "kalodata_creator_id": creator_id or str(item.get("id") or ""),
        "product_title": str(item.get("product_title") or item.get("title") or ""),
        "sale": int(sale) if sale is not None else None,
        "revenue": str(item.get("revenue") if item.get("revenue") is not None else ""),
        "live_revenue": str(item.get("live_revenue") if item.get("live_revenue") is not None else ""),
        "video_revenue": str(item.get("video_revenue") if item.get("video_revenue") is not None else ""),
        "channel": channel,
        "matched": pid in reference_pids if reference_pids else None,
    }


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False), encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def summarize(run: dict) -> dict:
    if "rows" not in run:
        return {key: value for key, value in run.items() if key != "job_id"}
    return {key: value for key, value in run.items() if key not in {"rows", "items", "job_id"}} | {
        "done": len(run["items"]), "total": len(run["query"]["targets"]),
        "row_count": len(run["rows"]), "creator_count": len({row["handle"] for row in run["rows"]}),
    }


class ResearchStore:
    def __init__(self, root: Path):
        self.root = root

    def directory(self, run_id: str) -> Path:
        if not RUN_ID.fullmatch(run_id):
            raise ValueError("探查记录编号无效。")
        return self.root / run_id

    def create(self, run_id: str, query: dict) -> dict:
        directory = self.directory(run_id)
        directory.mkdir(parents=True, exist_ok=False)
        request = {"run_id": run_id, "source": "Kalodata", "created_at": now(), "query": query}
        atomic_json(directory / "request.json", request)
        return request

    def save_result(self, run_id: str, result: dict) -> None:
        directory = self.directory(run_id)
        request = json.loads((directory / "request.json").read_text(encoding="utf-8"))
        atomic_json(directory / "result.json", result)
        atomic_json(directory / "summary.json", summarize({**request, **result}))

    def read(self, run_id: str, *, summary: bool = False) -> dict:
        directory = self.directory(run_id)
        request = json.loads((directory / "request.json").read_text(encoding="utf-8"))
        summary_path = directory / "summary.json"
        path = summary_path if summary and summary_path.exists() else directory / "result.json"
        result = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"state": "queued", "items": [], "rows": [], "error": ""}
        process = directory / "process.json"
        if process.exists():
            result["job_id"] = json.loads(process.read_text(encoding="utf-8"))["job_id"]
        if (directory / "stop").exists() and result["state"] not in TERMINAL:
            result["state"] = "stopping"
        return {**request, **result}

    def list(self, market: str) -> list[dict]:
        rows = []
        if self.root.exists():
            for path in self.root.glob("kd_*/request.json"):
                try:
                    item = self.read(path.parent.name, summary=True)
                except (ValueError, OSError):
                    continue
                if item["query"]["market"] == market:
                    rows.append(item)
        return sorted(rows, key=lambda item: item["created_at"], reverse=True)[:50]


def filtered_rows(run: dict, search: str = "", channel: str = "", matched_only: bool = False) -> list[dict]:
    keyword = search.strip().lower()
    return [row for row in run["rows"] if
            (not keyword or keyword in f"{row['pid']} {row['handle']} {row['nickname']} {row['product_title']}".lower())
            and (not channel or row["channel"] == channel)
            and (not matched_only or row["matched"] is True)]


def observed_currency(revenue: str) -> str:
    """请求币种可能被账号偏好覆盖；导出只能依据原始返回值。"""
    raw = revenue.strip()
    for prefix, currency in (("R$", "BRL"), ("M$", "MXN"), ("MX$", "MXN"), ("US$", "USD"), ("€", "EUR"), ("£", "GBP"), ("Rp", "IDR"), ("₫", "VND"), ("RM", "MYR"), ("฿", "THB")):
        if raw.startswith(prefix):
            return currency
    if raw.startswith(("¥", "￥")):
        return "¥（人民币/日元，未核验）"
    return "未核验（保留原始金额）"


def export_csv(run: dict, rows: list[dict]) -> str:
    out = StringIO(newline="")
    writer = csv.writer(out)
    writer.writerow(["handle", "pid", "达人名称", "商品名称", "Kalodata销量", "KalodataGMV", "币种", "渠道", "指定PID匹配", "统计开始", "统计结束", "来源", "探查编号"])
    for row in rows:
        values = [row["handle"], row["pid"], row["nickname"], row["product_title"], row["sale"], row["revenue"], observed_currency(row["revenue"]), {"live": "直播", "video": "短视频", "both": "直播及短视频", "unknown": "未识别"}[row["channel"]], {True: "匹配", False: "未匹配", None: "未指定"}[row["matched"]], run["query"]["start_date"], run["query"]["end_date"], "Kalodata研究线索", run["run_id"]]
        writer.writerow(["'" + value if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")) else value for value in values])
    return "\ufeff" + out.getvalue()
