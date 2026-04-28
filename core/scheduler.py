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

    def _poll_triggers(self):
        """
        Poll MongoDB 'scraper_triggers' collection for run-now requests
        submitted by the dashboard container.

        This is the cross-container bridge: dashboard writes a trigger doc,
        the scraper engine picks it up here and executes the run.

        Supported trigger types:
          - (default) run-now: full scraper run for a county
          - custody_recheck: re-check in-custody defendants against live roster
        """
        try:
            from pymongo import MongoClient
            from config.settings import settings as _s
            client = MongoClient(_s.MONGODB_URI, serverSelectionTimeoutMS=3000)
            db = client[_s.MONGODB_DB_NAME]
            col = db["scraper_triggers"]

            # Find all pending triggers
            pending = list(col.find({"status": "pending"}))
            if not pending:
                client.close()
                return

            for doc in pending:
                trigger_type = doc.get("type", "run_now")
                county = doc.get("county", "")
                job_id = f"scraper_{county.lower().replace(' ', '_')}"
                scraper = self._scrapers.get(job_id)

                # ── Custody Recheck Trigger ──
                if trigger_type == "custody_recheck":
                    if scraper:
                        logger.info(f"🔍 Custody recheck trigger for {county}")
                        col.update_one(
                            {"_id": doc["_id"]},
                            {"$set": {"status": "running", "started_at": datetime.now(timezone.utc)}}
                        )
                        try:
                            self._handle_custody_recheck(db, doc, scraper)
                            col.update_one(
                                {"_id": doc["_id"]},
                                {"$set": {"status": "done", "completed_at": datetime.now(timezone.utc)}}
                            )
                            logger.info(f"✅ Custody recheck complete: {county}")
                        except Exception as e:
                            col.update_one(
                                {"_id": doc["_id"]},
                                {"$set": {"status": "error", "error": str(e)[:500],
                                          "completed_at": datetime.now(timezone.utc)}}
                            )
                            logger.error(f"❌ Custody recheck failed: {county}: {e}")
                    else:
                        logger.warning(f"⚠️ Custody recheck: no scraper for '{county}'")
                        col.update_one(
                            {"_id": doc["_id"]},
                            {"$set": {"status": "not_found"}}
                        )
                    continue

                # ── Standard Run-Now Trigger ──
                if scraper:
                    logger.info(f"⚡ Trigger poll: running {county} (requested by dashboard)")
                    # Mark as running before executing
                    col.update_one(
                        {"_id": doc["_id"]},
                        {"$set": {"status": "running", "started_at": datetime.now(timezone.utc)}}
                    )
                    try:
                        result = scraper.run(writers=self._writers)
                        col.update_one(
                            {"_id": doc["_id"]},
                            {"$set": {"status": "done", "completed_at": datetime.now(timezone.utc),
                                      "result": str(result)[:500]}}
                        )
                        logger.info(f"✅ Trigger run complete: {county}")
                    except Exception as e:
                        col.update_one(
                            {"_id": doc["_id"]},
                            {"$set": {"status": "error", "error": str(e),
                                      "completed_at": datetime.now(timezone.utc)}}
                        )
                        logger.error(f"❌ Trigger run failed: {county}: {e}")
                else:
                    logger.warning(f"⚠️ Trigger: no scraper for county '{county}'")
                    col.update_one(
                        {"_id": doc["_id"]},
                        {"$set": {"status": "not_found"}}
                    )

            client.close()
        except Exception as e:
            logger.debug(f"Trigger poll error (non-fatal): {e}")

    def _handle_custody_recheck(self, db, trigger_doc, scraper):
        """
        Re-check in-custody defendants against the live jail roster.

        For each defendant:
        1. Fetch old record from MongoDB
        2. Call scraper._fetch_single_booking() to get live data
        3. Compare key fields: status, bond_amount, charges, bond_type
        4. Write diff results to custody_rechecks collection
        5. If changes detected, update the arrest record in MongoDB

        Counties without _fetch_single_booking() fall back to a full scraper run.
        """
        county = trigger_doc.get("county", "")
        trigger_id = str(trigger_doc.get("_id", ""))
        mode = trigger_doc.get("mode", "county")
        booking_number = trigger_doc.get("booking_number", "")

        arrests_col = db["arrests"]
        rechecks_col = db["custody_rechecks"]
        triggers_col = db["scraper_triggers"]

        # Check if scraper supports single-booking lookups
        has_single = hasattr(scraper, "_fetch_single_booking") and callable(
            getattr(scraper, "_fetch_single_booking", None)
        )

        if not has_single:
            logger.info(
                f"🔄 {county} has no _fetch_single_booking — falling back to full run"
            )
            scraper.run(writers=self._writers)
            triggers_col.update_one(
                {"_id": trigger_doc["_id"]},
                {"$set": {
                    "total_checked": 0,
                    "fallback": True,
                    "message": f"{county} does not support single-record recheck. Full scraper run completed.",
                }}
            )
            return

        # Build query for defendants to check
        if mode == "single" and booking_number:
            query = {"booking_number": booking_number, "county": county}
        else:
            # All in-custody defendants for this county
            query = {
                "county": county,
                "status": {"$regex": "custody", "$options": "i"},
            }

        defendants = list(arrests_col.find(query, {
            "booking_number": 1, "full_name": 1, "status": 1,
            "bond_amount": 1, "charges": 1, "bond_type": 1,
            "detail_url": 1, "county": 1,
        }))

        logger.info(f"🔍 Checking {len(defendants)} defendants in {county}")

        # Clear old recheck results for this trigger
        rechecks_col.delete_many({"trigger_id": trigger_id})

        checked = 0
        changes_found = 0
        not_found = 0

        # Fields to compare for diffs
        DIFF_FIELDS = ["status", "bond_amount", "charges", "bond_type"]

        for old_doc in defendants:
            bk = old_doc.get("booking_number", "")
            detail_url = old_doc.get("detail_url", "")
            full_name = old_doc.get("full_name", "Unknown")

            if not bk:
                continue

            checked += 1
            try:
                new_record = scraper._fetch_single_booking(bk, detail_url)
            except Exception as e:
                logger.warning(f"  ⚠️ Recheck error for {bk}: {e}")
                new_record = None

            now = datetime.now(timezone.utc)

            if new_record is None:
                # Not found on roster — possibly released
                not_found += 1
                rechecks_col.insert_one({
                    "trigger_id": trigger_id,
                    "county": county,
                    "booking_number": bk,
                    "full_name": full_name,
                    "checked_at": now,
                    "source_found": False,
                    "changes": [{"field": "status", "old": old_doc.get("status", ""), "new": "Not Found on Roster"}],
                    "detail_url": detail_url,
                    "checked_by": "custody_recheck_agent",
                })
                continue

            # Compare old vs new
            diffs = []
            new_dict = new_record.to_dict() if hasattr(new_record, "to_dict") else {}

            for field in DIFF_FIELDS:
                old_val = old_doc.get(field, "")
                new_val = new_dict.get(field, "")
                # Normalize for comparison
                if isinstance(old_val, (int, float)) and isinstance(new_val, (int, float)):
                    if old_val != new_val:
                        diffs.append({"field": field, "old": old_val, "new": new_val})
                else:
                    old_str = str(old_val or "").strip().lower()
                    new_str = str(new_val or "").strip().lower()
                    if old_str != new_str:
                        diffs.append({"field": field, "old": str(old_val or ""), "new": str(new_val or "")})

            if diffs:
                changes_found += 1
                # Write diff record
                rechecks_col.insert_one({
                    "trigger_id": trigger_id,
                    "county": county,
                    "booking_number": bk,
                    "full_name": full_name,
                    "checked_at": now,
                    "source_found": True,
                    "changes": diffs,
                    "detail_url": detail_url,
                    "checked_by": "custody_recheck_agent",
                })

                # Also update the arrest record with live data
                update_fields = {}
                for d in diffs:
                    update_fields[d["field"]] = d["new"]
                update_fields["last_custody_recheck"] = now.isoformat()
                update_fields["custody_recheck_source"] = "live_roster"

                arrests_col.update_one(
                    {"booking_number": bk, "county": county},
                    {"$set": update_fields}
                )
                logger.info(f"  📋 {bk} ({full_name}): {len(diffs)} change(s) detected")
            else:
                # No changes — still write a "verified" record
                rechecks_col.insert_one({
                    "trigger_id": trigger_id,
                    "county": county,
                    "booking_number": bk,
                    "full_name": full_name,
                    "checked_at": now,
                    "source_found": True,
                    "changes": [],
                    "detail_url": detail_url,
                    "checked_by": "custody_recheck_agent",
                })

                # Update last-checked timestamp
                arrests_col.update_one(
                    {"booking_number": bk, "county": county},
                    {"$set": {"last_custody_recheck": now.isoformat()}}
                )

        # Update trigger with summary
        triggers_col.update_one(
            {"_id": trigger_doc["_id"]},
            {"$set": {
                "total_checked": checked,
                "changes_found": changes_found,
                "not_found_count": not_found,
            }}
        )

        logger.info(
            f"🔍 Custody recheck summary for {county}: "
            f"{checked} checked, {changes_found} changed, {not_found} not found"
        )


    def start(self):
        """Start the scheduler."""
        logger.info(
            f"🚀 Starting scheduler with {len(self._scrapers)} scrapers"
        )
        self.scheduler.start()

        # Add trigger polling job — runs every 30 seconds
        from apscheduler.triggers.interval import IntervalTrigger as _IT
        self.scheduler.add_job(
            self._poll_triggers,
            trigger=_IT(seconds=30),
            id="trigger_poller",
            name="Dashboard Run-Now Trigger Poller",
            replace_existing=True,
            misfire_grace_time=60,
        )
        logger.info("🔄 Trigger poller registered (30s interval)")

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
