# ShamrockLeads — Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [Unreleased] — 2026-10-07 (Telegram and Shannon intake tags)

### Added
- `POST /api/intake/submit` keeps `telegram_miniapp` and `shannon_voice` as their own source tags. Older `telegram`, `telegram_mini_app`, `shannon`, and `elevenlabs_voice` values stay as they were. `shannon_voice` uses the same voice match skip as Shannon (the matcher runs from the desk, not inside the call) and the website pay-by-card link. `telegram_miniapp` still uses the Telegram pay link and still runs match-review on submit.

## [Unreleased] — 2026-10-08 (start bond packet key)

### Fixed
- **Start bond packet (`SAAS_MULTI_TENANT` still default off).** The `paperwork_packets` upsert is keyed on `packet_id` (`idx_pkt_packet_id`), with `created_at` only in `$setOnInsert`. A signed or voided packet that shares the bond case is left in place.
