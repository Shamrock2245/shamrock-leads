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
# ── Wave 2 — Full FL Coverage (67/67) ─────────────────────────────────────
from scrapers.counties.baker import BakerCountyScraper
from scrapers.counties.bradford import BradfordCountyScraper
from scrapers.counties.calhoun import CalhounCountyScraper
from scrapers.counties.franklin import FranklinCountyScraper
from scrapers.counties.gilchrist import GilchristCountyScraper
from scrapers.counties.gulf import GulfCountyScraper
from scrapers.counties.hamilton import HamiltonCountyScraper
from scrapers.counties.holmes import HolmesCountyScraper
from scrapers.counties.jefferson import JeffersonCountyScraper
from scrapers.counties.lafayette import LafayetteCountyScraper
from scrapers.counties.levy import LevyCountyScraper
from scrapers.counties.liberty import LibertyCountyScraper
from scrapers.counties.madison import MadisonCountyScraper
from scrapers.counties.union import UnionCountyScraper
from scrapers.counties.wakulla import WakullaCountyScraper
from scrapers.counties.washington import WashingtonCountyScraper

# ── Georgia Scrapers ───────────────────────────────────────────────────────
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
from scrapers.counties_ga.gordon import GordonScraper as GA_GordonScraper
from scrapers.counties_ga.walker import WalkerScraper as GA_WalkerScraper
from scrapers.counties_ga.whitfield import WhitfieldScraper as GA_WhitfieldScraper
from scrapers.counties_ga.tift import TiftScraper as GA_TiftScraper
from scrapers.counties_ga.ware import WareScraper as GA_WareScraper
from scrapers.counties_ga.coffee import CoffeeScraper as GA_CoffeeScraper
from scrapers.counties_ga.appling import ApplingScraper as GA_ApplingScraper
from scrapers.counties_ga.bleckley import BleckleyScraper as GA_BleckleyScraper
from scrapers.counties_ga.crisp import CrispScraper as GA_CrispScraper
from scrapers.counties_ga.laurens import LaurensScraper as GA_LaurensScraper
from scrapers.counties_ga.effingham import EffinghamScraper as GA_EffinghamScraper

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
from scrapers.counties_nc.wake import WakeScraper as NC_WakeScraper
from scrapers.counties_nc.guilford import GuilfordScraper as NC_GuilfordScraper
from scrapers.counties_nc.forsyth import ForsythScraper as NC_ForsythScraper
from scrapers.counties_nc.cumberland import CumberlandScraper as NC_CumberlandScraper
from scrapers.counties_nc.buncombe import BuncombeScraper as NC_BuncombeScraper
from scrapers.counties_nc.johnston import JohnstonScraper as NC_JohnstonScraper
from scrapers.counties_nc.onslow import OnslowScraper as NC_OnslowScraper
# Wave-4 NC: DCN cluster + Pitt metro
from scrapers.counties_nc.moore import MooreScraper as NC_MooreScraper
from scrapers.counties_nc.lee import LeeScraper as NC_LeeScraper
from scrapers.counties_nc.halifax import HalifaxScraper as NC_HalifaxScraper
from scrapers.counties_nc.richmond import RichmondScraper as NC_RichmondScraper
from scrapers.counties_nc.pitt import PittScraper as NC_PittScraper
from scrapers.counties_nc.craven import CravenScraper as NC_CravenScraper
from scrapers.counties_nc.randolph import RandolphScraper as NC_RandolphScraper
# Wave-6 NC mid-market
from scrapers.counties_nc.catawba import CatawbaScraper as NC_CatawbaScraper
from scrapers.counties_nc.carteret import CarteretScraper as NC_CarteretScraper
from scrapers.counties_nc.caldwell import CaldwellScraper as NC_CaldwellScraper
# Wave-7 NC: OCV + Orange PDF
from scrapers.counties_nc.chatham import ChathamScraper as NC_ChathamScraper
from scrapers.counties_nc.stanly import StanlyScraper as NC_StanlyScraper
from scrapers.counties_nc.orange import OrangeScraper as NC_OrangeScraper
from scrapers.counties_nc.rowan import RowanScraper as NC_RowanScraper
from scrapers.counties_nc.robeson import RobesonScraper as NC_RobesonScraper
from scrapers.counties_nc.wayne import WayneScraper as NC_WayneScraper
from scrapers.counties_nc.wilkes import WilkesScraper as NC_WilkesScraper
from scrapers.counties_nc.nash import NashScraper as NC_NashScraper
from scrapers.counties_nc.vance import VanceScraper as NC_VanceScraper
from scrapers.counties_nc.rockingham import RockinghamScraper as NC_RockinghamScraper
from scrapers.counties_nc.granville import GranvilleScraper as NC_GranvilleScraper
from scrapers.counties_nc.person import PersonScraper as NC_PersonScraper
from scrapers.counties_nc.warren import WarrenScraper as NC_WarrenScraper
from scrapers.counties_nc.caswell import CaswellScraper as NC_CaswellScraper
from scrapers.counties_nc.chowan import ChowanScraper as NC_ChowanScraper
from scrapers.counties_nc.perquimans import PerquimansScraper as NC_PerquimansScraper

# ── Tennessee Scrapers ─────────────────────────────────────────────────────
from scrapers.counties_tn.davidson import DavidsonScraper as TN_DavidsonScraper
from scrapers.counties_tn.shelby import ShelbyScraper as TN_ShelbyScraper
from scrapers.counties_tn.knox import KnoxScraper as TN_KnoxScraper
from scrapers.counties_tn.tncis import TnCISScraper as TN_TnCISScraper
from scrapers.counties_tn.hamilton import HamiltonScraper as TN_HamiltonScraper
from scrapers.counties_tn.rutherford import RutherfordScraper as TN_RutherfordScraper
from scrapers.counties_tn.williamson import WilliamsonScraper as TN_WilliamsonScraper
from scrapers.counties_tn.montgomery import MontgomeryScraper as TN_MontgomeryScraper
from scrapers.counties_tn.sumner import SumnerScraper as TN_SumnerScraper
from scrapers.counties_tn.wilson import WilsonScraper as TN_WilsonScraper
from scrapers.counties_tn.bradley import BradleyScraper as TN_BradleyScraper
from scrapers.counties_tn.blount import BlountScraper as TN_BlountScraper
from scrapers.counties_tn.sevier import SevierScraper as TN_SevierScraper
from scrapers.counties_tn.washington import WashingtonScraper as TN_WashingtonScraper
from scrapers.counties_tn.maury import MauryScraper as TN_MauryScraper
from scrapers.counties_tn.robertson import RobertsonScraper as TN_RobertsonScraper
from scrapers.counties_tn.hamblen import HamblenScraper as TN_HamblenScraper
from scrapers.counties_tn.bedford import BedfordScraper as TN_BedfordScraper
from scrapers.counties_tn.coffee import CoffeeTNScraper as TN_CoffeeScraper
from scrapers.counties_tn.lincoln import LincolnTNScraper as TN_LincolnScraper
from scrapers.counties_tn.giles import GilesScraper as TN_GilesScraper
from scrapers.counties_tn.putnam import PutnamScraper as TN_PutnamScraper

# ── Texas Scrapers ─────────────────────────────────────────────────────────
from scrapers.counties_tx.harris import HarrisScraper as TX_HarrisScraper
from scrapers.counties_tx.dallas import DallasScraper as TX_DallasScraper
from scrapers.counties_tx.bexar import BexarScraper as TX_BexarScraper
from scrapers.counties_tx.tarrant import TarrantScraper as TX_TarrantScraper
from scrapers.counties_tx.travis import TravisScraper as TX_TravisScraper
from scrapers.counties_tx.collin import CollinScraper as TX_CollinScraper
from scrapers.counties_tx.denton import DentonScraper as TX_DentonScraper
from scrapers.counties_tx.fort_bend import FortBendScraper as TX_FortBendScraper
from scrapers.counties_tx.montgomery import MontgomeryScraper as TX_MontgomeryScraper
from scrapers.counties_tx.williamson import WilliamsonScraper as TX_WilliamsonScraper
from scrapers.counties_tx.el_paso import ElPasoScraper as TX_ElPasoScraper
from scrapers.counties_tx.hidalgo import HidalgoScraper as TX_HidalgoScraper
from scrapers.counties_tx.cameron import CameronScraper as TX_CameronScraper
from scrapers.counties_tx.brazoria import BrazoriaScraper as TX_BrazoriaScraper
from scrapers.counties_tx.galveston import GalvestonScraper as TX_GalvestonScraper
from scrapers.counties_tx.bell import BellScraper as TX_BellScraper
from scrapers.counties_tx.lubbock import LubbockScraper as TX_LubbockScraper
from scrapers.counties_tx.webb import WebbScraper as TX_WebbScraper
from scrapers.counties_tx.jefferson import JeffersonScraper as TX_JeffersonScraper
from scrapers.counties_tx.mclennan import McLennanScraper as TX_McLennanScraper
from scrapers.counties_tx.nueces import NuecesScraper as TX_NuecesScraper
from scrapers.counties_tx.brazos import BrazosScraper as TX_BrazosScraper
from scrapers.counties_tx.hays import HaysScraper as TX_HaysScraper
from scrapers.counties_tx.ellis import EllisScraper as TX_EllisScraper
from scrapers.counties_tx.johnson import JohnsonScraper as TX_JohnsonScraper
from scrapers.counties_tx.ector import EctorScraper as TX_EctorScraper
from scrapers.counties_tx.midland import MidlandScraper as TX_MidlandScraper
from scrapers.counties_tx.potter import PotterScraper as TX_PotterScraper
from scrapers.counties_tx.bastrop import BastropScraper as TX_BastropScraper
from scrapers.counties_tx.guadalupe import GuadalupeScraper as TX_GuadalupeScraper
from scrapers.counties_tx.comal import ComalScraper as TX_ComalScraper
from scrapers.counties_tx.victoria import VictoriaScraper as TX_VictoriaScraper
from scrapers.counties_tx.walker import WalkerTXScraper as TX_WalkerScraper

# ── Louisiana Scrapers ─────────────────────────────────────────────────────
from scrapers.counties_la.orleans import OrleansScraper as LA_OrleansScraper
from scrapers.counties_la.lafayette import LafayetteScraper as LA_LafayetteScraper
from scrapers.counties_la.jefferson import JeffersonScraper as LA_JeffersonScraper
from scrapers.counties_la.east_baton_rouge import EastBatonRougeScraper as LA_EastBatonRougeScraper
from scrapers.counties_la.caddo import CaddoScraper as LA_CaddoScraper
from scrapers.counties_la.calcasieu import CalcasieuScraper as LA_CalcasieuScraper
from scrapers.counties_la.ouachita import OuachitaScraper as LA_OuachitaScraper
from scrapers.counties_la.st_tammany import StTammanyScraper as LA_StTammanyScraper
from scrapers.counties_la.ascension import AscensionScraper as LA_AscensionScraper
from scrapers.counties_la.livingston import LivingstonScraper as LA_LivingstonScraper

# ── Alabama Scrapers ───────────────────────────────────────────────────────
from scrapers.counties_al.jefferson import JeffersonScraper as AL_JeffersonScraper
from scrapers.counties_al.madison import MadisonScraper as AL_MadisonScraper
from scrapers.counties_al.mobile import MobileScraper as AL_MobileScraper
from scrapers.counties_al.baldwin import BaldwinScraper as AL_BaldwinScraper
from scrapers.counties_al.tuscaloosa import TuscaloosaScraper as AL_TuscaloosaScraper
from scrapers.counties_al.shelby import ShelbyScraper as AL_ShelbyScraper
from scrapers.counties_al.montgomery import MontgomeryScraper as AL_MontgomeryScraper
from scrapers.counties_al.houston import HoustonScraper as AL_HoustonScraper
from scrapers.counties_al.morgan import MorganScraper as AL_MorganScraper
from scrapers.counties_al.etowah import EtowahScraper as AL_EtowahScraper
from scrapers.counties_al.cullman import CullmanScraper as AL_CullmanScraper
from scrapers.counties_al.dekalb import DeKalbALScraper as AL_DeKalbScraper
from scrapers.counties_al.jackson import JacksonALScraper as AL_JacksonScraper

# ── Connecticut Scrapers ───────────────────────────────────────────────────
from scrapers.counties_ct.statewide_docket import CTStatewideDockerScraper as CT_StatewideScraper
from scrapers.counties_ct.ct_doc import CTDOCInmateScraper as CT_DOCScraper
from scrapers.counties_ct.hartford import HartfordScraper as CT_HartfordScraper
from scrapers.counties_ct.bridgeport import BridgeportScraper as CT_BridgeportScraper
from scrapers.counties_ct.new_haven import NewHavenScraper as CT_NewHavenScraper
from scrapers.counties_ct.stamford import StamfordScraper as CT_StamfordScraper

# ── Mississippi Scrapers ───────────────────────────────────────────────────
from scrapers.counties_ms.hinds import HindsScraper as MS_HindsScraper
from scrapers.counties_ms.jackson import JacksonScraper as MS_JacksonScraper
from scrapers.counties_ms.harrison import HarrisonScraper as MS_HarrisonScraper
from scrapers.counties_ms.desoto import DeSotoScraper as MS_DeSotoScraper
from scrapers.counties_ms.rankin import RankinScraper as MS_RankinScraper
from scrapers.counties_ms.lauderdale import LauderdaleScraper as MS_LauderdaleScraper
from scrapers.counties_ms.forrest import ForrestScraper as MS_ForrestScraper
from scrapers.counties_ms.jones import JonesScraper as MS_JonesScraper
from scrapers.counties_ms.madison import MadisonMSScraper as MS_MadisonScraper

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

    # ── SWFL Core (KEY — must stay registered; Lee + Sarasota are non-negotiable) ─
    # Intervals kept aggressive for bond-desk coverage. Do not comment these out.
    sched.register_scraper(LeeCountyScraper(), interval_minutes=30)
    sched.register_scraper(SarasotaCountyScraper(), interval_minutes=60)
    sched.register_scraper(CollierCountyScraper(), interval_minutes=75)
    sched.register_scraper(CharlotteCountyScraper(), interval_minutes=90)
    sched.register_scraper(ManateeCountyScraper(), interval_minutes=75)
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
    # Marion re-enabled via Wave 2 below (residential egress required — AWS WAF)
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
    # ── Wave 2 — Full FL Coverage (67/67) ─────────────────────────────────────
    sched.register_scraper(LeonCountyScraper(), interval_minutes=90)
    sched.register_scraper(MarionCountyScraper(), interval_minutes=90)
    sched.register_scraper(BakerCountyScraper(), interval_minutes=180)
    sched.register_scraper(BradfordCountyScraper(), interval_minutes=180)
    sched.register_scraper(CalhounCountyScraper(), interval_minutes=240)
    sched.register_scraper(FranklinCountyScraper(), interval_minutes=360)
    sched.register_scraper(GilchristCountyScraper(), interval_minutes=240)
    sched.register_scraper(GulfCountyScraper(), interval_minutes=240)
    sched.register_scraper(HamiltonCountyScraper(), interval_minutes=360)
    sched.register_scraper(HolmesCountyScraper(), interval_minutes=240)
    sched.register_scraper(JeffersonCountyScraper(), interval_minutes=360)
    sched.register_scraper(LafayetteCountyScraper(), interval_minutes=360)
    sched.register_scraper(LevyCountyScraper(), interval_minutes=180)
    sched.register_scraper(LibertyCountyScraper(), interval_minutes=360)
    sched.register_scraper(MadisonCountyScraper(), interval_minutes=240)
    sched.register_scraper(UnionCountyScraper(), interval_minutes=360)
    sched.register_scraper(WakullaCountyScraper(), interval_minutes=180)
    sched.register_scraper(WashingtonCountyScraper(), interval_minutes=180)

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
    sched.register_scraper(GA_GordonScraper(), interval_minutes=90)
    sched.register_scraper(GA_WalkerScraper(), interval_minutes=90)
    sched.register_scraper(GA_WhitfieldScraper(), interval_minutes=90)
    sched.register_scraper(GA_TiftScraper(), interval_minutes=90)
    sched.register_scraper(GA_WareScraper(), interval_minutes=90)
    sched.register_scraper(GA_CoffeeScraper(), interval_minutes=90)
    sched.register_scraper(GA_ApplingScraper(), interval_minutes=120)
    sched.register_scraper(GA_BleckleyScraper(), interval_minutes=120)
    sched.register_scraper(GA_CrispScraper(), interval_minutes=120)
    sched.register_scraper(GA_LaurensScraper(), interval_minutes=120)
    sched.register_scraper(GA_EffinghamScraper(), interval_minutes=120)

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
    sched.register_scraper(NC_WakeScraper(), interval_minutes=60)
    sched.register_scraper(NC_GuilfordScraper(), interval_minutes=60)
    sched.register_scraper(NC_ForsythScraper(), interval_minutes=90)
    sched.register_scraper(NC_CumberlandScraper(), interval_minutes=90)
    # Wave-3 NC metros (Asheville / Smithfield / Jacksonville)
    sched.register_scraper(NC_BuncombeScraper(), interval_minutes=90)
    sched.register_scraper(NC_JohnstonScraper(), interval_minutes=60)
    sched.register_scraper(NC_OnslowScraper(), interval_minutes=90)
    # Wave-4 NC: DCN family (Moore/Lee/Halifax/Richmond) + Pitt metro
    sched.register_scraper(NC_MooreScraper(), interval_minutes=90)
    sched.register_scraper(NC_LeeScraper(), interval_minutes=90)
    sched.register_scraper(NC_HalifaxScraper(), interval_minutes=90)
    sched.register_scraper(NC_RichmondScraper(), interval_minutes=90)
    sched.register_scraper(NC_PittScraper(), interval_minutes=60)
    # Wave-5 NC: Craven ArcGIS + Randolph confined list
    sched.register_scraper(NC_CravenScraper(), interval_minutes=90)
    sched.register_scraper(NC_RandolphScraper(), interval_minutes=90)
    # Wave-6 NC: Catawba HTML + Carteret DCN + Caldwell PDF
    sched.register_scraper(NC_CatawbaScraper(), interval_minutes=60)
    sched.register_scraper(NC_CarteretScraper(), interval_minutes=90)
    sched.register_scraper(NC_CaldwellScraper(), interval_minutes=120)
    # Wave-7 NC: OCV + Orange daily PDF + Wave-8
    sched.register_scraper(NC_ChathamScraper(), interval_minutes=60)
    sched.register_scraper(NC_StanlyScraper(), interval_minutes=60)
    sched.register_scraper(NC_OrangeScraper(), interval_minutes=120)
    sched.register_scraper(NC_RowanScraper(), interval_minutes=90)
    sched.register_scraper(NC_RobesonScraper(), interval_minutes=90)
    sched.register_scraper(NC_WayneScraper(), interval_minutes=90)
    sched.register_scraper(NC_WilkesScraper(), interval_minutes=90)
    sched.register_scraper(NC_NashScraper(), interval_minutes=90)
    sched.register_scraper(NC_VanceScraper(), interval_minutes=90)
    sched.register_scraper(NC_RockinghamScraper(), interval_minutes=90)
    sched.register_scraper(NC_GranvilleScraper(), interval_minutes=120)
    sched.register_scraper(NC_PersonScraper(), interval_minutes=120)
    sched.register_scraper(NC_WarrenScraper(), interval_minutes=120)
    sched.register_scraper(NC_CaswellScraper(), interval_minutes=120)
    sched.register_scraper(NC_ChowanScraper(), interval_minutes=120)
    sched.register_scraper(NC_PerquimansScraper(), interval_minutes=120)

    # ── Tennessee (wave-1 + TnCIS statewide + wave-2 + wave-3 + wave-4 + wave-5) ─────────────────────────────────
    sched.register_scraper(TN_DavidsonScraper(), interval_minutes=60)
    sched.register_scraper(TN_ShelbyScraper(), interval_minutes=90)
    sched.register_scraper(TN_KnoxScraper(), interval_minutes=90)
    sched.register_scraper(TN_TnCISScraper(), interval_minutes=180)
    sched.register_scraper(TN_HamiltonScraper(), interval_minutes=60)
    sched.register_scraper(TN_RutherfordScraper(), interval_minutes=90)
    # Wave-3 TN metros (Franklin / Clarksville / Gallatin)
    sched.register_scraper(TN_WilliamsonScraper(), interval_minutes=90)
    sched.register_scraper(TN_MontgomeryScraper(), interval_minutes=60)
    sched.register_scraper(TN_SumnerScraper(), interval_minutes=90)
    sched.register_scraper(TN_WilsonScraper(), interval_minutes=90)
    sched.register_scraper(TN_BradleyScraper(), interval_minutes=90)
    sched.register_scraper(TN_BlountScraper(), interval_minutes=90)
    sched.register_scraper(TN_SevierScraper(), interval_minutes=90)
    sched.register_scraper(TN_WashingtonScraper(), interval_minutes=90)
    sched.register_scraper(TN_MauryScraper(), interval_minutes=90)
    sched.register_scraper(TN_RobertsonScraper(), interval_minutes=90)
    sched.register_scraper(TN_HamblenScraper(), interval_minutes=90)
    sched.register_scraper(TN_BedfordScraper(), interval_minutes=120)
    sched.register_scraper(TN_CoffeeScraper(), interval_minutes=120)
    sched.register_scraper(TN_LincolnScraper(), interval_minutes=120)
    sched.register_scraper(TN_GilesScraper(), interval_minutes=120)
    sched.register_scraper(TN_PutnamScraper(), interval_minutes=120)

    # ── Texas (wave-1 + wave-2 + wave-3 + wave-4 + wave-5 + wave-6) ──────────────────────────────────────────
    sched.register_scraper(TX_HarrisScraper(), interval_minutes=90)
    sched.register_scraper(TX_DallasScraper(), interval_minutes=90)
    sched.register_scraper(TX_BexarScraper(), interval_minutes=60)
    sched.register_scraper(TX_TarrantScraper(), interval_minutes=60)
    sched.register_scraper(TX_TravisScraper(), interval_minutes=60)
    sched.register_scraper(TX_CollinScraper(), interval_minutes=90)
    sched.register_scraper(TX_DentonScraper(), interval_minutes=60)
    sched.register_scraper(TX_FortBendScraper(), interval_minutes=90)
    sched.register_scraper(TX_MontgomeryScraper(), interval_minutes=90)
    sched.register_scraper(TX_WilliamsonScraper(), interval_minutes=90)
    sched.register_scraper(TX_ElPasoScraper(), interval_minutes=90)
    sched.register_scraper(TX_HidalgoScraper(), interval_minutes=90)
    # Wave-4 TX metros (RGV / Gulf Coast)
    sched.register_scraper(TX_CameronScraper(), interval_minutes=90)
    sched.register_scraper(TX_BrazoriaScraper(), interval_minutes=120)
    sched.register_scraper(TX_GalvestonScraper(), interval_minutes=90)
    sched.register_scraper(TX_BellScraper(), interval_minutes=90)
    sched.register_scraper(TX_LubbockScraper(), interval_minutes=90)
    sched.register_scraper(TX_WebbScraper(), interval_minutes=90)
    sched.register_scraper(TX_JeffersonScraper(), interval_minutes=90)
    sched.register_scraper(TX_McLennanScraper(), interval_minutes=90)
    sched.register_scraper(TX_NuecesScraper(), interval_minutes=90)
    sched.register_scraper(TX_BrazosScraper(), interval_minutes=90)
    sched.register_scraper(TX_HaysScraper(), interval_minutes=90)
    sched.register_scraper(TX_EllisScraper(), interval_minutes=90)
    sched.register_scraper(TX_JohnsonScraper(), interval_minutes=90)
    sched.register_scraper(TX_EctorScraper(), interval_minutes=90)
    sched.register_scraper(TX_MidlandScraper(), interval_minutes=90)
    sched.register_scraper(TX_PotterScraper(), interval_minutes=90)
    sched.register_scraper(TX_BastropScraper(), interval_minutes=120)
    sched.register_scraper(TX_GuadalupeScraper(), interval_minutes=120)
    sched.register_scraper(TX_ComalScraper(), interval_minutes=120)
    sched.register_scraper(TX_VictoriaScraper(), interval_minutes=120)
    sched.register_scraper(TX_WalkerScraper(), interval_minutes=120)

    # ── Louisiana (wave-1 + wave-2 + wave-3) ───────────────────────────────────────────────────
    sched.register_scraper(LA_OrleansScraper(), interval_minutes=90)
    sched.register_scraper(LA_LafayetteScraper(), interval_minutes=90)
    sched.register_scraper(LA_JeffersonScraper(), interval_minutes=60)
    sched.register_scraper(LA_EastBatonRougeScraper(), interval_minutes=90)
    sched.register_scraper(LA_CaddoScraper(), interval_minutes=90)
    sched.register_scraper(LA_CalcasieuScraper(), interval_minutes=90)
    sched.register_scraper(LA_OuachitaScraper(), interval_minutes=90)
    sched.register_scraper(LA_StTammanyScraper(), interval_minutes=90)
    sched.register_scraper(LA_AscensionScraper(), interval_minutes=120)
    sched.register_scraper(LA_LivingstonScraper(), interval_minutes=120)

    # ── Alabama (wave-1 + wave-2 + wave-3) ─────────────────────────────────────────────────────
    sched.register_scraper(AL_JeffersonScraper(), interval_minutes=120)
    sched.register_scraper(AL_MadisonScraper(), interval_minutes=120)
    sched.register_scraper(AL_MobileScraper(), interval_minutes=120)
    sched.register_scraper(AL_BaldwinScraper(), interval_minutes=120)
    sched.register_scraper(AL_TuscaloosaScraper(), interval_minutes=120)
    sched.register_scraper(AL_ShelbyScraper(), interval_minutes=120)
    sched.register_scraper(AL_MontgomeryScraper(), interval_minutes=120)
    sched.register_scraper(AL_HoustonScraper(), interval_minutes=120)
    sched.register_scraper(AL_MorganScraper(), interval_minutes=120)
    sched.register_scraper(AL_EtowahScraper(), interval_minutes=120)
    sched.register_scraper(AL_CullmanScraper(), interval_minutes=120)
    sched.register_scraper(AL_DeKalbScraper(), interval_minutes=120)
    sched.register_scraper(AL_JacksonScraper(), interval_minutes=120)

    # ── Connecticut (wave-1 + wave-2 + wave-3) ────────────────────────────────────────
    sched.register_scraper(CT_StatewideScraper(), interval_minutes=180)
    sched.register_scraper(CT_DOCScraper(), interval_minutes=120)
    sched.register_scraper(CT_HartfordScraper(), interval_minutes=120)
    sched.register_scraper(CT_BridgeportScraper(), interval_minutes=120)
    sched.register_scraper(CT_NewHavenScraper(), interval_minutes=120)
    sched.register_scraper(CT_StamfordScraper(), interval_minutes=120)

    # ── Mississippi (wave-1 + wave-2 + wave-3) ─────────────────────────────────────────────────
    sched.register_scraper(MS_HindsScraper(), interval_minutes=90)
    sched.register_scraper(MS_JacksonScraper(), interval_minutes=120)
    sched.register_scraper(MS_HarrisonScraper(), interval_minutes=120)
    sched.register_scraper(MS_DeSotoScraper(), interval_minutes=120)
    sched.register_scraper(MS_RankinScraper(), interval_minutes=120)
    sched.register_scraper(MS_LauderdaleScraper(), interval_minutes=120)
    sched.register_scraper(MS_ForrestScraper(), interval_minutes=120)
    sched.register_scraper(MS_JonesScraper(), interval_minutes=120)
    sched.register_scraper(MS_MadisonScraper(), interval_minutes=120)

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


def _ensure_key_fl_counties_enabled():
    """Force-enable SWFL core scrapers so dashboard config never leaves Lee/Sarasota paused."""
    try:
        from pymongo import MongoClient
        from datetime import datetime, timezone
        client = MongoClient(settings.MONGODB_URI, serverSelectionTimeoutMS=5000)
        db = client[settings.MONGODB_DB_NAME]
        col = db["scraper_config"]
        key = ("Lee", "Sarasota", "Collier", "Charlotte", "Manatee", "DeSoto", "Hendry")
        now = datetime.now(timezone.utc)
        for bare in key:
            label = f"{bare} (FL)"
            # Re-enable any existing docs keyed by label or bare name
            col.update_many(
                {"$or": [{"county": label}, {"county": bare}, {"county": bare.lower()}]},
                {"$set": {
                    "enabled": True,
                    "county_label": label,
                    "state": "FL",
                    "updated_at": now,
                    "updated_by": "startup_key_fl",
                    "reason": "KEY_FL_COUNTIES must remain enabled",
                }},
            )
            # Canonical labeled doc for dashboard config UI
            col.update_one(
                {"county": label},
                {"$set": {
                    "county": label,
                    "county_label": label,
                    "state": "FL",
                    "enabled": True,
                    "updated_at": now,
                    "updated_by": "startup_key_fl",
                    "reason": "KEY_FL_COUNTIES must remain enabled",
                }},
                upsert=True,
            )
        client.close()
        logger.info("✅ KEY FL counties forced enabled: %s", ", ".join(key))
    except Exception as e:
        logger.warning("⚠️ Could not force-enable KEY FL counties: %s", e)


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
    _ensure_key_fl_counties_enabled()

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

