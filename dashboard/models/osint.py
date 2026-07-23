"""
Pydantic Models — OSINT Intelligence Module v2
Admin-only. All access is audited. PII must not appear in logs.

Engines: Maigret · Sherlock · Blackbird · SpiderFoot
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ── Enums ─────────────────────────────────────────────────────────────────────

class SubjectType(str, Enum):
    defendant = "defendant"
    indemnitor = "indemnitor"


class EngineType(str, Enum):
    maigret = "maigret"
    sherlock = "sherlock"
    blackbird = "blackbird"
    spiderfoot = "spiderfoot"


class ScanStatus(str, Enum):
    queued = "queued"
    running = "running"
    completed = "completed"
    partial = "partial"
    failed = "failed"
    degraded = "degraded"


class EngineStatus(str, Enum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"
    skipped = "skipped"


class Confidence(str, Enum):
    found = "found"
    likely = "likely"
    uncertain = "uncertain"


class Relevance(str, Enum):
    relevant = "relevant"
    irrelevant = "irrelevant"
    unreviewed = "unreviewed"


class EntityType(str, Enum):
    email = "email"
    phone = "phone"
    address = "address"
    name = "name"
    domain = "domain"
    ip = "ip"
    social_profile = "social_profile"
    organization = "organization"
    other = "other"


# ── Request Models ────────────────────────────────────────────────────────────

class OSINTScanRequest(BaseModel):
    """Initiate a multi-engine OSINT scan for a subject."""

    subject_type: SubjectType = Field(
        ..., description="Type of subject: 'defendant' or 'indemnitor'"
    )
    subject_id: str = Field(..., description="MongoDB ObjectId of the subject record")

    # Identifiers to search — at least one required
    full_name: Optional[str] = Field(None, description="Subject's full legal name")
    usernames: Optional[List[str]] = Field(
        default_factory=list,
        description="Known usernames or handles (for Maigret, Sherlock, Blackbird)",
    )
    email: Optional[str] = Field(
        None, description="Known email address (for Blackbird, SpiderFoot)"
    )
    phone: Optional[str] = Field(
        None, description="Known phone number (for SpiderFoot)"
    )
    dob: Optional[str] = Field(None, description="Date of birth (YYYY-MM-DD)")

    # Engine selection — list of engines to run
    engines: List[EngineType] = Field(
        default_factory=lambda: [EngineType.maigret],
        description="Engines to run. Default: [maigret]. Options: maigret, sherlock, blackbird, spiderfoot",
    )

    # Scan options
    deep_scan: bool = Field(
        False,
        description="Expand coverage (more sites for Maigret/Sherlock, more modules for SpiderFoot)",
    )
    second_opinion: bool = Field(
        False,
        description="Force all username-based engines for cross-validation",
    )
    notes: Optional[str] = Field(None, description="Admin notes for this scan request")


class FindingRelevanceUpdate(BaseModel):
    """Mark specific findings as relevant or irrelevant."""

    account_indices: Optional[List[int]] = Field(
        default_factory=list,
        description="Indices of accounts to update",
    )
    entity_indices: Optional[List[int]] = Field(
        default_factory=list,
        description="Indices of entities to update",
    )
    relevance: Relevance = Field(..., description="New relevance status")


class TrapeSessionRequest(BaseModel):
    """Generate a Trape-style tracking payload for skip-trace operations."""

    subject_type: SubjectType
    subject_id: str = Field(..., description="MongoDB ObjectId of the subject record")
    lure_url: str = Field(
        ..., description="The URL to clone as a lure (e.g. a local court notice page)"
    )
    notes: Optional[str] = Field(None, description="Operational notes for this tracking session")


# ── Response / Storage Models ─────────────────────────────────────────────────

class SocialAccount(BaseModel):
    """A discovered social media or web account."""

    platform: str
    url: str
    username: Optional[str] = None
    profile_data: Optional[Dict[str, Any]] = Field(default_factory=dict)
    source: EngineType = Field(..., description="Engine that found this account")
    confidence: Confidence = Field(Confidence.found)
    category: str = Field("other", description="social, forum, dating, professional, other")
    relevance: Relevance = Field(Relevance.unreviewed)


class OSINTEntity(BaseModel):
    """A discovered entity from SpiderFoot or other enrichment engines."""

    type: EntityType
    value: str
    source: EngineType = Field(EngineType.spiderfoot)
    module: Optional[str] = Field(None, description="SpiderFoot module that found this")
    confidence: str = Field("medium", description="high, medium, low")
    context: Optional[str] = Field(None, description="Additional context about the finding")
    relevance: Relevance = Field(Relevance.unreviewed)


class OSINTRiskSignal(BaseModel):
    """A risk signal derived from OSINT findings."""

    signal_type: str = Field(
        ...,
        description=(
            "Category: 'out_of_state', 'multiple_identities', 'criminal_record_mention', "
            "'employment_inconsistency', 'address_mismatch', 'social_inactivity', "
            "'high_account_count', 'osint_scan_failed', 'osint_degraded', 'osint_partial'"
        ),
    )
    severity: str = Field(..., description="'low', 'medium', 'high', 'critical'")
    detail: str
    source: str


class EngineProgress(BaseModel):
    """Progress tracking for a single engine within a scan."""

    status: EngineStatus = Field(EngineStatus.pending)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    accounts_found: int = 0
    entities_found: int = 0
    error: Optional[str] = None
    warning: Optional[str] = None


class OSINTScanResult(BaseModel):
    """The full OSINT scan result document (stored in Mongo)."""

    subject_type: SubjectType
    subject_id: str
    full_name: Optional[str] = None
    scan_requested_by: str = Field("admin")

    # What was requested
    engines_requested: List[EngineType] = Field(default_factory=list)
    scan_params: Dict[str, Any] = Field(default_factory=dict)

    # Status
    status: ScanStatus = Field(ScanStatus.queued)
    progress: Dict[str, EngineProgress] = Field(default_factory=dict)

    # Results
    accounts: List[SocialAccount] = Field(default_factory=list)
    entities: List[OSINTEntity] = Field(default_factory=list)
    total_accounts: int = 0
    total_entities: int = 0
    platforms_found: List[str] = Field(default_factory=list)

    # Risk
    risk_signals: List[OSINTRiskSignal] = Field(default_factory=list)
    osint_risk_score: int = Field(0, ge=0, le=100)
    risk_is_advisory: bool = True

    # AI
    ai_summary: Optional[str] = None

    # Raw outputs (stored but not returned in default view)
    raw_outputs: Dict[str, Any] = Field(default_factory=dict)
    tool_results: Dict[str, Any] = Field(default_factory=dict)

    # Meta
    warnings: List[str] = Field(default_factory=list)
    error: Optional[str] = None
    notes: Optional[str] = None
    created_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


class TrapeSession(BaseModel):
    """A Trape-based location/session tracking session."""

    subject_type: SubjectType
    subject_id: str
    session_id: str
    lure_url: str
    tracking_url: Optional[str] = None
    created_at: Optional[datetime] = None
    status: str = Field("active", description="'active', 'triggered', 'expired'")
    ip_address: Optional[str] = None
    geolocation: Optional[Dict[str, Any]] = None
    device_info: Optional[Dict[str, Any]] = None
    session_tokens: Optional[List[str]] = Field(default_factory=list)
    notes: Optional[str] = None
