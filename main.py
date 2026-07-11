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

# ── Georgia Scrapers ───────────────────────────────────────────────────────────
from scrapers.counties_ga.eas_batch_runner import run_eas_batch
from scrapers.counties_ga.bacon import BaconScraper
from scrapers.counties_ga.baker import BakerScraper
from scrapers.counties_ga.banks import BanksScraper
from scrapers.counties_ga.barrow import BarrowScraper
from scrapers.counties_ga.bartow import BartowScraper
from scrapers.counties_ga.bibb import BibbScraper
from scrapers.counties_ga.brantley import BrantleyScraper
from scrapers.counties_ga.bryan import BryanScraper
from scrapers.counties_ga.bulloch import BullochScraper
from scrapers.counties_ga.camden import CamdenScraper
from scrapers.counties_ga.carroll import CarrollScraper
from scrapers.counties_ga.catoosa import CatoosaScraper
from scrapers.counties_ga.chatham import ChathamScraper
from scrapers.counties_ga.cherokee import CherokeeScraper
from scrapers.counties_ga.clarke import ClarkeScraper
from scrapers.counties_ga.cobb import CobbScraper
from scrapers.counties_ga.columbia import ColumbiaScraper
from scrapers.counties_ga.coweta import CowetaScraper
from scrapers.counties_ga.crawford import CrawfordScraper
from scrapers.counties_ga.dawson import DawsonScraper
from scrapers.counties_ga.decatur import DecaturScraper
from scrapers.counties_ga.dekalb import DeKalbScraper
from scrapers.counties_ga.dodge import DodgeScraper
from scrapers.counties_ga.dougherty import DoughertyScraper
from scrapers.counties_ga.douglas import DouglasScraper
from scrapers.counties_ga.echols import EcholsScraper
from scrapers.counties_ga.emanuel import EmanuelScraper
from scrapers.counties_ga.fayette import FayetteScraper
from scrapers.counties_ga.floyd import FloydScraper
from scrapers.counties_ga.forsyth import ForsythScraper
from scrapers.counties_ga.fulton import FultonScraper
from scrapers.counties_ga.glynn import GlynnScraper
from scrapers.counties_ga.grady import GradyScraper
from scrapers.counties_ga.gwinnett import GwinnettScraper
from scrapers.counties_ga.habersham import HabershamScraper
from scrapers.counties_ga.hall import HallScraper
from scrapers.counties_ga.hancock import HancockScraper
from scrapers.counties_ga.haralson import HaralsonScraper
from scrapers.counties_ga.heard import HeardScraper
from scrapers.counties_ga.henry import HenryScraper
from scrapers.counties_ga.houston import HoustonScraper
from scrapers.counties_ga.jasper import JasperScraper
from scrapers.counties_ga.johnson import JohnsonScraper
from scrapers.counties_ga.jones import JonesScraper
from scrapers.counties_ga.lee import LeeScraper
from scrapers.counties_ga.liberty import LibertyScraper
from scrapers.counties_ga.lowndes import LowndesScraper
from scrapers.counties_ga.lumpkin import LumpkinScraper
from scrapers.counties_ga.macon import MaconScraper
from scrapers.counties_ga.mcintosh import McIntoshScraper
from scrapers.counties_ga.miller import MillerScraper
from scrapers.counties_ga.murray import MurrayScraper
from scrapers.counties_ga.muscogee import MuscogeeScraper
from scrapers.counties_ga.oconee import OconeeScraper
from scrapers.counties_ga.oglethorpe import OglethorpeScraper
from scrapers.counties_ga.paulding import PauldingScraper
from scrapers.counties_ga.pickens import PickensScraper
from scrapers.counties_ga.polk import PolkScraper
from scrapers.counties_ga.pulaski import PulaskiScraper
from scrapers.counties_ga.putnam import PutnamScraper
from scrapers.counties_ga.randolph import RandolphScraper
from scrapers.counties_ga.richmond import RichmondScraper
from scrapers.counties_ga.rockdale import RockdaleScraper
from scrapers.counties_ga.spalding import SpaldingScraper
from scrapers.counties_ga.sumter import SumterScraper
from scrapers.counties_ga.tattnall import TattnallScraper
from scrapers.counties_ga.taylor import TaylorScraper
from scrapers.counties_ga.thomas import ThomasScraper
from scrapers.counties_ga.toombs import ToombsScraper
from scrapers.counties_ga.treutlen import TreutlenScraper
from scrapers.counties_ga.troup import TroupScraper
from scrapers.counties_ga.twiggs import TwiggsScraper
from scrapers.counties_ga.upson import UpsonScraper
from scrapers.counties_ga.walton import WaltonScraper

# ── South Carolina Scrapers ────────────────────────────────────────────────────────
from scrapers.counties_sc.aiken import AikenScraper
from scrapers.counties_sc.bamberg import BambergScraper
from scrapers.counties_sc.beaufort import BeaufortScraper
from scrapers.counties_sc.berkeley import BerkeleyScraper
from scrapers.counties_sc.charleston import CharlestonScraper
from scrapers.counties_sc.darlington import DarlingtonScraper
from scrapers.counties_sc.florence import FlorenceScraper
from scrapers.counties_sc.greenville import GreenvilleScraper
from scrapers.counties_sc.hampton import HamptonScraper
from scrapers.counties_sc.horry import HorryScraper
from scrapers.counties_sc.jasper import JasperScraper
from scrapers.counties_sc.marion import MarionScraper
from scrapers.counties_sc.newberry import NewberryScraper
from scrapers.counties_sc.richland import RichlandScraper
from scrapers.counties_sc.york import YorkScraper
from scrapers.counties_sc.anderson import AndersonScraper
from scrapers.counties_sc.cherokee import CherokeeScraper
from scrapers.counties_sc.chester import ChesterScraper
from scrapers.counties_sc.chesterfield import ChesterfieldScraper
from scrapers.counties_sc.colleton import ColletonScraper
from scrapers.counties_sc.dorchester import DorchesterScraper
from scrapers.counties_sc.greenwood import GreenwoodScraper
from scrapers.counties_sc.kershaw import KershawScraper
from scrapers.counties_sc.lancaster import LancasterScraper
from scrapers.counties_sc.laurens import LaurensScraper
from scrapers.counties_sc.lee import LeeScraper
from scrapers.counties_sc.lexington import LexingtonScraper
from scrapers.counties_sc.oconee import OconeeScraper
from scrapers.counties_sc.pickens import PickensScraper
from scrapers.counties_sc.sumter import SumterScraper
from scrapers.counties_sc.union import UnionScraper

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
    main()    # ── Georgia All Counties ───────────────────────────────────────────────────
    sched.scheduler.add_job(run_eas_batch, 'interval', minutes=60, id='eas_batch', replace_existing=True)
    sched.register_scraper(BaconScraper(), interval_minutes=120)
    sched.register_scraper(BakerScraper(), interval_minutes=120)
    sched.register_scraper(BanksScraper(), interval_minutes=120)
    sched.register_scraper(BarrowScraper(), interval_minutes=60)
    sched.register_scraper(BartowScraper(), interval_minutes=60)
    sched.register_scraper(BibbScraper(), interval_minutes=120)
    sched.register_scraper(BrantleyScraper(), interval_minutes=120)
    sched.register_scraper(BryanScraper(), interval_minutes=120)
    sched.register_scraper(BullochScraper(), interval_minutes=120)
    sched.register_scraper(CamdenScraper(), interval_minutes=60)
    sched.register_scraper(CarrollScraper(), interval_minutes=120)
    sched.register_scraper(CatoosaScraper(), interval_minutes=120)
    sched.register_scraper(ChathamScraper(), interval_minutes=120)
    sched.register_scraper(CherokeeScraper(), interval_minutes=120)
    sched.register_scraper(ClarkeScraper(), interval_minutes=120)
    sched.register_scraper(CobbScraper(), interval_minutes=120)
    sched.register_scraper(ColumbiaScraper(), interval_minutes=60)
    sched.register_scraper(CowetaScraper(), interval_minutes=60)
    sched.register_scraper(CrawfordScraper(), interval_minutes=120)
    sched.register_scraper(DawsonScraper(), interval_minutes=120)
    sched.register_scraper(DecaturScraper(), interval_minutes=120)
    sched.register_scraper(DeKalbScraper(), interval_minutes=60)
    sched.register_scraper(DodgeScraper(), interval_minutes=120)
    sched.register_scraper(DoughertyScraper(), interval_minutes=60)
    sched.register_scraper(DouglasScraper(), interval_minutes=120)
    sched.register_scraper(EcholsScraper(), interval_minutes=60)
    sched.register_scraper(EmanuelScraper(), interval_minutes=120)
    sched.register_scraper(FayetteScraper(), interval_minutes=120)
    sched.register_scraper(FloydScraper(), interval_minutes=120)
    sched.register_scraper(ForsythScraper(), interval_minutes=60)
    sched.register_scraper(FultonScraper(), interval_minutes=120)
    sched.register_scraper(GlynnScraper(), interval_minutes=120)
    sched.register_scraper(GradyScraper(), interval_minutes=120)
    sched.register_scraper(GwinnettScraper(), interval_minutes=120)
    sched.register_scraper(HabershamScraper(), interval_minutes=120)
    sched.register_scraper(HallScraper(), interval_minutes=60)
    sched.register_scraper(HancockScraper(), interval_minutes=120)
    sched.register_scraper(HaralsonScraper(), interval_minutes=120)
    sched.register_scraper(HeardScraper(), interval_minutes=120)
    sched.register_scraper(HenryScraper(), interval_minutes=60)
    sched.register_scraper(HoustonScraper(), interval_minutes=120)
    sched.register_scraper(JasperScraper(), interval_minutes=120)
    sched.register_scraper(JohnsonScraper(), interval_minutes=120)
    sched.register_scraper(JonesScraper(), interval_minutes=120)
    sched.register_scraper(LeeScraper(), interval_minutes=120)
    sched.register_scraper(LibertyScraper(), interval_minutes=120)
    sched.register_scraper(LowndesScraper(), interval_minutes=60)
    sched.register_scraper(LumpkinScraper(), interval_minutes=120)
    sched.register_scraper(MaconScraper(), interval_minutes=60)
    sched.register_scraper(McIntoshScraper(), interval_minutes=120)
    sched.register_scraper(MillerScraper(), interval_minutes=120)
    sched.register_scraper(MurrayScraper(), interval_minutes=120)
    sched.register_scraper(MuscogeeScraper(), interval_minutes=60)
    sched.register_scraper(OconeeScraper(), interval_minutes=120)
    sched.register_scraper(OglethorpeScraper(), interval_minutes=120)
    sched.register_scraper(PauldingScraper(), interval_minutes=60)
    sched.register_scraper(PickensScraper(), interval_minutes=120)
    sched.register_scraper(PolkScraper(), interval_minutes=120)
    sched.register_scraper(PulaskiScraper(), interval_minutes=120)
    sched.register_scraper(PutnamScraper(), interval_minutes=120)
    sched.register_scraper(RandolphScraper(), interval_minutes=120)
    sched.register_scraper(RichmondScraper(), interval_minutes=120)
    sched.register_scraper(RockdaleScraper(), interval_minutes=60)
    sched.register_scraper(SpaldingScraper(), interval_minutes=60)
    sched.register_scraper(SumterScraper(), interval_minutes=120)
    sched.register_scraper(TattnallScraper(), interval_minutes=120)
    sched.register_scraper(TaylorScraper(), interval_minutes=120)
    sched.register_scraper(ThomasScraper(), interval_minutes=120)
    sched.register_scraper(ToombsScraper(), interval_minutes=120)
    sched.register_scraper(TreutlenScraper(), interval_minutes=120)
    sched.register_scraper(TroupScraper(), interval_minutes=120)
    sched.register_scraper(TwiggsScraper(), interval_minutes=120)
    sched.register_scraper(UpsonScraper(), interval_minutes=120)
    sched.register_scraper(WaltonScraper(), interval_minutes=120)

    # ── South Carolina All Counties ────────────────────────────────────────────
    sched.register_scraper(AikenScraper(), interval_minutes=60)
    sched.register_scraper(BambergScraper(), interval_minutes=60)
    sched.register_scraper(BeaufortScraper(), interval_minutes=60)
    sched.register_scraper(BerkeleyScraper(), interval_minutes=60)
    sched.register_scraper(CharlestonScraper(), interval_minutes=60)
    sched.register_scraper(DarlingtonScraper(), interval_minutes=60)
    sched.register_scraper(FlorenceScraper(), interval_minutes=60)
    sched.register_scraper(GreenvilleScraper(), interval_minutes=60)
    sched.register_scraper(HamptonScraper(), interval_minutes=60)
    sched.register_scraper(HorryScraper(), interval_minutes=60)
    sched.register_scraper(JasperScraper(), interval_minutes=60)
    sched.register_scraper(MarionScraper(), interval_minutes=60)
    sched.register_scraper(NewberryScraper(), interval_minutes=60)
    sched.register_scraper(RichlandScraper(), interval_minutes=60)
    sched.register_scraper(YorkScraper(), interval_minutes=60)
    sched.register_scraper(AndersonScraper(), interval_minutes=120)
    sched.register_scraper(CherokeeScraper(), interval_minutes=120)
    sched.register_scraper(ChesterScraper(), interval_minutes=120)
    sched.register_scraper(ChesterfieldScraper(), interval_minutes=120)
    sched.register_scraper(ColletonScraper(), interval_minutes=120)
    sched.register_scraper(DorchesterScraper(), interval_minutes=120)
    sched.register_scraper(GreenwoodScraper(), interval_minutes=120)
    sched.register_scraper(KershawScraper(), interval_minutes=120)
    sched.register_scraper(LancasterScraper(), interval_minutes=60)
    sched.register_scraper(LaurensScraper(), interval_minutes=120)
    sched.register_scraper(LeeScraper(), interval_minutes=60)
    sched.register_scraper(LexingtonScraper(), interval_minutes=60)
    sched.register_scraper(OconeeScraper(), interval_minutes=120)
    sched.register_scraper(PickensScraper(), interval_minutes=120)
    sched.register_scraper(SumterScraper(), interval_minutes=120)
    sched.register_scraper(UnionScraper(), interval_minutes=120)


