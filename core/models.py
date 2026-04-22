"""
ArrestRecord & Lead Models — MongoDB-native with Sheets compatibility.

Preserves the canonical 39-column schema from swfl-arrest-scrapers while
adding MongoDB-native features (BSON serialization, ObjectId references).
"""

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
import re


@dataclass
class ArrestRecord:
    """
    Universal arrest record — 39-column canonical schema v3.0.

    Dual-write capable: outputs both MongoDB documents and Sheet rows.
    Dedup key: County + Booking_Number.
    """

    # === Master Schema (39 Columns) ===
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
    Facility: str = ""
    Agency: str = ""
    Race: str = ""
    Sex: str = ""
    Height: str = ""
    Weight: str = ""
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
            "facility": self.Facility,
            "agency": self.Agency,
            "race": self.Race,
            "sex": self.Sex,
            "height": self.Height,
            "weight": self.Weight,
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
            Facility=doc.get("facility", ""),
            Agency=doc.get("agency", ""),
            Race=doc.get("race", ""),
            Sex=doc.get("sex", ""),
            Height=doc.get("height", ""),
            Weight=doc.get("weight", ""),
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
        """Returns the 39 fields in canonical column order."""
        return [
            self.Scrape_Timestamp, self.County, self.Booking_Number, self.Person_ID,
            self.Full_Name, self.First_Name, self.Middle_Name, self.Last_Name,
            self.DOB, self.Arrest_Date, self.Arrest_Time, self.Booking_Date,
            self.Booking_Time, self.Status, self.Facility, self.Agency,
            self.Race, self.Sex, self.Height, self.Weight,
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
            "Booking_Date", "Booking_Time", "Status", "Facility", "Agency",
            "Race", "Sex", "Height", "Weight", "Address", "City", "State", "ZIP",
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
