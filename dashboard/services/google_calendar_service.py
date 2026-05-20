"""
ShamrockLeads — Google Calendar Service
=========================================
Creates Google Calendar events from parsed court email data.
Implements strong dedup via extended properties (case_number|date key).

Auth model: OAuth2 refresh token — same credentials as GmailReaderService.
Calendar: admin@shamrockbailbonds.biz (or GOOGLE_CALENDAR_ID env var).

Event types:
  - courtDate   → Blue (colorId 9)
  - forfeiture  → Red (colorId 11)
  - discharge   → Green (colorId 2)
  - unknown     → Grey (colorId 8)

All-day event fix: Google Calendar requires end.date = start.date + 1 day.
Date parsing: supports M/D/Y, M-D-Y, Y-M-D, and long-form (January 5, 2026).
"""

import os
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, Tuple

logger = logging.getLogger(__name__)


class GoogleCalendarService:
    """
    Handles Google Calendar event creation for court dates, forfeitures, and discharges.
    Implements strong Case# + Date dedup logic.
    
    Uses OAuth2 refresh token from env vars — same credentials as GmailReaderService.
    Migrated from GAS CourtEmailProcessor.js
    """
    
    # Google Calendar Color IDs
    COLORS = {
        'courtDate': '9',    # Blueberry (Blue)
        'forfeiture': '11',  # Tomato (Red)
        'discharge': '2',    # Sage (Green)
        'unknown': '8'       # Graphite (Grey)
    }

    # All date formats we attempt to parse
    DATE_FORMATS = [
        '%m/%d/%Y', '%m-%d-%Y', '%Y-%m-%d',
        '%B %d, %Y', '%b %d, %Y',
        '%B %d %Y', '%b %d %Y',
        '%m/%d/%y', '%m-%d-%y',
    ]

    # All time formats we attempt to parse
    TIME_FORMATS = [
        '%I:%M %p', '%I:%M%p',
        '%I:%M:%S %p', '%I:%M:%S%p',
        '%H:%M', '%H:%M:%S',
    ]
    
    def __init__(self, calendar_id: str = None):
        self.calendar_id = calendar_id or os.getenv("GOOGLE_CALENDAR_ID", "admin@shamrockbailbonds.biz")
        self._service = None
        self._client_id = os.getenv("GOOGLE_CLIENT_ID", "")
        self._client_secret = os.getenv("GOOGLE_CLIENT_SECRET", "")
        self._refresh_token = os.getenv("GOOGLE_GMAIL_REFRESH_TOKEN", "")

    @property
    def is_configured(self) -> bool:
        """Check if Calendar OAuth credentials are present."""
        return bool(self._client_id and self._client_secret and self._refresh_token)

    def _get_service(self):
        """Build Google Calendar API service from refresh token."""
        if self._service:
            return self._service

        if not self.is_configured:
            logger.warning("[Calendar] OAuth not configured — operating in dry-run mode")
            return None

        try:
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build

            creds = Credentials(
                token=None,
                refresh_token=self._refresh_token,
                token_uri="https://oauth2.googleapis.com/token",
                client_id=self._client_id,
                client_secret=self._client_secret,
                scopes=["https://www.googleapis.com/auth/calendar"],
            )

            self._service = build("calendar", "v3", credentials=creds)
            logger.info("[Calendar] ✅ Google Calendar API authenticated")
            return self._service

        except Exception as e:
            logger.error("[Calendar] Authentication failed: %s", e)
            return None

    def _parse_date(self, date_str: str) -> Optional[datetime]:
        """Try all known date formats and return a datetime object, or None."""
        if not date_str:
            return None
        date_str = date_str.strip()
        for fmt in self.DATE_FORMATS:
            try:
                return datetime.strptime(date_str, fmt)
            except ValueError:
                continue
        return None

    def _parse_time(self, time_str: str) -> Optional[datetime]:
        """Try all known time formats and return a datetime object, or None."""
        if not time_str:
            return None
        time_str = time_str.strip().upper()
        # Normalize: "8:30AM" → "8:30 AM"
        import re
        time_str = re.sub(r'(\d)(AM|PM)$', r'\1 \2', time_str)
        for fmt in self.TIME_FORMATS:
            try:
                return datetime.strptime(time_str, fmt)
            except ValueError:
                continue
        return None

    def _generate_dedup_key(self, case_number: str, date_str: str) -> str:
        """Generate a strong composite key for deduplication."""
        # Normalize date to YYYY-MM-DD if possible, else use raw string
        dt = self._parse_date(date_str)
        if dt:
            date_str = dt.strftime('%Y-%m-%d')
        return f"{case_number}|{date_str}"
        
    def check_duplicate(self, dedup_key: str) -> bool:
        """
        Check if an event with this dedup key already exists.
        Returns True if duplicate found, False otherwise.
        """
        service = self._get_service()
        if not service:
            logger.info("[Calendar] Dry-run: checking for duplicate key: %s", dedup_key)
            return False

        try:
            events = service.events().list(
                calendarId=self.calendar_id,
                privateExtendedProperty=f"shamrock_dedup_key={dedup_key}",
                maxResults=1,
            ).execute()
            found = len(events.get('items', [])) > 0
            if found:
                logger.info("[Calendar] Duplicate found for key: %s", dedup_key)
            return found
        except Exception as e:
            logger.warning("[Calendar] Dedup check failed: %s — allowing creation", e)
            return False
        
    def create_event(self, email_data: Dict[str, Any], defendant_email: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        Create a calendar event from processed email data.
        Includes strong dedup and auto-sharing.
        
        Calendar event fields populated:
          - Defendant name
          - Case number
          - Court date + time
          - County
          - Judge (if extracted)
          - Courtroom/location (if extracted)
          - Event type (Court Date / Forfeiture / Discharge)
          - Source email subject + sender
        """
        case_number = email_data.get('case_number')
        datetime_info = email_data.get('datetime_info') or {}
        date_str = datetime_info.get('date_str')
        
        if not case_number or not date_str:
            logger.error("[Calendar] Cannot create event: missing case number or date")
            return None
            
        dedup_key = self._generate_dedup_key(case_number, date_str)
        
        if self.check_duplicate(dedup_key):
            logger.warning("[Calendar] Duplicate event for key %s — skipping", dedup_key)
            return None
            
        event_type = email_data.get('event_type', 'unknown')
        color_id = self.COLORS.get(event_type, self.COLORS['unknown'])
        
        defendant_name = email_data.get('defendant_name', 'Unknown Defendant')
        county = email_data.get('county', '')
        judge = email_data.get('judge', '')
        location = email_data.get('location', '')
        time_str = datetime_info.get('time_str', '')
        
        # Build event title — surfaces all 4 key fields visible in calendar views
        # Format: ⚖️ Name | County Co. | 9:00 AM | Courtroom 4A
        event_type_emoji = {
            'courtDate': '⚖️',
            'forfeiture': '🔴',
            'discharge': '🟢',
        }.get(event_type, '📧')

        title_parts = [f"{event_type_emoji} {defendant_name}"]
        if county:
            title_parts.append(f"{county} Co.")
        if time_str:
            title_parts.append(time_str)
        if location:
            title_parts.append(location)
        title = " | ".join(title_parts)
        
        # Build rich description
        desc_lines = [
            f"📋 Case: {case_number}",
            f"👤 Defendant: {defendant_name}",
        ]
        if county:
            desc_lines.append(f"📍 County: {county}")
        if time_str:
            desc_lines.append(f"🕐 Time: {time_str}")
        if judge:
            desc_lines.append(f"⚖️  Judge: {judge}")
        if location:
            desc_lines.append(f"🏛️  Location: {location}")
        desc_lines.append("")
        desc_lines.append(f"📧 Source: {email_data.get('subject', '')}")
        desc_lines.append(f"   From: {email_data.get('sender', '')}")
        desc_lines.append("")
        desc_lines.append("— Shamrock Bail Bonds | (239) 955-0178")
        description = "\n".join(desc_lines)
        
        # Prepare attendees
        attendees = [
            {'email': 'shamrockbailoffice@gmail.com', 'responseStatus': 'accepted'}
        ]
        if defendant_email:
            attendees.append({'email': defendant_email})

        # Parse date/time for event start/end
        start_dt_str, end_dt_str = self._parse_event_times(date_str, time_str)
            
        event_body = {
            'summary': title,
            'description': description,
            'colorId': color_id,
            'extendedProperties': {
                'private': {
                    'shamrock_dedup_key': dedup_key,
                    'case_number': case_number,
                    'event_type': event_type,
                    'county': county or '',
                    'defendant_name': defendant_name,
                }
            },
            'attendees': attendees,
            'reminders': {
                'useDefault': False,
                'overrides': [
                    {'method': 'email', 'minutes': 24 * 60},  # 24h before
                    {'method': 'popup', 'minutes': 60},        # 1h before
                ],
            },
        }

        # Add start/end times
        if start_dt_str and end_dt_str:
            event_body['start'] = {'dateTime': start_dt_str, 'timeZone': 'America/New_York'}
            event_body['end'] = {'dateTime': end_dt_str, 'timeZone': 'America/New_York'}
        else:
            # All-day event fallback
            dt = self._parse_date(date_str)
            if dt:
                event_body['start'] = {'date': dt.strftime('%Y-%m-%d')}
                # Google Calendar all-day events require end date = start date + 1 day
                event_body['end'] = {'date': (dt + timedelta(days=1)).strftime('%Y-%m-%d')}
            else:
                logger.error("[Calendar] Cannot parse date '%s' — skipping event", date_str)
                return None

        # Create the event
        service = self._get_service()
        if service:
            try:
                created_event = service.events().insert(
                    calendarId=self.calendar_id,
                    body=event_body,
                    sendUpdates='all',
                ).execute()
                logger.info("[Calendar] ✅ Created event: %s (id=%s)", title, created_event.get('id'))
                return created_event
            except Exception as e:
                logger.error("[Calendar] Event creation failed: %s", e)
                return None
        else:
            # Dry-run mode — return the event body as if created
            logger.info("[Calendar] Dry-run: would create event: %s", title)
            return event_body

    def _parse_event_times(self, date_str: str, time_str: Optional[str] = None) -> Tuple[Optional[str], Optional[str]]:
        """
        Parse date and time strings into ISO format start/end times.
        Returns (start_iso, end_iso) or (None, None) if time is missing/unparseable.
        """
        if not time_str:
            return None, None

        try:
            dt_date = self._parse_date(date_str)
            if not dt_date:
                return None, None

            time_dt = self._parse_time(time_str)
            if not time_dt:
                return None, None

            # Combine date + time
            start = dt_date.replace(hour=time_dt.hour, minute=time_dt.minute, second=0, microsecond=0)
            end = start + timedelta(hours=1)  # Default 1-hour duration

            return start.isoformat(), end.isoformat()

        except Exception as e:
            logger.warning("[Calendar] Time parse error: %s", e)
            return None, None
