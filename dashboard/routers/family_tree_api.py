"""
Family Tree & Relationship Network API Router — ShamrockLeads
============================================================
Endpoints for querying and updating 1st/2nd degree family graphs,
linking relatives, soft-deleting links, and exporting visual trees.
"""
from __future__ import annotations

import logging
from fastapi import APIRouter, HTTPException, Query, Request

from dashboard.auth.pin_middleware import get_session_from_request
from dashboard.models.family_tree import AddRelationshipRequest
from dashboard.services.family_tree_service import get_family_tree_service

log = logging.getLogger("shamrock.family_tree_api")

router = APIRouter(prefix="/api/family-tree", tags=["family-tree"])


def _actor(request: Request) -> str:
    sess = get_session_from_request(request)
    if not sess:
        return "dashboard"
    return sess.get("email") or sess.get("role") or "admin"


@router.get("/graph/{person_id_or_name:path}", summary="Get 1st/2nd degree family tree graph")
async def get_family_graph(
    person_id_or_name: str,
    request: Request,
    degree: int = Query(1, ge=1, le=2, description="Degree limit: 1 or 2"),
):
    """Retrieve family graph nodes formatted for relatives-tree renderer."""
    svc = get_family_tree_service()
    try:
        graph = await svc.get_family_graph(person_id_or_name=person_id_or_name, max_degree=degree)
        return graph
    except Exception as exc:
        log.error("Failed to fetch family graph for %s: %s", person_id_or_name, exc)
        raise HTTPException(status_code=500, detail=f"Failed to fetch graph: {exc}") from exc


@router.get("/relationships/{person_id_or_name:path}", summary="List relationship records for a person")
async def list_relationships(
    person_id_or_name: str,
    request: Request,
    limit: int = Query(100, ge=1, le=500),
):
    svc = get_family_tree_service()
    try:
        rows = await svc.list_relationships(person_id_or_name, limit=limit)
        return {"success": True, "count": len(rows), "relationships": rows}
    except Exception as exc:
        log.error("Failed to list relationships for %s: %s", person_id_or_name, exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/relationship", summary="Link two persons as relatives")
async def add_relationship(body: AddRelationshipRequest, request: Request):
    """Add a parent, child, sibling, spouse, or relative link."""
    svc = get_family_tree_service()
    try:
        rel_id = await svc.add_relationship(body, actor=_actor(request))
        return {"success": True, "ok": True, "relationship_id": rel_id}
    except Exception as exc:
        log.error("Failed to add relationship: %s", exc)
        raise HTTPException(status_code=500, detail=f"Failed to add relationship: {exc}") from exc


@router.delete("/relationship/{rel_id}", summary="Soft-delete a relationship link")
async def delete_relationship(rel_id: str, request: Request):
    """Soft-delete a relationship record, preserving audit logs."""
    svc = get_family_tree_service()
    success = await svc.soft_delete_relationship(rel_id)
    if not success:
        raise HTTPException(status_code=404, detail=f"Relationship {rel_id} not found.")
    return {"success": True, "ok": True, "relationship_id": rel_id}
