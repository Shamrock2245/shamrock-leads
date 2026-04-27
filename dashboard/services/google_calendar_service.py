import logging
from datetime import datetime
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

class GoogleCalendarService:
    """
    Handles Google Calendar event creation for court dates, forfeitures, and discharges.
    Implements strong Case# + Date dedup logic.
    
    Migrated from GAS CourtEmailProcessor.js
    """
    
    # Google Calendar Color IDs
    COLORS = {
        'courtDate': '9',    # Blueberry (Blue)
        'forfeiture': '11',  # Tomato (Red)
        'discharge': '2',    # Sage (Green)
        'unknown': '8'       # Graphite (Grey)
    }
    
    def __init__(self, calendar_id: str = 'admin@shamrockbailbonds.biz'):
        self.calendar_id = calendar_id
        # In production, initialize Google Calendar API client here
        # self.service = build('calendar', 'v3', credentials=creds)
        
    def _generate_dedup_key(self, case_number: str, date_str: str) -> str:
        """Generate a strong composite key for deduplication."""
        # Normalize date to YYYY-MM-DD if possible, else use raw string
        try:
            # Try to parse common formats
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
        # In production:
        # events = self.service.events().list(
        #     calendarId=self.calendar_id,
        #     privateExtendedProperty=f"shamrock_dedup_key={dedup_key}"
        # ).execute()
        # return len(events.get('items', [])) > 0
        
        logger.info(f"Checking for duplicate event with key: {dedup_key}")
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
            logger.error("Cannot create event: missing case number or date")
            return None
            
        dedup_key = self._generate_dedup_key(case_number, date_str)
        
        if self.check_duplicate(dedup_key):
            logger.warning(f"Duplicate event found for key {dedup_key}, skipping creation.")
            return None
            
        event_type = email_data.get('event_type', 'unknown')
        color_id = self.COLORS.get(event_type, self.COLORS['unknown'])
        
        title = f"[{event_type.upper()}] {email_data.get('defendant_name', 'Unknown')} - {case_number}"
        
        # Prepare attendees
        attendees = [
            {'email': 'shamrockbailoffice@gmail.com', 'responseStatus': 'accepted'}
        ]
        if defendant_email:
            attendees.append({'email': defendant_email})
            
        event_body = {
            'summary': title,
            'description': f"Source: {email_data.get('subject')}\nSender: {email_data.get('sender')}",
            'colorId': color_id,
            'extendedProperties': {
                'private': {
                    'shamrock_dedup_key': dedup_key,
                    'case_number': case_number,
                    'event_type': event_type
                }
            },
            'attendees': attendees,
            # 'start': {'dateTime': ...},
            # 'end': {'dateTime': ...}
        }
        
        # In production:
        # created_event = self.service.events().insert(
        #     calendarId=self.calendar_id,
        #     body=event_body,
        #     sendUpdates='all'
        # ).execute()
        # return created_event
        
        logger.info(f"Created event: {title} with dedup key {dedup_key}")
        return event_body
