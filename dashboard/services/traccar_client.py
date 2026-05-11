"""
ShamrockLeads — Traccar GPS Intelligence Client
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Async bridge between the Traccar tracking server and ShamrockBailDB.
Handles device CRUD, real-time position polling, geofence management,
and event forwarding for the bail bond monitoring lifecycle.

Competitive Edge vs Captira/SimpleBail/Clickbail:
  ✅ Real-time continuous GPS (not SMS poll-based)
  ✅ Vehicle Watch via OBD2/GPS103 trackers
  ✅ Multi-device per defendant (phone + vehicle + ankle)
  ✅ Server-side geofencing with instant webhook alerts
  ✅ Full position trail history with reverse geocoding
  ✅ Hardware-agnostic (200+ protocols vs app-only competitors)
"""
from __future__ import annotations

import os
import logging
from datetime import datetime, timezone
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# ── Traccar Connection Config ────────────────────────────────────────────────
TRACCAR_URL = os.getenv("TRACCAR_URL", "http://traccar:8082")
TRACCAR_EMAIL = os.getenv("TRACCAR_EMAIL", "admin@shamrockbailbonds.biz")
TRACCAR_PASSWORD = os.getenv("TRACCAR_PASSWORD", "shamrock-traccar-2245")
TRACCAR_TOKEN = os.getenv("TRACCAR_TOKEN", "")  # Optional: use token auth instead

_TIMEOUT = httpx.Timeout(15.0, connect=10.0)


class TraccarClient:
    """Async client for the Traccar REST API (v6.x)."""

    def __init__(
        self,
        base_url: str = TRACCAR_URL,
        email: str = TRACCAR_EMAIL,
        password: str = TRACCAR_PASSWORD,
        token: str = TRACCAR_TOKEN,
    ):
        self.base_url = base_url.rstrip("/")
        self.email = email
        self.password = password
        self.token = token
        self._session_cookie: Optional[str] = None
        self._client: Optional[httpx.AsyncClient] = None

    # ── Connection Management ────────────────────────────────────────────────

    async def _get_client(self) -> httpx.AsyncClient:
        """Lazy-init the httpx AsyncClient with authentication."""
        if self._client is None or self._client.is_closed:
            headers = {"Accept": "application/json"}
            if self.token:
                headers["Authorization"] = f"Bearer {self.token}"
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers=headers,
                timeout=_TIMEOUT,
                follow_redirects=True,
            )
            # If no token, authenticate via session
            if not self.token:
                await self._create_session()
        return self._client

    async def _create_session(self):
        """Authenticate with email/password to get a session cookie."""
        try:
            resp = await self._client.post(
                "/api/session",
                data={"email": self.email, "password": self.password},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            resp.raise_for_status()
            self._session_cookie = resp.cookies.get("JSESSIONID")
            if self._session_cookie:
                self._client.cookies.set("JSESSIONID", self._session_cookie)
            logger.info("✅ Traccar session authenticated as %s", self.email)
            return resp.json()
        except Exception as e:
            logger.error("❌ Traccar auth failed: %s", e)
            raise

    async def close(self):
        """Close the httpx client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def health_check(self) -> dict:
        """Check Traccar server connectivity and session validity."""
        try:
            client = await self._get_client()
            resp = await client.get("/api/session")
            if resp.status_code == 200:
                data = resp.json()
                return {
                    "status": "online",
                    "user": data.get("email", ""),
                    "admin": data.get("administrator", False),
                    "version": data.get("attributes", {}).get("version", "unknown"),
                }
            return {"status": "auth_required", "code": resp.status_code}
        except httpx.ConnectError:
            return {"status": "offline", "error": "Connection refused"}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    # ── Device Management ────────────────────────────────────────────────────

    async def list_devices(self) -> list[dict]:
        """Get all registered tracking devices."""
        client = await self._get_client()
        resp = await client.get("/api/devices")
        resp.raise_for_status()
        return resp.json()

    async def get_device(self, device_id: int) -> dict:
        """Get a single device by Traccar ID."""
        client = await self._get_client()
        resp = await client.get(f"/api/devices/{device_id}")
        resp.raise_for_status()
        return resp.json()

    async def create_device(
        self,
        name: str,
        unique_id: str,
        *,
        category: str = "person",
        phone: str = "",
        attributes: dict | None = None,
    ) -> dict:
        """Register a new tracking device (defendant, vehicle, or asset).

        Args:
            name: Display name (e.g. "John Doe — Lee County")
            unique_id: Unique hardware identifier (IMEI, phone#, or booking#)
            category: Device type — "person", "car", "motorcycle", "truck", etc.
            phone: Phone number associated with the device
            attributes: Custom metadata (booking_number, county, bond_case_id, etc.)
        """
        client = await self._get_client()
        payload = {
            "name": name,
            "uniqueId": unique_id,
            "category": category,
            "phone": phone,
            "attributes": attributes or {},
        }
        resp = await client.post("/api/devices", json=payload)
        resp.raise_for_status()
        device = resp.json()
        logger.info("✅ Traccar device created: %s (ID: %s)", name, device.get("id"))
        return device

    async def update_device(self, device_id: int, updates: dict) -> dict:
        """Update device properties."""
        client = await self._get_client()
        # Fetch current state first
        current = await self.get_device(device_id)
        current.update(updates)
        resp = await client.put(f"/api/devices/{device_id}", json=current)
        resp.raise_for_status()
        return resp.json()

    async def delete_device(self, device_id: int) -> bool:
        """Remove a tracking device."""
        client = await self._get_client()
        resp = await client.delete(f"/api/devices/{device_id}")
        return resp.status_code == 204

    # ── Position Tracking ────────────────────────────────────────────────────

    async def get_positions(
        self,
        device_id: int | None = None,
        from_dt: str | None = None,
        to_dt: str | None = None,
    ) -> list[dict]:
        """Fetch positions. Without params returns latest for all devices.

        Args:
            device_id: Filter to specific device
            from_dt: ISO 8601 start time
            to_dt: ISO 8601 end time
        """
        client = await self._get_client()
        params = {}
        if device_id:
            params["deviceId"] = device_id
        if from_dt:
            params["from"] = from_dt
        if to_dt:
            params["to"] = to_dt
        resp = await client.get("/api/positions", params=params)
        resp.raise_for_status()
        return resp.json()

    async def get_latest_positions(self) -> list[dict]:
        """Get the most recent position for every registered device."""
        return await self.get_positions()

    # ── Geofence Management ──────────────────────────────────────────────────

    async def list_geofences(self) -> list[dict]:
        """Get all configured geofences."""
        client = await self._get_client()
        resp = await client.get("/api/geofences")
        resp.raise_for_status()
        return resp.json()

    async def create_geofence(
        self,
        name: str,
        area: str,
        *,
        description: str = "",
        attributes: dict | None = None,
    ) -> dict:
        """Create a geofence.

        Args:
            name: Display name (e.g. "Lee County — Home Confinement")
            area: WKT geometry string. Examples:
                Circle:  "CIRCLE (26.6406 -81.8723, 500)"  (lat, lng, radius_meters)
                Polygon: "POLYGON ((...))"
            description: Human description
            attributes: Custom fields (zone_type: inclusion/exclusion, booking_number, etc.)
        """
        client = await self._get_client()
        payload = {
            "name": name,
            "area": area,
            "description": description,
            "attributes": attributes or {},
        }
        resp = await client.post("/api/geofences", json=payload)
        resp.raise_for_status()
        fence = resp.json()
        logger.info("✅ Geofence created: %s (ID: %s)", name, fence.get("id"))
        return fence

    async def update_geofence(self, fence_id: int, updates: dict) -> dict:
        """Update a geofence."""
        client = await self._get_client()
        # Fetch + merge
        resp = await client.get(f"/api/geofences")
        resp.raise_for_status()
        fences = resp.json()
        current = next((f for f in fences if f["id"] == fence_id), None)
        if not current:
            raise ValueError(f"Geofence {fence_id} not found")
        current.update(updates)
        resp = await client.put(f"/api/geofences/{fence_id}", json=current)
        resp.raise_for_status()
        return resp.json()

    async def delete_geofence(self, fence_id: int) -> bool:
        """Delete a geofence."""
        client = await self._get_client()
        resp = await client.delete(f"/api/geofences/{fence_id}")
        return resp.status_code == 204

    async def link_device_geofence(self, device_id: int, geofence_id: int) -> bool:
        """Link a device to a geofence (triggers enter/exit events)."""
        client = await self._get_client()
        resp = await client.post(
            "/api/permissions",
            json={"deviceId": device_id, "geofenceId": geofence_id},
        )
        return resp.status_code == 204

    async def unlink_device_geofence(self, device_id: int, geofence_id: int) -> bool:
        """Unlink a device from a geofence."""
        client = await self._get_client()
        resp = await client.request(
            "DELETE",
            "/api/permissions",
            json={"deviceId": device_id, "geofenceId": geofence_id},
        )
        return resp.status_code == 204

    # ── Event & Alert Retrieval ──────────────────────────────────────────────

    async def get_events(
        self,
        device_id: int,
        from_dt: str,
        to_dt: str,
        event_types: list[str] | None = None,
    ) -> list[dict]:
        """Fetch events for a device within a time range.

        Event types: geofenceEnter, geofenceExit, deviceMoving, deviceStopped,
                     deviceOnline, deviceOffline, alarm, ignitionOn/Off, etc.
        """
        client = await self._get_client()
        params = {"deviceId": device_id, "from": from_dt, "to": to_dt}
        if event_types:
            params["type"] = event_types
        resp = await client.get("/api/reports/events", params=params)
        resp.raise_for_status()
        return resp.json()

    async def get_route(
        self,
        device_id: int,
        from_dt: str,
        to_dt: str,
    ) -> list[dict]:
        """Get full position trail (route) for a device between two timestamps."""
        client = await self._get_client()
        params = {"deviceId": device_id, "from": from_dt, "to": to_dt}
        resp = await client.get("/api/reports/route", params=params)
        resp.raise_for_status()
        return resp.json()

    # ── Notification Configuration ───────────────────────────────────────────

    async def list_notifications(self) -> list[dict]:
        """Get all configured notifications."""
        client = await self._get_client()
        resp = await client.get("/api/notifications")
        resp.raise_for_status()
        return resp.json()

    async def create_notification(
        self,
        event_type: str,
        *,
        notificators: str = "web",
        always: bool = True,
        attributes: dict | None = None,
    ) -> dict:
        """Create a notification rule.

        Args:
            event_type: e.g. "geofenceEnter", "geofenceExit", "deviceMoving"
            notificators: Comma-separated: "web", "firebase", "mail"
            always: If True, applies to all devices
        """
        client = await self._get_client()
        payload = {
            "type": event_type,
            "notificators": notificators,
            "always": always,
            "attributes": attributes or {},
        }
        resp = await client.post("/api/notifications", json=payload)
        resp.raise_for_status()
        return resp.json()

    # ── Computed Positions (for reports) ──────────────────────────────────────

    async def get_summary(
        self,
        device_id: int,
        from_dt: str,
        to_dt: str,
    ) -> list[dict]:
        """Get summary statistics for a device (distance, engine hours, etc)."""
        client = await self._get_client()
        params = {"deviceId": device_id, "from": from_dt, "to": to_dt}
        resp = await client.get("/api/reports/summary", params=params)
        resp.raise_for_status()
        return resp.json()


# ── Singleton Instance ───────────────────────────────────────────────────────

_instance: TraccarClient | None = None


def get_traccar_client() -> TraccarClient:
    """Get or create the singleton TraccarClient."""
    global _instance
    if _instance is None:
        _instance = TraccarClient()
    return _instance
