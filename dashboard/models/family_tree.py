"""
Pydantic Models — Family & Relationship Network Module
======================================================
1st and 2nd degree family tree graph models for ShamrockLeads.
Supports parents, children, siblings, spouses, and extended relatives.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional  # Any used by FamilyTreeNode link dicts
from pydantic import BaseModel, Field


class RelationType(str, Enum):
    parent = "parent"
    child = "child"
    sibling = "sibling"
    spouse = "spouse"
    relative = "relative"
    co_indemnitor = "co_indemnitor"


class RelationshipConfidence(str, Enum):
    confirmed = "confirmed"
    probable = "probable"
    unverified = "unverified"


class RelationshipStatus(str, Enum):
    active = "active"
    soft_deleted = "soft_deleted"


class AddRelationshipRequest(BaseModel):
    person_id: str = Field(..., description="ID or name of anchor person")
    relative_id: Optional[str] = Field(None, description="ID of relative if existing")
    relative_name: str = Field(..., description="Full legal name of relative")
    relation_type: RelationType = Field(RelationType.relative, description="parent, child, sibling, spouse, relative")
    degree: int = Field(1, ge=1, le=3, description="1 for 1st-degree, 2 for 2nd-degree")
    phone: Optional[str] = Field(None, description="Phone number")
    email: Optional[str] = Field(None, description="Email address")
    confidence: RelationshipConfidence = Field(RelationshipConfidence.confirmed)
    notes: Optional[str] = Field(None, description="Internal notes")


class FamilyTreeNode(BaseModel):
    id: str
    name: str
    gender: Optional[str] = "unknown"
    role: Optional[str] = "relative"
    phone: Optional[str] = None
    email: Optional[str] = None
    has_active_bond: bool = False
    has_warrants: bool = False
    parents: List[Dict[str, Any]] = Field(default_factory=list)
    children: List[Dict[str, Any]] = Field(default_factory=list)
    siblings: List[Dict[str, Any]] = Field(default_factory=list)
    spouses: List[Dict[str, Any]] = Field(default_factory=list)
    relatives: List[Dict[str, Any]] = Field(default_factory=list)


class FamilyTreeGraphResponse(BaseModel):
    root_id: str
    root_name: str
    degree_limit: int = 1
    total_nodes: int = 0
    nodes: List[FamilyTreeNode] = Field(default_factory=list)
    relationships: List[Dict[str, Any]] = Field(default_factory=list)
