# SC Lancaster / York — honest identity recon (2026-10-15)

Probes (one each, honest UA, TLS verified, from the box; no names/DOBs logged):
- Lancaster `inmate.lancastercountysc.net/.../SC0290000?InCustody=True` → `EgressBlocked connection_reset` (TLS handshake dropped before a certificate was presented).
- York roster → `EgressBlocked connection_reset` (same).

So the live roster could not be re-verified from the box. Changes rely on the code, existing fixtures and the 2026-10-07 Lancaster recon.

Status rules:
- Lancaster: the roster is requested with the source's own `InCustody=True` filter; status is "In Custody" only when the returned page echoes that checkbox as checked (same rule CoS approved for Flagler), or from an InCustody column. Otherwise "".
- York: the Release Date cell publishes "*In Jail" for open bookings → "In Custody"; a published release date → "Released"; otherwise "".

fail_closed: neither county is switched. Both are egress-blocked from the box; a Leads Ops VPS read is needed to confirm whether production also sees resets (CoS decision).
