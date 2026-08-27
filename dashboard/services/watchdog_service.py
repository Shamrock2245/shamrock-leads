import logging
import os
import httpx
import asyncio
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
            "shannon_paths": False,
            "errors": []
        }
        
        # Check API Health (self check)
        try:
            # We assume we are running internally on port 5050
            async with httpx.AsyncClient(timeout=5.0) as client:
                r = await client.get("http://localhost:5050/health")
                if r.status_code == 200:
                    results["api_health"] = True
                else:
                    results["errors"].append(f"API Health returned {r.status_code}")
        except Exception as e:
            results["errors"].append(f"API Health check failed: {e}")

        # Shannon path: Netlify voice 403, fallback Dial 332, GAS, BlueBubbles, Mem0
        try:
            from dashboard.routers.shannon_health import collect_shannon_path_checks
            shannon = await collect_shannon_path_checks()
            results["shannon_paths"] = bool(shannon.get("success"))
            results["shannon_checks"] = shannon.get("checks") or {}
            gas_check = results["shannon_checks"].get("gas_health") or {}
            results["gas_bridge"] = bool(gas_check.get("ok"))
            if not results["shannon_paths"]:
                for name, check in results["shannon_checks"].items():
                    if isinstance(check, dict) and not check.get("ok"):
                        results["errors"].append(
                            f"Shannon {name}: {check.get('error') or check.get('status') or 'down'}"
                        )
        except Exception as e:
            results["errors"].append(f"Shannon path checks failed: {e}")
            gas_url = os.getenv("GAS_WEB_APP_URL", "")
            if gas_url:
                try:
                    async with httpx.AsyncClient(timeout=10.0) as client:
                        r = await client.get(gas_url)
                        if r.status_code < 500:
                            results["gas_bridge"] = True
                        else:
                            results["errors"].append(f"GAS Bridge returned {r.status_code}")
                except Exception as ge:
                    results["errors"].append(f"GAS Bridge check failed: {ge}")
            else:
                results["gas_bridge"] = True

        # Alert if anything is down
        if not results["api_health"] or not results["gas_bridge"] or not results["shannon_paths"]:
            msg = "🚨 *WATCHDOG ALERT* 🚨\nOne or more critical systems are down!\n\n"
            for err in results["errors"]:
                msg += f"• {err}\n"
            try:
                await asyncio.to_thread(self.slack._post, self.slack.webhook_errors, {"text": msg})
            except Exception:
                pass
                
        return results
