# Ohio Guarded Pilot — County Source Contracts

> **Status:** Registered for visible, fail-closed health reporting only. These entries are **not** approved for source retrieval, record persistence, scoring, Slack alerts, outreach, matching, bond writing, paperwork, signatures, or payments.
>
> **Method:** Passive, ordinary public-access reconnaissance. No person-level records, images, profile pages, sequential identifiers, authentication, CAPTCHA bypass, or source-control workaround were used or retained.

Ohio is outside the repository's established OSI/Palmetto state-footprint configuration. Accordingly, this document makes **no** licensing, appointment, court-registration, POA, or bond-writing assertion. Any operational promotion requires separate business, surety, and legal approval.

## Pilot registry

| County | Dashboard label / job ID | Official landing source | Observed posture | Runtime source state |
|---|---|---|---|---|
| Clermont | `Clermont (OH)` / `scraper_oh_clermont` | [Clermont County Sheriff jail inmate search](https://www.clermontsheriff.org/jail-inmate-search/) | Public roster-style page was observed, but no automated-use authorization, field-retention approval, or production schema validation is documented. | `fail_closed` |
| Clinton | `Clinton (OH)` / `scraper_oh_clinton` | [Clinton County Sheriff active inmates](https://clintonsheriff.com/active-inmates/) | Public roster-style page was observed, but no automated-use authorization, field-retention approval, or production schema validation is documented. | `fail_closed` |
| Huron | `Huron (OH)` / `scraper_oh_huron` | [Huron County Sheriff jail roster](https://huroncountysheriff.com/jailroster/) | Public roster-style page was observed, but no automated-use authorization, field-retention approval, or production schema validation is documented. | `fail_closed` |

## Promotion requirements

A source-specific promotion must be a new, reviewed change and satisfy **all** of the following. A green parser test or a successful HTTP response is not enough.

1. **Operating authorization.** Record the owner-approved source/terms determination and an accountable reviewer. No CAPTCHA, login, WAF, app-only surface, robots prohibition, or other access-control boundary may be bypassed.
2. **Source contract.** Through normal public access, establish a bounded broad listing that supplies complete displayed identity, a source-issued immutable booking/inmate identifier, booking or arrest date/time, known pagination/coverage limits, and a stable official source URL.
3. **Data minimization.** Define a per-source field allowlist and suppression rules. Do not retain images, unneeded demographics, protected/restricted information, phone numbers, addresses, SSNs, or raw page content.
4. **Attribution.** Prove the record is attributable to the named Ohio county. A regional facility needs a documented county/facility or arresting-agency rule; ambiguity fails closed.
5. **Safe adapter.** Implement fixture-only parser tests using synthetic data, source-issued booking keys only, idempotent `County + Booking_Number` handling with state `OH`, bounded pagination, error classification, and a per-source kill switch.
6. **Staged verification.** Begin with a no-write, aggregate-only observation. Do not claim a source is productive until staff confirms its source provenance and no PII leakage, identity ambiguity, parser drift, or duplicate issue is present.
7. **Human-gated workflow.** A promoted source may create only an `ArrestLead`; no automated match, outreach, surety/POA decision, bond case, paperwork, signature, payment, or full-auto revenue action is allowed.

## References

- [Ohio Department of Insurance — Surety Bail Bond Individual Agent](https://insurance.ohio.gov/wps/portal/gov/odi/agents-and-agencies/individual-agent/surety-bail-bond-individual-agent)
- [Ohio Revised Code §3905.87 — Registration of agent with court clerks](https://codes.ohio.gov/ohio-revised-code/section-3905.87)
- [Ohio Revised Code §3905.90 — Records of surety bonds](https://codes.ohio.gov/ohio-revised-code/section-3905.90)
