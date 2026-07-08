"""
Pydantic Models — OSINT Intelligence Module
Admin-only. All access is audited. PII must not appear in logs.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ── Request Models ─────────────────────────────────────────────────────────────

class OSINTScanRequest(BaseModel):
    """Initiate a Maigret + Blackbird scan for a subject."""

    subject_type: str = Field(
        ...,
        description="Type of subject: 'defendant' or 'indemnitor'",
        pattern="^(defendant|indemnitor)$",
    )
    subject_id: str = Field(..., description="MongoDB ObjectId of the subject record")
    # Identifiers to search — at least one required
    full_name: Optional[str] = Field(None, description="Subject's full legal name")
    usernames: Optional[List[str]] = Field(
        default_factory=list,
        description="Known usernames or handles (e.g. Facebook alias, email prefix)",
    )
    email: Optional[str] = Field(None, description="Known email address")
    phone: Optional[str] = Field(None, description="Known phone number")
    dob: Optional[str] = Field(None, description="Date of birth (YYYY-MM-DD)")
    # Scan options
    deep_scan: bool = Field(
        False,
        description="If True, runs Maigret against all 3000+ sites (slower). Default: top-500.",
    )
    run_maigret: bool = Field(True, description="Include Maigret username scan")
    run_blackbird: bool = Field(True, description="Include Blackbird username/email scan")
    notes: Optional[str] = Field(None, description="Admin notes for this scan request")


class TrapeSessionRequest(BaseModel):
    """Generate a Trape-style tracking payload for skip-trace operations."""

    subject_type: str = Field(..., pattern="^(defendant|indemnitor)$")
    subject_id: str = Field(..., description="MongoDB ObjectId of the subject record")
    lure_url: str = Field(
        ...,
        description="The URL to clone as a lure (e.g. a local court notice page)",
    )
    notes: Optional[str] = Field(None, description="Operational notes for this tracking session")


# ── Response / Storage Models ──────────────────────────────────────────────────

class SocialAccount(BaseModel):
    """A discovered social media or web account."""

    platform: str
    url: str
    username: Optional[str] = None
    profile_data: Optional[Dict[str, Any]] = Field(default_factory=dict)
    source: str = Field(..., description="'maigret' or 'blackbird'")
    confidence: str = Field("found", description="'found', 'likely', 'uncertain'")


class OSINTRiskSignal(BaseModel):
    """A risk signal derived from OSINT findings."""

    signal_type: str = Field(
        ...,
        description=(
            "Category: 'out_of_state', 'multiple_identities', 'criminal_record_mention', "
            "'employment_inconsistency', 'address_mismatch', 'social_inactivity', 'high_account_count'"
        ),
    )
    severity: str = Field(..., description="'low', 'medium', 'high', 'critical'")
    detail: str
    source: str


class OSINTReport(BaseModel):
    """The full OSINT intelligence report for a subject."""

    subject_type: str
    subject_id: str
    full_name: Optional[str] = None
    scan_requested_by: str = Field("admin", description="Actor who initiated the scan")
    scan_started_at: Optional[datetime] = None
    scan_completed_at: Optional[datetime] = None
    status: str = Field("pending", description="'pending', 'running', 'complete', 'failed'")
    # Tool outputs
    maigret_accounts: List[SocialAccount] = Field(default_factory=list)
    blackbird_accounts: List[SocialAccount] = Field(default_factory=list)
    # Derived intelligence
    total_accounts_found: int = 0
    platforms_found: List[str] = Field(default_factory=list)
    risk_signals: List[OSINTRiskSignal] = Field(default_factory=list)
    osint_risk_score: int = Field(
        0,
        ge=0,
        le=100,
        description="0-100 OSINT risk score layered on top of the bond risk score",
    )
    ai_summary: Optional[str] = Field(
        None,
        description="AI-generated investigation summary (if Gemini/OpenAI key is available)",
    )
    raw_maigret_json: Optional[Dict[str, Any]] = Field(
        None, description="Raw Maigret JSON output — stored but not displayed in UI"
    )
    raw_blackbird_json: Optional[Dict[str, Any]] = Field(
        None, description="Raw Blackbird JSON output — stored but not displayed in UI"
    )
    error: Optional[str] = None
    notes: Optional[str] = None


class TrapeSession(BaseModel):
    """A Trape-based location/session tracking session."""

    subject_type: str
    subject_id: str
    session_id: str
    lure_url: str
    tracking_url: Optional[str] = None
    created_at: Optional[datetime] = None
    status: str = Field("active", description="'active', 'triggered', 'expired'")
    # Data collected when target visits the tracking URL
    ip_address: Optional[str] = None
    geolocation: Optional[Dict[str, Any]] = None
    device_info: Optional[Dict[str, Any]] = None
    session_tokens: Optional[List[str]] = Field(default_factory=list)
    notes: Optional[str] = None
