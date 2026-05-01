import re
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)

class CourtEmailProcessor:
    """
    Processes court date emails from Gmail, extracts case details,
    and prepares them for Calendar dedup and event creation.
    
    Migrated from GAS CourtEmailProcessor.js
    """
    
    # Keywords for classification
    KEYWORDS = {
        'courtDate': ['SERVICE OF COURT DOCUMENT for Case Number', 'Notice of Appearance', 'Court Date Notice', 'Notice of Hearing'],
        'forfeiture': ['Notice of Forfeiture', 'FORFEITURE'],
        'discharge': ['Discharge', 'Release', 'Power of Attorney Discharge', 'Bond Discharge', 'Certificate of Discharge']
    }
    
    # Trusted senders
    WHITELIST_DOMAINS = [
        'leeclerk.org', 'collierclerk.com', 'hendryso.org', 'charlotteclerk.com',
        'manateeclerk.com', 'sarasotaclerk.com', 'desotoclerk.com', 'hillsboroughclerk.com'
    ]
    
    @classmethod
    def classify_email(cls, subject: str, body: str) -> str:
        """Classify the email type based on subject and body keywords."""
        text_to_search = f"{subject} {body}".lower()
        
        for event_type, keywords in cls.KEYWORDS.items():
            for keyword in keywords:
                if keyword.lower() in text_to_search:
                    return event_type
                    
        return 'unknown'
        
    @classmethod
    def extract_case_number(cls, subject: str, body: str) -> Optional[str]:
        """Extract case number using regex pattern."""
        # Common FL case number format: 24-CF-012345 or 2024-CF-123456
        patterns = [
            r'\d{2,4}-[A-Z]{2}-\d+',
            r'\d{2,4} [A-Z]{2} \d+',
            r'Case Number:?\s*([A-Z0-9-]+)'
        ]
        
        text_to_search = f"{subject}\n{body}"
        
        for pattern in patterns:
            match = re.search(pattern, text_to_search, re.IGNORECASE)
            if match:
                # If group 1 exists, return it, else return full match
                return match.group(1) if len(match.groups()) > 0 else match.group(0)
                
        return None
        
    @classmethod
    def extract_defendant_name(cls, body: str) -> Optional[str]:
        """Extract defendant name from email body."""
        patterns = [
            r'Defendant:?\s*([A-Za-z\s,]+)(?:\n|$)',
            r'State of Florida vs\.?\s*([A-Za-z\s,]+)(?:\n|$)',
            r'Name:?\s*([A-Za-z\s,]+)(?:\n|$)'
        ]
        
        for pattern in patterns:
            match = re.search(pattern, body, re.IGNORECASE)
            if match:
                name = match.group(1).strip()
                # Clean up common trailing artifacts
                name = re.sub(r'(?i)(?:esq|esquire|attorney|dob).*$', '', name).strip()
                return name
                
        return None
        
    @classmethod
    def extract_court_datetime(cls, body: str) -> Optional[Dict[str, Any]]:
        """Extract court date and time from email body."""
        # This is a simplified extraction. In production, use a more robust NLP/regex approach
        # or an LLM API for complex unstructured text.
        date_pattern = r'(?:Date|Hearing Date):?\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\w+ \d{1,2},? \d{4})'
        time_pattern = r'(?:Time|Hearing Time):?\s*(\d{1,2}:\d{2}\s*(?:AM|PM|am|pm)?)'
        
        date_match = re.search(date_pattern, body, re.IGNORECASE)
        time_match = re.search(time_pattern, body, re.IGNORECASE)
        
        result = {}
        if date_match:
            result['date_str'] = date_match.group(1).strip()
        if time_match:
            result['time_str'] = time_match.group(1).strip()
            
        return result if result else None

    @classmethod
    def extract_county(cls, sender: str, body: str) -> Optional[str]:
        """Extract county name from sender domain or email body."""
        domain_county_map = {
            'leeclerk.org': 'Lee',
            'collierclerk.com': 'Collier',
            'hendryso.org': 'Hendry',
            'charlotteclerk.com': 'Charlotte',
            'manateeclerk.com': 'Manatee',
            'sarasotaclerk.com': 'Sarasota',
            'desotoclerk.com': 'DeSoto',
            'hillsboroughclerk.com': 'Hillsborough',
            'circuit20.org': 'Lee',  # 20th Circuit covers Lee/Charlotte/Collier/Hendry/Glades
        }
        
        for domain, county in domain_county_map.items():
            if domain in sender.lower():
                return county
        
        # Fallback: search body for county mentions
        county_pattern = r'(\w+)\s+County'
        match = re.search(county_pattern, body)
        if match:
            return match.group(1).title()
        
        return None
    
    @classmethod
    def extract_judge(cls, body: str) -> Optional[str]:
        """Extract judge name from email body."""
        patterns = [
            r'(?:Judge|Hon\.|Honorable):?\s+([A-Za-z\s.]+?)(?:\n|,|$)',
            r'(?:before|assigned to)\s+(?:Judge|Hon\.)?\s*([A-Za-z\s.]+?)(?:\n|,|$)',
        ]
        for pattern in patterns:
            match = re.search(pattern, body, re.IGNORECASE)
            if match:
                return match.group(1).strip().rstrip('.')
        return None

    @classmethod
    def extract_location(cls, body: str) -> Optional[str]:
        """Extract courtroom/location from email body."""
        patterns = [
            r'(?:Room|Courtroom|Division):?\s*([A-Za-z0-9\s-]+?)(?:\n|,|$)',
            r'(?:Location|Courthouse):?\s*([A-Za-z0-9\s,.]+?)(?:\n|$)',
        ]
        for pattern in patterns:
            match = re.search(pattern, body, re.IGNORECASE)
            if match:
                return match.group(1).strip()
        return None

    @classmethod
    def generate_sms_summary(cls, parsed: Dict[str, Any]) -> Optional[str]:
        """
        Generate a clean iMessage summary for court date notifications.
        Sent to defendants/indemnitors via BlueBubbles.
        """
        event_type = parsed.get('event_type', 'unknown')
        case_number = parsed.get('case_number', 'N/A')
        defendant_name = parsed.get('defendant_name', 'N/A')
        datetime_info = parsed.get('datetime_info') or {}
        date_str = datetime_info.get('date_str', 'TBD')
        time_str = datetime_info.get('time_str', '')
        location = parsed.get('location', '')
        county = parsed.get('county', '')
        
        if event_type == 'courtDate':
            lines = [
                "⚖️ Court Date Notice",
                f"Case: {case_number}",
                f"Defendant: {defendant_name}",
            ]
            time_line = f"Date: {date_str}"
            if time_str:
                time_line += f" at {time_str}"
            lines.append(time_line)
            
            if location:
                lines.append(f"Location: {location}")
            elif county:
                lines.append(f"County: {county} County Courthouse")
            
            lines.append("")
            lines.append("📍 Reply CONFIRM to acknowledge")
            lines.append("")
            lines.append("— Shamrock Bail Bonds")
            lines.append("(239) 955-0178")
            
        elif event_type == 'forfeiture':
            lines = [
                "🔴 Forfeiture Notice",
                f"Case: {case_number}",
                f"Defendant: {defendant_name}",
                f"Date: {date_str}",
                "",
                "⚠️ Please contact us IMMEDIATELY.",
                "— Shamrock Bail Bonds",
                "(239) 955-0178",
            ]
        elif event_type == 'discharge':
            lines = [
                "🟢 Bond Discharge Notice",
                f"Case: {case_number}",
                f"Defendant: {defendant_name}",
                "",
                "Your bond obligation has been discharged.",
                "Thank you for your cooperation.",
                "— Shamrock Bail Bonds",
                "(239) 955-0178",
            ]
        else:
            return None
        
        return "\n".join(lines)

    @classmethod
    def process_email(cls, subject: str, body: str, sender: str) -> Dict[str, Any]:
        """Main processing pipeline for a single email."""
        # 1. Verify sender
        is_trusted = any(domain in sender.lower() for domain in cls.WHITELIST_DOMAINS)
        if not is_trusted and 'clerk' not in sender.lower():
            logger.warning(f"Untrusted sender: {sender}")
            # We might still process it, but flag it
            
        # 2. Classify
        event_type = cls.classify_email(subject, body)
        
        # 3. Extract data
        case_number = cls.extract_case_number(subject, body)
        defendant_name = cls.extract_defendant_name(body)
        datetime_info = cls.extract_court_datetime(body)
        county = cls.extract_county(sender, body)
        judge = cls.extract_judge(body)
        location = cls.extract_location(body)
        
        return {
            'event_type': event_type,
            'case_number': case_number,
            'defendant_name': defendant_name,
            'datetime_info': datetime_info,
            'county': county,
            'judge': judge,
            'location': location,
            'sender': sender,
            'subject': subject,
            'is_trusted': is_trusted,
            'processed_at': datetime.now(timezone.utc).isoformat()
        }
