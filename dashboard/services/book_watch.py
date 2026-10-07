"""Active Book Watch constants for the review queue (BailSafe P0 slice A1).

Billing enrollments, Stripe meters, and tenant fan-out stay out of this module.
"""

# Default-on watch set. Forfeited bonds stay watched; a tenant toggle is later.
WATCH_STATUSES = ("active", "monitoring", "alert", "reinstated", "forfeited")

# Staff may approve an indemnitor text only at these levels. Never automatic.
INDEMNITOR_TEXT_CONFIDENCE = frozenset({"confirmed", "high"})

PENDING_REVIEW_STATUS = "pending_review"
IDENTITY_CHECK_STATUS = "unconfirmed_triage"
QUEUE_STATUSES = (PENDING_REVIEW_STATUS, IDENTITY_CHECK_STATUS)
