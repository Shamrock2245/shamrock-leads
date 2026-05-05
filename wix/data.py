"""
Wix CMS Data Items API — Direct Collection CRUD
================================================
Enables direct read/write to Wix CMS collections without GAS middleman.

Endpoints (Wix Data v2):
    POST   /wix-data/v2/items             — Insert single item
    GET    /wix-data/v2/items/{id}         — Get item by ID
    PUT    /wix-data/v2/items/{id}         — Update (full replace)
    PATCH  /wix-data/v2/items/{id}         — Patch (partial update)
    DELETE /wix-data/v2/items/{id}         — Remove item
    POST   /wix-data/v2/items/query        — Query with filters
    POST   /wix-data/v2/bulk/items/insert  — Bulk insert
    POST   /wix-data/v2/bulk/items/save    — Bulk upsert
    POST   /wix-data/v2/bulk/items/update  — Bulk update
    POST   /wix-data/v2/bulk/items/remove  — Bulk delete

Key Wix CMS Collections (Shamrock Portal):
    IntakeQueue       — Indemnitor intake submissions
    Cases             — Active bond cases
    MagicLinks        — Portal magic-link auth sessions
    PendingDocuments  — Docs awaiting signature
    MemberDocuments   — Signed/completed docs
    PortalSessions    — Session tracking

Usage:
    from wix.data import WixDataClient
    cms = WixDataClient()

    # Insert into IntakeQueue
    cms.insert("IntakeQueue", {"defendantName": "John Doe", "bondAmount": 5000})

    # Query cases
    results = cms.query("Cases", filter={"status": {"$eq": "active"}})

    # Bulk upsert
    cms.bulk_save("IntakeQueue", items=[...])
"""

import logging
from typing import Optional, Dict, Any, List
from datetime import datetime, timezone

from wix.client import WixClient, WixAPIError

logger = logging.getLogger("wix.data")

# ── Known Wix CMS Collections ──────────────────────────────────────────────────
# Map logical names to actual Wix collection IDs.
# Use collection names as they appear in the Wix CMS editor.
COLLECTIONS = {
    "IntakeQueue": "IntakeQueue",
    "Cases": "Cases",
    "MagicLinks": "MagicLinks",
    "PendingDocuments": "PendingDocuments",
    "MemberDocuments": "MemberDocuments",
    "PortalSessions": "PortalSessions",
}


class WixDataClient:
    """
    Client for the Wix CMS Data Items REST API v2.

    Provides CRUD operations, querying with Wix filter syntax,
    and bulk operations for efficient batch processing.
    """

    BASE_PATH = "/wix-data/v2"

    def __init__(self, client: Optional[WixClient] = None):
        self.client = client or WixClient()

    # ── Single Item Operations ──────────────────────────────────────────────────

    def insert(
        self,
        collection: str,
        data: Dict[str, Any],
        item_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Insert a single item into a Wix CMS collection.

        Args:
            collection: Collection name (e.g., "IntakeQueue")
            data: Field key-value pairs to insert
            item_id: Optional custom ID (auto-generated if omitted)

        Returns:
            The created data item with _id, _createdDate, etc.
        """
        payload = {
            "dataCollectionId": collection,
            "dataItem": {
                "data": data,
            },
        }
        if item_id:
            payload["dataItem"]["id"] = item_id

        result = self.client.post(f"{self.BASE_PATH}/items", json=payload)
        logger.info(f"✅ Inserted item into {collection}: {result.get('dataItem', {}).get('id', 'unknown')}")
        return result.get("dataItem", result)

    def get(self, collection: str, item_id: str) -> Dict[str, Any]:
        """
        Get a single item by ID from a Wix CMS collection.

        Args:
            collection: Collection name
            item_id: The item's unique ID

        Returns:
            The data item
        """
        params = {"dataCollectionId": collection}
        result = self.client.get(f"{self.BASE_PATH}/items/{item_id}", params=params)
        return result.get("dataItem", result)

    def update(
        self,
        collection: str,
        item_id: str,
        data: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Full replace update of an item (all fields not included are lost).

        Args:
            collection: Collection name
            item_id: The item's unique ID
            data: Complete field data (replaces existing)

        Returns:
            The updated data item
        """
        payload = {
            "dataCollectionId": collection,
            "dataItem": {
                "id": item_id,
                "data": {**data, "_id": item_id},
            },
        }
        result = self.client.put(f"{self.BASE_PATH}/items/{item_id}", json=payload)
        logger.info(f"✅ Updated item {item_id} in {collection}")
        return result.get("dataItem", result)

    def patch(
        self,
        collection: str,
        item_id: str,
        data: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Partial update — only specified fields are modified, others preserved.

        Args:
            collection: Collection name
            item_id: The item's unique ID
            data: Fields to update (other fields remain unchanged)

        Returns:
            The patched data item
        """
        payload = {
            "dataCollectionId": collection,
            "dataItem": {
                "id": item_id,
                "data": {**data, "_id": item_id},
            },
        }
        result = self.client.patch(f"{self.BASE_PATH}/items/{item_id}", json=payload)
        logger.info(f"✅ Patched item {item_id} in {collection}")
        return result.get("dataItem", result)

    def remove(self, collection: str, item_id: str) -> Dict[str, Any]:
        """
        Remove a single item from a collection.

        Args:
            collection: Collection name
            item_id: The item's unique ID

        Returns:
            Confirmation dict
        """
        result = self.client.delete(
            f"{self.BASE_PATH}/items/{item_id}",
            params={"dataCollectionId": collection},
        )
        logger.info(f"🗑️  Removed item {item_id} from {collection}")
        return result

    # ── Query Operations ────────────────────────────────────────────────────────

    def query(
        self,
        collection: str,
        filter: Optional[Dict] = None,
        sort: Optional[List[Dict]] = None,
        fields: Optional[List[str]] = None,
        limit: int = 50,
        offset: int = 0,
        include_total: bool = True,
    ) -> Dict[str, Any]:
        """
        Query a Wix CMS collection with Wix filter syntax.

        Wix Filter Syntax:
            {"field": {"$eq": "value"}}
            {"field": {"$ne": "value"}}
            {"field": {"$gt": 100}}
            {"field": {"$contains": "text"}}
            {"field": {"$in": ["a", "b"]}}
            {"$and": [...filters]}
            {"$or": [...filters]}

        Args:
            collection: Collection name
            filter: Wix-format filter dict (optional)
            sort: List of sort specs [{"fieldName": "field", "order": "ASC"|"DESC"}]
            fields: List of field names to return (None = all)
            limit: Max items per page (1-1000, default 50)
            offset: Items to skip for pagination
            include_total: Whether to include total count

        Returns:
            Dict with "dataItems" list and optional "totalCount"
        """
        payload: Dict[str, Any] = {
            "dataCollectionId": collection,
            "query": {
                "paging": {
                    "limit": min(limit, 1000),
                    "offset": offset,
                },
            },
            "includeReferencedItems": [],
            "returnTotalCount": include_total,
        }

        if filter:
            payload["query"]["filter"] = filter
        if sort:
            payload["query"]["sort"] = sort
        if fields:
            payload["query"]["fields"] = fields

        result = self.client.post(f"{self.BASE_PATH}/items/query", json=payload)
        items = result.get("dataItems", [])
        total = result.get("totalCount", len(items))

        logger.debug(f"Query {collection}: {len(items)} items returned (total: {total})")
        return {
            "items": [item.get("data", item) for item in items],
            "totalCount": total,
            "hasMore": offset + len(items) < total,
        }

    def query_all(
        self,
        collection: str,
        filter: Optional[Dict] = None,
        sort: Optional[List[Dict]] = None,
        batch_size: int = 200,
    ) -> List[Dict[str, Any]]:
        """
        Query ALL items matching a filter (handles pagination automatically).

        Args:
            collection: Collection name
            filter: Wix-format filter dict (optional)
            sort: Sort specification
            batch_size: Items per request (max 1000)

        Returns:
            List of all matching items
        """
        all_items = []
        offset = 0

        while True:
            result = self.query(
                collection=collection,
                filter=filter,
                sort=sort,
                limit=batch_size,
                offset=offset,
            )
            all_items.extend(result["items"])

            if not result["hasMore"]:
                break
            offset += batch_size

        logger.info(f"Query all {collection}: {len(all_items)} total items fetched")
        return all_items

    def count(self, collection: str, filter: Optional[Dict] = None) -> int:
        """
        Count items in a collection matching an optional filter.

        Args:
            collection: Collection name
            filter: Optional filter

        Returns:
            Total count of matching items
        """
        result = self.query(collection, filter=filter, limit=1, include_total=True)
        return result["totalCount"]

    def find_one(
        self,
        collection: str,
        filter: Dict,
    ) -> Optional[Dict[str, Any]]:
        """
        Find a single item matching a filter.

        Returns:
            The first matching item, or None
        """
        result = self.query(collection, filter=filter, limit=1)
        items = result["items"]
        return items[0] if items else None

    # ── Bulk Operations ─────────────────────────────────────────────────────────

    def bulk_insert(
        self,
        collection: str,
        items: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        Insert multiple items at once (max 1000 per call).

        Args:
            collection: Collection name
            items: List of data dicts to insert

        Returns:
            Dict with "results" list containing inserted items and any errors
        """
        if len(items) > 1000:
            logger.warning(f"Bulk insert: {len(items)} items exceeds 1000 limit, batching")
            all_results = []
            for i in range(0, len(items), 1000):
                batch = items[i:i + 1000]
                result = self._do_bulk_insert(collection, batch)
                all_results.extend(result.get("results", []))
            return {"results": all_results, "totalInserted": len(all_results)}

        return self._do_bulk_insert(collection, items)

    def _do_bulk_insert(self, collection: str, items: List[Dict]) -> Dict:
        """Execute a single bulk insert call."""
        payload = {
            "dataCollectionId": collection,
            "dataItems": [{"data": item} for item in items],
        }
        result = self.client.post(f"{self.BASE_PATH}/bulk/items/insert", json=payload)
        count = len(result.get("results", []))
        logger.info(f"✅ Bulk inserted {count} items into {collection}")
        return result

    def bulk_save(
        self,
        collection: str,
        items: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        Upsert multiple items (insert if new, replace if exists).

        WARNING: Existing items are FULLY REPLACED — missing fields are lost.
        Use bulk_patch for partial updates.

        Args:
            collection: Collection name
            items: List of data dicts (include _id for updates)

        Returns:
            Dict with operation results
        """
        if len(items) > 1000:
            all_results = []
            for i in range(0, len(items), 1000):
                batch = items[i:i + 1000]
                result = self._do_bulk_save(collection, batch)
                all_results.extend(result.get("results", []))
            return {"results": all_results}

        return self._do_bulk_save(collection, items)

    def _do_bulk_save(self, collection: str, items: List[Dict]) -> Dict:
        """Execute a single bulk save call."""
        payload = {
            "dataCollectionId": collection,
            "dataItems": [{"data": item} for item in items],
        }
        result = self.client.post(f"{self.BASE_PATH}/bulk/items/save", json=payload)
        count = len(result.get("results", []))
        logger.info(f"✅ Bulk saved {count} items to {collection}")
        return result

    def bulk_remove(
        self,
        collection: str,
        item_ids: List[str],
    ) -> Dict[str, Any]:
        """
        Remove multiple items by ID.

        Args:
            collection: Collection name
            item_ids: List of item IDs to remove

        Returns:
            Dict with removal results
        """
        payload = {
            "dataCollectionId": collection,
            "dataItemIds": item_ids,
        }
        result = self.client.post(f"{self.BASE_PATH}/bulk/items/remove", json=payload)
        logger.info(f"🗑️  Bulk removed {len(item_ids)} items from {collection}")
        return result

    # ── Collection Discovery ────────────────────────────────────────────────────

    def list_collections(self) -> List[Dict[str, Any]]:
        """
        List all data collections on the Wix site.

        Returns:
            List of collection info dicts with id, displayName, fields, etc.
        """
        payload = {
            "paging": {"limit": 100, "offset": 0},
        }
        result = self.client.post("/wix-data/v2/collections/query", json=payload)
        collections = result.get("collections", [])
        logger.info(f"📋 Found {len(collections)} Wix CMS collections")
        return collections
