import logging
import os
import httpx
from datetime import datetime, timezone
from writers.slack_notifier import SlackNotifier

logger = logging.getLogger(__name__)

class WatchdogService:
    def __init__(self, db=None):
        self.db = db
        self.slack = SlackNotifier()

    async def run_health_checks(self):
        """Run system health checks for Watchdog."""
        results = {
            "api_health": False,
            "gas_bridge": False,
            "errors": []
        }
        
        # Check API Health (self check)
        try:
            # We assume we are running internally on port 5050
            async with httpx.AsyncClient(timeout=5.0) as client:
                r = await client.get("http://localhost:5050/api/counties-detail")
                if r.status_code == 200:
                    results["api_health"] = True
                else:
                    results["errors"].append(f"API Health returned {r.status_code}")
        except Exception as e:
            results["errors"].append(f"API Health check failed: {e}")

        # Check GAS Bridge
        gas_url = os.getenv("GAS_WEB_APP_URL", "")
        if gas_url:
            try:
                # GAS endpoints should respond to GET (even with missing params it should return 200 or meaningful JSON)
                async with httpx.AsyncClient(timeout=10.0) as client:
                    r = await client.get(gas_url)
                    if r.status_code < 500:
                        results["gas_bridge"] = True
                    else:
                        results["errors"].append(f"GAS Bridge returned {r.status_code}")
            except Exception as e:
                results["errors"].append(f"GAS Bridge check failed: {e}")
        else:
            results["gas_bridge"] = True # Ignore if not configured
            
        # Alert if anything is down
        if not results["api_health"] or not results["gas_bridge"]:
            msg = "🚨 *WATCHDOG ALERT* 🚨\nOne or more critical systems are down!\n\n"
            for err in results["errors"]:
                msg += f"• {err}\n"
            try:
                self.slack.send_message(msg, channel=os.getenv("SLACK_WEBHOOK_ERRORS"))
            except:
                pass
                
        return results
