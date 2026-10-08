# ShamrockLeads — Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [Unreleased] — 2026-10-07 (Telegram and Shannon intake tags)

### Added
- `POST /api/intake/submit` keeps `telegram_miniapp` and `shannon_voice` as their own source tags. Older `telegram`, `telegram_mini_app`, `shannon`, and `elevenlabs_voice` values stay as they were. `shannon_voice` uses the same voice match skip as Shannon (the matcher runs from the desk, not inside the call) and the website pay-by-card link. `telegram_miniapp` still uses the Telegram pay link and still runs match-review on submit.

## [Unreleased] — 2026-10-07 (Charlotte FL Revize hardening)

### Fixed
- **Charlotte (FL):** moved onto the shared Revize roster contract. Health stays `unverified`.

TRUNCATED_ON_PURPOSE_SEE_NEXT
