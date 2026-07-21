"""
ShamrockLeads — Entry Point

Initializes writers, registers scrapers, starts the APScheduler,
and provides a simple CLI interface for testing.

Supported states (Palmetto surety footprint):
  FL (live), GA (live), SC (building out), then NC/TN/TX/CT/LA/MS.
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

try:
    from dashboard.server import start_dashboard_server
    DASHBOARD_AVAILABLE = True
except ImportError:
    DASHBOARD_AVAILABLE = False

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

# ── Georgia Scrapers ───────────────────────────────────────────────────────
from scrapers.counties_ga.eas_batch_runner import run_eas_batch
from scrapers.counties_ga.bacon import BaconScraper as GA_BaconScraper
from scrapers.counties_ga.baker import BakerScraper as GA_BakerScraper
from scrapers.counties_ga.banks import BanksScraper as GA_BanksScraper
from scrapers.counties_ga.barrow import BarrowScraper as GA_BarrowScraper
from scrapers.counties_ga.bartow import BartowScraper as GA_BartowScraper
from scrapers.counties_ga.bibb import BibbScraper as GA_BibbScraper
from scrapers.counties_ga.brantley import BrantleyScraper as GA_BrantleyScraper
from scrapers.counties_ga.bryan import BryanScraper as GA_BryanScraper
from scrapers.counties_ga.bulloch import BullochScraper as GA_BullochScraper
from scrapers.counties_ga.camden import CamdenScraper as GA_CamdenScraper
from scrapers.counties_ga.carroll import CarrollScraper as GA_CarrollScraper
from scrapers.counties_ga.catoosa import CatoosaScraper as GA_CatoosaScraper
from scrapers.counties_ga.chatham import ChathamScraper as GA_ChathamScraper
from scrapers.counties_ga.cherokee import CherokeeScraper as GA_CherokeeScraper
from scrapers.counties_ga.clarke import ClarkeScraper as GA_ClarkeScraper
from scrapers.counties_ga.cobb import CobbScraper as GA_CobbScraper
from scrapers.counties_ga.columbia import ColumbiaScraper as GA_ColumbiaScraper
from scrapers.counties_ga.coweta import CowetaScraper as GA_CowetaScraper
from scrapers.counties_ga.crawford import CrawfordScraper as GA_CrawfordScraper
from scrapers.counties_ga.dawson import DawsonScraper as GA_DawsonScraper
from scrapers.counties_ga.decatur import DecaturScraper as GA_DecaturScraper
from scrapers.counties_ga.dekalb import DeKalbScraper as GA_DeKalbScraper
from scrapers.counties_ga.dodge import DodgeScraper as GA_DodgeScraper
from scrapers.counties_ga.dougherty import DoughertyScraper as GA_DoughertyScraper
from scrapers.counties_ga.douglas import DouglasScraper as GA_DouglasScraper
from scrapers.counties_ga.echols import EcholsScraper as GA_EcholsScraper
from scrapers.counties_ga.emanuel import EmanuelScraper as GA_EmanuelScraper
from scrapers.counties_ga.fayette import FayetteScraper as GA_FayetteScraper
from scrapers.counties_ga.floyd import FloydScraper as GA_FloydScraper
from scrapers.counties_ga.forsyth import ForsythScraper as GA_ForsythScraper
from scrapers.counties_ga.fulton import FultonScraper as GA_FultonScraper
from scrapers.counties_ga.glynn import GlynnScraper as GA_GlynnScraper
from scrapers.counties_ga.grady import GradyScraper as GA_GradyScraper
from scrapers.counties_ga.gwinnett import GwinnettScraper as GA_GwinnettScraper
from scrapers.counties_ga.habersham import HabershamScraper as GA_HabershamScraper
from scrapers.counties_ga.hall import HallScraper as GA_HallScraper
from scrapers.counties_ga.hancock import HancockScraper as GA_HancockScraper
from scrapers.counties_ga.haralson import HaralsonScraper as GA_HaralsonScraper
from scrapers.counties_ga.heard import HeardScraper as GA_HeardScraper
from scrapers.counties_ga.henry import HenryScraper as GA_HenryScraper
from scrapers.counties_ga.houston import HoustonScraper as GA_HoustonScraper
from scrapers.counties_ga.jasper import JasperScraper as GA_JasperScraper
from scrapers.counties_ga.johnson import JohnsonScraper as GA_JohnsonScraper
from scrapers.counties_ga.jones import JonesScraper as GA_JonesScraper
from scrapers.counties_ga.lee import LeeScraper as GA_LeeScraper
from scrapers.counties_ga.liberty import LibertyScraper as GA_LibertyScraper
from scrapers.counties_ga.lowndes import LowndesScraper as GA_LowndesScraper
from scrapers.counties_ga.lumpkin import LumpkinScraper as GA_LumpkinScraper
from scrapers.counties_ga.macon import MaconScraper as GA_MaconScraper
from scrapers.counties_ga.mcintosh import McIntoshScraper as GA_McIntoshScraper
from scrapers.counties_ga.miller import MillerScraper as GA_MillerScraper
from scrapers.counties_ga.murray import MurrayScraper as GA_MurrayScraper
from scrapers.counties_ga.muscogee import MuscogeeScraper as GA_MuscogeeScraper
from scrapers.counties_ga.oconee import OconeeScraper as GA_OconeeScraper
from scrapers.counties_ga.oglethorpe import OglethorpeScraper as GA_OglethorpeScraper
from scrapers.counties_ga.paulding import PauldingScraper as GA_PauldingScraper
from scrapers.counties_ga.pickens import PickensScraper as GA_PickensScraper
from scrapers.counties_ga.polk import PolkScraper as GA_PolkScraper
from scrapers.counties_ga.pulaski import PulaskiScraper as GA_PulaskiScraper
from scrapers.counties_ga.putnam import PutnamScraper as GA_PutnamScraper
from scrapers.counties_ga.randolph import RandolphScraper as GA_RandolphScraper
from scrapers.counties_ga.richmond import RichmondScraper as GA_RichmondScraper
from scrapers.counties_ga.rockdale import RockdaleScraper as GA_RockdaleScraper
from scrapers.counties_ga.spalding import SpaldingScraper as GA_SpaldingScraper
from scrapers.counties_ga.sumter import SumterScraper as GA_SumterScraper
from scrapers.counties_ga.tattnall import TattnallScraper as GA_TattnallScraper
from scrapers.counties_ga.taylor import TaylorScraper as GA_TaylorScraper
from scrapers.counties_ga.thomas import ThomasScraper as GA_ThomasScraper
from scrapers.counties_ga.toombs import ToombsScraper as GA_ToombsScraper
from scrapers.counties_ga.treutlen import TreutlenScraper as GA_TreutlenScraper
from scrapers.counties_ga.troup import TroupScraper as GA_TroupScraper
from scrapers.counties_ga.twiggs import TwiggsScraper as GA_TwiggsScraper
from scrapers.counties_ga.upson import UpsonScraper as GA_UpsonScraper
from scrapers.counties_ga.walton import WaltonScraper as GA_WaltonScraper

# ── South Carolina Scrapers ────────────────────────────────────────────────
from scrapers.counties_sc.abbeville import AbbevilleScraper as SC_AbbevilleScraper
from scrapers.counties_sc.aiken import AikenScraper as SC_AikenScraper
from scrapers.counties_sc.allendale import AllendaleScraper as SC_AllendaleScraper
from scrapers.counties_sc.anderson import AndersonScraper as SC_AndersonScraper
from scrapers.counties_sc.bamberg import BambergScraper as SC_BambergScraper
from scrapers.counties_sc.barnwell import BarnwellScraper as SC_BarnwellScraper
from scrapers.counties_sc.beaufort import BeaufortScraper as SC_BeaufortScraper
from scrapers.counties_sc.berkeley import BerkeleyScraper as SC_BerkeleyScraper
from scrapers.counties_sc.calhoun import CalhounScraper as SC_CalhounScraper
from scrapers.counties_sc.charleston import CharlestonScraper as SC_CharlestonScraper
from scrapers.counties_sc.cherokee import CherokeeScraper as SC_CherokeeScraper
from scrapers.counties_sc.chester import ChesterScraper as SC_ChesterScraper
from scrapers.counties_sc.chesterfield import ChesterfieldScraper as SC_ChesterfieldScraper
from scrapers.counties_sc.clarendon import ClarendonScraper as SC_ClarendonScraper
from scrapers.counties_sc.colleton import ColletonScraper as SC_ColletonScraper
from scrapers.counties_sc.darlington import DarlingtonScraper as SC_DarlingtonScraper
from scrapers.counties_sc.dillon import DillonScraper as SC_DillonScraper
from scrapers.counties_sc.dorchester import DorchesterScraper as SC_DorchesterScraper
from scrapers.counties_sc.edgefield import EdgefieldScraper as SC_EdgefieldScraper
from scrapers.counties_sc.fairfield import FairfieldScraper as SC_FairfieldScraper
from scrapers.counties_sc.florence import FlorenceScraper as SC_FlorenceScraper
from scrapers.counties_sc.georgetown import GeorgetownScraper as SC_GeorgetownScraper
from scrapers.counties_sc.greenville import GreenvilleScraper as SC_GreenvilleScraper
from scrapers.counties_sc.greenwood import GreenwoodScraper as SC_GreenwoodScraper
from scrapers.counties_sc.hampton import HamptonScraper as SC_HamptonScraper
from scrapers.counties_sc.horry import HorryScraper as SC_HorryScraper
from scrapers.counties_sc.jasper import JasperScraper as SC_JasperScraper
from scrapers.counties_sc.kershaw import KershawScraper as SC_KershawScraper
from scrapers.counties_sc.lancaster import LancasterScraper as SC_LancasterScraper
from scrapers.counties_sc.laurens import LaurensScraper as SC_LaurensScraper
from scrapers.counties_sc.lee import LeeScraper as SC_LeeScraper
from scrapers.counties_sc.lexington import LexingtonScraper as SC_LexingtonScraper
from scrapers.counties_sc.marion import MarionScraper as SC_MarionScraper
from scrapers.counties_sc.marlboro import MarlboroScraper as SC_MarlboroScraper
from scrapers.counties_sc.mccormick import McCormickScraper as SC_McCormickScraper
from scrapers.counties_sc.newberry import NewberryScraper as SC_NewberryScraper
from scrapers.counties_sc.oconee import OconeeScraper as SC_OconeeScraper
from scrapers.counties_sc.orangeburg import OrangeburgScraper as SC_OrangeburgScraper
from scrapers.counties_sc.pickens import PickensScraper as SC_PickensScraper
from scrapers.counties_sc.richland import RichlandScraper as SC_RichlandScraper
from scrapers.counties_sc.saluda import SaludaScraper as SC_SaludaScraper
from scrapers.counties_sc.spartanburg import SpartanburgScraper as SC_SpartanburgScraper
from scrapers.counties_sc.sumter import SumterScraper as SC_SumterScraper
from scrapers.counties_sc.union import UnionScraper as SC_UnionScraper
from scrapers.counties_sc.williamsburg import WilliamsburgScraper as SC_WilliamsburgScraper
from scrapers.counties_sc.york import YorkScraper as SC_YorkScraper

# ── North Carolina Scrapers ────────────────────────────────────────────────
from scrapers.counties_nc.alamance import AlamanceScraper as NC_AlamanceScraper
from scrapers.counties_nc.anson import AnsonScraper as NC_AnsonScraper
from scrapers.counties_nc.brunswick import BrunswickScraper as NC_BrunswickScraper
from scrapers.counties_nc.cabarrus import CabarrusScraper as NC_CabarrusScraper
from scrapers.counties_nc.cleveland import ClevelandScraper as NC_ClevelandScraper
from scrapers.counties_nc.davidson import DavidsonScraper as NC_DavidsonScraper
from scrapers.counties_nc.davie import DavieScraper as NC_DavieScraper
from scrapers.counties_nc.duplin import DuplinScraper as NC_DuplinScraper
from scrapers.counties_nc.durham import DurhamScraper as NC_DurhamScraper
from scrapers.counties_nc.edgecombe import EdgecombeScraper as NC_EdgecombeScraper
from scrapers.counties_nc.gaston import GastonScraper as NC_GastonScraper
from scrapers.counties_nc.harnett import HarnettScraper as NC_HarnettScraper
from scrapers.counties_nc.henderson import HendersonScraper as NC_HendersonScraper
from scrapers.counties_nc.hoke import HokeScraper as NC_HokeScraper
from scrapers.counties_nc.iredell import IredellScraper as NC_IredellScraper
from scrapers.counties_nc.lincoln import LincolnScraper as NC_LincolnScraper
from scrapers.counties_nc.mecklenburg import MecklenburgScraper as NC_MecklenburgScraper
from scrapers.counties_nc.new_hanover import NewHanoverScraper as NC_NewHanoverScraper
from scrapers.counties_nc.pender import PenderScraper as NC_PenderScraper
from scrapers.counties_nc.polk import PolkScraper as NC_PolkScraper
from scrapers.counties_nc.rutherford import RutherfordScraper as NC_RutherfordScraper
from scrapers.counties_nc.sampson import SampsonScraper as NC_SampsonScraper
from scrapers.counties_nc.scotland import ScotlandScraper as NC_ScotlandScraper
from scrapers.counties_nc.stokes import StokesScraper as NC_StokesScraper
from scrapers.counties_nc.surry import SurryScraper as NC_SurryScraper
from scrapers.counties_nc.transylvania import TransylvaniaScraper as NC_TransylvaniaScraper
from scrapers.counties_nc.union import UnionScraper as NC_UnionScraper

# ── Tennessee Scrapers ─────────────────────────────────────────────────────
from scrapers.counties_tn.davidson import DavidsonScraper as TN_DavidsonScraper
from scrapers.counties_tn.shelby import ShelbyScraper as TN_ShelbyScraper
from scrapers.counties_tn.knox import KnoxScraper as TN_KnoxScraper
from scrapers.counties_tn.tncis import TnCISScraper as TN_TnCISScraper

# ── Texas Scrapers ─────────────────────────────────────────────────────────
from scrapers.counties_tx.harris import HarrisScraper as TX_HarrisScraper
from scrapers.counties_tx.dallas import DallasScraper as TX_DallasScraper
from scrapers.counties_tx.bexar import BexarScraper as TX_BexarScraper

# ── Louisiana Scrapers ─────────────────────────────────────────────────────
from scrapers.counties_la.orleans import OrleansScraper as LA_OrleansScraper
from scrapers.counties_la.lafayette import LafayetteScraper as LA_LafayetteScraper

# ── Alabama Scrapers ───────────────────────────────────────────────────────
from scrapers.counties_al.jefferson import JeffersonScraper as AL_JeffersonScraper
from scrapers.counties_al.madison import MadisonScraper as AL_MadisonScraper
from scrapers.counties_al.mobile import MobileScraper as AL_MobileScraper

# ── Connecticut Scrapers ───────────────────────────────────────────────────
from scrapers.counties_ct.statewide_docket import CTStatewideDockerScraper as CT_StatewideScraper

# ── Mississippi Scrapers ───────────────────────────────────────────────────
from scrapers.counties_ms.hinds import HindsScraper as MS_HindsScraper
from scrapers.counties_ms.jackson import JacksonScraper as MS_JacksonScraper

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("shamrock-leads")
scheduler = None
_fa_watcher = None


def build_writers():
    writers = []
    if settings.ENABLE_MONGO_WRITER and settings.mongo_configured():
        try:
            writers.append(MongoWriter())
            logger.info("MongoDB writer initialized")
        except Exception as e:
            logger.error(f"MongoDB writer failed: {e}")
    if getattr(settings, "ENABLE_SHEETS_WRITER", False) and settings.sheets_configured():
        try:
            from writers.sheets_writer import SheetsWriter
            writers.append(SheetsWriter(
                spreadsheet_id=settings.GOOGLE_SPREADSHEET_ID,
                credentials_path=settings.GOOGLE_APPLICATION_CREDENTIALS,
            ))
            logger.info("Sheets writer initialized")
        except Exception as e:
            logger.error(f"Sheets writer failed: {e}")
    if not writers:
        logger.warning("No writers configured!")
    return writers


def register_scrapers(sched):
    """Register FL + GA + SC + NC + TN + TX + LA + AL + CT + MS scrapers with the scheduler."""

    # ── SWFL Core ─────────────────────────────────────────────────────────────
    sched.register_scraper(LeeCountyScraper(), interval_minutes=43)
    sched.register_scraper(CollierCountyScraper(), interval_minutes=75)
    sched.register_scraper(CharlotteCountyScraper(), interval_minutes=90)
    sched.register_scraper(ManateeCountyScraper(), interval_minutes=75)
    sched.register_scraper(SarasotaCountyScraper(), interval_minutes=90)
    sched.register_scraper(DeSotoCountyScraper(), interval_minutes=180)
    sched.register_scraper(HendryCountyScraper(), interval_minutes=120)

    # ── Tampa Bay / Central FL ────────────────────────────────────────────────
    sched.register_scraper(HillsboroughCountyScraper(), interval_minutes=90)
    sched.register_scraper(PinellasCountyScraper(), interval_minutes=90)
    sched.register_scraper(SeminoleCountyScraper(), interval_minutes=90)
    sched.register_scraper(OrangeCountyScraper(), interval_minutes=90)
    sched.register_scraper(PascoCountyScraper(), interval_minutes=90)
    sched.register_scraper(LakeCountyScraper(), interval_minutes=90)
    sched.register_scraper(HernandoCountyScraper(), interval_minutes=120)
    sched.register_scraper(PolkCountyScraper(), interval_minutes=120)
    sched.register_scraper(OsceolaCountyScraper(), interval_minutes=120)
    sched.register_scraper(CitrusCountyScraper(), interval_minutes=120)
    sched.register_scraper(SumterCountyScraper(), interval_minutes=180)

    # ── South FL / Metro ──────────────────────────────────────────────────────
    sched.register_scraper(BrowardCountyScraper(), interval_minutes=60)
    sched.register_scraper(PalmBeachCountyScraper(), interval_minutes=120)
    sched.register_scraper(MartinCountyScraper(), interval_minutes=120)
    sched.register_scraper(StLucieCountyScraper(), interval_minutes=90)
    sched.register_scraper(IndianRiverCountyScraper(), interval_minutes=180)
    sched.register_scraper(HighlandsCountyScraper(), interval_minutes=120)
    sched.register_scraper(GladesCountyScraper(), interval_minutes=360)

    # ── North Central FL ──────────────────────────────────────────────────────
    sched.register_scraper(VolusiaCountyScraper(), interval_minutes=90)
    sched.register_scraper(BrevardCountyScraper(), interval_minutes=120)
    sched.register_scraper(AlachuaCountyScraper(), interval_minutes=90)
    # Marion disabled — datacenter IP blocked
    sched.register_scraper(PutnamCountyScraper(), interval_minutes=180)

    # ── Panhandle / NW FL + Miami ─────────────────────────────────────────────
    sched.register_scraper(EscambiaCountyScraper(), interval_minutes=120)
    sched.register_scraper(MiamiDadeCountyScraper(), interval_minutes=60)
    sched.register_scraper(OkaloosaCountyScraper(), interval_minutes=120)
    sched.register_scraper(BayCountyScraper(), interval_minutes=120)
    # Leon disabled — target 500 errors

    # ── NE FL / First Coast ───────────────────────────────────────────────────
    sched.register_scraper(DuvalCountyScraper(), interval_minutes=90)
    sched.register_scraper(StJohnsCountyScraper(), interval_minutes=120)

    # ── North FL / Rural ──────────────────────────────────────────────────────
    sched.register_scraper(TaylorCountyScraper(), interval_minutes=240)
    sched.register_scraper(DixieCountyScraper(), interval_minutes=240)

    # ── Phase 1 expansion ─────────────────────────────────────────────────────
    sched.register_scraper(FlaglerCountyScraper(), interval_minutes=120)
    sched.register_scraper(NassauCountyScraper(), interval_minutes=120)
    sched.register_scraper(ClayCountyScraper(), interval_minutes=120)
    sched.register_scraper(ColumbiaCountyScraper(), interval_minutes=120)
    sched.register_scraper(SuwanneeCountyScraper(), interval_minutes=180)
    sched.register_scraper(SantaRosaCountyScraper(), interval_minutes=120)
    sched.register_scraper(WaltonCountyScraper(), interval_minutes=120)
    sched.register_scraper(JacksonCountyScraper(), interval_minutes=360)
    sched.register_scraper(GadsdenCountyScraper(), interval_minutes=180)
    sched.register_scraper(MonroeCountyScraper(), interval_minutes=120)
    sched.register_scraper(OkeechobeeCountyScraper(), interval_minutes=120)
    sched.register_scraper(HardeeCountyScraper(), interval_minutes=120)

    # ── Georgia ──────────────────────────────────────────────────────────────
    sched.register_scraper(GA_BaconScraper(), interval_minutes=120)
    sched.register_scraper(GA_BakerScraper(), interval_minutes=120)
    sched.register_scraper(GA_BanksScraper(), interval_minutes=120)
    sched.register_scraper(GA_BarrowScraper(), interval_minutes=60)
    sched.register_scraper(GA_BartowScraper(), interval_minutes=60)
    sched.register_scraper(GA_BibbScraper(), interval_minutes=120)
    sched.register_scraper(GA_BrantleyScraper(), interval_minutes=120)
    sched.register_scraper(GA_BryanScraper(), interval_minutes=120)
    sched.register_scraper(GA_BullochScraper(), interval_minutes=120)
    sched.register_scraper(GA_CamdenScraper(), interval_minutes=60)
    sched.register_scraper(GA_CarrollScraper(), interval_minutes=120)
    sched.register_scraper(GA_CatoosaScraper(), interval_minutes=60)
    sched.register_scraper(GA_ChathamScraper(), interval_minutes=30)
    sched.register_scraper(GA_CherokeeScraper(), interval_minutes=120)
    sched.register_scraper(GA_ClarkeScraper(), interval_minutes=120)
    sched.register_scraper(GA_CobbScraper(), interval_minutes=60)
    sched.register_scraper(GA_ColumbiaScraper(), interval_minutes=60)
    sched.register_scraper(GA_CowetaScraper(), interval_minutes=60)
    sched.register_scraper(GA_CrawfordScraper(), interval_minutes=120)
    sched.register_scraper(GA_DawsonScraper(), interval_minutes=120)
    sched.register_scraper(GA_DecaturScraper(), interval_minutes=120)
    sched.register_scraper(GA_DeKalbScraper(), interval_minutes=60)
    sched.register_scraper(GA_DodgeScraper(), interval_minutes=120)
    sched.register_scraper(GA_DoughertyScraper(), interval_minutes=60)
    sched.register_scraper(GA_DouglasScraper(), interval_minutes=60)
    sched.register_scraper(GA_EcholsScraper(), interval_minutes=60)
    sched.register_scraper(GA_EmanuelScraper(), interval_minutes=120)
    sched.register_scraper(GA_FayetteScraper(), interval_minutes=120)
    sched.register_scraper(GA_FloydScraper(), interval_minutes=60)
    sched.register_scraper(GA_ForsythScraper(), interval_minutes=30)
    sched.register_scraper(GA_FultonScraper(), interval_minutes=30)
    sched.register_scraper(GA_GlynnScraper(), interval_minutes=60)
    sched.register_scraper(GA_GradyScraper(), interval_minutes=120)
    sched.register_scraper(GA_GwinnettScraper(), interval_minutes=30)
    sched.register_scraper(GA_HabershamScraper(), interval_minutes=120)
    sched.register_scraper(GA_HallScraper(), interval_minutes=30)
    sched.register_scraper(GA_HancockScraper(), interval_minutes=120)
    sched.register_scraper(GA_HaralsonScraper(), interval_minutes=120)
    sched.register_scraper(GA_HeardScraper(), interval_minutes=120)
    sched.register_scraper(GA_HenryScraper(), interval_minutes=60)
    sched.register_scraper(GA_HoustonScraper(), interval_minutes=60)
    sched.register_scraper(GA_JasperScraper(), interval_minutes=120)
    sched.register_scraper(GA_JohnsonScraper(), interval_minutes=120)
    sched.register_scraper(GA_JonesScraper(), interval_minutes=120)
    sched.register_scraper(GA_LeeScraper(), interval_minutes=120)
    sched.register_scraper(GA_LibertyScraper(), interval_minutes=120)
    sched.register_scraper(GA_LowndesScraper(), interval_minutes=60)
    sched.register_scraper(GA_LumpkinScraper(), interval_minutes=120)
    sched.register_scraper(GA_MaconScraper(), interval_minutes=60)
    sched.register_scraper(GA_McIntoshScraper(), interval_minutes=120)
    sched.register_scraper(GA_MillerScraper(), interval_minutes=120)
    sched.register_scraper(GA_MurrayScraper(), interval_minutes=120)
    sched.register_scraper(GA_MuscogeeScraper(), interval_minutes=60)
    sched.register_scraper(GA_OconeeScraper(), interval_minutes=120)
    sched.register_scraper(GA_OglethorpeScraper(), interval_minutes=120)
    sched.register_scraper(GA_PauldingScraper(), interval_minutes=60)
    sched.register_scraper(GA_PickensScraper(), interval_minutes=120)
    sched.register_scraper(GA_PolkScraper(), interval_minutes=120)
    sched.register_scraper(GA_PulaskiScraper(), interval_minutes=120)
    sched.register_scraper(GA_PutnamScraper(), interval_minutes=120)
    sched.register_scraper(GA_RandolphScraper(), interval_minutes=120)
    sched.register_scraper(GA_RichmondScraper(), interval_minutes=60)
    sched.register_scraper(GA_RockdaleScraper(), interval_minutes=60)
    sched.register_scraper(GA_SpaldingScraper(), interval_minutes=60)
    sched.register_scraper(GA_SumterScraper(), interval_minutes=120)
    sched.register_scraper(GA_TattnallScraper(), interval_minutes=120)
    sched.register_scraper(GA_TaylorScraper(), interval_minutes=120)
    sched.register_scraper(GA_ThomasScraper(), interval_minutes=120)
    sched.register_scraper(GA_ToombsScraper(), interval_minutes=120)
    sched.register_scraper(GA_TreutlenScraper(), interval_minutes=120)
    sched.register_scraper(GA_TroupScraper(), interval_minutes=120)
    sched.register_scraper(GA_TwiggsScraper(), interval_minutes=120)
    sched.register_scraper(GA_UpsonScraper(), interval_minutes=120)
    sched.register_scraper(GA_WaltonScraper(), interval_minutes=30)

    from apscheduler.triggers.interval import IntervalTrigger
    sched.scheduler.add_job(
        run_eas_batch,
        trigger=IntervalTrigger(minutes=60),
        id="eas_batch_georgia",
        name="EAS Batch Runner (27 GA Counties)",
        replace_existing=True,
        misfire_grace_time=600,
    )

    # ── South Carolina ───────────────────────────────────────────────────────
    sched.register_scraper(SC_AbbevilleScraper(), interval_minutes=120)
    sched.register_scraper(SC_AikenScraper(), interval_minutes=60)
    sched.register_scraper(SC_AllendaleScraper(), interval_minutes=120)
    sched.register_scraper(SC_AndersonScraper(), interval_minutes=60)
    sched.register_scraper(SC_BambergScraper(), interval_minutes=120)
    sched.register_scraper(SC_BarnwellScraper(), interval_minutes=120)
    sched.register_scraper(SC_BeaufortScraper(), interval_minutes=60)
    sched.register_scraper(SC_BerkeleyScraper(), interval_minutes=60)
    sched.register_scraper(SC_CalhounScraper(), interval_minutes=120)
    sched.register_scraper(SC_CharlestonScraper(), interval_minutes=60)
    sched.register_scraper(SC_CherokeeScraper(), interval_minutes=120)
    sched.register_scraper(SC_ChesterScraper(), interval_minutes=120)
    sched.register_scraper(SC_ChesterfieldScraper(), interval_minutes=120)
    sched.register_scraper(SC_ClarendonScraper(), interval_minutes=120)
    sched.register_scraper(SC_ColletonScraper(), interval_minutes=120)
    sched.register_scraper(SC_DarlingtonScraper(), interval_minutes=120)
    sched.register_scraper(SC_DillonScraper(), interval_minutes=120)
    sched.register_scraper(SC_DorchesterScraper(), interval_minutes=60)
    sched.register_scraper(SC_EdgefieldScraper(), interval_minutes=120)
    sched.register_scraper(SC_FairfieldScraper(), interval_minutes=120)
    sched.register_scraper(SC_FlorenceScraper(), interval_minutes=60)
    sched.register_scraper(SC_GeorgetownScraper(), interval_minutes=120)
    sched.register_scraper(SC_GreenvilleScraper(), interval_minutes=60)
    sched.register_scraper(SC_GreenwoodScraper(), interval_minutes=120)
    sched.register_scraper(SC_HamptonScraper(), interval_minutes=120)
    sched.register_scraper(SC_HorryScraper(), interval_minutes=60)
    sched.register_scraper(SC_JasperScraper(), interval_minutes=60)
    sched.register_scraper(SC_KershawScraper(), interval_minutes=120)
    sched.register_scraper(SC_LancasterScraper(), interval_minutes=120)
    sched.register_scraper(SC_LaurensScraper(), interval_minutes=120)
    sched.register_scraper(SC_LeeScraper(), interval_minutes=120)
    sched.register_scraper(SC_LexingtonScraper(), interval_minutes=60)
    sched.register_scraper(SC_MarionScraper(), interval_minutes=120)
    sched.register_scraper(SC_MarlboroScraper(), interval_minutes=120)
    sched.register_scraper(SC_McCormickScraper(), interval_minutes=120)
    sched.register_scraper(SC_NewberryScraper(), interval_minutes=120)
    sched.register_scraper(SC_OconeeScraper(), interval_minutes=120)
    sched.register_scraper(SC_OrangeburgScraper(), interval_minutes=120)
    sched.register_scraper(SC_PickensScraper(), interval_minutes=120)
    sched.register_scraper(SC_RichlandScraper(), interval_minutes=60)
    sched.register_scraper(SC_SaludaScraper(), interval_minutes=120)
    sched.register_scraper(SC_SpartanburgScraper(), interval_minutes=120)
    sched.register_scraper(SC_SumterScraper(), interval_minutes=60)
    sched.register_scraper(SC_UnionScraper(), interval_minutes=120)
    sched.register_scraper(SC_WilliamsburgScraper(), interval_minutes=120)
    sched.register_scraper(SC_YorkScraper(), interval_minutes=60)

    # ── North Carolina ───────────────────────────────────────────────────────
    sched.register_scraper(NC_AlamanceScraper(), interval_minutes=60)
    sched.register_scraper(NC_AnsonScraper(), interval_minutes=120)
    sched.register_scraper(NC_BrunswickScraper(), interval_minutes=120)
    sched.register_scraper(NC_CabarrusScraper(), interval_minutes=60)
    sched.register_scraper(NC_ClevelandScraper(), interval_minutes=120)
    sched.register_scraper(NC_DavidsonScraper(), interval_minutes=60)
    sched.register_scraper(NC_DavieScraper(), interval_minutes=120)
    sched.register_scraper(NC_DuplinScraper(), interval_minutes=120)
    sched.register_scraper(NC_DurhamScraper(), interval_minutes=60)
    sched.register_scraper(NC_EdgecombeScraper(), interval_minutes=120)
    sched.register_scraper(NC_GastonScraper(), interval_minutes=60)
    sched.register_scraper(NC_HarnettScraper(), interval_minutes=60)
    sched.register_scraper(NC_HendersonScraper(), interval_minutes=120)
    sched.register_scraper(NC_HokeScraper(), interval_minutes=120)
    sched.register_scraper(NC_IredellScraper(), interval_minutes=60)
    sched.register_scraper(NC_LincolnScraper(), interval_minutes=120)
    sched.register_scraper(NC_MecklenburgScraper(), interval_minutes=60)
    sched.register_scraper(NC_NewHanoverScraper(), interval_minutes=60)
    sched.register_scraper(NC_PenderScraper(), interval_minutes=120)
    sched.register_scraper(NC_PolkScraper(), interval_minutes=120)
    sched.register_scraper(NC_RutherfordScraper(), interval_minutes=120)
    sched.register_scraper(NC_SampsonScraper(), interval_minutes=120)
    sched.register_scraper(NC_ScotlandScraper(), interval_minutes=120)
    sched.register_scraper(NC_StokesScraper(), interval_minutes=120)
    sched.register_scraper(NC_SurryScraper(), interval_minutes=120)
    sched.register_scraper(NC_TransylvaniaScraper(), interval_minutes=120)
    sched.register_scraper(NC_UnionScraper(), interval_minutes=60)

    # ── Tennessee (wave-1 + TnCIS statewide) ─────────────────────────────────
    sched.register_scraper(TN_DavidsonScraper(), interval_minutes=60)
    sched.register_scraper(TN_ShelbyScraper(), interval_minutes=90)
    sched.register_scraper(TN_KnoxScraper(), interval_minutes=90)
    sched.register_scraper(TN_TnCISScraper(), interval_minutes=180)

    # ── Texas (wave-1) ───────────────────────────────────────────────────────
    sched.register_scraper(TX_HarrisScraper(), interval_minutes=90)
    sched.register_scraper(TX_DallasScraper(), interval_minutes=90)
    sched.register_scraper(TX_BexarScraper(), interval_minutes=60)

    # ── Louisiana (wave-1) ───────────────────────────────────────────────────
    sched.register_scraper(LA_OrleansScraper(), interval_minutes=90)
    sched.register_scraper(LA_LafayetteScraper(), interval_minutes=90)

    # ── Alabama (wave-1) ─────────────────────────────────────────────────────
    sched.register_scraper(AL_JeffersonScraper(), interval_minutes=120)
    sched.register_scraper(AL_MadisonScraper(), interval_minutes=120)
    sched.register_scraper(AL_MobileScraper(), interval_minutes=120)

    # ── Connecticut (wave-1) ─────────────────────────────────────────────────
    sched.register_scraper(CT_StatewideScraper(), interval_minutes=180)

    # ── Mississippi (wave-1) ─────────────────────────────────────────────────
    sched.register_scraper(MS_HindsScraper(), interval_minutes=90)
    sched.register_scraper(MS_JacksonScraper(), interval_minutes=120)

def handle_shutdown(signum, frame):
    logger.info("Shutdown signal received")
    if scheduler:
        scheduler.stop()
    if _fa_watcher:
        try:
            _fa_watcher.close()
        except Exception:
            pass
    sys.exit(0)


def _run_scheduled_cleanup():
    logger.info("🧹 Running scheduled data cleanup...")
    try:
        logger.info(f"🧹 Cleanup complete: {run_cleanup()}")
    except Exception as e:
        logger.error(f"🧹 Cleanup failed: {e}")


def _run_first_appearance_watcher():
    if _fa_watcher is None:
        return
    try:
        stats = _fa_watcher.run()
        if stats.get("bond_set", 0) > 0:
            logger.info(f"🔔 FirstAppearanceWatcher: {stats['bond_set']} bond(s) set this cycle")
    except Exception as e:
        logger.error(f"FirstAppearanceWatcher run failed: {e}")


def main():
    global scheduler, _fa_watcher
    logger.info("=" * 60)
    logger.info("ShamrockLeads - Multi-State Arrest Intelligence Platform")
    logger.info("=" * 60)
    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)
    writers = build_writers()
    scheduler = ScraperScheduler()
    scheduler.set_writers(writers)
    register_scrapers(scheduler)

    scraper_registry = {s.county: s for s in scheduler._scrapers.values()}
    _fa_watcher = FirstAppearanceWatcher(writers=writers, scraper_registry=scraper_registry)
    logger.info("🔔 FirstAppearanceWatcher initialized")

    from apscheduler.triggers.interval import IntervalTrigger
    from datetime import datetime, timezone, timedelta

    scheduler.scheduler.add_job(
        _run_scheduled_cleanup,
        trigger=IntervalTrigger(hours=6),
        id="maintenance_cleanup",
        name="Data Cleanup & Purge",
        replace_existing=True,
        misfire_grace_time=600,
    )
    scheduler.scheduler.add_job(
        _run_first_appearance_watcher,
        trigger=IntervalTrigger(minutes=30),
        id="first_appearance_watcher",
        name="First Appearance Bond Watcher",
        replace_existing=True,
        next_run_time=datetime.now(timezone.utc) + timedelta(minutes=5),
        misfire_grace_time=300,
    )
    logger.info(f"📋 Total scrapers registered: {len(scheduler._scrapers)}")

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

