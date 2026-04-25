"""
ArrestRecord & Lead Models — MongoDB-native with Sheets compatibility.

Preserves the canonical 39-column schema from swfl-arrest-scrapers while
adding MongoDB-native features (BSON serialization, ObjectId references).
"""

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
import re


# ══════════════════════════════════════════════════════════════
# Surety Configuration — Phase 5 Scaffold
# Based on actual inventory receipts dated 04/20/2026.
# See docs/specs/surety-config-schema.md for full documentation.
# ══════════════════════════════════════════════════════════════

@dataclass
class POATier:
    """A POA prefix tier with its maximum bond value."""
    prefix: str          # e.g., "OSI3", "PSC50"
    max_bond_value: int  # Maximum bond amount for this tier


@dataclass
class SuretyConfig:
    """
    Configuration for a surety/insurance company.

    Shamrock represents two sureties:
      - OSI (O'Shaughnahill Surety & Insurance, Inc.)
      - Palmetto Surety Corporation

    Premium split rates (per $100 in premium collected):
      - OSI:     $7.50 to surety + $5.00 to BUF = $12.50 total
      - Palmetto: $10.00 to surety + $5.00 to BUF = $15.00 total
    """
    surety_id: str                   # "osi" or "palmetto"
    company_name: str
    licensed_states: List[str]
    poa_tiers: List[POATier]
    surety_rate_per_100: float       # Dollars owed to surety per $100 premium
    buf_rate_per_100: float          # Dollars owed to BUF per $100 premium
    template_set_id: str = ""        # SignNow template group (Phase 6)
    active: bool = True

    def calculate_split(self, bond_amount: float, premium_rate: float = 0.10) -> Dict[str, float]:
        """Calculate premium split for a given bond amount."""
        premium = bond_amount * premium_rate
        surety_owed = premium * (self.surety_rate_per_100 / 100.0)
        buf_owed = premium * (self.buf_rate_per_100 / 100.0)
        agent_retains = premium - surety_owed - buf_owed
        return {
            "premium": round(premium, 2),
            "surety_owed": round(surety_owed, 2),
            "buf_owed": round(buf_owed, 2),
            "agent_retains": round(agent_retains, 2),
        }

    def get_tier_for_bond(self, bond_amount: float) -> Optional[POATier]:
        """Find the smallest POA tier that covers the bond amount."""
        sorted_tiers = sorted(self.poa_tiers, key=lambda t: t.max_bond_value)
        for tier in sorted_tiers:
            if bond_amount <= tier.max_bond_value:
                return tier
        return None  # Bond exceeds all available tiers


# --- Surety Registry (from actual receipts dated 04/20/2026) ---

SURETY_OSI = SuretyConfig(
    surety_id="osi",
    company_name="O'Shaughnahill Surety & Insurance, Inc.",
    licensed_states=["FL"],
    surety_rate_per_100=7.50,
    buf_rate_per_100=5.00,
    poa_tiers=[
        POATier(prefix="OSI3", max_bond_value=3_000),
        POATier(prefix="OSI6", max_bond_value=6_000),
        POATier(prefix="OSI16", max_bond_value=16_000),
        POATier(prefix="OSI51", max_bond_value=51_000),
        POATier(prefix="OSI101", max_bond_value=101_000),
        POATier(prefix="OSI251", max_bond_value=251_000),
    ],
)

SURETY_PALMETTO = SuretyConfig(
    surety_id="palmetto",
    company_name="Palmetto Surety Corporation",
    licensed_states=["FL", "SC", "NC", "TN", "TX", "CT", "LA", "MS"],
    surety_rate_per_100=10.00,
    buf_rate_per_100=5.00,
    poa_tiers=[
        POATier(prefix="PSC5", max_bond_value=5_000),
        POATier(prefix="PSC15", max_bond_value=15_000),
        POATier(prefix="PSC25", max_bond_value=25_000),
        POATier(prefix="PSC50", max_bond_value=50_000),
        POATier(prefix="PSC75", max_bond_value=75_000),
        POATier(prefix="PSC105", max_bond_value=105_000),
        POATier(prefix="PSC200", max_bond_value=200_000),
        POATier(prefix="PSC250", max_bond_value=250_000),
    ],
)

SURETY_REGISTRY: Dict[str, SuretyConfig] = {
    "osi": SURETY_OSI,
    "palmetto": SURETY_PALMETTO,
}


def get_surety(surety_id: str) -> SuretyConfig:
    """Get surety config by ID. Raises ValueError if unknown."""
    if surety_id not in SURETY_REGISTRY:
        raise ValueError(f"Unknown surety_id: {surety_id!r}. Valid: {list(SURETY_REGISTRY.keys())}")
    return SURETY_REGISTRY[surety_id]


# ══════════════════════════════════════════════════════════════
# ArrestRecord — Phase 1 (Implemented)
# ══════════════════════════════════════════════════════════════

@dataclass
class ArrestRecord:
    """
    Universal arrest record — 41-column canonical schema v3.1.

    Dual-write capable: outputs both MongoDB documents and Sheet rows.
    Dedup key: County + Booking_Number.
    """

    # === Master Schema (41 Columns) ===
    Scrape_Timestamp: str = ""
    County: str = ""
    Booking_Number: str = ""
    Person_ID: str = ""
    Full_Name: str = ""
    First_Name: str = ""
    Middle_Name: str = ""
    Last_Name: str = ""
    DOB: str = ""
    Arrest_Date: str = ""
    Arrest_Time: str = ""
    Booking_Date: str = ""
    Booking_Time: str = ""
    Status: str = ""
    Release_Date: str = ""           # v3.1 — available from Lee, DeSoto, Hillsborough+
    Facility: str = ""
    Agency: str = ""
    Race: str = ""
    Sex: str = ""
    Height: str = ""
    Weight: str = ""
    Age_At_Arrest: str = ""          # v3.1 — available from Collier+
    Address: str = ""
    City: str = ""
    State: str = "FL"
    ZIP: str = ""
    Mugshot_URL: str = ""
    Charges: str = ""
    Bond_Amount: str = "0"
    Bond_Paid: str = "NO"
    Bond_Type: str = ""
    Court_Type: str = ""
    Case_Number: str = ""
    Court_Date: str = ""
    Court_Time: str = ""
    Court_Location: str = ""
    Detail_URL: str = ""
    Lead_Score: int = 0
    Lead_Status: str = ""
    LastChecked: str = ""
    LastCheckedMode: str = ""

    # Internal (not persisted to sheets)
    extra_data: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.Scrape_Timestamp:
            self.Scrape_Timestamp = datetime.now(timezone.utc).isoformat()
        if isinstance(self.Bond_Amount, (int, float)):
            self.Bond_Amount = str(self.Bond_Amount)
        if self.Sex:
            self.Sex = self.Sex.upper()[:1]

    # ── Dedup ──
    def get_dedup_key(self) -> str:
        return f"{self.County}:{self.Booking_Number}"

    def is_qualified(self, min_score: int = 70) -> bool:
        return self.Lead_Score >= min_score and self.Lead_Status != "Disqualified"

    # ── MongoDB serialization ──
    def to_mongo_doc(self) -> Dict[str, Any]:
        """Convert to a MongoDB-ready document."""
        doc = {
            "scrape_timestamp": self.Scrape_Timestamp,
            "county": self.County,
            "booking_number": self.Booking_Number,
            "person_id": self.Person_ID,
            "full_name": self.Full_Name,
            "first_name": self.First_Name,
            "middle_name": self.Middle_Name,
            "last_name": self.Last_Name,
            "dob": self.DOB,
            "arrest_date": self.Arrest_Date,
            "arrest_time": self.Arrest_Time,
            "booking_date": self.Booking_Date,
            "booking_time": self.Booking_Time,
            "status": self.Status,
            "release_date": self.Release_Date,
            "facility": self.Facility,
            "agency": self.Agency,
            "race": self.Race,
            "sex": self.Sex,
            "height": self.Height,
            "weight": self.Weight,
            "age_at_arrest": self.Age_At_Arrest,
            "address": self.Address,
            "city": self.City,
            "state": self.State,
            "zip": self.ZIP,
            "mugshot_url": self.Mugshot_URL,
            "charges": self.Charges,
            "bond_amount": self._parse_bond_numeric(),
            "bond_amount_raw": self.Bond_Amount,
            "bond_paid": self.Bond_Paid,
            "bond_type": self.Bond_Type,
            "court_type": self.Court_Type,
            "case_number": self.Case_Number,
            "court_date": self.Court_Date,
            "court_time": self.Court_Time,
            "court_location": self.Court_Location,
            "detail_url": self.Detail_URL,
            "lead_score": self.Lead_Score,
            "lead_status": self.Lead_Status,
            "last_checked": self.LastChecked,
            "last_checked_mode": self.LastCheckedMode,
            "updated_at": datetime.now(timezone.utc),
        }
        if self.extra_data:
            doc["extra"] = self.extra_data
        return doc

    @classmethod
    def from_mongo_doc(cls, doc: Dict[str, Any]) -> "ArrestRecord":
        """Reconstruct from a MongoDB document."""
        return cls(
            Scrape_Timestamp=doc.get("scrape_timestamp", ""),
            County=doc.get("county", ""),
            Booking_Number=doc.get("booking_number", ""),
            Person_ID=doc.get("person_id", ""),
            Full_Name=doc.get("full_name", ""),
            First_Name=doc.get("first_name", ""),
            Middle_Name=doc.get("middle_name", ""),
            Last_Name=doc.get("last_name", ""),
            DOB=doc.get("dob", ""),
            Arrest_Date=doc.get("arrest_date", ""),
            Arrest_Time=doc.get("arrest_time", ""),
            Booking_Date=doc.get("booking_date", ""),
            Booking_Time=doc.get("booking_time", ""),
            Status=doc.get("status", ""),
            Release_Date=doc.get("release_date", ""),
            Facility=doc.get("facility", ""),
            Agency=doc.get("agency", ""),
            Race=doc.get("race", ""),
            Sex=doc.get("sex", ""),
            Height=doc.get("height", ""),
            Weight=doc.get("weight", ""),
            Age_At_Arrest=doc.get("age_at_arrest", ""),
            Address=doc.get("address", ""),
            City=doc.get("city", ""),
            State=doc.get("state", "FL"),
            ZIP=doc.get("zip", ""),
            Mugshot_URL=doc.get("mugshot_url", ""),
            Charges=doc.get("charges", ""),
            Bond_Amount=doc.get("bond_amount_raw", str(doc.get("bond_amount", "0"))),
            Bond_Paid=doc.get("bond_paid", "NO"),
            Bond_Type=doc.get("bond_type", ""),
            Court_Type=doc.get("court_type", ""),
            Case_Number=doc.get("case_number", ""),
            Court_Date=doc.get("court_date", ""),
            Court_Time=doc.get("court_time", ""),
            Court_Location=doc.get("court_location", ""),
            Detail_URL=doc.get("detail_url", ""),
            Lead_Score=doc.get("lead_score", 0),
            Lead_Status=doc.get("lead_status", ""),
            LastChecked=doc.get("last_checked", ""),
            LastCheckedMode=doc.get("last_checked_mode", ""),
            extra_data=doc.get("extra", {}),
        )

    # ── Google Sheets serialization (backward compat) ──
    def to_sheet_row(self) -> list:
        """Returns the 41 fields in canonical column order."""
        return [
            self.Scrape_Timestamp, self.County, self.Booking_Number, self.Person_ID,
            self.Full_Name, self.First_Name, self.Middle_Name, self.Last_Name,
            self.DOB, self.Arrest_Date, self.Arrest_Time, self.Booking_Date,
            self.Booking_Time, self.Status, self.Release_Date, self.Facility, self.Agency,
            self.Race, self.Sex, self.Height, self.Weight, self.Age_At_Arrest,
            self.Address, self.City, self.State, self.ZIP,
            self.Mugshot_URL, self.Charges, self.Bond_Amount, self.Bond_Paid,
            self.Bond_Type, self.Court_Type, self.Case_Number, self.Court_Date,
            self.Court_Time, self.Court_Location, self.Detail_URL,
            self.Lead_Score, self.Lead_Status, self.LastChecked, self.LastCheckedMode,
        ]

    @staticmethod
    def get_header_row() -> list:
        return [
            "Scrape_Timestamp", "County", "Booking_Number", "Person_ID", "Full_Name",
            "First_Name", "Middle_Name", "Last_Name", "DOB", "Arrest_Date", "Arrest_Time",
            "Booking_Date", "Booking_Time", "Status", "Release_Date", "Facility", "Agency",
            "Race", "Sex", "Height", "Weight", "Age_At_Arrest",
            "Address", "City", "State", "ZIP",
            "Mugshot_URL", "Charges", "Bond_Amount", "Bond_Paid", "Bond_Type",
            "Court_Type", "Case_Number", "Court_Date", "Court_Time", "Court_Location",
            "Detail_URL", "Lead_Score", "Lead_Status", "LastChecked", "LastCheckedMode",
        ]

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ArrestRecord":
        valid = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**valid)

    # ── Internal ──
    def _parse_bond_numeric(self) -> float:
        """Parse bond amount to a float for MongoDB storage."""
        if not self.Bond_Amount:
            return 0.0
        cleaned = re.sub(r"[$,\s]", "", self.Bond_Amount.strip().upper())
        if any(t in cleaned for t in ["NO BOND", "NONE", "N/A", "HOLD"]):
            return 0.0
        try:
            return float(cleaned)
        except (ValueError, TypeError):
            return 0.0


# ══════════════════════════════════════════════════════════════
# DefendantRecord — Phase 2 (Scaffold)
#
# A Defendant is a PERSON, not an event. One defendant can have
# multiple ArrestRecords across multiple counties.
# Primary key: defendant_id (UUID)
# Natural dedup: normalized(last_name + first_name + dob)
# ══════════════════════════════════════════════════════════════

@dataclass
class DefendantRecord:
    """
    Normalized person record derived from one or more ArrestRecords.

    Phase 2 scaffold — created by the defendant normalizer from
    incoming ArrestRecords. Dedup key: normalized name + DOB.

    One Defendant ↔ many ArrestRecords (via arrest_ids list).
    """

    # === Identity ===
    defendant_id: str = ""           # UUID — generated on creation
    first_name: str = ""             # Canonical first name (title-cased)
    middle_name: str = ""
    last_name: str = ""              # Canonical last name (title-cased)
    full_name: str = ""              # "Last, First Middle"
    dob: str = ""                    # YYYY-MM-DD
    sex: str = ""                    # M or F
    race: str = ""

    # === Physical Description ===
    height: str = ""
    weight: str = ""

    # === Contact (Phase 9 — The Finder) ===
    phone: str = ""                  # Discovered phone number
    email: str = ""                  # Discovered email
    address: str = ""                # Last known address
    city: str = ""
    state: str = "FL"
    zip_code: str = ""

    # === Arrest History ===
    arrest_ids: List[str] = field(default_factory=list)      # List of ArrestRecord booking keys
    counties: List[str] = field(default_factory=list)         # Counties with arrests
    total_arrests: int = 0
    first_seen: str = ""             # ISO timestamp of first scrape
    last_seen: str = ""              # ISO timestamp of most recent scrape

    # === Mugshot ===
    mugshot_url: str = ""            # Most recent mugshot

    # === Metadata ===
    created_at: str = ""
    updated_at: str = ""
    source: str = "scraper"          # How this record was created

    def __post_init__(self):
        if not self.defendant_id:
            import uuid
            self.defendant_id = str(uuid.uuid4())
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()
        self.updated_at = datetime.now(timezone.utc).isoformat()
        if self.sex:
            self.sex = self.sex.upper()[:1]

    def get_dedup_key(self) -> str:
        """Natural dedup key: normalized name + DOB."""
        name = f"{self.last_name.lower().strip()}:{self.first_name.lower().strip()}"
        return f"{name}:{self.dob}"

    def merge_arrest(self, record: ArrestRecord) -> None:
        """Merge data from an ArrestRecord into this defendant."""
        booking_key = record.get_dedup_key()
        if booking_key not in self.arrest_ids:
            self.arrest_ids.append(booking_key)
        if record.County and record.County not in self.counties:
            self.counties.append(record.County)

        self.total_arrests = len(self.arrest_ids)
        self.last_seen = datetime.now(timezone.utc).isoformat()
        self.updated_at = self.last_seen

        # Update physical info if we have newer/better data
        if record.Mugshot_URL and not self.mugshot_url:
            self.mugshot_url = record.Mugshot_URL
        if record.Address and not self.address:
            self.address = record.Address
            self.city = record.City
            self.state = record.State
            self.zip_code = record.ZIP

    def to_mongo_doc(self) -> Dict[str, Any]:
        """Convert to a MongoDB-ready document."""
        return {
            "defendant_id": self.defendant_id,
            "first_name": self.first_name,
            "middle_name": self.middle_name,
            "last_name": self.last_name,
            "full_name": self.full_name,
            "dob": self.dob,
            "sex": self.sex,
            "race": self.race,
            "height": self.height,
            "weight": self.weight,
            "phone": self.phone,
            "email": self.email,
            "address": self.address,
            "city": self.city,
            "state": self.state,
            "zip_code": self.zip_code,
            "arrest_ids": self.arrest_ids,
            "counties": self.counties,
            "total_arrests": self.total_arrests,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "mugshot_url": self.mugshot_url,
            "created_at": self.created_at,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "source": self.source,
        }

    @classmethod
    def from_mongo_doc(cls, doc: Dict[str, Any]) -> "DefendantRecord":
        """Reconstruct from a MongoDB document."""
        return cls(
            defendant_id=doc.get("defendant_id", ""),
            first_name=doc.get("first_name", ""),
            middle_name=doc.get("middle_name", ""),
            last_name=doc.get("last_name", ""),
            full_name=doc.get("full_name", ""),
            dob=doc.get("dob", ""),
            sex=doc.get("sex", ""),
            race=doc.get("race", ""),
            height=doc.get("height", ""),
            weight=doc.get("weight", ""),
            phone=doc.get("phone", ""),
            email=doc.get("email", ""),
            address=doc.get("address", ""),
            city=doc.get("city", ""),
            state=doc.get("state", "FL"),
            zip_code=doc.get("zip_code", ""),
            arrest_ids=doc.get("arrest_ids", []),
            counties=doc.get("counties", []),
            total_arrests=doc.get("total_arrests", 0),
            first_seen=doc.get("first_seen", ""),
            last_seen=doc.get("last_seen", ""),
            mugshot_url=doc.get("mugshot_url", ""),
            created_at=doc.get("created_at", ""),
            updated_at=doc.get("updated_at", ""),
            source=doc.get("source", "scraper"),
        )

    @classmethod
    def from_arrest_record(cls, record: ArrestRecord) -> "DefendantRecord":
        """Create a new DefendantRecord from an ArrestRecord."""
        now = datetime.now(timezone.utc).isoformat()
        return cls(
            first_name=record.First_Name,
            middle_name=record.Middle_Name,
            last_name=record.Last_Name,
            full_name=record.Full_Name,
            dob=record.DOB,
            sex=record.Sex,
            race=record.Race,
            height=record.Height,
            weight=record.Weight,
            address=record.Address,
            city=record.City,
            state=record.State,
            zip_code=record.ZIP,
            arrest_ids=[record.get_dedup_key()],
            counties=[record.County] if record.County else [],
            total_arrests=1,
            first_seen=now,
            last_seen=now,
            mugshot_url=record.Mugshot_URL,
        )

