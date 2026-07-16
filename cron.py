"""
ShamrockLeads — Background Automation Engine (cron.py)
Replaces Node-RED cron scheduler.
Optimized for high-concurrency with bulk MongoDB operations.
"""

import sys
import logging
import asyncio
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Add root to sys.path
sys.path.insert(0, str(Path(__file__).parent))

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from config.settings import settings
import httpx
from dashboard.extensions import get_collection

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s")
logger = logging.getLogger("shamrock-cron")

scheduler = AsyncIOScheduler()

async def hit_internal_endpoint(method: str, path: str, json_data: dict = None):
    """Hits an internal FastAPI endpoint."""
    url = f"http://localhost:5050{path}"  # Assuming internal port is 5050
    try:
        async with httpx.AsyncClient() as client:
            res = await client.request(method, url, json=json_data, timeout=30.0)
            res.raise_for_status()
            logger.info(f"Successfully triggered {method} {path}")
            return res.json()
    except Exception as e:
        logger.error(f"Failed to trigger {method} {path}: {e}")

async def run_bulk_maintenance():
    """
    Perform system-wide maintenance using bulk MongoDB operations.
    - Mark overdue tasks
    - Purge old logs
    """
    try:
        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()
        
        # 1. Bulk Mark Overdue Tasks
        tasks_col = get_collection("tasks")
        result = await tasks_col.update_many(
            {
                "status": {"$in": ["pending", "active"]},
                "due_date": {"$lt": now_iso}
            },
            {
                "$set": {
                    "status": "overdue",
                    "updated_at": now_iso
                },
                "$push": {
                    "history": {
                        "action": "status_change",
                        "from": "auto-cron",
                        "to": "overdue",
                        "timestamp": now_iso,
                        "notes": "Automatically marked overdue by system maintenance"
                    }
                }
            }
        )
        if result.modified_count > 0:
            logger.info(f"Maintenance: Marked {result.modified_count} tasks as overdue.")

        # 2. Bulk Purge Old Logs (older than 30 days)
        logs_col = get_collection("app_logs")
        cutoff = (now - timedelta(days=30)).isoformat()
        res_logs = await logs_col.delete_many({"timestamp": {"$lt": cutoff}})
        if res_logs.deleted_count > 0:
            logger.info(f"Maintenance: Purged {res_logs.deleted_count} old log entries.")

    except Exception as e:
        logger.error(f"Maintenance: Bulk maintenance cycle failed: {e}")

# ─────────────────────────────────────────────────────────────────────────────
# 1. Daily Jobs (CronTrigger)
# ─────────────────────────────────────────────────────────────────────────────
def register_daily_jobs():
    # 5:00 AM ET - Scout (New County Scanning)
    scheduler.add_job(
        lambda: asyncio.create_task(hit_internal_endpoint("POST", "/api/scraper-control/run-scout")),
        trigger=CronTrigger(hour=5, minute=0, timezone="America/New_York"),
        id="daily_scout_scan",
        name="County Arrest Scanning (Scout)",
        replace_existing=True
    )
    
    # 7:00 AM ET - Morning Ops Briefing
    scheduler.add_job(
        lambda: asyncio.create_task(hit_internal_endpoint("POST", "/api/reports/morning-briefing")),
        trigger=CronTrigger(hour=7, minute=0, timezone="America/New_York"),
        id="daily_morning_briefing",
        name="Morning Ops Briefing",
        replace_existing=True
    )

    # 9:00 AM ET - Court Date Reminders
    scheduler.add_job(
        lambda: asyncio.create_task(hit_internal_endpoint("POST", "/api/court-reminders/trigger-daily")),
        trigger=CronTrigger(hour=9, minute=0, timezone="America/New_York"),
        id="daily_court_reminders",
        name="Court Date Reminders",
        replace_existing=True
    )

    # 11:00 AM ET - Defendant Check-Ins
    scheduler.add_job(
        lambda: asyncio.create_task(hit_internal_endpoint("POST", "/api/imessage-automation/trigger-checkins")),
        trigger=CronTrigger(hour=11, minute=0, timezone="America/New_York"),
        id="daily_checkins",
        name="Defendant Check-Ins",
        replace_existing=True
    )
    
    # 6:00 PM ET - Revenue Snapshot
    scheduler.add_job(
        lambda: asyncio.create_task(hit_internal_endpoint("POST", "/api/reports/revenue-snapshot")),
        trigger=CronTrigger(hour=18, minute=0, timezone="America/New_York"),
        id="daily_revenue_snapshot",
        name="Revenue Snapshot",
        replace_existing=True
    )

    # 2:00 AM ET - Bulk Maintenance
    scheduler.add_job(
        run_bulk_maintenance,
        trigger=CronTrigger(hour=2, minute=0, timezone="America/New_York"),
        id="daily_maintenance",
        name="Bulk System Maintenance",
        replace_existing=True
    )

# ─────────────────────────────────────────────────────────────────────────────
# 2. Recurring Interval Jobs (IntervalTrigger)
# ─────────────────────────────────────────────────────────────────────────────
def register_interval_jobs():
    # 30 min - Court Date Monitoring / Updates
    scheduler.add_job(
        lambda: asyncio.create_task(hit_internal_endpoint("POST", "/api/court-dockets/sync")),
        trigger=IntervalTrigger(minutes=30),
        id="interval_court_sync",
        name="Court Date Monitoring",
        replace_existing=True
    )

    # 30 min - Follow-up Lead Processing (The Closer)
    scheduler.add_job(
        lambda: asyncio.create_task(hit_internal_endpoint("POST", "/api/outreach/process-abandoned-leads")),
        trigger=IntervalTrigger(minutes=30),
        id="interval_abandoned_leads",
        name="Follow-Up Lead Processing",
        replace_existing=True
    )

    # 1 hr - Bounty Hunter Scan
    scheduler.add_job(
        lambda: asyncio.create_task(hit_internal_endpoint("POST", "/api/leads/bounty-hunter-scan")),
        trigger=IntervalTrigger(hours=1),
        id="interval_bounty_scan",
        name="Bounty Hunter Scan",
        replace_existing=True
    )

    # 1 hr - No-Show Escalation
    scheduler.add_job(
        lambda: asyncio.create_task(hit_internal_endpoint("POST", "/api/fta/no-show-escalation")),
        trigger=IntervalTrigger(hours=1),
        id="interval_fta_escalation",
        name="No-Show Escalation Check",
        replace_existing=True
    )

def main():
    logger.info("Initializing Shamrock Cron Engine...")
    register_daily_jobs()
    register_interval_jobs()
    
    scheduler.start()
    logger.info("Cron Engine started successfully. Awaiting jobs...")
    
    try:
        asyncio.get_event_loop().run_forever()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Shutting down Cron Engine...")
        scheduler.shutdown()

if __name__ == "__main__":
    main()
