---
name: seo-audit
description: "Comprehensive SEO auditing framework covering crawlability, indexation, speed, on-page optimization, and content quality. Use for auditing any Shamrock page or county-specific content."
source: "coreyhaines31/marketingskills/seo-audit"
---

# SEO Audit

Identify SEO issues and provide actionable recommendations.

## Priority Order
1. Crawlability & Indexation
2. Technical Foundations  
3. On-Page Optimization
4. Content Quality
5. Authority & Links

## Schema Markup Detection
`web_fetch` cannot detect JS-injected JSON-LD. Use browser tool with `document.querySelectorAll('script[type="application/ld+json"]')` or Google Rich Results Test.

## Crawlability Checklist
- Robots.txt: no unintentional blocks, sitemap reference
- XML Sitemap: exists, canonical URLs only, updated
- Architecture: pages within 3 clicks, no orphans

## Indexation Checklist
- `site:domain.com` check vs expected
- No noindex on important pages
- Canonicals correct, no redirect chains
- Self-referencing canonicals on unique pages

## Core Web Vitals
- LCP < 2.5s, INP < 200ms, CLS < 0.1

## On-Page
- Unique titles (50-60 chars), keyword near beginning
- Unique meta descriptions (150-160 chars)
- One H1 per page with primary keyword
- Keyword in first 100 words
- Alt text on all images, WebP format
- Descriptive anchor text for internal links

## E-E-A-T
- Experience, Expertise, Authoritativeness, Trustworthiness
- Author credentials, original data, HTTPS, contact info

## Local Business
- Consistent NAP, local schema, GBP optimization

## Deliverable: Executive Summary + prioritized action plan with impact levels.
