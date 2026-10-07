"""
Versioned surety form templates.

Published versions are immutable. OSI and Palmetto v1 are seeded from the
historical appearance-bond recipes and the on-disk blanks. Staff drafts live
in a process store plus an optional JSON file under data/surety_onboarding/.
Mongo is used when MONGODB_URI is set and SURETY_TEMPLATE_STORE is not
"memory". Tests force the memory store.

Outside dev/test, publish refuses to succeed unless that Mongo insert works.
A version that exists only in process memory or in the container JSON file
is not a published template.

Tenant fields (aligned with the SaaS foundation's tenant_id slug and
owner_tenant_id): owner_tenant_id null means platform-owned. entitled_tenant_ids
lists which tenants may use a platform template. A set owner_tenant_id is an
agency-private template, visible only to that tenant. The default tenant is
shamrock. resolve_active_published_template(surety, tenant) is the lookup.

DocuSeal template ids: a published version may store one. Resolution still
prefers DOCUSEAL_TEMPLATE_ID_* env vars for registry sureties, so production
OSI=1 and Palmetto=5 keep working when those env vars are set. Seeded
versions leave docuseal_template_id empty on purpose.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from dashboard.services.surety_canonical import is_canonical_id, suggest_canonical, validate_publish

# Same slug as dashboard/tenancy on the SaaS branch: tenant_id = "shamrock".
DEFAULT_TENANT_ID = "shamrock"
_TENANT_SLUG = re.compile(r"^[a-z][a-z0-9_]{1,48}$")

_LOCK = threading.RLock()
_DRAFTS: Dict[str, Dict[str, Any]] = {}
_PUBLISHED: Dict[str, List[Dict[str, Any]]] = {}
_FILES: Dict[str, bytes] = {}
_AUDIT: List[Dict[str, Any]] = []
_STORAGE_ROOT: Optional[Path] = None
_MEMORY_ONLY = os.getenv("SURETY_TEMPLATE_STORE", "").strip().lower() == "memory"
_LOADED = False
_MONGO_OVERRIDE: Any = None
VERSIONS_COLLECTION = "surety_template_versions"
FILES_COLLECTION = "surety_template_files"

_OSI_PREFIXES = (
    {"prefix": "OSI3", "max_bond_amount": 3000},
    {"prefix": "OSI6", "max_bond_amount": 6000},
    {"prefix": "OSI16", "max_bond_amount": 16000},
    {"prefix": "OSI51", "max_bond_amount": 51000},
    {"prefix": "OSI101", "max_bond_amount": 101000},
    {"prefix": "OSI251", "max_bond_amount": 251000},
)
_PALMETTO_PREFIXES = (
    {"prefix": "PSC5", "max_bond_amount": 5000},
    {"prefix": "PSC15", "max_bond_amount": 15000},
    {"prefix": "PSC25", "max_bond_amount": 25000},
    {"prefix": "PSC50", "max_bond_amount": 50000},
    {"prefix": "PSC75", "max_bond_amount": 75000},
    {"prefix": "PSC105", "max_bond_amount": 105000},
    {"prefix": "PSC200", "max_bond_amount": 200000},
    {"prefix": "PSC250", "max_bond_amount": 250000},
)


def _map(pdf_field: str, canonical: str) -> Dict[str, Any]:
    return {
        "name": pdf_field,
        "page": 0,
        "rect": None,
        "type": "Text",
        "canonical": canonical,
        "suggestion": canonical,
        "suggestion_confidence": 1.0,
    }


def _seed_version(
    *,
    surety_id: str,
    label: str,
    profile: str,
    filename: str,
    repo_path: str,
    mappings: List[Dict[str, Any]],
    prefixes: tuple,
    drive_folder_label: str,
) -> Dict[str, Any]:
    form_id = f"seed-{surety_id}-appearance"
    return {
        "version_id": f"seed-{surety_id}-v1",
        "surety_id": surety_id,
        "label": label,
        "version": 1,
        "status": "published",
        "immutable": True,
        "migration_seed": True,
        "value_profile": profile,
        "docuseal_template_id": "",
        "docuseal_field_mode": "canonical_prefill",
        "drive_folder_label": drive_folder_label,
        "settings": {
            "poa_prefixes": [dict(row) for row in prefixes],
            "repeat_per_charge": {"enabled": True, "form_id": form_id},
        },
        "forms": [
            {
                "form_id": form_id,
                "filename": filename,
                "repo_path": repo_path,
                "sha256": "",
                "kind": "acroform",
                "role": "repeat_per_charge",
                "storage_path": "",
                "fields": mappings,
                "placed_fields": [],
                "signatures": [],
                "dates": [],
            }
        ],
        "published_at": "2026-10-07T00:00:00+00:00",
        "published_by": "migration",
        "created_at": "2026-10-07T00:00:00+00:00",
        # Platform catalog. Shamrock is entitled; other tenants are not until listed.
        "owner_tenant_id": None,
        "entitled_tenant_ids": [DEFAULT_TENANT_ID],
    }


def _osi_seed() -> Dict[str, Any]:
    # PDF field names match bond_pdf_service.build_osi_field_values, including
    # keys the blank may not contain. That keeps filled output unchanged.
    mappings = [
        _map("DefLastName", "defendant.last_name"),
        _map("DefFirstName", "defendant.first_name"),
        _map("DefCounty", "defendant.county"),
        _map("DefCourtType", "court.type"),
        _map("BondAmountCharge1", "charge.bond_amount"),
        _map("DefCharge1", "charge.description"),
        _map("DefCharge1Line2", "charge.description_line2"),
        _map("CourtDate", "court.date"),
        _map("CourtTime", "court.time"),
        _map("CaseNum", "case.number"),
        _map("Arrest/case No", "booking.number"),
        _map("DefAddress", "defendant.address"),
        _map("DayDD", "bond.execution_day"),
        _map("Month", "bond.execution_month"),
        _map("YearYY", "bond.execution_year_yy"),
        _map("PowerNum", "charge.poa_number"),
        _map("WrittenPremiumAmount", "bond.premium_words"),
        _map("NumericPremiumAmount", "bond.premium_amount"),
        _map("BondAgentName", "agent.name"),
        _map("BondAgentLicenseNum", "agent.license"),
        _map("AgencyDetails", "agency.details"),
        _map("IndNameandDefName", "indemnitor.with_defendant"),
    ]
    return _seed_version(
        surety_id="osi",
        label="OSI (O'Shaughnahill Surety & Insurance)",
        profile="legacy_osi_appearance",
        filename="Appearance Bond blank.pdf",
        repo_path="osi/Appearance Bond blank.pdf",
        mappings=mappings,
        prefixes=_OSI_PREFIXES,
        drive_folder_label="OSI Appearance Bonds",
    )


def _palmetto_seed() -> Dict[str, Any]:
    # Widget names on the Palmetto appearance blank. ``chargestField1`` is the
    # carrier's printed name. Both agent lines share ``AgentField``. The blank
    # has no case-number widget; case.number stays mapped so publish validation
    # still requires it, and the writer does not emit CaseNumberField.
    mappings = [
        _map("defendantNameField", "defendant.full_name"),
        _map("countyField", "defendant.county"),
        _map("numericBondAmount", "charge.bond_amount"),
        _map("chargestField1", "charge.description"),
        _map("chargesField2", "charge.description_line2"),
        _map("CourtDateAndTimeField", "court.datetime"),
        _map("ArrestNumberField", "booking.number"),
        _map("CaseNumberField", "case.number"),
        _map("DefendantAddress", "defendant.address"),
        _map("powerNumField", "charge.poa_number"),
        _map("dayField", "bond.execution_day"),
        _map("monthWrittenField", "bond.execution_month"),
        _map("yearYYYYField", "bond.execution_year"),
        _map("cirCoField", "court.type"),
        _map("agentBailLicNumField", "agent.license"),
        _map("AgentField", "agent.name"),
        _map("writtenPremiumAmountField", "bond.premium_words"),
        _map("calculatedPremiumField", "bond.premium_amount"),
        _map("CollateralField", "collateral.description"),
        _map("AgencyField", "agency.name"),
    ]
    return _seed_version(
        surety_id="palmetto",
        label="Palmetto Surety Corporation",
        profile="legacy_palmetto_appearance",
        filename="Shamrock Palmetto Official Appearance Bond.pdf",
        repo_path="palmetto/Shamrock Palmetto Official Appearance Bond.pdf",
        mappings=mappings,
        prefixes=_PALMETTO_PREFIXES,
        drive_folder_label="Palmetto Appearance Bonds",
    )


SEEDS: Dict[str, Dict[str, Any]] = {
    "osi": _osi_seed(),
    "palmetto": _palmetto_seed(),
}


class SuretyTemplateError(ValueError):
    def __init__(self, message: str, code: str = "surety_template_error"):
        self.code = code
        super().__init__(message)


def reset_for_tests(storage_root: Optional[Path] = None) -> None:
    """Drop staff drafts. Seeded OSI/Palmetto v1 stay."""
    global _STORAGE_ROOT, _MEMORY_ONLY, _LOADED, _MONGO_OVERRIDE
    with _LOCK:
        _DRAFTS.clear()
        _PUBLISHED.clear()
        _FILES.clear()
        _AUDIT.clear()
        _LOADED = False
        _MONGO_OVERRIDE = None
        _MEMORY_ONLY = True
        _STORAGE_ROOT = Path(storage_root) if storage_root else None
        if _STORAGE_ROOT:
            _STORAGE_ROOT.mkdir(parents=True, exist_ok=True)


def install_mongo_for_tests(database: Any) -> None:
    """Point the store at a fake or local database. Does not open a network client."""
    global _MONGO_OVERRIDE, _MEMORY_ONLY, _LOADED
    with _LOCK:
        _MONGO_OVERRIDE = database
        _MEMORY_ONLY = False
        _LOADED = False


def reload_from_durable() -> None:
    """Drop process memory and read Mongo, as a fresh container would."""
    global _LOADED
    with _LOCK:
        _DRAFTS.clear()
        _PUBLISHED.clear()
        _FILES.clear()
        _LOADED = False
    ensure_loaded()


def publish_audit_log() -> List[Dict[str, Any]]:
    return [dict(row) for row in _AUDIT]


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _default_root() -> Path:
    if _STORAGE_ROOT:
        return _STORAGE_ROOT
    return Path(__file__).resolve().parents[2] / "data" / "surety_onboarding"


def _state_path() -> Path:
    return _default_root() / "versions.json"


def _dev_or_test_store() -> bool:
    """Local JSON / memory is enough only for dev and test.

    Production (any other ENV, including unset) must insert into Mongo.
    """
    if _MEMORY_ONLY:
        return True
    if os.getenv("SURETY_TEMPLATE_STORE", "").strip().lower() == "memory":
        return True
    env = (os.getenv("ENV") or os.getenv("APP_ENV") or "").strip().lower()
    return env in {"test", "dev", "development", "local"}


def _use_mongo() -> bool:
    if _MONGO_OVERRIDE is not None:
        return True
    if _MEMORY_ONLY:
        return False
    if os.getenv("SURETY_TEMPLATE_STORE", "").strip().lower() == "memory":
        return False
    return bool((os.getenv("MONGODB_URI") or "").strip())


def _mongo_database():
    if _MONGO_OVERRIDE is not None:
        return _MONGO_OVERRIDE
    if not _use_mongo():
        return None
    try:
        from pymongo import MongoClient
    except Exception:
        return None
    uri = os.getenv("MONGODB_URI") or ""
    db_name = os.getenv("MONGODB_DB_NAME") or "ShamrockBailDB"
    try:
        client = MongoClient(uri, serverSelectionTimeoutMS=1500)
        return client[db_name]
    except Exception:
        return None


def _mongo_col():
    db = _mongo_database()
    if db is None:
        return None
    try:
        return db[VERSIONS_COLLECTION]
    except Exception:
        return None


def _as_bytes(raw: Any) -> bytes:
    if isinstance(raw, bytes):
        return raw
    if raw is None:
        return b""
    try:
        return bytes(raw)
    except Exception:
        return b""


def _load_from_mongo(db) -> None:
    for doc in db[VERSIONS_COLLECTION].find({}):
        if not isinstance(doc, Mapping):
            continue
        clean = {k: v for k, v in dict(doc).items() if k != "_id"}
        vid = str(clean.get("version_id") or "").strip()
        if not vid:
            continue
        if clean.get("status") == "draft":
            _DRAFTS[vid] = clean
        elif clean.get("status") == "published":
            sid = str(clean.get("surety_id") or "").strip().lower()
            if sid:
                _PUBLISHED.setdefault(sid, []).append(clean)
    for row in db[FILES_COLLECTION].find({}):
        if not isinstance(row, Mapping):
            continue
        form_id = str(row.get("form_id") or "").strip()
        blob = _as_bytes(row.get("pdf"))
        if form_id and blob:
            _FILES[form_id] = blob


def _upsert_version(doc: Mapping[str, Any]) -> None:
    db = _mongo_database()
    if db is None:
        return
    payload = json.loads(json.dumps(doc, default=str))
    payload.pop("_id", None)
    db[VERSIONS_COLLECTION].replace_one(
        {"version_id": payload.get("version_id")},
        payload,
        upsert=True,
    )


def _upsert_file(form_id: str, version_id: str, filename: str, pdf_bytes: bytes) -> None:
    db = _mongo_database()
    if db is None:
        if not _dev_or_test_store():
            raise SuretyTemplateError(
                "Upload refused: durable template storage is unavailable. "
                "Set MONGODB_URI. Uploaded PDFs are not kept on container disk.",
                code="durable_storage_unavailable",
            )
        return
    db[FILES_COLLECTION].replace_one(
        {"form_id": form_id},
        {
            "form_id": form_id,
            "version_id": version_id,
            "filename": filename,
            "sha256": hashlib.sha256(pdf_bytes).hexdigest(),
            "pdf": pdf_bytes,
        },
        upsert=True,
    )


def _persist_local() -> None:
    """Dev/test JSON cache only. Production is read-only and has no data volume."""
    if not _dev_or_test_store():
        return
    if _MEMORY_ONLY and _STORAGE_ROOT is None:
        return
    root = _default_root()
    root.mkdir(parents=True, exist_ok=True)
    payload = {"drafts": _DRAFTS, "published": _PUBLISHED}
    _state_path().write_text(json.dumps(payload), encoding="utf-8")


def _load_local() -> None:
    path = _state_path()
    if not path.exists():
        return
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if isinstance(payload.get("drafts"), dict):
        _DRAFTS.update(payload["drafts"])
    if isinstance(payload.get("published"), dict):
        _PUBLISHED.update(payload["published"])


def ensure_loaded() -> None:
    """Load drafts, published versions, and PDF bytes.

    Mongo is the durable store. The local JSON file is a dev/test cache.
    A production process does not require /app/data.
    """
    global _LOADED
    with _LOCK:
        if _LOADED:
            return
        _LOADED = True
        db = _mongo_database()
        if db is not None:
            try:
                _load_from_mongo(db)
            except SuretyTemplateError:
                raise
            except Exception as exc:
                if not _dev_or_test_store():
                    raise SuretyTemplateError(
                        "Template store could not be read from MongoDB.",
                        code="durable_storage_unavailable",
                    ) from exc
        elif not _dev_or_test_store():
            return
        if _dev_or_test_store():
            _load_local()


def _public_version(doc: Mapping[str, Any]) -> Dict[str, Any]:
    """Staff view. PDF bytes and on-disk paths stay off the response."""
    out = json.loads(json.dumps(doc, default=str))
    for form in out.get("forms") or []:
        if isinstance(form, dict):
            form.pop("storage_path", None)
    check = validate_publish(out)
    out["publish_check"] = {
        "ok": check["ok"],
        "missing": check["missing"],
        "warnings": check["warnings"],
        "required": check["required"],
    }
    return out


def seed_version(surety_id: str) -> Optional[Dict[str, Any]]:
    doc = SEEDS.get(str(surety_id or "").strip().lower())
    return json.loads(json.dumps(doc)) if doc else None


def published_versions(surety_id: str) -> List[Dict[str, Any]]:
    ensure_loaded()
    sid = str(surety_id or "").strip().lower()
    rows: List[Dict[str, Any]] = []
    seed = seed_version(sid)
    if seed:
        rows.append(seed)
    with _LOCK:
        for doc in _PUBLISHED.get(sid) or []:
            rows.append(json.loads(json.dumps(doc)))
    rows.sort(key=lambda d: int(d.get("version") or 0))
    return rows


def normalize_tenant_id(raw: object, *, default: str = DEFAULT_TENANT_ID) -> str:
    """Tenant slug. Blank becomes the default. Anything else must match ^[a-z][a-z0-9_]{1,48}$."""
    text = str(raw or "").strip().lower() or default
    if not _TENANT_SLUG.fullmatch(text):
        raise SuretyTemplateError(
            "tenant id must be a lowercase slug.",
            code="invalid_tenant",
        )
    return text


def _owner_tenant_id(doc: Mapping[str, Any]) -> Optional[str]:
    """Null owner is platform-owned. Accept owner_tenant as an alias of owner_tenant_id."""
    raw = doc.get("owner_tenant_id", doc.get("owner_tenant"))
    if raw in (None, "", "null"):
        return None
    return normalize_tenant_id(raw)


def _entitled_tenant_ids(doc: Mapping[str, Any]) -> List[str]:
    """Who may use a platform template. A missing list means shamrock only."""
    raw = doc.get("entitled_tenant_ids")
    if raw is None:
        raw = doc.get("entitlements")
    if not isinstance(raw, list):
        return [DEFAULT_TENANT_ID]
    out = []
    for item in raw:
        slug = str(item or "").strip().lower()
        if not slug:
            continue
        if not _TENANT_SLUG.fullmatch(slug):
            raise SuretyTemplateError(
                "entitled tenant id must be a lowercase slug.",
                code="invalid_tenant",
            )
        if slug not in out:
            out.append(slug)
    return out


def _version_visible_to(doc: Mapping[str, Any], tenant_id: str) -> bool:
    if doc.get("status") != "published":
        return False
    owner = _owner_tenant_id(doc)
    if owner:
        return owner == tenant_id
    return tenant_id in _entitled_tenant_ids(doc)


def resolve_active_published_template(
    surety_id: str,
    tenant_id: str = DEFAULT_TENANT_ID,
) -> Optional[Dict[str, Any]]:
    """Active published template for (surety, tenant).

    An agency-private version (owner_tenant_id == tenant) wins over the
    platform catalog. A platform version (owner_tenant_id null) is returned
    only when tenant_id is in entitled_tenant_ids. Shamrock's seeded OSI and
    Palmetto versions resolve here. Another tenant does not see them until
    it is listed, or until it has its own private version.
    """
    tenant = normalize_tenant_id(tenant_id)
    visible = [
        row for row in published_versions(surety_id)
        if _version_visible_to(row, tenant)
    ]
    if not visible:
        return None
    private = [row for row in visible if _owner_tenant_id(row) == tenant]
    pool = private or visible
    pool.sort(key=lambda row: int(row.get("version") or 0))
    return pool[-1]


def active_published(surety_id: str) -> Optional[Dict[str, Any]]:
    """Newest published version the default tenant (shamrock) may use."""
    return resolve_active_published_template(surety_id, DEFAULT_TENANT_ID)


def has_published_version(surety_id: str) -> bool:
    return active_published(surety_id) is not None


def published_docuseal_template_id(surety_id: str) -> Optional[str]:
    doc = active_published(surety_id)
    if not doc:
        return None
    tid = str(doc.get("docuseal_template_id") or "").strip()
    return tid or None


def published_drive_label(surety_id: str) -> Optional[str]:
    doc = active_published(surety_id)
    if not doc:
        return None
    label = str(doc.get("drive_folder_label") or "").strip()
    return label or None


def published_poa_prefixes(surety_id: str) -> List[Dict[str, Any]]:
    doc = active_published(surety_id)
    if not doc:
        return []
    settings = doc.get("settings") if isinstance(doc.get("settings"), dict) else {}
    rows = settings.get("poa_prefixes") or []
    return [dict(r) for r in rows if isinstance(r, dict)]


def list_onboarding() -> List[Dict[str, Any]]:
    ensure_loaded()
    from dashboard.services.surety_registry import SURETY_REGISTRY

    seen = []
    ids = list(SURETY_REGISTRY.keys())
    with _LOCK:
        extra = list(_PUBLISHED.keys()) + [d.get("surety_id") for d in _DRAFTS.values()]
    for sid in extra:
        if sid and sid not in ids:
            ids.append(sid)
    rows = []
    for sid in ids:
        if sid in seen:
            continue
        seen.append(sid)
        meta = SURETY_REGISTRY.get(sid) or {}
        active = active_published(sid)
        rows.append({
            "surety_id": sid,
            "label": (active or {}).get("label") or meta.get("label") or sid,
            "registry_active": bool(meta.get("active")),
            "published_version": (active or {}).get("version"),
            "version_id": (active or {}).get("version_id"),
            "value_profile": (active or {}).get("value_profile") or "",
            "docuseal_template_id": (active or {}).get("docuseal_template_id") or "",
            "docuseal_field_mode": (active or {}).get("docuseal_field_mode") or "",
            "poa_prefixes": published_poa_prefixes(sid),
            "migration_seed": bool((active or {}).get("migration_seed")),
        })
    return rows


def get_version(version_id: str, *, include_storage: bool = False) -> Optional[Dict[str, Any]]:
    """Public view hides on-disk paths. Preview passes include_storage=True."""
    ensure_loaded()
    vid = str(version_id or "").strip()
    found = None
    for seed in SEEDS.values():
        if seed["version_id"] == vid:
            found = seed
            break
    if found is None:
        with _LOCK:
            if vid in _DRAFTS:
                found = _DRAFTS[vid]
            else:
                for rows in _PUBLISHED.values():
                    for doc in rows:
                        if doc.get("version_id") == vid:
                            found = doc
                            break
    if found is None:
        return None
    if include_storage:
        out = json.loads(json.dumps(found, default=str))
        out["publish_check"] = validate_publish(out)
        return out
    return _public_version(found)


def _clean_surety_id(raw: object) -> str:
    sid = str(raw or "").strip().lower()
    if not sid or not all(ch.isalnum() or ch == "_" for ch in sid) or not sid[0].isalpha():
        raise SuretyTemplateError(
            "surety_id must be a lowercase letter followed by letters, numbers, or underscores.",
            code="invalid_surety_id",
        )
    if len(sid) > 32:
        raise SuretyTemplateError("surety_id is too long.", code="invalid_surety_id")
    return sid


def _clean_prefixes(raw: object) -> List[Dict[str, Any]]:
    if raw in (None, "", []):
        return []
    if not isinstance(raw, list):
        raise SuretyTemplateError("poa_prefixes must be a list.", code="invalid_poa_prefix")
    out = []
    for row in raw:
        if not isinstance(row, Mapping):
            raise SuretyTemplateError("Each POA prefix needs prefix and max_bond_amount.", code="invalid_poa_prefix")
        prefix = str(row.get("prefix") or "").strip().upper()
        if not prefix or not prefix.isalnum() or len(prefix) > 12:
            raise SuretyTemplateError(f"Invalid POA prefix '{prefix}'.", code="invalid_poa_prefix")
        try:
            max_amount = int(row.get("max_bond_amount"))
        except (TypeError, ValueError):
            raise SuretyTemplateError(f"POA prefix {prefix} needs a max bond amount.", code="invalid_poa_prefix")
        if max_amount <= 0:
            raise SuretyTemplateError(f"POA prefix {prefix} max bond must be positive.", code="invalid_poa_prefix")
        out.append({"prefix": prefix, "max_bond_amount": max_amount})
    return out


def _clean_rect(raw: object) -> Optional[List[float]]:
    if raw in (None, "", []):
        return None
    if not isinstance(raw, (list, tuple)) or len(raw) != 4:
        raise SuretyTemplateError("A field rect is [x0, y0, x1, y1].", code="invalid_rect")
    try:
        rect = [float(v) for v in raw]
    except (TypeError, ValueError):
        raise SuretyTemplateError("A field rect must be four numbers.", code="invalid_rect")
    if rect[2] <= rect[0] or rect[3] <= rect[1]:
        raise SuretyTemplateError("A field rect must have positive width and height.", code="invalid_rect")
    return rect


def _clean_owner_tenant_id(raw: object) -> Optional[str]:
    if raw in (None, "", "null"):
        return None
    return normalize_tenant_id(raw)


def _clean_entitled_tenant_ids(raw: object, *, default_shamrock: bool) -> List[str]:
    if raw is None and default_shamrock:
        return [DEFAULT_TENANT_ID]
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise SuretyTemplateError(
            "entitled_tenant_ids must be a list of tenant slugs.",
            code="invalid_tenant",
        )
    return _entitled_tenant_ids({"entitled_tenant_ids": raw})


def create_draft(
    *,
    surety_id: str,
    label: str,
    poa_prefixes: Optional[list] = None,
    repeat_per_charge: bool = True,
    docuseal_template_id: str = "",
    drive_folder_label: str = "",
    owner_tenant_id: object = None,
    entitled_tenant_ids: Optional[list] = None,
) -> Dict[str, Any]:
    ensure_loaded()
    sid = _clean_surety_id(surety_id)
    title = str(label or "").strip()
    if not title:
        raise SuretyTemplateError("A display label is required.", code="label_required")
    prefixes = _clean_prefixes(poa_prefixes)
    doc_id = str(uuid.uuid4())
    form_placeholder = ""
    doc = {
        "version_id": doc_id,
        "surety_id": sid,
        "label": title[:120],
        "version": None,
        "status": "draft",
        "immutable": False,
        "migration_seed": False,
        "value_profile": "fail_closed",
        "docuseal_template_id": str(docuseal_template_id or "").strip(),
        "docuseal_field_mode": "mapped",
        "drive_folder_label": str(drive_folder_label or "").strip() or f"{title} Appearance Bonds",
        "settings": {
            "poa_prefixes": prefixes,
            "repeat_per_charge": {"enabled": bool(repeat_per_charge), "form_id": form_placeholder},
        },
        "forms": [],
        "created_at": _now(),
        "published_at": None,
        "published_by": None,
        "owner_tenant_id": _clean_owner_tenant_id(owner_tenant_id),
        "entitled_tenant_ids": _clean_entitled_tenant_ids(
            entitled_tenant_ids, default_shamrock=True
        ),
    }
    with _LOCK:
        _DRAFTS[doc_id] = doc
        _persist_local()
        if _use_mongo():
            _upsert_version(doc)
    return _public_version(doc)


def _require_draft(version_id: str) -> Dict[str, Any]:
    ensure_loaded()
    doc = _DRAFTS.get(str(version_id or "").strip())
    if not doc:
        raise SuretyTemplateError("Draft not found.", code="draft_not_found")
    if doc.get("status") != "draft" or doc.get("immutable"):
        raise SuretyTemplateError("Published versions are immutable.", code="immutable_version")
    return doc


def add_form(version_id: str, filename: str, pdf_bytes: bytes) -> Dict[str, Any]:
    from dashboard.services.surety_form_inspect import inspect_pdf

    if not pdf_bytes or pdf_bytes[:4] != b"%PDF":
        raise SuretyTemplateError("Upload a PDF.", code="pdf_required")
    if len(pdf_bytes) > 8 * 1024 * 1024:
        raise SuretyTemplateError("PDF exceeds 8 MB.", code="pdf_too_large")
    with _LOCK:
        doc = _require_draft(version_id)
        if len(doc.get("forms") or []) >= 12:
            raise SuretyTemplateError("A draft can hold 12 PDFs.", code="too_many_forms")
        inspected = inspect_pdf(pdf_bytes)
        form_id = str(uuid.uuid4())
        safe_name = Path(filename or "form.pdf").name.replace("\x00", "")[:180] or "form.pdf"
        _FILES[form_id] = pdf_bytes
        _upsert_file(form_id, doc["version_id"], safe_name, pdf_bytes)
        storage_path = ""
        if _dev_or_test_store() and not (_MEMORY_ONLY and _STORAGE_ROOT is None):
            root = _default_root()
            form_dir = root / "forms" / doc["version_id"]
            form_dir.mkdir(parents=True, exist_ok=True)
            path = form_dir / f"{form_id}.pdf"
            path.write_bytes(pdf_bytes)
            storage_path = str(path)
        role = "repeat_per_charge" if not doc["forms"] else "static"
        form = {
            "form_id": form_id,
            "filename": safe_name,
            "repo_path": "",
            "sha256": hashlib.sha256(pdf_bytes).hexdigest(),
            "kind": inspected["kind"],
            "role": role,
            "storage": "mongo" if _use_mongo() else "memory",
            "storage_path": storage_path,
            "page_count": inspected["page_count"],
            "fields": inspected["fields"],
            "placed_fields": [],
            "signatures": inspected["signatures"],
            "dates": [],
        }
        doc["forms"].append(form)
        repeat = doc["settings"]["repeat_per_charge"]
        if repeat.get("enabled") and not repeat.get("form_id"):
            repeat["form_id"] = form_id
        _persist_local()
        if _use_mongo():
            _upsert_version(doc)
        return _public_version(doc)


def update_draft(version_id: str, patch: Mapping[str, Any]) -> Dict[str, Any]:
    with _LOCK:
        doc = _require_draft(version_id)
        if "label" in patch:
            label = str(patch.get("label") or "").strip()
            if not label:
                raise SuretyTemplateError("A display label is required.", code="label_required")
            doc["label"] = label[:120]
        if "docuseal_template_id" in patch:
            doc["docuseal_template_id"] = str(patch.get("docuseal_template_id") or "").strip()
        if "owner_tenant_id" in patch or "owner_tenant" in patch:
            raw_owner = patch.get("owner_tenant_id", patch.get("owner_tenant"))
            doc["owner_tenant_id"] = _clean_owner_tenant_id(raw_owner)
        if "entitled_tenant_ids" in patch or "entitlements" in patch:
            raw_entitled = patch.get("entitled_tenant_ids", patch.get("entitlements"))
            doc["entitled_tenant_ids"] = _clean_entitled_tenant_ids(
                raw_entitled, default_shamrock=False
            )
        if "drive_folder_label" in patch:
            doc["drive_folder_label"] = str(patch.get("drive_folder_label") or "").strip()
        if "poa_prefixes" in patch:
            doc["settings"]["poa_prefixes"] = _clean_prefixes(patch.get("poa_prefixes"))
        if "repeat_per_charge" in patch:
            raw = patch.get("repeat_per_charge") or {}
            if not isinstance(raw, Mapping):
                raise SuretyTemplateError("repeat_per_charge must be an object.", code="invalid_repeat")
            form_id = str(raw.get("form_id") or "").strip()
            known = {f["form_id"] for f in doc["forms"]}
            if form_id and form_id not in known:
                raise SuretyTemplateError("repeat form_id is not on this draft.", code="invalid_repeat")
            doc["settings"]["repeat_per_charge"] = {
                "enabled": bool(raw.get("enabled", True)),
                "form_id": form_id,
            }
            for form in doc["forms"]:
                if form_id and form["form_id"] == form_id:
                    form["role"] = "repeat_per_charge"
                elif form.get("role") == "repeat_per_charge" and form_id:
                    form["role"] = "static"
        if "forms" in patch:
            _apply_form_edits(doc, patch.get("forms") or [])
        if patch.get("use_suggestions"):
            for form in doc["forms"]:
                for field in form.get("fields") or []:
                    if not str(field.get("canonical") or "").strip() and field.get("suggestion"):
                        if is_canonical_id(field.get("suggestion")):
                            field["canonical"] = field["suggestion"]
        _persist_local()
        if _use_mongo():
            _upsert_version(doc)
        return _public_version(doc)


def _apply_form_edits(doc: Dict[str, Any], edits: object) -> None:
    if not isinstance(edits, list):
        raise SuretyTemplateError("forms must be a list.", code="invalid_forms")
    by_id = {f["form_id"]: f for f in doc["forms"]}
    for edit in edits:
        if not isinstance(edit, Mapping):
            continue
        form = by_id.get(str(edit.get("form_id") or ""))
        if not form:
            raise SuretyTemplateError("Unknown form on draft.", code="unknown_form")
        if "role" in edit:
            role = str(edit.get("role") or "")
            if role not in ("static", "repeat_per_charge"):
                raise SuretyTemplateError("role must be static or repeat_per_charge.", code="invalid_role")
            form["role"] = role
        if "fields" in edit:
            form["fields"] = _clean_detected_fields(form.get("fields") or [], edit.get("fields") or [])
        if "placed_fields" in edit:
            form["placed_fields"] = _clean_placed_fields(edit.get("placed_fields") or [])
        if "signatures" in edit:
            form["signatures"] = _clean_marks(edit.get("signatures") or [], kind="signature")
        if "dates" in edit:
            form["dates"] = _clean_marks(edit.get("dates") or [], kind="date")


def _clean_detected_fields(existing: List[Dict[str, Any]], edits: object) -> List[Dict[str, Any]]:
    if not isinstance(edits, list):
        raise SuretyTemplateError("fields must be a list.", code="invalid_fields")
    by_name = {str(f.get("name") or ""): dict(f) for f in existing}
    out = []
    for edit in edits:
        if not isinstance(edit, Mapping):
            continue
        name = str(edit.get("name") or "")
        base = dict(by_name.get(name) or {})
        if not base and name not in by_name:
            # Staff may only retarget fields the PDF actually has.
            raise SuretyTemplateError(f"Unknown PDF field '{name}'.", code="unknown_field")
        canonical = str(edit.get("canonical") or "").strip()
        if canonical and not is_canonical_id(canonical):
            raise SuretyTemplateError(f"Unknown canonical field '{canonical}'.", code="unknown_canonical")
        base["name"] = name
        base["canonical"] = canonical
        out.append(base)
    # Keep detected fields the client omitted, without a canonical mapping.
    edited_names = {str(e.get("name") or "") for e in edits if isinstance(e, Mapping)}
    for name, field in by_name.items():
        if name not in edited_names:
            field = dict(field)
            field["canonical"] = ""
            out.append(field)
    return out


def _clean_placed_fields(raw: object) -> List[Dict[str, Any]]:
    if not isinstance(raw, list):
        raise SuretyTemplateError("placed_fields must be a list.", code="invalid_fields")
    out = []
    for i, row in enumerate(raw, start=1):
        if not isinstance(row, Mapping):
            continue
        canonical = str(row.get("canonical") or "").strip()
        if not is_canonical_id(canonical):
            raise SuretyTemplateError("Each placed field needs a canonical id.", code="unknown_canonical")
        name = str(row.get("name") or f"placed_{i}").strip() or f"placed_{i}"
        if not name.replace("_", "").isalnum():
            raise SuretyTemplateError("Placed field names must be alphanumeric.", code="invalid_fields")
        try:
            page = int(row.get("page") or 0)
        except (TypeError, ValueError):
            raise SuretyTemplateError("Placed field page must be an integer.", code="invalid_fields")
        out.append({
            "name": name[:80],
            "page": page,
            "rect": _clean_rect(row.get("rect")),
            "type": "Text",
            "canonical": canonical,
        })
        if out[-1]["rect"] is None:
            raise SuretyTemplateError("A placed field needs a rect.", code="invalid_rect")
    return out


def _clean_marks(raw: object, *, kind: str) -> List[Dict[str, Any]]:
    if not isinstance(raw, list):
        raise SuretyTemplateError(f"{kind} marks must be a list.", code="invalid_mark")
    out = []
    allowed_roles = {"indemnitor", "co_indemnitor", "defendant", "bondsman"}
    for row in raw:
        if not isinstance(row, Mapping):
            continue
        role = str(row.get("role") or "indemnitor").strip().lower()
        if role not in allowed_roles:
            raise SuretyTemplateError("Signature role must be indemnitor, co_indemnitor, defendant, or bondsman.", code="invalid_mark")
        try:
            page = int(row.get("page") or 0)
        except (TypeError, ValueError):
            raise SuretyTemplateError("Mark page must be an integer.", code="invalid_mark")
        canonical = str(row.get("canonical") or "").strip()
        if kind == "date":
            if canonical and not is_canonical_id(canonical):
                raise SuretyTemplateError("Date mark canonical id is unknown.", code="unknown_canonical")
            if not canonical:
                canonical = "bond.execution_date"
        out.append({
            "role": role,
            "kind": kind,
            "page": page,
            "rect": _clean_rect(row.get("rect")),
            "canonical": canonical if kind == "date" else "",
        })
        if out[-1]["rect"] is None:
            raise SuretyTemplateError(f"A {kind} mark needs a rect.", code="invalid_rect")
    return out


def load_form_bytes(form: Mapping[str, Any]) -> bytes:
    repo_path = str(form.get("repo_path") or "").strip()
    if repo_path:
        from dashboard.bond_pdf_service import TEMPLATES_DIR
        path = (TEMPLATES_DIR / repo_path).resolve()
        root = TEMPLATES_DIR.resolve()
        if root not in path.parents and path != root:
            raise SuretyTemplateError("Template path escapes the templates directory.", code="bad_path")
        if not path.exists():
            raise SuretyTemplateError(f"Template PDF missing: {repo_path}", code="pdf_missing")
        return path.read_bytes()
    form_id = str(form.get("form_id") or "").strip()
    cached = _FILES.get(form_id)
    if cached:
        return cached
    db = _mongo_database()
    if db is not None and form_id:
        row = db[FILES_COLLECTION].find_one({"form_id": form_id})
        blob = _as_bytes((row or {}).get("pdf")) if isinstance(row, Mapping) else b""
        if blob:
            _FILES[form_id] = blob
            return blob
    storage = str(form.get("storage_path") or "").strip()
    if storage and _dev_or_test_store():
        path = Path(storage)
        if path.exists():
            return path.read_bytes()
    raise SuretyTemplateError("Uploaded PDF is not in durable storage.", code="pdf_missing")


def publish_draft(version_id: str, actor: str) -> Dict[str, Any]:
    with _LOCK:
        doc = _require_draft(version_id)
        if not doc.get("forms"):
            raise SuretyTemplateError("Upload at least one PDF before publishing.", code="pdf_required")
        check = validate_publish(doc)
        if not check["ok"]:
            raise SuretyTemplateError(
                "Required canonical fields are unmapped: " + ", ".join(check["missing"]),
                code="required_unmapped",
            )
        sid = doc["surety_id"]
        prior = [
            int(r.get("version") or 0)
            for r in published_versions(sid)
            if r.get("status") == "published"
        ]
        old_version = max(prior) if prior else None
        nxt = max(prior or [0]) + 1
        published = json.loads(json.dumps(doc))
        published["status"] = "published"
        published["immutable"] = True
        published["version"] = nxt
        published["published_at"] = _now()
        published["published_by"] = str(actor or "staff")[:120]
        published["publish_warnings"] = check["warnings"]
        published["owner_tenant_id"] = _clean_owner_tenant_id(doc.get("owner_tenant_id"))
        published["entitled_tenant_ids"] = _clean_entitled_tenant_ids(
            doc.get("entitled_tenant_ids"), default_shamrock=True
        )
        # Refuse before mutating the draft. A failed ping must not leave a
        # published version that exists only in memory or on container disk.
        durable = None
        if not _dev_or_test_store():
            durable = _mongo_col()
            if durable is None:
                raise SuretyTemplateError(
                    "Publish refused: durable template storage is unavailable. "
                    "Set MONGODB_URI. A published version is not kept in memory "
                    "or on container disk.",
                    code="durable_storage_unavailable",
                )
            try:
                durable.database.client.admin.command("ping")
            except Exception as exc:
                raise SuretyTemplateError(
                    "Publish refused: durable template storage is unavailable.",
                    code="durable_storage_unavailable",
                ) from exc
        for form in published.get("forms") or []:
            if not isinstance(form, dict):
                continue
            form_id = str(form.get("form_id") or "")
            if form.get("repo_path"):
                continue
            blob = _FILES.get(form_id) or b""
            if not blob and _dev_or_test_store():
                disk = str(form.get("storage_path") or "")
                if disk and Path(disk).exists():
                    blob = Path(disk).read_bytes()
                    _FILES[form_id] = blob
            if not blob:
                raise SuretyTemplateError(
                    "Publish refused: an uploaded PDF is not in durable storage.",
                    code="durable_storage_unavailable",
                )
            if not _dev_or_test_store() or _use_mongo():
                _upsert_file(form_id, published["version_id"], str(form.get("filename") or ""), blob)
            form["storage"] = "mongo" if _use_mongo() else form.get("storage") or "memory"
            if not _dev_or_test_store():
                form["storage_path"] = ""
        _PUBLISHED.setdefault(sid, []).append(published)
        _DRAFTS.pop(doc["version_id"], None)
        _persist_local()
        try:
            if durable is not None or _use_mongo():
                _upsert_version(published)
            _write_publish_audit(
                actor=str(published.get("published_by") or actor),
                surety_id=sid,
                version_id=published["version_id"],
                old_version=old_version,
                new_version=nxt,
            )
        except Exception as exc:
            _PUBLISHED[sid].pop()
            if not _PUBLISHED[sid]:
                _PUBLISHED.pop(sid, None)
            _DRAFTS[doc["version_id"]] = doc
            _persist_local()
            if durable is not None or not _dev_or_test_store():
                raise SuretyTemplateError(
                    "Publish refused: durable template storage did not accept the version.",
                    code="durable_storage_unavailable",
                ) from exc
        return _public_version(published)


def _write_publish_audit(
    *,
    actor: str,
    surety_id: str,
    version_id: str,
    old_version: Optional[int],
    new_version: int,
) -> None:
    """Immutable publish record: who published, and which version replaced which."""
    event = {
        "event_type": "surety_template_published",
        "entity_type": "surety_template",
        "entity_id": version_id,
        "action": "publish",
        "actor": str(actor or "staff")[:120],
        "actor_type": "staff",
        "surety_id": surety_id,
        "version_id": version_id,
        "old_version": old_version,
        "new_version": new_version,
        "timestamp": _now(),
    }
    _AUDIT.append(dict(event))
    db = _mongo_database()
    if db is None:
        if _dev_or_test_store():
            return
        raise SuretyTemplateError(
            "Publish refused: audit storage is unavailable.",
            code="durable_storage_unavailable",
        )
    db["audit_events"].insert_one(dict(event))


def suggest_for_unmapped(form: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """Return suggestion rows for fields that have no confirmed canonical id."""
    rows = []
    for field in form.get("fields") or []:
        if not isinstance(field, Mapping):
            continue
        if str(field.get("canonical") or "").strip():
            continue
        canonical, confidence = suggest_canonical(field.get("name"))
        if canonical:
            rows.append({
                "name": field.get("name"),
                "canonical": canonical,
                "confidence": confidence,
            })
    return rows
