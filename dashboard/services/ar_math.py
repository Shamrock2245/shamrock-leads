"""
Accounts receivable math and collection guardrails.

Balance is never stored. It is always:

    premium − down payment − later payments (+ refunds)

Cents are the unit of record. Dollar floats are display only.

Quiet hours, opt-out, party limits, and frequency limits are enforced here
so the reminder prompt and the send path share one gate. Florida Consumer
Collection Practices Act, Fla. Stat. § 559.72, applies when Shamrock collects
its own premium.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Any, Iterable, Optional
from zoneinfo import ZoneInfo

from dashboard.services.ledger_service import LedgerService

# 8:00 AM inclusive through 9:00 PM exclusive in the recipient's zone.
CONTACT_TZ = "America/New_York"
CONTACT_START_HOUR = 8
CONTACT_END_HOUR = 21

# Reasonable FCCPA frequency (559.72(7) — harassment). Not a statute number;
# a Shamrock cap so one client is not contacted all day.
MAX_SENDS_PER_LOCAL_DAY = 1
MAX_SENDS_PER_7_DAYS = 3
MIN_HOURS_BETWEEN_SENDS = 20

AR_FROM_LINE = "2399550178"
DOWN_PAYMENT_REF_PREFIX = "write-bond-down:"

ALLOWED_PAYMENT_METHODS = ("cash", "check", "swipesimple", "card")

_PROHIBITED = (
    (r"\barrest", "arrest"),
    (r"\bwarrant", "warrant"),
    (r"\bjail\b", "jail"),
    (r"\bprison\b", "prison"),
    (r"\bpolice\b", "police"),
    (r"\blawsuit\b", "lawsuit"),
    (r"\bsue\b", "sue"),
    (r"\bgarnish", "garnish"),
    (r"legal action", "legal_action"),
    (r"\byour employer\b", "employer"),
    (r"\byour boss\b", "employer"),
    (r"\byour job\b", "employer"),
    (r"\bat your work\b", "employer"),
    (r"\bat work\b", "employer"),
)

_NEG_WORDS = (
    "stop", "angry", "upset", "ridiculous", "harass", "lawyer", "leave me alone",
    "don't text", "do not text", "not paying", "scam",
)
_POS_WORDS = (
    "thank", "thanks", "paid", "sending", "on my way", "ok", "okay", "will pay",
    "appreciate",
)


def to_cents(amount: Any) -> int:
    return LedgerService.to_cents(amount)


def cents_to_dollars(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    n = abs(int(cents))
    return f"{sign}{n // 100}.{n % 100:02d}"


def phone_last10(raw: Any) -> str:
    digits = "".join(ch for ch in str(raw or "") if ch.isdigit())
    return digits[-10:] if len(digits) >= 10 else digits


def phones_match(a: Any, b: Any) -> bool:
    left, right = phone_last10(a), phone_last10(b)
    return bool(left) and left == right


def booking_key(raw: Any) -> str:
    return str(raw or "").strip().upper()


def down_payment_ledger_ref(booking_number: str) -> str:
    return f"{DOWN_PAYMENT_REF_PREFIX}{booking_key(booking_number)}"


def parse_iso_date(raw: Any) -> Optional[date]:
    if raw is None or raw == "":
        return None
    text = str(raw).strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def as_utc(ts: Any) -> Optional[datetime]:
    if ts is None or ts == "":
        return None
    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            return ts.replace(tzinfo=timezone.utc)
        return ts.astimezone(timezone.utc)
    text = str(ts).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def local_datetime(now_utc: datetime, tz_name: str = CONTACT_TZ) -> datetime:
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    return now_utc.astimezone(ZoneInfo(tz_name))


def within_contact_window(now_utc: datetime, tz_name: str = CONTACT_TZ) -> bool:
    """True from 8:00 AM inclusive until 9:00 PM exclusive, recipient local time."""
    local = local_datetime(now_utc, tz_name)
    return CONTACT_START_HOUR <= local.hour < CONTACT_END_HOUR


def premium_cents_from_bond(bond: dict) -> Optional[int]:
    """Return premium cents, or None when the bond has no premium on file."""
    if not bond:
        return None
    if bond.get("premium_cents") is not None and bond.get("premium_cents") != "":
        return int(bond["premium_cents"])
    for key in ("premium", "premium_amount", "total_premium"):
        if key in bond and bond.get(key) not in (None, ""):
            return to_cents(bond.get(key))
    return None


def down_payment_from_bond(bond: dict, legacy_payments: Optional[Iterable[dict]] = None) -> dict:
    """
    Down payment entered on the bond, or the legacy Record Bond payment that
    logged the premium as collected. Missing data stays missing — never a
    guessed zero that looks like an entry.
    """
    if bond and bond.get("down_payment_cents") is not None and bond.get("down_payment_cents") != "":
        return {
            "cents": int(bond["down_payment_cents"]),
            "source": "field",
            "entered": True,
        }
    if bond and "down_payment" in bond and bond.get("down_payment") not in (None, ""):
        return {
            "cents": to_cents(bond.get("down_payment")),
            "source": "field",
            "entered": True,
        }
    collected = 0
    found = False
    for payment in legacy_payments or []:
        kind = str(payment.get("type") or "").lower()
        source = str(payment.get("source") or "")
        if kind == "down_payment" or source == "retrospective_manual":
            collected += to_cents(payment.get("amount"))
            found = True
    if found:
        return {"cents": collected, "source": "legacy_payment", "entered": True}
    return {"cents": 0, "source": "not_entered", "entered": False}


def _entry_cents(entry: dict) -> int:
    amount = entry.get("amount")
    if entry.get("amount_is_cents") or isinstance(amount, int):
        return int(amount or 0)
    return to_cents(amount)


def _entry_ref(entry: dict) -> str:
    return str(entry.get("stripe_swipe_ref") or entry.get("reference") or entry.get("reference_id") or "").strip()


def _is_swipesimple(row: dict) -> bool:
    blob = " ".join(
        str(row.get(key) or "")
        for key in ("source", "method", "actor", "notes", "description")
    ).lower()
    return "swipesimple" in blob or "swipe simple" in blob


def later_payments(
    bond: dict,
    ledger_entries: Iterable[dict],
    transactions: Optional[Iterable[dict]] = None,
) -> dict:
    """
    Later payments reduce the balance. The write-time down payment does not
    count again. SwipeSimple rows already on the ledger are not counted twice.
    Unattributed SwipeSimple rows (no booking number) are not guessed onto a bond.
    """
    booking = booking_key((bond or {}).get("booking_number"))
    down_ref = down_payment_ledger_ref(booking)
    extra_skip = {
        down_ref,
        str((bond or {}).get("down_payment_reference") or "").strip(),
        str((bond or {}).get("down_payment_ledger_ref") or "").strip(),
    }
    extra_skip.discard("")

    items: list[dict] = []
    seen_refs: set[str] = set()
    net_cents = 0

    for entry in ledger_entries or []:
        entry_booking = booking_key(entry.get("booking_number"))
        if not booking or entry_booking != booking:
            continue
        kind = str(entry.get("entry_kind") or entry.get("type") or "").lower()
        if kind == "down_payment":
            continue
        ref = _entry_ref(entry)
        if ref and ref in extra_skip:
            continue
        cents = _entry_cents(entry)
        if kind == "refund":
            applied = -abs(cents)
        elif kind == "payment" or (kind in ("premium", "payment_plan") and cents < 0):
            applied = abs(cents)
        else:
            continue
        if applied == 0:
            continue
        if ref:
            seen_refs.add(ref)
        net_cents += applied
        when = as_utc(entry.get("timestamp") or entry.get("created_at"))
        items.append({
            "source": entry.get("source") or "financial_ledger",
            "method": entry.get("method") or ("swipesimple" if _is_swipesimple(entry) else ""),
            "reference": ref,
            "amount_cents": applied if kind != "refund" else -abs(cents),
            "timestamp": when.isoformat() if when else "",
            "entered_by": entry.get("entered_by") or entry.get("actor") or "",
            "kind": "refund" if kind == "refund" else "payment",
            "swipesimple": _is_swipesimple(entry),
        })

    for txn in transactions or []:
        if booking and booking_key(txn.get("booking_number")) != booking:
            continue
        if not booking_key(txn.get("booking_number")):
            continue
        if str(txn.get("status") or "").lower() in ("void", "voided", "declined", "failed"):
            continue
        ref = str(txn.get("reference_id") or txn.get("transaction_id") or txn.get("dedup_key") or "").strip()
        dedup_keys = {ref, str(txn.get("dedup_key") or "").strip(), str(txn.get("transaction_id") or "").strip()}
        dedup_keys.discard("")
        if dedup_keys & seen_refs or dedup_keys & extra_skip:
            continue
        kind = str(txn.get("type") or "premium").lower()
        if kind not in ("premium", "payment", "payment_plan", "refund"):
            continue
        cents = to_cents(txn.get("amount"))
        if cents == 0:
            continue
        signed = -cents if kind == "refund" else cents
        net_cents += signed
        if ref:
            seen_refs.add(ref)
        when = as_utc(txn.get("timestamp") or txn.get("created_at"))
        items.append({
            "source": txn.get("source") or "transactions",
            "method": txn.get("method") or ("swipesimple" if _is_swipesimple(txn) else ""),
            "reference": ref,
            "amount_cents": signed if kind != "refund" else -cents,
            "timestamp": when.isoformat() if when else "",
            "entered_by": txn.get("agent_name") or txn.get("actor") or "",
            "kind": "refund" if kind == "refund" else "payment",
            "swipesimple": _is_swipesimple(txn),
            "linked_from": "transactions",
        })

    items.sort(key=lambda row: row.get("timestamp") or "")
    return {"cents": net_cents, "items": items}


def compute_balance_cents(premium_cents: Optional[int], down_cents: int, later_cents: int) -> Optional[int]:
    """Premium minus down payment minus later payments. None if premium was never entered."""
    if premium_cents is None:
        return None
    return int(premium_cents) - int(down_cents) - int(later_cents)


def days_overdue(due: Optional[date], today: date, balance_cents: Optional[int]) -> Optional[int]:
    if balance_cents is None:
        return None
    if balance_cents <= 0:
        return 0
    if due is None:
        return None
    delta = (today - due).days
    return delta if delta > 0 else 0


def next_due_date(bond: dict, plan: Optional[dict]) -> Optional[date]:
    """Active payment plan due date wins. Otherwise the date entered at write time."""
    if plan and str(plan.get("status") or "active").lower() == "active":
        planned = parse_iso_date(plan.get("next_due_date"))
        if planned:
            return planned
    return parse_iso_date((bond or {}).get("next_payment_due"))


def last_payment_date(down_entered: bool, down_cents: int, down_at: Any, items: Iterable[dict]) -> Optional[str]:
    stamps: list[datetime] = []
    if down_entered and down_cents > 0:
        parsed = as_utc(down_at)
        if parsed:
            stamps.append(parsed)
    for item in items:
        if item.get("kind") == "refund":
            continue
        if int(item.get("amount_cents") or 0) <= 0:
            continue
        parsed = as_utc(item.get("timestamp"))
        if parsed:
            stamps.append(parsed)
    if not stamps:
        return None
    return max(stamps).isoformat()


def money_fields(premium: Any, down_payment: Any) -> dict:
    premium_cents = to_cents(premium)
    down_cents = to_cents(down_payment)
    return {
        "premium": float(cents_to_dollars(premium_cents)),
        "premium_cents": premium_cents,
        "down_payment": float(cents_to_dollars(down_cents)),
        "down_payment_cents": down_cents,
    }


def validate_money(premium: Any, down_payment: Any) -> Optional[str]:
    try:
        premium_cents = to_cents(premium)
        down_cents = to_cents(down_payment)
    except Exception:
        return "invalid_amount"
    if premium_cents < 0 or down_cents < 0:
        return "amount_negative"
    if down_cents > premium_cents:
        return "down_payment_exceeds_premium"
    return None


def normalize_method(raw: Any) -> str:
    method = str(raw or "").strip().lower()
    if method in ("cheque",):
        return "check"
    if method in ("swipe", "swipe simple", "card_present"):
        return "swipesimple"
    if method in ALLOWED_PAYMENT_METHODS or method in ("financing", "other"):
        return method
    return "cash"


def money_changed(before: Optional[dict], premium_cents: int, down_cents: int) -> bool:
    if not before:
        return False
    old_premium = premium_cents_from_bond(before)
    old_down = down_payment_from_bond(before)
    if old_premium is None and not old_down["entered"]:
        return False
    if old_premium is None:
        old_premium = premium_cents
    if not old_down["entered"]:
        return old_premium != premium_cents
    return old_premium != premium_cents or old_down["cents"] != down_cents


def sentiment_label(inbound_texts: Iterable[str]) -> str:
    texts = [str(t or "").strip() for t in inbound_texts if str(t or "").strip()]
    if not texts:
        return "no_history"
    blob = " ".join(texts).lower()
    neg = sum(1 for word in _NEG_WORDS if word in blob)
    pos = sum(1 for word in _POS_WORDS if word in blob)
    if neg > pos and neg > 0:
        return "negative"
    if pos > neg and pos > 0:
        return "positive"
    return "neutral"


def payment_behavior(
    *,
    down_entered: bool,
    down_cents: int,
    down_at: Any,
    later_items: Iterable[dict],
    due: Optional[date],
    today: date,
    balance_cents: Optional[int],
    history: Optional[Iterable[dict]] = None,
) -> dict:
    on_time = 0
    late = 0
    if down_entered and down_cents > 0:
        paid_on = as_utc(down_at)
        paid_date = local_datetime(paid_on).date() if paid_on else None
        if due and paid_date and paid_date > due:
            late += 1
        else:
            on_time += 1
    for item in later_items:
        if item.get("kind") == "refund" or int(item.get("amount_cents") or 0) <= 0:
            continue
        paid_on = as_utc(item.get("timestamp"))
        paid_date = local_datetime(paid_on).date() if paid_on else None
        if due and paid_date and paid_date > due:
            late += 1
        else:
            on_time += 1
    if balance_cents and balance_cents > 0 and due and today > due:
        late += 1

    broken = 0
    for event in history or []:
        promise = parse_iso_date(event.get("promise_date"))
        if not promise or promise >= today:
            continue
        promised_at = datetime(promise.year, promise.month, promise.day, tzinfo=timezone.utc)
        paid_after = False
        for item in later_items:
            paid_on = as_utc(item.get("timestamp"))
            if paid_on and paid_on.date() > promise and int(item.get("amount_cents") or 0) > 0:
                paid_after = True
                break
        if not paid_after:
            broken += 1
    return {
        "on_time_count": on_time,
        "late_count": late,
        "broken_promises": broken,
    }


def choose_tone(
    *,
    on_time_count: int,
    late_count: int,
    broken_promises: int,
    days_overdue: Optional[int],
    sentiment: str,
) -> str:
    """
    On-time payer who missed once → friendly.
    Silent thread or repeated broken promises → firm, without threats.
    """
    overdue = days_overdue or 0
    if broken_promises >= 1 or (sentiment in ("no_history", "negative") and overdue >= 7):
        return "firm"
    if on_time_count >= 1 and late_count <= 1 and broken_promises == 0:
        return "friendly"
    if late_count >= 2 or overdue >= 7:
        return "direct"
    return "neutral"


def prohibited_language(text: str) -> list[str]:
    blob = str(text or "")
    hits = []
    for pattern, label in _PROHIBITED:
        if re.search(pattern, blob, flags=re.IGNORECASE):
            hits.append(label)
    return hits


def frequency_block(
    prior_sends: Iterable[Any],
    now_utc: datetime,
    tz_name: str = CONTACT_TZ,
) -> Optional[str]:
    stamps = [as_utc(item) for item in prior_sends]
    stamps = [item for item in stamps if item is not None]
    if not stamps:
        return None
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    latest = max(stamps)
    if (now_utc - latest).total_seconds() < MIN_HOURS_BETWEEN_SENDS * 3600:
        return "frequency_too_soon"
    today = local_datetime(now_utc, tz_name).date()
    day_count = sum(1 for ts in stamps if local_datetime(ts, tz_name).date() == today)
    if day_count >= MAX_SENDS_PER_LOCAL_DAY:
        return "frequency_daily"
    week_count = sum(1 for ts in stamps if (now_utc - ts).total_seconds() <= 7 * 86400)
    if week_count >= MAX_SENDS_PER_7_DAYS:
        return "frequency_weekly"
    return None


def evaluate_reminder_send(
    *,
    now_utc: datetime,
    tz_name: str = CONTACT_TZ,
    opted_out: bool,
    recipient_role: str,
    recipient_phone: str,
    defendant_phone: str,
    indemnitor_phone: str,
    text: str,
    prior_sends: Optional[Iterable[Any]] = None,
) -> dict:
    """Staff-approval still required. This is the code gate in front of send."""
    reasons: list[str] = []
    role = str(recipient_role or "").strip().lower()
    if role not in ("indemnitor", "defendant"):
        reasons.append("third_party_blocked")
        expected = ""
    else:
        expected = indemnitor_phone if role == "indemnitor" else defendant_phone
        if not phones_match(recipient_phone, expected):
            reasons.append("recipient_not_on_bond")
    if opted_out:
        reasons.append("opted_out")
    if not within_contact_window(now_utc, tz_name):
        reasons.append("quiet_hours")
    hits = prohibited_language(text)
    if hits:
        reasons.append("prohibited_language")
    freq = frequency_block(prior_sends or [], now_utc, tz_name)
    if freq:
        reasons.append(freq)
    return {
        "allowed": not reasons,
        "reasons": reasons,
        "prohibited_hits": hits,
        "recipient_role": role if role in ("indemnitor", "defendant") else "",
    }


def swipesimple_match_hook(transaction: dict, bond: dict) -> dict:
    """
    Future automatic matcher. Today it only confirms an exact booking number
    already stored on the SwipeSimple row. It never matches on a person's name.
    """
    txn_booking = booking_key(transaction.get("booking_number"))
    bond_booking = booking_key((bond or {}).get("booking_number"))
    if txn_booking and bond_booking and txn_booking == bond_booking:
        return {
            "matched": True,
            "method": "exact_booking_number",
            "auto_apply": False,
        }
    return {
        "matched": False,
        "method": "unmatched",
        "auto_apply": False,
        "hook": "swipesimple_match_hook",
    }


def build_ar_row(
    bond: dict,
    *,
    ledger_entries: Optional[Iterable[dict]] = None,
    transactions: Optional[Iterable[dict]] = None,
    legacy_payments: Optional[Iterable[dict]] = None,
    plan: Optional[dict] = None,
    today: Optional[date] = None,
    inbound_texts: Optional[Iterable[str]] = None,
) -> dict:
    today = today or local_datetime(datetime.now(timezone.utc)).date()
    bond = bond or {}
    premium_cents = premium_cents_from_bond(bond)
    down = down_payment_from_bond(bond, legacy_payments)
    later = later_payments(bond, ledger_entries or [], transactions or [])
    later_cents = later["cents"]
    if premium_cents is None:
        balance = None
    else:
        down_for_math = down["cents"] if down["entered"] else 0
        balance = compute_balance_cents(premium_cents, down_for_math, later_cents)
    due = next_due_date(bond, plan)
    overdue = days_overdue(due, today, balance)
    down_at = bond.get("down_payment_entered_at") or bond.get("bond_date") or bond.get("created_at")
    last_paid = last_payment_date(down["entered"], down["cents"], down_at, later["items"])
    behavior = payment_behavior(
        down_entered=down["entered"],
        down_cents=down["cents"],
        down_at=down_at,
        later_items=later["items"],
        due=due,
        today=today,
        balance_cents=balance,
        history=bond.get("ar_history") or [],
    )
    sentiment = sentiment_label(inbound_texts or [])
    tone = choose_tone(
        on_time_count=behavior["on_time_count"],
        late_count=behavior["late_count"],
        broken_promises=behavior["broken_promises"],
        days_overdue=overdue,
        sentiment=sentiment,
    )
    indemnitor = bond.get("indemnitor") if isinstance(bond.get("indemnitor"), dict) else {}
    poa = bond.get("poa_number") or ""
    if not poa:
        numbers = bond.get("poa_numbers") or []
        if isinstance(numbers, list) and numbers:
            first = numbers[0]
            if isinstance(first, dict):
                poa = first.get("poa_full") or first.get("poa_number") or ""
            else:
                poa = str(first)
    if premium_cents is None:
        status = "premium_not_entered"
    elif balance is not None and balance <= 0:
        status = "paid_in_full"
    elif overdue and overdue > 0:
        status = "overdue"
    elif balance and balance > 0:
        status = "open"
    else:
        status = "open"
    return {
        "booking_number": bond.get("booking_number") or "",
        "defendant_name": bond.get("defendant_name") or "",
        "indemnitor_name": bond.get("indemnitor_name") or indemnitor.get("name") or "",
        "defendant_phone": bond.get("defendant_phone") or "",
        "indemnitor_phone": bond.get("indemnitor_phone") or indemnitor.get("phone") or "",
        "case_number": bond.get("case_number") or "",
        "poa_number": poa,
        "county": bond.get("county") or "",
        "surety": bond.get("insurance_company") or bond.get("surety") or "",
        "status": bond.get("status") or "",
        "ar_status": status,
        "premium_cents": premium_cents,
        "premium_dollars": cents_to_dollars(premium_cents) if premium_cents is not None else None,
        "down_payment_cents": down["cents"] if down["entered"] else None,
        "down_payment_dollars": cents_to_dollars(down["cents"]) if down["entered"] else None,
        "down_payment_entered": down["entered"],
        "down_payment_source": down["source"],
        "down_payment_entered_at": str(down_at) if down["entered"] and down_at else None,
        "down_payment_method": bond.get("down_payment_method") or "",
        "down_payment_reference": bond.get("down_payment_reference") or "",
        "down_payment_entered_by": bond.get("down_payment_entered_by") or "",
        "later_payments_cents": later_cents,
        "balance_due_cents": balance,
        "balance_due_dollars": cents_to_dollars(balance) if balance is not None else None,
        "next_payment_due": due.isoformat() if due else None,
        "days_overdue": overdue,
        "last_payment_at": last_paid,
        "payments": later["items"],
        "tone": tone,
        "sentiment": sentiment,
        "on_time_count": behavior["on_time_count"],
        "late_count": behavior["late_count"],
        "broken_promises": behavior["broken_promises"],
        "opted_out": bool(bond.get("opted_out")),
    }


def row_matches_filter(row: dict, filt: str) -> bool:
    key = (filt or "all").strip().lower()
    if key in ("", "all"):
        return True
    if key in ("overdue",):
        return bool(row.get("days_overdue") and row["days_overdue"] > 0 and (row.get("balance_due_cents") or 0) > 0)
    if key in ("open", "balance", "balance_due"):
        return (row.get("balance_due_cents") or 0) > 0
    if key in ("paid", "paid_in_full"):
        return row.get("ar_status") == "paid_in_full"
    return True


def sort_rows(rows: list[dict], sort: str) -> list[dict]:
    key = (sort or "overdue").strip().lower()

    def overdue_key(row: dict):
        days = row.get("days_overdue")
        return (
            0 if isinstance(days, int) and days > 0 else 1,
            -(days or 0),
            -(row.get("balance_due_cents") or 0),
            (row.get("defendant_name") or "").lower(),
        )

    def balance_key(row: dict):
        return (-(row.get("balance_due_cents") or 0), (row.get("defendant_name") or "").lower())

    def name_key(row: dict):
        return ((row.get("defendant_name") or "").lower(), (row.get("indemnitor_name") or "").lower())

    def due_key(row: dict):
        due = row.get("next_payment_due") or "9999-12-31"
        return (due, (row.get("defendant_name") or "").lower())

    def last_key(row: dict):
        return (row.get("last_payment_at") or "", (row.get("defendant_name") or "").lower())

    sorters = {
        "overdue": overdue_key,
        "balance": balance_key,
        "name": name_key,
        "next_due": due_key,
        "last_payment": last_key,
    }
    return sorted(rows, key=sorters.get(key, overdue_key))
