"""
Florida federal bond-risk alerts.

Watches CourtListener v4 for criminal cases in FLMD, FLSD, and FLND that
show a bond-risk event for someone who already has an active Shamrock bond.
Reuses ``CourtListenerClient`` (token, 429 backoff, field selection, a
bounded number of recent pages).

Matching fails closed:
  * full name, plus a date of birth on the party whose name matched, or a
    case or agency number we already hold → confirmed and auto-linked
  * full name only, or a date of birth that belongs to a different party →
    ``possible`` for staff review, not auto-linked
  * the matched party's own date of birth conflicts, a surname-only caption,
    or two confirmed clients for the same filing → no auto-link

``FEDERAL_BOND_RISK_ALERTS`` defaults off. Nothing is written and CourtListener
is not called until it is on. With the flag on and no token, the scan skips
and the status payload carries a warning. Output is a CRM notification plus
a docket-monitor row and a Slack ``#alerts`` post. No client text, email, or
Apple chat.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
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
EVENT_INDEX = "idx_federal_bond_risk_source"
NOTIFICATION_INDEX = "idx_federal_bond_risk_notification"
AUDIT_INDEX = "idx_federal_bond_risk_audit"
_ON = {"1", "true", "yes", "on"}
ACTIVE_STATUSES = ("active", "monitoring", "alert", "reinstated")
FL_FEDERAL_COURTS = frozenset(FL_FEDERAL_COURT_IDS)
BOND_PAGE = 200
# Search already stops at 50 pages. Hydrate every seed from that window.
# 3,000 is only a ceiling. Past it, the next scan continues where this one stopped.
HYDRATE_CEILING = 3000
MAX_ROWS = 30
MAX_SLACK = 10
LOOKBACK_DAYS = 45
SLACK_CLAIM_TTL = timedelta(minutes=30)
# Safety ceiling only. Paging stops when the filing dates leave the window.
MAX_SEARCH_PAGES = 50
MAX_ENTRY_PAGES = 50

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
        r"revoc\w*\s+(?:of\s+)?(?:pretrial\s+)?release|revoke\s+(?:the\s+)?(?:bond|release|pretrial)|bond\s+revoc\w*|motion\s+to\s+revoke\s+bond",
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


def _match_paren(source: str, start: int) -> int:
    depth = 0
    index = start
    while index < len(source):
        if source[index] == "\\":
            index += 2
            continue
        if source[index] == "(":
            depth += 1
        elif source[index] == ")":
            depth -= 1
            if depth == 0:
                return index
        index += 1
    raise ValueError(source)


def _split_top(source: str, sep: str) -> list[str]:
    parts: list[str] = []
    buf: list[str] = []
    depth = 0
    index = 0
    while index < len(source):
        if source.startswith(sep, index) and depth == 0:
            parts.append("".join(buf))
            buf = []
            index += len(sep)
            continue
        char = source[index]
        if char == "\\":
            buf.append(source[index:index + 2])
            index += 2
            continue
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        buf.append(char)
        index += 1
    parts.append("".join(buf))
    return parts


def _expand_regex_literals(source: str) -> list[str]:
    """Word sequences a regex alternative can match, with the operators removed."""
    source = re.sub(r"\(\?[aiLmsux]+\)", "", source)
    out: list[str] = []
    for phrase in _expand_expr(source):
        cleaned = re.sub(r"\s+", " ", phrase).strip()
        if cleaned and re.search(r"[A-Za-z]", cleaned):
            out.append(cleaned)
    return out


def _expand_expr(source: str) -> list[str]:
    alternatives = _split_top(source, "|")
    if len(alternatives) > 1:
        phrases: list[str] = []
        for alternative in alternatives:
            phrases.extend(_expand_expr(alternative))
        return phrases
    return _expand_concat(source)


_STEM_EXPANSIONS: dict[str, tuple[str, ...]] = {
    "revoc": ("revocation",),
    "modif": ("modify", "modifying", "modified", "modification"),
}


def _expand_concat(source: str) -> list[str]:
    pieces: list[list[str]] = []
    index = 0
    while index < len(source):
        if source.startswith(r"\b", index):
            index += 2
            continue
        if source.startswith(r"\s+", index):
            pieces.append([" "])
            index += 3
            continue
        if source.startswith(r"\s", index):
            pieces.append([" "])
            index += 2
            continue
        if source.startswith(r"\w*", index) or source.startswith(r"\w+", index):
            if pieces and len(pieces[-1]) == 1:
                stem_match = re.search(r"([A-Za-z]+)$", pieces[-1][0])
                if stem_match:
                    stem = stem_match.group(1).lower()
                    expanded = _STEM_EXPANSIONS.get(stem)
                    if expanded:
                        prefix = pieces[-1][0][:stem_match.start(1)]
                        pieces[-1] = [f"{prefix}{word}" for word in expanded]
                        index += 3
                        continue
            return []
        if source[index] == "(":
            end = _match_paren(source, index)
            inner = source[index + 1:end]
            optional = end + 1 < len(source) and source[end + 1] == "?"
            if inner.startswith("?:"):
                inner = inner[2:]
            elif inner.startswith("?"):
                return []
            options = _expand_expr(inner)
            if optional:
                options = ["", *options]
            pieces.append(options or [""])
            index = end + 1 + (1 if optional else 0)
            continue
        if source[index] == "\\":
            return []
        end = index
        while end < len(source) and source[end] not in "(|)\\":
            end += 1
        pieces.append([source[index:end]])
        index = end
    combos = [""]
    for options in pieces:
        combos = [left + right for left in combos for right in options]
    phrases = []
    for combo in combos:
        text = re.sub(r"\s+", " ", combo)
        if text.strip() and "\\" not in text:
            phrases.append(text)
    return phrases


def search_phrases() -> list[str]:
    """Every literal the classifier can match, so the CourtListener query cannot drift."""
    found: list[str] = []
    seen: set[str] = set()
    for _event_type, label, pattern in _EVENT_RULES:
        candidates = _expand_regex_literals(pattern.pattern)
        if pattern.search(label):
            candidates.append(label)
        for phrase in candidates:
            phrase = re.sub(r"\s+", " ", phrase).strip()
            if not phrase or not pattern.search(phrase):
                continue
            key = phrase.lower()
            if len(re.sub(r"[^a-z0-9]", "", key)) < 3 or key in seen:
                continue
            seen.add(key)
            found.append(phrase)
    found.sort(key=lambda phrase: (-len(phrase), phrase.lower()))
    return found


def bond_risk_query() -> str:
    parts = []
    for phrase in search_phrases():
        parts.append(f'"{phrase}"' if " " in phrase else phrase)
    return " OR ".join(parts)


BOND_RISK_QUERY = bond_risk_query()
# These phrases can describe counsel, the government, or another person.
# Without a named defendant they stay possible, even on a one-defendant docket.
_ACTOR_SENSITIVE = frozenset({
    "failure_to_appear",
    "warrant",
    "revocation_of_release",
    "modification_of_release",
})

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


_SURNAME_PARTICLES = frozenset({
    "ABU", "AL", "BIN", "DA", "DAS", "DE", "DEL", "DEN", "DER", "DI", "DO",
    "DOS", "DU", "EL", "LA", "LAS", "LE", "LOS", "MAC", "MC", "SAN", "ST",
    "TEN", "TER", "VAN", "VON", "Y",
})


def _split_natural_name(tokens: list[str]) -> tuple[str, tuple[str, ...], str]:
    """Given name first. Surname particles stay on the surname, as in comma order."""
    end = len(tokens) - 1
    start = end
    while start > 1 and tokens[start - 1] in _SURNAME_PARTICLES:
        start -= 1
    return tokens[0], tuple(tokens[1:start]), " ".join(tokens[start:])


def name_parts(value: str) -> Optional[tuple[str, tuple[str, ...], str]]:
    """Return (first, middles, last) for a full name, or None.

    A single token or a one-letter given name is not a full name.
    ``John De La Cruz`` and ``De La Cruz, John`` share the surname DE LA CRUZ.
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
        first, middles, surname = _split_natural_name(tokens)
    if len(first) < 2 or len(surname.replace(" ", "")) < 2:
        return None
    return first, middles, surname


def _middles_compatible(left: tuple[str, ...], right: tuple[str, ...]) -> bool:
    if len(left) != len(right):
        return False
    for x, y in zip(left, right):
        if x == y:
            continue
        if len(x) == 1 and y.startswith(x):
            continue
        if len(y) == 1 and x.startswith(y):
            continue
        return False
    return True


def full_name_equal(left: str, right: str) -> bool:
    """Exact first and last name. Middles may be absent. Conflicting middles fail."""
    a = name_parts(left)
    b = name_parts(right)
    if not a or not b:
        return False
    if a[0] != b[0] or a[2] != b[2]:
        return False
    if a[1] and b[1]:
        return _middles_compatible(a[1], b[1])
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


def _dobs_from_extra(value) -> set[str]:
    """Dates of birth inside one PACER extra_info blob."""
    if isinstance(value, dict):
        found = set()
        for key in _DOB_FIELDS:
            norm = normalize_dob(str(value.get(key) or ""))
            if norm:
                found.add(norm)
        found |= extract_labeled_dobs(" ".join(str(item) for item in value.values() if item))
        return found
    return extract_labeled_dobs(str(value or ""))


def _party_type_docket_id(party_type: dict) -> str:
    explicit = str(party_type.get("docket_id") or "").strip()
    if explicit:
        return explicit
    docket = party_type.get("docket")
    if isinstance(docket, dict):
        nested = str(docket.get("id") or "").strip()
        if nested:
            return nested
        docket = docket.get("url") or docket.get("resource_uri") or ""
    match = re.search(r"/dockets/(\d+)/?", str(docket or ""))
    return match.group(1) if match else ""


def party_dobs_for_docket(party: dict, docket_id: str) -> set[str]:
    """Dates of birth on this party's type for this docket.

    CourtListener nests that text on ``party_types[].extra_info``. A type for
    another docket is ignored. The flat ``Party.extra_info`` field is used
    only when the response has no party types at all.
    """
    types = party.get("party_types") or []
    if isinstance(types, dict):
        types = [types]
    wanted = str(docket_id or "").strip()
    found: set[str] = set()
    saw_type = False
    for party_type in types:
        if not isinstance(party_type, dict):
            continue
        saw_type = True
        type_docket = _party_type_docket_id(party_type)
        if wanted and type_docket != wanted:
            continue
        found |= _dobs_from_extra(party_type.get("extra_info"))
    if saw_type:
        return found
    return _dobs_from_extra(party.get("extra_info"))


def canonical_identifier(value: str) -> Optional[str]:
    """Case or agency number. Requires a digit and at least 5 alphanumeric characters."""
    raw = str(value or "").strip()
    if not raw or len(raw) > 80:
        return None
    compact = re.sub(r"\s+", "", raw.upper()).replace("–", "-").replace("—", "-")
    # 2:24-cr-00123-JES-NPM and 2:24-cr-00123-SPC are the same case.
    compact = re.sub(r"(?:-[A-Z]{2,4})+$", "", compact)
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
    dob_conflict: bool = False


@dataclass(frozen=True)
class DocketParty:
    """One person on the docket. Their date of birth stays on this record."""

    name: str
    dobs: frozenset[str] = frozenset()


@dataclass
class DocketEvent:
    event_type: str
    label: str
    filing_id: str
    affected_names: tuple[str, ...] = ()


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
    parties: list[DocketParty] = field(default_factory=list)
    numbers: set[str] = field(default_factory=set)
    events: list[DocketEvent] = field(default_factory=list)
    seen_filing_ids: set[str] = field(default_factory=set)
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
    filing_id: str
    ambiguous: bool = False


def party_dobs_for_client(client: ClientIdentity, docket: DocketView) -> Optional[set[str]]:
    """Dates of birth on parties whose name matches the client.

    ``None`` means no party name matched (a caption-only hit). An empty set
    means a party matched and that party has no date of birth. Dates of birth
    on other parties are not included.
    """
    matched = [
        party for party in docket.parties
        if any(full_name_equal(name, party.name) for name in client.names)
    ]
    if not matched:
        return None
    found: set[str] = set()
    for party in matched:
        found.update(party.dobs)
    return found


def match_client(client: ClientIdentity, docket: DocketView) -> tuple[Optional[str], tuple[str, ...]]:
    """Return (confirmed|possible|None, confirmation labels).

    A date of birth confirms the link only when it is on the party whose name
    matched and that party has one date of birth. Several dates on that party
    stay possible for staff review. A different party's date of birth does not
    confirm and does not conflict. The matched party's own single date of
    birth, when it differs, drops the row even if a case number overlaps.
    """
    if not any(full_name_equal(name, other) for name in client.names for other in docket.names):
        return None, ()
    # Held dates that disagree cannot confirm, including through a case number.
    if client.dob_conflict:
        return "possible", ()
    party_dobs = party_dobs_for_client(client, docket)
    usable_dobs = set(client.dobs)
    if party_dobs is not None and len(party_dobs) > 1:
        # The matched party has more than one date of birth. One overlap does
        # not confirm, and a case number does not paper over that conflict.
        if usable_dobs and party_dobs and usable_dobs.isdisjoint(party_dobs):
            return None, ()
        return "possible", ()
    if party_dobs is not None and usable_dobs and party_dobs and usable_dobs.isdisjoint(party_dobs):
        return None, ()
    confirmations = []
    if party_dobs and usable_dobs & party_dobs:
        confirmations.append("dob")
    held_numbers = {
        ident for ident in (canonical_identifier(number) for number in client.numbers) if ident
    }
    if held_numbers & docket.identifier_pool():
        confirmations.append("case_number")
    if confirmations:
        return "confirmed", tuple(confirmations)
    return "possible", ()


_PARTY_TAIL = re.compile(
    r"\b(?:denying|granting|granted|denies|grants|motion|ordering|"
    r"setting|modifying|modify|modification|entered|issued|filed|ordered|"
    r"regarding|concerning|pursuant|under|upon)\b",
    re.I,
)


def _party_chunk(chunk: str) -> str:
    """Keep the person's name and drop the rest of the docket sentence."""
    clipped = _PARTY_TAIL.split(chunk or "", maxsplit=1)[0]
    clipped = re.sub(r"\([^)]*\)", "", clipped).strip(" ,.-")
    words = clipped.split()
    if len(words) > 6:
        clipped = " ".join(words[:6])
    return clipped.strip(" ,.-")


def parties_named_in_filing(text: str) -> tuple[str, ...]:
    """People a filing names, such as ``as to Jane Roe``. Empty when it names nobody."""
    found: list[str] = []
    patterns = (
        r"\bas to\b(?:\s+the)?(?:\s+defendant)?\s+([^.;\n]+)",
        r"\bfor defendant\s+([^.;\n]+)",
    )
    for pattern in patterns:
        for match in re.finditer(pattern, text or "", flags=re.I):
            chunk = _party_chunk(match.group(1))
            for part in re.split(r"\s+(?:and|&)\s+|;\s*", chunk):
                part = part.strip(" ,.-")
                if name_parts(part):
                    found.append(part)
    return tuple(dict.fromkeys(found))


_NAME_SEGMENT_SPLIT = re.compile(
    r"[;\n\r:()\[\]/|&]+"
    r"|\s+vs?\.?\s+"
    r"|\s+(?:and|or)\s+"
    r"|\s+[-–—]+\s+"
    r"|(?<!\b[A-Za-z])(?<!\bSt)(?<!\bJr)(?<!\bSr)\.(?=\s|$)",
    re.I,
)
_NON_MIDDLE_TOKENS = frozenset({
    "AND", "OR", "VS", "V", "FOR", "TO", "BY", "OF", "IN", "ON", "AT",
    "WITH", "FROM", "RE", "AS", "THE", "PER", "DEFENDANT", "PLAINTIFF",
    "COUNSEL", "ATTORNEY", "JUDGE", "COURT", "WARRANT", "BOND", "ORDER",
    "MOTION", "UNITED", "STATES", "AMERICA", "USA", "ET", "AL",
})
_COMMA_NAME_LEAD_INS = frozenset({
    "TO", "FOR", "DEFENDANT", "DEFENDANTS", "DEF", "RE", "BY", "OF",
    "AGAINST", "CONCERNING", "REGARDING", "AS", "ON", "PER",
    "MOVANT", "PETITIONER", "RESPONDENT",
})
_COMMA_NAME_TRAILERS = frozenset({
    "DENYING", "GRANTING", "GRANTED", "DENIES", "GRANTS", "MOTION",
    "ORDERING", "SETTING", "MODIFYING", "MODIFY", "MODIFICATION",
    "ENTERED", "ISSUED", "FILED", "ORDERED", "REGARDING", "CONCERNING",
    "PURSUANT", "UNDER", "UPON", "FAILED", "APPEARED", "SIGNED",
    "WAIVED", "REVOKED", "MODIFIED", "SET", "HELD", "WAS", "IS",
    "HAS", "HAD", "DETAINED", "RELEASED", "ARRESTED",
})


def _name_in_text(name: str, text: str) -> bool:
    """True only when ``name`` appears as one contiguous ordered sequence."""
    parts = name_parts(name)
    if not parts or not text:
        return False
    first, middles, last = parts
    last_seq = tuple(last.split())
    last_len = len(last_seq)

    for segment in _NAME_SEGMENT_SPLIT.split(text):
        if not segment.strip():
            continue
        comma_pieces = segment.split(",")
        for chunk in comma_pieces:
            toks = _tokens(chunk)
            for i, tok in enumerate(toks):
                if tok != first:
                    continue
                if i + 1 + last_len <= len(toks) and tuple(toks[i + 1 : i + 1 + last_len]) == last_seq:
                    return True
                if middles:
                    mid_len = len(middles)
                    j = i + 1 + mid_len
                    if (
                        j + last_len <= len(toks)
                        and tuple(toks[j : j + last_len]) == last_seq
                        and _middles_compatible(middles, tuple(toks[i + 1 : j]))
                    ):
                        return True
                elif (
                    i + 2 + last_len <= len(toks)
                    and tuple(toks[i + 2 : i + 2 + last_len]) == last_seq
                    and toks[i + 1] not in _NON_MIDDLE_TOKENS
                ):
                    return True
        for k in range(len(comma_pieces) - 1):
            left_toks = _tokens(comma_pieces[k])
            right_toks = _tokens(comma_pieces[k + 1])
            if not (
                len(left_toks) >= last_len
                and tuple(left_toks[-last_len:]) == last_seq
                and (len(left_toks) == last_len or left_toks[-last_len - 1] in _COMMA_NAME_LEAD_INS)
                and right_toks
                and right_toks[0] == first
            ):
                continue
            tail = right_toks[1:]
            if not tail or tail[0] in _COMMA_NAME_TRAILERS:
                return True
            if middles:
                mid_len = len(middles)
                if (
                    len(tail) >= mid_len
                    and _middles_compatible(middles, tuple(tail[:mid_len]))
                    and (len(tail) == mid_len or tail[mid_len] in _COMMA_NAME_TRAILERS)
                ):
                    return True
                continue
            if len(tail[0]) == 1 and (len(tail) == 1 or tail[1] in _COMMA_NAME_TRAILERS):
                return True
    return False


def parties_affected_by_text(text: str, party_names: list[str]) -> tuple[str, ...]:
    """Who a filing is about. A single named party does not attach to the others.

    A capture that is not an exact party name falls back to the docket party
    named inside it, so a long order title cannot rename the defendant.
    """
    names = list(party_names or [])
    explicit = parties_named_in_filing(text)
    if explicit:
        matched: list[str] = []
        leftover: list[str] = []
        for captured in explicit:
            exact = [name for name in names if full_name_equal(captured, name)]
            if exact:
                matched.extend(exact)
                continue
            inside = [name for name in names if _name_in_text(name, captured)]
            if len(inside) == 1:
                matched.append(inside[0])
                continue
            if not names and name_parts(captured):
                leftover.append(captured)
        if matched:
            return tuple(dict.fromkeys(matched))
        mentioned = [name for name in names if _name_in_text(name, text)]
        if len(mentioned) == 1:
            return (mentioned[0],)
        if mentioned:
            return tuple(dict.fromkeys(mentioned))
        return tuple(dict.fromkeys(leftover))
    mentioned = [name for name in names if _name_in_text(name, text)]
    if len(mentioned) == 1:
        return (mentioned[0],)
    return tuple(dict.fromkeys(mentioned))


def _filing_affects_client(event: DocketEvent, client: ClientIdentity) -> bool:
    """A filing that names someone else does not attach to this client."""
    if not event.affected_names:
        return True
    return any(
        full_name_equal(named, held)
        for named in event.affected_names
        for held in client.names
    )


def _person_key(client: ClientIdentity) -> tuple:
    """One person, even when that person has more than one active bond."""
    if client.defendant_id:
        return ("defendant", client.defendant_id)
    if client.names:
        parts = name_parts(client.names[0])
        if parts:
            return ("name", parts[0], parts[2])
    return ("bond", client.bond_case_id)


def _docket_people(docket: DocketView) -> set[tuple]:
    """Defendant-side people. The United States is not a defendant."""
    found: set[tuple] = set()
    for party in docket.parties:
        label = " ".join(_tokens(party.name))
        if label in {"UNITED STATES", "UNITED STATES OF AMERICA", "USA"}:
            continue
        parts = name_parts(party.name)
        if parts:
            found.add((parts[0], parts[2]))
    return found


def decide_matches(clients: list[ClientIdentity], docket: DocketView) -> list[MatchDecision]:
    """One decision per client per event. Two confirmed people on one event are not linked.

    A filing that names nobody is not confirmed when the docket has more than
    one defendant, or when the phrase can describe someone other than the
    defendant. Several bonds for the same defendant are still one person.
    """
    decisions: list[MatchDecision] = []
    for event in docket.events:
        group: list[MatchDecision] = []
        for client in clients:
            if not _filing_affects_client(event, client):
                continue
            status, confirmations = match_client(client, docket)
            if not status:
                continue
            if status == "confirmed" and not event.affected_names and (
                len(_docket_people(docket)) > 1
                or event.event_type in _ACTOR_SENSITIVE
            ):
                status = "possible"
                confirmations = ()
            group.append(MatchDecision(
                client=client,
                status=status,
                confirmations=confirmations,
                event_type=event.event_type,
                event_label=event.label,
                filing_id=event.filing_id,
            ))
        confirmed = [row for row in group if row.status == "confirmed"]
        people = {_person_key(row.client) for row in confirmed}
        if len(people) > 1:
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


def filing_window_start(now: Optional[datetime] = None) -> str:
    """First calendar day of the recent-filings window, UTC."""
    moment = now or datetime.now(timezone.utc)
    return (moment.date() - timedelta(days=LOOKBACK_DAYS)).isoformat()


def _parse_day(value: str) -> Optional[datetime]:
    raw = str(value or "").strip()[:10]
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def filing_in_window(value: str, start: Optional[str] = None) -> bool:
    """True when a filing date is inside the window. A missing date is kept."""
    day = _parse_day(value)
    if day is None:
        return True
    window = _parse_day(start or filing_window_start())
    if window is None:
        return True
    return day >= window


def _source_id(docket: DocketView, decision: MatchDecision) -> str:
    key = docket.docket_id or canonical_identifier(docket.docket_number) or docket.docket_number
    filing = decision.filing_id or "filing-unknown"
    base = f"fl-federal:{key}:{decision.event_type}:{filing}:{decision.client.bond_case_id}"
    token = _tenant_scope_token()
    return f"{token}:{base}" if token else base


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _to_list(cursor, limit: int) -> list:
    if hasattr(cursor, "limit"):
        cursor = cursor.limit(limit)
    rows = await cursor.to_list(length=limit)
    return list(rows or [])


async def _each_page(collection, query: dict, page_size: int = 200):
    """Every matching row, in ``_id`` order, one page after another."""
    last_id = None
    step = max(int(page_size or 200), 1)
    while True:
        page_query = dict(query)
        if last_id is not None:
            page_query["_id"] = {"$gt": last_id}
        cursor = collection.find(page_query)
        if hasattr(cursor, "sort"):
            cursor = cursor.sort("_id", 1)
        page = await _to_list(cursor, step)
        if not page:
            return
        yield page
        tail = page[-1].get("_id")
        if tail is None or tail == last_id or len(page) < step:
            return
        last_id = tail


async def _page_bonds(db, page_size: int) -> list[dict]:
    """Every open bond, including reinstated ones, in ``_id`` order."""
    bonds: list[dict] = []
    last_id = None
    while True:
        query: dict = {"status": {"$in": list(ACTIVE_STATUSES)}}
        if last_id is not None:
            query["_id"] = {"$gt": last_id}
        cursor = db.active_bonds.find(query)
        if hasattr(cursor, "sort"):
            cursor = cursor.sort("_id", 1)
        page = await _to_list(cursor, page_size)
        if not page:
            break
        bonds.extend(page)
        tail = page[-1].get("_id")
        if tail is None or len(page) < page_size:
            break
        last_id = tail
    return bonds


async def _related_by_ids(collection, ids: list[str], page_size: int = BOND_PAGE) -> list[dict]:
    """Every defendant or arrest row for these ids.

    One prolific defendant must not use up the page and hide another person's
    arrest. The cursor keeps going until that chunk is exhausted.
    """
    found: list[dict] = []
    seen: set = set()
    step = max(int(page_size or BOND_PAGE), 1)
    for start in range(0, len(ids), step):
        chunk = ids[start:start + step]
        last_id = None
        while True:
            query: dict = {"$or": [
                {"Defendant_ID": {"$in": chunk}},
                {"defendant_id": {"$in": chunk}},
            ]}
            if last_id is not None:
                query["_id"] = {"$gt": last_id}
            cursor = collection.find(query)
            if hasattr(cursor, "sort"):
                cursor = cursor.sort("_id", 1)
            page = await _to_list(cursor, step)
            if not page:
                break
            for row in page:
                marker = row.get("_id")
                if marker is not None and marker in seen:
                    continue
                if marker is not None:
                    seen.add(marker)
                found.append(row)
            tail = page[-1].get("_id")
            if tail is None or tail == last_id or len(page) < step:
                break
            last_id = tail
    return found


async def load_active_clients(db, page_size: int = BOND_PAGE) -> list[ClientIdentity]:
    """Open bonds (active, monitoring, alert, and reinstated), plus defendant and arrest identifiers."""
    bonds = await _page_bonds(db, page_size)
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
        defendants = await _related_by_ids(db.defendants, unique_ids, page_size)
        arrests = await _related_by_ids(db.arrests, unique_ids, page_size)
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
        numbers: set[str] = set()
        source_sets = []
        for doc in related:
            names.extend(_collect_names(doc))
            found = _collect_dobs(doc)
            if found:
                source_sets.append(found)
            numbers |= _collect_numbers(doc)
        dobs: set[str] = set()
        for found in source_sets:
            dobs |= found
        dob_conflict = len(dobs) > 1
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
            dob_conflict=dob_conflict,
        ))
    return clients


class _ScanStop(Exception):
    def __init__(self, warning: str, skipped: str):
        self.warning = warning
        self.skipped = skipped


def _scan_stopped(base: dict, stop: _ScanStop) -> dict:
    payload = {**base, "skipped": stop.skipped, "warning": stop.warning, "rows_written": 0}
    if stop.skipped in {
        "search_failed", "search_truncated", "index_unavailable",
        "party_hydration_failed", "entry_hydration_failed",
        "unauthorized", "rate_limited",
    }:
        payload["success"] = False
    return payload


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


def _events_from_text(text: str, filing_id: str, party_names: list[str] | None = None) -> list[DocketEvent]:
    named = parties_affected_by_text(text, list(party_names or []))
    return [
        DocketEvent(event_type, label, filing_id, named)
        for event_type, label in classify_bond_risk(text)
    ]


def _hit_entry_date(hit: dict) -> str:
    """Docket-entry date on a search hit. Case initiation (``dateFiled``) is ignored."""
    direct = _first_text(hit, "entry_date_filed", "entryDateFiled")
    if direct:
        return direct
    for doc in hit.get("recap_documents") or []:
        if isinstance(doc, dict):
            found = _first_text(doc, "entry_date_filed", "entryDateFiled")
            if found:
                return found
    return ""


def _norm_filing_text(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip().lower())


def _linked_entry_id(doc: dict) -> str:
    for key in ("docket_entry_id", "docketEntryId", "entry_id"):
        if doc.get(key):
            return str(doc[key])
    nested = doc.get("docket_entry")
    if isinstance(nested, dict) and nested.get("id"):
        return str(nested["id"])
    if isinstance(nested, str):
        tail = nested.rstrip("/").split("/")[-1]
        if tail.isdigit():
            return tail
    return ""


def _entry_document_ids(entry: dict) -> list[str]:
    raw = entry.get("recap_documents")
    if raw is None:
        raw = entry.get("recap_document")
    if isinstance(raw, dict):
        raw = [raw]
    found = []
    for doc in raw or []:
        if isinstance(doc, dict) and doc.get("id"):
            found.append(str(doc["id"]))
        elif str(doc).isdigit():
            found.append(str(doc))
    return found


def _hit_docket_id(hit: dict, search_type: str) -> str:
    for key in ("docket_id", "docketId"):
        if hit.get(key):
            return str(hit.get(key))
    if search_type in {"r", "d"} and hit.get("id"):
        return str(hit.get("id"))
    return ""


def _extend_events(docket: DocketView, text: str, filing_id: str, party_names: list[str] | None = None) -> None:
    """Keep every filing. A later copy of the same filing can add the party it names."""
    if not filing_id:
        return
    named = parties_affected_by_text(text, list(party_names or [party.name for party in docket.parties]))
    have = {(event.event_type, event.filing_id): event for event in docket.events}
    for event in _events_from_text(text, filing_id, party_names or [party.name for party in docket.parties]):
        key = (event.event_type, event.filing_id)
        existing = have.get(key)
        if existing is not None:
            if named:
                existing.affected_names = tuple(dict.fromkeys((*existing.affected_names, *named)))
            continue
        if named:
            event.affected_names = named
        docket.events.append(event)
        have[key] = event


def select_seed_batch(seeds: list[dict], offset: int, ceiling: int) -> tuple[list[dict], int, bool]:
    """One slice of the seed list, then the offset for the next scan.

    The slice does not wrap. A later scan starts at ``next_offset`` so a docket
    past this batch is hydrated instead of being skipped on every run.
    """
    ordered = sorted(seeds, key=lambda seed: str(seed.get("id") or ""))
    if not ordered:
        return [], 0, False
    limit = max(int(ceiling or 1), 1)
    start = int(offset or 0)
    if start < 0 or start >= len(ordered):
        start = 0
    batch = ordered[start:start + limit]
    next_offset = start + len(batch)
    if next_offset >= len(ordered):
        next_offset = 0
    return batch, next_offset, len(batch) < len(ordered)


def _tenant_scope_token() -> str:
    """Empty while multi-tenant is off, so existing single-tenant keys stay put."""
    from dashboard.tenancy.context import current_tenant_id
    from dashboard.tenancy.flag import multi_tenant_enabled

    if not multi_tenant_enabled():
        return ""
    tenant = re.sub(r"[^a-z0-9_-]", "", (current_tenant_id() or "unscoped").strip().lower())
    return tenant or "unscoped"


def _search_before_id(search_type: str) -> str:
    """One date checkpoint per search type. A shared id collides across tenants."""
    token = _tenant_scope_token()
    base = f"search_before:{search_type}"
    return base if not token else f"{base}:{token}"


def _oldest_entry_day(hits: list) -> str:
    days = []
    for hit in hits or []:
        if not isinstance(hit, dict):
            continue
        day = str(_hit_entry_date(hit) or "")[:10]
        if len(day) == 10 and day[4] == "-" and day[7] == "-":
            days.append(day)
    return min(days) if days else ""


async def _search_checkpoint(db, search_type: str) -> tuple[str, str]:
    """Saved upper date and the CourtListener ``next`` URL, if a slice remains."""
    collection = getattr(db, "federal_bond_risk_state", None) if db is not None else None
    if collection is None:
        return "", ""
    row = await collection.find_one({"_id": _search_before_id(search_type)}) or {}
    return str(row.get("before") or "")[:10], str(row.get("resume_url") or "")


async def _save_search_checkpoint(db, search_type: str, day: str = "", resume_url: str = "") -> None:
    collection = getattr(db, "federal_bond_risk_state", None) if db is not None else None
    if collection is None:
        return
    await collection.update_one(
        {"_id": _search_before_id(search_type)},
        {"$set": {
            "before": str(day or "")[:10],
            "resume_url": str(resume_url or ""),
            "updated_at": _now(),
        }},
        upsert=True,
    )


def _seed_cursor_id() -> str:
    """One cursor document per tenant. A shared ``_id`` collides when the proxy is on."""
    token = _tenant_scope_token()
    return "seed_cursor" if not token else f"seed_cursor:{token}"


async def _seed_offset(db) -> int:
    collection = getattr(db, "federal_bond_risk_state", None)
    if collection is None:
        return 0
    row = await collection.find_one({"_id": _seed_cursor_id()})
    try:
        return max(int((row or {}).get("offset") or 0), 0)
    except (TypeError, ValueError):
        return 0


async def _save_seed_offset(db, offset: int) -> None:
    collection = getattr(db, "federal_bond_risk_state", None)
    if collection is None:
        return
    await collection.update_one(
        {"_id": _seed_cursor_id()},
        {"$set": {"offset": int(offset), "updated_at": _now()}},
        upsert=True,
    )


async def _collect_dockets(
    client,
    pause_s: float,
    db=None,
) -> tuple[list[DocketView], bool, int, list[tuple[str, str, str]], list[str], list[str], list[str], list[str]]:
    """Search recent RECAP and docket pages, then hydrate the seed batch.

    Search checkpoints and seed offsets are returned rather than committed here
    so ``run_federal_bond_risk_scan`` only advances them once every match in
    the slice has been stored.

    Caption surnames are not a filter. A lead-defendant caption with "et al."
    still has to be hydrated so co-defendant parties can be matched.
    """
    import asyncio

    window = filing_window_start()
    seeds: dict[str, dict] = {}
    failures: list[str] = []
    successes = 0
    search_notes: list[str] = []
    stuck: list[str] = []
    pending_checkpoints: list[tuple[str, str, str]] = []
    for search_type in ("r", "rd", "d"):
        cursor, resume = await _search_checkpoint(db, search_type)
        result = await client.search_recent(
            search_type=search_type,
            query=BOND_RISK_QUERY,
            courts=list(FL_FEDERAL_COURT_IDS),
            entry_date_filed_after=window,
            entry_date_filed_before=None if resume else (cursor or None),
            start_url=resume or None,
            page_size=20,
            max_pages=MAX_SEARCH_PAGES,
            pause_s=pause_s,
            until_entry_before=window,
        )
        _stop_if_blocked(result)
        if not result.get("success"):
            detail = result.get("error") or f"HTTP {result.get('status_code') or 'error'}"
            failures.append(f"{search_type} {detail}")
            log.warning("Federal bond-risk search %s failed: %s", search_type, detail)
            continue
        successes += 1
        if result.get("truncated"):
            oldest = _oldest_entry_day(result.get("results") or [])
            resume_url = str(result.get("resume_url") or "")
            if resume_url and resume_url != resume:
                pending_checkpoints.append((search_type, oldest or cursor, resume_url))
                search_notes.append(
                    f"Search {search_type} stopped at the 1,000-hit ceiling on {oldest or 'the cutoff date'}. "
                    "The next scan continues from the remaining search cursor."
                )
            elif oldest and (not cursor or oldest < cursor):
                pending_checkpoints.append((search_type, oldest, ""))
                search_notes.append(
                    f"Search {search_type} stopped at the 1,000-hit ceiling on {oldest}. "
                    "The next scan continues with the older filings."
                )
            else:
                stuck.append(search_type)
        else:
            pending_checkpoints.append((search_type, "", ""))
        for hit in result.get("results") or []:
            if not isinstance(hit, dict):
                continue
            # dateFiled is the day the case was opened. A new warrant on an
            # older case is entry_date_filed. Only an entry date outside the
            # window drops the hit. A case date never does.
            entry_day = _hit_entry_date(hit)
            if entry_day and not filing_in_window(entry_day, window):
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
            key = docket_id or canonical_identifier(number) or ""
            if not key:
                continue
            seed = seeds.setdefault(key, {"id": docket_id, "hits": []})
            tagged = dict(hit)
            tagged["_search_type"] = search_type
            seed["hits"].append(tagged)

    if successes == 0 and failures:
        raise _ScanStop(
            "CourtListener search failed for every source ("
            + "; ".join(failures)
            + "). No alerts were written.",
            "search_failed",
        )
    if stuck:
        raise _ScanStop(
            "CourtListener search hit the 1,000-hit ceiling and the date window "
            "could not move (" + ", ".join(stuck) + "). No alerts were written.",
            "search_truncated",
        )

    batch, next_offset, truncated = select_seed_batch(
        list(seeds.values()),
        await _seed_offset(db) if db is not None else 0,
        HYDRATE_CEILING,
    )

    views: list[DocketView] = []
    party_failures: list[str] = []
    entry_failures: list[str] = []
    brief_failures: list[str] = []
    entry_attempts = 0
    for seed in batch:
        if pause_s:
            await asyncio.sleep(pause_s)
        view, party_error, entry_error, brief_error = await _hydrate(client, seed, pause_s)
        if party_error:
            party_failures.append(party_error)
            continue
        entry_attempts += 1
        if entry_error:
            entry_failures.append(entry_error)
        if brief_error:
            brief_failures.append(brief_error)
        if view and (view.events or view.seen_filing_ids) and is_fl_federal_criminal(view.court_id, view.docket_number):
            views.append(view)
    if batch and party_failures and len(party_failures) == len(batch):
        raise _ScanStop(
            "CourtListener party lists failed for every docket ("
            + "; ".join(party_failures[:8])
            + "). No alerts were written.",
            "party_hydration_failed",
        )
    hard_entry_failures = list(entry_failures)
    if entry_attempts and hard_entry_failures and len(hard_entry_failures) == entry_attempts:
        raise _ScanStop(
            "CourtListener docket entries failed for every docket ("
            + "; ".join(hard_entry_failures[:8])
            + "). No alerts were written.",
            "entry_hydration_failed",
        )
    return (
        views,
        truncated,
        next_offset,
        pending_checkpoints,
        party_failures,
        entry_failures,
        brief_failures,
        search_notes,
    )


async def _hydrate(client, seed: dict, pause_s: float = 0.0) -> tuple[Optional[DocketView], str, str, str]:
    hits = seed.get("hits") or []
    first = hits[0] if hits else {}
    docket_id = str(seed.get("id") or "")
    brief: dict = {}
    parties: list[dict] = []
    entries: list[dict] = []
    if not str(docket_id).isdigit():
        return None, "", "", ""
    brief_res = await client.get_docket_brief(int(docket_id))
    _stop_if_blocked(brief_res)
    brief_error = ""
    if not brief_res.get("success"):
        detail = brief_res.get("error") or f"HTTP {brief_res.get('status_code') or 'error'}"
        log.warning("Docket %s brief unavailable: %s", docket_id, detail)
        brief_error = f"{docket_id} {detail}"
    else:
        data = brief_res.get("data") or {}
        brief = data if isinstance(data, dict) else {}
    party_res = await client.list_docket_parties(
        int(docket_id), page_size=20, max_pages=50, pause_s=pause_s,
    )
    _stop_if_blocked(party_res)
    if not party_res.get("success"):
        # Without parties we cannot see a docket DOB, so we do not guess.
        detail = party_res.get("error") or f"HTTP {party_res.get('status_code') or 'error'}"
        log.warning("Docket %s parties unavailable: %s", docket_id, detail)
        return None, f"{docket_id} {detail}", "", ""
    if party_res.get("truncated"):
        log.warning("Docket %s party list was truncated", docket_id)
        return None, f"{docket_id} party_list_truncated", "", ""
    parties = [row for row in (party_res.get("results") or []) if isinstance(row, dict)]
    entry_res = await client.list_docket_entries(
        int(docket_id),
        page_size=20,
        max_pages=MAX_ENTRY_PAGES,
        pause_s=pause_s,
        until_date=filing_window_start(),
    )
    _stop_if_blocked(entry_res)
    entry_error = ""
    if entry_res.get("success") and entry_res.get("truncated"):
        # Still inside the lookback window when the safety ceiling was hit.
        # A partial list would hide a warrant or release, so this docket stops.
        log.warning("Docket %s entry list did not cover the lookback window", docket_id)
        return None, "", f"{docket_id} entry_list_truncated", ""
    if entry_res.get("success"):
        entries = [
            row for row in (entry_res.get("results") or [])
            if isinstance(row, dict) and filing_in_window(str(row.get("date_filed") or ""))
        ]
    else:
        detail = entry_res.get("error") or f"HTTP {entry_res.get('status_code') or 'error'}"
        log.warning("Docket %s entries unavailable: %s", docket_id, detail)
        entry_error = f"{docket_id} {detail}"

    court_id = _court_slug(brief) or _court_slug(first)
    docket_number = _first_text(brief, "docket_number") or _first_text(first, "docketNumber", "docket_number")
    case_name = (
        _first_text(brief, "case_name_full", "case_name")
        or _first_text(first, "caseName", "case_name", "case_name_full")
    )
    pacer = _first_text(brief, "pacer_case_id") or _first_text(first, "pacer_case_id")
    cause = _first_text(brief, "cause") or _first_text(first, "cause")
    names = defendant_names_from_caption(case_name)
    party_rows: list[DocketParty] = []
    for party in parties:
        party_name = str(party.get("name") or "").strip()
        if not party_name:
            continue
        names.append(party_name)
        party_dobs = party_dobs_for_docket(party, docket_id)
        party_rows.append(DocketParty(name=party_name, dobs=frozenset(party_dobs)))
    view = DocketView(
        docket_id=docket_id,
        court_id=court_id,
        court_name=(SE_US_COURTS.get(court_id) or {}).get("name") or court_id.upper(),
        docket_number=docket_number,
        case_name=case_name,
        pacer_case_id=pacer,
        cause=cause,
        names=names,
        parties=party_rows,
        absolute_url=f"https://www.courtlistener.com/docket/{docket_id}/" if docket_id else "",
    )
    for value in (docket_number, pacer):
        ident = canonical_identifier(value)
        if ident:
            view.numbers.add(ident)
    doc_to_entry: dict[str, str] = {}
    text_to_entry: dict[str, str] = {}
    for entry in entries:
        description = str(entry.get("description") or "")
        entry_token = entry.get("id") or entry.get("entry_number") or ""
        if entry_token:
            filing = f"entry:{entry_token}"
        elif description:
            filing = "entry:" + hashlib.sha256(description.encode()).hexdigest()[:12]
        else:
            filing = ""
        for doc_id in _entry_document_ids(entry):
            if entry_token:
                doc_to_entry[doc_id] = str(entry_token)
        if description and filing.startswith("entry:"):
            text_to_entry.setdefault(_norm_filing_text(description), filing)
        if filing:
            view.seen_filing_ids.add(filing)
        _extend_events(view, description, filing, [party.name for party in view.parties])
    for hit in hits:
        party_names = [party.name for party in view.parties]
        if hit.get("_search_type") == "rd":
            blob = " ".join(
                str(hit.get(key) or "")
                for key in ("description", "short_description", "snippet")
            )
            _attach_document(view, hit, blob, doc_to_entry, text_to_entry, party_names)
        for doc in hit.get("recap_documents") or []:
            if not isinstance(doc, dict):
                continue
            blob = " ".join(
                str(doc.get(key) or "") for key in ("description", "short_description", "snippet")
            )
            _attach_document(view, doc, blob, doc_to_entry, text_to_entry, party_names)
    return view, "", entry_error, brief_error


def _attach_document(
    view: DocketView,
    doc: dict,
    blob: str,
    doc_to_entry: dict[str, str],
    text_to_entry: dict[str, str],
    party_names: list[str],
) -> None:
    """Classify one RECAP document, using its docket entry as the filing id when known."""
    linked = _linked_entry_id(doc) or doc_to_entry.get(str(doc.get("id") or ""))
    if not linked:
        linked_filing = text_to_entry.get(_norm_filing_text(blob), "")
    else:
        linked_filing = f"entry:{linked}"
    if linked_filing:
        view.seen_filing_ids.add(linked_filing)
        _extend_events(view, blob, linked_filing, party_names)
        return
    doc_id = doc.get("id") or doc.get("pacer_doc_id") or ""
    if not doc_id and blob.strip():
        doc_id = hashlib.sha256(_norm_filing_text(blob).encode()).hexdigest()[:12]
    filing_id = f"doc:{doc_id}" if doc_id else ""
    if filing_id:
        view.seen_filing_ids.add(filing_id)
    _extend_events(view, blob, filing_id, party_names)


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
        "filing_id": decision.filing_id,
        "acknowledged": False,
        "detected_at": _now(),
        "client_messages": 0,
        "slack_delivered": False,
        "alert_complete": False,
        "slack_attempts": 0,
        "notification_written": False,
        "notification_status": "",
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
            "filing_id": decision.filing_id,
            "source_id": source_id,
            "absolute_url": docket.absolute_url,
            "candidate_bond_case_id": decision.client.bond_case_id,
        },
        "read": False,
        "dismissed": False,
        "created_at": _now(),
    })


def _duplicate_key(exc: Exception) -> bool:
    if getattr(exc, "code", None) == 11000:
        return True
    return "duplicate key" in str(exc).lower() or "E11000" in str(exc)


async def _index_present(collection, name: str) -> bool:
    listed = getattr(collection, "list_indexes", None)
    if listed is not None:
        cursor = listed()
        rows = await cursor.to_list(length=None) if hasattr(cursor, "to_list") else []
        return any(row.get("name") == name and row.get("unique") for row in rows or [])
    recorded = getattr(collection, "indexes", None)
    if not recorded:
        return False
    return any(kwargs.get("name") == name and kwargs.get("unique") is True for _keys, kwargs in recorded)


async def _ensure_unique_index(collection, keys, name: str, partial: dict) -> bool:
    create = getattr(collection, "create_index", None)
    if collection is None or create is None:
        return False
    try:
        await create(keys, unique=True, name=name, partialFilterExpression=partial, background=True)
    except Exception as exc:
        log.warning("Federal bond-risk index %s was not created: %s", name, type(exc).__name__)
        return False
    return await _index_present(collection, name)


async def ensure_federal_bond_alert_index(db) -> None:
    """Unique claim keys for the alert row, its notification, and its audit.

    A scan that cannot create or see an index stops before any alert.
    """
    events_ready = await _ensure_unique_index(
        getattr(db, "docket_events", None),
        [("source", 1), ("source_id", 1)],
        EVENT_INDEX,
        {"source": SOURCE},
    )
    notes_ready = await _ensure_unique_index(
        getattr(db, "notifications", None),
        [("type", 1), ("claim_key", 1)],
        NOTIFICATION_INDEX,
        {"type": "federal_bond_risk"},
    )
    audits_ready = await _ensure_unique_index(
        getattr(db, "audit_events", None),
        [("source", 1), ("event_id", 1)],
        AUDIT_INDEX,
        {"source": SOURCE},
    )
    if not events_ready or not notes_ready or not audits_ready:
        raise _ScanStop(
            "Federal bond-risk unique index was not created. Scan stopped before any alert was written.",
            "index_unavailable",
        )


async def _claim_insert(db, document: dict) -> str:
    """Insert the alert once. Returns ``inserted`` or ``exists``.

    One upsert plus the unique ``(source, source_id)`` index is the claim.
    A concurrent scan that loses the race does not insert a second row.
    """
    try:
        result = await db.docket_events.update_one(
            {"source": SOURCE, "source_id": document["source_id"]},
            {"$setOnInsert": document},
            upsert=True,
        )
    except Exception as exc:
        if _duplicate_key(exc):
            return "exists"
        raise
    if getattr(result, "upserted_id", None):
        return "inserted"
    return "exists"


def _notification_claim_key(source_id: str, status: str, revision: int = 0) -> str:
    """One key per transition. A later return to the same status is a new key."""
    token = _tenant_scope_token()
    prefix = f"{token}:" if token else ""
    return f"{prefix}{source_id}:{status}:{int(revision or 0)}"


def _next_notification_revision(existing: Optional[dict], new_status: str) -> int:
    stored = int((existing or {}).get("notification_revision") or 0)
    prior = str((existing or {}).get("match_status") or "")
    if prior and prior != new_status:
        return stored + 1
    return stored


async def _mark_notification(db, source_id: str, status: str, revision: int = 0) -> None:
    revision = int(revision or 0)
    version: dict = (
        {"$or": [
            {"notification_revision": 0},
            {"notification_revision": {"$exists": False}},
            {"notification_revision": None},
        ]}
        if revision == 0
        else {"notification_revision": revision}
    )
    status_match: dict = {"$or": [
        {"match_status": status},
        {"match_status": {"$exists": False}},
        {"match_status": None},
    ]}
    await db.docket_events.update_one(
        {"source": SOURCE, "source_id": source_id, "$and": [status_match, version]},
        {"$set": {"notification_written": True, "notification_status": status}},
    )


async def _ensure_notification(db, note: dict, source_id: str, status: str, revision: int = 0) -> bool:
    """Insert the CRM notification once. Overlapping scans share one row."""
    claim_key = _notification_claim_key(source_id, status, revision)
    stored = dict(note)
    stored["type"] = "federal_bond_risk"
    stored["claim_key"] = claim_key
    try:
        result = await db.notifications.update_one(
            {"type": "federal_bond_risk", "claim_key": claim_key},
            {"$setOnInsert": stored},
            upsert=True,
        )
    except Exception as exc:
        if not _duplicate_key(exc):
            log.warning("Federal bond-risk notification was not written: %s", type(exc).__name__)
            return False
        await _mark_notification(db, source_id, status, revision)
        return True
    if getattr(result, "upserted_id", None) or getattr(result, "matched_count", 0):
        await _mark_notification(db, source_id, status, revision)
        return True
    return False


def _audit_payload(source_id: str, before: dict, after: dict, reason: str, evidence: dict) -> dict:
    now = _now()
    return {
        "event_id": str(uuid.uuid4()),
        "event_type": "federal_bond_risk_match_changed",
        "action": "federal_bond_risk_match_changed",
        "entity_type": "docket_event",
        "entity_id": source_id,
        "actor": "system",
        "agent_name": "federal_bond_risk",
        "source": SOURCE,
        "reason": reason,
        "old_state": before,
        "new_state": after,
        "evidence": evidence,
        "timestamp": now,
        "created_at": now,
    }


def _pending_audits(*sources) -> list[dict]:
    """Pending audits from each source, one copy of each event id."""
    merged: list[dict] = []
    seen: set[str] = set()
    for source in sources:
        if isinstance(source, list):
            rows = source
        else:
            rows = (source or {}).get("audit_pending") or []
        for item in rows:
            if not isinstance(item, dict):
                continue
            event_id = str(item.get("event_id") or "")
            if event_id:
                if event_id in seen:
                    continue
                seen.add(event_id)
            merged.append(item)
    return merged


def _queue_match_audit(
    document: dict,
    source_id: str,
    before: dict,
    after: dict,
    reason: str,
    evidence: dict,
    prior: Optional[dict] = None,
) -> dict:
    """Keep this audit, and any still-unwritten earlier audits, on the row."""
    payload = _audit_payload(source_id, before, after, reason, evidence)
    document["audit_pending"] = _pending_audits(prior, document, [payload])
    return payload


async def _insert_audit(db, payload: dict) -> bool:
    """Claim one immutable audit. Overlapping scans share the ``event_id``."""
    collection = getattr(db, "audit_events", None)
    event_id = str(payload.get("event_id") or "")
    if collection is None or not event_id:
        return False
    stored = dict(payload)
    stored["source"] = SOURCE
    stored["event_id"] = event_id
    try:
        await collection.update_one(
            {"source": SOURCE, "event_id": event_id},
            {"$setOnInsert": stored},
            upsert=True,
        )
    except Exception as exc:
        if _duplicate_key(exc):
            return True
        log.warning("Federal bond-risk audit was not written: %s", type(exc).__name__)
        return False
    return True


async def _clear_pending_audit(db, source_id: str, event_id: str) -> None:
    """Drop one completed audit. A newer pending item is not rewritten away."""
    events = getattr(db, "docket_events", None)
    if events is None or not source_id or not event_id:
        return
    try:
        await events.update_one(
            {"source": SOURCE, "source_id": source_id},
            {"$pull": {"audit_pending": {"event_id": event_id}}},
        )
    except Exception as exc:
        log.warning("Federal bond-risk pending audit was not cleared: %s", type(exc).__name__)


async def _remember_audit(db, source_id: str, payload: dict) -> None:
    events = getattr(db, "docket_events", None)
    if events is None or not source_id:
        return
    event_id = str(payload.get("event_id") or "")
    if not event_id:
        return
    try:
        await events.update_one(
            {"source": SOURCE, "source_id": source_id},
            {"$addToSet": {"audit_pending": payload}},
        )
    except Exception as exc:
        log.warning("Federal bond-risk pending audit was not stored: %s", type(exc).__name__)


async def _flush_match_audit(db, source_id: str, payload: dict) -> bool:
    """Write one queued audit. A failed insert stays on the row for the next scan."""
    if await _insert_audit(db, payload):
        await _clear_pending_audit(db, source_id, str(payload.get("event_id") or ""))
        return True
    await _remember_audit(db, source_id, payload)
    return False


async def retry_pending_audits(db) -> int:
    """Write audits that were saved on the row after insert_one failed."""
    written = 0
    async for page in _each_page(db.docket_events, {"source": SOURCE}):
        for row in page:
            pending = [item for item in (row.get("audit_pending") or []) if isinstance(item, dict)]
            if not pending:
                continue
            failed = False
            for item in pending:
                if not await _insert_audit(db, item):
                    failed = True
                    break
                written += 1
                await _clear_pending_audit(db, str(row.get("source_id") or ""), str(item.get("event_id") or ""))
            if failed:
                return written
    return written


def _stored_notification(row: dict) -> dict:
    """Build a CRM notification from a stored alert, including withdrawn rows."""
    source_id = str(row.get("source_id") or "")
    status = str(row.get("match_status") or "possible")
    if status == "withdrawn" or row.get("downgrade_reason"):
        return _downgrade_note(row, source_id)
    confirmed = status == "confirmed" and bool(row.get("auto_linked"))
    return redact_internal_keys({
        "notification_id": uuid.uuid4().hex[:12],
        "type": "federal_bond_risk",
        "icon": "⚖️",
        "priority": "critical" if confirmed else "high",
        "color": "#dc2626" if confirmed else "#f59e0b",
        "title": row.get("title") or "Federal bond risk",
        "message": row.get("description") or "",
        "entity_id": row.get("bond_case_id") or row.get("candidate_bond_case_id") or "",
        "entity_type": "federal_bond_risk",
        "metadata": {
            "match_status": status,
            "auto_linked": confirmed,
            "ambiguous": bool(row.get("ambiguous")),
            "confirmations": list(row.get("confirmations") or []),
            "court_id": row.get("court_id") or "",
            "court_name": row.get("court_name") or "",
            "docket_number": row.get("docket_number") or "",
            "docket_id": row.get("docket_id") or "",
            "event_type": row.get("event_type") or "",
            "filing_id": row.get("filing_id") or "",
            "source_id": source_id,
            "absolute_url": row.get("absolute_url") or "",
            "candidate_bond_case_id": row.get("candidate_bond_case_id") or row.get("bond_case_id") or "",
        },
        "read": False,
        "dismissed": False,
        "created_at": _now(),
    })


async def retry_pending_notifications(
    db,
    *,
    skip_source_ids: Optional[set[str]] = None,
    statuses: Optional[set[str]] = None,
) -> int:
    """Write CRM notifications for any stored row (including withdrawn) whose write failed."""
    written = 0
    async for page in _each_page(db.docket_events, {"source": SOURCE}):
        for row in page:
            source_id = str(row.get("source_id") or "")
            status = str(row.get("match_status") or "")
            if not source_id or not status:
                continue
            if skip_source_ids and source_id in skip_source_ids:
                continue
            if statuses is not None and status not in statuses:
                continue
            written_flag = row.get("notification_written")
            if written_flag is None:
                continue
            if written_flag is True and str(row.get("notification_status") or "") == status:
                continue
            revision = int(row.get("notification_revision") or 0)
            if not await _ensure_notification(
                db, _stored_notification(row), source_id, status, revision,
            ):
                return written
            written += 1
    return written


def _match_state(row: dict) -> dict:
    return {
        "match_status": row.get("match_status") or "",
        "auto_linked": bool(row.get("auto_linked")),
        "bond_case_id": row.get("bond_case_id") or "",
        "defendant_id": row.get("defendant_id") or row.get("matched_defendant_id") or "",
        "confirmations": list(row.get("confirmations") or []),
    }


def _match_evidence(row: dict, reason: str) -> dict:
    return {
        "reason": reason,
        "docket_id": row.get("docket_id") or "",
        "docket_number": row.get("docket_number") or "",
        "filing_id": row.get("filing_id") or "",
        "event_type": row.get("event_type") or "",
        "confirmations": list(row.get("confirmations") or []),
    }


def _downgrade_copy(name: str, event_label: str) -> tuple[str, str]:
    title = f"Federal bond risk link withdrawn — {event_label}"
    message = (
        f"Previously linked for {name}. {event_label} no longer confirms. "
        "Newer court identity evidence removed the bond link and left it for staff review."
    )
    return _redact_sensitive(title), _redact_sensitive(message)


def _mutable_update(document: dict) -> dict:
    """Fields safe to ``$set``. Lists that other scans append stay off this update."""
    skipped = {"_id", "audit_pending", "match_history", "_history_event"}
    return {key: value for key, value in document.items() if key not in skipped}


def _transition_update(document: dict, audit: Optional[dict] = None) -> dict:
    """Commit a match change without replacing pending audits or history."""
    update: dict = {"$set": _mutable_update(document)}
    history_event = document.get("_history_event")
    if history_event:
        update["$push"] = {"match_history": history_event}
    if audit:
        update["$addToSet"] = {"audit_pending": audit}
    return update


def _with_downgrade_audit(existing: dict, document: dict, reason: str) -> dict:
    """Replace a confirmed link and keep the previous bond attachment in history."""
    now = _now()
    updated = _mutable_update(document)
    updated["detected_at"] = existing.get("detected_at") or updated.get("detected_at") or now
    updated["downgraded_at"] = now
    updated["downgraded_from"] = existing.get("match_status") or "confirmed"
    updated["downgrade_reason"] = reason
    updated["prior_bond_case_id"] = existing.get("bond_case_id") or ""
    updated["prior_matched_defendant_id"] = existing.get("matched_defendant_id")
    updated["_history_event"] = {
        "at": now,
        "from": existing.get("match_status") or "confirmed",
        "to": updated.get("match_status"),
        "reason": reason,
        "auto_linked": bool(existing.get("auto_linked")),
        "bond_case_id": existing.get("bond_case_id") or "",
        "matched_defendant_id": existing.get("matched_defendant_id"),
    }
    updated["slack_delivered"] = False
    updated["alert_complete"] = False
    updated["slack_claim"] = False
    updated["slack_attempts"] = int(existing.get("slack_attempts") or 0)
    updated["notification_written"] = False
    updated["notification_status"] = ""
    name = str(updated.get("defendant_name") or existing.get("defendant_name") or "the client")
    label = str(existing.get("event_type") or updated.get("event_type") or "filing").replace("_", " ")
    title, message = _downgrade_copy(name, label)
    updated["title"] = title
    updated["description"] = message
    return updated


def _downgrade_note(document: dict, source_id: str) -> dict:
    status = str(document.get("match_status") or "possible")
    return redact_internal_keys({
        "notification_id": uuid.uuid4().hex[:12],
        "type": "federal_bond_risk",
        "icon": "⚖️",
        "priority": "high",
        "color": "#f59e0b",
        "title": document.get("title") or "Federal bond risk link withdrawn",
        "message": document.get("description") or "",
        "entity_id": document.get("candidate_bond_case_id") or document.get("prior_bond_case_id") or "",
        "entity_type": "federal_bond_risk",
        "metadata": {
            "match_status": status,
            "auto_linked": False,
            "downgraded_from": "confirmed",
            "downgrade_reason": document.get("downgrade_reason") or "",
            "court_id": document.get("court_id") or "",
            "court_name": document.get("court_name") or "",
            "docket_number": document.get("docket_number") or "",
            "docket_id": document.get("docket_id") or "",
            "event_type": document.get("event_type") or "",
            "filing_id": document.get("filing_id") or "",
            "source_id": source_id,
            "absolute_url": document.get("absolute_url") or "",
            "candidate_bond_case_id": document.get("candidate_bond_case_id") or document.get("prior_bond_case_id") or "",
        },
        "read": False,
        "dismissed": False,
        "created_at": _now(),
    })


async def _store_decision(db, decision: MatchDecision, docket: DocketView) -> str:
    """Insert, upgrade, or downgrade one staff row.

    Returns ``new``, ``upgraded``, ``downgraded``, ``retry_slack``, or ``duplicate``.
    A Slack retry does not write another CRM notification. The row is not
    complete until Slack delivery is recorded. A confirmed link that later
    evidence no longer supports is replaced and the previous attachment is kept
    on ``match_history``.
    """
    source_id = _source_id(docket, decision)
    existing = await db.docket_events.find_one({"source": SOURCE, "source_id": source_id})
    document = _event_document(decision, docket, source_id)
    document["notification_revision"] = _next_notification_revision(existing, decision.status)
    note = _notification(decision, docket, source_id)
    restore = bool(
        existing
        and existing.get("match_status") == "withdrawn"
        and decision.status in {"confirmed", "possible"}
    )
    upgrade = bool(
        existing
        and existing.get("match_status") == "possible"
        and decision.status == "confirmed"
    )
    downgrade = bool(
        existing
        and existing.get("match_status") == "confirmed"
        and existing.get("auto_linked")
        and decision.status != "confirmed"
    )
    if upgrade:
        document["detected_at"] = existing.get("detected_at") or document["detected_at"]
        document["upgraded_at"] = _now()
        document["slack_attempts"] = int(existing.get("slack_attempts") or 0)
        audit = _queue_match_audit(
            document, source_id, _match_state(existing), _match_state(document),
            "possible_upgraded_to_confirmed", _match_evidence(document, "possible_upgraded_to_confirmed"),
            prior=existing,
        )
        result = await db.docket_events.update_one(
            {"_id": existing.get("_id"), "match_status": "possible"},
            _transition_update(document, audit),
        )
        if not getattr(result, "matched_count", 0):
            return "duplicate"
        await _flush_match_audit(db, source_id, audit)
        await _ensure_notification(
            db, note, source_id, decision.status, document["notification_revision"],
        )
        return "upgraded"
    if restore:
        document["detected_at"] = existing.get("detected_at") or document["detected_at"]
        document["slack_attempts"] = 0
        document["slack_claim"] = False
        document["slack_delivered"] = False
        document["alert_complete"] = False
        document["_history_event"] = {
            "at": _now(),
            "from": "withdrawn",
            "to": decision.status,
            "reason": "withdrawn_restored",
            "auto_linked": bool(existing.get("auto_linked")),
            "bond_case_id": existing.get("prior_bond_case_id") or existing.get("bond_case_id") or "",
            "matched_defendant_id": existing.get("prior_matched_defendant_id") or existing.get("matched_defendant_id"),
        }
        audit = _queue_match_audit(
            document, source_id, _match_state(existing), _match_state(document),
            "withdrawn_restored", _match_evidence(document, "withdrawn_restored"),
            prior=existing,
        )
        result = await db.docket_events.update_one(
            {"_id": existing.get("_id"), "match_status": "withdrawn"},
            _transition_update(document, audit),
        )
        if not getattr(result, "matched_count", 0):
            return "duplicate"
        await _flush_match_audit(db, source_id, audit)
        await _ensure_notification(
            db, note, source_id, decision.status, document["notification_revision"],
        )
        return "restored"
    if downgrade:
        document = _with_downgrade_audit(existing, document, "identity_no_longer_confirms")
        audit = _queue_match_audit(
            document, source_id, _match_state(existing), _match_state(document),
            "identity_no_longer_confirms", _match_evidence(document, "identity_no_longer_confirms"),
            prior=existing,
        )
        result = await db.docket_events.update_one(
            {"_id": existing.get("_id"), "match_status": "confirmed"},
            _transition_update(document, audit),
        )
        if not getattr(result, "matched_count", 0):
            return "duplicate"
        await _flush_match_audit(db, source_id, audit)
        await _ensure_notification(
            db, _downgrade_note(document, source_id), source_id, decision.status,
            document["notification_revision"],
        )
        return "downgraded"
    if existing:
        if (
            not existing.get("notification_written")
            or existing.get("notification_status") != decision.status
        ):
            await _ensure_notification(
                db, note, source_id, decision.status,
                _next_notification_revision(existing, decision.status),
            )
        if not existing.get("slack_delivered"):
            return "retry_slack"
        return "duplicate"
    audit = _queue_match_audit(
        document, source_id, {}, _match_state(document),
        "match_recorded", _match_evidence(document, "match_recorded"),
        prior=existing,
    )
    claimed = await _claim_insert(db, document)
    if claimed != "inserted":
        winner = await db.docket_events.find_one({"source": SOURCE, "source_id": source_id})
        if winner and winner.get("match_status") == "confirmed" and decision.status != "confirmed":
            return await _store_decision(db, decision, docket)
        if winner and not winner.get("notification_written"):
            await _ensure_notification(
                db, note, source_id, decision.status,
                _next_notification_revision(winner, decision.status),
            )
        if winner and not winner.get("slack_delivered"):
            return "retry_slack"
        return "duplicate"
    await _ensure_notification(
        db, note, source_id, decision.status, document["notification_revision"],
    )
    await _flush_match_audit(db, source_id, audit)
    log.info(
        "Federal bond-risk %s court=%s event=%s filing=%s auto_linked=%s",
        decision.status, docket.court_id, decision.event_type, decision.filing_id,
        decision.status == "confirmed",
    )
    return "new"


async def _unlink_inactive_clients(db, clients: list[ClientIdentity]) -> list[str]:
    """A stored confirmed or possible row whose bond is closed is not posted."""
    active = {client.bond_case_id for client in clients}
    unlinked_ids: list[str] = []
    async for page in _each_page(
        db.docket_events,
        {"source": SOURCE, "match_status": {"$in": ["confirmed", "possible"]}},
    ):
        for row in page:
            bonds = [
                str(row.get("bond_case_id") or "").strip(),
                str(row.get("candidate_bond_case_id") or "").strip(),
            ]
            if any(bond in active for bond in bonds if bond):
                continue
            source_id = str(row.get("source_id") or "")
            prior_status = row.get("match_status")
            document = _mutable_update(row)
            document["match_status"] = "withdrawn"
            document["auto_linked"] = False
            document["bond_case_id"] = ""
            document["defendant_id"] = ""
            document["matched_defendant_id"] = None
            document["confirmations"] = []
            document["notification_revision"] = _next_notification_revision(row, "withdrawn")
            document = _with_downgrade_audit(row, document, "client_no_longer_active")
            audit = _queue_match_audit(
                document, source_id, _match_state(row), _match_state(document),
                "client_no_longer_active", _match_evidence(document, "client_no_longer_active"),
                prior=row,
            )
            result = await db.docket_events.update_one(
                {"_id": row.get("_id"), "match_status": prior_status},
                _transition_update(document, audit),
            )
            if getattr(result, "matched_count", 0) and source_id:
                await _flush_match_audit(db, source_id, audit)
                await _ensure_notification(
                    db, _downgrade_note(document, source_id), source_id, "withdrawn",
                    document["notification_revision"],
                )
                unlinked_ids.append(source_id)
    return unlinked_ids


async def _withdraw_stale_confirmations(db, docket: DocketView, decisions: list[MatchDecision]) -> list[str]:
    """A filing seen again that no longer matches (or was corrected) must not stay auto-linked."""
    filing_ids = {fid for fid in docket.seen_filing_ids if fid} | {
        event.filing_id for event in docket.events if event.filing_id
    }
    if not filing_ids:
        return []
    still_open = {_source_id(docket, decision) for decision in decisions}
    withdrawn: list[str] = []
    async for page in _each_page(
        db.docket_events, {"source": SOURCE, "docket_id": str(docket.docket_id)},
    ):
        for row in page:
            source_id = str(row.get("source_id") or "")
            if row.get("match_status") not in {"confirmed", "possible"}:
                continue
            if str(row.get("filing_id") or "") not in filing_ids or source_id in still_open:
                continue
            prior_status = row.get("match_status")
            document = _mutable_update(row)
            document["match_status"] = "withdrawn"
            document["auto_linked"] = False
            document["bond_case_id"] = ""
            document["defendant_id"] = ""
            document["matched_defendant_id"] = None
            document["confirmations"] = []
            document["notification_revision"] = _next_notification_revision(row, "withdrawn")
            document = _with_downgrade_audit(row, document, "identity_no_longer_matches")
            audit = _queue_match_audit(
                document, source_id, _match_state(row), _match_state(document),
                "identity_no_longer_matches", _match_evidence(document, "identity_no_longer_matches"),
                prior=row,
            )
            result = await db.docket_events.update_one(
                {"_id": row.get("_id"), "match_status": prior_status},
                _transition_update(document, audit),
            )
            if not getattr(result, "matched_count", 0):
                continue
            await _flush_match_audit(db, source_id, audit)
            await _ensure_notification(
                db, _downgrade_note(document, source_id), source_id, "withdrawn",
                document["notification_revision"],
            )
            withdrawn.append(source_id)
    return withdrawn


def _slack_version_filter(source_id: str, match_status: str, revision: int) -> dict:
    """The claim and the delivery stamp both name this match version."""
    revision = int(revision or 0)
    if revision == 0:
        version: dict = {"$or": [
            {"notification_revision": 0},
            {"notification_revision": {"$exists": False}},
            {"notification_revision": None},
        ]}
    else:
        version = {"notification_revision": revision}
    return {
        "source": SOURCE,
        "source_id": source_id,
        "match_status": match_status,
        "$and": [version],
    }


async def _record_slack(
    db, source_id: str, delivered: bool, attempts: int, *, match_status: str, revision: int,
) -> None:
    """Store whether Slack accepted this version. A newer transition is left unsent."""
    now = _now()
    fields = {
        "slack_delivered": delivered,
        "alert_complete": delivered,
        "slack_attempts": attempts,
        "slack_last_attempt_at": now,
        "slack_claim": False,
    }
    if delivered:
        fields["slack_delivered_at"] = now
    await db.docket_events.update_one(
        {
            **_slack_version_filter(source_id, match_status, revision),
            "slack_delivered": {"$ne": True},
        },
        {"$set": fields},
    )


async def _claim_slack_delivery(db, source_id: str, *, match_status: str, revision: int = 0) -> Optional[dict]:
    """Claim one undelivered version and return that stored row.

    A claim older than 30 minutes can be taken again. The filter names the
    match status and revision that were read, so a downgrade that lands first
    is not posted from the earlier snapshot.
    """
    cutoff = (datetime.now(timezone.utc) - SLACK_CLAIM_TTL).isoformat()
    result = await db.docket_events.update_one(
        {
            **_slack_version_filter(source_id, match_status, revision),
            "slack_delivered": {"$ne": True},
            "$and": [
                *_slack_version_filter(source_id, match_status, revision)["$and"],
                {"$or": [
                    {"slack_claim": {"$ne": True}},
                    {"slack_claim_at": {"$lt": cutoff}},
                    {"slack_claim_at": {"$exists": False}},
                ]},
            ],
        },
        {"$set": {"slack_claim": True, "slack_claim_at": _now()}},
    )
    if getattr(result, "matched_count", 0) != 1:
        return None
    fresh = await db.docket_events.find_one({"source": SOURCE, "source_id": source_id})
    if not fresh:
        return None
    if str(fresh.get("match_status") or "") != match_status:
        return None
    if int(fresh.get("notification_revision") or 0) != int(revision or 0):
        return None
    return fresh


def _alert_stored(row: dict, notifier) -> bool:
    """Repost a stored alert. The filing does not have to still be in search results."""
    title = str(row.get("title") or "Federal bond risk")
    message = str(row.get("description") or "")
    confirmed = bool(row.get("auto_linked"))
    try:
        return bool(notifier.notify_alert(
            title=title[:140],
            message=message,
            level="critical" if confirmed else "warning",
            details={
                "court": row.get("court_name") or "",
                "docket": row.get("docket_number") or "",
                "event": row.get("event_type") or "",
                "filing_id": row.get("filing_id") or "",
                "match_status": row.get("match_status") or "",
                "auto_linked": confirmed,
                "bond_case_id": row.get("bond_case_id") or row.get("candidate_bond_case_id") or "",
            },
            source="FederalBondRisk",
            bridge=False,
        ))
    except Exception as exc:
        log.warning("Federal bond-risk Slack retry failed: %s", type(exc).__name__)
        return False


async def retry_pending_slack(db, notifier, limit: int = 200) -> tuple[int, int, int]:
    """Retry alerts whose Slack post never succeeded, independent of this search.

    Returns sent, still pending, and claimed attempts. A failed post counts
    toward the per-run cap.
    """
    cursor = db.docket_events.find({"source": SOURCE, "slack_delivered": False})
    if hasattr(cursor, "sort"):
        cursor = cursor.sort("_id", 1)
    rows = await _to_list(cursor, limit)
    sent = 0
    pending = 0
    attempted = 0
    for row in rows:
        if attempted >= MAX_SLACK:
            pending += 1
            continue
        source_id = str(row.get("source_id") or "")
        match_status = str(row.get("match_status") or "")
        revision = int(row.get("notification_revision") or 0)
        claimed = await _claim_slack_delivery(
            db, source_id, match_status=match_status, revision=revision,
        ) if source_id else None
        if not claimed:
            pending += 1
            continue
        attempted += 1
        delivered = _alert_stored(claimed, notifier)
        attempts = int(claimed.get("slack_attempts") or 0) + 1
        await _record_slack(
            db, source_id, delivered, attempts,
            match_status=match_status, revision=revision,
        )
        if delivered:
            sent += 1
        else:
            pending += 1
    return sent, pending, attempted


def _alert_slack(decision: MatchDecision, docket: DocketView, notifier) -> bool:
    title, message = _staff_copy(decision, docket)
    confirmed = decision.status == "confirmed"
    try:
        return bool(notifier.notify_alert(
            title=title[:140],
            message=message,
            level="critical" if confirmed else "warning",
            details={
                "court": docket.court_name,
                "docket": docket.docket_number,
                "event": decision.event_type,
                "filing_id": decision.filing_id,
                "match_status": decision.status,
                "auto_linked": confirmed,
                "bond_case_id": decision.client.bond_case_id if confirmed else "",
            },
            source="FederalBondRisk",
            bridge=False,
        ))
    except Exception as exc:
        log.warning("Federal bond-risk Slack failed: %s", type(exc).__name__)
        return False


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
        try:
            await ensure_federal_bond_alert_index(db)
        except _ScanStop as stop:
            return _scan_stopped(base, stop)
        if notifier is None:
            from writers.slack_notifier import SlackNotifier

            notifier = SlackNotifier()
        await retry_pending_audits(db)
        attempted_notifications: set[str] = set()
        loaded_clients = clients is None
        if clients is None:
            clients = await load_active_clients(db)
        if loaded_clients:
            attempted_notifications.update(await _unlink_inactive_clients(db, clients) or [])
        if not clients:
            await retry_pending_notifications(db, skip_source_ids=attempted_notifications)
            slack_sent, slack_pending, slack_attempts = await retry_pending_slack(db, notifier)
            return {
                **base,
                "skipped": "no_active_clients",
                "bonds_considered": 0,
                "slack_sent": slack_sent,
                "slack_pending": slack_pending,
            }
        try:
            (
                dockets,
                hydrate_truncated,
                next_seed_offset,
                pending_checkpoints,
                party_failures,
                entry_failures,
                brief_failures,
                search_notes,
            ) = await _collect_dockets(client, pause_s, db)
        except _ScanStop as stop:
            await retry_pending_notifications(
                db, skip_source_ids=attempted_notifications, statuses={"withdrawn"},
            )
            slack_sent, slack_pending, _attempts = await retry_pending_slack(db, notifier)
            stopped = _scan_stopped(base, stop)
            stopped["slack_sent"] = slack_sent
            stopped["slack_pending"] = slack_pending
            return stopped
        seeds_truncated = hydrate_truncated or bool(search_notes)
        if seeds_truncated:
            base["truncated"] = True

        decisions: list[tuple[MatchDecision, DocketView]] = []
        by_docket: dict[str, tuple[DocketView, list[MatchDecision]]] = {}
        for docket in dockets:
            group = decide_matches(clients, docket)
            by_docket[str(docket.docket_id)] = (docket, group)
            for decision in group:
                decisions.append((decision, docket))

        written = 0
        confirmed = 0
        possible = 0
        rows_capped = False
        for decision, docket in decisions:
            source_id = _source_id(docket, decision)
            existing = await db.docket_events.find_one({"source": SOURCE, "source_id": source_id})
            if not existing and written >= MAX_ROWS:
                rows_capped = True
                base["truncated"] = True
                continue
            outcome = await _store_decision(db, decision, docket)
            attempted_notifications.add(source_id)
            if outcome in {"duplicate", "retry_slack"}:
                continue
            written += 1
            if outcome == "downgraded" or decision.status != "confirmed":
                possible += 1
            else:
                confirmed += 1
        withdrawn_ids: list[str] = []
        for docket, group in by_docket.values():
            withdrawn_ids.extend(await _withdraw_stale_confirmations(db, docket, group))
        attempted_notifications.update(withdrawn_ids)
        written += len(withdrawn_ids)
        possible += len(withdrawn_ids)
        # Only advance search/hydration checkpoints once every match in this slice
        # has been hydrated and stored; otherwise a capped or failed-hydration run
        # re-reads this slice so no docket is skipped.
        hydration_incomplete = bool(party_failures or entry_failures or brief_failures)
        if hydration_incomplete and (seeds_truncated or pending_checkpoints):
            base["truncated"] = True
        if db is not None and not rows_capped and not hydration_incomplete:
            await _save_seed_offset(db, next_seed_offset)
            if next_seed_offset == 0:
                for search_type, day, resume_url in pending_checkpoints:
                    await _save_search_checkpoint(db, search_type, day, resume_url)
        # Notification and Slack retries run only after this scan's evidence has
        # been reconciled, so an outdated pre-downgrade alert is never sent.
        await retry_pending_notifications(db, skip_source_ids=attempted_notifications)
        slack_sent, slack_pending, _slack_attempts = await retry_pending_slack(db, notifier)
        warnings = []
        if rows_capped:
            warnings.append(
                "New matches exceeded the per-scan row cap. "
                "The next scan continues from the same search slice until every match is stored."
            )
        if seeds_truncated:
            warnings.append(
                "Matching dockets remain after this hydration batch. "
                "The next scan continues with the rest."
            )
        if party_failures:
            warnings.append(
                "CourtListener party list failed for "
                f"{len(party_failures)} docket(s): " + "; ".join(party_failures[:8])
            )
        if entry_failures:
            warnings.append(
                "CourtListener docket entries failed for "
                f"{len(entry_failures)} docket(s): " + "; ".join(entry_failures[:8])
            )
        if brief_failures:
            warnings.append(
                "CourtListener docket brief failed for "
                f"{len(brief_failures)} docket(s): " + "; ".join(brief_failures[:8])
            )
        warnings.extend(search_notes)
        return {
            **base,
            "skipped": None,
            "warning": " ".join(warnings) or None,
            "bonds_considered": len(clients),
            "dockets_reviewed": len(dockets),
            "rows_written": written,
            "confirmed": confirmed,
            "possible": possible,
            "slack_sent": slack_sent,
            "slack_pending": slack_pending,
            "client_messages": 0,
        }
    finally:
        if owns_client:
            close = getattr(client, "close", None)
            if close:
                await close()
