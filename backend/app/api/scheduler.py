"""Scheduling control endpoints.

Exposes the configurable run frequency the brief asks for, so an operator can
change the cadence without redeploying.
"""

from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.concurrency import run_in_threadpool

from app.services.scheduler_service import (
    SchedulerUnavailableError,
    run_scheduled_cycle,
    scheduler_manager,
)

router = APIRouter(prefix="/scheduler", tags=["Scheduling"])


@router.get("/status")
def scheduler_status():
    """Current state, next run time and configured defaults."""
    return scheduler_manager.status()


@router.post("/start")
def start_scheduler(
    interval_minutes: Optional[int] = Query(
        None, ge=1, le=1440, description="Run frequency in minutes (1 to 1440)"
    )
):
    try:
        return scheduler_manager.start(interval_minutes=interval_minutes)
    except SchedulerUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/stop")
def stop_scheduler():
    return scheduler_manager.shutdown()


@router.post("/pause")
def pause_scheduler():
    return scheduler_manager.pause()


@router.post("/resume")
def resume_scheduler():
    return scheduler_manager.resume()


@router.post("/interval")
def set_interval(
    interval_minutes: int = Query(..., ge=1, le=1440, description="New run frequency in minutes")
):
    """Change the run frequency at runtime."""
    try:
        return scheduler_manager.reschedule(interval_minutes)
    except SchedulerUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/run-now")
async def run_now(
    sync_mojaloop: Optional[bool] = Query(None, description="Pull from the hub first"),
    refresh_forecast: Optional[bool] = Query(None, description="Refresh the cash-flow forecast"),
):
    """Execute one cycle immediately, without waiting for the next tick.

    Run in a worker thread because the cycle is CPU-bound and synchronous; doing
    it inline would block the event loop for the duration of the run.
    """
    result = await run_in_threadpool(
        run_scheduled_cycle,
        trigger="manual-now",
        sync_mojaloop=sync_mojaloop,
        refresh_forecast=refresh_forecast,
    )
    scheduler_manager.record_manual_result(result)
    return result
