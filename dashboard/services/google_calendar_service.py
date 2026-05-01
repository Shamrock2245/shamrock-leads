import os
import logging
from datetime import datetime
from typing import Dict, Any, Optional

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

    def _generate_dedup_key(self, case_number: str, date_str: str) -> str:
        """Generate a strong composite key for deduplication."""
        # Normalize date to YYYY-MM-DD if possible, else use raw string
        try:
            for fmt in ['%m/%d/%Y', '%m-%d-%Y', '%Y-%m-%d']:
                try:
                    dt = datetime.strptime(date_str, fmt)
                    date_str = dt.strftime('%Y-%m-%d')
                    break
                except ValueError:
                    continue
        except Exception:
            pass
            
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
        
        defendant_name = email_data.get('defendant_name', 'Unknown')
        county = email_data.get('county', '')
        judge = email_data.get('judge', '')
        location = email_data.get('location', '')
        
        title = f"[{event_type.upper()}] {defendant_name} — {case_number}"
        if county:
            title += f" ({county})"
        
        # Build description
        desc_lines = [
            f"Source: {email_data.get('subject', '')}",
            f"Sender: {email_data.get('sender', '')}",
        ]
        if judge:
            desc_lines.append(f"Judge: {judge}")
        if location:
            desc_lines.append(f"Location: {location}")
        description = "\n".join(desc_lines)
        
        # Prepare attendees
        attendees = [
            {'email': 'shamrockbailoffice@gmail.com', 'responseStatus': 'accepted'}
        ]
        if defendant_email:
            attendees.append({'email': defendant_email})

        # Parse date/time for event start/end
        start_dt, end_dt = self._parse_event_times(date_str, datetime_info.get('time_str'))
            
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
                }
            },
            'attendees': attendees,
        }

        # Add start/end times
        if start_dt and end_dt:
            event_body['start'] = {'dateTime': start_dt, 'timeZone': 'America/New_York'}
            event_body['end'] = {'dateTime': end_dt, 'timeZone': 'America/New_York'}
        elif date_str:
            # All-day event fallback
            try:
                for fmt in ['%m/%d/%Y', '%m-%d-%Y', '%Y-%m-%d']:
                    try:
                        dt = datetime.strptime(date_str, fmt)
                        event_body['start'] = {'date': dt.strftime('%Y-%m-%d')}
                        event_body['end'] = {'date': dt.strftime('%Y-%m-%d')}
                        break
                    except ValueError:
                        continue
            except Exception:
                pass

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

    def _parse_event_times(self, date_str: str, time_str: Optional[str] = None):
        """Parse date and time strings into ISO format start/end times."""
        if not time_str:
            return None, None

        try:
            import re
            from datetime import timedelta

            # Parse date
            dt_date = None
            for fmt in ['%m/%d/%Y', '%m-%d-%Y', '%Y-%m-%d']:
                try:
                    dt_date = datetime.strptime(date_str, fmt)
                    break
                except ValueError:
                    continue

            if not dt_date:
                return None, None

            # Parse time
            time_clean = time_str.strip()
            time_dt = None
            for fmt in ['%I:%M %p', '%I:%M%p', '%H:%M']:
                try:
                    time_dt = datetime.strptime(time_clean, fmt)
                    break
                except ValueError:
                    continue

            if not time_dt:
                return None, None

            # Combine
            start = dt_date.replace(hour=time_dt.hour, minute=time_dt.minute)
            end = start + timedelta(hours=1)  # Default 1-hour duration

            return start.isoformat(), end.isoformat()

        except Exception:
            return None, None
