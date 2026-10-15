# FL rule-conflict fail_closed: Hillsborough and Marion (2026-10-14)

Found in the 2026-10-14 scraper health snapshot. Both modules can only reach
their source by breaking a standing rule, so both are set to fail_closed
(`SOURCE_CONTRACT_VALIDATED = False` + Health `SCRAPER_SOURCE_STATES`). This is
a proposal in a PR; CoS decides at merge.

| County | Vol rank | What the module did | Rule it breaks |
|---|---:|---|---|
| Hillsborough (FL) | 4 | Logs in with `HCSO_EMAIL` / `HCSO_PASSWORD`, solves reCAPTCHA v2 through SolveCaptcha, falls back to a SOCKS / residential proxy | CAPTCHA solving is owner-approved only for Broward and Lake; no proxies; public unauthenticated sources only |
| Marion (FL) | 18 | Reaches `jail.marionso.com` (AWS WAF) only through a residential / Tailscale proxy with curl_cffi Chrome impersonation | No proxies, no impersonation |

No live request was made for this change. No stored data is touched; rows
already written stay as they are (any cleanup waits on Brendan).

Reopen path: a public, unauthenticated, directly reachable roster with a source
booking number, proven by a plain read and then a Leads Ops write smoke. For
Hillsborough that likely means the owner adds it to the approved-CAPTCHA list
(owner decision) or HCSO publishes an open listing. For Marion, a plain direct
read from the Leads Ops home relay (stock client, honest User-Agent) could be
tried if CoS approves, like Pinellas.
