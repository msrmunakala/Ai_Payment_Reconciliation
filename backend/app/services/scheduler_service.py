"""Automated reconciliation scheduling with configurable run frequency.

The project brief lists "automated reconciliation scheduling with configurable
run frequencies" as a deliverable. Nothing existed: there was no scheduler, no
background job, and no cron integration anywhere in the backend. Reconciliation
only ever ran when a human posted to ``/reconciliation/run`` or when a webhook
arrived (which re-reconciled the entire database synchronously).

This module adds an in-process APScheduler job that can be started, stopped,
paused and re-scheduled at runtime, and that optionally pulls from Mojaloop and
refreshes the forecast as part of each cycle.

Scope and limits, stated plainly: this is a single-process scheduler. It is the
right tool for one API instance, a demo, or a pilot. If the API is ever scaled to
multiple replicas, every replica would run its own copy of the job, so the job
would need to move to a dedicated worker with a shared broker (Celery or ARQ on
Redis) and a distributed lock. The ``_run_lock`` and ``max_instances=1`` guard
below prevent overlap *within* a process only.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime
from typing import Any, Dict, Optional

from app.core.config import settings
from app.database.db import session_scope

logger = logging.getLogger(__name__)

JOB_ID = "automated-reconciliation"

# Serialises runs inside this process. APScheduler's max_instances guards the
# scheduled path; this also covers a manual trigger landing on top of a
# scheduled one.
_run_lock = threading.Lock()


class SchedulerUnavailableError(RuntimeError):
    """Raised when APScheduler is not installed."""


def run_scheduled_cycle(
    trigger: str = "scheduled",
    sync_mojaloop: Optional[bool] = None,
    refresh_forecast: Optional[bool] = None,
) -> Dict[str, Any]:
    """One reconciliation cycle: optional hub pull, reconcile, refresh forecast.

    Safe to call directly (the API exposes it as "run now"). Skips immediately if
    another cycle is already in flight rather than queueing up overlapping work.
    """
    config = settings.scheduler
    do_sync = config.sync_mojaloop if sync_mojaloop is None else sync_mojaloop
    do_forecast = config.refresh_forecast if refresh_forecast is None else refresh_forecast

    if not _run_lock.acquire(blocking=False):
        logger.warning("Skipping %s cycle: a reconciliation cycle is already running", trigger)
        return {
            "skipped": True,
            "reason": "another reconciliation cycle is already running",
            "trigger": trigger,
        }

    started = datetime.utcnow()
    outcome: Dict[str, Any] = {
        "skipped": False,
        "trigger": trigger,
        "started_at": started.isoformat(),
    }

    try:
        # Imported lazily to keep module import cheap and avoid cycles.
        from app.services.reconciliation_service import run_reconciliation
        from app.services import settlement_service

        with session_scope() as db:
            if do_sync:
                # Pull settlement windows, not transfers: transfers arrive by
                # push on the webhook, and FSPIOP has no bulk transfer endpoint.
                try:
                    outcome["settlement_sync"] = settlement_service.sync_windows(db)
                except Exception as exc:
                    logger.exception("Scheduled settlement sync failed")
                    outcome["settlement_sync_error"] = str(exc)[:500]

            # Prefer reconciling newly closed settlement windows, which is the
            # correct unit of work. Fall back to a full run when no window is
            # pending (for example when the hub integration is disabled).
            pending = []
            try:
                pending = settlement_service.pending_windows(db)
            except Exception:  # pragma: no cover - table may not exist yet
                logger.debug("Could not read pending settlement windows", exc_info=True)

            if pending:
                outcome["mode"] = "settlement-window"
                outcome["windows"] = settlement_service.reconcile_pending_windows(
                    db, refresh_forecast=do_forecast
                )
            else:
                outcome["mode"] = "full"
                outcome["reconciliation"] = run_reconciliation(
                    db, trigger=trigger, refresh_forecast=do_forecast
                )

        finished = datetime.utcnow()
        outcome["finished_at"] = finished.isoformat()
        outcome["duration_seconds"] = round((finished - started).total_seconds(), 3)
        logger.info(
            "Scheduled cycle complete in %.2fs (trigger=%s)",
            outcome["duration_seconds"],
            trigger,
        )
        return outcome

    except Exception as exc:
        logger.exception("Scheduled reconciliation cycle failed")
        outcome["error"] = str(exc)[:1000]
        outcome["finished_at"] = datetime.utcnow().isoformat()
        return outcome
    finally:
        _run_lock.release()


class SchedulerManager:
    """Lifecycle wrapper around a single APScheduler background scheduler."""

    def __init__(self) -> None:
        self._scheduler = None
        self._last_result: Optional[Dict[str, Any]] = None
        self._listener_attached = False

    # -- construction ----------------------------------------------------

    def _ensure_scheduler(self):
        if self._scheduler is not None:
            return self._scheduler

        try:
            from apscheduler.schedulers.background import BackgroundScheduler
        except ImportError as exc:  # pragma: no cover
            raise SchedulerUnavailableError(
                "APScheduler is not installed. Install it with "
                "'pip install APScheduler' to enable automated scheduling."
            ) from exc

        self._scheduler = BackgroundScheduler(
            timezone="UTC",
            job_defaults={
                "coalesce": True,  # collapse missed runs into one
                "max_instances": settings.scheduler.max_instances,
                "misfire_grace_time": settings.scheduler.misfire_grace_seconds,
            },
        )
        return self._scheduler

    def _job(self) -> None:
        self._last_result = run_scheduled_cycle(trigger="scheduled")

    # -- lifecycle -------------------------------------------------------

    def start(self, interval_minutes: Optional[int] = None) -> Dict[str, Any]:
        """Start the scheduler and register the reconciliation job."""
        from apscheduler.triggers.interval import IntervalTrigger

        scheduler = self._ensure_scheduler()
        minutes = int(interval_minutes or settings.scheduler.interval_minutes)
        if minutes < 1:
            raise ValueError("interval_minutes must be at least 1")

        if not scheduler.running:
            scheduler.start()
            logger.info("Scheduler started")

        scheduler.add_job(
            self._job,
            trigger=IntervalTrigger(minutes=minutes),
            id=JOB_ID,
            name="Automated reconciliation",
            replace_existing=True,
        )
        logger.info("Automated reconciliation scheduled every %d minute(s)", minutes)
        return self.status()

    def shutdown(self, wait: bool = False) -> Dict[str, Any]:
        """Stop the scheduler, leaving any in-flight cycle to finish."""
        if self._scheduler is not None and self._scheduler.running:
            self._scheduler.shutdown(wait=wait)
            logger.info("Scheduler stopped")
        return {"running": False, "job": None}

    def pause(self) -> Dict[str, Any]:
        job = self._get_job()
        if job is not None:
            job.pause()
            logger.info("Automated reconciliation paused")
        return self.status()

    def resume(self) -> Dict[str, Any]:
        job = self._get_job()
        if job is not None:
            job.resume()
            logger.info("Automated reconciliation resumed")
        return self.status()

    def reschedule(self, interval_minutes: int) -> Dict[str, Any]:
        """Change the run frequency without restarting the process."""
        minutes = int(interval_minutes)
        if minutes < 1:
            raise ValueError("interval_minutes must be at least 1")

        scheduler = self._ensure_scheduler()
        if not scheduler.running or self._get_job() is None:
            return self.start(interval_minutes=minutes)

        from apscheduler.triggers.interval import IntervalTrigger

        scheduler.reschedule_job(JOB_ID, trigger=IntervalTrigger(minutes=minutes))
        logger.info("Automated reconciliation rescheduled to every %d minute(s)", minutes)
        return self.status()

    # -- introspection ---------------------------------------------------

    def _get_job(self):
        if self._scheduler is None:
            return None
        try:
            return self._scheduler.get_job(JOB_ID)
        except Exception:  # pragma: no cover - scheduler not started
            return None

    @property
    def available(self) -> bool:
        try:
            import apscheduler  # noqa: F401

            return True
        except ImportError:
            return False

    def status(self) -> Dict[str, Any]:
        """Current scheduler and job state, plus the configured defaults."""
        job = self._get_job()
        running = bool(self._scheduler is not None and self._scheduler.running)

        interval_minutes = None
        next_run = None
        paused = False
        if job is not None:
            trigger = getattr(job, "trigger", None)
            interval = getattr(trigger, "interval", None)
            if interval is not None:
                interval_minutes = round(interval.total_seconds() / 60, 2)
            next_run_time = getattr(job, "next_run_time", None)
            next_run = next_run_time.isoformat() if next_run_time else None
            paused = next_run_time is None

        return {
            "available": self.available,
            "running": running,
            "job_registered": job is not None,
            "paused": paused,
            "interval_minutes": interval_minutes,
            "next_run_time": next_run,
            "configured": {
                "enabled_on_startup": settings.scheduler.enabled,
                "interval_minutes": settings.scheduler.interval_minutes,
                "sync_mojaloop": settings.scheduler.sync_mojaloop,
                "refresh_forecast": settings.scheduler.refresh_forecast,
                "max_instances": settings.scheduler.max_instances,
                "misfire_grace_seconds": settings.scheduler.misfire_grace_seconds,
            },
            "cycle_in_progress": _run_lock.locked(),
            "last_result": self._summarise_last_result(),
            "note": (
                "Single-process scheduler. Move to a dedicated worker with a shared "
                "broker before running multiple API replicas."
            ),
        }

    def _summarise_last_result(self) -> Optional[Dict[str, Any]]:
        result = self._last_result
        if not result:
            return None
        summary: Dict[str, Any] = {
            "trigger": result.get("trigger"),
            "started_at": result.get("started_at"),
            "finished_at": result.get("finished_at"),
            "duration_seconds": result.get("duration_seconds"),
            "skipped": result.get("skipped"),
        }
        if "error" in result:
            summary["error"] = result["error"]
        reconciliation = result.get("reconciliation")
        if isinstance(reconciliation, dict):
            summary["matched"] = reconciliation.get("matched_count")
            summary["total"] = reconciliation.get("total_reconciled")
            summary["anomalies"] = reconciliation.get("anomalies_detected")
            summary["run_id"] = reconciliation.get("run_id")
        return summary

    def record_manual_result(self, result: Dict[str, Any]) -> None:
        self._last_result = result


scheduler_manager = SchedulerManager()


def start_if_enabled() -> Optional[Dict[str, Any]]:
    """Start the scheduler at application startup when configured to do so."""
    if not settings.scheduler.enabled:
        logger.info("Automated scheduling disabled (SCHEDULER_ENABLED=false)")
        return None
    try:
        return scheduler_manager.start()
    except Exception:
        logger.exception("Could not start the scheduler; continuing without it")
        return None


def shutdown() -> None:
    try:
        scheduler_manager.shutdown()
    except Exception:  # pragma: no cover
        logger.exception("Error while shutting down the scheduler")


__all__ = [
    "JOB_ID",
    "SchedulerManager",
    "SchedulerUnavailableError",
    "run_scheduled_cycle",
    "scheduler_manager",
    "shutdown",
    "start_if_enabled",
]
