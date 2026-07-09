"""
ShamrockLeads — Gmail Reader Service
=====================================
Reads court scheduling emails from admin@shamrockbailbonds.biz via
the Gmail API (google-api-python-client). Uses OAuth2 refresh tokens
so no interactive login is needed in production.

Zero third-party paid services — just Google's free API tier.

Usage:
    from dashboard.services.gmail_reader import GmailReaderService
    reader = GmailReaderService()
    emails = reader.fetch_unread_court_emails()
"""

import os
import re
import logging
import base64
import email
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)


class GmailReaderService:
    """
    Production Gmail reader using google-api-python-client.
    Authenticates via OAuth2 refresh token stored in env vars.
    """

    # Clerk domains we monitor for court-related emails
    COURT_DOMAINS = [
        "leeclerk.org", "collierclerk.com", "hendryso.org",
        "charlotteclerk.com", "manateeclerk.com", "sarasotaclerk.com",
        "desotoclerk.com", "hillsboroughclerk.com", "circuit20.org",
        "ca.cjis20.org", "jud12.flcourts.org", "jud20.flcourts.org",
        "shamrockbailbonds.biz"
    ]

    # Keywords that signal court-related emails
    COURT_KEYWORDS = [
        "court document", "case number", "notice of hearing",
        "forfeiture", "discharge", "bond", "arraignment",
        "first appearance", "court date", "service of court",
        "notice of appearance", "subpoena", "summons",
        "criminal bonds", "clerk set"
    ]

    def __init__(self):
        self._service = None
        self._client_id = os.getenv("GOOGLE_CLIENT_ID", "")
        self._client_secret = os.getenv("GOOGLE_CLIENT_SECRET", "")
        self._refresh_token = os.getenv("GOOGLE_GMAIL_REFRESH_TOKEN", "")

    @property
    def is_configured(self) -> bool:
        """Check if Gmail OAuth credentials are present."""
        return bool(self._client_id and self._client_secret and self._refresh_token)

    def authenticate(self):
        """Build Gmail API service from refresh token."""
        if self._service:
            return self._service

        if not self.is_configured:
            logger.warning("[GmailReader] Gmail OAuth not configured — missing env vars")
            return None

        try:
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build

            # readonly + modify (mark read) + send (court date notices to clients)
            creds = Credentials(
                token=None,
                refresh_token=self._refresh_token,
                token_uri="https://oauth2.googleapis.com/token",
                client_id=self._client_id,
                client_secret=self._client_secret,
                scopes=[
                    "https://www.googleapis.com/auth/gmail.readonly",
                    "https://www.googleapis.com/auth/gmail.modify",
                    "https://www.googleapis.com/auth/gmail.send",
                ],
            )

            self._service = build("gmail", "v1", credentials=creds)
            logger.info("[GmailReader] ✅ Gmail API authenticated")
            return self._service

        except Exception as e:
            logger.error("[GmailReader] Authentication failed: %s", e)
            return None

    def _build_search_query(self, since_hours: int = 1) -> str:
        """
        Build a Gmail search query for court-related emails.
        Uses 'newer_than' for time window + sender domain filtering.
        """
        # Time window
        time_filter = f"newer_than:{since_hours}h"

        # Domain filters — OR'd together
        domain_clauses = " OR ".join(
            f"from:@{domain}" for domain in self.COURT_DOMAINS
        )

        # Also catch emails with court keywords in case sender domain
        # isn't in our whitelist (some counties use generic .gov domains)
        keyword_clauses = " OR ".join(
            f'subject:"{kw}"' for kw in [
                "court document", "forfeiture", "discharge",
                "notice of hearing", "case number",
            ]
        )

        return f"is:unread ({time_filter}) ({domain_clauses} OR {keyword_clauses})"

    def fetch_unread_court_emails(self, since_hours: int = 1) -> List[Dict[str, Any]]:
        """
        Fetch unread court-related emails from the last N hours.

        Returns a list of dicts with:
            - message_id: Gmail message ID
            - subject: Email subject
            - sender: Sender email address
            - body: Plain text body
            - received_at: ISO timestamp
            - labels: Gmail labels
        """
        service = self.authenticate()
        if not service:
            return []

        try:
            query = self._build_search_query(since_hours)
            logger.info("[GmailReader] Searching: %s", query[:120])

            results = service.users().messages().list(
                userId="me",
                q=query,
                maxResults=50,
            ).execute()

            messages = results.get("messages", [])
            if not messages:
                logger.info("[GmailReader] No new court emails found")
                return []

            logger.info("[GmailReader] Found %d candidate emails", len(messages))

            parsed_emails = []
            for msg_stub in messages:
                try:
                    email_data = self._get_message_detail(service, msg_stub["id"])
                    if email_data:
                        parsed_emails.append(email_data)
                except Exception as e:
                    logger.warning(
                        "[GmailReader] Failed to parse message %s: %s",
                        msg_stub["id"], e,
                    )

            return parsed_emails

        except Exception as e:
            logger.error("[GmailReader] fetch_unread_court_emails failed: %s", e)
            return []

    def _get_message_detail(self, service, message_id: str) -> Optional[Dict[str, Any]]:
        """Fetch full message details and extract text body."""
        msg = service.users().messages().get(
            userId="me",
            id=message_id,
            format="full",
        ).execute()

        headers = {h["name"].lower(): h["value"] for h in msg["payload"]["headers"]}

        subject = headers.get("subject", "(no subject)")
        sender = headers.get("from", "")
        date_str = headers.get("date", "")

        # Parse received date
        received_at = None
        try:
            # Gmail date format varies, use email.utils for robust parsing
            from email.utils import parsedate_to_datetime
            received_at = parsedate_to_datetime(date_str).isoformat()
        except Exception:
            received_at = datetime.now(timezone.utc).isoformat()

        # Extract body
        body = self._extract_body(msg["payload"])

        return {
            "message_id": message_id,
            "subject": subject,
            "sender": sender,
            "body": body,
            "received_at": received_at,
            "labels": msg.get("labelIds", []),
        }

    def _extract_body(self, payload: Dict) -> str:
        """Recursively extract plain text body from MIME parts."""
        # Direct body
        if payload.get("body", {}).get("data"):
            return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="replace")

        # Multipart — recurse
        parts = payload.get("parts", [])
        for part in parts:
            mime_type = part.get("mimeType", "")
            if mime_type == "text/plain":
                data = part.get("body", {}).get("data", "")
                if data:
                    return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")

        # Fallback: try any text part
        for part in parts:
            body = self._extract_body(part)
            if body:
                return body

        return ""

    def mark_as_read(self, message_id: str) -> bool:
        """Mark a message as read (remove UNREAD label).

        NOTE: Requires gmail.modify scope. If the OAuth token was authorized
        with gmail.readonly, this will silently return False instead of
        throwing a 403 on every court-email scan cycle.
        """
        service = self.authenticate()
        if not service:
            return False

        try:
            service.users().messages().modify(
                userId="me",
                id=message_id,
                body={"removeLabelIds": ["UNREAD"]},
            ).execute()
            return True
        except Exception as e:
            err_str = str(e)
            if "insufficientPermissions" in err_str or "Insufficient Permission" in err_str:
                # OAuth token only has gmail.readonly — can't modify.
                # This is expected; don't spam the logs every cycle.
                logger.debug(
                    "[GmailReader] Cannot mark %s as read — gmail.readonly scope (need gmail.modify)",
                    message_id,
                )
            else:
                logger.error("[GmailReader] Failed to mark %s as read: %s", message_id, e)
            return False

    def send_email(
        self,
        to: str,
        subject: str,
        body_text: str,
        *,
        body_html: str | None = None,
        cc: str | None = None,
        reply_to: str | None = None,
    ) -> dict:
        """
        Send an email from admin@shamrockbailbonds.biz via Gmail API.

        Requires gmail.send scope on the OAuth refresh token.
        Returns {"success": bool, "id": str|None, "error": str|None}.
        """
        service = self.authenticate()
        if not service:
            return {"success": False, "id": None, "error": "gmail_not_configured"}

        to = (to or "").strip()
        if not to or "@" not in to:
            return {"success": False, "id": None, "error": "invalid_recipient"}

        try:
            from email.mime.multipart import MIMEMultipart
            from email.mime.text import MIMEText

            msg = MIMEMultipart("alternative")
            msg["To"] = to
            msg["Subject"] = subject
            msg["From"] = "Shamrock Bail Bonds <admin@shamrockbailbonds.biz>"
            if cc:
                msg["Cc"] = cc
            if reply_to:
                msg["Reply-To"] = reply_to
            msg.attach(MIMEText(body_text or "", "plain", "utf-8"))
            if body_html:
                msg.attach(MIMEText(body_html, "html", "utf-8"))

            raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
            sent = (
                service.users()
                .messages()
                .send(userId="me", body={"raw": raw})
                .execute()
            )
            mid = sent.get("id")
            logger.info("[GmailReader] ✅ Sent email to %s id=%s", to.split("@")[0] + "@…", mid)
            return {"success": True, "id": mid, "error": None}
        except Exception as e:
            err_str = str(e)
            if "insufficientPermissions" in err_str or "Insufficient Permission" in err_str:
                logger.warning(
                    "[GmailReader] Cannot send — OAuth token missing gmail.send scope"
                )
                return {"success": False, "id": None, "error": "missing_gmail_send_scope"}
            logger.error("[GmailReader] send_email failed: %s", e)
            return {"success": False, "id": None, "error": err_str[:300]}

    def get_labels(self) -> List[Dict]:
        """List all Gmail labels (for debugging / label-based filtering)."""
        service = self.authenticate()
        if not service:
            return []
        try:
            results = service.users().labels().list(userId="me").execute()
            return results.get("labels", [])
        except Exception as e:
            logger.error("[GmailReader] get_labels failed: %s", e)
            return []
