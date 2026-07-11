# Codex Handoff Prompt: Georgia Expansion Phase 1c

**Context:**
We are Shamrock Bail Bonds, building a multi-state bail bond Auto-CRM and arrest intelligence engine (`shamrock-leads`). We have successfully scaled to 52 Florida counties and are now actively expanding into the Georgia market (159 counties total). 

**Current State:**
We have just completed the initial recon and foundation-building for Georgia. 
- All documentation (`README.md`, `ROADMAP.md`, `STATUS.md`, `DATA_MODEL.md`, `AGENTS.md`, `GEMINI.md`) has been updated to reflect the Georgia expansion.
- The `GEORGIA_COUNTY_REGISTRY.md` has been created, mapping out the JMS platforms for all 159 counties.
- We have built 5 new reusable base scrapers specifically for Georgia platforms: `EASBaseScraper`, `ZuercherBaseScraper`, `SouthernSWBaseScraper`, `SocrataBaseScraper`, and `XMLFeedBaseScraper`.
- We currently have 38 active Georgia counties covered via 9 scraper files in `scrapers/counties_ga/` (including a batch runner for 27 EAS counties).

**Your Mission:**
Continue the Phase 1c Georgia expansion by tackling the remaining high-value "easy pickings" scrapers and organizing the data structure for the Georgia market.

**Specific Tasks:**

1. **Track A (Easy Wins - Base Class Reuse):**
   - Build Zuercher scrapers for Houston, Floyd, and Catoosa counties. (Use `zuercher_base.py`; this should be 3-4 lines of code each, similar to `douglas.py`).
   - Build Southern Software scrapers for Decatur, Lee, and Oglethorpe counties. (Use `southern_sw_base.py`; similar to `banks.py`).

2. **Track B (High-Value Custom HTML Scrapers):**
   - Build custom HTML scrapers for the confirmed live portals in these high-population counties:
     - Cobb County (Pop: ~770K)
     - Gwinnett County (Pop: ~960K)
     - Richmond County / Augusta (Pop: ~202K)
     - Glynn County / Brunswick (Pop: ~85K)
   - These will require individual `BaseScraper` implementations with custom `requests` + `BeautifulSoup` parsing logic. Ensure robust error handling and proper data mapping to our 39-column `ArrestRecord` schema.

3. **Data Organization & Schema Updates:**
   - Review the `ArrestRecord` schema in `core/models.py`. Ensure it optimally supports Georgia-specific data nuances discovered during recon (e.g., handling missing mugshots due to O.C.G.A. § 35-1-19 compliance).
   - If any structural changes are needed to better organize the Georgia data for our Auto-CRM pipeline (scoring, matching, intake), implement them while maintaining backward compatibility with the Florida scrapers.

**Constraints & Guidelines:**
- **Strict Identity:** We are Shamrock2245. Never reference WTF or any other identity.
- **Idempotency:** Always dedup by `county` + `booking_number`.
- **Quality:** Ensure every new scraper implements `BaseScraper.run()` correctly, including scoring and Slack alerting.
- **Documentation:** Update `docs/GEORGIA_COUNTY_REGISTRY.md` as you activate new counties.
- **Autonomy:** If you encounter minor bugs or missing dependencies while building these scrapers, fix them autonomously. Prioritize action and working code over discussion.

Let's finish the factory. Begin by analyzing the existing base classes and executing Track A.
