"""Direct Tushare data import into TickFlow local parquet storage."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
import logging
import os
import time
from typing import Any, Callable
from urllib.parse import urlparse

import httpx
import polars as pl

from app import secrets_store
from app.indicators.pipeline import compute_enriched, run_pipeline
from app.services.local_quant_import import local_quant_minute_status, load_local_quant_settings
from app.tickflow.repository import KlineRepository

logger = logging.getLogger(__name__)

TUSHARE_API_URL = "https://api.tushare.pro"
TUSHARE_RETRIES = 3
TUSHARE_RETRY_SLEEP_SECONDS = 2.0
ProgressCb = Callable[[str, int, str], None]


def _ensure_no_proxy_for_host(url: str) -> None:
    host = (urlparse(url or "").hostname or "").strip()
    if not host:
        return
    existing = os.environ.get("NO_PROXY") or os.environ.get("no_proxy") or ""
    entries = [item.strip() for item in existing.split(",") if item.strip()]
    lowered = {item.lower() for item in entries}
    if host.lower() not in lowered:
        entries.append(host)
    joined = ",".join(entries)
    os.environ["NO_PROXY"] = joined
    os.environ["no_proxy"] = joined


@dataclass(slots=True)
class TushareClient:
    token: str
    api_url: str = TUSHARE_API_URL
    timeout_s: float = 60.0

    def call(self, api_name: str, params: dict[str, Any] | None = None, fields: str = "") -> list[dict[str, Any]]:
        if not self.token:
            raise RuntimeError("Tushare token is not configured")
        payload = {
            "api_name": api_name,
            "token": self.token,
            "params": params or {},
            "fields": fields,
        }
        _ensure_no_proxy_for_host(self.api_url)
        last_error: Exception | None = None
        for attempt in range(1, TUSHARE_RETRIES + 1):
            try:
                with httpx.Client(timeout=self.timeout_s, trust_env=False) as client:
                    resp = client.post(self.api_url, json=payload)
                    resp.raise_for_status()
                    body = resp.json()
                break
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                if attempt >= TUSHARE_RETRIES:
                    raise RuntimeError(f"Tushare {api_name} request failed: {exc}") from exc
                time.sleep(TUSHARE_RETRY_SLEEP_SECONDS * attempt)
        else:  # pragma: no cover
            raise RuntimeError(f"Tushare {api_name} request failed: {last_error}")
        if body.get("code") != 0:
            raise RuntimeError(body.get("msg") or f"Tushare {api_name} failed")
        data = body.get("data") or {}
        names = data.get("fields") or []
        items = data.get("items") or []
        return [dict(zip(names, item, strict=False)) for item in items]


def has_tushare_token() -> bool:
    return bool(secrets_store.get_tushare_token())


def tushare_status() -> dict[str, Any]:
    token = secrets_store.get_tushare_token()
    if not token:
        return {
            "configured": False,
            "token_masked": "",
            "http_url": secrets_store.get_tushare_http_url(),
        }
    return {
        "configured": True,
        "token_masked": secrets_store.mask(token),
        "http_url": secrets_store.get_tushare_http_url(),
    }


def tushare_realtime_available() -> bool:
    if secrets_store.get_tushare_token():
        return True
    try:
        return bool(local_quant_minute_status().get("available"))
    except Exception:
        return False


def _client() -> TushareClient:
    return TushareClient(
        token=secrets_store.get_tushare_token(),
        api_url=secrets_store.get_tushare_http_url(),
    )


def fetch_stock_basic() -> pl.DataFrame:
    rows = _client().call(
        "stock_basic",
        params={"exchange": "", "list_status": "L"},
        fields="ts_code,symbol,name,area,industry,market,list_date",
    )
    if not rows:
        return pl.DataFrame()
    return _normalize_tushare_basic_frame(rows)


def fetch_realtime_quotes() -> list[dict[str, Any]]:
    """Fetch realtime-like quotes from Tushare, falling back to local Tushare minute DB."""
    token = secrets_store.get_tushare_token()
    if token:
        try:
            rows = TushareClient(token=token, api_url=secrets_store.get_tushare_http_url()).call(
                "realtime_quote",
                params={},
                fields="ts_code,name,price,open,high,low,pre_close,vol,amount,change,pct_chg",
            )
            quotes = [_normalize_tushare_quote(row) for row in rows]
            quotes = [q for q in quotes if q.get("symbol")]
            if quotes:
                return quotes
        except Exception as exc:  # noqa: BLE001
            logger.warning("Tushare realtime_quote failed, fallback to local minute DB: %s", exc)
    return _fetch_realtime_from_local_minute()


def _normalize_tushare_quote(row: dict[str, Any]) -> dict[str, Any]:
    last_price = row.get("price") or row.get("close")
    prev_close = row.get("pre_close") or row.get("prev_close")
    change_amount = row.get("change")
    change_pct = row.get("pct_chg")
    return {
        "symbol": row.get("ts_code") or row.get("symbol"),
        "name": row.get("name"),
        "last_price": last_price,
        "prev_close": prev_close,
        "open": row.get("open") or last_price,
        "high": row.get("high") or last_price,
        "low": row.get("low") or last_price,
        "volume": _scale_float(row.get("vol"), 100.0),
        "amount": _scale_float(row.get("amount"), 1000.0),
        "change_pct": change_pct,
        "change_amount": change_amount,
        "timestamp": None,
        "session": "tushare",
    }


def _fetch_realtime_from_local_minute() -> list[dict[str, Any]]:
    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("psycopg is required for local Tushare minute quotes") from exc

    settings = load_local_quant_settings()
    with psycopg.connect(settings.dsn, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                with latest_time as (
                    select max(trade_time) as trade_time
                    from {settings.tushare_schema}.{settings.minute_table}
                )
                select m.ts_code,
                       sb.name,
                       m.trade_time,
                       m.open,
                       m.high,
                       m.low,
                       m.close,
                       m.vol,
                       m.amount
                from {settings.tushare_schema}.{settings.minute_table} m
                join latest_time lt on m.trade_time = lt.trade_time
                left join {settings.tushare_schema}.stock_basic sb on sb.ts_code = m.ts_code
                """
            )
            rows = [dict(row) for row in cur.fetchall()]
    quotes = []
    for row in rows:
        close = _to_float(row.get("close"))
        quotes.append({
            "symbol": row.get("ts_code"),
            "name": row.get("name"),
            "last_price": close,
            "prev_close": None,
            "open": row.get("open"),
            "high": row.get("high"),
            "low": row.get("low"),
            "volume": row.get("vol"),
            "amount": row.get("amount"),
            "change_pct": None,
            "change_amount": None,
            "timestamp": row.get("trade_time"),
            "session": "local_tushare_minute",
        })
    return quotes


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _scale_float(value: Any, factor: float) -> float | None:
    parsed = _to_float(value)
    return parsed * factor if parsed is not None else None


def _normalize_tushare_basic_frame(rows: list[dict[str, Any]]) -> pl.DataFrame:
    if not rows:
        return pl.DataFrame()
    df = pl.DataFrame(rows)
    if df.is_empty():
        return df

    rename_map = {
        "ts_code": "symbol",
        "symbol": "code",
        "total_share": "total_shares",
        "float_share": "float_shares",
        "total_mv": "total_market_cap",
        "circ_mv": "float_market_cap",
    }
    df = df.rename({k: v for k, v in rename_map.items() if k in df.columns})

    exprs: list[pl.Expr] = [
        pl.col("symbol").cast(pl.Utf8, strict=False),
        pl.lit("CN").alias("region"),
        pl.lit("stock").alias("type"),
        pl.lit(date.today()).cast(pl.Date).alias("as_of"),
    ]
    if "code" not in df.columns:
        exprs.append(pl.col("symbol").str.split(".").list.first().alias("code"))
    if "exchange" not in df.columns:
        exprs.append(pl.col("symbol").str.split(".").list.last().alias("exchange"))
    for col in ("code", "name", "industry", "area", "market", "list_date"):
        if col in df.columns:
            exprs.append(pl.col(col).cast(pl.Utf8, strict=False))
    for col in ("pe_ttm", "pb"):
        if col in df.columns:
            exprs.append(pl.col(col).cast(pl.Float64, strict=False))
    # Tushare daily_basic share fields are in 10k shares; mv fields are in 10k CNY.
    if "total_shares" in df.columns:
        exprs.append((pl.col("total_shares").cast(pl.Float64, strict=False) * 10000.0).alias("total_shares"))
    if "float_shares" in df.columns:
        exprs.append((pl.col("float_shares").cast(pl.Float64, strict=False) * 10000.0).alias("float_shares"))
    if "total_market_cap" in df.columns:
        exprs.append((pl.col("total_market_cap").cast(pl.Float64, strict=False) * 10000.0).alias("total_market_cap"))
    if "float_market_cap" in df.columns:
        exprs.append((pl.col("float_market_cap").cast(pl.Float64, strict=False) * 10000.0).alias("float_market_cap"))

    keep = [
        "symbol",
        "name",
        "code",
        "region",
        "type",
        "industry",
        "area",
        "market",
        "exchange",
        "list_date",
        "total_shares",
        "float_shares",
        "total_market_cap",
        "float_market_cap",
        "pe_ttm",
        "pb",
        "as_of",
    ]
    normalized = df.with_columns(exprs)
    return normalized.select([c for c in keep if c in normalized.columns]).drop_nulls(["symbol"])


def _normalize_tushare_daily_frame(
    daily_rows: list[dict[str, Any]],
    basic_rows: list[dict[str, Any]] | None = None,
) -> pl.DataFrame:
    if not daily_rows:
        return pl.DataFrame()
    daily = pl.DataFrame(daily_rows)
    if daily.is_empty():
        return daily
    if basic_rows:
        basic = pl.DataFrame(basic_rows)
        if not basic.is_empty():
            daily = daily.join(basic, on=["ts_code", "trade_date"], how="left")

    df = daily.rename({
        "ts_code": "symbol",
        "trade_date": "date",
        "vol": "volume",
    })
    # Canonical daily matches TickFlow input: raw OHLCV rows before pipeline enrichment.
    # Tushare daily.vol is already in hands; daily.amount is thousand yuan.
    df = df.with_columns(
        pl.col("symbol").cast(pl.Utf8, strict=False),
        pl.col("date").str.strptime(pl.Date, "%Y%m%d", strict=False),
        pl.col("open").cast(pl.Float64, strict=False),
        pl.col("high").cast(pl.Float64, strict=False),
        pl.col("low").cast(pl.Float64, strict=False),
        pl.col("close").cast(pl.Float64, strict=False),
        pl.col("volume").cast(pl.Float64, strict=False),
        (pl.col("amount").cast(pl.Float64, strict=False) * 1000.0).alias("amount"),
        pl.col("turnover_rate").cast(pl.Float64, strict=False)
        if "turnover_rate" in df.columns
        else pl.lit(None).cast(pl.Float64).alias("turnover_rate"),
    )
    keep = ["symbol", "date", "open", "high", "low", "close", "volume", "amount", "turnover_rate"]
    return (
        df.select(keep)
        .unique(subset=["symbol", "date"], keep="last")
        .drop_nulls(["symbol", "date", "open", "high", "low", "close"])
    )


def _normalize_tushare_index_basic_frame(rows: list[dict[str, Any]]) -> pl.DataFrame:
    if not rows:
        return pl.DataFrame()
    df = pl.DataFrame(rows)
    if df.is_empty():
        return df
    df = df.rename({"ts_code": "symbol"})
    exprs: list[pl.Expr] = [
        pl.col("symbol").cast(pl.Utf8, strict=False),
        pl.lit("index").alias("asset_type"),
    ]
    if "code" not in df.columns:
        exprs.append(pl.col("symbol").str.split(".").list.first().alias("code"))
    for col in ("name", "market", "publisher", "category", "base_date", "base_point", "list_date"):
        if col in df.columns:
            exprs.append(pl.col(col).cast(pl.Utf8, strict=False))
    normalized = df.with_columns(exprs)
    keep = ["symbol", "name", "code", "asset_type", "market", "publisher", "category", "base_date", "base_point", "list_date"]
    return normalized.select([c for c in keep if c in normalized.columns]).drop_nulls(["symbol"])


def _normalize_tushare_index_daily_frame(rows: list[dict[str, Any]]) -> pl.DataFrame:
    if not rows:
        return pl.DataFrame()
    df = pl.DataFrame(rows)
    if df.is_empty():
        return df
    df = df.rename({
        "ts_code": "symbol",
        "trade_date": "date",
        "vol": "volume",
    })
    # Tushare index_daily.amount is thousand yuan, same as stock daily.amount.
    df = df.with_columns(
        pl.col("symbol").cast(pl.Utf8, strict=False),
        pl.col("date").str.strptime(pl.Date, "%Y%m%d", strict=False),
        pl.col("open").cast(pl.Float64, strict=False),
        pl.col("high").cast(pl.Float64, strict=False),
        pl.col("low").cast(pl.Float64, strict=False),
        pl.col("close").cast(pl.Float64, strict=False),
        pl.col("volume").cast(pl.Float64, strict=False),
        (pl.col("amount").cast(pl.Float64, strict=False) * 1000.0).alias("amount"),
    )
    keep = ["symbol", "date", "open", "high", "low", "close", "volume", "amount"]
    return (
        df.select(keep)
        .unique(subset=["symbol", "date"], keep="last")
        .drop_nulls(["symbol", "date", "open", "high", "low", "close"])
    )



def upsert_instruments_from_tushare(repo: KlineRepository) -> int:
    df = fetch_stock_basic()
    if df.is_empty():
        return 0
    keep = [
        "symbol",
        "name",
        "code",
        "region",
        "type",
        "industry",
        "area",
        "market",
        "exchange",
        "list_date",
        "total_shares",
        "float_shares",
        "total_market_cap",
        "float_market_cap",
        "pe_ttm",
        "pb",
        "as_of",
    ]
    df = df.select([c for c in keep if c in df.columns])
    out = repo.store.data_dir / "instruments" / "instruments.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        existing = pl.read_parquet(out)
        df = pl.concat([existing, df], how="diagonal_relaxed").unique(subset=["symbol"], keep="last")
    df.sort("symbol").write_parquet(out)
    repo.clear_cache()
    repo.refresh_cache()
    return df.height


def _trade_dates(start: date, end: date) -> list[date]:
    days = (end - start).days + 1
    return [start + timedelta(days=i) for i in range(days) if (start + timedelta(days=i)).weekday() < 5]


def fetch_daily_for_trade_date(trade_date: date) -> pl.DataFrame:
    ds = trade_date.strftime("%Y%m%d")
    client = _client()
    daily_rows = client.call(
        "daily",
        params={"trade_date": ds},
        fields="ts_code,trade_date,open,high,low,close,vol,amount",
    )
    if not daily_rows:
        return pl.DataFrame()
    try:
        basic_rows = client.call(
            "daily_basic",
            params={"trade_date": ds},
            fields="ts_code,trade_date,turnover_rate,total_share,float_share,total_mv,circ_mv,pe_ttm,pb",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Tushare daily_basic skipped for %s: %s", ds, exc)
        basic_rows = []
    return _normalize_tushare_daily_frame(daily_rows, basic_rows)


def fetch_daily_for_symbol(symbol: str, start_date: date, end_date: date) -> pl.DataFrame:
    client = _client()
    params = {
        "ts_code": symbol,
        "start_date": start_date.strftime("%Y%m%d"),
        "end_date": end_date.strftime("%Y%m%d"),
    }
    daily_rows = client.call(
        "daily",
        params=params,
        fields="ts_code,trade_date,open,high,low,close,vol,amount",
    )
    if not daily_rows:
        return pl.DataFrame()
    try:
        basic_rows = client.call(
            "daily_basic",
            params=params,
            fields="ts_code,trade_date,turnover_rate,total_share,float_share,total_mv,circ_mv,pe_ttm,pb",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Tushare daily_basic skipped for %s: %s", symbol, exc)
        basic_rows = []
    return _normalize_tushare_daily_frame(daily_rows, basic_rows)


def import_tushare_daily_symbols(
    repo: KlineRepository,
    symbols: list[str],
    *,
    start_date: date,
    end_date: date,
    compute_enriched: bool = True,
) -> dict[str, Any]:
    rows_written = 0
    unique_symbols = sorted(set(symbols))
    for symbol in unique_symbols:
        df = fetch_daily_for_symbol(symbol, start_date, end_date)
        if df.is_empty():
            continue
        repo.append_daily(df)
        rows_written += df.height

    inst_rows = upsert_instruments_from_tushare(repo)
    repo.store._register_views()
    enriched_rows = 0
    if compute_enriched and rows_written:
        enriched_rows = run_pipeline(repo.store.data_dir, new_dates_only=True)
    repo.store._register_views()
    repo.clear_cache()
    repo.refresh_cache()
    return {
        "status": "ok" if rows_written else "empty",
        "rows_written": rows_written,
        "symbols": len(unique_symbols),
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "instrument_rows": inst_rows,
        "enriched_rows_written": enriched_rows,
        "source": "tushare",
    }


def fetch_index_basic() -> pl.DataFrame:
    frames: list[pl.DataFrame] = []
    client = _client()
    fields = "ts_code,name,market,publisher,category,base_date,base_point,list_date"
    for market in ("SSE", "SZSE", "CSI", "CICC", "SW", "OTH"):
        try:
            rows = client.call("index_basic", params={"market": market}, fields=fields)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Tushare index_basic skipped for %s: %s", market, exc)
            continue
        df = _normalize_tushare_index_basic_frame(rows)
        if not df.is_empty():
            frames.append(df)
    if not frames:
        return pl.DataFrame()
    return pl.concat(frames, how="diagonal_relaxed").unique(subset=["symbol"], keep="last").sort("symbol")


def upsert_index_instruments_from_tushare(repo: KlineRepository) -> int:
    df = fetch_index_basic()
    if df.is_empty():
        return 0
    existing = repo.get_index_instruments()
    if not existing.is_empty():
        df = pl.concat([existing, df], how="diagonal_relaxed").unique(subset=["symbol"], keep="last")
    repo.save_index_instruments(df)
    repo.refresh_index_views()
    return df.height


def fetch_index_daily_for_symbol(symbol: str, start_date: date, end_date: date) -> pl.DataFrame:
    rows = _client().call(
        "index_daily",
        params={
            "ts_code": symbol,
            "start_date": start_date.strftime("%Y%m%d"),
            "end_date": end_date.strftime("%Y%m%d"),
        },
        fields="ts_code,trade_date,open,high,low,close,vol,amount",
    )
    return _normalize_tushare_index_daily_frame(rows)


def import_tushare_index_daily(
    repo: KlineRepository,
    *,
    start_date: date | datetime | None = None,
    end_date: date | datetime | None = None,
    days: int = 365,
) -> dict[str, Any]:
    resolved_end_raw = end_date or date.today()
    resolved_start_raw = start_date or (date.today() - timedelta(days=max(30, min(5000, int(days or 365)))))
    resolved_end = resolved_end_raw.date() if isinstance(resolved_end_raw, datetime) else resolved_end_raw
    resolved_start = resolved_start_raw.date() if isinstance(resolved_start_raw, datetime) else resolved_start_raw

    inst_count = upsert_index_instruments_from_tushare(repo)
    instruments = repo.get_index_instruments()
    if instruments.is_empty() or "symbol" not in instruments.columns:
        return {"status": "empty", "index_count": inst_count, "rows_written": 0, "source": "tushare"}

    rows_written = 0
    for symbol in sorted(set(instruments["symbol"].to_list())):
        df = fetch_index_daily_for_symbol(symbol, resolved_start, resolved_end)
        if df.is_empty():
            continue
        repo.append_index_daily(df)
        repo.append_index_enriched(compute_enriched(df, factors=None, instruments=None))
        rows_written += df.height
    repo.refresh_index_views()
    return {
        "status": "ok" if rows_written else "empty",
        "index_count": inst_count,
        "rows_written": rows_written,
        "start_date": resolved_start.isoformat(),
        "end_date": resolved_end.isoformat(),
        "source": "tushare",
    }


def import_tushare_daily(
    repo: KlineRepository,
    *,
    start_date: date | None = None,
    end_date: date | None = None,
    days: int | None = 30,
    compute_enriched: bool = True,
    on_progress: ProgressCb | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    emit = on_progress or (lambda *args, **kwargs: None)
    cancel_requested = should_cancel or (lambda: False)
    resolved_end = end_date or date.today()
    resolved_start = start_date or (
        resolved_end - timedelta(days=max(1, min(5000, int(days or 30))) - 1)
    )
    if resolved_start > resolved_end:
        return {"status": "empty", "rows_written": 0}

    dates = _trade_dates(resolved_start, resolved_end)
    frames: list[pl.DataFrame] = []
    rows_written = 0
    symbols: set[str] = set()
    for idx, trade_date in enumerate(dates, start=1):
        if cancel_requested():
            raise RuntimeError("cancelled")
        pct = 5 + int(65 * idx / max(len(dates), 1))
        emit("tushare_import", pct, f"Tushare daily {idx}/{len(dates)} [{trade_date}]", stage_pct=int(100 * idx / max(len(dates), 1)), skip_log=True)
        df = fetch_daily_for_trade_date(trade_date)
        if df.is_empty():
            continue
        repo.append_daily(df)
        rows_written += df.height
        symbols.update(df["symbol"].unique().to_list())
        frames.append(df)

    emit("tushare_instruments", 75, "refresh instruments from Tushare")
    inst_rows = upsert_instruments_from_tushare(repo)
    repo.store._register_views()

    enriched_rows = 0
    if compute_enriched and rows_written:
        emit("tushare_enriched", 85, "compute enriched indicators")
        enriched_rows = run_pipeline(repo.store.data_dir, new_dates_only=True)
        repo.store._register_views()
        repo.refresh_cache()
    else:
        repo.clear_cache()
        repo.refresh_cache()

    min_date = min((df["date"].min() for df in frames), default=resolved_start)
    max_date = max((df["date"].max() for df in frames), default=resolved_end)
    emit("tushare_import", 100, "Tushare import complete")
    return {
        "status": "ok" if rows_written else "empty",
        "rows_written": rows_written,
        "symbols": len(symbols),
        "start_date": min_date.isoformat() if hasattr(min_date, "isoformat") else str(min_date),
        "end_date": max_date.isoformat() if hasattr(max_date, "isoformat") else str(max_date),
        "instrument_rows": inst_rows,
        "enriched_rows_written": enriched_rows,
    }
