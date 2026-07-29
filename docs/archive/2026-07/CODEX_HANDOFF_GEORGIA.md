# Codex Handoff Prompt: Georgia Expansion Phase 1c (Track C)

**Context:**
We are Shamrock Bail Bonds, building a multi-state bail bond Auto-CRM and arrest intelligence engine (`shamrock-leads`). We have successfully scaled to 52 Florida counties and 48 Georgia counties (100 total active scrapers).

**Current State:**
We have completed Track A (Base Class Reuse) and Track B (Custom HTML) for Georgia.
- All 100 scrapers are registered in `main.py` with the APScheduler.
- The `GEORGIA_COUNTY_REGISTRY.md` maps out the JMS platforms for all 159 counties.
- We have 5 base scrapers for Georgia: `EASBaseScraper`, `ZuercherBaseScraper`, `SouthernSWBaseScraper`, `SocrataBaseScraper`, and `XMLFeedBaseScraper`.

**Your Mission:**
Continue the Phase 1c Georgia expansion by executing Track C: Deep Recon and implementation for the remaining ~111 Georgia counties.

**Specific Tasks:**
1. **Track C (Deep Recon):**
   - Systematically discover the inmate search portals for the remaining 111 Georgia counties.
   - Use the Georgia Sheriffs' Association directory or targeted web searches to find the active URLs.
   - Identify the JMS platform for each discovered portal (e.g., JailTracker, SmartCOP, custom HTML).

2. **Scraper Implementation:**
   - Build new scrapers for the discovered portals using existing base classes wherever possible.
   - For unknown platforms, build custom `requests` + `BeautifulSoup` parsers or use stealth packages (`curl_cffi`, `nodriver`) if anti-bot measures are present.
   - Ensure robust error handling and proper data mapping to our 39-column `ArrestRecord` schema.

3. **Scheduler & Registry:**
   - Register all new scrapers in `main.py` with appropriate intervals.
   - Update `docs/GEORGIA_COUNTY_REGISTRY.md` to mark newly built counties as Active.

**Constraints & Guidelines:**
- **Strict Identity:** We are Shamrock2245. Never reference WTF or any other identity.
- **Idempotency:** Always dedup by `county` + `booking_number`.
- **Quality:** Ensure every new scraper implements `BaseScraper.run()` correctly, including scoring and Slack alerting.
- **Autonomy:** If you encounter minor bugs or missing dependencies while building these scrapers, fix them autonomously. Prioritize action and working code over discussion.

Let's finish the factory. Begin by analyzing the remaining counties in the registry and starting the deep recon process.
