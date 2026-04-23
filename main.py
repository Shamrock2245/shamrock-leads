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
from writers.mongo_writer import MongoWriter

from scrapers.counties.lee import LeeCountyScraper
from scrapers.counties.collier import CollierCountyScraper
from scrapers.counties.charlotte import CharlotteCountyScraper
from scrapers.counties.hendry import HendryCountyScraper
from scrapers.counties.desoto import DeSotoCountyScraper
from scrapers.counties.manatee import ManateeCountyScraper
from scrapers.counties.sarasota import SarasotaCountyScraper
from scrapers.counties.orange import OrangeCountyScraper
from scrapers.counties.pinellas import PinellasCountyScraper
from scrapers.counties.polk import PolkCountyScraper
from scrapers.counties.osceola import OsceolaCountyScraper
from scrapers.counties.seminole import SeminoleCountyScraper
from scrapers.counties.palm_beach import PalmBeachCountyScraper
from scrapers.counties.hillsborough import HillsboroughCountyScraper
from scrapers.counties.broward import BrowardCountyScraper
from scrapers.counties.duval import DuvalCountyScraper
from scrapers.counties.volusia import VolusiaCountyScraper
from scrapers.counties.brevard import BrevardCountyScraper
from scrapers.counties.pasco import PascoCountyScraper
from scrapers.counties.escambia import EscambiaCountyScraper

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
    sched.register_scraper(LeeCountyScraper(), interval_minutes=20)
    sched.register_scraper(CollierCountyScraper(), interval_minutes=30)
    sched.register_scraper(CharlotteCountyScraper(), interval_minutes=45)
    sched.register_scraper(HendryCountyScraper(), interval_minutes=120)
    sched.register_scraper(DeSotoCountyScraper(), interval_minutes=60)
    sched.register_scraper(ManateeCountyScraper(), interval_minutes=45)
    sched.register_scraper(SarasotaCountyScraper(), interval_minutes=60)
    sched.register_scraper(OrangeCountyScraper(), interval_minutes=90)
    sched.register_scraper(PinellasCountyScraper(), interval_minutes=90)
    sched.register_scraper(PolkCountyScraper(), interval_minutes=120)
    sched.register_scraper(OsceolaCountyScraper(), interval_minutes=120)
    sched.register_scraper(SeminoleCountyScraper(), interval_minutes=90)
    sched.register_scraper(PalmBeachCountyScraper(), interval_minutes=120)
    sched.register_scraper(HillsboroughCountyScraper(), interval_minutes=90)
    sched.register_scraper(BrowardCountyScraper(), interval_minutes=60)
    sched.register_scraper(DuvalCountyScraper(), interval_minutes=90)
    sched.register_scraper(VolusiaCountyScraper(), interval_minutes=90)
    sched.register_scraper(BrevardCountyScraper(), interval_minutes=120)
    sched.register_scraper(PascoCountyScraper(), interval_minutes=90)
    sched.register_scraper(EscambiaCountyScraper(), interval_minutes=120)

def handle_shutdown(signum, frame):
    logger.info("Shutdown signal received")
    if scheduler:
        scheduler.stop()
    sys.exit(0)

def main():
    global scheduler
    logger.info("=" * 60)
    logger.info("ShamrockLeads - Florida Arrest Intelligence Platform")
    logger.info("=" * 60)
    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)
    writers = build_writers()
    scheduler = ScraperScheduler()
    scheduler.set_writers(writers)
    register_scrapers(scheduler)
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
    logger.info("Scheduler running. Press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(60)
    except (KeyboardInterrupt, SystemExit):
        handle_shutdown(None, None)

if __name__ == "__main__":
    main()
