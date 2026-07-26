"""
Family & Relationship Network Service — ShamrockLeads
=====================================================
Manages graph persistence, 1st & 2nd degree relative retrieval,
and formatting for relatives-tree renderer in Shamrock Dashboard.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from bson import ObjectId
from bson.errors import InvalidId

from dashboard.extensions import get_db
from dashboard.models.family_tree import (
    AddRelationshipRequest,
)

log = logging.getLogger("shamrock.family_tree")


def _slug(name: str) -> str:
    s = (name or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    return s or "unknown"


def _name_variants(name: str) -> List[str]:
    """Generate loose match keys for person lookup (case/spacing tolerant)."""
    raw = (name or "").strip()
    if not raw:
        return []
    variants = {raw, raw.lower(), raw.upper(), raw.title()}
    # "Last, First" ↔ "First Last"
    if "," in raw:
        parts = [p.strip() for p in raw.split(",", 1)]
        if len(parts) == 2 and parts[0] and parts[1]:
            flipped = f"{parts[1]} {parts[0]}".strip()
            variants.add(flipped)
            variants.add(flipped.lower())
    return list(variants)


class FamilyTreeService:
    """Async service for family network graph storage and retrieval."""

    def __init__(self):
        self._db = None

    def _get_db(self):
        if self._db is None:
            self._db = get_db()
        return self._db

    @property
    def _rel_col(self):
        return self._get_db()["family_relationships"]

    @property
    def _persons_col(self):
        return self._get_db()["persons"]

    @property
    def _bonds_col(self):
        return self._get_db()["active_bonds"]

    async def get_family_graph(self, person_id_or_name: str, max_degree: int = 1) -> Dict[str, Any]:
        """Fetch 1st (and optionally 2nd) degree relatives for a person.

        Output nodes follow relatives-tree shape:
          { id, gender, parents[], children[], siblings[], spouses[] }
        plus Shamrock extras: name, role, phone, email, has_active_bond.
        """
        col = self._rel_col
        bonds_col = self._bonds_col
        variants = _name_variants(person_id_or_name)
        slug = _slug(person_id_or_name)

        # Case-insensitive name match via regex on stored strings
        name_regex = re.compile(f"^{re.escape(person_id_or_name.strip())}$", re.IGNORECASE)
        query = {
            "$or": [
                {"person_id": {"$in": variants + [slug, person_id_or_name]}},
                {"person_name": name_regex},
                {"relative_name": name_regex},
                {"relative_id": {"$in": variants + [slug]}},
            ],
            "status": {"$ne": "soft_deleted"},
        }
        if max_degree:
            query["degree"] = {"$lte": max_degree}

        rel_docs = await col.find(query).to_list(length=300)

        # Expand 2nd degree: pull links where 1st-degree relatives are anchors
        if max_degree >= 2 and rel_docs:
            first_names = set()
            for r in rel_docs:
                for k in ("person_name", "relative_name", "person_id", "relative_id"):
                    v = r.get(k)
                    if v:
                        first_names.add(str(v))
            first_names.discard(person_id_or_name)
            if first_names:
                extra = await col.find({
                    "$or": [
                        {"person_name": {"$in": list(first_names)}},
                        {"relative_name": {"$in": list(first_names)}},
                        {"person_id": {"$in": list(first_names)}},
                    ],
                    "status": {"$ne": "soft_deleted"},
                    "degree": {"$lte": max_degree},
                }).to_list(length=300)
                seen_ids = {str(d.get("_id")) for d in rel_docs}
                for d in extra:
                    if str(d.get("_id")) not in seen_ids:
                        rel_docs.append(d)

        # Bond-linked co-signors / defendants (case-insensitive name)
        bond_or = []
        for v in variants:
            bond_or.extend([
                {"defendant_name": re.compile(f"^{re.escape(v)}$", re.IGNORECASE)},
                {"indemnitor_name": re.compile(f"^{re.escape(v)}$", re.IGNORECASE)},
                {"indemnitor.name": re.compile(f"^{re.escape(v)}$", re.IGNORECASE)},
            ])
        bonds = await bonds_col.find({"$or": bond_or} if bond_or else {"_id": None}).to_list(length=50)

        nodes_map: Dict[str, Dict[str, Any]] = {}
        relationships: List[Dict[str, Any]] = []

        def _touch_node(node_id: str, name: str, role: str = "relative", **extra):
            if node_id not in nodes_map:
                nodes_map[node_id] = {
                    "id": node_id,
                    "name": name,
                    "gender": extra.get("gender") or "unknown",
                    "role": role,
                    "phone": extra.get("phone"),
                    "email": extra.get("email"),
                    "has_active_bond": bool(extra.get("has_active_bond")),
                    "has_warrants": bool(extra.get("has_warrants")),
                    "parents": [],
                    "children": [],
                    "siblings": [],
                    "spouses": [],
                    "relatives": [],
                }
            else:
                if role and nodes_map[node_id].get("role") in (None, "relative", "root"):
                    if role != "relative":
                        nodes_map[node_id]["role"] = role
                for k in ("phone", "email"):
                    if extra.get(k) and not nodes_map[node_id].get(k):
                        nodes_map[node_id][k] = extra[k]
                if extra.get("has_active_bond"):
                    nodes_map[node_id]["has_active_bond"] = True
            return nodes_map[node_id]

        def _link(a_id: str, b_id: str, bucket: str, link_type: str, rel_id: Optional[str] = None):
            node = nodes_map[a_id]
            existing = {x.get("id") for x in node[bucket]}
            if b_id in existing:
                return
            entry: Dict[str, Any] = {"id": b_id, "type": link_type}
            if rel_id:
                entry["relationship_id"] = rel_id
            node[bucket].append(entry)

        root_id = _slug(person_id_or_name)
        display_name = person_id_or_name.strip()
        _touch_node(root_id, display_name, "root")

        for r in rel_docs:
            p_name = r.get("person_name") or r.get("person_id") or display_name
            r_name = r.get("relative_name") or r.get("relative_id") or "Relative"
            p_id = _slug(str(p_name))
            r_id = _slug(str(r_name))
            rel_id = str(r.get("_id")) if r.get("_id") is not None else None
            rel_type = (r.get("relation_type") or "relative").lower()

            _touch_node(
                p_id, str(p_name),
                phone=r.get("phone") if p_id == root_id else None,
            )
            _touch_node(
                r_id, str(r_name),
                phone=r.get("phone"),
                email=r.get("email"),
            )

            if rel_type == "parent":
                _link(p_id, r_id, "parents", "blood", rel_id)
                _link(r_id, p_id, "children", "blood", rel_id)
            elif rel_type == "child":
                _link(p_id, r_id, "children", "blood", rel_id)
                _link(r_id, p_id, "parents", "blood", rel_id)
            elif rel_type == "sibling":
                _link(p_id, r_id, "siblings", "blood", rel_id)
                _link(r_id, p_id, "siblings", "blood", rel_id)
            elif rel_type == "spouse":
                _link(p_id, r_id, "spouses", "married", rel_id)
                _link(r_id, p_id, "spouses", "married", rel_id)
            else:
                _link(p_id, r_id, "relatives", rel_type or "extended", rel_id)

            # Normalize so root-facing list always has relative as the "other"
            other_name = r_name if _slug(str(p_name)) == root_id else p_name
            if _slug(str(other_name)) == root_id:
                other_name = r_name if p_id == root_id else p_name
            relationships.append({
                "relationship_id": rel_id,
                "person_name": p_name,
                "relative_name": r_name if p_id == root_id else (
                    p_name if r_id == root_id else r_name
                ),
                "relation_type": rel_type,
                "degree": r.get("degree", 1),
                "confidence": r.get("confidence", "confirmed"),
                "phone": r.get("phone"),
                "email": r.get("email"),
                "notes": r.get("notes"),
                "status": r.get("status", "active"),
            })

        for b in bonds:
            def_name = (b.get("defendant_name") or "").strip()
            ind = b.get("indemnitor") if isinstance(b.get("indemnitor"), dict) else {}
            ind_name = (b.get("indemnitor_name") or ind.get("name") or "").strip()
            if not def_name or not ind_name:
                continue
            d_id = _slug(def_name)
            i_id = _slug(ind_name)
            _touch_node(d_id, def_name, "defendant", has_active_bond=True)
            _touch_node(
                i_id, ind_name, "indemnitor",
                phone=b.get("indemnitor_phone") or ind.get("phone"),
                email=b.get("indemnitor_email") or ind.get("email"),
            )
            _link(d_id, i_id, "relatives", "co_indemnitor")
            _link(i_id, d_id, "relatives", "co_indemnitor")

        # Prefer root display name from bonds if exact match
        if root_id in nodes_map:
            nodes_map[root_id]["role"] = "root"

        return {
            "root_id": root_id,
            "root_name": display_name,
            "degree_limit": max_degree,
            "total_nodes": len(nodes_map),
            "nodes": list(nodes_map.values()),
            "relationships": relationships,
        }

    async def add_relationship(self, req: AddRelationshipRequest, actor: str = "admin") -> str:
        """Add a family relationship record to MongoDB."""
        col = self._rel_col
        now = datetime.now(timezone.utc)

        doc = {
            "person_id": req.person_id,
            "person_name": req.person_id,
            "relative_id": req.relative_id or _slug(req.relative_name),
            "relative_name": req.relative_name,
            "relation_type": req.relation_type.value,
            "degree": req.degree,
            "phone": req.phone,
            "email": req.email,
            "confidence": req.confidence.value,
            "notes": req.notes,
            "status": "active",
            "created_by": actor,
            "created_at": now,
            "updated_at": now,
        }

        # Upsert person shells for searchability
        persons = self._persons_col
        for name, pid in (
            (req.person_id, _slug(req.person_id)),
            (req.relative_name, req.relative_id or _slug(req.relative_name)),
        ):
            await persons.update_one(
                {"person_id": pid},
                {
                    "$set": {
                        "full_name": name,
                        "updated_at": now,
                    },
                    "$setOnInsert": {
                        "person_id": pid,
                        "created_at": now,
                    },
                },
                upsert=True,
            )

        res = await col.insert_one(doc)
        return str(res.inserted_id)

    async def soft_delete_relationship(self, rel_id: str) -> bool:
        """Soft-delete relationship record for audit retention."""
        col = self._rel_col
        try:
            oid = ObjectId(rel_id)
        except (InvalidId, TypeError):
            return False
        res = await col.update_one(
            {"_id": oid},
            {"$set": {"status": "soft_deleted", "updated_at": datetime.now(timezone.utc)}},
        )
        return res.modified_count > 0 or res.matched_count > 0

    async def list_relationships(self, person_id_or_name: str, limit: int = 100) -> List[Dict[str, Any]]:
        """List active relationship docs for a person (for link management UI)."""
        name_regex = re.compile(f"^{re.escape(person_id_or_name.strip())}$", re.IGNORECASE)
        variants = _name_variants(person_id_or_name)
        cursor = self._rel_col.find({
            "$or": [
                {"person_id": {"$in": variants}},
                {"person_name": name_regex},
                {"relative_name": name_regex},
            ],
            "status": {"$ne": "soft_deleted"},
        }).sort("created_at", -1).limit(limit)
        out = []
        async for doc in cursor:
            doc["relationship_id"] = str(doc.pop("_id"))
            out.append(doc)
        return out


_service_instance: Optional[FamilyTreeService] = None


def get_family_tree_service() -> FamilyTreeService:
    global _service_instance
    if _service_instance is None:
        _service_instance = FamilyTreeService()
    return _service_instance
