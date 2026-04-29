"""
ShamrockLeads — Defendant Normalization Service (Phase 2)

Builds and maintains the `defendants` MongoDB collection by:
  1. Deduplicating persons across multiple arrest records (same person, different bookings)
  2. Normalizing name, DOB, address, and physical descriptor fields
  3. Merging arrest history into a single DefendantRecord per person
  4. Emitting AuditEvents on create/merge operations

Identity Resolution Strategy:
  - Primary key: normalized(last_name) + normalized(first_name) + dob
  - Fuzzy fallback: Levenshtein distance ≤ 2 on full_name + same DOB
  - Collision guard: if DOB is blank, require exact full_name match

Collections written:
  - `defendants`    — one document per unique person
  - `audit_events`  — immutable event log (Phase 2+)
"""
from __future__ import annotations

import logging
import re
import unicodedata
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ── Normalization helpers ─────────────────────────────────────────────────────

_SUFFIX_RE = re.compile(
    r"\b(JR|SR|II|III|IV|V|ESQ|MD|PHD|DDS|RN)\b\.?$",
    re.IGNORECASE,
)
_WHITESPACE_RE = re.compile(r"\s+")
_NON_ALPHA_RE = re.compile(r"[^a-z ]")


def _strip_accents(text: str) -> str:
    """Remove diacritics from unicode text."""
    return "".join(
        c for c in unicodedata.normalize("NFD", text)
        if unicodedata.category(c) != "Mn"
    )


def normalize_name_part(raw: str) -> str:
    """
    Normalize a name token for identity comparison.
    Steps: lower → strip accents → remove suffixes → collapse whitespace → strip punctuation.
    """
    if not raw:
        return ""
    s = raw.strip().lower()
    s = _strip_accents(s)
    s = _SUFFIX_RE.sub("", s).strip()
    s = _NON_ALPHA_RE.sub(" ", s)
    s = _WHITESPACE_RE.sub(" ", s).strip()
    return s


def normalize_dob(raw: str) -> str:
    """
    Normalize date-of-birth to YYYY-MM-DD.
    Handles: MM/DD/YYYY, MM-DD-YYYY, YYYY-MM-DD, M/D/YYYY.
    Returns empty string if unparseable.
    """
    if not raw:
        return ""
    raw = raw.strip()
    # Already ISO
    if re.match(r"^\d{4}-\d{2}-\d{2}$", raw):
        return raw
    # MM/DD/YYYY or M/D/YYYY
    m = re.match(r"^(\d{1,2})[/\-](\d{1,2})[/\-](\d{4})$", raw)
    if m:
        month, day, year = m.group(1), m.group(2), m.group(3)
        return f"{year}-{int(month):02d}-{int(day):02d}"
    return raw  # Return as-is if unrecognised format


def normalize_phone(raw: str) -> str:
    """Strip non-digits and format as E.164 (US +1 assumed)."""
    if not raw:
        return ""
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 10:
        return f"+1{digits}"
    if len(digits) == 11 and digits.startswith("1"):
        return f"+{digits}"
    return digits  # Return raw digits if format is unexpected


def normalize_address(raw: str) -> str:
    """Title-case and collapse whitespace in an address string."""
    if not raw:
        return ""
    return _WHITESPACE_RE.sub(" ", raw.strip()).title()


def _levenshtein(a: str, b: str) -> int:
    """Compute Levenshtein edit distance between two strings."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        curr = [i]
        for j, cb in enumerate(b, 1):
            curr.append(min(prev[j] + 1, curr[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = curr
    return prev[-1]


# ── Identity key ──────────────────────────────────────────────────────────────

def make_identity_key(last: str, first: str, dob: str) -> str:
    """
    Canonical identity key used as the upsert filter in `defendants`.
    Format: ``norm_last:norm_first:dob``
    """
    return f"{normalize_name_part(last)}:{normalize_name_part(first)}:{normalize_dob(dob)}"


# ── Main service ──────────────────────────────────────────────────────────────

class DefendantNormalizationService:
    """
    Async service that builds/maintains the `defendants` collection.

    Usage (from an async context)::

        svc = DefendantNormalizationService(db)
        result = await svc.normalize_arrest(arrest_doc)
    """

    FUZZY_THRESHOLD = 2  # Max Levenshtein distance for fuzzy name match

    def __init__(self, db):
        self.db = db
        self._defendants = db["defendants"]
        self._arrests = db["arrests"]
        self._audit = db["audit_events"]

    # ── Public API ────────────────────────────────────────────────────────────

    async def normalize_arrest(self, arrest_doc: Dict[str, Any]) -> Dict[str, Any]:
        """
        Normalize a single arrest document into the `defendants` collection.

        Returns a dict with:
          - ``defendant_id``  — UUID of the defendant record
          - ``action``        — ``"created"`` | ``"merged"``
          - ``identity_key``  — the canonical key used for lookup
        """
        norm = self._extract_normalized_fields(arrest_doc)
        identity_key = make_identity_key(
            norm["last_name"], norm["first_name"], norm["dob"]
        )

        existing = await self._defendants.find_one(
            {"identity_key": identity_key}, {"_id": 0}
        )

        if existing:
            defendant_id = existing["defendant_id"]
            await self._merge_arrest_into_defendant(existing, arrest_doc, norm)
            action = "merged"
        else:
            # Try fuzzy match before creating a new record
            fuzzy_match = await self._fuzzy_lookup(norm)
            if fuzzy_match:
                defendant_id = fuzzy_match["defendant_id"]
                await self._merge_arrest_into_defendant(fuzzy_match, arrest_doc, norm)
                action = "merged_fuzzy"
            else:
                defendant_id = await self._create_defendant(arrest_doc, norm, identity_key)
                action = "created"

        # Back-reference: stamp the arrest record with its defendant_id
        await self._arrests.update_one(
            {
                "county": arrest_doc.get("county", ""),
                "booking_number": arrest_doc.get("booking_number", ""),
            },
            {"$set": {"defendant_id": defendant_id}},
        )

        return {
            "defendant_id": defendant_id,
            "action": action,
            "identity_key": identity_key,
        }

    async def normalize_batch(
        self, county: str = None, limit: int = 500
    ) -> Dict[str, Any]:
        """
        Backfill normalization for existing arrest records that have no
        ``defendant_id`` stamp yet.  Processes up to ``limit`` records.

        Returns summary stats.
        """
        query: Dict[str, Any] = {"defendant_id": {"$exists": False}}
        if county:
            query["county"] = county

        cursor = self._arrests.find(query).limit(limit)
        created = merged = errors = 0

        async for doc in cursor:
            try:
                result = await self.normalize_arrest(doc)
                if result["action"] == "created":
                    created += 1
                else:
                    merged += 1
            except Exception as exc:
                errors += 1
                logger.warning(
                    "normalize_batch error for %s/%s: %s",
                    doc.get("county"), doc.get("booking_number"), exc,
                )

        logger.info(
            "normalize_batch: created=%d merged=%d errors=%d county=%s",
            created, merged, errors, county or "ALL",
        )
        return {
            "created": created,
            "merged": merged,
            "errors": errors,
            "county": county or "ALL",
        }

    async def get_defendant(self, defendant_id: str) -> Optional[Dict[str, Any]]:
        """Fetch a single defendant by UUID."""
        doc = await self._defendants.find_one(
            {"defendant_id": defendant_id}, {"_id": 0}
        )
        return doc

    async def get_defendant_arrests(self, defendant_id: str) -> List[Dict[str, Any]]:
        """Return all arrest records linked to a defendant_id."""
        cursor = self._arrests.find(
            {"defendant_id": defendant_id}, {"_id": 0}
        ).sort("booking_date", -1)
        return await cursor.to_list(length=200)

    async def search_defendants(
        self,
        query_str: str = "",
        county: str = "",
        page: int = 1,
        limit: int = 20,
        min_arrests: int = 0,
    ) -> Dict[str, Any]:
        """
        Paginated search over the `defendants` collection.
        Supports full-name substring, county filter, and min arrest count.
        """
        mongo_query: Dict[str, Any] = {}
        if query_str:
            mongo_query["$or"] = [
                {"full_name": {"$regex": query_str, "$options": "i"}},
                {"last_name": {"$regex": query_str, "$options": "i"}},
                {"first_name": {"$regex": query_str, "$options": "i"}},
                {"dob": {"$regex": query_str, "$options": "i"}},
                {"phone": {"$regex": query_str, "$options": "i"}},
                {"address": {"$regex": query_str, "$options": "i"}},
            ]
        if county:
            mongo_query["counties"] = county
        if min_arrests > 0:
            mongo_query["total_arrests"] = {"$gte": min_arrests}

        total = await self._defendants.count_documents(mongo_query)
        cursor = (
            self._defendants.find(mongo_query, {"_id": 0})
            .sort("total_arrests", -1)
            .skip((page - 1) * limit)
            .limit(limit)
        )
        results = []
        async for doc in cursor:
            results.append(_serialize_doc(doc))

        return {
            "defendants": results,
            "total": total,
            "page": page,
            "pages": max(1, (total + limit - 1) // limit),
        }

    async def update_defendant_contact(
        self,
        defendant_id: str,
        phone: str = None,
        email: str = None,
        address: str = None,
        agent: str = "dashboard",
    ) -> bool:
        """
        Manually update contact fields on a defendant record.
        Emits an audit event.
        """
        updates: Dict[str, Any] = {
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        if phone is not None:
            updates["phone"] = normalize_phone(phone)
        if email is not None:
            updates["email"] = email.strip().lower()
        if address is not None:
            updates["address"] = normalize_address(address)

        result = await self._defendants.update_one(
            {"defendant_id": defendant_id},
            {"$set": updates},
        )
        if result.modified_count:
            await self._emit_audit(
                entity_type="defendant",
                entity_id=defendant_id,
                event_type="contact_updated",
                detail=updates,
                agent=agent,
            )
        return bool(result.modified_count)

    async def merge_defendants(
        self,
        primary_id: str,
        secondary_id: str,
        agent: str = "dashboard",
    ) -> Dict[str, Any]:
        """
        Manually merge two defendant records.
        The secondary record's arrest_ids are absorbed into the primary.
        The secondary record is tombstoned (merged_into set, active=False).
        """
        primary = await self._defendants.find_one({"defendant_id": primary_id})
        secondary = await self._defendants.find_one({"defendant_id": secondary_id})

        if not primary or not secondary:
            return {"success": False, "error": "One or both defendants not found"}

        # Merge arrest_ids and counties
        merged_arrest_ids = list(
            set(primary.get("arrest_ids", []) + secondary.get("arrest_ids", []))
        )
        merged_counties = list(
            set(primary.get("counties", []) + secondary.get("counties", []))
        )

        now = datetime.now(timezone.utc).isoformat()
        await self._defendants.update_one(
            {"defendant_id": primary_id},
            {
                "$set": {
                    "arrest_ids": merged_arrest_ids,
                    "counties": merged_counties,
                    "total_arrests": len(merged_arrest_ids),
                    "updated_at": now,
                    "phone": primary.get("phone") or secondary.get("phone", ""),
                    "email": primary.get("email") or secondary.get("email", ""),
                    "address": primary.get("address") or secondary.get("address", ""),
                    "mugshot_url": primary.get("mugshot_url") or secondary.get("mugshot_url", ""),
                }
            },
        )

        # Tombstone the secondary
        await self._defendants.update_one(
            {"defendant_id": secondary_id},
            {
                "$set": {
                    "active": False,
                    "merged_into": primary_id,
                    "merged_at": now,
                    "updated_at": now,
                }
            },
        )

        # Re-point all arrests that referenced secondary → primary
        await self._arrests.update_many(
            {"defendant_id": secondary_id},
            {"$set": {"defendant_id": primary_id}},
        )

        await self._emit_audit(
            entity_type="defendant",
            entity_id=primary_id,
            event_type="defendants_merged",
            detail={
                "primary_id": primary_id,
                "secondary_id": secondary_id,
                "merged_arrest_ids": len(merged_arrest_ids),
            },
            agent=agent,
        )

        return {
            "success": True,
            "primary_id": primary_id,
            "secondary_id": secondary_id,
            "total_arrests": len(merged_arrest_ids),
        }

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _extract_normalized_fields(self, doc: Dict[str, Any]) -> Dict[str, Any]:
        """Pull and normalize identity fields from an arrest document."""
        raw_first = doc.get("first_name", "") or ""
        raw_middle = doc.get("middle_name", "") or ""
        raw_last = doc.get("last_name", "") or ""
        raw_full = doc.get("full_name", "") or ""

        # Build full_name from parts if not present
        if not raw_full and (raw_first or raw_last):
            parts = [p for p in [raw_first, raw_middle, raw_last] if p]
            raw_full = " ".join(parts)

        dob = normalize_dob(doc.get("dob", "") or "")

        return {
            "first_name": raw_first.strip().title(),
            "middle_name": raw_middle.strip().title(),
            "last_name": raw_last.strip().title(),
            "full_name": raw_full.strip().title(),
            "norm_first": normalize_name_part(raw_first),
            "norm_last": normalize_name_part(raw_last),
            "dob": dob,
            "sex": (doc.get("sex", "") or "").upper()[:1],
            "race": (doc.get("race", "") or "").strip().title(),
            "height": (doc.get("height", "") or "").strip(),
            "weight": (doc.get("weight", "") or "").strip(),
            "phone": normalize_phone(doc.get("phone", "") or ""),
            "email": (doc.get("email", "") or "").strip().lower(),
            "address": normalize_address(doc.get("address", "") or ""),
            "city": (doc.get("city", "") or "").strip().title(),
            "state": (doc.get("state", "") or "FL").strip().upper(),
            "zip_code": (doc.get("zip", "") or "").strip(),
            "mugshot_url": (doc.get("mugshot_url", "") or "").strip(),
            "county": (doc.get("county", "") or "").strip(),
            "booking_number": (doc.get("booking_number", "") or "").strip(),
        }

    async def _fuzzy_lookup(
        self, norm: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """
        Attempt a fuzzy name match against existing defendants with the same DOB.
        Only used when exact identity_key lookup fails.
        """
        dob = norm["dob"]
        if not dob:
            return None  # Can't fuzzy-match without DOB

        # Fetch candidates with the same DOB
        cursor = self._defendants.find(
            {"dob": dob, "active": {"$ne": False}}, {"_id": 0}
        )
        target_full = f"{norm['norm_last']} {norm['norm_first']}"

        async for candidate in cursor:
            cand_last = normalize_name_part(candidate.get("last_name", ""))
            cand_first = normalize_name_part(candidate.get("first_name", ""))
            cand_full = f"{cand_last} {cand_first}"
            dist = _levenshtein(target_full, cand_full)
            if dist <= self.FUZZY_THRESHOLD:
                logger.info(
                    "Fuzzy match: '%s' ≈ '%s' (dist=%d)",
                    target_full, cand_full, dist,
                )
                return candidate
        return None

    async def _create_defendant(
        self,
        arrest_doc: Dict[str, Any],
        norm: Dict[str, Any],
        identity_key: str,
    ) -> str:
        """Insert a new defendant record. Returns the new defendant_id."""
        now = datetime.now(timezone.utc).isoformat()
        defendant_id = str(uuid.uuid4())
        booking_key = f"{norm['county']}:{norm['booking_number']}"

        doc = {
            "defendant_id": defendant_id,
            "identity_key": identity_key,
            "first_name": norm["first_name"],
            "middle_name": norm["middle_name"],
            "last_name": norm["last_name"],
            "full_name": norm["full_name"],
            "dob": norm["dob"],
            "sex": norm["sex"],
            "race": norm["race"],
            "height": norm["height"],
            "weight": norm["weight"],
            "phone": norm["phone"],
            "email": norm["email"],
            "address": norm["address"],
            "city": norm["city"],
            "state": norm["state"],
            "zip_code": norm["zip_code"],
            "mugshot_url": norm["mugshot_url"],
            "arrest_ids": [booking_key] if booking_key.strip(":") else [],
            "counties": [norm["county"]] if norm["county"] else [],
            "total_arrests": 1,
            "first_seen": now,
            "last_seen": now,
            "active": True,
            "source": "normalizer",
            "created_at": now,
            "updated_at": now,
        }

        await self._defendants.insert_one(doc)
        await self._emit_audit(
            entity_type="defendant",
            entity_id=defendant_id,
            event_type="defendant_created",
            detail={
                "identity_key": identity_key,
                "booking_number": norm["booking_number"],
                "county": norm["county"],
            },
        )
        logger.debug("Created defendant %s (%s)", defendant_id, norm["full_name"])
        return defendant_id

    async def _merge_arrest_into_defendant(
        self,
        existing: Dict[str, Any],
        arrest_doc: Dict[str, Any],
        norm: Dict[str, Any],
    ) -> None:
        """Merge a new arrest record into an existing defendant document."""
        defendant_id = existing["defendant_id"]
        booking_key = f"{norm['county']}:{norm['booking_number']}"
        now = datetime.now(timezone.utc).isoformat()

        # Build incremental update
        set_fields: Dict[str, Any] = {
            "last_seen": now,
            "updated_at": now,
        }

        # Enrich contact fields only if currently blank
        if norm["phone"] and not existing.get("phone"):
            set_fields["phone"] = norm["phone"]
        if norm["email"] and not existing.get("email"):
            set_fields["email"] = norm["email"]
        if norm["address"] and not existing.get("address"):
            set_fields["address"] = norm["address"]
            set_fields["city"] = norm["city"]
            set_fields["state"] = norm["state"]
            set_fields["zip_code"] = norm["zip_code"]
        if norm["mugshot_url"] and not existing.get("mugshot_url"):
            set_fields["mugshot_url"] = norm["mugshot_url"]

        # Add booking_key to arrest_ids if not already present
        add_to_set: Dict[str, Any] = {}
        if booking_key.strip(":"):
            add_to_set["arrest_ids"] = booking_key
        if norm["county"]:
            add_to_set["counties"] = norm["county"]

        update: Dict[str, Any] = {"$set": set_fields}
        if add_to_set:
            update["$addToSet"] = add_to_set

        await self._defendants.update_one(
            {"defendant_id": defendant_id}, update
        )

        # Recalculate total_arrests from actual arrest_ids length
        updated = await self._defendants.find_one(
            {"defendant_id": defendant_id}, {"arrest_ids": 1}
        )
        if updated:
            total = len(updated.get("arrest_ids", []))
            await self._defendants.update_one(
                {"defendant_id": defendant_id},
                {"$set": {"total_arrests": total}},
            )

        logger.debug(
            "Merged arrest %s into defendant %s", booking_key, defendant_id
        )

    async def _emit_audit(
        self,
        entity_type: str,
        entity_id: str,
        event_type: str,
        detail: Dict[str, Any] = None,
        agent: str = "normalizer",
    ) -> None:
        """Append an immutable audit event to the `audit_events` collection."""
        try:
            await self._audit.insert_one(
                {
                    "event_id": str(uuid.uuid4()),
                    "entity_type": entity_type,
                    "entity_id": entity_id,
                    "event_type": event_type,
                    "detail": detail or {},
                    "agent": agent,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            )
        except Exception as exc:
            logger.warning("audit emit failed: %s", exc)


def _serialize_doc(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Convert any datetime objects in a MongoDB doc to ISO strings."""
    result = {}
    for k, v in doc.items():
        if isinstance(v, datetime):
            result[k] = v.isoformat()
        else:
            result[k] = v
    return result
