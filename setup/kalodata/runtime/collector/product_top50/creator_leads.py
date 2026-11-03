#!/usr/bin/env python3
"""Low-frequency creator-lead pilot across selected Kalodata PIDs."""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timedelta
import json
from pathlib import Path
import time
from typing import Any

import collect as core


DEFAULT_SELECTION = Path("/Users/bjn00003/kaladatagrab/.work/br_pid_selection/analysis.json")
BASE_URL = "https://www.kalodata.com"


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def channel_for(item: dict[str, Any]) -> str:
    live = core.parse_compact_number(item.get("live_revenue"))
    video = core.parse_compact_number(item.get("video_revenue"))
    if live > video * 1.2:
        return "直播"
    if video > live * 1.2:
        return "视频"
    return "混合"


def build_handle_summary(leads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "handle": "",
            "nickname": "",
            "pid_count": 0,
            "total_sale": 0,
            "max_pid_sale": 0,
            "followers": 0,
            "pids": [],
            "priority_hits": 0,
            "live_hits": 0,
            "video_hits": 0,
            "mixed_hits": 0,
        }
    )
    for lead in leads:
        handle = lead["handle"]
        row = grouped[handle]
        row["handle"] = handle
        row["nickname"] = row["nickname"] or lead.get("nickname", "")
        row["pid_count"] += 1
        row["total_sale"] += lead["sale"]
        row["max_pid_sale"] = max(row["max_pid_sale"], lead["sale"])
        row["followers"] = max(row["followers"], lead.get("followers", 0))
        row["pids"].append(lead["pid"])
        row["priority_hits"] += int(lead["lead_tier"] == "优先线索")
        row[{"直播": "live_hits", "视频": "video_hits", "混合": "mixed_hits"}[lead["primary_channel"]]] += 1
    results = list(grouped.values())
    for row in results:
        row["pids"] = list(dict.fromkeys(row["pids"]))
        row["creator_url"] = f"https://www.tiktok.com/@{row['handle']}"
    return sorted(results, key=lambda row: (-row["pid_count"], -row["total_sale"], row["handle"]))


def run(selection_path: Path, output_path: Path, pid_limit: int, lead_limit: int, delay_seconds: float) -> dict[str, Any]:
    selection = json.loads(selection_path.read_text(encoding="utf-8"))
    selected = selection.get("core", [])[:pid_limit]
    if not selected:
        raise RuntimeError("没有可试跑的核心精选 PID。")

    end_dt = datetime.now() - timedelta(days=2)
    start_dt = end_dt - timedelta(days=6)
    start_date, end_date = start_dt.strftime("%Y-%m-%d"), end_dt.strftime("%Y-%m-%d")
    cookie = core.read_cookie()
    leads: list[dict[str, Any]] = []
    runs: list[dict[str, Any]] = []
    payload: dict[str, Any] = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "query": {
            "country": "BR",
            "currency": "IDR",
            "start_date": start_date,
            "end_date": end_date,
            "pid_limit": pid_limit,
            "lead_limit_per_pid": lead_limit,
            "delay_seconds": delay_seconds,
        },
        "pid_runs": runs,
        "leads": leads,
        "handle_summary": [],
    }

    first_pid = str(selected[0]["PID_text"])
    with core.BrowserTransport(first_pid, "BR", "IDR") as transport:
        core._browser_transport = transport
        try:
            for index, product in enumerate(selected, start=1):
                pid = str(product["PID_text"])
                headers = core.build_headers(cookie, pid, "BR", "IDR")
                request_payload = core.build_payload(pid, start_date, end_date, 1, max(20, lead_limit))
                try:
                    items = core.request_json(
                        f"{BASE_URL}/product/detail/creator/queryList",
                        headers,
                        request_payload,
                        retries=0,
                    ).get("data", [])
                except Exception as exc:
                    runs.append({"pid": pid, "status": "failed", "error": type(exc).__name__})
                    write_json_atomic(output_path, payload)
                    break
                valid_items = [
                    item
                    for item in items
                    if isinstance(item, dict)
                    and core.normalize_handle(item.get("handle"))
                    and core.normalize_int(item.get("sale", 0)) > 0
                ]
                for rank, item in enumerate(valid_items[:lead_limit], start=1):
                    handle = core.normalize_handle(item.get("handle"))
                    leads.append(
                        {
                            "pid": pid,
                            "pid_value_score": product.get("value_score"),
                            "product_title": product.get("商品名称", ""),
                            "creator_rank": rank,
                            "lead_tier": "优先线索" if rank <= 10 else "测试线索",
                            "handle": handle,
                            "nickname": str(item.get("nickname") or ""),
                            "sale": core.normalize_int(item.get("sale", 0)),
                            "revenue": str(item.get("revenue") or ""),
                            "live_revenue": str(item.get("live_revenue") or ""),
                            "video_revenue": str(item.get("video_revenue") or ""),
                            "primary_channel": channel_for(item),
                            "followers": core.normalize_int(item.get("followers", 0)),
                            "creator_id": str(item.get("id") or ""),
                            "creator_url": f"https://www.tiktok.com/@{handle}",
                            "product_url": f"https://www.kalodata.com/product/detail?id={pid}&language=zh-CN&region=BR",
                        }
                    )
                runs.append({"pid": pid, "status": "success", "lead_count": min(len(valid_items), lead_limit)})
                payload["handle_summary"] = build_handle_summary(leads)
                write_json_atomic(output_path, payload)
                print(f"[{index}/{len(selected)}] PID {pid}：{min(len(valid_items), lead_limit)} 条达人线索")
                if index < len(selected):
                    time.sleep(delay_seconds)
        finally:
            core._browser_transport = None

    payload["handle_summary"] = build_handle_summary(leads)
    payload["summary"] = {
        "requested_pids": len(selected),
        "successful_pids": sum(run["status"] == "success" for run in runs),
        "failed_pids": sum(run["status"] == "failed" for run in runs),
        "lead_rows": len(leads),
        "unique_handles": len(payload["handle_summary"]),
        "multi_pid_handles": sum(row["pid_count"] >= 2 for row in payload["handle_summary"]),
        "priority_lead_rows": sum(lead["lead_tier"] == "优先线索" for lead in leads),
    }
    write_json_atomic(output_path, payload)
    return payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="对核心精选 PID 低频试跑达人线索。")
    parser.add_argument("--selection", type=Path, default=DEFAULT_SELECTION)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--pid-limit", type=int, default=10)
    parser.add_argument("--lead-limit", type=int, default=20)
    parser.add_argument("--delay", type=float, default=2.0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = run(
        selection_path=args.selection.expanduser().resolve(),
        output_path=args.output.expanduser().resolve(),
        pid_limit=max(1, min(args.pid_limit, 50)),
        lead_limit=max(1, min(args.lead_limit, 50)),
        delay_seconds=max(1.0, args.delay),
    )
    print(json.dumps(result["summary"], ensure_ascii=False))
    return 0 if result["summary"]["failed_pids"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
