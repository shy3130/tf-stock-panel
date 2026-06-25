"""财务数据 API — 独立路由, Cap.FINANCIAL 门控。"""
from __future__ import annotations

import logging

import polars as pl
from fastapi import APIRouter, HTTPException, Request

from app.services.financial_sync import get_financial_df
from app.tickflow.capabilities import Cap

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/financials", tags=["financials"])


@router.get("/status")
def financial_status(request: Request):
    """返回各财务表的同步状态。无需 FINANCIAL 权限（前端根据 available 决定是否展示）。"""
    capset = request.app.state.capabilities
    if not capset.has(Cap.FINANCIAL):
        return {"available": False, "tables": {}}

    data_dir = request.app.state.repo.store.data_dir
    tables = {}

    for table in ("metrics", "income", "balance_sheet", "cash_flow"):
        path = data_dir / "financials" / table / "part.parquet"
        if path.exists():
            try:
                df = pl.read_parquet(path, columns=["symbol"])
                tables[table] = {
                    "rows": len(df),
                    "symbols": df["symbol"].n_unique() if not df.is_empty() else 0,
                }
            except Exception:
                tables[table] = {"rows": 0, "symbols": 0}
        else:
            tables[table] = {"rows": 0, "symbols": 0}

    fs = getattr(request.app.state, "financial_scheduler", None)
    last_sync = fs.last_sync if fs else {}

    return {
        "available": True,
        "tables": tables,
        "last_sync": last_sync,
        # 服务端是否正在同步(手动触发)——前端据此显示"同步中"并防重复点击,
        # 且刷新页面后仍能正确反映服务端状态。
        "syncing": bool(fs and fs.is_syncing),
    }


@router.get("/metrics")
def get_metrics(request: Request, symbol: str | None = None):
    """查询核心财务指标。"""
    capset = request.app.state.capabilities
    capset.require(Cap.FINANCIAL)

    df = get_financial_df(request.app.state.repo.store.data_dir, "metrics")
    if df.is_empty():
        return {"data": []}
    if symbol:
        df = df.filter(pl.col("symbol") == symbol)
    return {"data": df.to_dicts()}


@router.get("/income")
def get_income(request: Request, symbol: str | None = None):
    """查询利润表。"""
    capset = request.app.state.capabilities
    capset.require(Cap.FINANCIAL)

    df = get_financial_df(request.app.state.repo.store.data_dir, "income")
    if df.is_empty():
        return {"data": []}
    if symbol:
        df = df.filter(pl.col("symbol") == symbol)
    return {"data": df.to_dicts()}


@router.get("/balance-sheet")
def get_balance_sheet(request: Request, symbol: str | None = None):
    """查询资产负债表。"""
    capset = request.app.state.capabilities
    capset.require(Cap.FINANCIAL)

    df = get_financial_df(request.app.state.repo.store.data_dir, "balance_sheet")
    if df.is_empty():
        return {"data": []}
    if symbol:
        df = df.filter(pl.col("symbol") == symbol)
    return {"data": df.to_dicts()}


@router.get("/cash-flow")
def get_cash_flow(request: Request, symbol: str | None = None):
    """查询现金流量表。"""
    capset = request.app.state.capabilities
    capset.require(Cap.FINANCIAL)

    df = get_financial_df(request.app.state.repo.store.data_dir, "cash_flow")
    if df.is_empty():
        return {"data": []}
    if symbol:
        df = df.filter(pl.col("symbol") == symbol)
    return {"data": df.to_dicts()}


@router.post("/sync/{table}")
def sync_table(request: Request, table: str):
    """手动触发同步(立即返回,后台异步执行)。

    table: metrics / income / balance_sheet / cash_flow / all
    同步在后台线程执行,全量同步需数分钟。本接口立即返回 started 状态,
    前端通过轮询 GET /status 的 syncing 字段观察进度。
    """
    capset = request.app.state.capabilities
    capset.require(Cap.FINANCIAL)

    valid_tables = {"metrics", "income", "balance_sheet", "cash_flow", "all"}
    if table not in valid_tables:
        raise HTTPException(400, f"invalid table: {table}, expected one of {valid_tables}")

    fs = getattr(request.app.state, "financial_scheduler", None)
    if not fs:
        return {"status": "error", "message": "FinancialScheduler not available"}

    target = None if table == "all" else table
    result = fs.trigger(target)

    return {"status": "ok", "synced": result}
