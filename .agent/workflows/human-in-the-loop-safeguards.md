# Human-in-the-Loop Safeguards — Gating Workflow

> Centralized instructions for enforcing human authorization gates and preventing destructive state changes in ShamrockLeads.

---

## The Safeguard Rule (SOC II Readiness)

All high-risk operations—including bond state transitions (e.g., active → forfeited/surrendered), premium transactions, document packet voiding, and Power of Attorney inventory voiding—**must implement a two-phase check gate** at both the frontend and backend boundaries.

```
[User Action] ──→ 1. Frontend Confirmation Modal (Checks intent & notes)
                         │
                    2. Backend Signature / Validation Payload (Gated)
                         │
                    3. Immutable Write & Audit Log (audit_events)
```

---

## 🔒 Destructive Kanban & Bond Transitions

Changing a bond’s active lifecycle state (especially to `forfeited` or `surrendered`) has serious financial and regulatory consequences (e.g. triggering POA release, forfeiture collections, or defendant custody changes).

### 1. Frontend Guard (The Confirmation Modal)
Never allow a drag-and-drop or single-click status shift without launching an explicit confirmation modal.

The modal must capture:
- **Confirmation Checklist**: Explicitly check-marking acknowledgment of financial liability.
- **Human Note / Reason**: Mandatory text area explaining the state change.
- **Actor Signature**: The name of the bondsman making the shift.

### 2. Backend Router Verification Payload
The path route processing the transition must not accept raw updates. It must require a structured validation payload:

```python
class BondStatusTransitionRequest(BaseModel):
    new_status: str = Field(..., regex="^(active|monitoring|alert|exonerated|forfeited|surrendered|reinstated)$")
    actor: str = Field(..., min_length=2, description="The bondsman executing the change")
    reason: str = Field(..., min_length=10, description="Detailed explanation for audit log")
    confirm_financials: bool = Field(True, description="Explicit acknowledgment of POA release / liability shifts")
```

The router must:
1. Validate that the new status matches lifecycle rules.
2. Verify that the POA exists and is attached to the target case.
3. Write the update to the database inside a `status_history[]` log.
4. Record an **immutable audit event** to the `audit_events` collection.

---

## 📄 Document Pack Hydration & Voiding

Generating template packets or voiding live SignNow envelopes requires strict verification.

### Gating Hydration
1. **Validation Checklist**: Verify that a Defendant exists, an Indemnitor exists, the Match is validated, the Bonded Case exists with a surety, a Case Number is present, and a Power of Attorney is assigned from the correct surety inventory.
2. If any of these are missing, **block generation immediately** and raise an escalation warning.

### Gating Voiding
1. If a bondsman clicks "Void" on an active SignNow envelope, pop up an alert: `"WARNING: Voiding this packet will invalidate all signatures. This cannot be undone."`
2. Backend must confirm the envelope is not already complete in SignNow before sending the void request to the SignNow API, preventing mismatched signature records.

---

## 📈 Immutable Audit Trail (Rule of the Auditor)

Every gated human action must write to `audit_events`.

```python
async def log_audit_event(
    db,
    event_type: str,
    entity_type: str,
    entity_id: str,
    actor: str,
    reason: str,
    old_state: dict,
    new_state: dict
):
    await db["audit_events"].insert_one({
        "event_id": str(uuid.uuid4()),
        "event_type": event_type,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "actor": actor,
        "reason": reason,
        "old_state": old_state,
        "new_state": new_state,
        "timestamp": datetime.now(timezone.utc).isoformat()
    })
```
Never skip audit logs for human-gated transitions.
