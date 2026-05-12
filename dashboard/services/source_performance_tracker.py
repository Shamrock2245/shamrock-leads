"""
ShamrockLeads — Source Performance Tracker (Alpha Engine)
==========================================================
The "closed feedback loop" — tracks which lead sources (counties, channels)
actually convert to bonds written and revenue earned.

Inspired by shamrock-trading-bot's sniper_discovery.py pattern:
  Observe → Harvest → Score → Promote → Monitor → Execute → Feedback → Prune

This service computes a composite "Source Score" (0–100) per county that
combines scraper yield, lead quality, outreach response rate, conversion
rate, and revenue per lead. The score drives:
  - Scraper frequency recommendations
  - Outreach tier assignments (immediate / queued / cold)
  - Resource allocation (which counties deserve more attention)

Collections Read:
  - arrests              → Lead volume, hot lead ratio
  - outreach_sequences   → Outreach attempts, reply rate
  - active_bonds         → Bonds written per county
  - payments             → Revenue per county
  - scraper_run_log      → Scraper health, uptime
  - scraper_status       → Last run info per county
  - prospective_bonds    → Pipeline stage distribution

Collection Written:
  - source_performance   → Computed source scores + recommendations
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)


# ── Scoring Weights (tunable — mirrors trading bot's _calculate_sniper_score) ─
WEIGHTS = {
    "lead_volume":       0.10,  # Raw arrest count (normalized)
    "hot_lead_ratio":    0.20,  # % of leads that score Hot
    "outreach_reply":    0.15,  # Outreach reply rate
    "conversion_rate":   0.30,  # Arrests → bonded cases (THE key metric)
    "revenue_per_lead":  0.15,  # Revenue efficiency
    "scraper_health":    0.10,  # Uptime, error rate
}

# ── Tier Thresholds (mirrors trading bot's Seed/Growth/Acceleration/Whale) ─
TIER_THRESHOLDS = {
    "alpha":     75,   # Top-tier county — aggressive scraping + immediate outreach
    "growth":    50,   # Proven county — standard cadence + queued outreach
    "prospect":  25,   # Early-stage — monitoring + cold outreach
    "dormant":    0,   # Low-yield — reduced scraping, no outreach
}

# ── Scraper Frequency Recommendations (minutes) ─
FREQUENCY_RECOMMENDATIONS = {
    "alpha":     20,   # Every 20 min for top-yield counties
    "growth":    60,   # Every hour
    "prospect": 120,   # Every 2 hours
    "dormant":  360,   # Every 6 hours
}

# ── Outreach Tiers (maps to outreach_sequencer.py SCORE_TIERS) ─
OUTREACH_TIER_MAP = {
    "alpha":    "high",     # Immediate, aggressive outreach
    "growth":   "medium",   # Standard cadence
    "prospect": "low",      # Single touchpoint
    "dormant":  "none",     # No automated outreach
}


@dataclass
class SourceScore:
    """Computed score for a single lead source (county)."""
    county: str
    score: float = 0.0
    tier: str = "dormant"

    # Raw metrics
    lead_volume_30d: int = 0
    hot_leads_30d: int = 0
    hot_lead_ratio: float = 0.0
    outreach_attempts: int = 0
    outreach_replies: int = 0
    outreach_reply_rate: float = 0.0
    bonds_written_90d: int = 0
    conversion_rate: float = 0.0
    total_premium_90d: float = 0.0
    revenue_per_lead: float = 0.0
    scraper_uptime_pct: float = 100.0
    scraper_error_rate: float = 0.0
    last_scrape_at: Optional[str] = None

    # Component scores (0–100 each, before weighting)
    score_lead_volume: float = 0.0
    score_hot_ratio: float = 0.0
    score_outreach: float = 0.0
    score_conversion: float = 0.0
    score_revenue: float = 0.0
    score_health: float = 0.0

    # Recommendations
    recommended_frequency_min: int = 360
    recommended_outreach_tier: str = "none"
    actions: list = field(default_factory=list)

    # Metadata
    computed_at: str = ""
    trend_vs_prior: float = 0.0  # +/- change from last calculation

    def to_dict(self) -> dict:
        return asdict(self)


class SourcePerformanceTracker:
    """
    The Alpha Engine — computes and persists source performance scores.

    Usage:
        tracker = SourcePerformanceTracker(db)
        results = await tracker.run_scoring_cycle()

    Mirrors sniper_discovery.py's run_discovery_cycle().
    """

    def __init__(self, db):
        self.db = db

    # ── Collection accessors ─────────────────────────────────────────────
    @property
    def arrests(self):
        return self.db["arrests"]

    @property
    def outreach_sequences(self):
        return self.db["outreach_sequences"]

    @property
    def active_bonds(self):
        return self.db["active_bonds"]

    @property
    def payments(self):
        return self.db["payments"]

    @property
    def scraper_status(self):
        return self.db["scraper_status"]

    @property
    def scraper_run_log(self):
        return self.db["scraper_run_log"]

    @property
    def source_perf(self):
        return self.db["source_performance"]

    @property
    def prospective_bonds(self):
        return self.db["prospective_bonds"]

    # ── Main Scoring Cycle ───────────────────────────────────────────────
    async def run_scoring_cycle(self) -> dict:
        """
        Run a full source scoring cycle — equivalent to
        sniper_discovery.run_discovery_cycle().

        Steps:
          1. Gather lead volume per county (30d)
          2. Gather outreach metrics per county (30d)
          3. Gather conversion metrics per county (90d)
          4. Gather revenue metrics per county (90d)
          5. Gather scraper health per county
          6. Compute composite scores
          7. Assign tiers + recommendations
          8. Persist to source_performance collection
          9. Detect trends vs prior scores

        Returns summary dict.
        """
        now = datetime.now(timezone.utc)
        cutoff_30d = (now - timedelta(days=30)).isoformat()
        cutoff_90d = (now - timedelta(days=90)).isoformat()

        # ── Step 1: Lead Volume (30d) ─────────────────────────────────────
        lead_data = await self._get_lead_volume(cutoff_30d)

        # ── Step 2: Outreach Metrics (30d) ────────────────────────────────
        outreach_data = await self._get_outreach_metrics(cutoff_30d)

        # ── Step 3: Conversion Metrics (90d) ──────────────────────────────
        conversion_data = await self._get_conversion_metrics(cutoff_90d)

        # ── Step 4: Revenue Metrics (90d) ─────────────────────────────────
        revenue_data = await self._get_revenue_metrics(cutoff_90d)

        # ── Step 5: Scraper Health ────────────────────────────────────────
        health_data = await self._get_scraper_health()

        # ── Step 6: Load prior scores for trend detection ─────────────────
        prior_scores = {}
        async for doc in self.source_perf.find({}, {"_id": 0, "county": 1, "score": 1}):
            prior_scores[doc["county"]] = doc.get("score", 0)

        # ── Step 7: Compute scores ────────────────────────────────────────
        # Get all unique counties from all data sources
        all_counties = set()
        for d in [lead_data, outreach_data, conversion_data, revenue_data, health_data]:
            all_counties.update(d.keys())

        if not all_counties:
            return {"success": True, "counties_scored": 0, "message": "No data to score"}

        # Compute normalizer: max values across all counties for relative scoring
        max_leads = max((d.get("total", 0) for d in lead_data.values()), default=1) or 1
        max_revenue = max((d.get("revenue_per_lead", 0) for d in revenue_data.values()), default=1) or 1

        scores: list[SourceScore] = []
        for county in sorted(all_counties):
            leads = lead_data.get(county, {})
            outreach = outreach_data.get(county, {})
            conversion = conversion_data.get(county, {})
            revenue = revenue_data.get(county, {})
            health = health_data.get(county, {})

            ss = self._compute_score(
                county=county,
                leads=leads,
                outreach=outreach,
                conversion=conversion,
                revenue=revenue,
                health=health,
                max_leads=max_leads,
                max_revenue_per_lead=max_revenue,
            )

            # Trend detection
            prior = prior_scores.get(county, 0)
            ss.trend_vs_prior = round(ss.score - prior, 1)
            ss.computed_at = now.isoformat()

            # Generate action recommendations
            ss.actions = self._generate_actions(ss)

            scores.append(ss)

        # Sort by score descending (like trading bot's leaderboard)
        scores.sort(key=lambda s: s.score, reverse=True)

        # ── Step 8: Persist ───────────────────────────────────────────────
        for ss in scores:
            await self.source_perf.update_one(
                {"county": ss.county},
                {"$set": ss.to_dict()},
                upsert=True,
            )

        # Summary
        alpha_count = sum(1 for s in scores if s.tier == "alpha")
        growth_count = sum(1 for s in scores if s.tier == "growth")
        prospect_count = sum(1 for s in scores if s.tier == "prospect")
        dormant_count = sum(1 for s in scores if s.tier == "dormant")

        summary = {
            "success": True,
            "counties_scored": len(scores),
            "alpha_counties": alpha_count,
            "growth_counties": growth_count,
            "prospect_counties": prospect_count,
            "dormant_counties": dormant_count,
            "top_5": [{"county": s.county, "score": s.score, "tier": s.tier}
                      for s in scores[:5]],
            "computed_at": now.isoformat(),
        }
        logger.info(
            "✅ SourcePerformanceTracker: %d counties scored — "
            "%d alpha, %d growth, %d prospect, %d dormant",
            len(scores), alpha_count, growth_count, prospect_count, dormant_count,
        )
        return summary

    # ── Data Gathering Helpers ────────────────────────────────────────────
    async def _get_lead_volume(self, cutoff_iso: str) -> dict:
        """Lead volume + hot lead ratio per county (last 30d)."""
        pipe = [
            {"$match": {"scraped_at": {"$gte": cutoff_iso}}},
            {"$group": {
                "_id": "$county",
                "total": {"$sum": 1},
                "hot": {"$sum": {"$cond": [{"$eq": ["$lead_status", "Hot"]}, 1, 0]}},
                "warm": {"$sum": {"$cond": [{"$eq": ["$lead_status", "Warm"]}, 1, 0]}},
                "scored": {"$sum": {"$cond": [
                    {"$and": [{"$ne": ["$lead_score", None]}, {"$gt": ["$lead_score", 0]}]},
                    1, 0
                ]}},
            }},
        ]
        result = {}
        async for doc in self.arrests.aggregate(pipe):
            county = doc["_id"] or "Unknown"
            total = doc["total"]
            result[county] = {
                "total": total,
                "hot": doc["hot"],
                "warm": doc["warm"],
                "scored": doc["scored"],
                "hot_ratio": round(doc["hot"] / max(total, 1), 4),
            }
        return result

    async def _get_outreach_metrics(self, cutoff_iso: str) -> dict:
        """Outreach attempts, replies, conversions per county (last 30d)."""
        pipe = [
            {"$match": {"created_at": {"$gte": cutoff_iso}}},
            {"$group": {
                "_id": "$county",
                "attempts": {"$sum": 1},
                "replied": {"$sum": {"$cond": [{"$eq": ["$status", "replied"]}, 1, 0]}},
                "converted": {"$sum": {"$cond": [{"$eq": ["$status", "converted"]}, 1, 0]}},
                "stopped": {"$sum": {"$cond": [{"$eq": ["$status", "stopped"]}, 1, 0]}},
            }},
        ]
        result = {}
        async for doc in self.outreach_sequences.aggregate(pipe):
            county = doc["_id"] or "Unknown"
            attempts = doc["attempts"]
            result[county] = {
                "attempts": attempts,
                "replied": doc["replied"],
                "converted": doc["converted"],
                "reply_rate": round(doc["replied"] / max(attempts, 1), 4),
                "conversion_rate": round(doc["converted"] / max(attempts, 1), 4),
            }
        return result

    async def _get_conversion_metrics(self, cutoff_iso: str) -> dict:
        """Bonds written per county (last 90d)."""
        pipe = [
            {"$match": {"created_at": {"$gte": cutoff_iso}}},
            {"$group": {
                "_id": "$county",
                "bonds": {"$sum": 1},
                "total_bond_amount": {"$sum": "$bond_amount"},
                "avg_bond": {"$avg": "$bond_amount"},
                "total_premium": {"$sum": "$premium"},
            }},
        ]
        result = {}
        async for doc in self.active_bonds.aggregate(pipe):
            county = doc["_id"] or "Unknown"
            result[county] = {
                "bonds": doc["bonds"],
                "total_bond_amount": doc["total_bond_amount"] or 0,
                "avg_bond": doc["avg_bond"] or 0,
                "total_premium": doc["total_premium"] or 0,
            }
        return result

    async def _get_revenue_metrics(self, cutoff_iso: str) -> dict:
        """Revenue data per county via active_bonds premiums (last 90d)."""
        # Use active_bonds premium as revenue proxy
        pipe = [
            {"$match": {"created_at": {"$gte": cutoff_iso}, "premium": {"$gt": 0}}},
            {"$group": {
                "_id": "$county",
                "total_revenue": {"$sum": "$premium"},
                "bond_count": {"$sum": 1},
            }},
        ]
        result = {}
        async for doc in self.active_bonds.aggregate(pipe):
            county = doc["_id"] or "Unknown"
            bond_count = doc["bond_count"] or 1
            result[county] = {
                "total_revenue": doc["total_revenue"] or 0,
                "bond_count": bond_count,
                "revenue_per_lead": round((doc["total_revenue"] or 0) / bond_count, 2),
            }
        return result

    async def _get_scraper_health(self) -> dict:
        """Scraper uptime and error rates per county."""
        result = {}
        async for doc in self.scraper_status.find({}, {"_id": 0}):
            county = doc.get("county", "")
            if not county:
                continue
            status = doc.get("status", "unknown")
            last_run = doc.get("last_run_at", "")
            if isinstance(last_run, datetime):
                last_run = last_run.isoformat()

            # Compute error rate from run log (last 20 runs)
            total_runs = 0
            error_runs = 0
            async for log_doc in self.scraper_run_log.find(
                {"county": county}
            ).sort("started_at", -1).limit(20):
                total_runs += 1
                if log_doc.get("status") == "error":
                    error_runs += 1

            error_rate = (error_runs / max(total_runs, 1))
            uptime = 1.0 - error_rate

            result[county] = {
                "status": status,
                "last_run_at": last_run,
                "total_recent_runs": total_runs,
                "error_runs": error_runs,
                "error_rate": round(error_rate, 4),
                "uptime_pct": round(uptime * 100, 1),
            }
        return result

    # ── Score Computation ─────────────────────────────────────────────────
    def _compute_score(
        self,
        county: str,
        leads: dict,
        outreach: dict,
        conversion: dict,
        revenue: dict,
        health: dict,
        max_leads: int,
        max_revenue_per_lead: float,
    ) -> SourceScore:
        """
        Compute composite score (0–100) for a county.
        Mirrors trading bot's _calculate_sniper_score().
        """
        # ── Component 1: Lead Volume (relative to top county) ─────────────
        lead_total = leads.get("total", 0)
        score_lead_volume = min(100, (lead_total / max(max_leads, 1)) * 100)

        # ── Component 2: Hot Lead Ratio ───────────────────────────────────
        hot_ratio = leads.get("hot_ratio", 0)
        # Scale: 0% hot = 0, 10%+ hot = 100
        score_hot_ratio = min(100, hot_ratio * 1000)

        # ── Component 3: Outreach Reply Rate ──────────────────────────────
        reply_rate = outreach.get("reply_rate", 0)
        # Scale: 0% reply = 0, 20%+ reply = 100
        score_outreach = min(100, reply_rate * 500)

        # ── Component 4: Conversion Rate (THE key metric) ─────────────────
        bonds = conversion.get("bonds", 0)
        conv_rate = bonds / max(lead_total, 1) if lead_total > 0 else 0
        # Scale: 0% conversion = 0, 5%+ conversion = 100
        score_conversion = min(100, conv_rate * 2000)

        # ── Component 5: Revenue Per Lead ─────────────────────────────────
        rev_per_lead = revenue.get("revenue_per_lead", 0)
        score_revenue = min(100, (rev_per_lead / max(max_revenue_per_lead, 1)) * 100)

        # ── Component 6: Scraper Health ───────────────────────────────────
        uptime_pct = health.get("uptime_pct", 100)
        score_health = uptime_pct  # Already 0–100

        # ── Composite Score (weighted) ────────────────────────────────────
        composite = (
            score_lead_volume * WEIGHTS["lead_volume"]
            + score_hot_ratio * WEIGHTS["hot_lead_ratio"]
            + score_outreach * WEIGHTS["outreach_reply"]
            + score_conversion * WEIGHTS["conversion_rate"]
            + score_revenue * WEIGHTS["revenue_per_lead"]
            + score_health * WEIGHTS["scraper_health"]
        )
        composite = round(min(100, max(0, composite)), 1)

        # ── Tier Assignment ───────────────────────────────────────────────
        tier = "dormant"
        for tier_name, threshold in sorted(
            TIER_THRESHOLDS.items(), key=lambda x: x[1], reverse=True
        ):
            if composite >= threshold:
                tier = tier_name
                break

        return SourceScore(
            county=county,
            score=composite,
            tier=tier,
            # Raw metrics
            lead_volume_30d=lead_total,
            hot_leads_30d=leads.get("hot", 0),
            hot_lead_ratio=round(hot_ratio * 100, 1),
            outreach_attempts=outreach.get("attempts", 0),
            outreach_replies=outreach.get("replied", 0),
            outreach_reply_rate=round(reply_rate * 100, 1),
            bonds_written_90d=bonds,
            conversion_rate=round(conv_rate * 100, 2),
            total_premium_90d=round(conversion.get("total_premium", 0), 2),
            revenue_per_lead=round(rev_per_lead, 2),
            scraper_uptime_pct=uptime_pct,
            scraper_error_rate=round(health.get("error_rate", 0) * 100, 1),
            last_scrape_at=health.get("last_run_at"),
            # Component scores
            score_lead_volume=round(score_lead_volume, 1),
            score_hot_ratio=round(score_hot_ratio, 1),
            score_outreach=round(score_outreach, 1),
            score_conversion=round(score_conversion, 1),
            score_revenue=round(score_revenue, 1),
            score_health=round(score_health, 1),
            # Recommendations
            recommended_frequency_min=FREQUENCY_RECOMMENDATIONS.get(tier, 360),
            recommended_outreach_tier=OUTREACH_TIER_MAP.get(tier, "none"),
        )

    # ── Action Recommendations ────────────────────────────────────────────
    def _generate_actions(self, ss: SourceScore) -> list[str]:
        """
        Generate actionable recommendations based on score components.
        Mirrors the trading bot's promotion/demotion logic.
        """
        actions = []

        # High-value county not being scraped frequently enough
        if ss.tier == "alpha" and ss.scraper_uptime_pct < 95:
            actions.append(f"⚠️ Fix scraper reliability — {ss.county} is alpha-tier but only {ss.scraper_uptime_pct}% uptime")

        # Good conversion but low volume — could benefit from more leads
        if ss.conversion_rate > 3.0 and ss.lead_volume_30d < 100:
            actions.append(f"📈 Increase scrape frequency — {ss.county} converts well ({ss.conversion_rate}%) but low lead volume")

        # High volume but low conversion — outreach needs work
        if ss.lead_volume_30d > 200 and ss.conversion_rate < 0.5:
            actions.append(f"🎯 Review outreach strategy — {ss.county} has volume ({ss.lead_volume_30d} leads) but {ss.conversion_rate}% conversion")

        # No outreach happening in a county with hot leads
        if ss.hot_leads_30d > 10 and ss.outreach_attempts == 0:
            actions.append(f"🔥 Enable outreach — {ss.county} has {ss.hot_leads_30d} hot leads with zero outreach attempts")

        # Outreach happening but no replies — template or targeting issue
        if ss.outreach_attempts > 20 and ss.outreach_reply_rate < 2.0:
            actions.append(f"💬 Review outreach templates — {ss.county} has <2% reply rate on {ss.outreach_attempts} attempts")

        # Strong trend upward
        if ss.trend_vs_prior > 10:
            actions.append(f"🚀 Momentum detected — {ss.county} score up +{ss.trend_vs_prior}pts from last cycle")

        # Significant decline
        if ss.trend_vs_prior < -10:
            actions.append(f"📉 Score declining — {ss.county} down {abs(ss.trend_vs_prior)}pts — investigate scraper/outreach issues")

        # Dormant county with any leads at all
        if ss.tier == "dormant" and ss.lead_volume_30d > 50:
            actions.append(f"🔍 Investigate — {ss.county} is dormant-tier but scraping {ss.lead_volume_30d} leads/month")

        return actions

    # ── Leaderboard Query ─────────────────────────────────────────────────
    async def get_leaderboard(self, limit: int = 50) -> list[dict]:
        """Return all counties ranked by source score."""
        result = []
        async for doc in self.source_perf.find(
            {}, {"_id": 0}
        ).sort("score", -1).limit(limit):
            result.append(doc)
        return result

    async def get_county_detail(self, county: str) -> Optional[dict]:
        """Return detailed score breakdown for a single county."""
        doc = await self.source_perf.find_one(
            {"county": {"$regex": f"^{county}$", "$options": "i"}},
            {"_id": 0}
        )
        return doc

    async def get_tier_summary(self) -> dict:
        """Return count of counties per tier."""
        pipe = [
            {"$group": {"_id": "$tier", "count": {"$sum": 1}, "avg_score": {"$avg": "$score"}}},
            {"$sort": {"avg_score": -1}},
        ]
        tiers = {}
        async for doc in self.source_perf.aggregate(pipe):
            tiers[doc["_id"]] = {
                "count": doc["count"],
                "avg_score": round(doc["avg_score"], 1),
            }
        return tiers

    # ── Feedback Recording (mirrors trading bot's record_copy_signal_result) ─
    async def record_conversion(
        self,
        county: str,
        booking_number: str,
        bond_amount: float,
        premium: float,
        channel: str = "scraper",
    ) -> None:
        """
        Record a successful conversion (bond written) for a county.
        This is the feedback signal that closes the loop.

        Called when a bond case transitions to 'active' status.
        """
        await self.db["conversion_events"].insert_one({
            "county": county,
            "booking_number": booking_number,
            "bond_amount": bond_amount,
            "premium": premium,
            "channel": channel,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        })
        logger.info(
            "📊 Conversion recorded: %s (%s) — bond=$%.0f premium=$%.0f",
            county, booking_number, bond_amount, premium,
        )

    # ── System Summary ────────────────────────────────────────────────────
    async def get_system_stats(self) -> dict:
        """High-level alpha engine KPIs for dashboard header."""
        total = await self.source_perf.count_documents({})
        if total == 0:
            return {
                "total_counties": 0,
                "alpha_counties": 0,
                "avg_score": 0,
                "total_conversions_90d": 0,
                "status": "no_data",
            }

        pipe_avg = [{"$group": {"_id": None, "avg": {"$avg": "$score"}}}]
        avg_result = await self.source_perf.aggregate(pipe_avg).to_list(1)
        avg_score = round(avg_result[0]["avg"], 1) if avg_result else 0

        alpha = await self.source_perf.count_documents({"tier": "alpha"})
        growth = await self.source_perf.count_documents({"tier": "growth"})

        # Total conversions (from conversion_events)
        cutoff_90d = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
        conversions = await self.db["conversion_events"].count_documents(
            {"recorded_at": {"$gte": cutoff_90d}}
        )

        return {
            "total_counties": total,
            "alpha_counties": alpha,
            "growth_counties": growth,
            "avg_score": avg_score,
            "total_conversions_90d": conversions,
            "status": "active",
        }
