from __future__ import annotations

from datetime import date

import polars as pl

from app.indicators.pipeline import compute_enriched


def test_cumulative_tushare_adj_factor_does_not_null_adjusted_prices():
    raw = pl.DataFrame([
        {"symbol": "000001.SZ", "date": date(2026, 6, 25), "open": 10.0, "high": 10.5, "low": 9.9, "close": 10.2, "volume": 1000.0, "amount": 10000.0},
        {"symbol": "000001.SZ", "date": date(2026, 6, 26), "open": 10.4, "high": 10.5, "low": 10.1, "close": 10.3, "volume": 1200.0, "amount": 12000.0},
    ])
    factors = pl.DataFrame([
        {"symbol": "000001.SZ", "trade_date": date(2026, 6, 25), "ex_factor": 110.0},
        {"symbol": "000001.SZ", "trade_date": date(2026, 6, 26), "ex_factor": 111.0},
    ])
    instruments = pl.DataFrame([
        {"symbol": "000001.SZ", "name": "Ping An Bank", "float_shares": 1000000.0},
    ])

    enriched = compute_enriched(raw, factors=factors, instruments=instruments)

    assert enriched.height == 2
    assert enriched.select(pl.col("open").null_count()).item() == 0
    assert enriched.select(pl.col("close").null_count()).item() == 0
    assert enriched.filter(pl.col("close").is_nan()).height == 0


def test_limit_price_tolerates_missing_previous_close():
    raw = pl.DataFrame([
        {"symbol": "000002.SZ", "date": date(2026, 6, 26), "open": 3.0, "high": 3.1, "low": 2.9, "close": 3.0, "volume": 1000.0, "amount": 3000.0},
    ])
    instruments = pl.DataFrame([
        {"symbol": "000002.SZ", "name": "Vanke A", "float_shares": 1000000.0},
    ])

    enriched = compute_enriched(raw, factors=None, instruments=instruments)

    assert enriched.height == 1
    assert enriched["signal_limit_up"][0] is None
    assert enriched["consecutive_limit_ups"][0] == 0
