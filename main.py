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
from scrapers.counties.okaloosa import OkaloosaCountyScraper
from scrapers.counties.bay import BayCountyScraper
from scrapers.counties.leon import LeonCountyScraper

# ── Wave 1 — NE FL / First Coast ─────────────────────────────────────────────
from scrapers.counties.duval import DuvalCountyScraper
from scrapers.counties.st_johns import StJohnsCountyScraper

# ── Wave 1 — North FL / Rural ────────────────────────────────────────────────
from scrapers.counties.taylor import TaylorCountyScraper
from scrapers.counties.dixie import DixieCountyScraper

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
    sched.register_scraper(LeeCountyScraper(), interval_minutes=30)
    sched.register_scraper(CollierCountyScraper(), interval_minutes=30)
    sched.register_scraper(CharlotteCountyScraper(), interval_minutes=45)
    sched.register_scraper(ManateeCountyScraper(), interval_minutes=45)
    sched.register_scraper(SarasotaCountyScraper(), interval_minutes=60)
    sched.register_scraper(DeSotoCountyScraper(), interval_minutes=60)
    sched.register_scraper(HendryCountyScraper(), interval_minutes=120)

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
    sched.register_scraper(OkaloosaCountyScraper(), interval_minutes=120)
    sched.register_scraper(BayCountyScraper(), interval_minutes=120)
    sched.register_scraper(LeonCountyScraper(), interval_minutes=90)

    # ── NE FL / First Coast ────────────────────────────────────────────────────
    sched.register_scraper(DuvalCountyScraper(), interval_minutes=90)
    sched.register_scraper(StJohnsCountyScraper(), interval_minutes=120)

    # ── North FL / Rural ───────────────────────────────────────────────────────
    sched.register_scraper(TaylorCountyScraper(), interval_minutes=240)
    sched.register_scraper(DixieCountyScraper(), interval_minutes=240)

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
