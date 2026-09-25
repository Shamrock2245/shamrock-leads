"""
Shared DocuSeal ``submission.completed`` handler — exactly-once side effects.

Used by BOTH completion paths:
  - dashboard/routers/webhooks.py              docuseal_webhook (submission.completed)
  - dashboard/services/lifecycle_automations.py run_docuseal_poller (cron backup)

Design
------
1. **Atomic claim per packet.** ``claim_completion`` does a single Mongo
   ``find_one_and_update`` on ``paperwork_packets`` that only matches when the
   packet's completion lease is free (never claimed, released, or expired) AND
   there is still work to do. Only the caller that wins the claim performs side
   effects. The lease (``DOCUSEAL_COMPLETION_LEASE_SECONDS``, default 900s)
   lets the poller retry a run that crashed mid-way.

2. **Per-step stamps.** Every step writes
   ``docuseal_completion.steps.<step>.state`` (done / skipped / gave_up are
   terminal; failed is retryable). A step with a terminal stamp is never run
   again, so each step runs exactly once; a failed step is retried by the next
   claim (the poller picks up packets whose ``docuseal_completion.done_at`` is
   unset). Retries are capped per step, then the step is stamped ``gave_up``.
   Stamps are written with ``claim_token`` in the filter, so a runner that lost
   its lease can't overwrite the new owner's state.

3. **Steps (in the order the webhook ran them before):**
     drive_upload   signed PDF → Drive "Completed Bonds" (skipped if a Drive link exists)
     packet_signed  paperwork_packets status=signed / docuseal_status=completed
     bond_cases     Packet_Status / Signature_Status = signed
     sse_event      publish_event("docuseal_submission_completed")
     legacy_payment_link   (behind the switch below; never retried)
     share_invoice  stage-only SwipeSimple Share Invoice (dispatch=False, PR #56)
     court_sync     seed_court_calendar_for_bond
     slack          non-PII Slack post

Legacy payment link switch — DEFAULT OFF (owner decision 2026-09-25)
-------------------------------------------------------------------
``maybe_send_packet_payment_link`` auto-SENDS the static SwipeSimple link to
the customer (BlueBubbles / Gmail). It is OFF by default on ALL THREE automatic
paths — DocuSeal completion (this handler), intake promote, and packet
finalize — via one switch, ``DOCUSEAL_COMPLETION_LEGACY_PAYMENT_LINK``
(parsed in ``legacy_payment_link_switch``; unset or unrecognized → off).

  unset / "0" / "false" / "off" / unknown  → off everywhere (default; fail closed)
  "1" / "true" / "yes" / "on" / "webhook"  → completion: only packets where a
            submission.completed WEBHOOK was received (previous production
            behavior); intake promote + packet finalize: enabled.
  "all"     → completion: webhook AND poller; intake promote + finalize: enabled.

Even when enabled, the service sends ONLY when a STAFF-CONFIRMED premium is
present (``premium_confirmed_amount`` + ``premium_confirmed_at`` +
``premium_confirmed_by``); otherwise the step is stamped
``skipped: premium_unconfirmed``. Stored ``premium`` values (often the 10%
estimate) never count. Nothing sets the confirmed fields yet, so in practice
enabling the switch still sends nothing until a staff confirmation UI exists.

Reversible with the env flag (or the one-line ``LEGACY_PAYMENT_LINK_DEFAULT``
constant in ``legacy_payment_link_switch``). When enabled, sends are send-once
twice over: this handler stamps ``started`` before the call (never retried), and
the service itself claims ``payment_link_send_once`` atomically BEFORE sending,
so concurrent DocuSeal retries, the poller, packet finalize, or a >24h gap can
never double-send.

PII: logs carry packet_id / bond_id / step / reason / exception type only.
"""
from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, Mapping, Optional

logger = logging.getLogger(__name__)

# OWNER SWITCH — legacy static payment link. DEFAULT OFF. Single source of truth
# lives in ``legacy_payment_link_switch`` (shared with intake promote + packet
# finalize); re-exported here for backwards compatibility.
from dashboard.services import legacy_payment_link_switch as _switch  # noqa: E402
from dashboard.services.legacy_payment_link_switch import (  # noqa: E402,F401
    LEGACY_MODES,
    LEGACY_PAYMENT_LINK_DEFAULT,
    LEGACY_PAYMENT_LINK_ENV,
)

LEASE_SECONDS_DEFAULT = 900
LEASE_ENV = "DOCUSEAL_COMPLETION_LEASE_SECONDS"

SOURCE_WEBHOOK = "webhook"
SOURCE_POLLER = "poller"

STEP_DRIVE = "drive_upload"
STEP_PACKET = "packet_signed"
STEP_BOND_CASES = "bond_cases"
STEP_EVENT = "sse_event"
STEP_LEGACY = "legacy_payment_link"
STEP_SHARE = "share_invoice"
STEP_COURT = "court_sync"
STEP_SLACK = "slack"

# Steps that must be terminal before the packet's completion is "done".
# The legacy payment link is excluded: in "webhook" mode it may legitimately
# wait for a webhook that never comes (poller-only completion).
REQUIRED_STEPS = (
    STEP_DRIVE,
    STEP_PACKET,
    STEP_BOND_CASES,
    STEP_EVENT,
    STEP_SHARE,
    STEP_COURT,
    STEP_SLACK,
)

TERMINAL_STATES = frozenset({"done", "skipped", "gave_up"})

# Retry caps (per step, counted across claims). The poller runs every 30 min.
MAX_ATTEMPTS = {
    STEP_DRIVE: 10,
    STEP_PACKET: 5,
    STEP_BOND_CASES: 5,
    STEP_SHARE: 3,
    STEP_COURT: 5,
    STEP_SLACK: 5,
}

# Packets completed by the pre-shared-handler code (status already "signed"
# with no completion record) only get the Drive retry — never a retroactive
# Slack / invoice / court / bond_cases replay.
PREEXISTING_SKIP_STEPS = (
    STEP_PACKET,
    STEP_BOND_CASES,
    STEP_EVENT,
    STEP_LEGACY,   # never a customer message for a historical packet, whatever the switch says
    STEP_SHARE,
    STEP_COURT,
    STEP_SLACK,
)

# Drive-only backfill of pre-handler completions (poller "legacy_drive" set).
# Idempotency: a packet with ANY of these fields set is treated as already filed
# and is never uploaded again. (``signed_pdf_drive_id`` historically held the
# upload FOLDER id, but was only ever written after a successful upload.)
DRIVE_LINK_FIELDS = ("signed_pdf_drive_url", "drive_link")
DRIVE_FILE_ID_FIELDS = ("signed_pdf_drive_file_id", "signed_pdf_drive_id")
DRIVE_MARKER_FIELDS = DRIVE_LINK_FIELDS + DRIVE_FILE_ID_FIELDS
# Rate limit (poller config keys; env not needed — lifecycle config is in Mongo).
DRIVE_BACKFILL_BATCH_DEFAULT = 10       # uploads attempted per poller tick
DRIVE_BACKFILL_BATCH_MAX = 50
DRIVE_BACKFILL_DELAY_DEFAULT = 2.0      # seconds between backfill uploads
DRIVE_BACKFILL_DELAY_MAX = 30.0

_C = "docuseal_completion"

GetCol = Callable[[str], Any]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _log_prefix(source: str) -> str:
    return "[docuseal_webhook]" if source == SOURCE_WEBHOOK else "[docuseal-poll]"


def _share_source(source: str) -> str:
    # Same `source` strings PR #56 used.
    return "docuseal_submission_completed" if source == SOURCE_WEBHOOK else "docuseal_poller_completed"


def _legacy_payment_link_mode() -> str:
    """'off' (default) | 'webhook' | 'all' — see ``legacy_payment_link_switch``."""
    return _switch.legacy_payment_link_mode()


def _lease_seconds() -> int:
    try:
        val = int(os.getenv(LEASE_ENV) or LEASE_SECONDS_DEFAULT)
    except ValueError:
        val = LEASE_SECONDS_DEFAULT
    return max(60, val)


def _completion(packet: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    rec = (packet or {}).get(_C)
    return rec if isinstance(rec, dict) else {}


def _step(packet: Optional[Mapping[str, Any]], step: str) -> Dict[str, Any]:
    steps = _completion(packet).get("steps")
    if not isinstance(steps, dict):
        return {}
    rec = steps.get(step)
    return rec if isinstance(rec, dict) else {}


def _is_terminal(packet: Optional[Mapping[str, Any]], step: str) -> bool:
    return _step(packet, step).get("state") in TERMINAL_STATES


def packet_filter(packet: Mapping[str, Any]) -> Dict[str, Any]:
    if packet.get("_id") is not None:
        return {"_id": packet["_id"]}
    return {"packet_id": packet.get("packet_id")}


def _lease_free_clause(now: datetime) -> Dict[str, Any]:
    # None matches "missing" and "null" in Mongo.
    return {
        "$or": [
            {f"{_C}.lease_expires_at": None},
            {f"{_C}.lease_expires_at": {"$lt": now}},
        ]
    }


def _work_remaining_clause(source: str, mode: str) -> Dict[str, Any]:
    clauses: list = [{f"{_C}.done_at": None}]
    if mode != "off":
        legacy_pending: Dict[str, Any] = {f"{_C}.steps.{STEP_LEGACY}.state": None}
        if source != SOURCE_WEBHOOK:
            # Poller only re-claims a finished packet for the legacy link when
            # a webhook delivery asked for it (webhook arrived while the poller
            # held the lease). Never for historical packets.
            legacy_pending[f"{_C}.webhook_received_at"] = {"$ne": None}
        clauses.append(legacy_pending)
    return {"$or": clauses}


def poller_retry_query(mode: Optional[str] = None) -> Dict[str, Any]:
    """Packets the shared handler already started but has not finished."""
    mode = mode or _legacy_payment_link_mode()
    q: Dict[str, Any] = {
        "esign_provider": "docuseal",
        f"{_C}.started_at": {"$ne": None},
    }
    q.update(_work_remaining_clause(SOURCE_POLLER, mode))
    return q


def poller_legacy_drive_query() -> Dict[str, Any]:
    """Packets completed before this handler existed that have no Drive link AND
    no Drive file id recorded (dedup on both)."""
    q: Dict[str, Any] = {
        "esign_provider": "docuseal",
        "status": "signed",
        _C: {"$exists": False},
        "docuseal_submission_id": {"$exists": True, "$ne": None},
    }
    for field in DRIVE_MARKER_FIELDS:
        q[field] = {"$in": [None, ""]}
    return q


def drive_record_reason(doc: Mapping[str, Any]) -> Optional[str]:
    """'drive_link_exists' / 'drive_file_id_exists' when the packet is already
    filed in Drive, else None."""
    if any(doc.get(f) for f in DRIVE_LINK_FIELDS):
        return "drive_link_exists"
    if any(doc.get(f) for f in DRIVE_FILE_ID_FIELDS):
        return "drive_file_id_exists"
    return None


def drive_file_id_from_url(url: Any) -> Optional[str]:
    """Extract the Drive file id from a webViewLink (``/d/<id>/`` or ``?id=<id>``)."""
    import re

    text = str(url or "")
    m = re.search(r"/d/([A-Za-z0-9_-]{10,})", text) or re.search(r"[?&]id=([A-Za-z0-9_-]{10,})", text)
    return m.group(1) if m else None


def drive_backfill_limits(cfg: Optional[Mapping[str, Any]] = None) -> tuple:
    """(batch, delay_seconds) from poller config, clamped to sane bounds.

    ``drive_backfill_batch`` (default 10, 1..50) — max backfill packets per tick.
    ``drive_backfill_delay_seconds`` (default 2.0, 0..30) — pause between them.
    """
    cfg = cfg or {}
    try:
        batch = int(cfg.get("drive_backfill_batch") or DRIVE_BACKFILL_BATCH_DEFAULT)
    except (TypeError, ValueError):
        batch = DRIVE_BACKFILL_BATCH_DEFAULT
    raw_delay = cfg.get("drive_backfill_delay_seconds")
    try:
        delay = float(DRIVE_BACKFILL_DELAY_DEFAULT if raw_delay is None or raw_delay == "" else raw_delay)
    except (TypeError, ValueError):
        delay = DRIVE_BACKFILL_DELAY_DEFAULT
    batch = max(1, min(batch, DRIVE_BACKFILL_BATCH_MAX))
    delay = max(0.0, min(delay, DRIVE_BACKFILL_DELAY_MAX))
    return batch, delay


def is_backfill_packet(doc: Mapping[str, Any]) -> bool:
    """A pre-handler completion: either never claimed (legacy query) or claimed
    with ``preexisting_completion`` (Drive-only; everything else skipped)."""
    rec = doc.get(_C)
    if not isinstance(rec, Mapping) or not rec:
        return str(doc.get("status") or "").lower() == "signed"
    return bool(rec.get("preexisting_completion"))


async def mark_webhook_received(packets_col, packet: Mapping[str, Any], *, event_type: str, now_iso: str) -> None:
    """Record that a completion webhook arrived (drives the legacy switch in 'webhook' mode)."""
    await packets_col.update_one(
        packet_filter(packet),
        {
            "$set": {
                f"{_C}.webhook_received_at": now_iso,
                "docuseal_last_event": event_type,
                "docuseal_last_event_at": now_iso,
            }
        },
    )


async def claim_completion(
    packets_col,
    packet: Mapping[str, Any],
    *,
    source: str,
    mode: Optional[str] = None,
    now: Optional[datetime] = None,
) -> Optional[Dict[str, Any]]:
    """
    Atomically claim the packet's completion. Returns the claimed packet doc
    (post-claim state, includes ``docuseal_completion.claim_token``) or None
    when another run holds a live lease or there is nothing left to do.
    """
    from pymongo import ReturnDocument

    mode = mode or _legacy_payment_link_mode()
    now = now or _now()
    token = uuid.uuid4().hex
    filt = dict(packet_filter(packet))
    filt["$and"] = [_lease_free_clause(now), _work_remaining_clause(source, mode)]
    update = {
        "$set": {
            f"{_C}.claim_token": token,
            f"{_C}.claimed_at": now.isoformat(),
            f"{_C}.claimed_by": source,
            f"{_C}.lease_expires_at": now + timedelta(seconds=_lease_seconds()),
        },
        "$inc": {f"{_C}.runs": 1},
    }
    doc = await packets_col.find_one_and_update(
        filt, update, return_document=ReturnDocument.AFTER
    )
    if not doc:
        return None
    if _completion(doc).get("claim_token") != token:  # defensive (fakes / old servers)
        return None
    return doc


class _LostLease(Exception):
    pass


class _Run:
    """One claimed execution of the completion steps."""

    def __init__(
        self,
        doc: Dict[str, Any],
        *,
        get_col: GetCol,
        source: str,
        submission_id: Any,
        event_type: str,
        cfg: Mapping[str, Any],
    ):
        self.doc = doc
        self.get_col = get_col
        self.packets = get_col("paperwork_packets")
        self.source = source
        self.submission_id = submission_id or doc.get("docuseal_submission_id") or ""
        self.event_type = event_type
        self.cfg = cfg or {}
        self.token = _completion(doc).get("claim_token")
        self.filter = packet_filter(doc)
        self.packet_id = doc.get("packet_id") or str(doc.get("_id", ""))
        self.pfx = _log_prefix(source)
        self.mode = _legacy_payment_link_mode()
        self.result: Dict[str, Any] = {
            "packet_id": self.packet_id,
            "source": source,
            "ran": [],
            "signed": False,
            "filed_drive": False,
            "drive_error": None,
            "done": False,
        }

    # ── Mongo helpers (always scoped to our claim token) ──────────────────
    def _owned(self, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        f = dict(self.filter)
        f[f"{_C}.claim_token"] = self.token
        if extra:
            f.update(extra)
        return f

    async def _update_owned(self, update: Dict[str, Any], extra: Optional[Dict[str, Any]] = None) -> bool:
        res = await self.packets.update_one(self._owned(extra), update)
        return bool(getattr(res, "matched_count", 1))

    async def _reload(self) -> None:
        fresh = await self.packets.find_one(self.filter)
        if fresh:
            self.doc = fresh

    async def stamp(self, step: str, state: str, *, count_attempt: bool = False, **info: Any) -> None:
        now_iso = _now().isoformat()
        sets: Dict[str, Any] = {
            f"{_C}.steps.{step}.state": state,
            f"{_C}.steps.{step}.at": now_iso,
            f"{_C}.steps.{step}.source": self.source,
        }
        for k, v in info.items():
            sets[f"{_C}.steps.{step}.{k}"] = v
        update: Dict[str, Any] = {"$set": sets}
        if count_attempt:
            update["$inc"] = {f"{_C}.steps.{step}.attempts": 1}
        if not await self._update_owned(update):
            raise _LostLease(step)
        self.result["ran"].append((step, state))
        # keep local view in sync for later steps
        steps = self.doc.setdefault(_C, {}).setdefault("steps", {})
        rec = steps.setdefault(step, {})
        rec.update({"state": state, **info})
        if count_attempt:
            rec["attempts"] = int(rec.get("attempts") or 0) + 1

    async def fail(self, step: str, *, error_type: str, reason: str = "") -> None:
        attempts = int(_step(self.doc, step).get("attempts") or 0) + 1
        cap = MAX_ATTEMPTS.get(step, 5)
        state = "gave_up" if attempts >= cap else "failed"
        await self.stamp(step, state, count_attempt=True, error_type=error_type, reason=reason)
        logger.warning(
            "%s completion step=%s %s packet=%s attempt=%s/%s err_type=%s reason=%s",
            self.pfx, step, state, self.packet_id, attempts, cap, error_type, reason,
        )

    # ── common packet facts ────────────────────────────────────────────────
    @property
    def booking_number(self) -> str:
        return self.doc.get("booking_number") or self.doc.get("defendant_booking_number") or ""

    @property
    def defendant_name(self) -> str:
        return self.doc.get("defendant_name") or "Unknown"

    @property
    def surety_id(self) -> str:
        return (self.doc.get("surety_id") or self.doc.get("insurance_company") or "osi").lower().strip()

    def drive_url(self) -> Optional[str]:
        return self.doc.get("signed_pdf_drive_url") or self.doc.get("drive_link") or None

    # ── main ───────────────────────────────────────────────────────────────
    async def execute(self) -> Dict[str, Any]:
        try:
            await self._init_record()
            for step, fn in (
                (STEP_DRIVE, self.step_drive),
                (STEP_PACKET, self.step_packet),
                (STEP_BOND_CASES, self.step_bond_cases),
                (STEP_EVENT, self.step_event),
                (STEP_LEGACY, self.step_legacy_payment_link),
                (STEP_SHARE, self.step_share_invoice),
                (STEP_COURT, self.step_court),
                (STEP_SLACK, self.step_slack),
            ):
                if step != STEP_LEGACY and _is_terminal(self.doc, step):
                    continue
                try:
                    await fn()
                except _LostLease:
                    raise
                except Exception as exc:  # a bug in one step must not stop the others
                    await self.fail(step, error_type=type(exc).__name__, reason="unhandled")
        except _LostLease as lost:
            logger.warning(
                "%s completion lost lease packet=%s at step=%s — another run owns it",
                self.pfx, self.packet_id, lost,
            )
            self.result["lost_lease"] = True
            return self.result
        finally:
            if not self.result.get("lost_lease"):
                try:
                    await self._release()
                except _LostLease:
                    self.result["lost_lease"] = True
        return self.result

    async def _init_record(self) -> None:
        rec = _completion(self.doc)
        if rec.get("started_at"):
            return
        preexisting = str(self.doc.get("status") or "").lower() == "signed"
        now_iso = _now().isoformat()
        if not await self._update_owned({
            "$set": {
                f"{_C}.started_at": now_iso,
                f"{_C}.first_source": self.source,
                f"{_C}.preexisting_completion": preexisting,
            }
        }):
            raise _LostLease("init")
        self.doc.setdefault(_C, {})["started_at"] = now_iso
        if preexisting:
            for step in PREEXISTING_SKIP_STEPS:
                if not _step(self.doc, step):
                    await self.stamp(step, "skipped", reason="completed_before_shared_handler")

    async def _release(self) -> None:
        await self._reload()
        done = all(_is_terminal(self.doc, s) for s in REQUIRED_STEPS)
        now_iso = _now().isoformat()
        sets: Dict[str, Any] = {
            f"{_C}.claim_token": None,
            f"{_C}.lease_expires_at": None,
            f"{_C}.last_run_at": now_iso,
            f"{_C}.last_run_source": self.source,
        }
        if done:
            sets[f"{_C}.done_at"] = now_iso
        if not await self._update_owned({"$set": sets}):
            raise _LostLease("release")
        self.result["done"] = done

    # ── (a) Drive ──────────────────────────────────────────────────────────
    async def step_drive(self) -> None:
        existing = drive_record_reason(self.doc)
        if existing:
            await self.stamp(STEP_DRIVE, "skipped", reason=existing)
            return
        if self.source == SOURCE_POLLER and not bool(self.cfg.get("file_to_drive", True)):
            return  # poller config disabled Drive filing; leave pending, no attempt
        from dashboard.services import docuseal_service as _ds_mod

        now_iso = _now().isoformat()
        ds = _ds_mod.DocuSealService()
        if not self.submission_id or not getattr(ds, "is_configured", False):
            await self.fail(STEP_DRIVE, error_type="NotConfigured", reason="docuseal_not_configured_or_no_submission")
            return
        drive_error: Optional[Dict[str, Any]] = None
        filed: Dict[str, Any] = {}
        try:
            pdf_bytes = await ds.download_combined_pdf(self.submission_id)
        except Exception as exc:
            logger.error("%s PDF download failed packet=%s err_type=%s", self.pfx, self.packet_id, type(exc).__name__)
            pdf_bytes = None
            drive_error = {"error_code": "pdf_download_failed", "error": str(exc)[:300], "at": now_iso}
        if pdf_bytes:
            try:
                filed = ds.file_signed_pdf_to_drive(
                    pdf_bytes,
                    defendant_name=self.defendant_name,
                    surety_id=self.surety_id,
                    packet_id=self.packet_id,
                    booking_number=self.booking_number,
                ) or {}
                if not filed.get("ok"):
                    drive_error = {
                        "error_code": filed.get("error_code"),
                        "error": (filed.get("error") or "")[:300],
                        "auth_mode": filed.get("auth_mode"),
                        "at": now_iso,
                    }
            except Exception as exc:
                logger.error("%s Drive upload error packet=%s err_type=%s", self.pfx, self.packet_id, type(exc).__name__)
                drive_error = {"error_code": "upload_exception", "error": str(exc)[:300], "at": now_iso}
        elif drive_error is None:
            drive_error = {"error_code": "empty_pdf", "error": "empty_pdf", "at": now_iso}

        if filed.get("ok") and filed.get("drive_url"):
            drive_url = filed.get("drive_url")
            sets: Dict[str, Any] = {
                "signed_pdf_drive_url": drive_url,
                "drive_link": drive_url,
                "drive_archive_error": None,
            }
            file_id = drive_file_id_from_url(drive_url)
            if file_id:
                sets["signed_pdf_drive_file_id"] = file_id
            if filed.get("drive_folder_id"):
                sets["drive_folder_id"] = filed.get("drive_folder_id")
                # Legacy field name (historically the folder id) — kept for compat.
                sets["signed_pdf_drive_id"] = filed.get("drive_folder_id")
            if not await self._update_owned({"$set": sets}):
                raise _LostLease(STEP_DRIVE)
            self.doc.update(sets)
            # If bond_cases was already stamped by an earlier run, backfill the link.
            if _is_terminal(self.doc, STEP_BOND_CASES):
                try:
                    await self.get_col("bond_cases").update_one(
                        self._bond_query(), {"$set": {"signed_pdf_drive_url": drive_url}}
                    )
                except Exception as exc:
                    logger.warning("%s bond_cases drive backfill failed packet=%s err_type=%s",
                                   self.pfx, self.packet_id, type(exc).__name__)
            await self.stamp(STEP_DRIVE, "done")
            self.result["filed_drive"] = True
            return

        attempts = int(_step(self.doc, STEP_DRIVE).get("attempts") or 0) + 1
        drive_error = dict(drive_error or {})
        drive_error["attempts"] = attempts
        if attempts >= MAX_ATTEMPTS[STEP_DRIVE]:
            drive_error["gave_up"] = True
        if not await self._update_owned({"$set": {"drive_archive_error": drive_error}}):
            raise _LostLease(STEP_DRIVE)
        self.result["drive_error"] = {
            "error_code": drive_error.get("error_code"),
            "error": (drive_error.get("error") or "")[:200],
        }
        await self.fail(STEP_DRIVE, error_type="DriveFilingFailed", reason=str(drive_error.get("error_code") or ""))

    # ── packet status ──────────────────────────────────────────────────────
    async def step_packet(self) -> None:
        now_iso = _now().isoformat()
        sets: Dict[str, Any] = {
            "status": "signed",
            "esign_provider": "docuseal",
            "docuseal_status": "completed",
            "signed_at": now_iso,
        }
        if self.source == SOURCE_WEBHOOK:
            if self.submission_id:
                sets["docuseal_submission_id"] = self.submission_id
        else:
            sets["signnow_status"] = "signed"
            sets["docuseal_polled_at"] = now_iso
        if not await self._update_owned({"$set": sets}):
            raise _LostLease(STEP_PACKET)
        self.doc.update(sets)
        await self.stamp(STEP_PACKET, "done")
        self.result["signed"] = True

    # ── (d) bond_cases ─────────────────────────────────────────────────────
    def _bond_query(self) -> Dict[str, Any]:
        bond_case_id = self.doc.get("bond_case_id")
        return {"bond_case_id": bond_case_id} if bond_case_id else {"packet_id": self.packet_id}

    async def step_bond_cases(self) -> None:
        bond_update: Dict[str, Any] = {
            "Packet_Status": "signed",
            "Signature_Status": "signed",
            "signed_at": _now().isoformat(),
            "esign_provider": "docuseal",
        }
        if self.drive_url():
            bond_update["signed_pdf_drive_url"] = self.drive_url()
        try:
            await self.get_col("bond_cases").update_one(self._bond_query(), {"$set": bond_update})
        except Exception as exc:
            await self.fail(STEP_BOND_CASES, error_type=type(exc).__name__)
            return
        await self.stamp(STEP_BOND_CASES, "done")

    # ── SSE (in-process dashboard event bus) ───────────────────────────────
    async def step_event(self) -> None:
        try:
            from dashboard.routers import events as _events

            await _events.publish_event(
                "docuseal_submission_completed",
                {
                    "packet_id": self.packet_id,
                    "submission_id": self.submission_id,
                    "defendant_name": self.defendant_name,
                    "drive_url": self.drive_url(),
                    "booking_number": self.booking_number,
                },
            )
        except Exception as exc:
            # Best-effort UI refresh; not worth a retry.
            await self.stamp(STEP_EVENT, "done", ok=False, error_type=type(exc).__name__)
            return
        await self.stamp(STEP_EVENT, "done", ok=True)

    # ── legacy static payment link (behind the owner switch) ───────────────
    async def step_legacy_payment_link(self) -> None:
        if _step(self.doc, STEP_LEGACY).get("state"):
            return  # at-most-once: any stamp (started/done/skipped/failed) is final
        mode = self.mode
        if mode == "off":
            await self.stamp(STEP_LEGACY, "skipped", reason="switch_off")
            logger.info("%s payment_link auto-dispatch off (%s unset/off — default) packet=%s",
                        self.pfx, LEGACY_PAYMENT_LINK_ENV, self.packet_id)
            return
        webhook_seen = bool(_completion(self.doc).get("webhook_received_at")) or self.source == SOURCE_WEBHOOK
        if mode == "webhook" and not webhook_seen:
            return  # poller-only completion: current behavior is "no legacy send"
        # Stamp BEFORE calling so a crash / redelivery can never send twice.
        now_iso = _now().isoformat()
        if not await self._update_owned(
            {"$set": {
                f"{_C}.steps.{STEP_LEGACY}.state": "started",
                f"{_C}.steps.{STEP_LEGACY}.at": now_iso,
                f"{_C}.steps.{STEP_LEGACY}.source": self.source,
                f"{_C}.steps.{STEP_LEGACY}.mode": mode,
            }},
            extra={f"{_C}.steps.{STEP_LEGACY}.state": None},
        ):
            return  # someone else already started it
        self.doc.setdefault(_C, {}).setdefault("steps", {})[STEP_LEGACY] = {"state": "started"}
        legacy_source = "docuseal_submission_completed" if webhook_seen else "docuseal_poller_completed"
        try:
            from dashboard.services import packet_payment_link_service as _pay

            pay_result = await _pay.maybe_send_packet_payment_link(
                packet_id=self.packet_id,
                booking_number=self.booking_number,
                packet_doc=self.doc,
                source=legacy_source,
            )
            pay_result = pay_result or {}
            logger.info(
                "%s payment_link auto-dispatch packet=%s skipped=%s delivered=%s reason=%s",
                self.pfx,
                self.packet_id,
                pay_result.get("skipped"),
                pay_result.get("delivered"),
                pay_result.get("reason") or ("error" if pay_result.get("error") else None),
            )
            # premium_unconfirmed / switch_off / send-once skips → terminal
            # "skipped" (never retried; staff can use the explicit endpoint).
            await self.stamp(
                STEP_LEGACY, "skipped" if pay_result.get("skipped") else "done",
                skipped=bool(pay_result.get("skipped")),
                delivered=bool(pay_result.get("delivered")),
                reason=str(pay_result.get("reason") or "")[:80],
            )
        except Exception as exc:
            logger.warning("%s payment_link auto-dispatch failed (non-fatal, not retried) packet=%s err_type=%s",
                           self.pfx, self.packet_id, type(exc).__name__)
            await self.stamp(STEP_LEGACY, "failed_no_retry", error_type=type(exc).__name__)

    # ── (b) stage-only share invoice (PR #56 semantics) ─────────────────────
    async def step_share_invoice(self) -> None:
        from dashboard.services.docuseal_share_invoice import resolve_share_invoice_bond_id

        share_bond_id, share_skip = resolve_share_invoice_bond_id(self.doc)
        if not share_bond_id:
            # Never guess a bond (no booking # / packet_id fallback) — skip.
            logger.info("%s share_invoice stage skipped reason=%s packet=%s",
                        self.pfx, share_skip, self.packet_id)
            await self.stamp(STEP_SHARE, "skipped", reason=share_skip)
            return
        from dashboard.services import swipesimple_invoice_service as _ss

        try:
            # Stage only: ALWAYS pass dispatch=False explicitly — never rely on
            # the function's default. No customer message from this path.
            share_result = await _ss.maybe_issue_share_invoice_for_bond(
                share_bond_id,
                channel="imessage",
                dispatch=False,
                source=_share_source(self.source),
            )
        except (_ss.SwipeSimpleInvoiceError, _ss.SwipeSimpleInvoiceNotWired) as exc:
            # Fail-closed business rule (missing premium, mismatch, unresolved
            # create…). Retrying could create a second vendor draft → terminal.
            logger.warning(
                "%s share_invoice stage failed (non-fatal) bond_id=%s err_type=%s",
                self.pfx, share_bond_id, type(exc).__name__,
            )
            await self.stamp(STEP_SHARE, "done", ok=False, bond_id=share_bond_id,
                             error_type=type(exc).__name__)
            return
        except Exception as exc:
            logger.warning(
                "%s share_invoice stage failed (non-fatal) bond_id=%s err_type=%s",
                self.pfx, share_bond_id, type(exc).__name__,
            )
            await self.fail(STEP_SHARE, error_type=type(exc).__name__)
            return
        share_result = share_result or {}
        idempotent = (share_result.get("create") or {}).get("idempotent")
        logger.info(
            "%s share_invoice staged bond_id=%s ok=%s idempotent=%s",
            self.pfx, share_bond_id, share_result.get("ok"), idempotent,
        )
        await self.stamp(STEP_SHARE, "done", ok=bool(share_result.get("ok")),
                         bond_id=share_bond_id, idempotent=idempotent)

    # ── (c) court-date sync ────────────────────────────────────────────────
    async def step_court(self) -> None:
        booking_number = self.booking_number
        if not booking_number:
            await self.stamp(STEP_COURT, "skipped", reason="no_booking_number")
            return
        try:
            from dashboard.services import bond_court_seed_service as _seed

            seed_result = await _seed.seed_court_calendar_for_bond(
                booking_number=booking_number,
                source=_share_source(self.source),
            )
        except Exception as exc:
            await self.fail(STEP_COURT, error_type=type(exc).__name__)
            return
        seed_result = seed_result or {}
        logger.info(
            "%s court seed booking=%s success=%s reason=%s gcal=%s",
            self.pfx, booking_number, seed_result.get("success"), seed_result.get("reason"),
            (seed_result.get("gcal") or {}).get("status"),
        )
        await self.stamp(STEP_COURT, "done", success=bool(seed_result.get("success")),
                         reason=str(seed_result.get("reason") or "")[:80])

    # ── (e) Slack ──────────────────────────────────────────────────────────
    async def step_slack(self) -> None:
        slack = os.getenv("SLACK_WEBHOOK_LEADS") or os.getenv("SLACK_WEBHOOK_URL") or ""
        if not slack:
            await self.stamp(STEP_SLACK, "skipped", reason="no_slack_webhook")
            return
        try:
            import httpx

            async with httpx.AsyncClient(timeout=8) as client:
                await client.post(
                    slack,
                    json={
                        "text": (
                            f":white_check_mark: *DocuSeal packet signed* — "
                            f"`{self.packet_id}` | {self.defendant_name}"
                            f"{' | Drive filed' if self.drive_url() else ' | Drive pending'}"
                        )
                    },
                )
        except Exception as exc:
            await self.fail(STEP_SLACK, error_type=type(exc).__name__)
            return
        await self.stamp(STEP_SLACK, "done")


async def run_claimed_completion(
    claimed_doc: Dict[str, Any],
    *,
    get_col: GetCol,
    source: str,
    submission_id: Any = None,
    event_type: str = "submission.completed",
    cfg: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Run every not-yet-terminal step for a packet this caller has claimed."""
    run = _Run(
        claimed_doc,
        get_col=get_col,
        source=source,
        submission_id=submission_id,
        event_type=event_type,
        cfg=cfg or {},
    )
    result = await run.execute()
    logger.info(
        "%s completion run packet=%s done=%s steps=%s",
        run.pfx,
        run.packet_id,
        result.get("done"),
        ",".join(f"{s}:{st}" for s, st in result.get("ran", [])),
    )
    return result


async def run_claimed_completion_safely(claimed_doc: Dict[str, Any], **kwargs: Any) -> Optional[Dict[str, Any]]:
    """Background-task wrapper: never raises; logs exception type only (no PII)."""
    try:
        return await run_claimed_completion(claimed_doc, **kwargs)
    except Exception as exc:
        logger.error(
            "[docuseal_completion] background run failed packet=%s err_type=%s "
            "(lease will expire; poller retries)",
            claimed_doc.get("packet_id"),
            type(exc).__name__,
        )
        return None


async def handle_docuseal_completion(
    packet: Mapping[str, Any],
    *,
    get_col: GetCol,
    source: str,
    submission_id: Any = None,
    event_type: str = "submission.completed",
    cfg: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Claim + run inline (poller path). Returns {'claimed': bool, ...}."""
    claimed = await claim_completion(get_col("paperwork_packets"), packet, source=source)
    if not claimed:
        return {"claimed": False, "packet_id": packet.get("packet_id")}
    res = await run_claimed_completion(
        claimed, get_col=get_col, source=source, submission_id=submission_id,
        event_type=event_type, cfg=cfg,
    )
    res["claimed"] = True
    return res
