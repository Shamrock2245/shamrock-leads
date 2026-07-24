"""
ShamrockLeads — Court Email Processor
=======================================
Parses court-related emails from Gmail and extracts structured data
for Google Calendar event creation and defendant notifications.

Handles the following email types seen in production:
  - "SERVICE OF COURT DOCUMENT for Case Number XX-CF-XXXXXX"
  - "SERVICE OF COURT..." from Info Criminal Bonds / CourtService
  - "Clerk Set" hearing notices
  - Forfeiture notices
  - Discharge / exoneration notices

Extracted fields:
  - event_type: courtDate | forfeiture | discharge | unknown
  - case_number: FL format (25-CF-012345, 26-MM-000123, etc.)
  - defendant_name: from "Defendant:" or "State of Florida vs."
  - datetime_info: {date_str, time_str}
  - county: from sender domain or body text
  - judge: from "Judge:" or "Honorable" prefix
  - location: courtroom / courthouse address

Migrated from GAS CourtEmailProcessor.js
"""

import re
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)


class CourtEmailProcessor:
    """
    Processes court date emails from Gmail, extracts case details,
    and prepares them for Calendar dedup and event creation.
    """
    
    # Keywords for classification — ordered by specificity
    KEYWORDS = {
        'courtDate': [
            'SERVICE OF COURT DOCUMENT for Case Number',
            'SERVICE OF COURT',
            'Notice of Appearance',
            'Court Date Notice',
            'Notice of Hearing',
            'criminal bonds',
            'Clerk Set',
            'Arraignment',
            'First Appearance',
            'Pretrial Conference',
            'Status Conference',
            'Trial Date',
        ],
        'forfeiture': [
            'Notice of Forfeiture',
            'FORFEITURE',
            'Forfeiture Hearing',
        ],
        'discharge': [
            'Discharge',
            'Release',
            'Power of Attorney Discharge',
            'Bond Discharge',
            'Certificate of Discharge',
            'Exoneration',
        ]
    }
    
    # Trusted senders — all clerk domains + our own domain
    WHITELIST_DOMAINS = [
        'leeclerk.org', 'collierclerk.com', 'hendryso.org', 'charlotteclerk.com',
        'manateeclerk.com', 'sarasotaclerk.com', 'desotoclerk.com', 'hillsboroughclerk.com',
        'circuit20.org', 'jud12.flcourts.org', 'jud20.flcourts.org', 'ca.cjis20.org',
        'shamrockbailbonds.biz',
    ]

    # Domain → County mapping
    DOMAIN_COUNTY_MAP = {
        'leeclerk.org': 'Lee',
        'collierclerk.com': 'Collier',
        'hendryso.org': 'Hendry',
        'charlotteclerk.com': 'Charlotte',
        'manateeclerk.com': 'Manatee',
        'sarasotaclerk.com': 'Sarasota',
        'desotoclerk.com': 'DeSoto',
        'hillsboroughclerk.com': 'Hillsborough',
        'circuit20.org': 'Lee',       # 20th Circuit: Lee/Charlotte/Collier/Hendry/Glades
        'jud12.flcourts.org': 'Sarasota',   # 12th Circuit: Sarasota/Manatee/DeSoto
        'jud20.flcourts.org': 'Lee',         # 20th Circuit
    }

    # All 67 FL counties for body text extraction
    FL_COUNTIES = [
        'Alachua', 'Baker', 'Bay', 'Bradford', 'Brevard', 'Broward', 'Calhoun',
        'Charlotte', 'Citrus', 'Clay', 'Collier', 'Columbia', 'DeSoto', 'Dixie',
        'Duval', 'Escambia', 'Flagler', 'Franklin', 'Gadsden', 'Gilchrist', 'Glades',
        'Gulf', 'Hamilton', 'Hardee', 'Hendry', 'Hernando', 'Highlands', 'Hillsborough',
        'Holmes', 'Indian River', 'Jackson', 'Jefferson', 'Lafayette', 'Lake', 'Lee',
        'Leon', 'Levy', 'Liberty', 'Madison', 'Manatee', 'Marion', 'Martin',
        'Miami-Dade', 'Monroe', 'Nassau', 'Okaloosa', 'Okeechobee', 'Orange', 'Osceola',
        'Palm Beach', 'Pasco', 'Pinellas', 'Polk', 'Putnam', 'Saint Johns', 'Saint Lucie',
        'Santa Rosa', 'Sarasota', 'Seminole', 'Sumter', 'Suwannee', 'Taylor', 'Union',
        'Volusia', 'Wakulla', 'Walton', 'Washington',
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
        """
        Extract FL case number using regex.
        FL format: YY-CF-XXXXXX, YY-MM-XXXXXX, YY-CF-XXXXXX-A, etc.
        Examples: 25-CF-012345, 26-MM-000123, 2025-CF-123456
        """
        patterns = [
            # Standard FL case number: 25-CF-012345 or 2025-CF-123456
            r'\b(\d{2,4}-[A-Z]{2}-\d{4,}(?:-[A-Z0-9]+)?)\b',
            # With spaces: 25 CF 012345
            r'\b(\d{2,4}\s[A-Z]{2}\s\d{4,})\b',
            # After "Case Number:" label
            r'Case\s*(?:Number|No\.?|#):?\s*([A-Z0-9][A-Z0-9\-\s]{4,20})',
        ]
        
        text_to_search = f"{subject}\n{body}"
        
        for pattern in patterns:
            match = re.search(pattern, text_to_search, re.IGNORECASE)
            if match:
                return match.group(1).strip()
                
        return None
        
    @classmethod
    def extract_defendant_name(cls, body: str) -> Optional[str]:
        """Extract defendant name from email body."""
        patterns = [
            # "Defendant: SMITH, JOHN"
            r'Defendant:?\s*([A-Za-z\s,\-\.\']+?)(?:\n|\r|DOB|Date of Birth|$)',
            # "State of Florida vs. John Smith"
            r'State\s+of\s+Florida\s+vs?\.?\s+([A-Za-z\s,\-\.\']+?)(?:\n|\r|,|$)',
            # "IN RE: JOHN SMITH" or "RE: JOHN SMITH"
            r'(?:IN\s+)?RE:?\s+([A-Z][A-Za-z\s,\-\.\']+?)(?:\n|\r|$)',
            # "Name: SMITH, JOHN"
            r'\bName:?\s*([A-Za-z\s,\-\.\']+?)(?:\n|\r|DOB|$)',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, body, re.IGNORECASE)
            if match:
                name = match.group(1).strip()
                # Clean up trailing artifacts
                name = re.sub(r'(?i)\b(?:esq|esquire|attorney|dob|vs?\.?|jr\.?|sr\.?|ii|iii)\s*$', '', name).strip()
                name = re.sub(r'\s+', ' ', name).strip().rstrip(',').strip()
                if len(name) > 3:  # Avoid single-char matches
                    return name
                
        return None
        
    @classmethod
    def extract_court_datetime(cls, body: str) -> Optional[Dict[str, Any]]:
        """
        Extract court date and time from email body.
        Handles multiple date/time formats found in FL court emails.
        """
        # Date patterns — ordered from most specific to least
        date_patterns = [
            # "Hearing Date: 05/15/2026" or "Date: May 15, 2026"
            r'(?:Hearing\s+Date|Court\s+Date|Date\s+of\s+Hearing|Scheduled\s+(?:for|on)):?\s*(\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4})',
            r'(?:Hearing\s+Date|Court\s+Date|Date\s+of\s+Hearing|Scheduled\s+(?:for|on)):?\s*([A-Za-z]+\s+\d{1,2},?\s+\d{4})',
            # Bare date patterns in body
            r'\b(\d{1,2}/\d{1,2}/\d{4})\b',
            r'\b(\d{1,2}-\d{1,2}-\d{4})\b',
            r'\b([A-Za-z]+ \d{1,2},? \d{4})\b',
        ]
        
        # Time patterns
        time_patterns = [
            r'(?:Hearing\s+Time|Court\s+Time|Time):?\s*(\d{1,2}:\d{2}\s*(?:AM|PM|am|pm))',
            r'(?:at|@)\s+(\d{1,2}:\d{2}\s*(?:AM|PM|am|pm))',
            r'\b(\d{1,2}:\d{2}\s*(?:AM|PM|am|pm))\b',
        ]
        
        result = {}

        for pattern in date_patterns:
            match = re.search(pattern, body, re.IGNORECASE)
            if match:
                result['date_str'] = match.group(1).strip()
                break

        for pattern in time_patterns:
            match = re.search(pattern, body, re.IGNORECASE)
            if match:
                result['time_str'] = match.group(1).strip()
                break
            
        return result if result else None

    @classmethod
    def extract_county(cls, sender: str, body: str) -> Optional[str]:
        """Extract county name from sender domain or email body."""
        # Check sender domain first (most reliable)
        for domain, county in cls.DOMAIN_COUNTY_MAP.items():
            if domain in sender.lower():
                return county
        
        # Check body for FL county names
        for county in cls.FL_COUNTIES:
            pattern = rf'\b{re.escape(county)}\s+County\b'
            if re.search(pattern, body, re.IGNORECASE):
                return county
        
        # Generic fallback
        match = re.search(r'\b(\w+)\s+County\b', body, re.IGNORECASE)
        if match:
            return match.group(1).title()
        
        return None
    
    @classmethod
    def extract_judge(cls, body: str) -> Optional[str]:
        """Extract judge name from email body."""
        patterns = [
            r'(?:Judge|Hon\.|Honorable):?\s+([A-Za-z\s\.]+?)(?:\n|\r|,|$)',
            r'(?:before|assigned\s+to)\s+(?:Judge|Hon\.?)?\s*([A-Za-z\s\.]+?)(?:\n|\r|,|$)',
            r'(?:The\s+Honorable)\s+([A-Za-z\s\.]+?)(?:\n|\r|,|$)',
        ]
        for pattern in patterns:
            match = re.search(pattern, body, re.IGNORECASE)
            if match:
                name = match.group(1).strip().rstrip('.')
                if len(name) > 3:
                    return name
        return None

    @classmethod
    def extract_location(cls, body: str) -> Optional[str]:
        """Extract courtroom/location from email body."""
        patterns = [
            r'(?:Room|Courtroom|Division):?\s*([A-Za-z0-9\s\-]+?)(?:\n|\r|,|$)',
            r'(?:Location|Courthouse|Address):?\s*([A-Za-z0-9\s,\.]+?)(?:\n|\r|$)',
        ]
        for pattern in patterns:
            match = re.search(pattern, body, re.IGNORECASE)
            if match:
                loc = match.group(1).strip()
                if len(loc) > 2:
                    return loc
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
        datetime_val = parsed.get('datetime_info')
        if isinstance(datetime_val, dict):
            date_str = datetime_val.get('date_str', 'TBD')
            time_str = datetime_val.get('time_str', '')
        elif isinstance(datetime_val, str):
            date_str = datetime_val
            time_str = ''
        else:
            date_str = 'TBD'
            time_str = ''
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
        if not is_trusted and not any(kw in sender.lower() for kw in ['clerk', 'court', 'criminal', 'bonds']):
            logger.warning("[CourtEmailProcessor] Untrusted sender: %s", sender)
            
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
