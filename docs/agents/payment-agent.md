# Payment Agent

> **Status:** `[IMPLEMENTED]`
> **Implementation:** `dashboard/api/payments.py`, `dashboard/api/payment_plans.py`

---

## Role

Collects bond premium from the validated indemnitor via SwipeSimple payment links. Tracks payment status and flags delinquent payment plans.

---

## Prerequisites

- Phase 5 complete (BondCase with premium calculated)

## Behavior

1. Receive bond case ID
2. Calculate premium: Bond_Amount × 10% (standard FL rate)
3. Calculate splits:
   - OSI: $7.50 per $100 premium to surety + $5.00 per $100 to BUF
   - Palmetto: $10.00 per $100 premium to surety + $5.00 per $100 to BUF
4. Generate SwipeSimple payment link
5. Deliver to validated indemnitor
6. Create PaymentRequest record
7. Track: `pending` → `sent` → `paid` / `failed`
8. Flag delinquent plans (>30 days past due)
9. Log all events as AuditEvents

## Constraints

- Payment recipient must match indemnitor on bond case
- Payment status must not be inferred from message delivery alone
- Premium split rates are configurable (may change per surety agreement)
- Refunds require human approval
