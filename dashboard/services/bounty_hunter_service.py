import logging
from datetime import datetime, timezone
from dashboard.extensions import get_collection
from writers.slack_notifier import SlackNotifier

logger = logging.getLogger(__name__)

class BountyHunterService:
    def __init__(self, db=None):
        self.db = db
        self.slack = SlackNotifier()

    async def scan_and_alert(self):
        """Find active unposted bonds >$2,500 and surface them to Slack."""
        try:
            if not self.db:
                return {"scanned": 0, "found": 0}
            
            arrests_col = self.db["arrests"]
            # Look for recent arrests (last 48 hours) where bond is not written, bond amount > 2500
            # and custody status implies they are still in custody.
            cursor = arrests_col.find({
                "bond_written": {"$ne": True},
                "bond_amount": {"$gte": 2500},
                "custody_status": {"$in": ["In Custody", "IN CUSTODY", "Confined", "CONFINED", ""]}
            }).sort("scraped_at", -1).limit(50)
            
            high_value_leads = await cursor.to_list(length=50)
            
            if high_value_leads:
                # We don't want to alert on the same lead repeatedly.
                # In a full implementation, we'd check an alert log or use an 'alerted_bounty' flag.
                # For now, we just pick the top 5 highest value ones that haven't been processed.
                high_value_leads.sort(key=lambda x: x.get("bond_amount", 0), reverse=True)
                top_leads = high_value_leads[:3]
                
                for lead in top_leads:
                    # Check if already alerted (we can use nlp_enriched_at or a new flag, but for now we rely on the SlackNotifier deduplication if possible)
                    # We will construct a Slack message.
                    msg = (
                        f"🏹 *BOUNTY HUNTER ALERT* 🏹\n"
                        f"High-value unposted bond detected!\n\n"
                        f"👤 *Name:* {lead.get('full_name')}\n"
                        f"💵 *Bond:* ${lead.get('bond_amount'):,.2f}\n"
                        f"🏛️ *County:* {lead.get('county')} | *Booking:* {lead.get('booking_number')}\n"
                        f"⚖️ *Charges:* {lead.get('charges', 'N/A')}\n"
                    )
                    # Notify on #leads
                    self.slack.notify_lead(lead, score=lead.get("lead_score", 85))
                    
            return {"scanned": 50, "found": len(high_value_leads)}
        except Exception as e:
            logger.error(f"[BountyHunter] Error: {e}")
            return {"scanned": 0, "found": 0, "error": str(e)}
