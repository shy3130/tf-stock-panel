from __future__ import annotations

from datetime import date

from app.services.tushare_import import (
    _normalize_tushare_basic_frame,
    _normalize_tushare_daily_frame,
    _normalize_tushare_index_daily_frame,
)


def test_tushare_daily_normalizes_to_tickflow_canonical_schema():
    df = _normalize_tushare_daily_frame(
        [
            {
                "ts_code": "000001.SZ",
                "trade_date": "20260626",
                "open": "10.1",
                "high": "10.8",
                "low": "9.9",
                "close": "10.5",
                "vol": "12345.6",
                "amount": "7890.12",
            }
        ],
        [
            {
                "ts_code": "000001.SZ",
                "trade_date": "20260626",
                "turnover_rate": "1.23",
            }
        ],
    )

    assert df.columns == [
        "symbol",
        "date",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "amount",
        "turnover_rate",
    ]
    row = df.row(0, named=True)
    assert row["symbol"] == "000001.SZ"
    assert row["date"] == date(2026, 6, 26)
    assert row["volume"] == 12345.6
    assert row["amount"] == 7890120.0
    assert row["turnover_rate"] == 1.23


def test_tushare_basic_units_match_tickflow_instruments():
    df = _normalize_tushare_basic_frame(
        [
            {
                "ts_code": "000001.SZ",
                "symbol": "000001",
                "name": "平安银行",
                "area": "深圳",
                "industry": "银行",
                "market": "主板",
                "list_date": "19910403",
                "total_share": "1940591.82",
                "float_share": "1940575.45",
                "total_mv": "20000000.5",
                "circ_mv": "19900000.25",
                "pe_ttm": "5.6",
                "pb": "0.7",
            }
        ]
    )

    row = df.row(0, named=True)
    assert row["symbol"] == "000001.SZ"
    assert row["code"] == "000001"
    assert row["exchange"] == "SZ"
    assert row["region"] == "CN"
    assert row["type"] == "stock"
    assert row["total_shares"] == 19405918200.0
    assert row["float_shares"] == 19405754500.0
    assert row["total_market_cap"] == 200000005000.0
    assert row["float_market_cap"] == 199000002500.0
    assert row["pe_ttm"] == 5.6
    assert row["pb"] == 0.7


def test_tushare_index_daily_normalizes_to_index_storage_schema():
    df = _normalize_tushare_index_daily_frame([
        {
            "ts_code": "000001.SH",
            "trade_date": "20260626",
            "open": "3080.0",
            "high": "3090.0",
            "low": "3070.0",
            "close": "3085.0",
            "vol": "1000000",
            "amount": "123456.7",
        }
    ])

    assert df.columns == ["symbol", "date", "open", "high", "low", "close", "volume", "amount"]
    row = df.row(0, named=True)
    assert row["symbol"] == "000001.SH"
    assert row["date"] == date(2026, 6, 26)
    assert row["volume"] == 1000000.0
    assert row["amount"] == 123456700.0
