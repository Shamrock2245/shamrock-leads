"""
ShamrockLeads — Entry Point

Initializes writers, registers scrapers, starts the APScheduler,
and provides a simple CLI interface for testing.
"""

import sys
import signal
import logging
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from config.settings import settings
from core.scheduler import ScraperScheduler
from core.dedup import DedupEngine
from core.first_appearance_watcher import FirstAppearanceWatcher
from writers.mongo_writer import MongoWriter
from maintenance.cleanup import run_cleanup

# Dashboard server
try:
    from dashboard.server import start_dashboard_server
    DASHBOARD_AVAILABLE = True
except ImportError:
    DASHBOARD_AVAILABLE = False

# ── Wave 1 — SWFL Core ──────────────────────────────────────────────────────
from scrapers.counties.lee import LeeCountyScraper
from scrapers.counties.collier import CollierCountyScraper
from scrapers.counties.charlotte import CharlotteCountyScraper
from scrapers.counties.hendry import HendryCountyScraper
from scrapers.counties.desoto import DeSotoCountyScraper
from scrapers.counties.manatee import ManateeCountyScraper
from scrapers.counties.sarasota import SarasotaCountyScraper

# ── Wave 1 — Tampa Bay / Central FL ─────────────────────────────────────────
from scrapers.counties.orange import OrangeCountyScraper
from scrapers.counties.pinellas import PinellasCountyScraper
from scrapers.counties.polk import PolkCountyScraper
from scrapers.counties.osceola import OsceolaCountyScraper
from scrapers.counties.seminole import SeminoleCountyScraper
from scrapers.counties.hillsborough import HillsboroughCountyScraper
from scrapers.counties.pasco import PascoCountyScraper
from scrapers.counties.hernando import HernandoCountyScraper
from scrapers.counties.citrus import CitrusCountyScraper
from scrapers.counties.sumter import SumterCountyScraper
from scrapers.counties.lake import LakeCountyScraper

# ── Wave 1 — South FL / Metro ────────────────────────────────────────────────
from scrapers.counties.palm_beach import PalmBeachCountyScraper
from scrapers.counties.broward import BrowardCountyScraper
from scrapers.counties.martin import MartinCountyScraper
from scrapers.counties.st_lucie import StLucieCountyScraper
from scrapers.counties.indian_river import IndianRiverCountyScraper
from scrapers.counties.glades import GladesCountyScraper
from scrapers.counties.highlands import HighlandsCountyScraper

# ── Wave 1 — North Central FL ────────────────────────────────────────────────
from scrapers.counties.alachua import AlachuaCountyScraper
from scrapers.counties.marion import MarionCountyScraper
from scrapers.counties.volusia import VolusiaCountyScraper
from scrapers.counties.brevard import BrevardCountyScraper
from scrapers.counties.putnam import PutnamCountyScraper

# ── Wave 1 — Panhandle / NW FL ───────────────────────────────────────────────
from scrapers.counties.escambia import EscambiaCountyScraper
from scrapers.counties.miami_dade import MiamiDadeCountyScraper
from scrapers.counties.okaloosa import OkaloosaCountyScraper
from scrapers.counties.bay import BayCountyScraper
from scrapers.counties.leon import LeonCountyScraper

# ── Wave 1 — NE FL / First Coast ─────────────────────────────────────────
from scrapers.counties.duval import DuvalCountyScraper
from scrapers.counties.st_johns import StJohnsCountyScraper

# ── Wave 1 — North FL / Rural ────────────────────────────────────────────────
from scrapers.counties.taylor import TaylorCountyScraper
from scrapers.counties.dixie import DixieCountyScraper

# ── Phase 1 Priority Expansion ───────────────────────────────────────────────
from scrapers.counties.flagler import FlaglerCountyScraper
from scrapers.counties.nassau import NassauCountyScraper
from scrapers.counties.clay import ClayCountyScraper
from scrapers.counties.columbia import ColumbiaCountyScraper
from scrapers.counties.suwannee import SuwanneeCountyScraper
from scrapers.counties.santa_rosa import SantaRosaCountyScraper
from scrapers.counties.walton import WaltonCountyScraper
from scrapers.counties.jackson import JacksonCountyScraper
from scrapers.counties.gadsden import GadsdenCountyScraper
from scrapers.counties.monroe import MonroeCountyScraper
from scrapers.counties.okeechobee import OkeechobeeCountyScraper
from scrapers.counties.hardee import HardeeCountyScraper

logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL, logging.INFO),
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("shamrock-leads")
scheduler = None

def build_writers():
    writers = []
    if settings.ENABLE_MONGO_WRITER and settings.mongo_configured():
        try:
            mongo = MongoWriter()
            writers.append(mongo)
            logger.info("MongoDB writer initialized")
        except Exception as e:
            logger.error(f"MongoDB writer failed: {e}")
    if settings.ENABLE_SHEETS_WRITER and settings.sheets_configured():
        try:
            from writers.sheets_writer import SheetsWriter
            sheets = SheetsWriter(spreadsheet_id=settings.GOOGLE_SPREADSHEET_ID, credentials_path=settings.GOOGLE_APPLICATION_CREDENTIALS)
            writers.append(sheets)
            logger.info("Sheets writer initialized")
        except ImportError:
            logger.warning("gspread not installed")
        except Exception as e:
            logger.error(f"Sheets writer failed: {e}")
    if not writers:
        logger.warning("No writers configured!")
    return writers

def register_scrapers(sched):
    # ── SWFL Core (highest priority) ──────────────────────────────────────────
    sched.register_scraper(LeeCountyScraper(), interval_minutes=10)
    sched.register_scraper(CollierCountyScraper(), interval_minutes=15)
    sched.register_scraper(CharlotteCountyScraper(), interval_minutes=10)
    sched.register_scraper(ManateeCountyScraper(), interval_minutes=10)
    sched.register_scraper(SarasotaCountyScraper(), interval_minutes=10)
    sched.register_scraper(DeSotoCountyScraper(), interval_minutes=60)
    sched.register_scraper(HendryCountyScraper(), interval_minutes=10)

    # ── Tampa Bay / Central FL ─────────────────────────────────────────────────
    sched.register_scraper(HillsboroughCountyScraper(), interval_minutes=90)
    sched.register_scraper(PinellasCountyScraper(), interval_minutes=90)
    sched.register_scraper(SeminoleCountyScraper(), interval_minutes=90)
    sched.register_scraper(OrangeCountyScraper(), interval_minutes=90)
    sched.register_scraper(PascoCountyScraper(), interval_minutes=90)
    sched.register_scraper(LakeCountyScraper(), interval_minutes=90)
    sched.register_scraper(HernandoCountyScraper(), interval_minutes=90)
    sched.register_scraper(PolkCountyScraper(), interval_minutes=120)
    sched.register_scraper(OsceolaCountyScraper(), interval_minutes=120)
    sched.register_scraper(CitrusCountyScraper(), interval_minutes=120)
    sched.register_scraper(SumterCountyScraper(), interval_minutes=180)

    # ── South FL / Metro ───────────────────────────────────────────────────────
    sched.register_scraper(BrowardCountyScraper(), interval_minutes=60)
    sched.register_scraper(PalmBeachCountyScraper(), interval_minutes=120)
    sched.register_scraper(MartinCountyScraper(), interval_minutes=120)
    sched.register_scraper(StLucieCountyScraper(), interval_minutes=90)
    sched.register_scraper(IndianRiverCountyScraper(), interval_minutes=120)
    sched.register_scraper(HighlandsCountyScraper(), interval_minutes=120)
    sched.register_scraper(GladesCountyScraper(), interval_minutes=180)

    # ── North Central FL ───────────────────────────────────────────────────────
    sched.register_scraper(VolusiaCountyScraper(), interval_minutes=90)
    sched.register_scraper(BrevardCountyScraper(), interval_minutes=120)
    sched.register_scraper(AlachuaCountyScraper(), interval_minutes=90)
    # MarionCountyScraper DISABLED — jail.marionso.com blocks datacenter IPs (403)
    # Needs residential proxy to re-enable
    # sched.register_scraper(MarionCountyScraper(), interval_minutes=90)
    sched.register_scraper(PutnamCountyScraper(), interval_minutes=180)

    # ── Panhandle / NW FL ──────────────────────────────────────────────────────
    sched.register_scraper(EscambiaCountyScraper(), interval_minutes=120)
    sched.register_scraper(MiamiDadeCountyScraper(), interval_minutes=60)
    sched.register_scraper(OkaloosaCountyScraper(), interval_minutes=120)
    sched.register_scraper(BayCountyScraper(), interval_minutes=120)
    # LeonCountyScraper DISABLED — target server throws unhandled 500 Runtime Error when results exist
    # Needs sheriff IT to fix their IIS search page to re-enable
    # sched.register_scraper(LeonCountyScraper(), interval_minutes=90)

    # ── NE FL / First Coast ────────────────────────────────────────────────────
    sched.register_scraper(DuvalCountyScraper(), interval_minutes=90)
    sched.register_scraper(StJohnsCountyScraper(), interval_minutes=120)

    # ── North FL / Rural ───────────────────────────────────────────────────────
    sched.register_scraper(TaylorCountyScraper(), interval_minutes=240)
    sched.register_scraper(DixieCountyScraper(), interval_minutes=240)

    # ── Phase 1 Priority Expansion ────────────────────────────────────────────
    sched.register_scraper(FlaglerCountyScraper(), interval_minutes=120)   # New World
    sched.register_scraper(NassauCountyScraper(), interval_minutes=120)    # New World
    sched.register_scraper(ClayCountyScraper(), interval_minutes=120)      # Custom HTML
    sched.register_scraper(ColumbiaCountyScraper(), interval_minutes=120)  # P2C
    sched.register_scraper(SuwanneeCountyScraper(), interval_minutes=180)  # SmartWeb
    sched.register_scraper(SantaRosaCountyScraper(), interval_minutes=120) # SmartWeb
    sched.register_scraper(WaltonCountyScraper(), interval_minutes=120)    # New World
    sched.register_scraper(JacksonCountyScraper(), interval_minutes=360)   # Stub — no public roster
    sched.register_scraper(GadsdenCountyScraper(), interval_minutes=180)   # Needs recon
    sched.register_scraper(MonroeCountyScraper(), interval_minutes=120)    # Keys SO
    sched.register_scraper(OkeechobeeCountyScraper(), interval_minutes=120)# Custom HTML
    sched.register_scraper(HardeeCountyScraper(), interval_minutes=120)    # OCV API

# Global watcher instance (set in main)
_fa_watcher: "FirstAppearanceWatcher | None" = None


def handle_shutdown(signum, frame):
    logger.info("Shutdown signal received")
    if scheduler:
        scheduler.stop()
    if _fa_watcher:
        _fa_watcher.close()
    sys.exit(0)

def _run_scheduled_cleanup():
    """Wrapper for APScheduler to run MongoDB + data cleanup."""
    logger.info("🧹 Running scheduled data cleanup...")
    try:
        result = run_cleanup()
        logger.info(f"🧹 Cleanup complete: {result}")
    except Exception as e:
        logger.error(f"🧹 Cleanup failed: {e}")


def _run_first_appearance_watcher():
    """
    Wrapper for APScheduler to run the FirstAppearanceWatcher cycle.
    Re-checks no-bond / disqualified records up to 3 days after arrest
    so that bond set at first appearance is detected and re-alerted.
    """
    if _fa_watcher is None:
        return
    try:
        stats = _fa_watcher.run()
        if stats.get("bond_set", 0) > 0:
            logger.info(
                f"🔔 FirstAppearanceWatcher: {stats['bond_set']} bond(s) set this cycle"
            )
    except Exception as e:
        logger.error(f"FirstAppearanceWatcher run failed: {e}")

def main():
    global scheduler, _fa_watcher
    logger.info("=" * 60)
    logger.info("ShamrockLeads - Florida Arrest Intelligence Platform")
    logger.info("=" * 60)
    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)
    writers = build_writers()
    scheduler = ScraperScheduler()
    scheduler.set_writers(writers)
    register_scrapers(scheduler)

    # ── Build scraper registry for FirstAppearanceWatcher ─────────────────
    # Maps county name → scraper instance so the watcher can call
    # _fetch_single_booking() on the appropriate county scraper.
    scraper_registry = {
        s.county: s for s in scheduler._scrapers.values()
    }

    # ── Initialize FirstAppearanceWatcher ─────────────────────────────────
    # Watches no-bond / disqualified records for up to 3 days post-arrest
    # and re-alerts when bond is set at first appearance.
    _fa_watcher = FirstAppearanceWatcher(
        writers=writers,
        scraper_registry=scraper_registry,
    )
    logger.info("🔔 FirstAppearanceWatcher initialized")

    # ── Register maintenance jobs ─────────────────────────────────────────
    # Auto-purge stale data every 6 hours to keep MongoDB lean
    from apscheduler.triggers.interval import IntervalTrigger
    scheduler.scheduler.add_job(
        _run_scheduled_cleanup,
        trigger=IntervalTrigger(hours=6),
        id="maintenance_cleanup",
        name="Data Cleanup & Purge",
        replace_existing=True,
        misfire_grace_time=600,
    )

    # ── First Appearance Watcher — every 30 minutes ───────────────────────
    # Catches no-bond records that get bond set at first appearance
    # (within 24–72 hours of arrest per Fla. R. Crim. P. 3.130).
    # Runs 5 minutes after startup to let scrapers populate data first.
    from datetime import datetime, timezone, timedelta
    fa_first_run = datetime.now(timezone.utc) + timedelta(minutes=5)
    scheduler.scheduler.add_job(
        _run_first_appearance_watcher,
        trigger=IntervalTrigger(minutes=30),
        id="first_appearance_watcher",
        name="First Appearance Bond Watcher",
        replace_existing=True,
        next_run_time=fa_first_run,
        misfire_grace_time=300,
    )
    logger.info("🔔 FirstAppearanceWatcher scheduled (every 30 min, first run in 5 min)")
    if len(sys.argv) > 1:
        county = sys.argv[1]
        logger.info(f"One-shot mode: running {county} scraper")
        result = scheduler.run_now(county)
        if result:
            logger.info(f"Result: {result}")
        else:
            logger.error(f"No scraper found for county: {county}")
        return
    scheduler.start()
    # Start dashboard server on port 8088
    if DASHBOARD_AVAILABLE:
        try:
            start_dashboard_server(port=8088)
        except Exception as e:
            logger.warning(f"Dashboard server failed to start: {e}")
    logger.info("Scheduler running. Press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(60)
    except (KeyboardInterrupt, SystemExit):
        handle_shutdown(None, None)

if __name__ == "__main__":
    main()
