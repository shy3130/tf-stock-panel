"""指数数据同步服务。"""
from __future__ import annotations

import logging
import gc
from datetime import datetime, timedelta

import polars as pl

from app.indicators.pipeline import compute_enriched
from app.services import kline_sync, preferences
from app.tickflow.capabilities import Cap, CapabilitySet
from app.tickflow.client import get_client
from app.tickflow.repository import KlineRepository

logger = logging.getLogger(__name__)


def _quotes_to_index_instruments(resp) -> pl.DataFrame:
    """将 TickFlow quotes 响应规范为指数 instruments。"""
    if resp is None:
        return pl.DataFrame()

    if isinstance(resp, pl.DataFrame):
        df = resp
    elif hasattr(resp, "columns"):
        df = pl.from_pandas(resp.reset_index() if hasattr(resp, "reset_index") else resp)
    else:
        rows: list[dict] = []
        for q in resp or []:
            item = q if isinstance(q, dict) else {}
            ext = item.get("ext") or {}
            symbol = item.get("symbol")
            if not symbol:
                continue
            rows.append({
                "symbol": str(symbol),
                "name": ext.get("name") or item.get("name") or str(symbol),
            })
        df = pl.DataFrame(rows)

    if df.is_empty() or "symbol" not in df.columns:
        return pl.DataFrame()

    rename = {"ts_code": "symbol"}
    df = df.rename({k: v for k, v in rename.items() if k in df.columns})

    if "name" not in df.columns:
        if "ext" in df.columns:
            df = df.with_columns(pl.col("symbol").cast(pl.Utf8).alias("name"))
        else:
            df = df.with_columns(pl.col("symbol").cast(pl.Utf8).alias("name"))

    result = df.select([
        pl.col("symbol").cast(pl.Utf8),
        pl.col("name").cast(pl.Utf8),
    ]).with_columns([
        pl.col("symbol").str.split(".").list.first().alias("code"),
        pl.lit("index").alias("asset_type"),
    ])
    return result.unique(subset=["symbol"], keep="last").sort("symbol")


def sync_index_instruments(repo: KlineRepository) -> int:
    """同步 CN_Index 指数标的维表，返回指数数量。"""
    from app import secrets_store
    if secrets_store.get_tushare_token():
        try:
            from app.services.tushare_import import upsert_index_instruments_from_tushare
            count = upsert_index_instruments_from_tushare(repo)
            if count:
                return count
        except Exception as exc:  # noqa: BLE001
            logger.warning("Tushare index instruments fallback failed: %s", exc)

    tf = get_client()
    resp = None
    errors: list[str] = []
    for kwargs in (
        {"universes": ["CN_Index"]},
        {"universes": ["CN_Index"], "as_dataframe": False},
    ):
        try:
            resp = tf.quotes.get_by_universes(**kwargs)
            if resp is not None and len(resp) > 0:
                break
        except Exception as e:  # noqa: BLE001
            errors.append(str(e))
            resp = None

    if resp is None or len(resp) == 0:
        logger.warning("CN_Index universe returned empty: %s", "; ".join(errors))
        return 0

    instruments = _quotes_to_index_instruments(resp)
    if instruments.is_empty():
        return 0
    repo.save_index_instruments(instruments)
    repo.refresh_index_views()
    return instruments.height


def sync_and_persist_index_daily(
    repo: KlineRepository,
    capset: CapabilitySet,
    count: int | None = None,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
) -> int:
    """同步指数日K到独立 parquet，并计算指数 enriched。"""
    if not capset.has(Cap.KLINE_DAILY_BATCH):
        return 0

    instruments = repo.get_index_instruments()
    if instruments.is_empty():
        sync_index_instruments(repo)
        instruments = repo.get_index_instruments()
    if instruments.is_empty() or "symbol" not in instruments.columns:
        return 0

    symbols = sorted(set(instruments["symbol"].to_list()))
    lim = capset.limits(Cap.KLINE_DAILY_BATCH)
    batch_size = preferences.get_index_daily_batch_size()
    if lim and lim.batch:
        batch_size = min(batch_size, lim.batch)
    rpm = lim.rpm if lim else None

    end_time = end_date or datetime.now()
    start_time = start_date or (end_time - timedelta(days=365))

    total_rows = 0
    interval = (60.0 / rpm) if rpm else 0
    chunks = [symbols[i:i + batch_size] for i in range(0, len(symbols), batch_size)]
    for i, chunk in enumerate(chunks):
        if i > 0 and interval > 0 and len(chunks) > rpm:
            import time
            time.sleep(interval)
        raw = kline_sync.sync_daily_batch(
            chunk,
            count=count,
            batch_size=None,
            start_time=start_time,
            end_time=end_time,
        )
        if raw.is_empty():
            continue

        repo.append_index_daily(raw)
        enriched = compute_enriched(raw, factors=None, instruments=None)
        repo.append_index_enriched(enriched)
        total_rows += raw.height
        logger.info("index daily synced: %d/%d chunks, +%d rows", i + 1, len(chunks), raw.height)
        del raw, enriched
        gc.collect()
    repo.refresh_index_views()
    return total_rows
