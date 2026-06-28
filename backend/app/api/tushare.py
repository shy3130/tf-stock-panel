"""Tushare configuration and import APIs."""
from __future__ import annotations

import asyncio
import concurrent.futures as _cf
from datetime import date
import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app import secrets_store
from app.api.data import invalidate_storage_cache
from app.services.pipeline_jobs import job_store
from app.services.ext_pull import pull_scheduler
from app.services.tushare_import import import_tushare_adj_factor, import_tushare_daily, tushare_status, upsert_instruments_from_tushare

router = APIRouter(prefix="/api/tushare", tags=["tushare"])
logger = logging.getLogger(__name__)
_tushare_executor = _cf.ThreadPoolExecutor(max_workers=1, thread_name_prefix="tushare")


def _refresh_tushare_consumers(request: Request | None = None) -> None:
    invalidate_storage_cache()
    if request is not None and hasattr(request.app.state, "repo"):
        pull_scheduler.refresh(request.app.state.repo.store.data_dir)


class TushareTokenIn(BaseModel):
    token: str


class TushareHttpUrlIn(BaseModel):
    url: str


class ImportDailyRequest(BaseModel):
    start_date: date | None = None
    end_date: date | None = None
    days: int | None = Field(default=30, ge=1, le=5000)
    compute_enriched: bool = True


class ImportAdjFactorRequest(BaseModel):
    start_date: date | None = None
    end_date: date | None = None
    days: int | None = Field(default=5000, ge=1, le=5000)


@router.get("/status")
def status() -> dict:
    return tushare_status()


@router.post("/token")
def save_token(req: TushareTokenIn, request: Request) -> dict:
    token = req.token.strip()
    if not token:
        raise HTTPException(status_code=400, detail="Tushare token is empty")
    secrets_store.save({"tushare_token": token})
    _refresh_tushare_consumers(request)
    return tushare_status()


@router.post("/http-url")
def save_http_url(req: TushareHttpUrlIn, request: Request) -> dict:
    url = req.url.strip()
    if not url:
        raise HTTPException(status_code=400, detail="Tushare HTTP URL is empty")
    secrets_store.save({"tushare_http_url": secrets_store._normalize_http_url(url)})
    _refresh_tushare_consumers(request)
    return tushare_status()


@router.delete("/http-url")
def clear_http_url(request: Request) -> dict:
    secrets_store.save({"tushare_http_url": "https://tt.xiaodefa.cn"})
    _refresh_tushare_consumers(request)
    return tushare_status()


@router.delete("/token")
def clear_token(request: Request) -> dict:
    secrets_store.save({"tushare_token": ""})
    _refresh_tushare_consumers(request)
    return tushare_status()


@router.post("/import/instruments")
def import_instruments(request: Request) -> dict:
    try:
        rows = upsert_instruments_from_tushare(request.app.state.repo)
        request.app.state.repo.store._register_views()
        request.app.state.repo.clear_cache()
        request.app.state.repo.refresh_cache()
        invalidate_storage_cache()
        return {"status": "ok", "rows_written": rows}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/import/daily")
def import_daily(body: ImportDailyRequest, request: Request) -> dict:
    try:
        result = import_tushare_daily(
            request.app.state.repo,
            start_date=body.start_date,
            end_date=body.end_date,
            days=body.days,
            compute_enriched=body.compute_enriched,
        )
        invalidate_storage_cache()
        return result
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/import/adj-factor")
def import_adj_factor(body: ImportAdjFactorRequest, request: Request) -> dict:
    try:
        result = import_tushare_adj_factor(
            request.app.state.repo,
            start_date=body.start_date,
            end_date=body.end_date,
            days=body.days,
        )
        invalidate_storage_cache()
        return result
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/import/daily/job")
async def import_daily_job(body: ImportDailyRequest, request: Request) -> dict:
    existing_id = job_store.active_id()
    if existing_id:
        existing = job_store.get(existing_id)
        if existing and existing["status"] in {"pending", "running"}:
            return {"status": "reused", "job_id": existing_id}

    repo = request.app.state.repo
    job_id = job_store.create()

    async def task() -> None:
        job_store.start(job_id)
        loop = asyncio.get_event_loop()

        def progress(
            stage: str,
            pct: int,
            msg: str,
            stage_pct: int | None = None,
            skip_log: bool = False,
        ) -> None:
            job_store.progress(job_id, stage, pct, msg, stage_pct=stage_pct, skip_log=skip_log)

        try:
            progress("tushare_import", 1, "start Tushare import")

            def should_cancel() -> bool:
                current = job_store.get(job_id)
                return not current or current.get("status") != "running"

            result = await loop.run_in_executor(
                _tushare_executor,
                lambda: import_tushare_daily(
                    repo,
                    start_date=body.start_date,
                    end_date=body.end_date,
                    days=body.days,
                    compute_enriched=body.compute_enriched,
                    on_progress=progress,
                    should_cancel=should_cancel,
                ),
            )
            job_store.succeed(job_id, result)
            invalidate_storage_cache()
        except Exception as exc:  # noqa: BLE001
            logger.exception("Tushare import job failed")
            job_store.fail(job_id, str(exc))
            invalidate_storage_cache()

    asyncio.create_task(task())
    return {"status": "started", "job_id": job_id}
