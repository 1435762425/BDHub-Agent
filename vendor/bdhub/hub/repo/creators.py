# -*- coding: utf-8 -*-
"""画像库 PostgreSQL 存储：handle_key 主键 upsert，含抓取时间，支持新鲜度判断 + xlsx 导出。

后端 = PostgreSQL（SQLAlchemy Core）。表/索引定义见 bdhub/hub/schema.py（schema 单一真相源）。
并发：依赖 engine 连接池（线程安全），不再手写连接锁/PRAGMA WAL。
时间：captured_at/missed_at 存 TIMESTAMPTZ；新鲜度统一用 tz-aware UTC 比较。
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select, func, delete, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from ..engine import check_schema, get_engine
from ..keys import norm_handle
from ..outcome import ExactMissEvidence
from ..profile_observation import CaptureContext, CaptureKind, SnapshotQuality
from ..schema import creator_profile, creator_profile_current, miss_log
from .identity_reader import CreatorIdentityReader
from .profile_snapshots import ProfileSnapshotStore
from .profile_readiness import complete_profile_oecs


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_dt(v):
    """把 captured_at/missed_at 读回值规整成 tz-aware datetime；None/非法 → None。"""
    if v is None:
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(v))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


class ProfileStore:
    def __init__(self, engine=None, profile_tbl=None, miss_tbl=None,
                 profile_name="creator_profile", miss_name="miss_log", market: str = "mx"):
        # engine 默认取进程内共享池；传入则用指定 engine（测试/迁移）
        self.engine = engine or get_engine()
        # 表参数保留给测试/扩展；生产市场统一使用带 bd_market 的主表。
        self.ptbl = profile_tbl if profile_tbl is not None else creator_profile
        self.mtbl = miss_tbl if miss_tbl is not None else miss_log
        self._pname = profile_name
        self._mname = miss_name
        self.market = market
        self._has_bdmkt = "bd_market" in self.ptbl.c
        check_schema(self.engine)

    # ---------------- 新鲜度 / 跳过 ----------------
    def is_fresh(self, handle_key: str, refresh_days: int) -> bool:
        return norm_handle(handle_key) in self.fresh_keys_for_handles(
            [handle_key],
            refresh_days,
        )

    def skip_status(self, handle_key: str, refresh_days: int, miss_skip_days: int) -> bool:
        """一次往返点查是否跳过：已抓且新鲜 或 近期死号。合并两表查询，减少 5万级重跑的往返。"""
        return (
            self.is_fresh(handle_key, refresh_days)
            or self.is_missed(handle_key, miss_skip_days)
        )

    def fresh_keys(self, refresh_days: int) -> set:
        """一次查出【所有仍新鲜(captured_at 在 refresh_days 内)】的 handle_key 集合。
        供大名单批量预过滤:2 条集合查询替代 N 次 skip_status 逐个往返(6.5万级快 100 倍)。"""
        cutoff = _now() - timedelta(days=refresh_days)
        q = select(creator_profile_current.c.handle_key).where(
            creator_profile_current.c.captured_at > cutoff,
            creator_profile_current.c.bd_market == self.market,
        )
        with self.engine.connect() as con:
            rows = con.execute(q)
            return {r[0] for r in rows}

    def fresh_keys_for_handles(self, handle_keys, refresh_days: int) -> set:
        """按输入 handle（含唯一历史 alias）批量判断 OEC current 是否仍新鲜。"""
        resolutions = CreatorIdentityReader(self.engine).resolve_handles(
            self.market,
            handle_keys,
        )
        oec_ids = list(dict.fromkeys(
            item.creator.oec_id
            for item in resolutions
            if item.creator is not None
        ))
        if not oec_ids:
            return set()
        cutoff = _now() - timedelta(days=refresh_days)
        fresh_oecs: set[str] = set()
        with self.engine.connect() as con:
            for start in range(0, len(oec_ids), 5000):
                rows = con.execute(
                    select(creator_profile_current.c.oec_id).where(
                        creator_profile_current.c.bd_market == self.market,
                        creator_profile_current.c.oec_id.in_(oec_ids[start:start + 5000]),
                        creator_profile_current.c.captured_at > cutoff,
                    )
                )
                fresh_oecs.update(str(row[0]) for row in rows)
        return {
            item.requested_handle_key
            for item in resolutions
            if item.creator is not None and item.creator.oec_id in fresh_oecs
        }

    def negcached_keys(self, miss_skip_days: int) -> set:
        """一次查出【近 miss_skip_days 内标记的死号】handle_key 集合。miss_skip_days<=0 → 空集(不跳死号)。"""
        if miss_skip_days <= 0:
            return set()
        cutoff = _now() - timedelta(days=miss_skip_days)
        q = select(self.mtbl.c.handle_key).where(self.mtbl.c.missed_at > cutoff)
        if self._has_bdmkt:
            q = q.where(self.mtbl.c.bd_market == self.market)
        with self.engine.connect() as con:
            rows = con.execute(q)
            return {r[0] for r in rows}

    def complete_fresh_keys_for_handles(self, handle_keys, refresh_days: int) -> set:
        resolutions = CreatorIdentityReader(self.engine).resolve_handles(self.market, handle_keys)
        ready = complete_profile_oecs(self.engine, self.market,
            [r.creator.oec_id for r in resolutions if r.creator is not None],
            cutoff=_now() - timedelta(days=refresh_days))
        return {r.requested_handle_key for r in resolutions if r.creator is not None and r.creator.oec_id in ready}

    def resolved_current_identities(self, handle_keys) -> dict:
        return {r.requested_handle_key: {"oec_id": r.creator.oec_id, "audit_handle": r.creator.handle}
                for r in CreatorIdentityReader(self.engine).resolve_handles(self.market, handle_keys)
                if r.creator is not None}

    # ---------------- 存在性(上传名单去重用) ----------------
    def _subset_present(self, tbl, handle_keys) -> set:
        """给定 handle_key 列表 → 其中【已存在于 tbl】的子集(任意时间,不看新鲜度/TTL)。
        分块 IN 查询(每块 5000；PG 参数上限约 6.5 万，5000 是保守值，给大名单上传留裕度)，按统一 bd_market 过滤。"""
        keys = [k for k in dict.fromkeys(handle_keys) if k]          # 去重去空
        if not keys:
            return set()
        found: set = set()
        with self.engine.connect() as con:
            for i in range(0, len(keys), 5000):
                q = select(tbl.c.handle_key).where(tbl.c.handle_key.in_(keys[i:i + 5000]))
                if self._has_bdmkt:
                    q = q.where(tbl.c.bd_market == self.market)
                found.update(r[0] for r in con.execute(q))
        return found

    def existing_keys(self, handle_keys) -> set:
        """这批 handle_key 里【已富化(creator_profile 存在,任意新鲜度)】的子集。
        "已存在就跳" 的去重语义 —— 区别于 fresh_keys(只算 refresh_days 内的近期富化)。"""
        return {
            item.requested_handle_key
            for item in CreatorIdentityReader(self.engine).resolve_handles(
                self.market,
                handle_keys,
            )
            if item.creator is not None
        }

    def missed_keys(self, handle_keys) -> set:
        """这批 handle_key 里【已知死号(miss_log 标记过,任意时间)】的子集。
        跳过已知死号用 —— 区别于 negcached_keys(只算 miss_skip_days TTL 内的)。"""
        return self._subset_present(self.mtbl, handle_keys)

    # ---------------- 负缓存(死号) ----------------
    def mark_miss(self, handle_key: str, evidence: ExactMissEvidence):
        """只接受带精确匹配证据的死号负缓存写入。"""
        if not isinstance(evidence, ExactMissEvidence):
            raise TypeError("mark_miss 必须传 ExactMissEvidence")
        values = {"handle_key": handle_key, "missed_at": _now(), "n": 1}
        idx = ["handle_key"]
        if self._has_bdmkt:
            values["bd_market"] = self.market
            idx = ["handle_key", "bd_market"]
        stmt = pg_insert(self.mtbl).values(**values)
        stmt = stmt.on_conflict_do_update(
            index_elements=idx,
            set_={"missed_at": stmt.excluded.missed_at, "n": self.mtbl.c.n + 1},
        )
        with self.engine.begin() as con:
            con.execute(stmt)

    def is_missed(self, handle_key: str, miss_skip_days: int) -> bool:
        q = select(self.mtbl.c.missed_at).where(self.mtbl.c.handle_key == handle_key)
        if self._has_bdmkt:
            q = q.where(self.mtbl.c.bd_market == self.market)
        with self.engine.connect() as con:
            ts = con.execute(q).scalar()
        dt = _as_dt(ts)
        return bool(dt and (_now() - dt) < timedelta(days=miss_skip_days))

    def clear_miss(
        self,
        handle_key: str,
        *,
        observed_at: datetime | None = None,
    ):
        """清掉不晚于成功画像观测时间的死号标记。"""
        q = delete(self.mtbl).where(self.mtbl.c.handle_key == handle_key)
        if observed_at is not None:
            cutoff = _as_dt(observed_at)
            if cutoff is None:
                raise ValueError("clear_miss observed_at 非法")
            q = q.where(self.mtbl.c.missed_at <= cutoff)
        if self._has_bdmkt:
            q = q.where(self.mtbl.c.bd_market == self.market)
        with self.engine.begin() as con:
            con.execute(q)

    def clear_recent_misses(self, seconds: float) -> int:
        """撤销最近 seconds 内的死号标记——确认软限流时取消被风控误伤的 miss。"""
        cutoff = _now() - timedelta(seconds=seconds)
        q = delete(self.mtbl).where(self.mtbl.c.missed_at >= cutoff)
        if self._has_bdmkt:
            q = q.where(self.mtbl.c.bd_market == self.market)
        with self.engine.begin() as con:
            res = con.execute(q)
            return res.rowcount

    def miss_count(self) -> int:
        q = select(func.count()).select_from(self.mtbl)
        if self._has_bdmkt:
            q = q.where(self.mtbl.c.bd_market == self.market)
        with self.engine.connect() as con:
            return int(con.execute(q).scalar() or 0)

    # ---------------- 画像 upsert / 查询 ----------------
    def upsert(
        self,
        handle_key: str,
        profile,
        *,
        capture: CaptureContext | None = None,
    ):
        """先落不可变快照，再由同一事务刷新 OEC current 与 legacy 兼容缓存。"""
        captured_at = _now()
        context = capture or CaptureContext(
            market=self.market,
            kind=CaptureKind.LEGACY_IMPORT,
            route="legacy_runtime",
            expected_oec=str(profile.oec_id or ""),
            idempotency_key=f"legacy_runtime:{uuid4().hex}",
            captured_at=captured_at,
        )
        if context.market != self.market:
            raise ValueError(
                f"capture.market={context.market!r} 与 store.market={self.market!r} 不一致"
            )
        return ProfileSnapshotStore(self.engine).ingest(
            handle_key,
            profile,
            context,
        )

    def reconcile_snapshot(
        self,
        idempotency_key: str,
        *,
        expected_oec: str,
    ):
        """重放同一 durable 工作项已落库的快照；不存在时返回 ``None``。"""
        result = ProfileSnapshotStore(self.engine).reconcile_if_present(
            idempotency_key,
            expected_market=self.market,
            expected_oec=expected_oec,
        )
        if (
            result is not None
            and result.quality is not SnapshotQuality.DEGRADED
            and result.observed_handle_key
        ):
            # 原始摄取可能已提交快照、但随后清理 miss 或写任务终态失败。
            # 恢复路径继续完成这一幂等清理，避免留下“有画像又是死号”的冲突事实。
            self.clear_miss(
                result.observed_handle_key,
                observed_at=result.captured_at,
            )
        return result

    def count(self) -> int:
        q = select(func.count()).select_from(creator_profile_current).where(
            creator_profile_current.c.bd_market == self.market
        )
        with self.engine.connect() as con:
            return int(con.execute(q).scalar() or 0)

    def handles_with_gmv_over(self, threshold: float) -> list[str]:
        """从库里捞 GMV(gmv_value) 超过阈值的 handle，供 Phase2 满血深抓。NUMERIC 列直接比较，不再 CAST。"""
        c = creator_profile_current.c
        q = select(c.handle_key).where(
            c.bd_market == self.market,
            c.gmv_value.isnot(None),
            c.gmv_value > threshold,
        )
        q = q.order_by(c.gmv_value.desc())
        with self.engine.connect() as con:
            rows = con.execute(q).fetchall()
        return [r[0] for r in rows]

    # 英文列 → 中文表头（导出给人看）
    _ZH = {
        "handle": "达人handle", "nickname": "昵称", "market": "市场", "followers": "粉丝数",
        "main_category": "主类目", "gmv_value": "GMV", "gmv_display": "GMV显示", "video_gmv": "视频GMV", "live_gmv": "直播GMV",
        "units_sold": "成交件数", "gpm": "GPM", "live_gpm": "直播GPM", "video_gpm": "视频GPM", "commission_rate": "佣金率区间", "price_range": "商品价格区间",
        "video_cnt_30d": "近30天全部视频", "ec_video_cnt_30d": "近30天带货视频", "live_cnt_30d": "近30天直播场次", "avg_view": "平均播放",
        "female_pct": "女性粉丝占比", "top_age": "主力年龄", "top_region": "主力地区", "brands": "合作品牌",
        "is_fast_growing": "快速成长", "is_quickly_response": "响应快", "is_high_sample_dispatch": "高样品发放",
        "is_active": "活跃达人", "contact_available": "披露联系方式", "bind_mcn": "绑定MCN",
        "is_our_mcn": "我方MCN", "oec_id": "OEC_ID", "labels": "标签", "avatar_url": "头像URL",
        "captured_at": "抓取时间", "bio": "达人简介", "email": "邮箱(简介中)",
    }
    _ORDER = ["handle", "nickname", "email", "market", "followers", "main_category", "gmv_value",
              "video_gmv", "live_gmv", "units_sold", "gpm", "live_gpm", "video_gpm", "commission_rate", "price_range",
              "video_cnt_30d", "ec_video_cnt_30d", "live_cnt_30d", "avg_view", "female_pct", "top_age", "top_region",
              "brands", "is_fast_growing", "is_quickly_response", "is_high_sample_dispatch",
              "is_active", "contact_available", "bind_mcn", "is_our_mcn", "oec_id", "labels",
              "bio", "avatar_url", "captured_at"]
    _BOOL = {"is_fast_growing", "is_quickly_response", "is_high_sample_dispatch", "is_active",
             "contact_available", "is_our_mcn"}
    _WIDTH = {"达人handle": 22, "昵称": 16, "主类目": 22, "合作品牌": 34, "商品价格区间": 18,
              "头像URL": 42, "OEC_ID": 20, "抓取时间": 19, "标签": 18}

    def export_xlsx(self, xlsx_path: Path) -> int:
        import pandas as pd
        from openpyxl import load_workbook
        from openpyxl.styles import Font, PatternFill, Alignment
        from openpyxl.utils import get_column_letter

        # 读取走连接池；gmv_value 已是 NUMERIC，ORDER BY 直接用（不再 CAST）
        where = " WHERE bd_market = :market" if self._has_bdmkt else ""
        params = {"market": self.market} if self._has_bdmkt else {}
        query = text(f"SELECT * FROM {self._pname}{where} ORDER BY gmv_value DESC NULLS LAST")
        df = pd.read_sql_query(query, self.engine, params=params)
        df = df[[c for c in self._ORDER if c in df.columns]]
        # 布尔列 True/False → ✓/空；女性占比 → 百分比
        for c in self._BOOL:
            if c in df.columns:
                df[c] = df[c].apply(lambda v: "✓" if str(v) in ("1", "True", "true") else "")
        if "female_pct" in df.columns:
            df["female_pct"] = (pd.to_numeric(df["female_pct"], errors="coerce") * 100).round(1).map(
                lambda x: "" if pd.isna(x) else f"{x}%")
        df = df.rename(columns=self._ZH)

        # Excel 不支持带时区 datetime(captured_at 是 TIMESTAMPTZ)→ 落盘前把 tz-aware 列转 tz-naive
        for _c in df.columns:
            if isinstance(df[_c].dtype, pd.DatetimeTZDtype):
                df[_c] = df[_c].dt.tz_localize(None)

        Path(xlsx_path).parent.mkdir(parents=True, exist_ok=True)
        df.to_excel(xlsx_path, index=False)

        wb = load_workbook(xlsx_path)
        ws = wb.active
        ws.freeze_panes = "B2"
        ws.auto_filter.ref = ws.dimensions
        for cell in ws[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="527E65")
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        for i, col in enumerate(df.columns, 1):
            ws.column_dimensions[get_column_letter(i)].width = self._WIDTH.get(col, 13)
        ws.row_dimensions[1].height = 26
        wb.save(xlsx_path)
        return len(df)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        # 共享 engine 由进程持有（连接池自动回收连接），单个 Store 关闭不 dispose 全局池。
        pass


__all__ = ["ProfileStore"]
