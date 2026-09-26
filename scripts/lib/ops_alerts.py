"""Read-only health alerts for the alert bar at the top of every market page.

``gather`` reads the same ledgers and status files the pages already use; ``evaluate`` turns those
facts into alerts.  Nothing here starts, stops or retries work.  While the scheduler is stopped on
purpose, the consequences of that pause (a stale inbox, identity maintenance past due) drop to info;
real errors keep their level so they are fixed before production resumes.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import sqlite3
from contextlib import closing
from pathlib import Path

LEVELS = ("critical", "warning", "info")
INBOX_STALE_SECONDS = 15 * 60
INBOX_ERROR_GRACE_SECONDS = 10 * 60
# live_guard_busy is retried within seconds; stopped is the orderly exit after a stop request.
NON_FAILURE_INBOX_CODES = frozenset({"live_guard_busy", "stopped"})
IDENTITY_GRACE_SECONDS = 2 * 3600
BACKLOG_WARNING_SECONDS = 26 * 3600  # older than one daily reply window
OFFSITE_STALE_SECONDS = 7 * 86400
BEIJING = timezone(timedelta(hours=8))
STAGE_LABELS = {"taplink_clean": "TapLink 清理", "catalog": "货盘", "taplink_prepare": "TapLink",
                "kalodata": "Kalodata", "oecid": "OECID", "send_pool": "发送池"}


def _read_json(path):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _stamp(value):
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0 else None


def _has(db, table):
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def _selection_isolated(root, market, now):
    """Selections isolated for a campaign mismatch in the last 7 days; 0 when the ledger is absent."""
    name = "global-selection.sqlite" if market == "it" else f"global-selection-{market}.sqlite"
    path = Path(root) / "var" / name
    if not path.exists():
        return 0
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
            if not _has(db, "intake_item"):
                return 0
            return db.execute("SELECT count(*) FROM intake_item WHERE state='isolated_unverified' "
                              "AND json_extract(payload,'$.isolation.at')>=?", (now - 7 * 86400,)).fetchone()[0]
    except sqlite3.Error:
        return 0


def _market_facts(root, store, market, accounts):
    from lib.agent_reply_v2 import rollout_stage
    from lib.conversation_workbench import unread_backlog
    from lib.operations_workflow import setting as market_setting
    from lib.template_library import agent_setting

    db = store.db
    row = db.execute("SELECT id FROM plan WHERE market=? AND institution='bjn-local-research'", (market,)).fetchone()
    if not row:
        raise ValueError("plan_missing")
    plan = row[0]
    current, agent = market_setting(store, market), agent_setting(store, plan)
    inbox = _read_json(root / "var" / ("cycle-inbox-status.json" if market == "it" else f"market-inbox-{market}.json"))
    runtime = _read_json(root / "var" / ("agent-reply-status.json" if market == "it" else f"agent-reply-status-{market}.json"))
    latest_run = db.execute("SELECT run_id FROM workflow_run WHERE market=? ORDER BY started_at DESC LIMIT 1",
                            (market,)).fetchone()
    stages = [{"stage": stage["stage"], "state": stage["state"], "errorCode": stage["error_code"],
               "at": _stamp(stage["finished_at"]) or _stamp(stage["started_at"])}
              for stage in db.execute("SELECT stage,state,error_code,started_at,finished_at FROM workflow_stage_run "
                                      "WHERE run_id=? ORDER BY position", (latest_run[0],))] if latest_run else []
    deliveries = {state: {"count": count, "oldestAt": _stamp(oldest)} for state, count, oldest in db.execute(
        "SELECT state,count(*),min(created) FROM cycle_delivery WHERE plan_id=? "
        "AND state IN ('unknown','quarantined_unknown') GROUP BY state", (plan,))} if _has(db, "cycle_delivery") else {}
    cases = db.execute("SELECT count(*),min(created) FROM service_case WHERE plan_id=? AND state='open'", (plan,)).fetchone() \
        if _has(db, "service_case") else (0, None)
    day = datetime.fromtimestamp(store.clock(), BEIJING).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    rejected = db.execute("SELECT count(*),min(s.at) FROM cycle_platform_signal s JOIN cycle_delivery d ON d.id=s.delivery_id "
                          "WHERE d.plan_id=? AND s.outcome='rejected' AND s.at>=?", (plan, day)).fetchone() \
        if _has(db, "cycle_platform_signal") and _has(db, "cycle_delivery") else (0, None)
    from lib.cycle_delivery import new_contact_hold
    rejection_hold = new_contact_hold(store, plan) if _has(db, "cycle_delivery") else None
    empty = {"count": 0, "oldestAt": None}
    return {
        "market": market,
        "active": bool(current["automaticOperationsEnabled"] or current["continuousSendEnabled"] or agent["enabled"]),
        "selectionIsolated": _selection_isolated(root, market, store.clock()),
        "inbox": {"checkedAt": _stamp(inbox.get("checkedAt")),
                  "lastSuccessAt": _stamp((inbox.get("status") or {}).get("lastCheckedAt")),
                  "errorCode": inbox.get("errorCode") or None, "failureStage": inbox.get("failureStage") or None,
                  "accountRecovery": inbox.get("accountRecovery") if isinstance(inbox.get("accountRecovery"), dict) else None,
                  "stopRequested": market == "it" and (root / "var/job-inbox.stop").exists()},
        "stages": stages,
        "unknown": deliveries.get("unknown", empty), "quarantined": deliveries.get("quarantined_unknown", empty),
        "humanCases": {"count": cases[0], "oldestAt": _stamp(cases[1])},
        "platformRejections": {"count": rejected[0], "oldestAt": _stamp(rejected[1]), "hold": rejection_hold},
        "unread": unread_backlog(store, market),
        "agent": {"enabled": bool(agent["enabled"]), "rolloutStage": rollout_stage(store, plan, market),
                  "runtimeState": runtime.get("state"), "replyWindow": [agent["replyStart"], agent["replyEnd"]]},
        "accounts": [{"account": item["account"], "nextMaintenanceAt": _stamp(item["nextMaintenanceAt"]),
                      "maintenanceState": (item["maintenance"] or {}).get("state")}
                     for item in accounts["accounts"] if item["market"] == market],
        "needsHuman": [{"account": item["account"], "errorCode": item["errorCode"],
                        "at": _stamp(item["finishedAt"]) or _stamp(item["createdAt"])}
                       for item in accounts["queue"] if item["market"] == market and item["state"] == "needs_human"],
    }


def _reason(error):
    return (str(error) or type(error).__name__)[:200]


def _model_service(store):
    from lib import model_service
    from lib.draft_provider import MODEL
    return model_service.paused(store.db, model_service.service_key("DeepSeek", MODEL), store.clock())


def gather(root, store):
    """Facts for ``evaluate``; a market whose ledgers cannot be read carries ``error`` instead."""
    from lib.account_identity import status as account_status
    from lib.market_registry import operational_market_keys
    from lib.offsite_backup import latest as offsite_latest
    from lib.operations_scheduler import scheduler_state, stop_path

    root = Path(root)
    scheduler = scheduler_state(root)
    stop = stop_path(root)
    requested = (_stamp(_read_json(stop).get("requestedAt")) or stop.stat().st_mtime) if stop.exists() else None
    from lib.runtime_release import head_sha, loaded
    facts = {"now": store.clock(), "offsite": offsite_latest(root), "markets": [],
             "release": {"head": head_sha(root), "loaded": loaded(root)},
             "modelService": _model_service(store),
             "scheduler": {"running": bool(scheduler["running"]), "stopRequestedAt": requested,
                           "checkedAt": _stamp(scheduler.get("checkedAt"))}}
    try:
        accounts = account_status(store, root)
    except Exception as error:  # noqa: BLE001 - one unreadable ledger must not hide the other alerts
        accounts, facts["accountError"] = {"accounts": [], "queue": []}, _reason(error)
    for market in operational_market_keys(root):
        try:
            entry = _market_facts(root, store, market, accounts)
        except Exception as error:  # noqa: BLE001
            entry = {"market": market, "error": _reason(error)}
        facts["markets"].append(entry)
    return facts


def _hours(seconds):
    return f"{max(0.0, seconds) / 3600:.1f}"


def _recovery_note(recovery):
    """What the inbox did about a lapsed login, for the inbox alert."""
    if not recovery:
        return ""
    state = recovery.get("state")
    if state in ("queued", "draining", "running"):
        return "；已自动发起账号刷新，等待结果"
    if state == "completed":
        return "；账号已自动刷新，等下一轮收信确认"
    if state == "not_requested":
        return "；账号维护进行中" if recovery.get("reason") == "account_maintenance_active" else "；未能自动发起账号刷新"
    return f"；自动刷新未成功（{recovery.get('errorCode') or state}），需要人工处理"


def evaluate(facts):
    now, alerts = facts["now"], []

    def add(alert_id, level, title, detail, *, market=None, since=None, href=None):
        alerts.append({"id": alert_id, "level": level, "market": market, "title": title, "detail": detail,
                       "since": since, "href": href})

    scheduler = facts["scheduler"]
    running = scheduler["running"]
    paused = not running and scheduler["stopRequestedAt"] is not None
    if paused:
        add("production-paused", "info", "生产已暂停",
            "调度器已按停止请求退出：收信、发送、AI 回复和账号维护都不会运行。恢复需在各市场运营首页打开开关并启动调度。",
            since=scheduler["stopRequestedAt"], href="/it/ops/jobs")
    elif not running and any(market.get("active") for market in facts["markets"]):
        add("scheduler-down", "critical", "调度器未运行",
            "没有停止请求，但调度器不在运行：收信、发送、AI 回复和账号维护都已中断。",
            since=scheduler["checkedAt"], href="/it/ops/jobs")
    service = facts.get("modelService")
    if service:
        add("model-service-paused", "warning", "AI 模型服务暂停",
            f"连续调用失败（{service.get('lastError') or '原因未记录'}），已暂停调用，下次尝试 {datetime.fromtimestamp(service['nextAt'], BEIJING).strftime('%H:%M')}；待答问题保留，暂停期间不消耗每位达人的尝试次数。")
    release = facts.get("release") or {}
    head, live = release.get("head"), release.get("loaded") or []
    # Only the commit is compared: pages legitimately rewrite tracked config files at runtime.
    stale = sorted(row["role"] for row in live if head and row.get("sha") != head)
    if stale:
        add("runtime-version-mixed", "warning", f"{len(stale)} 个常驻进程运行的不是当前代码",
            f"{'、'.join(stale)} 载入的版本与仓库当前提交 {head[:7]} 不同；按发布流程安全重启后新代码才生效。")
    offsite = facts["offsite"]
    if not offsite:
        add("offsite-missing", "warning", "还没有 U 盘备份", "插上 U 盘后执行一次异机备份。")
    elif now - offsite["createdAt"] > OFFSITE_STALE_SECONDS:
        add("offsite-stale", "warning", f"U 盘备份已 {int((now - offsite['createdAt']) // 86400)} 天未更新",
            "插上 U 盘后执行异机备份。", since=offsite["createdAt"])
    if facts.get("accountError"):
        add("accounts-read-failed", "warning", "账号状态读取失败", facts["accountError"], href="/it/ops/accounts")
    for market in facts["markets"]:
        key, name = market["market"], market["market"].upper()
        base = f"/{key}"
        if market.get("error"):
            add(f"{key}-read-failed", "warning", f"{name} 巡检读取失败", market["error"], market=key, href=base)
            continue
        inbox = market["inbox"]
        failing = inbox["errorCode"] and inbox["errorCode"] not in NON_FAILURE_INBOX_CODES and (
            inbox["lastSuccessAt"] is None or now - inbox["lastSuccessAt"] > INBOX_ERROR_GRACE_SECONDS)
        if failing:
            stage = f"（{inbox['failureStage']} 阶段）" if inbox["failureStage"] else ""
            stage += _recovery_note(inbox.get("accountRecovery"))
            # ``since`` is the last successful read, which the bar shows as the start of the failure.
            add(f"{key}-inbox-error", "critical" if running else "warning", f"{name} 收信失败",
                f"{inbox['errorCode']}{stage}", market=key, since=inbox["lastSuccessAt"],
                href=f"{base}/ops/accounts" if inbox["failureStage"] == "auth" else f"{base}/ops/jobs")
        elif running and inbox["stopRequested"]:
            add(f"{key}-inbox-stopped", "info", f"{name} 收信已人工停止", "调度在运行，但收信作业有停止请求。",
                market=key, href=f"{base}/ops/jobs")
        elif running and market["active"] and (inbox["checkedAt"] is None or now - inbox["checkedAt"] > INBOX_STALE_SECONDS):
            idle = f"{int((now - inbox['checkedAt']) // 60)} 分钟没有新的收信记录" if inbox["checkedAt"] else "还没有收信记录"
            add(f"{key}-inbox-stale", "critical", f"{name} 收信停滞", idle, market=key, since=inbox["checkedAt"],
                href=f"{base}/ops/jobs")
        for stage in market["stages"]:
            if stage["state"] not in ("failed", "needs_human"):
                continue
            label = STAGE_LABELS.get(stage["stage"], stage["stage"])
            code = stage["errorCode"] or stage["state"]
            add(f"{key}-stage-{stage['stage']}", "warning",
                f"{name} {label}：{'需要人工' if stage['state'] == 'needs_human' else '失败'}", code, market=key,
                since=stage["at"], href=f"{base}/ops/kalodata" if code.startswith("kalodata_auth") else base)
        if market["unknown"]["count"]:
            add(f"{key}-send-unknown", "warning", f"{name} {market['unknown']['count']} 条发送结果未知",
                "只按原账号和原意图核验，不会自动重发；结清前本市场二发和 AI 回复都暂停。", market=key, since=market["unknown"]["oldestAt"], href=base)
        if market["quarantined"]["count"]:
            add(f"{key}-send-quarantined", "info", f"{name} {market['quarantined']['count']} 条未知发送已隔离",
                "保留原意图，不会重发或新建技术人工事项；历史案件保留。", market=key, since=market["quarantined"]["oldestAt"], href=base)
        rejections = market.get("platformRejections") or {"count": 0}
        if rejections["count"]:
            hold = rejections.get("hold")
            detail = {"platform_quota": "平台明确提示新联系额度用尽，已暂停新联系到明天。",
                      "platform_rejection_repeated": "同一类非额度拒绝今天重复出现，已暂停新联系到明天；按平台回执核对原因。"
                      }.get(hold, "非额度拒绝只结束对应达人，继续发送；同类拒绝再重复会暂停新联系。")
            add(f"{key}-platform-rejected", "warning", f"{name} 平台今天拒绝发送 {rejections['count']} 次",
                detail, market=key, since=rejections["oldestAt"], href=f"{base}/workspace/send")
        isolated = market.get("selectionIsolated") or 0
        if isolated:
            add(f"{key}-selection-isolated", "warning", f"{name} {isolated} 个选品因活动不符已隔离",
                "证据已保留，本轮不建链接；核对实际活动后再决定是否恢复。", market=key, href=f"{base}/ops/jobs")
        if market["humanCases"]["count"]:
            add(f"{key}-human", "warning", f"{name} {market['humanCases']['count']} 条人工会话待处理",
                "在会话页处理后关闭。", market=key, since=market["humanCases"]["oldestAt"], href=f"{base}/conversations")
        agent, unread = market["agent"], market["unread"]
        if unread["unread"]:
            waited = now - unread["oldestAt"] if unread["oldestAt"] else 0
            reply = f"AI 回复窗口北京时间 {'–'.join(agent['replyWindow'])}" if agent["enabled"] else "AI 回复已关闭"
            add(f"{key}-unread", "warning" if waited > BACKLOG_WARNING_SECONDS else "info",
                f"{name} {unread['unread']} 位达人来信未回", f"最早一条已等 {_hours(waited)} 小时；{reply}。",
                market=key, since=unread["oldestAt"], href=f"{base}/conversations")
        if agent["enabled"] and agent["rolloutStage"] == "pilot_required":
            add(f"{key}-agent-first-send", "warning", f"{name} AI 回复待首次启动", "需要你在页面明确启动首发。",
                market=key, href=f"{base}/conversations/agent")
        elif agent["enabled"] and agent["rolloutStage"] == "pilot_complete":
            add(f"{key}-agent-pilot-complete", "info", f"{name} AI 首轮已完成", "核对首轮结果后在页面开启全量。",
                market=key, href=f"{base}/conversations/agent")
        if agent["runtimeState"] == "failed":
            add(f"{key}-agent-failed", "warning", f"{name} AI 回复运行失败", "查看 Agent 运行记录。", market=key,
                href=f"{base}/conversations/agent")
        for account in market["needsHuman"]:
            add(f"{key}-{account['account']}-needs-human", "warning", f"{name} {account['account'].upper()} 需要人工处理",
                account["errorCode"] or "needs_human", market=key, since=account["at"], href=f"{base}/ops/accounts")
        for account in market["accounts"]:
            due = account["nextMaintenanceAt"]
            if due is None or now - due <= IDENTITY_GRACE_SECONDS or account["maintenanceState"] in ("queued", "draining", "running"):
                continue
            add(f"{key}-{account['account']}-identity-overdue", "warning" if running else "info",
                f"{name} {account['account'].upper()} 身份维护逾期 {_hours(now - due)} 小时",
                "维护队列没有按时处理，检查账号页。" if running else "调度恢复后会按顺序重新维护。",
                market=key, since=due, href=f"{base}/ops/accounts")
    alerts.sort(key=lambda alert: (LEVELS.index(alert["level"]), alert["since"] or now, alert["id"]))
    return alerts
