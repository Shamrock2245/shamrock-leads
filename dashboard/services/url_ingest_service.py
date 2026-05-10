"""
URL Ingestion Service — ShamrockLeads
Fetches a jail booking URL and extracts structured arrest data.
Supports JailTracker, Odyssey, P2C, and generic HTML parsers.
"""
import re, logging
from typing import Optional
import httpx
from bs4 import BeautifulSoup

log = logging.getLogger("shamrock.url_ingest")

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125.0 Safari/537.36"

COUNTY_URL_PATTERNS = {
    r"sheriffleefl\.org": "Lee", r"ccso\.org": "Charlotte",
    r"colliersheriff\.org": "Collier", r"hendryso\.com": "Hendry",
    r"desotosheriff\.com": "DeSoto", r"manateesheriff\.com": "Manatee",
    r"sarasotasheriff\.org": "Sarasota", r"hillsboroughcounty\.org": "Hillsborough",
    r"pcsoweb\.com": "Pinellas", r"inmatelookup\.mcso\.org": "Marion",
}


async def ingest_url(url: str) -> dict:
    """Fetch a URL and extract structured arrest data."""
    if not url or not url.strip():
        return {"success": False, "error": "No URL provided"}
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True,
                                      headers={"User-Agent": UA}, verify=False) as client:
            resp = await client.get(url)
            resp.raise_for_status()
        html = resp.text
        soup = BeautifulSoup(html, "html.parser")
        county = _detect_county(url)

        data = None
        if "jailtracker" in url.lower() or "JailTracker" in html:
            data = _parse_labeled(soup, "jailtracker")
        if not data and ("odyssey" in url.lower() or "CaseSearch" in html):
            data = _parse_tables(soup, "odyssey")
        if not data and "p2c" in url.lower():
            data = _parse_tables(soup, "p2c")
        if not data:
            data = _parse_generic(soup)

        if not data or not data.get("full_name"):
            return {"success": False, "error": "Could not extract arrest data from this URL.",
                    "url": url, "html_title": soup.title.string.strip() if soup.title else "Unknown"}

        if county and not data.get("county"):
            data["county"] = county
        method = data.pop("_method", "generic")
        data = _normalize(data)
        data["source_url"] = url
        data["ingestion_method"] = "url_ingest"
        return {"success": True, "data": data, "source_url": url, "parse_method": method}
    except httpx.TimeoutException:
        return {"success": False, "error": "Request timed out."}
    except httpx.HTTPStatusError as e:
        return {"success": False, "error": f"HTTP {e.response.status_code}"}
    except Exception as e:
        log.exception(f"URL ingestion error: {e}")
        return {"success": False, "error": str(e)}


def _detect_county(url):
    for pat, county in COUNTY_URL_PATTERNS.items():
        if re.search(pat, url, re.I):
            return county
    return None


FIELD_MAP = {
    "full_name": ["full name", "inmate name", "defendant name", "offender name", "name"],
    "booking_number": ["booking", "booking #", "booking no", "book #", "book no"],
    "charges": ["charge", "offense", "charge description"],
    "bond_amount": ["bond amount", "total bond", "bail amount", "bond"],
    "bond_type": ["bond type", "bail type"],
    "case_number": ["case number", "case #", "case no", "docket"],
    "court_date": ["court date", "next court", "arraignment date"],
    "court_location": ["court location", "courthouse", "division"],
    "facility": ["facility", "housing", "jail", "detention", "location"],
    "booking_date": ["arrest date", "booking date", "date booked", "booked on"],
    "date_of_birth": ["date of birth", "dob", "birth date"],
    "custody_status": ["status", "custody status"],
    "county": ["county"],
    "age": ["age"], "gender": ["gender", "sex"], "race": ["race", "ethnicity"],
}


def _match_field(label):
    label = label.lower().rstrip(":").strip()
    for field, keywords in FIELD_MAP.items():
        if any(kw in label for kw in keywords):
            return field
    return None


def _parse_labeled(soup, method):
    data = {"_method": method}
    for el in soup.find_all(["label", "th", "dt", "span", "div"]):
        label = (el.get_text() or "").strip().lower().rstrip(":")
        if not label:
            continue
        val_el = el.find_next_sibling(["span", "td", "dd", "div"]) or el.find_next(["span", "td", "dd"])
        if not val_el:
            continue
        val = val_el.get_text(strip=True)
        if not val:
            continue
        field = _match_field(label)
        if field == "bond_amount":
            data[field] = _extract_money(val)
        elif field == "charges":
            existing = data.get("charges", "")
            data["charges"] = f"{existing}; {val}".lstrip("; ") if existing else val
        elif field:
            data.setdefault(field, val)
    return data if data.get("full_name") else None


def _parse_tables(soup, method):
    data = {"_method": method}
    for row in soup.find_all("tr"):
        cells = row.find_all(["td", "th"])
        if len(cells) < 2:
            continue
        label = cells[0].get_text(strip=True)
        val = cells[1].get_text(strip=True)
        if not val or len(val) > 500:
            continue
        field = _match_field(label)
        if field == "bond_amount":
            data[field] = _extract_money(val)
        elif field == "charges":
            existing = data.get("charges", "")
            data["charges"] = f"{existing}; {val}".lstrip("; ") if existing else val
        elif field:
            data.setdefault(field, val)
    return data if data.get("full_name") else None


def _parse_generic(soup):
    data = {"_method": "generic"}
    # Check headings for name
    for h in soup.find_all(["h1", "h2", "h3"]):
        m = re.match(r"(?:Inmate|Defendant|Offender)[:\s]+(.+)", h.get_text(strip=True), re.I)
        if m:
            data["full_name"] = m.group(1).strip()
            break
    # Scan all tables
    for table in soup.find_all("table"):
        for row in table.find_all("tr"):
            cells = row.find_all(["td", "th"])
            if len(cells) < 2:
                continue
            label = cells[0].get_text(strip=True)
            val = cells[1].get_text(strip=True)
            if val and len(val) < 500:
                field = _match_field(label)
                if field == "bond_amount":
                    data[field] = _extract_money(val)
                elif field == "charges":
                    existing = data.get("charges", "")
                    data["charges"] = f"{existing}; {val}".lstrip("; ") if existing else val
                elif field:
                    data.setdefault(field, val)
    # Scan dl/dt/dd
    for dl in soup.find_all("dl"):
        for dt, dd in zip(dl.find_all("dt"), dl.find_all("dd")):
            field = _match_field(dt.get_text(strip=True))
            val = dd.get_text(strip=True)
            if field and val:
                if field == "bond_amount":
                    data[field] = _extract_money(val)
                elif field:
                    data.setdefault(field, val)
    return data if data.get("full_name") else None


def _extract_money(text):
    m = re.search(r"\$?([\d,]+(?:\.\d{2})?)", (text or "").replace(" ", ""))
    return m.group(1).replace(",", "") if m else text


def _normalize(data):
    name = data.get("full_name", "")
    if "," in name:
        parts = name.split(",", 1)
        data["full_name"] = f"{parts[1].strip().title()} {parts[0].strip().title()}"
    elif name.isupper():
        data["full_name"] = name.title()
    ba = data.get("bond_amount", "")
    if ba:
        try:
            data["bond_amount"] = str(float(str(ba).replace(",", "").replace("$", "")))
        except (ValueError, TypeError):
            pass
    for k in data:
        if isinstance(data[k], str):
            data[k] = " ".join(data[k].split())
    return data
