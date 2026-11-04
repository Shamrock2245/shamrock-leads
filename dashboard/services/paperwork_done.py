"""Public /done screen after a signer finishes DocuSeal paperwork.

The page tells the signer what is done, which roles still have to sign,
and — only when the existing invoice safeguards allow it — the locked-amount
SwipeSimple pay link already stored on the bond. The amount is never read
from the query string. A lookup failure renders a generic page with no
pay link.

No DocuSeal email/SMS and no SwipeSimple HTTP call happen here. The link
is the one the payments code already proved.
"""
from __future__ import annotations

import html
import logging
import re
from typing import Any, Optional

logger = logging.getLogger(__name__)

_PACKET_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,79}$")
_AMOUNT = re.compile(r"^\d+\.\d{2}$")
_SIGNED = frozenset({"signed", "completed", "complete"})
_DEAD = frozenset({"voided", "cancelled", "canceled", "declined", "unverified"})
_PACKET_VOID = frozenset({"voided", "cancelled", "canceled"})
_PAY_ROLES = frozenset({"indemnitor", "coindemnitor"})
_LABELS = {
    "indemnitor": "Indemnitor",
    "coindemnitor": "Co-indemnitor",
    "defendant": "Defendant",
}

_EMPTY: dict[str, Any] = {
    "loaded": False,
    "blocked": False,
    "completed_roles": [],
    "pending_roles": [],
    "viewer_label": "",
    "viewer_signed": False,
    "pay_url": None,
    "pay_amount": "",
    "nothing_due": False,
    "already_paid": False,
}


def _norm_role(role: Optional[str]) -> str:
    from dashboard.services.paperwork_signers import normalize_role

    raw = str(role or "").strip()
    if not raw:
        return ""
    key = normalize_role(raw)
    if key == "co_indemnitor":
        return "coindemnitor"
    return key


def _submitters(packet: dict) -> list[dict]:
    rows = packet.get("docuseal_submitters") or packet.get("submitters") or []
    return [row for row in rows if isinstance(row, dict)]


def _packet_voided(packet: dict) -> bool:
    if packet.get("voided") is True:
        return True
    return str(packet.get("status") or "").strip().lower() in _PACKET_VOID


def _packet_fully_signed(packet: dict) -> bool:
    status = str(packet.get("status") or packet.get("docuseal_status") or "").strip().lower()
    return status in _SIGNED


def _completed_role_keys(packet: dict) -> set[str]:
    done: set[str] = set()
    for raw in packet.get("docuseal_completed_roles") or []:
        key = _norm_role(str(raw))
        if key:
            done.add(key)
    parties = packet.get("docuseal_parties")
    if isinstance(parties, dict):
        for raw, info in parties.items():
            if isinstance(info, dict) and str(info.get("completed_at") or "").strip():
                key = _norm_role(str(raw))
                if key:
                    done.add(key)
    return done


def _role_status(packet: dict, role: str) -> str:
    key = _norm_role(role)
    for row in _submitters(packet):
        if _norm_role(str(row.get("role") or "")) == key:
            return str(row.get("status") or "").strip().lower()
    return ""


def _role_dead(packet: dict, role: str) -> bool:
    return _role_status(packet, role) in _DEAD


def _role_signed(packet: dict, role: str) -> bool:
    if _role_dead(packet, role):
        return False
    if _role_status(packet, role) in _SIGNED:
        return True
    if _norm_role(role) in _completed_role_keys(packet):
        return True
    return _packet_fully_signed(packet) and _norm_role(role) in _LABELS


def _text(doc: Optional[dict], *keys: str) -> str:
    if not isinstance(doc, dict):
        return ""
    for key in keys:
        value = str(doc.get(key) or "").strip()
        if value:
            return value
    return ""


async def _load_bond(packet: dict) -> Optional[dict]:
    from dashboard.extensions import get_collection

    case_id = _text(packet, "bond_case_id", "Bond_Case_ID")
    booking = _text(packet, "booking_number", "Booking_Number")
    clauses: list[dict] = []
    if case_id:
        clauses.extend([{"bond_case_id": case_id}, {"Bond_Case_ID": case_id}])
    if booking:
        clauses.extend([{"booking_number": booking}, {"Booking_Number": booking}])
    if not clauses:
        return None
    query: dict = {"$or": clauses} if len(clauses) > 1 else clauses[0]
    for name in ("bond_cases", "active_bonds"):
        doc = await get_collection(name).find_one(query)
        if isinstance(doc, dict):
            return doc
    return None


def _party_for_role(packet: dict, bond: Optional[dict], role: str) -> Optional[dict]:
    from dashboard.services.identity_verification_service import packet_parties

    want = "co_indemnitor" if _norm_role(role) == "coindemnitor" else "indemnitor"
    for source in (bond, packet):
        if not isinstance(source, dict):
            continue
        for party in packet_parties(source):
            if party.get("role") == want and str(party.get("name") or "").strip():
                return party
    return None


async def _identity_verdict(packet: dict, bond: Optional[dict], party: dict) -> str:
    """verified, unverified, or lookup_failed. Lookup errors hide the pay link."""
    from dashboard.services.identity_verification_service import (
        IdentityLookupError,
        evaluate_parties,
        explicit_self_indemnitor,
        gather_identity_evidence,
    )

    bond_case_id = _text(bond, "bond_case_id", "Bond_Case_ID") or _text(packet, "bond_case_id", "Bond_Case_ID")
    booking = _text(bond, "booking_number", "Booking_Number") or _text(packet, "booking_number", "Booking_Number")
    packet_id = _text(packet, "packet_id")
    county = _text(bond, "county", "County") or _text(packet, "county", "County")
    try:
        evidence = await gather_identity_evidence(
            bond_case_id=bond_case_id,
            booking_number=booking,
            packet_id=packet_id,
            indemnitor_ids=[str(party.get("indemnitor_id") or "")],
        )
    except IdentityLookupError:
        return "lookup_failed"
    except Exception:
        logger.warning("[paperwork_done] identity lookup failed packet_id=%s", packet_id)
        return "lookup_failed"
    decision = evaluate_parties(
        [party],
        evidence.get("scans") or [],
        evidence.get("attestations") or [],
        bond_case_id=bond_case_id,
        booking_number=booking,
        packet_id=packet_id,
        self_indemnitor=explicit_self_indemnitor(packet) or explicit_self_indemnitor(bond),
        county=county,
    )
    return "verified" if decision.get("ok") else "unverified"


def _safe_pay(packet: dict, bond: Optional[dict]) -> tuple[str, str]:
    """Return (amount, url) from the existing locked-invoice view, or blanks."""
    from dashboard.services.cosigner_link_service import _payment_view

    view = _payment_view(packet, bond if isinstance(bond, dict) else None)
    if view.get("error"):
        return "", ""
    amount = str(view.get("amount") or "").strip()
    url = str(view.get("url") or "").strip()
    if not _AMOUNT.match(amount) or amount == "0.00":
        return "", ""
    if not url.startswith("https://"):
        return "", ""
    return amount, url


async def load_done_state(packet_id: str, role: Optional[str] = None) -> dict[str, Any]:
    """Case progress for /done. Fail closed: loaded False has no pay link."""
    from dashboard.extensions import get_collection
    from dashboard.services.premium_confirmation_service import invoice_due_cents

    state = {
        "loaded": False,
        "blocked": False,
        "completed_roles": [],
        "pending_roles": [],
        "viewer_label": "",
        "viewer_signed": False,
        "pay_url": None,
        "pay_amount": "",
        "nothing_due": False,
        "already_paid": False,
    }
    pid = str(packet_id or "").strip()
    if not _PACKET_ID.match(pid):
        return state
    try:
        packet = await get_collection("paperwork_packets").find_one({"packet_id": pid})
    except Exception:
        logger.warning("[paperwork_done] packet lookup failed")
        return state
    if not isinstance(packet, dict):
        return state
    try:
        bond = await _load_bond(packet)
    except Exception:
        logger.warning("[paperwork_done] bond lookup failed packet_id=%s", pid)
        return state

    state["loaded"] = True
    if _packet_voided(packet):
        state["blocked"] = True
        return state

    viewer = _norm_role(role)
    if viewer not in _LABELS:
        viewer = ""
    if viewer and _role_dead(packet, viewer):
        state["blocked"] = True
        return state

    completed: list[str] = []
    pending: list[str] = []
    seen: set[str] = set()
    for row in _submitters(packet):
        key = _norm_role(str(row.get("role") or ""))
        if key not in _LABELS or key in seen:
            continue
        seen.add(key)
        if _role_dead(packet, key):
            continue
        label = _LABELS[key]
        if _role_signed(packet, key):
            completed.append(label)
        else:
            pending.append(label)
    for key in _completed_role_keys(packet):
        label = _LABELS.get(key)
        if label and label not in completed and key not in seen:
            completed.append(label)
    state["completed_roles"] = completed
    state["pending_roles"] = pending
    if viewer:
        state["viewer_label"] = _LABELS[viewer]
        state["viewer_signed"] = _role_signed(packet, viewer)

    if viewer not in _PAY_ROLES or not state["viewer_signed"]:
        return state

    party = _party_for_role(packet, bond, viewer)
    if not party:
        return state
    if await _identity_verdict(packet, bond, party) != "verified":
        return state
    if isinstance(bond, dict) and bond.get("premium_paid") is True:
        state["already_paid"] = True
        return state

    due = invoice_due_cents(packet)
    if due is None and isinstance(bond, dict):
        due = invoice_due_cents(bond)
    if due == 0:
        state["nothing_due"] = True
        state["pay_amount"] = "0.00"
        return state

    amount, url = _safe_pay(packet, bond if isinstance(bond, dict) else None)
    if amount and url:
        state["pay_amount"] = amount
        state["pay_url"] = url
    return state


def _esc(value: object) -> str:
    return html.escape(str(value or ""), quote=True)


def _contact_html() -> str:
    return (
        '<section class="contact" aria-labelledby="office-title">'
        '<h2 id="office-title">Office</h2>'
        "<p>Call <a href=\"tel:+12393322245\">(239) 332-2245</a> "
        "or text <a href=\"sms:+12399550178\">(239) 955-0178</a>. "
        "Walk-in hours Mon-Fri 9-5. Phones 24/7.</p>"
        "</section>"
    )


def _next_steps_html() -> str:
    return (
        '<section aria-labelledby="next-title">'
        '<h2 id="next-title">What happens next</h2>'
        "<ol>"
        "<li>Each role that still needs to sign uses that role's own paperwork link.</li>"
        "<li>Pay the premium with the button on this page when it is shown. "
        "The amount comes from the bond invoice.</li>"
        "<li>After the premium is paid, Shamrock posts the bond. "
        "The jail releases the defendant when it finishes processing the bond.</li>"
        "<li>After release, the defendant must go to every court date.</li>"
        "</ol>"
        "</section>"
    )


def _pay_html(state: dict) -> str:
    if state.get("nothing_due"):
        return (
            '<section aria-labelledby="pay-title">'
            '<h2 id="pay-title">Premium</h2>'
            '<p data-pay-step="nothing-due">No premium is due right now.</p>'
            "</section>"
        )
    if state.get("already_paid"):
        return (
            '<section aria-labelledby="pay-title">'
            '<h2 id="pay-title">Premium</h2>'
            '<p data-pay-step="recorded">The premium is already recorded.</p>'
            "</section>"
        )
    url = str(state.get("pay_url") or "")
    amount = str(state.get("pay_amount") or "")
    if url.startswith("https://") and _AMOUNT.match(amount):
        safe_url = _esc(url)
        safe_amount = _esc(amount)
        return (
            '<section aria-labelledby="pay-title">'
            '<h2 id="pay-title">Pay the premium</h2>'
            f"<p>The amount due is <strong>${safe_amount}</strong>. "
            "This is the invoice stored on the bond.</p>"
            f'<a class="pay" data-pay-step="ready" href="{safe_url}">Pay ${safe_amount}</a>'
            "</section>"
        )
    if state.get("viewer_signed") and state.get("viewer_label") in {"Indemnitor", "Co-indemnitor"}:
        hidden = (
            "The payment button is off until identity is verified and the bond invoice is ready."
        )
    elif state.get("viewer_signed"):
        hidden = "The payment button is off for this role."
    else:
        hidden = "The payment button is off until this signature is recorded."
    return (
        '<section aria-labelledby="pay-title">'
        '<h2 id="pay-title">Pay the premium</h2>'
        f'<p data-pay-step="hidden">{hidden}</p>'
        "</section>"
    )


def _kiosk_script() -> str:
    return (
        "<script>(function(){try{sessionStorage.clear();"
        "['sl_portal_phone','sl_indemnitor_scanned_profile','sl_portal_in_person']"
        ".forEach(function(k){localStorage.removeItem(k)})}catch(e){}"
        "var n=20,c=document.getElementById('kioskResetCount');"
        "var t=setInterval(function(){n-=1;if(c)c.textContent=String(n);"
        "if(n<=0){clearInterval(t);location.replace('/kiosk');}},1000);})();</script>"
    )


def _page(title: str, body: str, *, kiosk: bool) -> str:
    kiosk_note = ""
    script = ""
    if kiosk:
        kiosk_note = (
            '<p id="kioskResetNote">This screen resets for the next client in '
            '<strong id="kioskResetCount">20</strong> seconds.</p>'
            '<a class="secondary" href="/kiosk">Done — reset now</a>'
        )
        script = _kiosk_script()
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
    <meta name="robots" content="noindex, nofollow">
    <meta name="theme-color" content="#0b0f19">
    <title>{_esc(title)}</title>
    <style>
        :root {{
            --bg: #0b0f19;
            --card: #151c2c;
            --text: #f8fafc;
            --muted: #cbd5e1;
            --accent: #22c55e;
            --line: rgba(255,255,255,0.12);
        }}
        * {{ box-sizing: border-box; }}
        body {{
            margin: 0;
            background: var(--bg);
            color: var(--text);
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            font-size: 16px;
            line-height: 1.5;
            padding: max(16px, env(safe-area-inset-top)) max(16px, env(safe-area-inset-right))
                max(24px, env(safe-area-inset-bottom)) max(16px, env(safe-area-inset-left));
        }}
        main {{
            max-width: 32rem;
            margin: 0 auto;
            background: var(--card);
            border: 1px solid var(--line);
            border-radius: 16px;
            padding: 24px 18px 20px;
        }}
        h1 {{ font-size: 1.5rem; line-height: 1.25; margin: 0 0 8px; }}
        h2 {{ font-size: 1.125rem; margin: 20px 0 8px; }}
        p, li {{ color: var(--muted); }}
        ol, ul {{ margin: 0; padding-left: 1.25rem; }}
        li + li {{ margin-top: 6px; }}
        a {{ color: var(--accent); }}
        a:focus-visible, .pay:focus-visible, .secondary:focus-visible {{
            outline: 3px solid #f8fafc;
            outline-offset: 3px;
        }}
        .pay, .secondary {{
            display: flex;
            align-items: center;
            justify-content: center;
            width: 100%;
            min-height: 48px;
            margin-top: 12px;
            padding: 12px 16px;
            border-radius: 12px;
            font-size: 1.125rem;
            font-weight: 700;
            text-decoration: none;
            text-align: center;
        }}
        .pay {{ background: var(--accent); color: #052e16; }}
        .secondary {{
            background: transparent;
            color: var(--accent);
            border: 1px solid rgba(34,197,94,0.45);
        }}
        .contact {{ margin-top: 8px; }}
        @media (min-width: 768px) {{
            main {{ padding: 32px 28px; }}
            h1 {{ font-size: 1.75rem; }}
        }}
    </style>
</head>
<body>
<main>
{body}
{kiosk_note}
</main>
{script}
</body>
</html>"""


def render_done_page(state: Optional[dict], *, kiosk: bool = False) -> str:
    """Mobile-first completion page. Pay markup exists only when state allows it."""
    data = state if isinstance(state, dict) else _EMPTY
    if not data.get("loaded"):
        body = (
            "<h1>We could not open this case</h1>"
            "<p>This page does not show a payment button until the bond can be loaded. "
            "Refresh once if you just finished signing.</p>"
            + _next_steps_html()
            + _contact_html()
        )
        return _page("Shamrock Bail Bonds — Next steps", body, kiosk=kiosk)
    if data.get("blocked"):
        body = (
            "<h1>This paperwork is closed</h1>"
            "<p>There is nothing else on this page.</p>"
            + _contact_html()
        )
        return _page("Shamrock Bail Bonds — Paperwork closed", body, kiosk=kiosk)

    if data.get("viewer_signed") and data.get("viewer_label"):
        heading = f"You finished signing as the {_esc(data['viewer_label'])}."
        lead = "<p>Shamrock has that signature.</p>"
    elif data.get("completed_roles"):
        heading = "Paperwork is in progress."
        lead = "<p>Some roles have signed. This page does not show a payment button for an unfinished signature.</p>"
    else:
        heading = "Signing is still open."
        lead = "<p>This signature is not recorded yet, so there is no payment button.</p>"

    completed = data.get("completed_roles") or []
    pending = data.get("pending_roles") or []
    done_html = ""
    if completed:
        items = "".join(f"<li>{_esc(label)}</li>" for label in completed)
        done_html = (
            '<section aria-labelledby="done-title">'
            '<h2 id="done-title">Signed</h2>'
            f"<ul>{items}</ul>"
            "</section>"
        )
    if pending:
        items = "".join(f"<li>{_esc(label)}</li>" for label in pending)
        wait_html = (
            '<section aria-labelledby="wait-title">'
            '<h2 id="wait-title">Still needs to sign</h2>'
            f"<ul>{items}</ul>"
            "<p>Each of those roles signs their own paperwork.</p>"
            "</section>"
        )
    else:
        wait_html = (
            '<section aria-labelledby="wait-title">'
            '<h2 id="wait-title">Still needs to sign</h2>'
            "<p>No other roles are waiting to sign.</p>"
            "</section>"
        )
    body = f"<h1>{heading}</h1>{lead}{done_html}{wait_html}{_pay_html(data)}{_next_steps_html()}{_contact_html()}"
    return _page("Shamrock Bail Bonds — Next steps", body, kiosk=kiosk)
