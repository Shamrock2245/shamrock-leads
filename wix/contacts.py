"""
Wix CRM Contacts API — Contact Management & Labeling
=====================================================
Create, query, and label contacts in the Wix CRM from ShamrockLeads.

Endpoints (Wix Contacts v4):
    POST   /contacts/v4/contacts               — Create contact
    GET    /contacts/v4/contacts/{id}           — Get contact
    PATCH  /contacts/v4/contacts/{id}           — Update contact
    DELETE /contacts/v4/contacts/{id}           — Delete contact
    POST   /contacts/v4/contacts/query          — Query contacts
    POST   /contacts/v4/contacts/{id}/labels    — Label contact
    POST   /contacts/v4/labels                  — Create/find label
    POST   /contacts/v4/labels/query            — Query labels

Contact Types for Shamrock:
    - defendant       — Arrested individual (lead source)
    - indemnitor      — Co-signer responsible for premium
    - family-contact  — OSINT-discovered family member
    - repeat-client   — Previous Shamrock customer
    - attorney        — Legal counsel reference

Usage:
    from wix.contacts import WixContactsClient
    crm = WixContactsClient()

    # Create a contact from a lead
    crm.create_contact(
        first_name="Jane",
        last_name="Doe",
        email="jane@email.com",
        phone="+12395551234",
        labels=["indemnitor", "hot-lead"],
    )

    # Query by label
    contacts = crm.query(label_keys=["hot-lead"])
"""

import logging
from typing import Optional, Dict, Any, List

from wix.client import WixClient, WixAPIError

logger = logging.getLogger("wix.contacts")

# ── Shamrock Contact Labels ────────────────────────────────────────────────────
SHAMROCK_LABELS = {
    "defendant": "Defendant",
    "indemnitor": "Indemnitor",
    "family-contact": "Family Contact",
    "repeat-client": "Repeat Client",
    "attorney": "Attorney",
    "hot-lead": "Hot Lead",
    "warm-lead": "Warm Lead",
    "cold-lead": "Cold Lead",
    "active-case": "Active Case",
    "payment-plan": "Payment Plan",
    "do-not-bond": "Do Not Bond",
    "do-not-contact": "Do Not Contact",
    "vip": "VIP",
}


class WixContactsClient:
    """
    Client for the Wix CRM Contacts REST API v4.

    Manages contacts, labels, and segmentation for bail bond
    defendants, indemnitors, and discovered family contacts.
    """

    BASE_PATH = "/contacts/v4"

    def __init__(self, client: Optional[WixClient] = None):
        self.client = client or WixClient()
        self._label_cache: Dict[str, str] = {}  # key -> id mapping

    # ── Contact CRUD ────────────────────────────────────────────────────────────

    def create_contact(
        self,
        first_name: str,
        last_name: str,
        email: Optional[str] = None,
        phone: Optional[str] = None,
        labels: Optional[List[str]] = None,
        company: Optional[str] = None,
        address: Optional[Dict[str, str]] = None,
        custom_fields: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Create a new contact in the Wix CRM.

        Args:
            first_name: Contact's first name
            last_name: Contact's last name
            email: Email address (optional)
            phone: Phone number in E.164 format (optional)
            labels: List of label keys to apply (optional)
            company: Company name (optional)
            address: Address dict with street, city, state, zip (optional)
            custom_fields: Custom extended fields (optional)

        Returns:
            The created contact object
        """
        info: Dict[str, Any] = {
            "name": {
                "first": first_name,
                "last": last_name,
            },
        }

        if email:
            info["emails"] = {"items": [{"email": email, "primary": True}]}
        if phone:
            info["phones"] = {"items": [{"phone": phone, "primary": True}]}
        if company:
            info["company"] = company
        if address:
            info["addresses"] = {"items": [{
                "streetAddress": {"value": address.get("street", "")},
                "city": address.get("city", ""),
                "subdivision": address.get("state", "FL"),
                "postalCode": address.get("zip", ""),
                "country": "US",
            }]}
        if custom_fields:
            info["extendedFields"] = {"items": custom_fields}

        payload: Dict[str, Any] = {"info": info}
        if labels:
            # Ensure labels exist first
            label_keys = []
            for label_key in labels:
                self._ensure_label(label_key)
                label_keys.append(label_key)
            payload["info"]["labelKeys"] = {"items": label_keys}

        result = self.client.post(f"{self.BASE_PATH}/contacts", json=payload)
        contact = result.get("contact", result)
        contact_id = contact.get("id", "unknown")
        logger.info(f"✅ Created contact: {first_name} {last_name} (ID: {contact_id})")
        return contact

    def get_contact(self, contact_id: str) -> Dict[str, Any]:
        """Get a contact by ID."""
        result = self.client.get(f"{self.BASE_PATH}/contacts/{contact_id}")
        return result.get("contact", result)

    def update_contact(
        self,
        contact_id: str,
        revision: int,
        updates: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Update an existing contact (partial update).

        Args:
            contact_id: Contact's unique ID
            revision: Current revision number (for optimistic concurrency)
            updates: Fields to update in the contact info

        Returns:
            Updated contact object
        """
        payload = {
            "info": updates,
            "revision": revision,
        }
        result = self.client.patch(
            f"{self.BASE_PATH}/contacts/{contact_id}",
            json=payload,
        )
        logger.info(f"✅ Updated contact {contact_id}")
        return result.get("contact", result)

    def delete_contact(self, contact_id: str) -> Dict[str, Any]:
        """Delete a contact by ID."""
        result = self.client.delete(f"{self.BASE_PATH}/contacts/{contact_id}")
        logger.info(f"🗑️  Deleted contact {contact_id}")
        return result

    # ── Query ───────────────────────────────────────────────────────────────────

    def query(
        self,
        filter: Optional[Dict] = None,
        label_keys: Optional[List[str]] = None,
        search: Optional[str] = None,
        sort: Optional[List[Dict]] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """
        Query contacts with filters, labels, or full-text search.

        Args:
            filter: Wix-format filter dict
            label_keys: Filter by label keys (AND logic)
            search: Full-text search string
            sort: Sort specs
            limit: Max items per page
            offset: Pagination offset

        Returns:
            Dict with "contacts" list and "totalCount"
        """
        payload: Dict[str, Any] = {
            "query": {
                "paging": {
                    "limit": min(limit, 100),
                    "offset": offset,
                },
            },
        }

        # Build composite filter
        filters = []
        if filter:
            filters.append(filter)
        if label_keys:
            for key in label_keys:
                filters.append({
                    "info.labelKeys.items": {"$hasSome": [key]}
                })

        if len(filters) == 1:
            payload["query"]["filter"] = filters[0]
        elif len(filters) > 1:
            payload["query"]["filter"] = {"$and": filters}

        if sort:
            payload["query"]["sort"] = sort
        if search:
            payload["search"] = {"expression": search}

        result = self.client.post(f"{self.BASE_PATH}/contacts/query", json=payload)
        contacts = result.get("contacts", [])
        total = result.get("pagingMetadata", {}).get("total", len(contacts))

        return {
            "contacts": contacts,
            "totalCount": total,
            "hasMore": offset + len(contacts) < total,
        }

    def find_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        """Find a contact by email address (dedup check)."""
        result = self.query(
            filter={"info.emails.items.email": {"$eq": email}},
            limit=1,
        )
        contacts = result["contacts"]
        return contacts[0] if contacts else None

    def find_by_phone(self, phone: str) -> Optional[Dict[str, Any]]:
        """Find a contact by phone number (dedup check)."""
        result = self.query(
            filter={"info.phones.items.phone": {"$eq": phone}},
            limit=1,
        )
        contacts = result["contacts"]
        return contacts[0] if contacts else None

    # ── Labels ──────────────────────────────────────────────────────────────────

    def _ensure_label(self, label_key: str) -> str:
        """Create a label if it doesn't exist. Returns label key."""
        if label_key in self._label_cache:
            return self._label_cache[label_key]

        display_name = SHAMROCK_LABELS.get(label_key, label_key.replace("-", " ").title())

        try:
            result = self.client.post(
                f"{self.BASE_PATH}/labels",
                json={
                    "label": {
                        "key": label_key,
                        "displayName": display_name,
                        "labelType": "USER_DEFINED",
                    },
                },
            )
            self._label_cache[label_key] = label_key
            logger.debug(f"Label ensured: {label_key} ({display_name})")
        except WixAPIError as e:
            # Label may already exist — that's fine
            if e.status_code == 409 or "already exists" in str(e.message).lower():
                self._label_cache[label_key] = label_key
            else:
                raise

        return label_key

    def add_labels(self, contact_id: str, label_keys: List[str]) -> Dict[str, Any]:
        """
        Add labels to an existing contact.

        Args:
            contact_id: Contact's unique ID
            label_keys: Label keys to add

        Returns:
            Updated contact
        """
        # Ensure all labels exist
        for key in label_keys:
            self._ensure_label(key)

        result = self.client.post(
            f"{self.BASE_PATH}/contacts/{contact_id}/labels",
            json={"labelKeys": label_keys},
        )
        logger.info(f"🏷️  Added labels {label_keys} to contact {contact_id}")
        return result.get("contact", result)

    def remove_labels(self, contact_id: str, label_keys: List[str]) -> Dict[str, Any]:
        """Remove labels from a contact."""
        result = self.client.post(
            f"{self.BASE_PATH}/contacts/{contact_id}/labels/remove",
            json={"labelKeys": label_keys},
        )
        logger.info(f"🏷️  Removed labels {label_keys} from contact {contact_id}")
        return result.get("contact", result)

    def list_labels(self) -> List[Dict[str, Any]]:
        """Get all labels defined on the site."""
        result = self.client.post(
            f"{self.BASE_PATH}/labels/query",
            json={"query": {"paging": {"limit": 100, "offset": 0}}},
        )
        return result.get("labels", [])

    # ── Convenience Methods ─────────────────────────────────────────────────────

    def upsert_contact(
        self,
        email: Optional[str] = None,
        phone: Optional[str] = None,
        first_name: str = "",
        last_name: str = "",
        labels: Optional[List[str]] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Find existing contact by email/phone or create new one.
        Dedup-safe: prevents duplicate contacts.

        Returns:
            Existing or newly created contact
        """
        existing = None
        if email:
            existing = self.find_by_email(email)
        if not existing and phone:
            existing = self.find_by_phone(phone)

        if existing:
            contact_id = existing.get("id")
            # Add new labels if provided
            if labels and contact_id:
                try:
                    self.add_labels(contact_id, labels)
                except WixAPIError:
                    pass  # Non-critical
            logger.info(f"♻️  Found existing contact: {contact_id}")
            return existing

        return self.create_contact(
            first_name=first_name,
            last_name=last_name,
            email=email,
            phone=phone,
            labels=labels,
            **kwargs,
        )
