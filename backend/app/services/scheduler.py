"""APScheduler jobs for automated reconciliation."""

import logging
from datetime import datetime, timezone

from apscheduler.schedulers.background import BackgroundScheduler

from app.database.db import SessionLocal
from app.models.reconciliation import AutomatedReconciliationRun
from app.services.reconciliation_service import run_reconciliation

logger = logging.getLogger(__name__)
JOB_ID = "automated-reconciliation"
scheduler = BackgroundScheduler(timezone="UTC")


def run_automated_reconciliation(trigger: str = "scheduled"):
    """Run matching in its own session and persist a success/failure audit row."""
    db = SessionLocal()
    run = AutomatedReconciliationRun(trigger=trigger, status="running")
    run_id = None
    try:
        db.add(run)
        db.commit()
        db.refresh(run)
        run_id = run.id
        result = run_reconciliation(db)
        run.status = "success"
        run.total_reconciled = result.get("total_reconciled", 0)
        run.matched_count = result.get("matched_count", 0)
        run.anomalies_detected = result.get("anomalies_detected", 0)
        run.message = result.get("message", "Automated reconciliation completed.")
        return result
    except Exception as exc:
        db.rollback()
        logger.exception("Automated reconciliation failed")
        if run_id is not None:
            run = db.get(AutomatedReconciliationRun, run_id)
        if run is not None:
            run.status = "failed"
            run.message = str(exc)[:4000]
        return {"status": "failed", "message": str(exc)}
    finally:
        if run is not None:
            run.finished_at = datetime.now(timezone.utc).replace(tzinfo=None)
            try:
                db.add(run)
                db.commit()
            except Exception:
                db.rollback()
                logger.exception("Could not save automated reconciliation audit result")
        db.close()


def configure_daily_job(hour: int = 0, minute: int = 0, timezone_name: str = "UTC"):
    if not 0 <= int(hour) <= 23 or not 0 <= int(minute) <= 59:
        raise ValueError("Daily schedule hour must be 0-23 and minute must be 0-59.")
    scheduler.add_job(
        run_automated_reconciliation,
        trigger="cron",
        id=JOB_ID,
        args=["daily"],
        hour=int(hour),
        minute=int(minute),
        timezone=timezone_name,
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )
    return get_scheduler_status()


def configure_interval_job(hours: int):
    hours = int(hours)
    if hours < 1 or hours > 720:
        raise ValueError("Interval must be between 1 and 720 hours.")
    scheduler.add_job(
        run_automated_reconciliation,
        trigger="interval",
        id=JOB_ID,
        args=[f"interval:{hours}h"],
        hours=hours,
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )
    return get_scheduler_status()


def start():
    if scheduler.running:
        return get_scheduler_status()
    if scheduler.get_job(JOB_ID) is None:
        configure_daily_job()
    scheduler.start()
    return get_scheduler_status()


def shutdown(wait: bool = True):
    if scheduler.running:
        scheduler.shutdown(wait=wait)


def get_scheduler_status():
    job = scheduler.get_job(JOB_ID)
    next_run = None
    if job is not None:
        try:
            next_run = job.next_run_time.isoformat() if job.next_run_time else None
        except AttributeError:
            next_run = None
    return {
        "running": scheduler.running,
        "job_id": JOB_ID if job else None,
        "trigger": str(job.trigger) if job else None,
        "next_run_at": next_run,
    }
