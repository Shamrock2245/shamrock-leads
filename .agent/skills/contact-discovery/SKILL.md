---
name: contact-discovery
description: OSINT-style family/friend contact discovery for arrestees. Identifies potential indemnitors from public records. Use when building the contact finder pipeline.
---

# Contact Discovery ("The Finder")

> Find a name. Ideally, find a name AND a phone number.

## When to Use
- Building or improving the contact discovery pipeline
- Adding new public data sources
- User says "find family for", "who can we contact", "indemnitor lookup"
- Phase 4 implementation work

## ⚠️ Legal Compliance

**CRITICAL RULES:**
1. **All sources must be publicly available** — no purchased data, no hacking
2. **No direct automated outreach** until FL Statute 648 compliance is verified
3. **Results are surfaced to human bondsman** — never auto-contacted
4. **PII never logged to Slack, console, or public channels in production**
5. **Opt-out mechanism required** before any outreach goes live

---

## Data Source Hierarchy

Listed by reliability and ease of access:

### Tier 1: Public Records (High Confidence)

| Source | Data Available | Access Method | Confidence |
|--------|---------------|---------------|------------|
| **FL Voter Registration** | Name, DOB, address, household | Bulk download from FL DOS | 0.90 |
| **Property Appraiser** | Owner names, address, co-owners | County appraiser APIs | 0.85 |
| **Court Records (Clerk)** | Co-defendants, attorneys, contacts | County clerk public search | 0.80 |
| **FL Corporations (Sunbiz)** | Business associates, registered agents | sunbiz.org search | 0.70 |

### Tier 2: Public Web (Medium Confidence)

| Source | Data Available | Access Method | Confidence |
|--------|---------------|---------------|------------|
| **Facebook** | Family members, friends list | Public profile scraping | 0.60 |
| **LinkedIn** | Professional connections | Public profile | 0.50 |
| **WhitePages / TruePeopleSearch** | Phone, relatives, associates | Web scraping | 0.65 |
| **FastPeopleSearch** | Phone, email, relatives | Web scraping | 0.65 |

### Tier 3: Inference (Low Confidence)

| Source | Data Available | Method | Confidence |
|--------|---------------|--------|------------|
| **Same address** | Household members | Voter reg cross-reference | 0.40 |
| **Same last name + county** | Potential relatives | Voter reg search | 0.30 |
| **Emergency contact** | Listed contacts | Prior booking data | 0.50 |

---

## Discovery Pipeline

```python
class ContactFinder:
    """
    Given a defendant's name, DOB, and county,
    find potential indemnitor contacts.
    """
    
    def discover(self, defendant: ArrestRecord) -> list[Contact]:
        contacts = []
        
        # 1. Address-based household lookup
        if defendant.Address:
            contacts += self.find_household(defendant.Address)
        
        # 2. Same last name in county
        contacts += self.find_relatives(
            defendant.Last_Name, 
            defendant.County
        )
        
        # 3. Prior arrest co-defendants
        contacts += self.find_associates(defendant.Booking_Number)
        
        # 4. Public web search
        contacts += self.find_web_profiles(
            f"{defendant.First_Name} {defendant.Last_Name}",
            defendant.City,
            defendant.State
        )
        
        # Deduplicate and rank by confidence
        return self.rank_contacts(contacts)
```

---

## Contact Schema

```python
@dataclass
class Contact:
    name: str                    # Full name
    relationship: str            # "household_member", "relative", "associate"
    phone: Optional[str]         # Phone number if found
    email: Optional[str]         # Email if found
    address: Optional[str]       # Address if found
    source: str                  # "voter_reg", "property", "web_search"
    confidence: float            # 0.0 to 1.0
    discovered_at: datetime      # When this contact was found
    defendant_booking: str       # Links back to the defendant
```

---

## Implementation Notes

### FL Voter Registration
- Bulk data available from Florida Division of Elections
- Updated monthly
- Contains: name, DOB, address, party, precinct
- **Key insight**: Multiple voters at the same address = household members
- File format: pipe-delimited text, ~14M records

### Property Appraiser
- Each county has its own appraiser website
- Most have public search APIs
- **Key insight**: Property owner name often reveals spouse/partner

### People Search Aggregators
- Rate-limited — use with delays
- Results vary in accuracy
- Cross-reference with voter reg for validation

---

## Confidence Scoring

```python
def calculate_confidence(contact, defendant):
    base = 0.0
    
    # Same address
    if contact.address == defendant.Address:
        base += 0.40
    
    # Same last name
    if contact.name.split()[-1] == defendant.Last_Name:
        base += 0.25
    
    # Phone number found
    if contact.phone:
        base += 0.20
    
    # Multiple sources confirm
    if contact.source_count > 1:
        base += 0.15
    
    return min(base, 1.0)
```

---

## Privacy & Ethics

1. **Never store SSNs, financial data, or medical records**
2. **All data must be from public sources** — no data broker purchases
3. **Implement data retention** — delete contact records after 30 days if no engagement
4. **Log all discovery actions** for audit trail
5. **Respect Do Not Contact lists** when outreach is enabled
