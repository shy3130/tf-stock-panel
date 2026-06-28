"""盘后管道 + 盘前维表同步。

调度:
  09:10 盘前 — 同步标的维表 instruments (全量覆盖)
  15:30 盘后 — 日K同步 + 增量除权因子 + enriched 计算 + 刷新视图

盘后同步策略:
  日 K: QuoteService 交易时段已实时落盘 → 有数据时跳过 batch,首次拉 1 年区间
  除权因子: 从已有数据最新日期的下一天开始增量获取,避免重复拉取和计算
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

import polars as pl
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app import secrets_store
from app.indicators.pipeline import run_pipeline
from app.config import settings
from app.services import index_sync, instrument_sync, kline_sync
from app.services.local_quant_import import (
    import_local_quant_incremental,
    import_local_quant_minute,
    local_quant_auto_import_enabled,
    upsert_instruments_from_local_quant,
)
from app.services.tushare_import import import_tushare_daily, import_tushare_index_daily, upsert_instruments_from_tushare
from app.tickflow.capabilities import Cap, CapabilitySet
from app.tickflow.pools import DEMO_SYMBOLS, get_pool
from app.tickflow.repository import KlineRepository

logger = logging.getLogger(__name__)

ProgressCb = Callable[..., None]


def _noop(stage: str, pct: int, msg: str, **kwargs) -> None:  # noqa: ARG001
    pass


def _invalidate(table: str | None = None) -> None:
    """stage 写完调用,让 /api/data/status 只重算被影响的那张表。"""
    from app.api.data import invalidate_data_cache
    invalidate_data_cache(table)


def _resolve_universe(capset: CapabilitySet) -> list[str]:
    """解析标的池 — 以 CN_Equity_A (沪深京A股 ~5522只) 为主。

    有 batch 能力 → 直接拉 CN_Equity_A universe
    其他用户 → 用 instruments parquet + watchlist 兜底
    """
    if capset.has(Cap.KLINE_DAILY_BATCH):
        try:
            all_a = get_pool("CN_Equity_A", refresh=True)
            if all_a:
                return sorted(all_a)
        except Exception as e:  # noqa: BLE001
            logger.warning("CN_Equity_A pool unavailable, fallback: %s", e)

    # Free 用户兜底: instruments parquet + watchlist + demo
    base: set[str] = set(DEMO_SYMBOLS)
    base.update(get_pool("watchlist"))
    d = Path(settings.data_dir)
    inst_path = d / "instruments" / "instruments.parquet"
    if inst_path.exists():
        try:
            inst = pl.read_parquet(inst_path, columns=["symbol"])
            base.update(inst["symbol"].to_list())
        except Exception as e:  # noqa: BLE001
            logger.warning("instruments supplement failed: %s", e)
    return sorted(base)


def run_instruments_sync(repo: KlineRepository) -> dict:
    """盘前同步标的维表。"""
    if local_quant_auto_import_enabled():
        rows = upsert_instruments_from_local_quant(repo)
        _refresh_instruments_view(repo)
        _invalidate("instruments")
        return {"instruments_rows": rows, "source": "local_quant"}

    if secrets_store.get_tushare_token():
        rows = upsert_instruments_from_tushare(repo)
        _refresh_instruments_view(repo)
        _invalidate("instruments")
        return {"instruments_rows": rows, "source": "tushare"}

    rows = instrument_sync.sync_instruments(repo.store.data_dir)
    _refresh_instruments_view(repo)
    _invalidate("instruments")
    return {"instruments_rows": rows, "source": "tickflow"}


def run_now(
    repo: KlineRepository,
    capset: CapabilitySet,
    on_progress: ProgressCb | None = None,
) -> dict:
    """立即执行一次盘后管道,支持进度回调。

    跳过的 stage **不 emit**,避免前端把"无 capability"的卡片错误标记为 active/done。
    result 里带 skipped_stages 列表供前端展示。
    """
    emit = on_progress or _noop
    skipped: list[str] = []
    local_quant_enabled = local_quant_auto_import_enabled()
    tushare_enabled = bool(secrets_store.get_tushare_token())
    inst_source = "tickflow"

    # Step 0: 先同步标的维表, 再解析标的池 — 确保标的池基于最新 instruments
    emit("sync_instruments", 2, "同步标的维表…")
    if local_quant_enabled:
        try:
            inst_rows = upsert_instruments_from_local_quant(repo)
            inst_source = "local_quant"
            _refresh_instruments_view(repo)
        except Exception as e:  # noqa: BLE001
            logger.warning("local_quant instruments sync failed, fallback to TickFlow: %s", e)
            inst_rows = instrument_sync.sync_instruments(repo.store.data_dir)
            if inst_rows > 0:
                _refresh_instruments_view(repo)
    elif tushare_enabled:
        try:
            inst_rows = upsert_instruments_from_tushare(repo)
            inst_source = "tushare"
            _refresh_instruments_view(repo)
        except Exception as e:  # noqa: BLE001
            logger.warning("Tushare instruments sync failed, fallback to TickFlow: %s", e)
            inst_rows = instrument_sync.sync_instruments(repo.store.data_dir)
            if inst_rows > 0:
                _refresh_instruments_view(repo)
    else:
        inst_rows = instrument_sync.sync_instruments(repo.store.data_dir)
        if inst_rows > 0:
            _refresh_instruments_view(repo)
    emit("sync_instruments", 8, f"标的维表同步完成,{inst_rows} 只标的")
    _invalidate("instruments")

    emit("resolve_universe", 9, "解析标的池…")
    universe = _resolve_universe(capset)
    emit("resolve_universe", 10, f"标的池规模:{len(universe)} 只")

    # Step 1: 日 K 同步
    #   付费档 + 今天有数据 → 实时行情接口拉一次覆写（1请求全市场）
    #   有历史数据 → batch K-line API 补齐缺口
    #   无任何数据 → batch K-line API 拉首次 1 年
    from datetime import date as _date, timedelta as _td, datetime as _dt
    latest_daily = repo.latest_daily_date()
    today = _date.today()
    today_exists = latest_daily and latest_daily >= today
    new_daily_days = 0
    local_daily_result: dict | None = None
    tushare_daily_result: dict | None = None

    if local_quant_enabled:
        try:
            emit("sync_daily", 12, "从本地 quant-screener 增量导入日K…")
            local_daily_result = import_local_quant_incremental(repo, compute_enriched=False)
            if local_daily_result.get("status") == "unavailable":
                raise RuntimeError(str(local_daily_result.get("reason") or "local quant unavailable"))
            written_daily = int(local_daily_result.get("rows_written") or 0)
            start_date = local_daily_result.get("start_date")
            end_date = local_daily_result.get("end_date")
            new_daily_days = len(list((repo.store.data_dir / "kline_daily").glob("date=*")))
            _refresh_single_view(repo, "kline_daily")
            emit("sync_daily", 45, f"本地日K导入完成,{written_daily} 行 [{start_date} ~ {end_date}]")
            logger.info("local_quant sync_daily done: %s", local_daily_result)
        except Exception as e:  # noqa: BLE001
            logger.warning("local_quant daily import failed, fallback to TickFlow: %s", e)
            local_daily_result = None

    if local_daily_result is not None:
        pass
    elif tushare_enabled and not capset.has(Cap.KLINE_DAILY_BATCH):
        start_date = latest_daily or (today - _td(days=365))
        emit("sync_daily", 12, f"从 Tushare 导入日K [{start_date} ~ {today}]…")
        logger.info("sync_daily tushare: [%s ~ %s] start", start_date, today)
        try:
            tushare_daily_result = import_tushare_daily(
                repo,
                start_date=start_date,
                end_date=today,
                compute_enriched=False,
            )
            written_daily = int(tushare_daily_result.get("rows_written") or 0)
            new_daily_days = max((today - start_date).days, 1)
            _refresh_single_view(repo, "kline_daily")
            emit("sync_daily", 45, f"Tushare 日K导入完成,{written_daily} 行")
            logger.info("sync_daily tushare done: %s", tushare_daily_result)
        except Exception as e:  # noqa: BLE001
            logger.warning("Tushare daily import failed: %s", e)
            skipped.append("sync_daily")
            written_daily = 0
            new_daily_days = 0
    elif today_exists and capset.has(Cap.QUOTE_POOL):
        # 付费档:今天有数据(QuoteService 已落盘)→ 实时行情覆写,确保最新。
        # free/none 档无 quote.pool 能力,即便今天已有数据(如从 expert 降级),
        # 也降级到下方 batch 路径刷新,避免调用无权限的实时行情接口。
        emit("sync_daily", 12, f"获取日K [{today} ~ {today}] 实时行情…")
        written_daily = kline_sync.sync_daily_by_quotes(repo)
        new_daily_days = 1
        emit("sync_daily", 45, f"日K 完成,{written_daily} 只标的")
        logger.info("sync_daily: [%s ~ %s] live quotes, %d symbols", today, today, written_daily)
    elif latest_daily:
        # 有历史 → batch 补齐缺口。
        # 也覆盖"今天已有数据但无实时行情权限(free/none)"的降级场景:
        #   此时 start_date = latest_daily = today,batch 刷新当天日K。
        start_date = latest_daily
        emit("sync_daily", 12, f"获取日K [{start_date} ~ {today}]…")
        logger.info("sync_daily: [%s ~ %s] %s", start_date, today,
                    "refresh today" if today_exists else "gap fill")

        def _daily_chunk_progress(cur: int, tot: int) -> None:
            emit("sync_daily", 12 + int(33 * cur / tot),
                 f"日K 批次 {cur}/{tot}", stage_pct=int(100 * cur / tot), skip_log=True)
        written_daily = kline_sync.sync_and_persist_daily_batch(
            universe, repo, capset,
            start_date=_dt.combine(start_date, _dt.min.time()),
            end_date=_dt.combine(today, _dt.min.time()),
            on_chunk_done=_daily_chunk_progress,
        )
        gap_days = (today - start_date).days
        new_daily_days = gap_days
        emit("sync_daily", 45, f"日K 完成,覆盖 {gap_days} 天")
        logger.info("sync_daily: [%s ~ %s] done, %d days", start_date, today, gap_days)
    else:
        # 首次：无任何数据 → batch 拉 1 年
        start_date = today - _td(days=365)
        emit("sync_daily", 12, f"获取日K [{start_date} ~ {today}]…")
        logger.info("sync_daily: [%s ~ %s] initial fetch", start_date, today)

        def _daily_chunk_progress(cur: int, tot: int) -> None:
            emit("sync_daily", 12 + int(33 * cur / tot),
                 f"日K 批次 {cur}/{tot}", stage_pct=int(100 * cur / tot), skip_log=True)
        written_daily = kline_sync.sync_and_persist_daily_batch(
            universe, repo, capset,
            start_date=_dt.combine(start_date, _dt.min.time()),
            end_date=_dt.combine(today, _dt.min.time()),
            on_chunk_done=_daily_chunk_progress,
        )
        new_daily_days = 365
        emit("sync_daily", 45, "日K 完成")
        logger.info("sync_daily: [%s ~ %s] done", start_date, today)
    _invalidate("daily")

    # Step 1.5: 增量同步除权因子 — 从已有数据最新日期的下一天开始获取
    written_adj = 0
    affected_symbols: list[str] = []
    if capset.has(Cap.ADJ_FACTOR):
        from datetime import datetime, timedelta
        adj_end = datetime.now()
        # 从已有除权因子数据的最新日期开始获取，避免重复拉取
        adj_factor_path = repo.store.data_dir / "adj_factor" / "all.parquet"
        fallback_start = adj_end - timedelta(days=30)
        if adj_factor_path.exists():
            try:
                from datetime import date as date_cls
                max_date = pl.scan_parquet(adj_factor_path).select(
                    pl.col("trade_date").max()
                ).collect().item()
                if max_date is not None:
                    # trade_date 可能是 date / datetime / string 类型
                    if isinstance(max_date, str):
                        td = date_cls.fromisoformat(max_date)
                    elif isinstance(max_date, datetime):
                        td = max_date.date()
                    else:
                        td = max_date
                    adj_start = datetime.combine(td, datetime.min.time())
                else:
                    adj_start = fallback_start
            except Exception:
                adj_start = fallback_start
        else:
            adj_start = fallback_start
        adj_start_str = adj_start.strftime("%Y-%m-%d")
        adj_end_str = adj_end.strftime("%Y-%m-%d")
        emit("sync_adj", 50, f"获取除权因子 [{adj_start_str} ~ {adj_end_str}]…")
        logger.info("sync_adj: [%s ~ %s] start", adj_start_str, adj_end_str)

        def _adj_chunk_progress(cur: int, tot: int) -> None:
            emit("sync_adj", 50 + int(10 * cur / tot),
                 f"除权因子批次 {cur}/{tot}", stage_pct=int(100 * cur / tot), skip_log=True)
        written_adj, affected_symbols = kline_sync.sync_adj_factor(
            universe, repo, capset,
            start_time=adj_start, end_time=adj_end,
            on_chunk_done=_adj_chunk_progress,
        )
        if affected_symbols:
            _refresh_single_view(repo, "adj_factor")
            emit("sync_adj", 60, f"除权因子完成,新增 {len(affected_symbols)} 只个股")
            logger.info("sync_adj: [%s ~ %s] done, %d symbols", adj_start_str, adj_end_str, len(affected_symbols))
        else:
            emit("sync_adj", 60, "除权因子完成,无新增")
            logger.info("sync_adj: [%s ~ %s] no new factors", adj_start_str, adj_end_str)
        _invalidate("adj_factor")
    else:
        skipped.append("sync_adj")
        logger.info("sync_adj skipped: no ADJ_FACTOR capability")

    # Step 2: 计算 enriched
    #   判断策略:
    #     - 首次 (enriched 目录不存在) → 全量
    #     - 往前扩展历史 (新日期 < enriched 已有最早日期) → 全量
    #       前面的除权因子会改变累积因子链,影响后面所有日期的复权价格
    #     - 往后新增日期 (新日期 > enriched 已有最晚日期)
    #       → 增量补新区块(所有标的) + 受除权影响个股全日期重算
    #     - 无新日期 + 有新除权因子 → 增量: 只重算受影响个股的全部日期
    #     - 无新日期 + 无变化 → 跳过
    enriched_dir = repo.store.data_dir / "kline_daily_enriched"
    enriched_exists = enriched_dir.exists() and any(enriched_dir.glob("date=*"))
    daily_dir = repo.store.data_dir / "kline_daily"
    daily_days = len(list(daily_dir.glob("date=*"))) if daily_dir.exists() else 0
    prev_enriched_days = len(list(enriched_dir.glob("date=*"))) if enriched_exists else 0

    # 判断新日期方向: 找 daily 和 enriched 的日期集合做比较
    forward_incremental = False
    backward_extension = False

    if daily_days > prev_enriched_days and enriched_exists:
        daily_dates = sorted(d.stem.split("=")[1] for d in daily_dir.glob("date=*"))
        enriched_dates = sorted(d.stem.split("=")[1] for d in enriched_dir.glob("date=*"))
        earliest_enriched = enriched_dates[0]
        latest_enriched = enriched_dates[-1]
        new_dates = set(daily_dates) - set(enriched_dates)
        if new_dates:
            # 有新日期早于 enriched 最早日期 → 往前扩展
            if any(d < earliest_enriched for d in new_dates):
                backward_extension = True
            # 有新日期晚于 enriched 最晚日期 → 往后新增
            if any(d > latest_enriched for d in new_dates):
                forward_incremental = True

    def _enriched_batch_progress(cur: int, tot: int) -> None:
        emit("compute_enriched", 65 + int(23 * cur / tot),
             f"计算指标 批次 {cur}/{tot}", stage_pct=int(100 * cur / tot), skip_log=True)

    if not enriched_exists or backward_extension:
        # 首次 或 往前扩展 → 全量
        emit("compute_enriched", 65, "全量计算 enriched…")
        logger.info("compute_enriched: full rebuild (first=%s, backward=%s, daily=%d, enriched=%d)",
                    not enriched_exists, backward_extension, daily_days, prev_enriched_days)
        written_enriched = run_pipeline(on_batch_done=_enriched_batch_progress)
        new_enriched_days = len(list(enriched_dir.glob("date=*")))
        emit("compute_enriched", 88, f"enriched 完成,覆盖 {new_enriched_days} 天")
        logger.info("compute_enriched: full rebuild done, %d days", new_enriched_days)
    elif forward_incremental:
        # 往后新增日期: 增量补新区块 + 受影响个股全日期重算
        symbols_to_recompute = list(set(affected_symbols)) if affected_symbols else []
        emit("compute_enriched", 65,
             f"增量计算 enriched (新日期 + {len(symbols_to_recompute)} 只个股重算)…"
             if symbols_to_recompute else "增量计算 enriched (新日期)…")
        logger.info("compute_enriched: forward incremental, %d symbols to recompute",
                    len(symbols_to_recompute))
        written_enriched = run_pipeline(
            new_dates_only=True,
            symbols=symbols_to_recompute or None,
            on_batch_done=_enriched_batch_progress,
        )
        new_enriched_days = len(list(enriched_dir.glob("date=*")))
        emit("compute_enriched", 88, f"enriched 完成,覆盖 {new_enriched_days} 天")
        logger.info("compute_enriched: forward incremental done, %d days", new_enriched_days)
    elif affected_symbols:
        # 无新日期,仅除权因子变更 → 只重算受影响个股的全部日期
        emit("compute_enriched", 65, f"增量计算 enriched ({len(affected_symbols)} 只个股)…")
        logger.info("compute_enriched: adj_factor incremental, %d symbols", len(affected_symbols))
        written_enriched = run_pipeline(symbols=affected_symbols, on_batch_done=_enriched_batch_progress)
        emit("compute_enriched", 88, f"enriched 完成,{len(affected_symbols)} 只个股")
    else:
        written_enriched = 0
        logger.info("compute_enriched: skip (no new daily, no adj_factor changes)")
    _refresh_single_view(repo, "kline_enriched")
    _invalidate("enriched")

    # Step 2.3: 指数同步 — 独立 kline_index_* 存储，不进入股票选股/策略链路。
    written_index_daily = 0
    index_count = 0
    if capset.has(Cap.KLINE_DAILY_BATCH):
        emit("sync_index", 88, "同步指数列表与日K…")
        try:
            index_count = index_sync.sync_index_instruments(repo)
            index_dir = repo.store.data_dir / "kline_index_enriched"
            index_dates = sorted(
                d.name[5:] for d in index_dir.glob("date=*")
                if d.is_dir() and d.name.startswith("date=")
            ) if index_dir.exists() else []
            index_start = _date.fromisoformat(index_dates[-1]) if index_dates else today - _td(days=365)
            written_index_daily = index_sync.sync_and_persist_index_daily(
                repo,
                capset,
                start_date=_dt.combine(index_start, _dt.min.time()),
                end_date=_dt.combine(today, _dt.min.time()),
            )
            repo.refresh_index_views()
            _invalidate("index_instruments")
            _invalidate("index_daily")
            _invalidate("index_enriched")
            emit("sync_index", 89, f"指数完成,{index_count} 只指数,{written_index_daily} 行日K")
        except Exception as e:  # noqa: BLE001
            logger.warning("sync_index failed: %s", e)
            emit("sync_index", 89, f"指数同步失败:{e}")
    elif tushare_enabled:
        emit("sync_index", 88, "从 Tushare 同步指数列表与日K…")
        try:
            index_dir = repo.store.data_dir / "kline_index_enriched"
            index_dates = sorted(
                d.name[5:] for d in index_dir.glob("date=*")
                if d.is_dir() and d.name.startswith("date=")
            ) if index_dir.exists() else []
            index_start = _date.fromisoformat(index_dates[-1]) if index_dates else today - _td(days=365)
            index_result = import_tushare_index_daily(
                repo,
                start_date=index_start,
                end_date=today,
            )
            index_count = int(index_result.get("index_count") or 0)
            written_index_daily = int(index_result.get("rows_written") or 0)
            _invalidate("index_instruments")
            _invalidate("index_daily")
            _invalidate("index_enriched")
            emit("sync_index", 89, f"Tushare 指数完成,{index_count} 只指数 {written_index_daily} 行日K")
        except Exception as e:  # noqa: BLE001
            skipped.append("sync_index")
            logger.warning("sync_index tushare failed: %s", e)
            emit("sync_index", 89, f"Tushare 指数同步失败:{e}")
    else:
        skipped.append("sync_index")

    # Step 2.5: 分钟 K 同步(可选) — 未启用或无 capability 时静默跳过(不 emit)
    from app.services import preferences
    minute_on = preferences.get_minute_sync_enabled()
    minute_days = preferences.get_minute_sync_days()
    minute_source = preferences.get_minute_sync_source()
    written_minute = 0
    local_minute_result: dict | None = None
    if minute_on and minute_source == "local_quant":
        minute_start = today - _td(days=minute_days)
        emit("sync_minute", 90, f"从本地 Tushare 导入分钟K [{minute_start} ~ {today}]…")
        logger.info("sync_minute local_quant: [%s ~ %s] start", minute_start, today)
        try:
            local_minute_result = import_local_quant_minute(repo, days=minute_days)
            written_minute = int(local_minute_result.get("rows_written") or 0)
            minute_dir = repo.store.data_dir / "kline_minute"
            minute_cover_days = len(list(minute_dir.glob("date=*"))) if minute_dir.exists() else 0
            emit("sync_minute", 93, f"本地分钟K完成,覆盖 {minute_cover_days} 天")
            logger.info("sync_minute local_quant done: %s", local_minute_result)
            _invalidate("minute")
        except Exception as e:  # noqa: BLE001
            skipped.append("sync_minute")
            logger.warning("local_quant minute import failed: %s", e)
            emit("sync_minute", 93, f"本地分钟K导入失败:{e}")
    elif minute_on and capset.has(Cap.KLINE_MINUTE_BATCH):
        minute_start = today - _td(days=minute_days)
        emit("sync_minute", 90, f"获取分钟K [{minute_start} ~ {today}]…")
        logger.info("sync_minute: [%s ~ %s] start", minute_start, today)
        minute_symbols = _resolve_minute_symbols(capset)
        def _minute_chunk_progress(cur: int, tot: int) -> None:
            emit("sync_minute", 90 + int(3 * cur / tot),
                 f"分钟K 批次 {cur}/{tot}", stage_pct=int(100 * cur / tot), skip_log=True)
        written_minute = kline_sync.sync_and_persist_minute(
            minute_symbols, repo, capset, days=minute_days,
            on_chunk_done=_minute_chunk_progress,
        )
        minute_dir = repo.store.data_dir / "kline_minute"
        minute_cover_days = len(list(minute_dir.glob("date=*"))) if minute_dir.exists() else 0
        emit("sync_minute", 93, f"分钟K完成,覆盖 {minute_cover_days} 天")
        logger.info("sync_minute: [%s ~ %s] done, %d days", minute_start, today, minute_cover_days)
        _invalidate("minute")
    else:
        skipped.append("sync_minute")
        if minute_on:
            logger.info("sync_minute skipped: no KLINE_MINUTE_BATCH capability")
        else:
            logger.info("sync_minute skipped: user disabled")

    # Step 3: 刷新视图
    emit("refresh_views", 95, "刷新 DuckDB 视图…")
    _refresh_views(repo)

    emit("done", 100, "完成")
    _invalidate(None)  # 兜底:全清

    return {
        "universe_size": len(universe),
        "daily_days": new_daily_days,
        "adj_factor_symbols": len(affected_symbols),
        "enriched_days": written_enriched,
        "index_count": index_count,
        "index_daily_rows": written_index_daily,
        "minute_rows": written_minute,
        "data_source": "local_quant" if local_daily_result is not None else ("tushare" if tushare_daily_result is not None else "tickflow"),
        "instrument_source": inst_source,
        "local_quant_daily": local_daily_result,
        "tushare_daily": tushare_daily_result,
        "local_quant_minute": local_minute_result,
        "skipped_stages": skipped,
    }


def _refresh_views(repo: KlineRepository) -> None:
    """刷新所有 DuckDB 视图。"""
    d = repo.store.data_dir.as_posix()
    views = {
        "kline_daily": f"{d}/kline_daily/**/*.parquet",
        "kline_enriched": f"{d}/kline_daily_enriched/**/*.parquet",
        "kline_index_daily": f"{d}/kline_index_daily/**/*.parquet",
        "kline_index_enriched": f"{d}/kline_index_enriched/**/*.parquet",
        "kline_minute": f"{d}/kline_minute/**/*.parquet",
        "adj_factor": f"{d}/adj_factor/**/*.parquet",
        "instruments": f"{d}/instruments/**/*.parquet",
        "instruments_index": f"{d}/instruments_index/**/*.parquet",
    }
    for name, path in views.items():
        try:
            repo.db.execute(
                f"CREATE OR REPLACE VIEW {name} AS "
                f"SELECT * FROM read_parquet('{path}', union_by_name=true)"
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("refresh view %s failed: %s", name, e)


def _refresh_single_view(repo: KlineRepository, name: str) -> None:
    """刷新单个 DuckDB 视图。"""
    d = repo.store.data_dir.as_posix()
    paths = {
        "kline_daily": f"{d}/kline_daily/**/*.parquet",
        "kline_enriched": f"{d}/kline_daily_enriched/**/*.parquet",
        "kline_index_daily": f"{d}/kline_index_daily/**/*.parquet",
        "kline_index_enriched": f"{d}/kline_index_enriched/**/*.parquet",
        "kline_minute": f"{d}/kline_minute/**/*.parquet",
        "adj_factor": f"{d}/adj_factor/**/*.parquet",
        "instruments": f"{d}/instruments/**/*.parquet",
        "instruments_index": f"{d}/instruments_index/**/*.parquet",
    }
    path = paths.get(name)
    if not path:
        return
    try:
        repo.db.execute(
            f"CREATE OR REPLACE VIEW {name} AS "
            f"SELECT * FROM read_parquet('{path}', union_by_name=true)"
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("refresh view %s failed: %s", name, e)


def _resolve_minute_symbols(capset: CapabilitySet) -> list[str]:
    """分钟 K 同步标的 — 与日K共用同一标的池。"""
    return _resolve_universe(capset)


def _refresh_instruments_view(repo: KlineRepository) -> None:
    """单独刷新 instruments 视图。"""
    d = repo.store.data_dir.as_posix()
    try:
        repo.db.execute(
            f"CREATE OR REPLACE VIEW instruments AS "
            f"SELECT * FROM read_parquet('{d}/instruments/**/*.parquet', union_by_name=true)"
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("refresh instruments view failed: %s", e)


def _run_tracked(fn, job_label: str) -> None:
    """调度触发时包装 JobStore 跟踪，确保同步历史有记录。"""
    from app.services.pipeline_jobs import job_store

    job_id = job_store.create()
    job_store.start(job_id)

    def progress(stage: str, pct: int, msg: str, stage_pct: int | None = None,
                 skip_log: bool = False) -> None:
        job_store.progress(job_id, stage, pct, msg, stage_pct=stage_pct, skip_log=skip_log)

    try:
        result = fn(on_progress=progress)
        job_store.succeed(job_id, result)
        logger.info("scheduled %s completed: job_id=%s", job_label, job_id)
    except Exception:
        logger.exception("scheduled %s failed: job_id=%s", job_label, job_id)
        job_store.fail(job_id, f"scheduled {job_label} failed")


def start_scheduler(repo: KlineRepository, capset: CapabilitySet) -> AsyncIOScheduler:
    """启动调度器。

    工作日 09:10 — 同步标的维表
    工作日 HH:MM — 盘后管道（时间由用户偏好决定，默认 15:30）
    """
    from app.services import preferences
    sched = preferences.get_pipeline_schedule()
    inst_sched = preferences.get_instruments_schedule()

    scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")

    # 盘前: 同步 instruments（时间由偏好决定）
    def _instruments_task(on_progress=None):
        emit = on_progress or _noop
        emit("sync_instruments", 0, "同步标的维表…")
        result = run_instruments_sync(repo)
        emit("done", 100, f"标的维表同步完成,{result.get('instruments_rows', 0)} 只标的")
        return result

    scheduler.add_job(
        lambda: _run_tracked(_instruments_task, "instruments_sync"),
        trigger=CronTrigger(day_of_week="mon-fri",
                            hour=inst_sched["hour"], minute=inst_sched["minute"],
                            timezone="Asia/Shanghai"),
        id="pre_market_instruments",
        misfire_grace_time=1800,
        replace_existing=True,
    )

    # 盘后: 日 K + enriched（时间由偏好决定）
    scheduler.add_job(
        lambda: _run_tracked(
            lambda on_progress=None: run_now(repo, capset, on_progress=on_progress),
            "daily_pipeline",
        ),
        trigger=CronTrigger(day_of_week="mon-fri",
                            hour=sched["hour"], minute=sched["minute"],
                            timezone="Asia/Shanghai"),
        id="daily_pipeline",
        misfire_grace_time=3600,
        replace_existing=True,
    )
    if not preferences.get_daily_pipeline_enabled():
        scheduler.pause_job("daily_pipeline")

    # 盘后: 五档盘口 sealed 定版(时间由偏好决定, 默认15:02, 范围15:01~18:00)
    depth_sched = preferences.get_depth_finalize_time()

    def _depth_finalize():
        depth_svc = getattr(_get_app_state(), "depth_service", None) if _get_app_state() else None
        if depth_svc:
            depth_svc.finalize()

    scheduler.add_job(
        _depth_finalize,
        trigger=CronTrigger(day_of_week="mon-fri",
                            hour=depth_sched["hour"], minute=depth_sched["minute"],
                            timezone="Asia/Shanghai"),
        id="depth_finalize",
        misfire_grace_time=3600,
        replace_existing=True,
    )

    scheduler.start()
    logger.info("scheduler started; instruments@%02d:%02d, pipeline@%02d:%02d, depth@%02d:%02d mon-fri",
                inst_sched["hour"], inst_sched["minute"], sched["hour"], sched["minute"],
                depth_sched["hour"], depth_sched["minute"])
    return scheduler


# app_state 延迟引用(start_scheduler 在 lifespan 早期调用, app.state 可能还没就绪)
_app_state_ref = None


def set_app_state(app_state) -> None:
    """lifespan 注册 app.state 引用, 供 scheduled job 访问 depth_service 等单例。"""
    global _app_state_ref
    _app_state_ref = app_state


def _get_app_state():
    return _app_state_ref
