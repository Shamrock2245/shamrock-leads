import re
import logging
from datetime import datetime
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
        
        return {
            'event_type': event_type,
            'case_number': case_number,
            'defendant_name': defendant_name,
            'datetime_info': datetime_info,
            'sender': sender,
            'subject': subject,
            'processed_at': datetime.utcnow().isoformat()
        }
