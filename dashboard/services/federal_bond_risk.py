"""
Florida federal bond-risk alerts.

Watches CourtListener v4 for criminal cases in FLMD, FLSD, and FLND that
show a bond-risk event for someone who already has an active Shamrock bond.
Reuses ``CourtListenerClient`` (token, 429 backoff, field selection, one page).

Matching fails closed:
  * full name, plus a date of birth the docket actually has, or a case or
    agency number we already hold → confirmed and auto-linked
  * full name only → ``possible`` for staff review, not auto-linked
  * a conflicting date of birth, a surname-only caption, or two confirmed
    clients for the same docket → no auto-link

``FEDERAL_BOND_RISK_ALERTS`` defaults off. Nothing is written and CourtListener
is not called until it is on. With the flag on and no token, the scan skips
and the status payload carries a warning. Output is a CRM notification plus
a docket-monitor row and a Slack ``#alerts`` post. No client text, email, or
Apple chat.
"""
from __future__ import annotations

import logging
import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from core.booking_identity import redact_internal_keys
from dashboard.services.courtlistener_client import (
    ANONYMOUS_V4_REJECTED,
    FL_FEDERAL_COURT_IDS,
    SE_US_COURTS,
)

log = logging.getLogger("shamrock.federal_bond_risk")

FLAG = "FEDERAL_BOND_RISK_ALERTS"
SOURCE = "courtlistener_federal_bond_risk"
_ON = {"1", "true", "yes", "on"}
ACTIVE_STATUSES = ("active", "monitoring", "alert")
FL_FEDERAL_COURTS = frozenset(FL_FEDERAL_COURT_IDS)
MAX_BONDS = 200
MAX_DOCKETS = 8
MAX_ROWS = 30
MAX_SLACK = 10

BOND_RISK_QUERY = (
    '"conditions of release" OR "detention hearing" OR "failure to appear" '
    'OR "arrest warrant" OR "bench warrant" OR capias OR "revocation of release" '
    'OR "revoke bond" OR "pretrial release"'
)

_SUFFIXES = {"JR", "SR", "II", "III", "IV", "V"}
_CAPTION_SPLIT = re.compile(r"\s+v(?:s\.?)?\s+", re.I)
_CRIMINAL_DOCKET = re.compile(r"(?i)(?:\d{1,2}:)?\d{2}-(?:cr|mj)-")
_CIVIL_DOCKET = re.compile(r"(?i)(?:\d{1,2}:)?\d{2}-cv-")
_DOB_LABELED = re.compile(
    r"(?i)(?:\bdob\b|date\s+of\s+birth|d\.o\.b\.?)\s*[:#-]?\s*"
    r"(\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4})"
)

# More specific release events are listed before "conditions set" so a
# modification is not also stored as a new bond being set.
_EVENT_RULES: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    ("failure_to_appear", "Failure to appear", re.compile(r"failure\s+to\s+appear|\bfailed\s+to\s+appear\b|\bfta\b", re.I)),
    ("warrant", "Warrant", re.compile(r"arrest\s+warrant|bench\s+warrant|warrant\s+(?:issued|for\s+arrest)|\bcapias\b", re.I)),
    ("revocation_of_release", "Revocation of release", re.compile(
        r"revoc\w*\s+(?:of\s+)?(?:pretrial\s+)?release|revoke\s+(?:the\s+)?(?:bond|release|pretrial)|bond\s+revoc|motion\s+to\s+revoke\s+bond",
        re.I,
    )),
    ("modification_of_release", "Modification of release", re.compile(
        r"modif\w+\s+(?:the\s+)?(?:conditions\s+of\s+)?release|order\s+modifying\s+conditions",
        re.I,
    )),
    ("bond_conditions_changed", "Bond conditions changed", re.compile(
        r"(?:bond|conditions\s+of\s+release)\s+(?:increased|decreased|reduced|raised|amended|changed)"
        r"|amended\s+conditions\s+of\s+release",
        re.I,
    )),
    ("detention_hearing", "Detention hearing", re.compile(r"detention\s+hearing", re.I)),
    ("bond_conditions_set", "Bond conditions set", re.compile(
        r"conditions\s+of\s+release|order\s+setting\s+(?:conditions|bond)|appearance\s+bond\s+set|\bbond\s+set\b",
        re.I,
    )),
)
_SET_SUPPRESSED_BY = {"revocation_of_release", "modification_of_release", "bond_conditions_changed"}

_DOB_FIELDS = (
    "dob", "DOB", "date_of_birth", "defendant_dob", "Date_of_Birth", "Defendant_DOB",
)
_NUMBER_FIELDS = (
    "case_number", "Case_Number", "court_case_number", "Court_Case_Number",
    "agency_case_number", "agency_number", "Agency_Number", "Agency_Case_Number",
    "federal_case_number", "pacer_case_id", "pacer_case_number",
)
_NAME_FIELDS = ("defendant_name", "Defendant_Name", "full_name", "Full_Name")


def federal_bond_risk_enabled() -> bool:
    return os.getenv(FLAG, "").strip().lower() in _ON


def federal_bond_risk_health() -> dict:
    """Flag and token state. Safe to call when the scan is not running."""
    token_on = bool(os.getenv("COURTLISTENER_API_TOKEN", "").strip())
    enabled = federal_bond_risk_enabled()
    warning = None
    if not token_on:
        warning = ANONYMOUS_V4_REJECTED
    elif not enabled:
        warning = (
            f"{FLAG} is off. No CRM rows or Slack alerts are written."
        )
    return {
        "enabled": enabled,
        "token_configured": token_on,
        "warning": warning,
        "courts": list(FL_FEDERAL_COURT_IDS),
        "client_messages": 0,
    }


def _tokens(value: str) -> list[str]:
    cleaned = re.sub(r"[^A-Za-z\s'-]", " ", value or "")
    out = []
    for raw in cleaned.split():
        token = raw.strip("'-").replace("'", "").upper()
        if token and token not in _SUFFIXES:
            out.append(token)
    return out


def name_parts(value: str) -> Optional[tuple[str, tuple[str, ...], str]]:
    """Return (first, middles, last) for a full name, or None.

    A single token or a one-letter given name is not a full name.
    """
    raw = (value or "").strip()
    if not raw:
        return None
    if "," in raw:
        last_raw, _, rest = raw.partition(",")
        first_tokens = _tokens(rest)
        last_tokens = _tokens(last_raw)
        if not first_tokens or not last_tokens:
            return None
        first, middles, surname = first_tokens[0], tuple(first_tokens[1:]), " ".join(last_tokens)
    else:
        tokens = _tokens(raw)
        if len(tokens) < 2:
            return None
        first, surname = tokens[0], tokens[-1]
        middles = tuple(tokens[1:-1])
    if len(first) < 2 or len(surname.replace(" ", "")) < 2:
        return None
    return first, middles, surname


def full_name_equal(left: str, right: str) -> bool:
    """Exact first and last name. Middles may be absent. Conflicting middles fail."""
    a = name_parts(left)
    b = name_parts(right)
    if not a or not b:
        return False
    if a[0] != b[0] or a[2] != b[2]:
        return False
    if a[1] and b[1]:
        if len(a[1]) != len(b[1]):
            return False
        for x, y in zip(a[1], b[1]):
            if x == y:
                continue
            if len(x) == 1 and y.startswith(x):
                continue
            if len(y) == 1 and x.startswith(y):
                continue
            return False
    return True


def defendant_names_from_caption(case_name: str) -> list[str]:
    """Defendant side of ``United States v. Jane Roe``. Surname-only stays surname-only."""
    text = re.sub(r"\([^)]*\)", " ", case_name or "")
    parts = _CAPTION_SPLIT.split(text, maxsplit=1)
    tail = parts[-1] if len(parts) == 2 else ""
    tail = re.sub(r"\bet\s+al\.?\b", " ", tail, flags=re.I)
    if not tail.strip():
        return []
    names = []
    for chunk in re.split(r"\s+(?:and|&)\s+|;\s*", tail):
        for alias in re.split(r"\s+a/?k/?a\s+", chunk, flags=re.I):
            alias = alias.strip(" ,.-")
            if alias:
                names.append(alias)
    return names


def normalize_dob(value: str) -> Optional[str]:
    raw = str(value or "").strip()
    iso = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", raw)
    us = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", raw)
    if iso:
        year, month, day = int(iso.group(1)), int(iso.group(2)), int(iso.group(3))
    elif us:
        month, day, year = int(us.group(1)), int(us.group(2)), int(us.group(3))
    else:
        return None
    if year < 1900 or year > datetime.now(timezone.utc).year:
        return None
    try:
        return datetime(year, month, day).date().isoformat()
    except ValueError:
        return None


def extract_labeled_dobs(text: str) -> set[str]:
    found = set()
    for match in _DOB_LABELED.finditer(text or ""):
        norm = normalize_dob(match.group(1))
        if norm:
            found.add(norm)
    return found


def canonical_identifier(value: str) -> Optional[str]:
    """Case or agency number. Requires a digit and at least 5 alphanumeric characters."""
    raw = str(value or "").strip()
    if not raw or len(raw) > 80:
        return None
    compact = re.sub(r"\s+", "", raw.upper()).replace("–", "-").replace("—", "-")
    compact = re.sub(r"-([A-Z]{2,4})$", "", compact)
    if not re.search(r"\d", compact):
        return None
    alnum = re.sub(r"[^A-Z0-9]", "", compact)
    if len(alnum) < 5:
        return None
    return compact


def _collect_dobs(doc: dict) -> set[str]:
    found = set()
    for key in _DOB_FIELDS:
        norm = normalize_dob(str(doc.get(key) or ""))
        if norm:
            found.add(norm)
    return found


def _collect_numbers(doc: dict) -> set[str]:
    found = set()
    for key in _NUMBER_FIELDS:
        ident = canonical_identifier(str(doc.get(key) or ""))
        if ident:
            found.add(ident)
    return found


def _collect_names(doc: dict) -> list[str]:
    names = []
    for key in _NAME_FIELDS:
        value = str(doc.get(key) or "").strip()
        if value and name_parts(value):
            names.append(value)
    first = str(doc.get("first_name") or doc.get("First_Name") or "").strip()
    last = str(doc.get("last_name") or doc.get("Last_Name") or "").strip()
    if first and last:
        combined = f"{first} {last}".strip()
        middle = str(doc.get("middle_name") or doc.get("Middle_Name") or "").strip()
        if middle:
            combined = f"{first} {middle} {last}"
        if name_parts(combined):
            names.append(combined)
    return names


def is_fl_federal_criminal(court_id: str, docket_number: str) -> bool:
    if (court_id or "").strip().lower() not in FL_FEDERAL_COURTS:
        return False
    number = docket_number or ""
    if _CIVIL_DOCKET.search(number) and not _CRIMINAL_DOCKET.search(number):
        return False
    return bool(_CRIMINAL_DOCKET.search(number))


def classify_bond_risk(text: str) -> list[tuple[str, str]]:
    """Bond-risk events in one docket entry or RECAP blurb. Empty when none match."""
    if not text or not text.strip():
        return []
    if re.search(r"search\s+warrant", text, re.I) and not re.search(
        r"arrest\s+warrant|bench\s+warrant|warrant\s+(?:issued|for\s+arrest)|\bcapias\b",
        text,
        re.I,
    ):
        # A search warrant alone is not a bond-risk warrant.
        stripped = re.sub(r"search\s+warrant", " ", text, flags=re.I)
    else:
        stripped = text
    found: list[tuple[str, str]] = []
    seen = set()
    for event_type, label, pattern in _EVENT_RULES:
        if event_type not in seen and pattern.search(stripped):
            found.append((event_type, label))
            seen.add(event_type)
    if seen & _SET_SUPPRESSED_BY:
        found = [item for item in found if item[0] != "bond_conditions_set"]
    return found


def _court_slug(raw: dict) -> str:
    from dashboard.services.courtlistener_client import CourtListenerClient

    cid = str(raw.get("court_id") or "")
    slug = CourtListenerClient._extract_court_slug(cid)
    if slug:
        return slug.lower()
    court = str(raw.get("court") or "").strip().lower()
    slug = CourtListenerClient._extract_court_slug(court)
    if slug:
        return slug.lower()
    if court in FL_FEDERAL_COURTS:
        return court
    bare = cid.strip().lower()
    if re.fullmatch(r"[a-z0-9_]+", bare):
        return bare
    return ""


def _first_text(raw: dict, *keys: str) -> str:
    for key in keys:
        value = raw.get(key)
        if value:
            return str(value).strip()
    return ""


@dataclass
class ClientIdentity:
    bond_case_id: str
    defendant_id: str
    names: tuple[str, ...]
    dobs: frozenset[str]
    numbers: frozenset[str]
    status: str


@dataclass
class DocketEvent:
    event_type: str
    label: str


@dataclass
class DocketView:
    docket_id: str
    court_id: str
    court_name: str
    docket_number: str
    case_name: str
    pacer_case_id: str
    cause: str
    names: list[str] = field(default_factory=list)
    dobs: set[str] = field(default_factory=set)
    numbers: set[str] = field(default_factory=set)
    events: list[DocketEvent] = field(default_factory=list)
    absolute_url: str = ""

    def identifier_pool(self) -> set[str]:
        pool = set(self.numbers)
        for value in (self.docket_number, self.pacer_case_id, self.cause):
            ident = canonical_identifier(value)
            if ident:
                pool.add(ident)
        return pool


@dataclass
class MatchDecision:
    client: ClientIdentity
    status: str
    confirmations: tuple[str, ...]
    event_type: str
    event_label: str
    ambiguous: bool = False


def match_client(client: ClientIdentity, docket: DocketView) -> tuple[Optional[str], tuple[str, ...]]:
    """Return (confirmed|possible|None, confirmation labels).

    A date of birth that both sides have and that differs is a conflict:
    the row is dropped, even when a case number overlaps.
    """
    if not any(full_name_equal(name, other) for name in client.names for other in docket.names):
        return None, ()
    docket_dobs = set(docket.dobs)
    if client.dobs and docket_dobs and client.dobs.isdisjoint(docket_dobs):
        return None, ()
    confirmations = []
    if client.dobs & docket_dobs:
        confirmations.append("dob")
    held_numbers = {
        ident for ident in (canonical_identifier(number) for number in client.numbers) if ident
    }
    if held_numbers & docket.identifier_pool():
        confirmations.append("case_number")
    if confirmations:
        return "confirmed", tuple(confirmations)
    return "possible", ()


def decide_matches(clients: list[ClientIdentity], docket: DocketView) -> list[MatchDecision]:
    """One decision per client per event. Two confirmed hits on one event are not linked."""
    decisions: list[MatchDecision] = []
    for event in docket.events:
        group: list[MatchDecision] = []
        for client in clients:
            status, confirmations = match_client(client, docket)
            if not status:
                continue
            group.append(MatchDecision(
                client=client,
                status=status,
                confirmations=confirmations,
                event_type=event.event_type,
                event_label=event.label,
            ))
        confirmed = [row for row in group if row.status == "confirmed"]
        if len(confirmed) > 1:
            for row in group:
                if row.status == "confirmed":
                    row.status = "possible"
                    row.confirmations = ()
                    row.ambiguous = True
        decisions.extend(group)
    return decisions


def _redact_sensitive(text: str) -> str:
    def _hide_dob(match: re.Match) -> str:
        full = match.group(0)
        date = match.group(1)
        return full[: full.rfind(date)] + "[redacted]"

    cleaned = _DOB_LABELED.sub(_hide_dob, text or "")
    cleaned = re.sub(r"\b\d{3}-\d{2}-\d{4}\b", "[redacted]", cleaned)
    cleaned = re.sub(r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}\b", "[redacted]", cleaned)
    return redact_internal_keys(cleaned)


def _staff_copy(decision: MatchDecision, docket: DocketView) -> tuple[str, str]:
    name = decision.client.names[0]
    court = docket.court_name or docket.court_id.upper()
    docket_no = docket.docket_number or "unknown docket"
    if decision.status == "confirmed":
        how = []
        if "dob" in decision.confirmations:
            how.append("date of birth")
        if "case_number" in decision.confirmations:
            how.append("a case or agency number already on the bond")
        title = f"Federal bond risk linked — {decision.event_label}"
        message = (
            f"Confirmed for {name}. {decision.event_label} in {court}, docket {docket_no}. "
            f"Matched on full name and {' and '.join(how)}. Linked to the active bond."
        )
    else:
        title = f"Federal bond risk needs review — {decision.event_label}"
        if decision.ambiguous:
            why = "More than one active client confirmed to this docket, so nobody was auto-linked."
        else:
            why = "Full name matched. No date of birth or held case or agency number confirmed it."
        message = (
            f"Possible match for {name}. {decision.event_label} in {court}, docket {docket_no}. "
            f"{why} Staff review only."
        )
    return _redact_sensitive(title), _redact_sensitive(message)


def _source_id(docket: DocketView, decision: MatchDecision) -> str:
    key = docket.docket_id or canonical_identifier(docket.docket_number) or docket.docket_number
    return f"fl-federal:{key}:{decision.event_type}:{decision.client.bond_case_id}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _to_list(cursor, limit: int) -> list:
    if hasattr(cursor, "limit"):
        cursor = cursor.limit(limit)
    rows = await cursor.to_list(length=limit)
    return list(rows or [])


async def load_active_clients(db, limit: int = MAX_BONDS) -> list[ClientIdentity]:
    """Active, monitoring, and alert bonds, plus defendant and arrest identifiers."""
    bonds = await _to_list(
        db.active_bonds.find(
            {"status": {"$in": list(ACTIVE_STATUSES)}},
        ),
        limit,
    )
    defendant_ids = []
    for bond in bonds:
        for key in ("Defendant_ID", "defendant_id"):
            value = str(bond.get(key) or "").strip()
            if value:
                defendant_ids.append(value)
    defendants: list[dict] = []
    arrests: list[dict] = []
    if defendant_ids:
        unique_ids = list(dict.fromkeys(defendant_ids))
        defendants = await _to_list(
            db.defendants.find({"$or": [
                {"Defendant_ID": {"$in": unique_ids}},
                {"defendant_id": {"$in": unique_ids}},
            ]}),
            limit,
        )
        arrests = await _to_list(
            db.arrests.find({"$or": [
                {"Defendant_ID": {"$in": unique_ids}},
                {"defendant_id": {"$in": unique_ids}},
            ]}),
            limit,
        )
    by_defendant: dict[str, list[dict]] = {}
    for doc in defendants:
        for key in ("Defendant_ID", "defendant_id"):
            value = str(doc.get(key) or "").strip()
            if value:
                by_defendant.setdefault(value, []).append(doc)
    arrests_by_defendant: dict[str, list[dict]] = {}
    for doc in arrests:
        for key in ("Defendant_ID", "defendant_id"):
            value = str(doc.get(key) or "").strip()
            if value:
                arrests_by_defendant.setdefault(value, []).append(doc)

    clients = []
    for bond in bonds:
        defendant_id = str(bond.get("Defendant_ID") or bond.get("defendant_id") or "").strip()
        bond_case_id = str(bond.get("Bond_Case_ID") or bond.get("bond_case_id") or bond.get("_id") or "").strip()
        related = [bond, *by_defendant.get(defendant_id, []), *arrests_by_defendant.get(defendant_id, [])]
        names: list[str] = []
        dobs: set[str] = set()
        numbers: set[str] = set()
        for doc in related:
            names.extend(_collect_names(doc))
            dobs |= _collect_dobs(doc)
            numbers |= _collect_numbers(doc)
        deduped = tuple(dict.fromkeys(names))
        if not bond_case_id or not deduped:
            continue
        clients.append(ClientIdentity(
            bond_case_id=bond_case_id,
            defendant_id=defendant_id,
            names=deduped,
            dobs=frozenset(dobs),
            numbers=frozenset(numbers),
            status=str(bond.get("status") or "active"),
        ))
    return clients


def _surnames(clients: list[ClientIdentity]) -> set[str]:
    found = set()
    for client in clients:
        for name in client.names:
            parts = name_parts(name)
            if parts:
                found.add(parts[2])
    return found


def _might_be_ours(case_name: str, surnames: set[str]) -> bool:
    blob = (case_name or "").upper()
    return any(re.search(rf"\b{re.escape(surname)}\b", blob) for surname in surnames)


class _ScanStop(Exception):
    def __init__(self, warning: str, skipped: str):
        self.warning = warning
        self.skipped = skipped


def _stop_if_blocked(result: dict) -> None:
    """Stop the scan before any staff row is written."""
    if result.get("status_code") == 401 or result.get("error") in {"unauthorized", "no_token"}:
        raise _ScanStop(
            result.get("warning") or "CourtListener returned 401. Scan stopped before any alert was written.",
            "unauthorized",
        )
    if result.get("skipped") == "maintenance":
        raise _ScanStop(
            "CourtListener maintenance window. Scan skipped.",
            "maintenance",
        )
    if result.get("error") in {"ReadTimeout", "ConnectError"}:
        raise _ScanStop(
            "CourtListener rate limit or connection failure. Scan stopped with no alerts written.",
            "rate_limited",
        )


def _events_from_text(text: str) -> list[DocketEvent]:
    return [DocketEvent(event_type, label) for event_type, label in classify_bond_risk(text)]


def _hit_docket_id(hit: dict, search_type: str) -> str:
    for key in ("docket_id", "docketId"):
        if hit.get(key):
            return str(hit.get(key))
    if search_type in {"r", "d"} and hit.get("id"):
        return str(hit.get("id"))
    return ""


def _extend_events(docket: DocketView, text: str) -> None:
    have = {event.event_type for event in docket.events}
    for event in _events_from_text(text):
        if event.event_type not in have:
            docket.events.append(event)
            have.add(event.event_type)


async def _collect_dockets(client, clients: list[ClientIdentity], pause_s: float) -> list[DocketView]:
    """Search RECAP and dockets once each, then hydrate at most ``MAX_DOCKETS``."""
    import asyncio

    surnames = _surnames(clients)
    seeds: dict[str, dict] = {}
    for search_type in ("r", "rd", "d"):
        result = await client.search_limited(
            search_type=search_type,
            query=BOND_RISK_QUERY,
            courts=list(FL_FEDERAL_COURT_IDS),
            page_size=15,
        )
        _stop_if_blocked(result)
        if not result.get("success"):
            log.warning("Federal bond-risk search %s skipped: %s", search_type, result.get("error"))
            continue
        for hit in result.get("results") or []:
            if not isinstance(hit, dict):
                continue
            docket_id = _hit_docket_id(hit, search_type)
            if not str(docket_id).isdigit():
                continue
            court_id = _court_slug(hit)
            if court_id and court_id not in FL_FEDERAL_COURTS:
                continue
            number = _first_text(hit, "docketNumber", "docket_number")
            if number and _CIVIL_DOCKET.search(number) and not _CRIMINAL_DOCKET.search(number):
                continue
            case_name = _first_text(hit, "caseName", "case_name", "case_name_full")
            if case_name and not _might_be_ours(case_name, surnames):
                continue
            key = docket_id or canonical_identifier(number) or ""
            if not key:
                continue
            seed = seeds.setdefault(key, {"id": docket_id, "hits": []})
            seed["hits"].append(hit)
            if len(seeds) >= MAX_DOCKETS:
                break
        if len(seeds) >= MAX_DOCKETS:
            break

    views: list[DocketView] = []
    for seed in list(seeds.values())[:MAX_DOCKETS]:
        if pause_s:
            await asyncio.sleep(pause_s)
        view = await _hydrate(client, seed)
        if view and view.events and is_fl_federal_criminal(view.court_id, view.docket_number):
            views.append(view)
    return views


async def _hydrate(client, seed: dict) -> Optional[DocketView]:
    hits = seed.get("hits") or []
    first = hits[0] if hits else {}
    docket_id = str(seed.get("id") or "")
    brief: dict = {}
    parties: list[dict] = []
    entries: list[dict] = []
    if not str(docket_id).isdigit():
        return None
    brief_res = await client.get_docket_brief(int(docket_id))
    _stop_if_blocked(brief_res)
    if not brief_res.get("success"):
        log.warning("Docket %s brief unavailable: %s", docket_id, brief_res.get("error"))
    else:
        data = brief_res.get("data") or {}
        brief = data if isinstance(data, dict) else {}
    party_res = await client.list_docket_parties(int(docket_id))
    _stop_if_blocked(party_res)
    if not party_res.get("success"):
        # Without parties we cannot see a docket DOB, so we do not guess.
        log.warning("Docket %s parties unavailable: %s", docket_id, party_res.get("error"))
        return None
    parties = [row for row in (party_res.get("results") or []) if isinstance(row, dict)]
    entry_res = await client.list_docket_entries(int(docket_id))
    _stop_if_blocked(entry_res)
    if entry_res.get("success"):
        entries = [row for row in (entry_res.get("results") or []) if isinstance(row, dict)]

    court_id = _court_slug(brief) or _court_slug(first)
    docket_number = _first_text(brief, "docket_number") or _first_text(first, "docketNumber", "docket_number")
    case_name = (
        _first_text(brief, "case_name_full", "case_name")
        or _first_text(first, "caseName", "case_name", "case_name_full")
    )
    pacer = _first_text(brief, "pacer_case_id") or _first_text(first, "pacer_case_id")
    cause = _first_text(brief, "cause") or _first_text(first, "cause")
    names = defendant_names_from_caption(case_name)
    dobs: set[str] = set()
    for party in parties:
        party_name = str(party.get("name") or "").strip()
        if party_name:
            names.append(party_name)
        extra = str(party.get("extra_info") or "")
        dobs |= extract_labeled_dobs(extra)
        labeled = normalize_dob(str(party.get("date_of_birth") or ""))
        if labeled:
            dobs.add(labeled)
    view = DocketView(
        docket_id=docket_id,
        court_id=court_id,
        court_name=(SE_US_COURTS.get(court_id) or {}).get("name") or court_id.upper(),
        docket_number=docket_number,
        case_name=case_name,
        pacer_case_id=pacer,
        cause=cause,
        names=names,
        dobs=dobs,
        absolute_url=f"https://www.courtlistener.com/docket/{docket_id}/" if docket_id else "",
    )
    for value in (docket_number, pacer):
        ident = canonical_identifier(value)
        if ident:
            view.numbers.add(ident)
    for hit in hits:
        _extend_events(view, _first_text(hit, "description", "short_description", "snippet"))
        snippet = str(hit.get("snippet") or "")
        _extend_events(view, snippet)
        for doc in hit.get("recap_documents") or []:
            if isinstance(doc, dict):
                blob = " ".join(str(doc.get(key) or "") for key in ("description", "short_description", "snippet"))
                _extend_events(view, blob)
    for entry in entries:
        _extend_events(view, str(entry.get("description") or ""))
    return view


def _event_document(decision: MatchDecision, docket: DocketView, source_id: str) -> dict:
    confirmed = decision.status == "confirmed"
    title, message = _staff_copy(decision, docket)
    return {
        "source": SOURCE,
        "source_id": source_id,
        "monitor": "federal_bond_risk",
        "match_status": decision.status,
        "auto_linked": confirmed,
        "ambiguous": decision.ambiguous,
        "confirmations": list(decision.confirmations),
        "bond_case_id": decision.client.bond_case_id if confirmed else "",
        "candidate_bond_case_id": decision.client.bond_case_id,
        "defendant_id": decision.client.defendant_id if confirmed else "",
        "candidate_defendant_id": decision.client.defendant_id,
        "matched_defendant_id": decision.client.defendant_id if confirmed else None,
        "defendant_name": decision.client.names[0],
        "court_id": docket.court_id,
        "court_name": docket.court_name,
        "docket_id": docket.docket_id,
        "docket_number": docket.docket_number,
        "case_name": docket.case_name,
        "absolute_url": docket.absolute_url,
        "event_type": decision.event_type,
        "event_severity": "critical" if decision.event_type in {
            "failure_to_appear", "warrant", "revocation_of_release",
        } else "high",
        "description": message,
        "title": title,
        "acknowledged": False,
        "detected_at": _now(),
        "client_messages": 0,
    }


def _notification(decision: MatchDecision, docket: DocketView, source_id: str) -> dict:
    title, message = _staff_copy(decision, docket)
    confirmed = decision.status == "confirmed"
    return redact_internal_keys({
        "notification_id": uuid.uuid4().hex[:12],
        "type": "federal_bond_risk",
        "icon": "⚖️",
        "priority": "critical" if confirmed else "high",
        "color": "#dc2626" if confirmed else "#f59e0b",
        "title": title,
        "message": message,
        "entity_id": decision.client.bond_case_id,
        "entity_type": "federal_bond_risk",
        "metadata": {
            "match_status": decision.status,
            "auto_linked": confirmed,
            "ambiguous": decision.ambiguous,
            "confirmations": list(decision.confirmations),
            "court_id": docket.court_id,
            "court_name": docket.court_name,
            "docket_number": docket.docket_number,
            "docket_id": docket.docket_id,
            "event_type": decision.event_type,
            "source_id": source_id,
            "absolute_url": docket.absolute_url,
            "candidate_bond_case_id": decision.client.bond_case_id,
        },
        "read": False,
        "dismissed": False,
        "created_at": _now(),
    })


async def _store_decision(db, decision: MatchDecision, docket: DocketView) -> str:
    """Insert or upgrade one staff row. Returns ``new``, ``upgraded``, or ``duplicate``."""
    source_id = _source_id(docket, decision)
    existing = await db.docket_events.find_one({"source": SOURCE, "source_id": source_id})
    document = _event_document(decision, docket, source_id)
    if existing and existing.get("match_status") == "confirmed":
        return "duplicate"
    if existing and not (existing.get("match_status") == "possible" and decision.status == "confirmed"):
        return "duplicate"
    note = _notification(decision, docket, source_id)
    if existing:
        document["detected_at"] = existing.get("detected_at") or document["detected_at"]
        document["upgraded_at"] = _now()
        await db.docket_events.update_one({"_id": existing.get("_id")}, {"$set": document})
    else:
        await db.docket_events.insert_one(document)
    await db.notifications.insert_one(note)
    log.info(
        "Federal bond-risk %s court=%s event=%s auto_linked=%s",
        decision.status, docket.court_id, decision.event_type, decision.status == "confirmed",
    )
    return "upgraded" if existing else "new"


def _alert_slack(decision: MatchDecision, docket: DocketView, notifier) -> bool:
    title, message = _staff_copy(decision, docket)
    confirmed = decision.status == "confirmed"
    return bool(notifier.notify_alert(
        title=title[:140],
        message=message,
        level="critical" if confirmed else "warning",
        details={
            "court": docket.court_name,
            "docket": docket.docket_number,
            "event": decision.event_type,
            "match_status": decision.status,
            "auto_linked": confirmed,
            "bond_case_id": decision.client.bond_case_id if confirmed else "",
        },
        source="FederalBondRisk",
        bridge=False,
    ))


async def list_federal_bond_alerts(db, limit: int = 50) -> list[dict]:
    try:
        cursor = db.docket_events.find({"source": SOURCE})
        if hasattr(cursor, "sort"):
            cursor = cursor.sort("detected_at", -1)
        rows = await _to_list(cursor, min(max(int(limit or 50), 1), 100))
    except Exception as exc:
        log.warning("Federal bond-risk list failed: %s", type(exc).__name__)
        return []
    cleaned = []
    for row in rows:
        item = dict(row)
        if "_id" in item:
            item["_id"] = str(item["_id"])
        cleaned.append(item)
    return cleaned


async def run_federal_bond_risk_scan(
    db,
    *,
    client=None,
    notifier=None,
    pause_s: float = 0.3,
    clients: Optional[list[ClientIdentity]] = None,
) -> dict:
    """Scan Florida federal criminal dockets. Flag off and missing token skip cleanly."""
    health = federal_bond_risk_health()
    base = {
        "success": True,
        "rows_written": 0,
        "confirmed": 0,
        "possible": 0,
        "slack_sent": 0,
        "client_messages": 0,
        **health,
    }
    if not health["enabled"]:
        return {**base, "skipped": "flag_off"}
    if not health["token_configured"]:
        return {**base, "skipped": "no_token"}

    owns_client = client is None
    if client is None:
        from dashboard.services.courtlistener_client import CourtListenerClient

        client = CourtListenerClient(api_token=os.getenv("COURTLISTENER_API_TOKEN", "").strip())
    try:
        if clients is None:
            clients = await load_active_clients(db)
        if not clients:
            return {**base, "skipped": "no_active_clients", "bonds_considered": 0}
        try:
            dockets = await _collect_dockets(client, clients, pause_s)
        except _ScanStop as stop:
            return {**base, "skipped": stop.skipped, "warning": stop.warning, "rows_written": 0}

        decisions: list[tuple[MatchDecision, DocketView]] = []
        for docket in dockets:
            for decision in decide_matches(clients, docket):
                decisions.append((decision, docket))

        if notifier is None:
            from writers.slack_notifier import SlackNotifier

            notifier = SlackNotifier()

        written = 0
        confirmed = 0
        possible = 0
        slack_sent = 0
        for decision, docket in decisions:
            if written >= MAX_ROWS:
                base["truncated"] = True
                break
            outcome = await _store_decision(db, decision, docket)
            if outcome == "duplicate":
                continue
            written += 1
            if decision.status == "confirmed":
                confirmed += 1
            else:
                possible += 1
            if slack_sent < MAX_SLACK:
                if _alert_slack(decision, docket, notifier):
                    slack_sent += 1
        return {
            **base,
            "skipped": None,
            "warning": None,
            "bonds_considered": len(clients),
            "dockets_reviewed": len(dockets),
            "rows_written": written,
            "confirmed": confirmed,
            "possible": possible,
            "slack_sent": slack_sent,
            "client_messages": 0,
        }
    finally:
        if owns_client:
            close = getattr(client, "close", None)
            if close:
                await close()
