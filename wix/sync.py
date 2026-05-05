"""
Wix ↔ MongoDB Sync Engine — Bidirectional Data Bridge
======================================================
Synchronizes data between ShamrockLeads MongoDB and Wix CMS collections,
and pushes lead/defendant data into Wix CRM Contacts.

Sync Flows:
    1. Intake Sync:  MongoDB intakes → Wix CMS IntakeQueue
    2. Case Sync:    MongoDB bond_cases → Wix CMS Cases
    3. Lead → CRM:   Hot leads from MongoDB → Wix CRM Contacts
    4. Status Sync:  Case status changes → Wix CMS update

Architecture:
    MongoDB (source of truth) → WixSyncEngine → Wix CMS + Wix CRM
    No reverse sync (Wix → MongoDB) to maintain single source of truth.
    GAS continues to handle complex business logic (PDF gen, SignNow).
    This engine handles DATA SYNC only.

Usage:
    from wix.sync import WixSyncEngine
    engine = WixSyncEngine(db)
    
    # One-shot sync
    await engine.sync_intakes()
    await engine.sync_cases()
    await engine.sync_hot_leads_to_crm()
    
    # Full sync
    await engine.run_full_sync()
"""

import logging
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, List

from wix.client import WixClient, WixAPIError
from wix.data import WixDataClient
from wix.contacts import WixContactsClient

logger = logging.getLogger("wix.sync")


class WixSyncEngine:
    """
    Bidirectional sync engine between ShamrockLeads MongoDB and Wix platform.

    Handles:
      - Intake queue sync (MongoDB → Wix CMS IntakeQueue)
      - Bond case sync (MongoDB → Wix CMS Cases)
      - Hot lead → CRM contact creation
      - Case status propagation
      - Sync state tracking (prevents duplicate pushes)
    """

    def __init__(self, db=None):
        """
        Initialize the sync engine.

        Args:
            db: Motor async database instance (from extensions.get_db())
        """
        self.db = db
        self.client = WixClient()
        self.cms = WixDataClient(client=self.client)
        self.crm = WixContactsClient(client=self.client)

        # Track sync state to prevent duplicate operations
        self._sync_collection = "wix_sync_log"

    @property
    def is_configured(self) -> bool:
        """Check if Wix API is configured."""
        return self.client.is_configured

    # ── Sync State Tracking ─────────────────────────────────────────────────────

    async def _get_sync_state(self, entity_type: str, entity_id: str) -> Optional[Dict]:
        """Check if an entity has been synced to Wix before."""
        if self.db is None:
            return None
        coll = self.db[self._sync_collection]
        return await coll.find_one({
            "entity_type": entity_type,
            "entity_id": str(entity_id),
        })

    async def _set_sync_state(
        self,
        entity_type: str,
        entity_id: str,
        wix_id: str,
        collection: str,
        status: str = "synced",
    ):
        """Record that an entity was synced to Wix."""
        if self.db is None:
            return
        coll = self.db[self._sync_collection]
        await coll.update_one(
            {"entity_type": entity_type, "entity_id": str(entity_id)},
            {"$set": {
                "entity_type": entity_type,
                "entity_id": str(entity_id),
                "wix_id": wix_id,
                "wix_collection": collection,
                "status": status,
                "synced_at": datetime.now(timezone.utc).isoformat(),
            }},
            upsert=True,
        )

    # ── 1. Intake Queue Sync (MongoDB → Wix CMS) ───────────────────────────────

    async def sync_intakes(self, limit: int = 50) -> Dict[str, Any]:
        """
        Sync recent intakes from MongoDB to Wix CMS IntakeQueue.

        Finds intakes not yet synced to Wix and pushes them.
        Uses wix_sync_log to track what's been sent.

        Returns:
            Summary dict with counts
        """
        if not self.is_configured:
            return {"status": "disabled", "reason": "WIX_BLOG_API_KEY not configured"}

        if self.db is None:
            return {"status": "error", "reason": "No database connection"}

        stats = {"synced": 0, "skipped": 0, "errors": 0, "total_checked": 0}

        try:
            # Get recent intakes from MongoDB
            intakes_coll = self.db["intakes"]
            cursor = intakes_coll.find({}).sort("created_at", -1).limit(limit)
            intakes = await cursor.to_list(length=limit)
            stats["total_checked"] = len(intakes)

            for intake in intakes:
                intake_id = str(intake.get("_id", ""))
                if not intake_id:
                    continue

                # Check if already synced
                sync_state = await self._get_sync_state("intake", intake_id)
                if sync_state and sync_state.get("status") == "synced":
                    stats["skipped"] += 1
                    continue

                try:
                    # Map MongoDB intake to Wix CMS fields
                    wix_data = self._map_intake_to_wix(intake)

                    # Insert into Wix CMS
                    result = await asyncio.to_thread(
                        self.cms.insert, "IntakeQueue", wix_data
                    )
                    wix_id = result.get("id", result.get("_id", ""))

                    # Record sync state
                    await self._set_sync_state("intake", intake_id, wix_id, "IntakeQueue")
                    stats["synced"] += 1

                except WixAPIError as e:
                    logger.error(f"Failed to sync intake {intake_id}: {e}")
                    stats["errors"] += 1
                except Exception as e:
                    logger.error(f"Unexpected error syncing intake {intake_id}: {e}")
                    stats["errors"] += 1

        except Exception as e:
            logger.error(f"Intake sync failed: {e}")
            return {"status": "error", "reason": str(e), **stats}

        logger.info(
            f"📥 Intake sync complete: {stats['synced']} synced, "
            f"{stats['skipped']} skipped, {stats['errors']} errors"
        )
        return {"status": "ok", **stats}

    def _map_intake_to_wix(self, intake: Dict) -> Dict:
        """Map a MongoDB intake document to Wix CMS IntakeQueue fields."""
        return {
            "defendantName": intake.get("defendant_name", ""),
            "defendantFirstName": intake.get("defendant_first_name", ""),
            "defendantLastName": intake.get("defendant_last_name", ""),
            "indemnitorName": intake.get("indemnitor_name", ""),
            "indemnitorPhone": intake.get("indemnitor_phone", ""),
            "indemnitorEmail": intake.get("indemnitor_email", ""),
            "bondAmount": intake.get("bond_amount", 0),
            "charges": intake.get("charges", ""),
            "county": intake.get("county", ""),
            "bookingNumber": intake.get("booking_number", ""),
            "jailFacility": intake.get("facility", ""),
            "relationship": intake.get("relationship", ""),
            "status": intake.get("status", "new"),
            "source": intake.get("source", "shamrock-leads"),
            "createdAt": intake.get("created_at", datetime.now(timezone.utc).isoformat()),
            "mongoId": str(intake.get("_id", "")),
        }

    # ── 2. Bond Case Sync (MongoDB → Wix CMS) ──────────────────────────────────

    async def sync_cases(self, limit: int = 50) -> Dict[str, Any]:
        """
        Sync bond cases from MongoDB to Wix CMS Cases collection.

        Returns:
            Summary dict with counts
        """
        if not self.is_configured:
            return {"status": "disabled", "reason": "WIX_BLOG_API_KEY not configured"}

        if self.db is None:
            return {"status": "error", "reason": "No database connection"}

        stats = {"synced": 0, "updated": 0, "skipped": 0, "errors": 0}

        try:
            cases_coll = self.db["bond_cases"]
            cursor = cases_coll.find({}).sort("created_at", -1).limit(limit)
            cases = await cursor.to_list(length=limit)

            for case in cases:
                case_id = str(case.get("_id", ""))
                if not case_id:
                    continue

                sync_state = await self._get_sync_state("bond_case", case_id)

                try:
                    wix_data = self._map_case_to_wix(case)

                    if sync_state and sync_state.get("wix_id"):
                        # Update existing Wix item
                        await asyncio.to_thread(
                            self.cms.patch,
                            "Cases",
                            sync_state["wix_id"],
                            wix_data,
                        )
                        stats["updated"] += 1
                    else:
                        # Insert new
                        result = await asyncio.to_thread(
                            self.cms.insert, "Cases", wix_data
                        )
                        wix_id = result.get("id", result.get("_id", ""))
                        await self._set_sync_state("bond_case", case_id, wix_id, "Cases")
                        stats["synced"] += 1

                except WixAPIError as e:
                    logger.error(f"Failed to sync case {case_id}: {e}")
                    stats["errors"] += 1

        except Exception as e:
            logger.error(f"Case sync failed: {e}")
            return {"status": "error", "reason": str(e), **stats}

        logger.info(
            f"📦 Case sync complete: {stats['synced']} new, "
            f"{stats['updated']} updated, {stats['errors']} errors"
        )
        return {"status": "ok", **stats}

    def _map_case_to_wix(self, case: Dict) -> Dict:
        """Map a MongoDB bond_case document to Wix CMS Cases fields."""
        return {
            "caseNumber": case.get("case_number", ""),
            "poaNumber": case.get("poa_number", ""),
            "suretyId": case.get("surety_id", ""),
            "defendantName": case.get("defendant_name", ""),
            "indemnitorName": case.get("indemnitor_name", ""),
            "bondAmount": case.get("bond_amount", 0),
            "premiumAmount": case.get("premium_amount", 0),
            "county": case.get("county", ""),
            "charges": case.get("charges", ""),
            "courtDate": case.get("court_date", ""),
            "status": case.get("status", "active"),
            "agentName": case.get("agent_name", "Brendan O'Neal"),
            "postedDate": case.get("posted_date", ""),
            "mongoId": str(case.get("_id", "")),
        }

    # ── 3. Hot Lead → CRM Contact Sync ──────────────────────────────────────────

    async def sync_hot_leads_to_crm(self, min_score: int = 70, limit: int = 25) -> Dict[str, Any]:
        """
        Push hot leads from the arrest scraper pipeline to Wix CRM as contacts.

        Only syncs leads with lead_score >= min_score that haven't been
        previously synced to Wix CRM.

        Returns:
            Summary dict with counts
        """
        if not self.is_configured:
            return {"status": "disabled", "reason": "WIX_BLOG_API_KEY not configured"}

        if self.db is None:
            return {"status": "error", "reason": "No database connection"}

        stats = {"created": 0, "skipped": 0, "errors": 0}

        try:
            arrests_coll = self.db["arrests"]
            query = {
                "lead_score": {"$gte": min_score},
                "lead_status": {"$in": ["Hot", "Warm"]},
            }
            cursor = arrests_coll.find(query).sort("scraped_at", -1).limit(limit)
            leads = await cursor.to_list(length=limit)

            for lead in leads:
                lead_id = str(lead.get("_id", ""))
                if not lead_id:
                    continue

                sync_state = await self._get_sync_state("lead_crm", lead_id)
                if sync_state:
                    stats["skipped"] += 1
                    continue

                try:
                    # Parse name
                    full_name = (lead.get("full_name") or lead.get("name") or "").strip()
                    if not full_name:
                        logger.debug(f"Skipping lead {lead_id}: no name available")
                        stats["skipped"] += 1
                        continue

                    parts = full_name.split(",", 1) if "," in full_name else full_name.rsplit(" ", 1)
                    if len(parts) == 2:
                        last_name = parts[0].strip()
                        first_name = parts[1].strip()
                    else:
                        first_name = full_name
                        last_name = ""

                    # Final guard — Wix CRM requires non-empty displayName
                    if not first_name:
                        first_name = full_name or "Unknown"

                    # Determine labels
                    labels = ["defendant"]
                    score = lead.get("lead_score", 0)
                    if score >= 80:
                        labels.append("hot-lead")
                    elif score >= 50:
                        labels.append("warm-lead")

                    # Create in Wix CRM (dedup-safe via upsert)
                    contact = await asyncio.to_thread(
                        self.crm.upsert_contact,
                        first_name=first_name,
                        last_name=last_name,
                        labels=labels,
                    )

                    wix_contact_id = contact.get("id", "")
                    await self._set_sync_state("lead_crm", lead_id, wix_contact_id, "contacts")
                    stats["created"] += 1

                except WixAPIError as e:
                    logger.error(f"Failed to sync lead {lead_id} to CRM: {e}")
                    stats["errors"] += 1
                except Exception as e:
                    logger.error(f"Unexpected error syncing lead {lead_id}: {e}")
                    stats["errors"] += 1

        except Exception as e:
            logger.error(f"Lead-to-CRM sync failed: {e}")
            return {"status": "error", "reason": str(e), **stats}

        logger.info(
            f"👤 Lead→CRM sync: {stats['created']} contacts created, "
            f"{stats['skipped']} skipped, {stats['errors']} errors"
        )
        return {"status": "ok", **stats}

    # ── 4. Case Status Propagation ──────────────────────────────────────────────

    async def push_case_status(
        self,
        mongo_case_id: str,
        new_status: str,
        additional_fields: Optional[Dict] = None,
    ) -> Dict[str, Any]:
        """
        Push a case status change to Wix CMS.

        Called by the bond lifecycle when a case status changes
        (active → discharged, active → forfeited, etc.)

        Args:
            mongo_case_id: MongoDB case _id
            new_status: New case status string
            additional_fields: Optional extra fields to update

        Returns:
            Sync result dict
        """
        if not self.is_configured:
            return {"status": "disabled"}

        sync_state = await self._get_sync_state("bond_case", mongo_case_id)
        if not sync_state or not sync_state.get("wix_id"):
            return {"status": "not_synced", "reason": "Case not yet in Wix CMS"}

        try:
            update_data = {"status": new_status}
            if additional_fields:
                update_data.update(additional_fields)

            await asyncio.to_thread(
                self.cms.patch,
                "Cases",
                sync_state["wix_id"],
                update_data,
            )

            await self._set_sync_state(
                "bond_case", mongo_case_id, sync_state["wix_id"], "Cases", "synced"
            )

            logger.info(f"📡 Pushed case status to Wix: {mongo_case_id} → {new_status}")
            return {"status": "ok", "wix_id": sync_state["wix_id"]}

        except WixAPIError as e:
            logger.error(f"Failed to push case status: {e}")
            return {"status": "error", "reason": str(e)}

    # ── 5. Collection Discovery ─────────────────────────────────────────────────

    async def discover_collections(self) -> List[Dict[str, Any]]:
        """
        List all Wix CMS collections on the site.
        Useful for initial setup and verification.
        """
        if not self.is_configured:
            return []

        try:
            return await asyncio.to_thread(self.cms.list_collections)
        except WixAPIError as e:
            logger.error(f"Collection discovery failed: {e}")
            return []

    # ── Full Sync Orchestrator ──────────────────────────────────────────────────

    async def run_full_sync(self) -> Dict[str, Any]:
        """
        Run all sync operations in sequence.

        Returns:
            Combined results from all sync operations
        """
        if not self.is_configured:
            return {
                "status": "disabled",
                "reason": "WIX_BLOG_API_KEY not configured",
            }

        logger.info("🔄 Starting full Wix sync...")
        results = {}

        # 1. Intake sync
        results["intakes"] = await self.sync_intakes()

        # 2. Case sync
        results["cases"] = await self.sync_cases()

        # 3. Hot leads to CRM
        results["leads_to_crm"] = await self.sync_hot_leads_to_crm()

        logger.info(f"✅ Full Wix sync complete: {results}")
        return {"status": "ok", "results": results}

    # ── Sync Status Report ──────────────────────────────────────────────────────

    async def get_sync_status(self) -> Dict[str, Any]:
        """
        Get current sync status and statistics.

        Returns:
            Dict with sync counts by type and last sync timestamps
        """
        if self.db is None:
            return {"status": "no_db"}

        coll = self.db[self._sync_collection]

        # Count by entity type
        pipeline = [
            {"$group": {
                "_id": "$entity_type",
                "count": {"$sum": 1},
                "last_sync": {"$max": "$synced_at"},
            }},
        ]
        cursor = coll.aggregate(pipeline)
        type_stats = {}
        async for doc in cursor:
            type_stats[doc["_id"]] = {
                "count": doc["count"],
                "last_sync": doc["last_sync"],
            }

        total = await coll.count_documents({})

        return {
            "status": "ok",
            "configured": self.is_configured,
            "total_synced": total,
            "by_type": type_stats,
        }
