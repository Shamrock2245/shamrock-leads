"""
Pydantic models — Palantir Intelligence Hub (OpenPlanter + OSIRIS + SPECTRA)

Safety: these models carry optional `source` / `demo` flags so the UI never
presents synthetic graph nodes as verified court or property facts.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class NodeType(str, Enum):
    defendant = "defendant"
    indemnitor = "indemnitor"
    company = "company"
    property = "property"
    vehicle = "vehicle"
    relative = "relative"
    phone = "phone"
    email = "email"
    social_account = "social_account"
    court_case = "court_case"
    arrest = "arrest"
    bond = "bond"
    note = "note"


class EdgeRelation(str, Enum):
    co_defendant = "co_defendant"
    indemnified_by = "indemnified_by"
    officer_of = "officer_of"
    owns_property = "owns_property"
    registered_vehicle = "registered_vehicle"
    family_member = "family_member"
    associated_phone = "associated_phone"
    associated_email = "associated_email"
    social_profile = "social_profile"
    same_address = "same_address"
    related_arrest = "related_arrest"
    active_bond = "active_bond"


class EntityNode(BaseModel):
    id: str = Field(..., description="Unique node identifier")
    type: NodeType = Field(..., description="Entity node type")
    label: str = Field(..., description="Display label for node")
    subtitle: Optional[str] = Field(None, description="Subtitle or detail text")
    risk_level: str = Field("low", description="low, medium, high, critical")
    metadata: Dict[str, Any] = Field(default_factory=dict)
    # Provenance — UI must show demo nodes differently from Mongo-backed facts
    source: str = Field("mongo", description="mongo | derived | demo | open_data")
    verified: bool = Field(True, description="False when demo/synthetic")


class RelationshipEdge(BaseModel):
    source: str = Field(..., description="Source node ID")
    target: str = Field(..., description="Target node ID")
    relation: EdgeRelation = Field(..., description="Type of relationship")
    label: Optional[str] = Field(None, description="Relationship description")
    confidence: float = Field(1.0, ge=0.0, le=1.0)
    source_kind: str = Field("mongo", description="mongo | derived | demo")
    verified: bool = True


class KnowledgeGraph(BaseModel):
    nodes: List[EntityNode] = Field(default_factory=list)
    edges: List[RelationshipEdge] = Field(default_factory=list)
    subject_id: Optional[str] = None
    subject_found: bool = False
    resolved_at: datetime = Field(default_factory=_utcnow)
    data_mode: str = Field(
        "live",
        description="live = Mongo-backed only; empty = no match; mixed if open-data feeds included",
    )
    warnings: List[str] = Field(default_factory=list)


class ThreatFeedItem(BaseModel):
    id: str
    category: str = Field(..., description="incident, flight, weather, cctv, court, arrest")
    title: str
    description: Optional[str] = None
    lat: float
    lng: float
    severity: str = Field("info", description="info, warning, danger, critical")
    timestamp: str
    source: str
    url: Optional[str] = None
    demo: bool = Field(False, description="True = illustrative only, not a live alert")


class BreachLookupRequest(BaseModel):
    email: Optional[str] = None
    phone: Optional[str] = None
    username: Optional[str] = None


class BreachLookupItem(BaseModel):
    breach_name: str
    domain: str
    breach_date: str
    compromised_data: List[str] = Field(default_factory=list)
    description: str = ""
    verified: bool = False
    demo: bool = True


class BreachLookupResponse(BaseModel):
    query: str
    found: bool
    total_breaches: int
    breaches: List[BreachLookupItem] = Field(default_factory=list)
    risk_impact: str = Field("unknown", description="low, medium, high, unknown")
    data_mode: str = Field(
        "unavailable",
        description="live when a breach provider is configured; otherwise unavailable",
    )
    message: Optional[str] = None


class DossierGenerateRequest(BaseModel):
    subject_id: str
    subject_type: str = Field("defendant", description="defendant or indemnitor")
    include_openplanter_graph: bool = True
    include_spectra_breach: bool = True
    include_osiris_threats: bool = True
    notes: Optional[str] = None


class PalantirDossier(BaseModel):
    dossier_id: str
    subject_id: str
    subject_name: str
    subject_found: bool = False
    generated_at: str
    risk_score: Optional[int] = Field(
        None,
        description="0-100 only when grounded in live records; null if insufficient data",
    )
    summary: str
    key_findings: List[str] = Field(default_factory=list)
    graph_summary: Dict[str, Any] = Field(default_factory=dict)
    breach_summary: Dict[str, Any] = Field(default_factory=dict)
    threat_proximity: List[Dict[str, Any]] = Field(default_factory=list)
    recommendation: str = ""
    data_mode: str = "live"
    warnings: List[str] = Field(default_factory=list)


class GraphResolveRequest(BaseModel):
    target: str = Field(..., min_length=1, description="Name, booking #, or subject id")
    subject_type: str = Field("defendant", description="defendant or indemnitor")


class BookingIntakePreviewRequest(BaseModel):
    url: str = Field(..., min_length=12, max_length=2048, description="Supported public booking URL")


class BookingIntakeConfirmRequest(BaseModel):
    preview_id: str = Field(..., min_length=12, max_length=160)
    confirmed_booking_number: str = Field(..., min_length=1, max_length=80)
    exact_match_confirmed: bool = Field(False, description="Staff confirms the preview is the exact booking record")
