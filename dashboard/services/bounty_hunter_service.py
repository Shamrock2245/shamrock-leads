import logging
import asyncio
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
            if self.db is None:
                return {"scanned": 0, "found": 0}
            
            arrests_col = self.db["arrests"]
            # Look for recent arrests (last 48 hours) where bond is not written, bond amount > 2500
            # and custody status implies they are still in custody.
            cursor = arrests_col.find({
                "bond_written": {"$ne": True},
                "bounty_alerted": {"$ne": True},
                "bond_amount": {"$gte": 2500},
                "custody_status": {"$in": ["In Custody", "IN CUSTODY", "Confined", "CONFINED", ""]}
            }).sort("scraped_at", -1).limit(50)
            
            high_value_leads = await cursor.to_list(length=50)
            
            if high_value_leads:
                # Pick the top 3 highest value ones that haven't been processed
                high_value_leads.sort(key=lambda x: x.get("bond_amount", 0), reverse=True)
                top_leads = high_value_leads[:3]
                
                for lead in top_leads:
                    # Construct a Slack message payload
                    msg = (
                        f"🏹 *BOUNTY HUNTER ALERT* 🏹\n"
                        f"High-value unposted bond detected!\n\n"
                        f"👤 *Name:* {lead.get('full_name')}\n"
                        f"💵 *Bond:* ${lead.get('bond_amount'):,.2f}\n"
                        f"🏛️ *County:* {lead.get('county')} | *Booking:* {lead.get('booking_number')}\n"
                        f"⚖️ *Charges:* {lead.get('charges', 'N/A')}\n"
                    )
                    
                    blocks = [
                        {
                            "type": "section",
                            "text": {"type": "mrkdwn", "text": msg}
                        }
                    ]
                    
                    # Notify on #leads using _post asynchronously to avoid blocking the event loop
                    success = await asyncio.to_thread(self.slack._post, self.slack.webhook_leads, {"blocks": blocks})
                    if success:
                        await arrests_col.update_one(
                            {"_id": lead["_id"]},
                            {"$set": {"bounty_alerted": True}}
                        )
                    
            return {"scanned": 50, "found": len(high_value_leads)}
        except Exception as e:
            logger.error(f"[BountyHunter] Error: {e}")
            return {"scanned": 0, "found": 0, "error": str(e)}
