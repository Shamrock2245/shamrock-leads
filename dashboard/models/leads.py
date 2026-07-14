"""Pydantic Models for Leads Query Validation"""
from __future__ import annotations

from typing import Optional
from pydantic import BaseModel, Field, field_validator


class LeadsQueryModel(BaseModel):
    """Pydantic model for structured, type-safe lead querying."""
    status: Optional[str] = Field(default="", description="Lead status: Hot, Warm, Cold, etc.")
    county: Optional[str] = Field(default="", description="Single county or comma-separated list (supports 'Lee (FL)' labels)")
    state: Optional[str] = Field(default="", description="State code filter: FL, GA, SC, NC (comma-separated OK)")
    custody: Optional[str] = Field(default="", description="Custody status filter: true (in custody) or released")
    days: Optional[int] = Field(default=None, description="Number of recent days to filter by scraped_at")
    min_bond: Optional[float] = Field(default=None, description="Minimum bond amount")
    search: Optional[str] = Field(default="", description="Regex search query across names, charges, booking, or case numbers")
    sort: Optional[str] = Field(default="lead_score", description="Field to sort by")
    order: Optional[str] = Field(default="desc", description="Sort direction: asc or desc")
    page: int = Field(default=1, ge=1, description="Page number for pagination")
    limit: int = Field(default=50, ge=1, le=200, description="Items per page")

    @field_validator("days", mode="before")
    @classmethod
    def validate_days(cls, v) -> Optional[int]:
        """Gracefully handle empty strings and validate integer range."""
        if v == "" or v is None:
            return None
        try:
            val = int(v)
            if 1 <= val <= 30:
                return val
            return None
        except (ValueError, TypeError):
            return None

    @field_validator("min_bond", mode="before")
    @classmethod
    def validate_min_bond(cls, v) -> Optional[float]:
        """Gracefully handle empty strings and parse float."""
        if v == "" or v is None:
            return None
        try:
            return float(v)
        except (ValueError, TypeError):
            return None
