# Production readiness audit — 2026-08-31

> Scope: `Shamrock2245/shamrock-leads` (platform omnirepo) plus sibling-repo
> pointers already recorded in `docs/ECOSYSTEM.md`.
> Method: documentation + tree + workflow + STATUS/checklist review.
> No live BondCase, packet, payment, or client message was created.

## Verdict

**Stage 2 production-hardened is still not earned.** That is an honest
platform-law result, not a code desert. The Auto-CRM on `main` is large,
tested, fail-closed in the write paths that matter, and already live on
`leads.shamrockbailbonds.biz`. What remains is operator proof plus a few
ops/security items no agent can close from a clean checkout.

Do not flip `full_auto`. D3's 7-day `review` clock started **2026-08-28**
and remains in force through **2026-09-04**.

## What is already production-grade in repo

| Area | Evidence |
|------|----------|
| Bond chain code | Phases 1–17 implemented; DocuSeal is the only active e-sign path |
| Source safety | Shared pre-scrape contract gate; OH/CT docket/LA/NC/SC/TN fail-closed rows |
| Paperwork | Packet must originate from a validated BondCase; ID-scan shortcut fail-closed |
| Outreach | iMessage delivery packet-bound, one-time, HTTPS signer links only |
| Tests | Large focused suite (source-contract, paperwork, matching, secrets, Palantir) |
| Deploy | Hetzner workflow on `main`; health probes historically `200` |
| Docs corpus | STATUS, PLATFORM, ECOSYSTEM checklist, runbooks, county registries |

## Open Stage 2 gates (human)

| Gate | State as of 2026-08-31 | Who |
|------|------------------------|-----|
| **B3** | Path believed live; no documented real BondCase smoke / correlation ID | Staff on next validated case — `docs/runbooks/B3_WRITE_BOND_FORWARD_SMOKE_RUNBOOK.md` |
| **B5** | Templates exist; one staff-approved OSI + Palmetto packet not logged | Staff |
| **C3** | Owner-deferred rotation of historical git secrets | Brendan — `docs/ops/C3_SECRET_ROTATION_APPROVAL_PACKAGE.md` |
| **E3** | Telegram Palmetto template ID vs `DOCUSEAL_TEMPLATE_ID_PALMETTO` | Ops |
| **P1.8** | Gmail Pub/Sub push 503 fail-closed until VPS env + GCP subscription | Ops |
| **D3** | Keep automations in `review` until 2026-09-04 | Product |

## Engineering gaps closed in this change

1. **CI on PRs** — `.github/workflows/ci.yml` byte-compiles application packages and runs the fail-closed / contract suite. Deploy-to-Hetzner stays separate and must not be the only gate.
2. **Dependabot** — weekly pip + Actions, monthly Docker.
3. **CODEOWNERS** — default + money/trust paths.
4. **Public PIN redaction** — README no longer publishes the God-Admin PIN.
5. **ROADMAP count drift** — registered-scraper totals aligned to `STATUS.md` (2026-08-14 scale table).
6. **SECURITY.md** — production-hardening backlog that was documented but easy to miss (Atlas IP allowlist, disk growth, branch protection).

## Engineering / ops gaps that remain (do not claim closed)

| Priority | Item | Why it matters |
|----------|------|----------------|
| P0 | Grow VPS root disk from ~38 GB to 160–240 GB | CCX33 CPU/RAM resize did not grow the volume; Chromium + images will fill the disk |
| P0 | Restrict MongoDB Atlas network access (currently called out as `0.0.0.0/0`) | Compensating control after C3 rotation |
| P0 | Enable GitHub branch protection: required `CI` check, no force-push to `main` | Stops broken contract tests from auto-deploying |
| P1 | Paperwork leftovers: selfie enforcement, staff second-PIN exception ceremony, multi-indemnitor walkthrough, collateral receipt serial OCR (never invent a serial) | Locked spec, not blockers for SWFL write |
| P1 | Per-source Mongo upsert + alert telemetry for most non-FL registered scrapers | Registration ≠ emitting |
| P2 | Phase 21 phone→autopilot state machine | Explicitly not a production blocker |
| P2 | Enable `full_auto` | Forbidden until D3 clock + metrics |

## Sibling repos (omnirepo surface)

Reviewed as inventory only in this pass. Authoritative per-repo truth stays in each `STATUS.md`.

| Repo | Role | Prod note |
|------|------|-----------|
| `shamrock-leads` | Bond Auto-CRM | This audit |
| `shamrock-bail-portal-site` | Brand / clipboard | Factory **V468 / @468**; do not mint a new `/exec` URL |
| `shamrock-bail-school` | LMS | School P0 A1–A8 marked done |
| `shamrock-telegram-app` | Mini-app intake | E3 template verify still open |
| `shamrock-node-red` | Automation fabric | P1.5 marked done |
| `shamrock-bond-tracker` | GPS / risk geo | Separate private tracker |
| `swfl-arrest-scrapers` | Legacy SWFL scrapers | Do not treat as current writer path |

## Definition of done for Stage 2

Unchanged from `docs/ECOSYSTEM_PROD_CHECKLIST.md`:

A1–A5, B1–B7, C1–C4, D1–D2 all `[x]` **and** live-proven. After this audit:
D2 is `[x]`; B3, B5, C3, E3, P1.8 are not. Do not update `docs/PLATFORM.md`
maturity to Stage 2 until those gates are logged with evidence.
