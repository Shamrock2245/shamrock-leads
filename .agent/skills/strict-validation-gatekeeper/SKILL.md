---
name: strict-validation-gatekeeper
description: Enforces 'The Chain is Law' by ensuring no case logic executes without a validated match.
---

# strict-validation-gatekeeper

## Mission
You enforce "The Chain is Law" (ArrestLead -> Defendant -> Indemnitor -> Match -> BondCase -> Packet).

## Directives
1. **No Skipping Steps**: You cannot generate a SignNow packet without a `BondCase`. You cannot create a `BondCase` without a validated `Match`.
2. **Confidence Gating**: Indemnitor-to-Defendant matches must clear an 85% confidence threshold or be explicitly flagged for human review.
3. **Surety and POA**: A case must have an assigned `Surety_ID` and a valid `POA_Number` before it is considered active.

## When to use
Whenever building workflows involving case creation, matching indemnitors, or generating bond paperwork.
