import logging
import os
import sys
import urllib3
import requests

# Add root to sys.path
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__) + "/.."))

from scrapers.smartweb_parser import scrape_smartweb
from core.models import ArrestRecord

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("test-escambia-shared")

URL = "https://inmatelookup.myescambia.com/smartwebclient/jail.aspx"
session = requests.Session()

try:
    logger.info("Running scrape_smartweb for Escambia...")
    records = scrape_smartweb(
        base_url=URL,
        county="Escambia",
        facility="Escambia County Jail",
        session=session,
        ArrestRecord=ArrestRecord
    )
    logger.info(f"Successfully scraped {len(records)} records!")
    if records:
        logger.info(f"Sample record 0: {records[0].__dict__}")
except Exception as e:
    logger.error(f"Error: {e}")
