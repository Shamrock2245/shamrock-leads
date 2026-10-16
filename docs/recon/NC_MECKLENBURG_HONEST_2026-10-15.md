# NC Mecklenburg — honest identity recon (2026-10-15)

Probe (one, honest UA, TLS verified, from the box; nothing personal logged): `GET /Inmate` landing → **HTTP 403** (`EgressBlocked`). No API call was reached.

Status: the roster uses the source's `activeOnly=true` search parameter. That is not a published custody label: neither the 2026-10-07 recon nor the fixtures show a per-person custody field (`ImprisonmentStatus` is a prisoner type such as "STATE INMATE"), and "active" was never shown to equal "in custody". So Status is "" with no status_source. If a VPS read shows the site labels the view as current inmates/in custody, a follow-up can set `status_source=published_active_only_view`.

Booking key: source JID (`YY-NNNNNN`), with ArrestNumber only when JID is absent — both source-issued.

fail_closed: not switched in this PR. The box gets a 403; a Leads Ops VPS read is needed to see whether production is also blocked (CoS decision).
