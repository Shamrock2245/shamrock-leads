"""
URL Ingestion Service — ShamrockLeads
Fetches a jail booking URL and extracts structured arrest data.
Supports Lee County API, JailTracker, Odyssey, P2C, and generic HTML parsers.
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

FL_COUNTIES_UPPER = {
    "ALACHUA", "BAKER", "BAY", "BRADFORD", "BREVARD", "BROWARD", "CALHOUN", "CHARLOTTE", "CITRUS",
    "CLAY", "COLLIER", "COLUMBIA", "DESOTO", "DIXIE", "DUVAL", "ESCAMBIA", "FLAGLER", "FRANKLIN",
    "GADSDEN", "GILCHRIST", "GLADES", "GULF", "HAMILTON", "HARDEE", "HENDRY", "HERNANDO", "HIGHLANDS",
    "HILLSBOROUGH", "HOLMES", "INDIAN RIVER", "JACKSON", "JEFFERSON", "LAFAYETTE", "LAKE", "LEE",
    "LEON", "LEVY", "LIBERTY", "MADISON", "MANATEE", "MARION", "MARTIN", "MIAMI-DADE", "MONROE",
    "NASSAU", "OKALOOSA", "OKEECHOBEE", "ORANGE", "OSCEOLA", "PALM BEACH", "PASCO", "PINELLAS",
    "POLK", "PUTNAM", "SANTA ROSA", "SARASOTA", "SEMINOLE", "ST. JOHNS", "ST. LUCIE", "SUMTER",
    "SUWANNEE", "TAYLOR", "UNION", "VOLUSIA", "WAKULLA", "WALTON", "WASHINGTON"
}


def _first_str(*vals) -> str:
    for v in vals:
        if v is None:
            continue
        if isinstance(v, dict):
            inner = _first_str(
                v.get("line1"), v.get("address"), v.get("street"),
                v.get("full"), v.get("value"),
            )
            if inner:
                return inner
            continue
        s = str(v).strip()
        if s and s.lower() not in ("none", "null", "n/a", "na"):
            return s
    return ""


def _normalize_dob(raw: str) -> tuple[str, str]:
    """Return (iso_yyyy_mm_dd, display_mm_dd_yyyy). Empty strings if unusable."""
    s = (raw or "").strip()
    if not s:
        return "", ""
    s = s.replace("Z", "")
    if "T" in s:
        s = s.split("T", 1)[0]
    s = s[:10]
    if len(s) == 10 and s[4] == "-" and s[7] == "-":
        y, m, d = s.split("-")
        return s, f"{m}/{d}/{y}"
    if len(s) == 10 and s[2] == "/" and s[5] == "/":
        m, d, y = s.split("/")
        if len(y) == 4:
            return f"{y}-{m}-{d}", s
    return s, s


def _compose_address(b_data: dict) -> str:
    street = _first_str(
        b_data.get("address"),
        b_data.get("address1"),
        b_data.get("street"),
        b_data.get("streetAddress"),
        b_data.get("inmateAddress"),
        b_data.get("residence"),
        b_data.get("homeAddress"),
    )
    city = _first_str(b_data.get("city"), b_data.get("addressCity"))
    state = _first_str(b_data.get("state"), b_data.get("addressState"), "FL")
    zip_code = _first_str(
        b_data.get("zip"), b_data.get("zipcode"), b_data.get("postalCode"),
        b_data.get("zipCode"), b_data.get("addressZip"),
    )
    city_state = ", ".join(p for p in [city, " ".join(p for p in [state, zip_code] if p)] if p)
    if street and city_state:
        return f"{street}, {city_state}"
    return street or city_state


def _title_case_name(name_str: str) -> str:
    """Title case a name string while properly preserving apostrophes (e.g. D'Angelo, O'Connor)."""
    if not name_str:
        return ""
    words = name_str.strip().split()
    res = []
    for w in words:
        if "'" in w:
            parts = w.split("'")
            w_cased = "'".join(p.capitalize() for p in parts)
        elif "-" in w:
            parts = w.split("-")
            w_cased = "-".join(p.capitalize() for p in parts)
        else:
            w_cased = w.capitalize()
        res.append(w_cased)
    return " ".join(res)


async def ingest_url(url: str) -> dict:
    """Fetch a URL and extract structured arrest data."""
    if not url or not url.strip():
        return {"success": False, "error": "No URL provided"}
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    # ── Fast path: Lee County API Ingestion ────────────────────────────────────
    if "sheriffleefl.org" in url.lower():
        m_id = re.search(r"(?:id=|bookings/?)(\d+)", url, re.I)
        booking_id = m_id.group(1) if m_id else None
        if booking_id:
            try:
                data = await _ingest_lee_county_api(booking_id, url)
                if data and data.get("full_name"):
                    return {
                        "success": True,
                        "data": data,
                        "source_url": url,
                        "parse_method": "lee_county_api"
                    }
            except Exception as e:
                log.warning(f"Lee API ingest failed for {url}, falling back: {e}")
            mongo_data = await _ingest_lee_from_mongo(booking_id, url)
            if mongo_data and mongo_data.get("full_name"):
                return {
                    "success": True,
                    "data": mongo_data,
                    "source_url": url,
                    "parse_method": "lee_mongo_arrest",
                }

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


async def _ingest_lee_county_api(booking_id: str, source_url: str) -> Optional[dict]:
    """Fetch booking info and charges directly from Lee County Sheriff's public API.

    Uses origin DNS pin (``scrapers.lee_origin``) because ``www.sheriffleefl.org``
    A-record can point at a dead host while the apex origin still serves the API.
    """
    import asyncio

    headers = {"User-Agent": UA, "Accept": "application/json"}

    def _fetch_both():
        from scrapers.lee_origin import lee_api_get
        try:
            from scrapers.lee_rate_limit import is_cooled_down, seconds_remaining

            if is_cooled_down():
                log.warning(
                    "Lee URL ingest skipped — rate-limit cooldown (%.0fs left)",
                    seconds_remaining(),
                )
                return None
        except Exception:
            pass

        b = lee_api_get(
            f"/public-api/bookings/{booking_id}",
            headers=headers,
            timeout=20,
            max_retries=2,
        )
        c = lee_api_get(
            f"/public-api/bookings/{booking_id}/charges",
            headers=headers,
            timeout=20,
            max_retries=2,
        )
        return b, c

    try:
        b_resp, c_resp = await asyncio.to_thread(_fetch_both)
    except Exception as e:
        log.warning("Lee origin-pinned ingest failed, trying plain httpx: %s", e)
        booking_url = f"https://www.sheriffleefl.org/public-api/bookings/{booking_id}"
        charges_url = f"https://www.sheriffleefl.org/public-api/bookings/{booking_id}/charges"
        async with httpx.AsyncClient(
            timeout=20.0, follow_redirects=True, headers=headers, verify=False
        ) as client:
            b_resp = await client.get(booking_url)
            c_resp = await client.get(charges_url)

    if b_resp is None or b_resp.status_code != 200:
        return None

    b_data = b_resp.json() or {}
    c_data = []
    if c_resp is not None and c_resp.status_code == 200:
        try:
            parsed_charges = c_resp.json()
            if isinstance(parsed_charges, list):
                c_data = parsed_charges
        except Exception:
            c_data = []

    first = _title_case_name(str(b_data.get("givenName") or b_data.get("firstName") or ""))
    middle = _title_case_name(str(b_data.get("middleName") or ""))
    last = _title_case_name(str(b_data.get("surName") or b_data.get("lastName") or ""))
    suffix = str(b_data.get("suffix") or "").strip()

    name_parts = [p for p in [first, middle, last] if p]
    if suffix:
        name_parts.append(suffix)
    full_name = " ".join(name_parts)

    # Parse charges array
    charges_list = []
    total_bond = 0.0
    bond_types = set()
    case_numbers = set()
    court_locations = set()
    hearing_dates = []
    detected_county = "Lee"

    for c in c_data:
        desc = str(c.get("offenseDescription") or "").strip()
        if desc:
            clean_desc = re.sub(r"\s+", " ", desc).strip()
            if clean_desc and clean_desc not in charges_list:
                charges_list.append(clean_desc)

        # Bond
        ba = c.get("bondAmount")
        if ba:
            try:
                amt = float(str(ba).replace(",", "").replace("$", ""))
                total_bond += amt
            except (ValueError, TypeError):
                pass

        # Bond Type
        bt = str(c.get("bondTypeName") or "").strip()
        if bt:
            bond_types.add(bt)

        # Case number & potential out-of-county indicator
        cn = str(c.get("caseNumber") or "").strip()
        if cn:
            case_numbers.add(cn)
            if cn.upper() in FL_COUNTIES_UPPER and cn.upper() != "LEE":
                detected_county = cn.title()

        # Check disposition / offense text for out-of-county warrants
        disp = str(c.get("disposition") or "").strip().upper()
        if "OUT OF COUNTY" in disp or "OUT-OF-COUNTY" in desc.upper():
            if detected_county == "Lee":
                # Scan desc for known county name
                for fl_c in FL_COUNTIES_UPPER:
                    if fl_c in desc.upper() and fl_c != "LEE":
                        detected_county = fl_c.title()
                        break
                if detected_county == "Lee":
                    detected_county = "Out of County"

        cl = str(c.get("courtLocation") or "").strip()
        if cl:
            court_locations.add(cl)

        hd = c.get("hearingDate")
        if hd:
            hearing_dates.append(str(hd))

    charges_str = " | ".join(charges_list) if charges_list else "None specified"
    court_date_val = "TBN"
    court_time_val = ""

    if hearing_dates:
        # Parse ISO date
        h_str = hearing_dates[0]
        try:
            from datetime import datetime
            dt = datetime.fromisoformat(h_str.replace("Z", "+00:00"))
            court_date_val = dt.strftime("%Y-%m-%d")
            court_time_val = dt.strftime("%H:%M:%S")
        except Exception:
            court_date_val = h_str[:10] if len(h_str) >= 10 else "TBN"

    status_str = "In Custody" if b_data.get("inCustody", True) else "Released"

    dob_iso, dob_display = _normalize_dob(_first_str(
        b_data.get("birthDate"),
        b_data.get("dateOfBirth"),
        b_data.get("birthdate"),
        b_data.get("dob"),
        b_data.get("date_of_birth"),
    ))
    address = _compose_address(b_data)
    charge_amounts = []
    charge_details = []
    for c in c_data:
        desc = str(c.get("offenseDescription") or "").strip()
        amt = 0.0
        ba = c.get("bondAmount")
        if ba:
            try:
                amt = float(str(ba).replace(",", "").replace("$", ""))
            except (ValueError, TypeError):
                amt = 0.0
        if amt > 0:
            charge_amounts.append(amt)
        if desc:
            charge_details.append({
                "charge": desc,
                "bond_amount": amt,
                "bond_type": str(c.get("bondTypeName") or "").strip(),
                "case_number": str(c.get("caseNumber") or "").strip(),
            })

    from dashboard.services.premium import statutory_premium

    return {
        "booking_number": booking_id,
        "full_name": full_name,
        "first_name": first,
        "middle_name": middle,
        "last_name": last,
        "charges": charges_str,
        "bond_amount": str(round(total_bond, 2)) if total_bond > 0 else "0",
        "bond_type": " / ".join(bond_types) if bond_types else "Surety",
        "case_number": ", ".join(case_numbers) if case_numbers else "",
        "court_date": court_date_val,
        "court_time": court_time_val,
        "court_location": ", ".join(court_locations) if court_locations else "",
        "county": detected_county,
        "status": status_str,
        "address": address,
        "defendant_address": address,
        "city": _first_str(b_data.get("city"), b_data.get("addressCity")),
        "state": _first_str(b_data.get("state"), b_data.get("addressState"), "FL"),
        "zip": _first_str(b_data.get("zip"), b_data.get("zipcode"), b_data.get("postalCode")),
        "facility": str(b_data.get("housing") or "Lee County Jail").strip(),
        "dob": dob_iso or dob_display,
        "date_of_birth": dob_display or dob_iso,
        "charge_details": charge_details,
        "charge_amounts": charge_amounts,
        "premium": statutory_premium(
            total_bond,
            charge_amounts=charge_amounts,
            charge_count=len(charges_list) or 1,
        ),
        "source_url": source_url,
        "ingestion_method": "lee_county_api"
    }


async def _ingest_lee_from_mongo(booking_id: str, source_url: str) -> Optional[dict]:
    """Use the last scraped Lee arrest when the public API is cooled down."""
    try:
        from dashboard.extensions import get_db
        db = get_db()
        if db is None:
            return None
        doc = await db["arrests"].find_one(
            {"$or": [
                {"booking_number": booking_id},
                {"Booking_Number": booking_id},
            ]},
            sort=[("last_checked", -1), ("Last_Checked", -1)],
        )
        if not doc:
            return None
        dob_iso, dob_display = _normalize_dob(
            _first_str(doc.get("dob"), doc.get("DOB"), doc.get("date_of_birth"))
        )
        street = _first_str(doc.get("address"), doc.get("Address"))
        city = _first_str(doc.get("city"), doc.get("City"))
        state = _first_str(doc.get("state"), doc.get("State"), "FL")
        zip_code = _first_str(doc.get("zip"), doc.get("ZIP"), doc.get("Zip"))
        address = _compose_address({
            "address": street, "city": city, "state": state, "zip": zip_code,
        })
        extra = doc.get("extra_data") or doc.get("Extra_Data") or {}
        details = extra.get("charge_details") or []
        charge_amounts = []
        for d in details:
            try:
                amt = float(str((d or {}).get("bond_amount") or 0).replace(",", "").replace("$", ""))
            except (ValueError, TypeError):
                amt = 0.0
            if amt > 0:
                charge_amounts.append(amt)
        charges = _first_str(doc.get("charges"), doc.get("Charges"))
        bond = doc.get("bond_amount") or doc.get("Bond_Amount") or 0
        try:
            bond_f = float(str(bond).replace(",", "").replace("$", ""))
        except (ValueError, TypeError):
            bond_f = 0.0
        from dashboard.services.premium import statutory_premium
        return {
            "booking_number": booking_id,
            "full_name": _first_str(doc.get("full_name"), doc.get("Full_Name")),
            "charges": charges,
            "bond_amount": str(bond_f) if bond_f else "0",
            "case_number": _first_str(doc.get("case_number"), doc.get("Case_Number")),
            "court_date": _first_str(doc.get("court_date"), doc.get("Court_Date")) or "TBN",
            "court_location": _first_str(doc.get("court_location"), doc.get("Court_Location")),
            "county": _first_str(doc.get("county"), doc.get("County"), "Lee"),
            "facility": _first_str(doc.get("facility"), doc.get("Facility"), "Lee County Jail"),
            "address": address,
            "defendant_address": address,
            "dob": dob_iso or dob_display,
            "date_of_birth": dob_display or dob_iso,
            "charge_details": details,
            "charge_amounts": charge_amounts,
            "premium": statutory_premium(
                bond_f,
                charge_amounts=charge_amounts,
                charge_count=max(1, len(charges.split("|")) if charges else 1),
            ),
            "source_url": source_url,
            "ingestion_method": "lee_mongo_arrest",
        }
    except Exception as e:
        log.warning("Lee Mongo fallback failed: %s", e)
        return None


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
    for h in soup.find_all(["h1", "h2", "h3"]):
        m = re.match(r"(?:Inmate|Defendant|Offender)[:\s]+(.+)", h.get_text(strip=True), re.I)
        if m:
            data["full_name"] = m.group(1).strip()
            break
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
        data["full_name"] = f"{_title_case_name(parts[1])} {_title_case_name(parts[0])}"
    elif name.isupper():
        data["full_name"] = _title_case_name(name)
    ba = data.get("bond_amount", "")
    if ba:
        try:
            data["bond_amount"] = str(float(str(ba).replace(",", "").replace("$", "")))
        except (ValueError, TypeError):
            pass
    for k in data:
        if isinstance(data[k], str):
            data[k] = " ".join(data[k].split())
    if not data.get("court_date"):
        data["court_date"] = "TBN"
    return data
