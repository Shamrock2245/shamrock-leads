"""
Shamrock Social Engine — Gmail Scanner for Grok Posts
======================================================
Scans admin@shamrockbailbonds.biz for emails from Grok/xAI containing
pre-written social media posts. Grok has been writing X posts for months
via email — this service finally harvests them into the social queue.

Reuses the authentication pattern from dashboard/services/gmail_reader.py
(OAuth2 refresh token via google-api-python-client).

Flow:
  1. Query Gmail for Grok-authored emails (from:noreply@x.ai, from:grok@x.ai,
     subject contains "post", "tweet", "social", "shamrock")
  2. Parse email body to extract post content
  3. Run through humanizer for AI pattern removal
  4. Enqueue each post to social_queue with source_type="grok_email"
  5. Label email as processed to avoid re-scanning
"""

from __future__ import annotations

import base64
import logging
import os
import re
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

from motor.motor_asyncio import AsyncIOMotorDatabase

from social.models import (
    SocialPost, PostStatus, Platform, SourceType,
    ContentTone, ContentVariant,
)
from social.queue_manager import QueueManager
from social.humanizer import ContentHumanizer
from social.config import settings

logger = logging.getLogger("social.gmail_scanner")

# ── Grok Email Detection Patterns ─────────────────────────────────────────────

# Senders that indicate Grok-authored content
GROK_SENDERS = [
    "noreply@x.ai",
    "grok@x.ai",
    "notifications@x.ai",
    "noreply@xai.com",
    "grok@xai.com",
]

# Subject line patterns that suggest social media post content
GROK_SUBJECT_PATTERNS = [
    r"(?i)post",
    r"(?i)tweet",
    r"(?i)social",
    r"(?i)shamrock",
    r"(?i)content",
    r"(?i)draft",
    r"(?i)grok",
    r"(?i)scheduled.*post",
    r"(?i)your.*post",
    r"(?i)ready.*to.*publish",
]

# Gmail label we create to mark processed emails
PROCESSED_LABEL = "SocialEngine/Processed"
GROK_POSTS_LABEL = "SocialEngine/GrokPosts"


class GmailGrokScanner:
    """
    Scans Gmail for Grok-authored social media posts and ingests them
    into the social queue.
    """

    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self.queue = QueueManager(db)
        self.humanizer = ContentHumanizer()
        self._service = None
        self._processed_label_id = None
        self._grok_label_id = None

    @property
    def is_configured(self) -> bool:
        """Check if Gmail OAuth credentials are present."""
        return bool(
            os.getenv("GOOGLE_CLIENT_ID")
            and os.getenv("GOOGLE_CLIENT_SECRET")
            and os.getenv("GOOGLE_GMAIL_REFRESH_TOKEN")
        )

    def _authenticate(self):
        """Build Gmail API service from refresh token."""
        if self._service:
            return self._service

        if not self.is_configured:
            logger.warning("[GrokScanner] Gmail OAuth not configured")
            return None

        try:
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build

            creds = Credentials(
                token=None,
                refresh_token=os.getenv("GOOGLE_GMAIL_REFRESH_TOKEN"),
                token_uri="https://oauth2.googleapis.com/token",
                client_id=os.getenv("GOOGLE_CLIENT_ID"),
                client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
                scopes=["https://www.googleapis.com/auth/gmail.modify"],
            )

            self._service = build("gmail", "v1", credentials=creds)
            logger.info("[GrokScanner] ✅ Gmail API authenticated")
            return self._service

        except Exception as e:
            logger.error("[GrokScanner] Authentication failed: %s", e)
            return None

    # ── Main Scan Pipeline ────────────────────────────────────────────────

    async def scan_and_ingest(
        self,
        max_results: int = 50,
        humanize: bool = True,
        target_platform: Platform = Platform.TWITTER,
    ) -> dict:
        """
        Full pipeline: scan Gmail → parse Grok posts → humanize → enqueue.

        Returns summary dict with counts.
        """
        import asyncio

        service = self._authenticate()
        if not service:
            return {"success": False, "error": "Gmail not configured", "ingested": 0}

        # Ensure labels exist
        await asyncio.to_thread(self._ensure_labels)

        # Search for Grok emails
        emails = await asyncio.to_thread(self._fetch_grok_emails, max_results)

        if not emails:
            logger.info("[GrokScanner] No new Grok post emails found")
            return {"success": True, "scanned": 0, "ingested": 0, "skipped": 0}

        logger.info("[GrokScanner] Found %d Grok post emails", len(emails))

        ingested = 0
        skipped = 0
        errors = 0
        details = []

        for email_data in emails:
            try:
                # Extract post content from email body
                posts = self._extract_posts_from_email(email_data)

                if not posts:
                    skipped += 1
                    continue

                for post_content in posts:
                    # Humanize the content
                    final_content = post_content
                    if humanize:
                        final_content = await self.humanizer.humanize(
                            post_content,
                            platform=target_platform.value,
                            max_length=280 if target_platform == Platform.TWITTER else None,
                        )

                    # Create social post
                    hashtags = re.findall(r"#(\w+)", final_content)
                    post = SocialPost(
                        source_type=SourceType.MANUAL,
                        source_id=f"grok_email_{email_data['message_id']}",
                        source_title=f"Grok Email: {email_data.get('subject', '')[:50]}",
                        platform=target_platform,
                        content=final_content,
                        hashtags=hashtags,
                        variant=ContentVariant.SINGLE,
                        tone=ContentTone.CASUAL,
                        tone_confidence=0.9,
                        cta="📞 (239) 552-1349 | 🌐 shamrockbailbonds.biz",
                        compliance_disclaimer=settings.compliance_disclaimer,
                        status=PostStatus.PENDING,
                    )

                    result = await self.queue.enqueue(post)
                    if result:
                        ingested += 1
                        details.append({
                            "subject": email_data.get("subject", ""),
                            "content_preview": final_content[:80],
                            "platform": target_platform.value,
                        })
                    else:
                        skipped += 1

                # Mark email as processed
                await asyncio.to_thread(
                    self._mark_processed, email_data["message_id"]
                )

            except Exception as e:
                logger.error(
                    "[GrokScanner] Failed to process email %s: %s",
                    email_data.get("message_id", "?"), e,
                )
                errors += 1

        logger.info(
            "[GrokScanner] Scan complete: ingested=%d skipped=%d errors=%d",
            ingested, skipped, errors,
        )

        return {
            "success": True,
            "scanned": len(emails),
            "ingested": ingested,
            "skipped": skipped,
            "errors": errors,
            "details": details,
        }

    async def scan_backlog(
        self,
        max_results: int = 200,
        humanize: bool = True,
    ) -> dict:
        """
        One-time backlog scan — pull ALL historical Grok emails,
        not just recent ones. Use this to harvest months of posts.
        """
        import asyncio

        service = self._authenticate()
        if not service:
            return {"success": False, "error": "Gmail not configured"}

        await asyncio.to_thread(self._ensure_labels)

        # Broader search — all Grok emails, no time limit
        emails = await asyncio.to_thread(
            self._fetch_grok_emails, max_results, include_read=True,
        )

        if not emails:
            return {"success": True, "message": "No Grok post emails found in backlog"}

        logger.info("[GrokScanner] Backlog scan: found %d emails", len(emails))

        # Process each email
        return await self.scan_and_ingest(
            max_results=max_results,
            humanize=humanize,
        )

    # ── Gmail API Calls ───────────────────────────────────────────────────

    def _fetch_grok_emails(
        self,
        max_results: int = 50,
        include_read: bool = False,
    ) -> List[Dict[str, Any]]:
        """Fetch emails from Grok/xAI senders."""
        if not self._service:
            return []

        # Build search query
        sender_clauses = " OR ".join(f"from:{s}" for s in GROK_SENDERS)

        # Also search for forwarded Grok content or manual labels
        query_parts = [
            f"({sender_clauses})",
        ]

        if not include_read:
            query_parts.append("is:unread")

        # Exclude already-processed emails
        if self._processed_label_id:
            query_parts.append(f"-label:{PROCESSED_LABEL.replace('/', '-')}")

        query = " ".join(query_parts)
        logger.info("[GrokScanner] Search query: %s", query[:120])

        try:
            results = self._service.users().messages().list(
                userId="me",
                q=query,
                maxResults=max_results,
            ).execute()

            messages = results.get("messages", [])
            if not messages:
                return []

            parsed = []
            for msg_stub in messages:
                try:
                    detail = self._get_message_detail(msg_stub["id"])
                    if detail:
                        parsed.append(detail)
                except Exception as e:
                    logger.warning(
                        "[GrokScanner] Failed to parse message %s: %s",
                        msg_stub["id"], e,
                    )

            return parsed

        except Exception as e:
            logger.error("[GrokScanner] Gmail search failed: %s", e)
            return []

    def _get_message_detail(self, message_id: str) -> Optional[Dict[str, Any]]:
        """Fetch full message details."""
        msg = self._service.users().messages().get(
            userId="me",
            id=message_id,
            format="full",
        ).execute()

        headers = {h["name"].lower(): h["value"] for h in msg["payload"]["headers"]}

        subject = headers.get("subject", "(no subject)")
        sender = headers.get("from", "")
        date_str = headers.get("date", "")

        # Parse date
        received_at = None
        try:
            from email.utils import parsedate_to_datetime
            received_at = parsedate_to_datetime(date_str).isoformat()
        except Exception:
            received_at = datetime.now(timezone.utc).isoformat()

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
        if payload.get("body", {}).get("data"):
            return base64.urlsafe_b64decode(
                payload["body"]["data"]
            ).decode("utf-8", errors="replace")

        parts = payload.get("parts", [])
        for part in parts:
            if part.get("mimeType", "") == "text/plain":
                data = part.get("body", {}).get("data", "")
                if data:
                    return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")

        for part in parts:
            body = self._extract_body(part)
            if body:
                return body

        return ""

    # ── Post Extraction from Email Body ───────────────────────────────────

    def _extract_posts_from_email(self, email_data: Dict) -> List[str]:
        """
        Parse an email body to extract social media post content.

        Handles various formats:
          - Plain text posts (one per email)
          - Multiple posts separated by dividers (---, ===, numbered)
          - Posts with metadata headers (Platform:, Date:, etc.) — strip those
        """
        body = email_data.get("body", "").strip()
        if not body:
            return []

        # Strip common email noise
        body = self._clean_email_body(body)

        if not body or len(body) < 20:
            return []

        # Check if there are multiple posts separated by dividers
        posts = self._split_multiple_posts(body)

        # Filter out posts that are too short or clearly not social content
        valid_posts = []
        for post in posts:
            post = post.strip()
            if len(post) >= 20 and not self._is_metadata_only(post):
                # Strip metadata lines (Platform:, Date:, Type:, etc.)
                cleaned = self._strip_metadata_lines(post)
                if cleaned and len(cleaned) >= 20:
                    valid_posts.append(cleaned)

        return valid_posts

    def _clean_email_body(self, body: str) -> str:
        """Remove email boilerplate, signatures, and noise."""
        # Remove common email footers
        footer_patterns = [
            r"(?i)^--\s*$.*",           # Standard email sig separator
            r"(?i)unsubscribe.*$",
            r"(?i)sent from.*$",
            r"(?i)this email was.*$",
            r"(?i)powered by.*$",
            r"(?i)view in browser.*$",
            r"(?i)manage preferences.*$",
        ]

        lines = body.split("\n")
        cleaned_lines = []
        for line in lines:
            is_footer = False
            for pattern in footer_patterns:
                if re.match(pattern, line.strip()):
                    is_footer = True
                    break
            if is_footer:
                break  # Stop at first footer line
            cleaned_lines.append(line)

        return "\n".join(cleaned_lines).strip()

    def _split_multiple_posts(self, body: str) -> List[str]:
        """Split email body into individual posts if separated by dividers."""
        # Try common divider patterns
        divider_patterns = [
            r"\n---+\n",          # --- divider
            r"\n===+\n",          # === divider
            r"\n\*\*\*+\n",      # *** divider
            r"\n#{3,}\n",        # ### divider
            r"\nPost \d+[:\.]?\n", # "Post 1:" numbering
            r"\n\d+\.\s+\n",    # "1. \n" numbering
        ]

        for pattern in divider_patterns:
            parts = re.split(pattern, body)
            if len(parts) > 1:
                return [p.strip() for p in parts if p.strip()]

        # No dividers found — treat entire body as one post
        return [body]

    def _is_metadata_only(self, text: str) -> bool:
        """Check if text is just metadata with no actual content."""
        metadata_ratio = 0
        lines = text.strip().split("\n")
        for line in lines:
            if re.match(r"^(Platform|Date|Type|Category|Status|Tone|Time|Schedule|Tags?)[\s]*:", line, re.IGNORECASE):
                metadata_ratio += 1

        return metadata_ratio >= len(lines) * 0.7

    def _strip_metadata_lines(self, text: str) -> str:
        """Remove metadata header lines from post content."""
        lines = text.strip().split("\n")
        content_lines = []
        for line in lines:
            if re.match(r"^(Platform|Date|Type|Category|Status|Tone|Time|Schedule|Tags?)[\s]*:", line, re.IGNORECASE):
                continue
            content_lines.append(line)
        return "\n".join(content_lines).strip()

    # ── Label Management ──────────────────────────────────────────────────

    def _ensure_labels(self):
        """Create Gmail labels for tracking processed emails."""
        if not self._service:
            return

        try:
            existing = self._service.users().labels().list(userId="me").execute()
            existing_names = {l["name"]: l["id"] for l in existing.get("labels", [])}

            for label_name in [PROCESSED_LABEL, GROK_POSTS_LABEL]:
                if label_name in existing_names:
                    if label_name == PROCESSED_LABEL:
                        self._processed_label_id = existing_names[label_name]
                    elif label_name == GROK_POSTS_LABEL:
                        self._grok_label_id = existing_names[label_name]
                else:
                    # Create the label
                    result = self._service.users().labels().create(
                        userId="me",
                        body={
                            "name": label_name,
                            "labelListVisibility": "labelShow",
                            "messageListVisibility": "show",
                        },
                    ).execute()
                    if label_name == PROCESSED_LABEL:
                        self._processed_label_id = result["id"]
                    elif label_name == GROK_POSTS_LABEL:
                        self._grok_label_id = result["id"]
                    logger.info("[GrokScanner] Created Gmail label: %s", label_name)

        except Exception as e:
            logger.warning("[GrokScanner] Label setup failed: %s", e)

    def _mark_processed(self, message_id: str):
        """Mark an email as processed (add label + remove UNREAD)."""
        if not self._service:
            return

        try:
            modify_body = {"removeLabelIds": ["UNREAD"]}
            if self._processed_label_id:
                modify_body["addLabelIds"] = [self._processed_label_id]

            self._service.users().messages().modify(
                userId="me",
                id=message_id,
                body=modify_body,
            ).execute()
        except Exception as e:
            logger.warning("[GrokScanner] Failed to mark %s processed: %s", message_id, e)
