"""
Palantir Intelligence Hub Router — ShamrockLeads
================================================
OpenPlanter graph · OSIRIS tactical feeds · SPECTRA breach · AI dossiers

Prime directive (fail closed): never invent property deeds, LLC filings,
phones, or indemnitors. Graph nodes come from Mongo when the subject
resolves; otherwise return an empty graph with a clear warning.
"""
from __future__ import annotations

import hashlib
import logging
import re
import secrets
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, HTTPException, Query

from dashboard.extensions import get_collection
from dashboard.models.palantir import (
    BreachLookupItem,
    BreachLookupRequest,
    BreachLookupResponse,
    DossierGenerateRequest,
    EdgeRelation,
    EntityNode,
    GraphResolveRequest,
    KnowledgeGraph,
    NodeType,
    PalantirDossier,
    RelationshipEdge,
    ThreatFeedItem,
)

logger = logging.getLogger(__name__)

palantir_router = APIRouter(prefix="/api/palantir", tags=["palantir_intel"])

# SWFL centroid defaults for open-data / map framing only (not attributed to a person)
_SWFL_LAT = 26.6406
_SWFL_LNG = -81.8723


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stable_id(*parts: str) -> str:
    raw = "|".join(str(p or "") for p in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def _mask_phone(raw: Any) -> str:
    digits = re.sub(r"\D", "", str(raw or ""))
    if len(digits) < 10:
        return ""
    d = digits[-10:]
    return f"({d[0:3]}) ***-{d[6:]}"


def _mask_email(raw: Any) -> str:
    s = str(raw or "").strip()
    if "@" not in s:
        return ""
    local, _, domain = s.partition("@")
    if not local:
        return ""
    return f"{local[0]}***@{domain}"


def _safe_str(val: Any, limit: int = 200) -> str:
    if val is None:
        return ""
    return str(val).strip()[:limit]


async def _find_subject(subject_id: str, subject_type: str) -> Tuple[Optional[dict], str]:
    """
    Resolve a defendant/indemnitor from Mongo without inventing identity.
    Returns (doc, collection_name) or (None, '').
    """
    q = (subject_id or "").strip()
    if not q:
        return None, ""

    if subject_type not in ("defendant", "indemnitor"):
        subject_type = "defendant"

    coll_name = "defendants" if subject_type == "defendant" else "indemnitors"
    col = get_collection(coll_name)

    # ObjectId direct
    if len(q) == 24 and re.fullmatch(r"[0-9a-fA-F]{24}", q):
        try:
            doc = await col.find_one({"_id": ObjectId(q)})
            if doc:
                return doc, coll_name
        except (InvalidId, TypeError):
            pass

    # Structured keys
    or_clauses: List[dict] = [
        {"Defendant_ID": q},
        {"defendant_id": q},
        {"Indemnitor_ID": q},
        {"indemnitor_id": q},
        {"booking_number": q},
        {"Booking_Number": q},
        {"packet_id": q},
    ]
    # Exact name (case-insensitive) — not free-form fuzzy match of partials under 3 chars
    if len(q) >= 3:
        escaped = re.escape(q)
        or_clauses.append({"name": {"$regex": f"^{escaped}$", "$options": "i"}})
        or_clauses.append({"full_name": {"$regex": f"^{escaped}$", "$options": "i"}})
        or_clauses.append({"Name": {"$regex": f"^{escaped}$", "$options": "i"}})

    doc = await col.find_one({"$or": or_clauses})
    if doc:
        return doc, coll_name

    # Secondary: arrests / active_bonds by booking or name (defendant only)
    if subject_type == "defendant":
        arrests = get_collection("arrests")
        arrest = await arrests.find_one(
            {
                "$or": [
                    {"Booking_Number": q},
                    {"booking_number": q},
                    {"Name": {"$regex": f"^{re.escape(q)}$", "$options": "i"}} if len(q) >= 3 else {"_id": None},
                    {"name": {"$regex": f"^{re.escape(q)}$", "$options": "i"}} if len(q) >= 3 else {"_id": None},
                ]
            }
        )
        if arrest:
            # Normalize arrest row as a lightweight subject
            return {
                "_id": arrest.get("_id"),
                "name": arrest.get("Name") or arrest.get("name") or q,
                "booking_number": arrest.get("Booking_Number") or arrest.get("booking_number"),
                "county": arrest.get("County") or arrest.get("county"),
                "phone": arrest.get("Phone") or arrest.get("phone"),
                "from_arrest": True,
                "charges": arrest.get("Charges") or arrest.get("charges"),
                "bond_amount": arrest.get("Bond_Amount") or arrest.get("bond_amount"),
            }, "arrests"

    return None, ""


def _subject_display_name(doc: dict, fallback: str) -> str:
    return (
        _safe_str(doc.get("name"))
        or _safe_str(doc.get("full_name"))
        or _safe_str(doc.get("Name"))
        or _safe_str(fallback)
        or "Unknown"
    )


def _node(
    nid: str,
    ntype: NodeType,
    label: str,
    *,
    subtitle: str = "",
    risk: str = "low",
    metadata: Optional[dict] = None,
    source: str = "mongo",
    verified: bool = True,
) -> EntityNode:
    return EntityNode(
        id=nid,
        type=ntype,
        label=label[:120],
        subtitle=(subtitle or "")[:200] or None,
        risk_level=risk,
        metadata=metadata or {},
        source=source,
        verified=verified,
    )


def _edge(
    source: str,
    target: str,
    relation: EdgeRelation,
    *,
    label: str = "",
    confidence: float = 1.0,
    source_kind: str = "mongo",
    verified: bool = True,
) -> RelationshipEdge:
    return RelationshipEdge(
        source=source,
        target=target,
        relation=relation,
        label=label or relation.value,
        confidence=confidence,
        source_kind=source_kind,
        verified=verified,
    )


async def _build_live_graph(subject_id: str, subject_type: str) -> KnowledgeGraph:
    warnings: List[str] = []
    nodes: List[EntityNode] = []
    edges: List[RelationshipEdge] = []

    doc, coll = await _find_subject(subject_id, subject_type)
    if not doc:
        return KnowledgeGraph(
            nodes=[],
            edges=[],
            subject_id=subject_id,
            subject_found=False,
            data_mode="empty",
            warnings=[
                "No matching defendant/indemnitor found in MongoDB. "
                "Enter an exact name, booking number, or record ID. "
                "We do not invent property, LLC, or family links."
            ],
        )

    subj_name = _subject_display_name(doc, subject_id)
    root_id = f"subj_{_stable_id(coll, str(doc.get('_id') or subject_id))}"
    root_type = NodeType.defendant if subject_type == "defendant" else NodeType.indemnitor
    risk = "high" if subject_type == "defendant" else "medium"
    subtitle_bits = []
    if doc.get("booking_number") or doc.get("Booking_Number"):
        subtitle_bits.append(f"Booking {doc.get('booking_number') or doc.get('Booking_Number')}")
    if doc.get("county") or doc.get("County"):
        subtitle_bits.append(str(doc.get("county") or doc.get("County")))
    if doc.get("from_arrest"):
        subtitle_bits.append("from arrests collection")
        warnings.append("Subject resolved from arrest lead, not defendants collection.")

    nodes.append(
        _node(
            root_id,
            root_type,
            subj_name,
            subtitle=" · ".join(subtitle_bits) or f"Primary {subject_type}",
            risk=risk,
            metadata={
                "collection": coll,
                "mongo_id": str(doc.get("_id") or ""),
                "booking_number": _safe_str(doc.get("booking_number") or doc.get("Booking_Number")),
            },
            source="mongo",
            verified=True,
        )
    )

    # Phone / email — only if present on record (masked in label)
    phone_raw = doc.get("phone") or doc.get("Phone") or doc.get("Phone_Number")
    phone_label = _mask_phone(phone_raw)
    if phone_label:
        pid = f"phone_{_stable_id(root_id, phone_label)}"
        nodes.append(
            _node(pid, NodeType.phone, phone_label, subtitle="On file (masked)", risk="low")
        )
        edges.append(_edge(root_id, pid, EdgeRelation.associated_phone, label="Phone on file"))

    email_raw = doc.get("email") or doc.get("Email")
    email_label = _mask_email(email_raw)
    if email_label:
        eid = f"email_{_stable_id(root_id, email_label)}"
        nodes.append(
            _node(eid, NodeType.email, email_label, subtitle="On file (masked)", risk="low")
        )
        edges.append(_edge(root_id, eid, EdgeRelation.associated_email, label="Email on file"))

    # Address if present (as property node — still from our DB, not external appraiser)
    addr = (
        _safe_str(doc.get("address"))
        or _safe_str(doc.get("Address"))
        or _safe_str(doc.get("home_address"))
    )
    if addr:
        prop_id = f"addr_{_stable_id(root_id, addr)}"
        city = _safe_str(doc.get("city") or doc.get("City"))
        st = _safe_str(doc.get("state") or doc.get("State") or "FL")
        label = addr if not city else f"{addr}, {city} {st}"
        nodes.append(
            _node(
                prop_id,
                NodeType.property,
                label[:100],
                subtitle="Address on file (not third-party deed verification)",
                risk="low",
                metadata={"verified_deed": False},
            )
        )
        edges.append(
            _edge(root_id, prop_id, EdgeRelation.same_address, label="Address on file", confidence=0.85)
        )

    booking = _safe_str(doc.get("booking_number") or doc.get("Booking_Number"))
    name_for_links = subj_name

    # Active bonds linked by booking / defendant name
    try:
        bonds_col = get_collection("active_bonds")
        bond_q: Dict[str, Any] = {"$or": []}
        if booking:
            bond_q["$or"].extend(
                [{"booking_number": booking}, {"Booking_Number": booking}]
            )
        if name_for_links and len(name_for_links) >= 3:
            bond_q["$or"].append(
                {"defendant_name": {"$regex": f"^{re.escape(name_for_links)}$", "$options": "i"}}
            )
        if bond_q["$or"]:
            cursor = bonds_col.find(bond_q).limit(5)
            async for bond in cursor:
                bid = f"bond_{_stable_id(str(bond.get('_id')))}"
                status = _safe_str(bond.get("status") or bond.get("Status") or "active")
                poa = _safe_str(bond.get("poa_number") or bond.get("POA_Number"))
                surety = _safe_str(bond.get("surety_id") or bond.get("Surety_ID") or "")
                label = f"Bond {status}" + (f" · {surety.upper()}" if surety else "")
                nodes.append(
                    _node(
                        bid,
                        NodeType.bond,
                        label,
                        subtitle=f"POA {poa}" if poa else "Active bond record",
                        risk="medium" if status.lower() in ("alert", "forfeited") else "low",
                        metadata={"status": status, "poa": poa, "surety": surety},
                    )
                )
                edges.append(
                    _edge(root_id, bid, EdgeRelation.active_bond, label=f"Bond · {status}")
                )

                # Indemnitor on bond if present
                ind_blob = bond.get("indemnitor") if isinstance(bond.get("indemnitor"), dict) else {}
                ind_name = _safe_str(
                    bond.get("indemnitor_name")
                    or bond.get("Indemnitor_Name")
                    or ind_blob.get("name")
                    or ind_blob.get("full_name")
                )
                if ind_name:
                    iid = f"ind_{_stable_id(bid, ind_name)}"
                    nodes.append(
                        _node(
                            iid,
                            NodeType.indemnitor,
                            ind_name,
                            subtitle="Indemnitor on bonded case",
                            risk="low",
                        )
                    )
                    edges.append(
                        _edge(root_id, iid, EdgeRelation.indemnified_by, label="Indemnitor on bond")
                    )
    except Exception as exc:
        logger.warning("[palantir] active_bonds graph: %s", exc)
        warnings.append("Could not load active bonds for this subject.")

    # Family tree links if service/collection exists
    try:
        fam = get_collection("family_trees")
        fam_doc = None
        if booking:
            fam_doc = await fam.find_one({"booking_number": booking})
        if not fam_doc and name_for_links:
            fam_doc = await fam.find_one(
                {"defendant_name": {"$regex": f"^{re.escape(name_for_links)}$", "$options": "i"}}
            )
        if fam_doc:
            relatives = fam_doc.get("relatives") or fam_doc.get("nodes") or []
            if isinstance(relatives, list):
                for i, rel in enumerate(relatives[:8]):
                    if not isinstance(rel, dict):
                        continue
                    rname = _safe_str(rel.get("name") or rel.get("label"))
                    if not rname:
                        continue
                    rid = f"rel_{_stable_id(root_id, rname, str(i))}"
                    rel_type = _safe_str(rel.get("relationship") or rel.get("relation") or "relative")
                    nodes.append(
                        _node(
                            rid,
                            NodeType.relative,
                            rname,
                            subtitle=rel_type,
                            risk="low",
                            metadata={"relationship": rel_type},
                        )
                    )
                    edges.append(
                        _edge(
                            root_id,
                            rid,
                            EdgeRelation.family_member,
                            label=rel_type,
                            confidence=float(rel.get("confidence") or 0.7),
                        )
                    )
    except Exception as exc:
        logger.debug("[palantir] family_trees skip: %s", exc)

    # Recent paperwork packets for this person (indemnitor phone / name)
    try:
        packets = get_collection("paperwork_packets")
        pq: Dict[str, Any] = {"$or": []}
        if name_for_links:
            pq["$or"].append(
                {"defendant_name": {"$regex": f"^{re.escape(name_for_links)}$", "$options": "i"}}
            )
            pq["$or"].append(
                {"indemnitor_name": {"$regex": f"^{re.escape(name_for_links)}$", "$options": "i"}}
            )
        if booking:
            pq["$or"].append({"booking_number": booking})
        if pq["$or"]:
            async for pkt in packets.find(pq).sort("created_at", -1).limit(3):
                pkid = f"pkt_{_stable_id(str(pkt.get('packet_id') or pkt.get('_id')))}"
                status = _safe_str(pkt.get("status") or "packet")
                nodes.append(
                    _node(
                        pkid,
                        NodeType.note,
                        f"Paperwork · {status}",
                        subtitle=_safe_str(pkt.get("packet_id")),
                        risk="low",
                        metadata={"esign": pkt.get("esign_provider"), "status": status},
                    )
                )
                edges.append(
                    _edge(root_id, pkid, EdgeRelation.related_arrest, label="Paperwork packet", confidence=0.9)
                )
    except Exception as exc:
        logger.debug("[palantir] paperwork skip: %s", exc)

    if len(nodes) == 1:
        warnings.append(
            "Subject found, but few linked records. Graph will grow as bonds, "
            "indemnitors, and family data are attached in CRM."
        )

    return KnowledgeGraph(
        nodes=nodes,
        edges=edges,
        subject_id=subject_id,
        subject_found=True,
        data_mode="live",
        warnings=warnings,
        resolved_at=datetime.now(timezone.utc),
    )


# ── 1. OpenPlanter Knowledge Graph ───────────────────────────────────────────

@palantir_router.get("/health")
async def palantir_health():
    """Liveness for the Palantir API surface."""
    return {
        "ok": True,
        "module": "palantir_intel",
        "endpoints": [
            "/api/palantir/graph/{subject_id}",
            "/api/palantir/graph/resolve",
            "/api/palantir/situation-room/feeds",
            "/api/palantir/spectra/breach-lookup",
            "/api/palantir/dossier/generate",
        ],
        "policy": "fail_closed_no_synthetic_identity",
    }


@palantir_router.get("/graph/{subject_id}", response_model=KnowledgeGraph)
async def get_knowledge_graph(
    subject_id: str,
    subject_type: str = Query("defendant"),
):
    """
    Build a knowledge graph from **live CRM records only**.
    Does not invent LLCs, deeds, or family members.
    """
    try:
        return await _build_live_graph(subject_id, subject_type)
    except Exception as exc:
        logger.exception("[palantir] graph failed")
        raise HTTPException(status_code=500, detail=f"graph_failed: {exc}") from exc


@palantir_router.post("/graph/resolve", response_model=KnowledgeGraph)
async def resolve_entity_graph(req: GraphResolveRequest):
    """Resolve graph by explicit target name/id (typed body)."""
    return await get_knowledge_graph(
        subject_id=req.target.strip(),
        subject_type=(req.subject_type or "defendant").strip().lower(),
    )


# ── 2. OSIRIS Situation Room feeds ───────────────────────────────────────────

@palantir_router.get("/situation-room/feeds", response_model=List[ThreatFeedItem])
async def get_situation_room_feeds(county: Optional[str] = None):
    """
    Operational context feeds for SWFL.

    - Live: recent hot arrests from Mongo (when available), framed as operational intel
    - Open-data style map markers are labeled demo=True when not from our DB
    """
    now_iso = _now_iso()
    feeds: List[ThreatFeedItem] = []

    # Live: recent high-score arrests (no invented charges)
    try:
        arrests = get_collection("arrests")
        q: Dict[str, Any] = {}
        if county and county.strip():
            q["$or"] = [
                {"County": {"$regex": re.escape(county.strip()), "$options": "i"}},
                {"county": {"$regex": re.escape(county.strip()), "$options": "i"}},
            ]
        # Prefer scored hot leads if field exists
        cursor = (
            arrests.find(q)
            .sort([("Lead_Score", -1), ("scraped_at", -1), ("_id", -1)])
            .limit(8)
        )
        async for a in cursor:
            score = a.get("Lead_Score") or a.get("lead_score") or 0
            try:
                score_f = float(score)
            except (TypeError, ValueError):
                score_f = 0.0
            name = _safe_str(a.get("Name") or a.get("name") or "Arrest lead")
            cty = _safe_str(a.get("County") or a.get("county") or county or "FL")
            booking = _safe_str(a.get("Booking_Number") or a.get("booking_number"))
            severity = "danger" if score_f >= 80 else "warning" if score_f >= 50 else "info"
            # Approximate pin near SWFL unless county-specific coords known
            lat, lng = _SWFL_LAT, _SWFL_LNG
            county_l = cty.lower()
            if "collier" in county_l:
                lat, lng = 26.1420, -81.7948
            elif "charlotte" in county_l:
                lat, lng = 26.9342, -82.0454
            elif "sarasota" in county_l:
                lat, lng = 27.3364, -82.5307
            feeds.append(
                ThreatFeedItem(
                    id=f"arrest_{_stable_id(booking or name, cty)}",
                    category="arrest",
                    title=f"{cty} · Lead score {int(score_f) if score_f else '—'}",
                    description=(
                        f"Arrest intelligence on file"
                        + (f" · booking {booking}" if booking else "")
                        + f" · subject ref {name[:40]}"
                    ),
                    lat=lat,
                    lng=lng,
                    severity=severity,
                    timestamp=now_iso,
                    source="ShamrockLeads arrests",
                    demo=False,
                )
            )
    except Exception as exc:
        logger.warning("[palantir] arrest feeds: %s", exc)

    # Map frame helpers — clearly demo (public reference points, not incidents)
    feeds.extend(
        [
            ThreatFeedItem(
                id="ref_lee_courthouse",
                category="court",
                title="Lee County Justice Center (map reference)",
                description="Reference pin for SWFL sector map — not a live docket alert.",
                lat=26.6444,
                lng=-81.8720,
                severity="info",
                timestamp=now_iso,
                source="Map reference",
                url="https://www.leeclerk.org",
                demo=True,
            ),
            ThreatFeedItem(
                id="ref_office",
                category="cctv",
                title="Shamrock HQ area (map reference)",
                description="Office-area map frame only — not a CCTV feed.",
                lat=26.6406,
                lng=-81.8723,
                severity="info",
                timestamp=now_iso,
                source="Map reference",
                demo=True,
            ),
        ]
    )
    return feeds


# ── 3. SPECTRA breach matrix ─────────────────────────────────────────────────

@palantir_router.post("/spectra/breach-lookup", response_model=BreachLookupResponse)
async def breach_lookup(req: BreachLookupRequest):
    """
    Executes live SPECTRA breach lookup against HIBP / licensed breach provider if key is set,
    or checks MongoDB Atlas stored OSINT scans and public OSINT registries.
    Never invents synthetic or fake breach data.
    """
    import os
    import httpx

    query = (req.email or req.phone or req.username or "").strip()
    if not query:
        raise HTTPException(
            status_code=422,
            detail="Provide email, phone, or username",
        )

    hibp_key = (os.getenv("HIBP_API_KEY") or "").strip()
    breach_key = (os.getenv("BREACH_API_KEY") or "").strip()
    api_key = hibp_key or breach_key

    breaches: List[BreachLookupItem] = []

    if api_key and "@" in query:
        # Live HaveIBeenPwned API v3 query
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                resp = await client.get(
                    f"https://haveibeenpwned.com/api/v3/breachedaccount/{query}?truncateResponse=false",
                    headers={"hibp-api-key": api_key, "user-agent": "ShamrockOSINT/1.0"}
                )
                if resp.status_code == 200:
                    data = resp.json()
                    for item in data:
                        breaches.append(BreachLookupItem(
                            breach_name=item.get("Name", "Unknown Breach"),
                            domain=item.get("Domain", "unknown"),
                            breach_date=item.get("BreachDate", "Unknown"),
                            compromised_data=item.get("DataClasses", []),
                            description=item.get("Description", "")[:200],
                            verified=item.get("IsVerified", True)
                        ))
                    return BreachLookupResponse(
                        query=query,
                        found=len(breaches) > 0,
                        total_breaches=len(breaches),
                        breaches=breaches,
                        risk_impact="high" if len(breaches) > 3 else ("medium" if breaches else "low"),
                        data_mode="live",
                        message=f"Discovered {len(breaches)} verified HIBP breach records for {query}."
                    )
        except Exception as exc:
            logger.warning("HIBP API query error for %s: %s", query, exc)

    # Check MongoDB stored OSINT scans for matching target
    db = get_db()
    if db:
        try:
            osint_col = get_collection("osint_scans")
            if osint_col:
                saved = await osint_col.find_one({"$or": [{"target": query}, {"email": query}, {"phone": query}]})
                if saved and saved.get("breaches"):
                    for b in saved["breaches"]:
                        breaches.append(BreachLookupItem(
                            breach_name=b.get("name", "OSINT Dump"),
                            domain=b.get("domain", "osint"),
                            breach_date=b.get("date", "Stored"),
                            compromised_data=b.get("fields", ["account_data"]),
                            description=b.get("notes", "OSINT scanner hit"),
                            verified=True
                        ))
                    return BreachLookupResponse(
                        query=query,
                        found=True,
                        total_breaches=len(breaches),
                        breaches=breaches,
                        risk_impact="medium",
                        data_mode="live",
                        message=f"Discovered {len(breaches)} verified stored OSINT breach records."
                    )
        except Exception as exc:
            logger.warning("Mongo OSINT search exception: %s", exc)

    return BreachLookupResponse(
        query=query,
        found=False,
        total_breaches=0,
        breaches=[],
        risk_impact="low",
        data_mode="live",
        message="No verified breach records found for this query." if api_key else "No breach hits found in database (set HIBP_API_KEY for live global breach search)."
    )


# ── 4. Executive dossier ─────────────────────────────────────────────────────

@palantir_router.post("/dossier/generate", response_model=PalantirDossier)
async def generate_dossier(req: DossierGenerateRequest):
    """
    Fuse **live** graph data into a short executive summary.
    Risk score is only set when the subject resolves in Mongo.
    """
    graph = await _build_live_graph(req.subject_id, req.subject_type)
    now_iso = _now_iso()
    dossier_id = f"dos_{secrets.token_hex(6)}"

    if not graph.subject_found:
        return PalantirDossier(
            dossier_id=dossier_id,
            subject_id=req.subject_id,
            subject_name=req.subject_id,
            subject_found=False,
            generated_at=now_iso,
            risk_score=None,
            summary=(
                f"No CRM record found for “{req.subject_id}”. "
                "Dossier not generated — system refuses to invent findings."
            ),
            key_findings=[],
            graph_summary={"total_nodes": 0, "total_edges": 0, "verified_links": 0},
            breach_summary={"breaches_found": 0, "status": "not_run"},
            threat_proximity=[],
            recommendation="Resolve the subject in Lead Explorer / defendants first, then regenerate.",
            data_mode="empty",
            warnings=graph.warnings,
        )

    root = next((n for n in graph.nodes if n.id.startswith("subj_")), None)
    subj_name = root.label if root else req.subject_id

    findings: List[str] = []
    for n in graph.nodes:
        if n.id.startswith("subj_"):
            continue
        if n.type == NodeType.bond:
            findings.append(f"Bond record: {n.label}" + (f" — {n.subtitle}" if n.subtitle else ""))
        elif n.type == NodeType.indemnitor:
            findings.append(f"Indemnitor linked: {n.label}")
        elif n.type == NodeType.relative:
            findings.append(f"Relative on file: {n.label}" + (f" ({n.subtitle})" if n.subtitle else ""))
        elif n.type == NodeType.property:
            findings.append(f"Address on file: {n.label}")
        elif n.type == NodeType.phone:
            findings.append(f"Phone on file: {n.label}")
        elif n.type == NodeType.note:
            findings.append(f"Paperwork: {n.label}")

    if not findings:
        findings.append("Subject resolved in CRM with limited linked entities.")

    # Conservative score: base on linked risk signals only
    score = 40
    for n in graph.nodes:
        if n.type == NodeType.bond and n.risk_level in ("medium", "high"):
            score += 15
        if n.type == NodeType.bond:
            score += 10
        if n.type == NodeType.indemnitor:
            score += 5
    score = max(0, min(100, score))

    rec = (
        "Continue standard underwriting: confirm POA inventory, indemnitor ID, "
        "and bond amount before DocuSeal send."
    )
    if score >= 70:
        rec = (
            "Elevated linkage density — verify indemnitor capacity, "
            "review active bond status history, require manager review if forfeitures present."
        )

    return PalantirDossier(
        dossier_id=dossier_id,
        subject_id=req.subject_id,
        subject_name=subj_name,
        subject_found=True,
        generated_at=now_iso,
        risk_score=score,
        summary=(
            f"Live CRM dossier for {subj_name}. "
            f"{len(graph.nodes)} graph nodes and {len(graph.edges)} verified links "
            f"from Shamrock MongoDB (no synthetic property/LLC invent)."
        ),
        key_findings=findings[:12],
        graph_summary={
            "total_nodes": len(graph.nodes),
            "total_edges": len(graph.edges),
            "verified_links": sum(1 for e in graph.edges if e.verified),
            "data_mode": graph.data_mode,
        },
        breach_summary={
            "breaches_found": 0,
            "status": "provider_not_configured",
        },
        threat_proximity=[],
        recommendation=rec,
        data_mode="live",
        warnings=list(graph.warnings),
    )
