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

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from config.settings import settings
from core.scheduler import ScraperScheduler
from core.dedup import DedupEngine
from writers.mongo_writer import MongoWriter

# Import county scrapers
from scrapers.counties.lee import LeeCountyScraper
from scrapers.counties.collier import CollierCountyScraper
from scrapers.counties.charlotte import CharlotteCountyScraper
from scrapers.counties.hendry import HendryCountyScraper
from scrapers.counties.desoto import DeSotoCountyScraper
from scrapers.counties.manatee import ManateeCountyScraper
from scrapers.counties.sarasota import SarasotaCountyScraper

# Tier 4: Central & East FL expansion
from scrapers.counties.orange import OrangeCountyScraper
from scrapers.counties.pinellas import PinellasCountyScraper
from scrapers.counties.polk import PolkCountyScraper
from scrapers.counties.osceola import OsceolaCountyScraper
from scrapers.counties.seminole import SeminoleCountyScraper
from scrapers.counties.palm_beach import PalmBeachCountyScraper

# ── Logging ──
logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL, logging.INFO),
    format="%(asctime)s │ %(levelname)-7s │ %(name)s │ %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("shamrock-leads")

# ── Globals ──
scheduler: ScraperScheduler = None


def build_writers() -> list:
    """Initialize configured data writers."""
    writers = []

    if settings.ENABLE_MONGO_WRITER and settings.mongo_configured():
        try:
            mongo = MongoWriter()
            writers.append(mongo)
            logger.info("✅ MongoDB writer initialized")
        except Exception as e:
            logger.error(f"❌ MongoDB writer failed to initialize: {e}")

    if settings.ENABLE_SHEETS_WRITER and settings.sheets_configured():
        try:
            # Lazy import — gspread is optional
            from writers.sheets_writer import SheetsWriter
            sheets = SheetsWriter(
                spreadsheet_id=settings.GOOGLE_SPREADSHEET_ID,
                credentials_path=settings.GOOGLE_APPLICATION_CREDENTIALS
            )
            writers.append(sheets)
            logger.info("✅ Google Sheets writer initialized")
        except ImportError:
            logger.warning("⚠️ gspread not installed, Sheets writer disabled")
        except Exception as e:
            logger.error(f"❌ Sheets writer failed: {e}")

    if not writers:
        logger.warning("⚠️ No writers configured! Records will only be logged.")

    return writers


def register_scrapers(sched: ScraperScheduler):
    """Register all county scrapers with their schedules."""

    # ── Tier 1: Core SWFL counties (API-based, high frequency) ──
    sched.register_scraper(LeeCountyScraper(), interval_minutes=20)
    sched.register_scraper(CollierCountyScraper(), interval_minutes=30)

    # ── Tier 2: Browser-automated counties (lower frequency) ──
    sched.register_scraper(CharlotteCountyScraper(), interval_minutes=45)
    sched.register_scraper(HendryCountyScraper(), interval_minutes=120)

    # ── Tier 3: Expanded SWFL coverage (browser-automated) ──
    sched.register_scraper(DeSotoCountyScraper(), interval_minutes=60)
    sched.register_scraper(ManateeCountyScraper(), interval_minutes=45)
    sched.register_scraper(SarasotaCountyScraper(), interval_minutes=60)

    # ── Tier 4: Central & East FL expansion (browser-automated, lower freq) ──
    sched.register_scraper(OrangeCountyScraper(), interval_minutes=90)
    sched.register_scraper(PinellasCountyScraper(), interval_minutes=90)
    sched.register_scraper(PolkCountyScraper(), interval_minutes=120)
    sched.register_scraper(OsceolaCountyScraper(), interval_minutes=120)
    sched.register_scraper(SeminoleCountyScraper(), interval_minutes=90)
    sched.register_scraper(PalmBeachCountyScraper(), interval_minutes=120)


def handle_shutdown(signum, frame):
    """Graceful shutdown handler."""
    logger.info("🛑 Shutdown signal received")
    if scheduler:
        scheduler.stop()
    sys.exit(0)


def main():
    """Main entry point."""
    global scheduler

    logger.info("═" * 60)
    logger.info("🍀 ShamrockLeads — Florida Arrest Intelligence Platform")
    logger.info("═" * 60)

    # Register signal handlers
    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)

    # Initialize writers
    writers = build_writers()

    # Initialize scheduler
    scheduler = ScraperScheduler()
    scheduler.set_writers(writers)

    # Register scrapers
    register_scrapers(scheduler)

    # Check for one-shot mode
    if len(sys.argv) > 1:
        county = sys.argv[1]
        logger.info(f"⚡ One-shot mode: running {county} scraper")
        result = scheduler.run_now(county)
        if result:
            logger.info(f"📊 Result: {result}")
        else:
            logger.error(f"❌ No scraper found for county: {county}")
        return

    # Start scheduled mode
    scheduler.start()
    logger.info("✅ Scheduler running. Press Ctrl+C to stop.")

    # Keep main thread alive
    try:
        while True:
            time.sleep(60)
    except (KeyboardInterrupt, SystemExit):
        handle_shutdown(None, None)


if __name__ == "__main__":
    main()
