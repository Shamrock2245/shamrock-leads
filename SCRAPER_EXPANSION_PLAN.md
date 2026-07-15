# ShamrockLeads Scraper Expansion Plan (2026-07)

## Overview
This document outlines the reconnaissance and architectural blueprints for expanding the ShamrockLeads ecosystem into six new states: **Tennessee, Connecticut, Texas, Louisiana, Alabama, and Mississippi**. The goal is to ingest high-intent arrest data while adhering to the "Chain Is Law" and "Idempotent Writes" axioms.

---

## 1. Tennessee (TN)
**Target Sources:** TnCIS (Administrative Office of the Courts), Shelby County, Davidson County.

### Ecosystem Mapping
| Target | Vendor / System | Access Method | Scraping Complexity |
| :--- | :--- | :--- | :--- |
| **TnCIS (80+ Counties)** | LGC (TnCIS Web Inquiry) | HTTPS / Playwright | **High** (Cloudflare Protected) |
| **Shelby County (Memphis)** | Custom Portal | HTTPS / DrissionPage | **Medium** |
| **Davidson County (Nashville)** | Custom Portal | HTTPS / DrissionPage | **Medium** |

### Implementation Blueprint
- **TnCIS Scraper:** Must utilize `_get_obscura_browser()` due to Cloudflare. Target the `lgc-tn.com/tncis-web-inquiry/` portal.
- **Data Schema:** Primary key: `Case_Number` + `County`.
- **Base Class:** Inherit from `BaseScraper`.

---

## 2. Connecticut (CT)
**Target Sources:** CT Judicial Branch (Criminal/Motor Vehicle Case Look-up).

### Ecosystem Mapping
| Target | Vendor / System | Access Method | Scraping Complexity |
| :--- | :--- | :--- | :--- |
| **Statewide Criminal** | CT Judicial Portal | HTTPS / Playwright | **Medium** |
| **Statewide Pending** | CT Judicial Portal | HTTPS / Playwright | **Medium** |

### Implementation Blueprint
- **CT Judicial Scraper:** Navigate to `jud.ct.gov/crim.htm`. Scrape "Pending Cases" and "Daily Dockets".
- **Stealth Requirement:** Use randomized viewports and `_inject_stealth_js()` to avoid detection on the state portal.
- **Data Schema:** Primary key: `Docket_Number`.

---

## 3. Texas (TX)
**Target Sources:** Tyler Technologies (Odyssey), SmartCop, various county-specific portals.

### Ecosystem Mapping
| Target | Vendor / System | Access Method | Scraping Complexity |
| :--- | :--- | :--- | :--- |
| **Odyssey Counties (Large)** | Tyler Technologies | HTTPS / Playwright | **High** (Amazon WAF / Captcha) |
| **SmartCop Counties** | SmartJail | HTTPS / DrissionPage | **Medium** |
| **Harris County** | Custom | HTTPS / DrissionPage | **Medium** |

### Implementation Blueprint
- **Odyssey Scraper:** Utilize the `tylertech.cloud` portals. Requires `_get_obscura_browser()` and potential CAPTCHA solving via `recaptcha_audio_solver.py`.
- **SmartJail Scraper:** Target the `smartcop.com` standard layouts.
- **Data Schema:** Primary key: `Booking_Number` + `County`.

---

## 4. Louisiana (LA)
**Target Sources:** LA VINE (Statewide), Orleans Parish, Lafayette Parish.

### Ecosystem Mapping
| Target | Vendor / System | Access Method | Scraping Complexity |
| :--- | :--- | :--- | :--- |
| **Statewide VINE** | Appriss (VINE) | HTTPS / Playwright | **High** (Strict Bot Detection) |
| **Orleans Parish** | Custom (OPSO) | HTTPS / DrissionPage | **Medium** |
| **Lafayette Parish** | 365Labs | HTTPS / DrissionPage | **Medium** |

### Implementation Blueprint
- **LAVINE Scraper:** Highly sensitive to automation. Use `curl_cffi` or `nodriver` for TLS fingerprinting.
- **Parish-Specific:** Orleans (OPSO) and Lafayette (365Labs) are the highest volume leads.
- **Data Schema:** Primary key: `Booking_Number` + `Parish`.

---

## 5. Alabama (AL)
**Target Sources:** Alacourt.com (Statewide), County Jail Portals.

### Ecosystem Mapping
| Target | Vendor / System | Access Method | Scraping Complexity |
| :--- | :--- | :--- | :--- |
| **Alacourt (Statewide)** | On-Demand (Paid) | HTTPS / Playwright | **High** (Paywall/Auth) |
| **County Jails** | Various | HTTPS / DrissionPage | **Medium** |

### Implementation Blueprint
- **Alacourt Integration:** Requires account credentials. Use `Atomic Rotation` for session management as per project instructions.
- **County Focus:** Prioritize Jefferson, Mobile, and Madison counties.
- **Data Schema:** Primary key: `Case_Number` + `County`.

---

## 6. Mississippi (MS)
**Target Sources:** MEC (Mississippi Electronic Courts), Jackson County, Hinds County.

### Ecosystem Mapping
| Target | Vendor / System | Access Method | Scraping Complexity |
| :--- | :--- | :--- | :--- |
| **MEC (Statewide)** | Custom (MEC) | HTTPS / Playwright | **Medium** (Auth Required) |
| **Jackson County** | Custom | HTTPS / DrissionPage | **Low** |
| **Hinds County** | Custom | HTTPS / DrissionPage | **Low** |

### Implementation Blueprint
- **MEC Scraper:** Target the `courts.ms.gov/mec/` portal. Requires user authentication.
- **Kologik Integration:** Many MS counties use Kologik/Coreforce; target their citizen portals.
- **Data Schema:** Primary key: `Case_Number` + `County`.

---

## Core System Axioms & Compliance
1. **The Chain Is Law:** All scraped data must map to `ArrestRecord` model and flow into the `LeadScorer`.
2. **Idempotent Writes:** Always use `Booking_Number` + `County` as the unique identifier for MongoDB `upsert` operations.
3. **Fail Closed:** If a scraper encounters a selector change, it must log a critical error to `ErrorTracker` and notify Slack without polluting the DB with partial data.
4. **Stealth First:** All new scrapers should default to `_get_browser_options()` and `_inject_stealth_js()` to ensure long-term stability.

---
**Prepared by:** Manus AI Agent
**Project:** shamrock-leads
