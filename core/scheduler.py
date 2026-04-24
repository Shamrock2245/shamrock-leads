"""
APScheduler Job Management for ShamrockLeads.

Manages the cron-like scheduling of county scrapers.
Each county runs on its own configurable interval.
"""

import logging
from typing import Dict, List, Optional
from datetime import datetime, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_EXECUTED
from datetime import timedelta

from scrapers.base_scraper import BaseScraper
from config.settings import settings

logger = logging.getLogger(__name__)

# Stagger delay between first-run starts (seconds per county)
STAGGER_SECONDS = 15


class ScraperScheduler:
    """
    Manages scheduled scraper execution via APScheduler.

    Features:
    - Per-county interval configuration
    - Max concurrent job limits
    - Staggered first-run on startup (fires immediately, not after first interval)
    - Job execution logging
    - Health status endpoint data
    """

    def __init__(self, max_workers: int = None):
        self.max_workers = max_workers or settings.MAX_CONCURRENT
        self.scheduler = BackgroundScheduler(
            executors={
                "default": {
                    "type": "threadpool",
                    "max_workers": self.max_workers,
                }
            },
            job_defaults={
                "coalesce": True,
                "max_instances": 1,
                "misfire_grace_time": 300,
            },
        )
        self._scrapers: Dict[str, BaseScraper] = {}
        self._writers: list = []
        self._job_history: List[Dict] = []
        self._registration_count: int = 0

        self.scheduler.add_listener(
            self._on_job_executed, EVENT_JOB_EXECUTED
        )
        self.scheduler.add_listener(
            self._on_job_error, EVENT_JOB_ERROR
        )

    def register_scraper(
        self,
        scraper: BaseScraper,
        interval_minutes: int = None,
    ):
        """Register a county scraper with its schedule.

        Jobs fire immediately on startup (staggered by STAGGER_SECONDS per county)
        then repeat every interval_minutes thereafter.
        """
        interval = interval_minutes or settings.DEFAULT_INTERVAL_MINUTES
        job_id = scraper.scraper_id

        self._scrapers[job_id] = scraper

        # Stagger first run: county 0 starts at +10s, county 1 at +25s, etc.
        stagger_offset = 10 + (self._registration_count * STAGGER_SECONDS)
        first_run = datetime.now(timezone.utc) + timedelta(seconds=stagger_offset)
        self._registration_count += 1

        self.scheduler.add_job(
            self._run_scraper,
            trigger=IntervalTrigger(minutes=interval),
            id=job_id,
            name=f"{scraper.county} County Scraper",
            args=[job_id],
            replace_existing=True,
            next_run_time=first_run,
        )

        logger.info(
            f"📋 Registered {scraper.county} scraper "
            f"(every {interval} min, first run in {stagger_offset}s, job_id={job_id})"
        )

    def set_writers(self, writers: list):
        """Set the writer instances for all scrapers."""
        self._writers = writers

    def _run_scraper(self, job_id: str):
        """Execute a scraper job."""
        scraper = self._scrapers.get(job_id)
        if not scraper:
            logger.error(f"❌ Unknown scraper job: {job_id}")
            return

        result = scraper.run(writers=self._writers)
        self._job_history.append({
            "job_id": job_id,
            "county": scraper.county,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "result": result,
        })

        if len(self._job_history) > 1000:
            self._job_history = self._job_history[-500:]

    def _on_job_executed(self, event):
        logger.debug(f"✅ Job {event.job_id} executed successfully")

    def _on_job_error(self, event):
        logger.error(
            f"❌ Job {event.job_id} failed: {event.exception}"
        )

    def start(self):
        """Start the scheduler."""
        logger.info(
            f"🚀 Starting scheduler with {len(self._scrapers)} scrapers"
        )
        self.scheduler.start()

    def stop(self):
        """Gracefully stop the scheduler."""
        logger.info("🛑 Stopping scheduler...")
        self.scheduler.shutdown(wait=True)

    def run_now(self, county: str) -> Optional[dict]:
        """Trigger an immediate run for a specific county."""
        job_id = f"scraper_{county.lower().replace(' ', '_')}"
        scraper = self._scrapers.get(job_id)
        if not scraper:
            logger.error(f"❌ No scraper registered for county: {county}")
            return None

        logger.info(f"⚡ Manual trigger: {county}")
        return scraper.run(writers=self._writers)

    def get_status(self) -> dict:
        """Return current scheduler status."""
        jobs = []
        for job in self.scheduler.get_jobs():
            scraper = self._scrapers.get(job.id)
            jobs.append({
                "job_id": job.id,
                "county": scraper.county if scraper else "unknown",
                "next_run": job.next_run_time.isoformat() if job.next_run_time else None,
                "health": scraper.health_check() if scraper else {},
            })

        return {
            "running": self.scheduler.running,
            "total_scrapers": len(self._scrapers),
            "max_workers": self.max_workers,
            "jobs": jobs,
            "recent_history": self._job_history[-10:],
        }
