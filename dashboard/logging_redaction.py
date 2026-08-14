"""Redaction helpers for dashboard production logs.

The filter is deliberately applied before a record reaches a handler so both
plain-text and structured logging formatters receive sanitized values.
"""
from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from typing import Any


_REDACTIONS = (
    # Database connection strings can contain both credentials and host details.
    (re.compile(r"mongodb(?:\+srv)?://[^\s]+", re.IGNORECASE), "[REDACTED_DATABASE_URI]"),
    # Authorization headers and common secret-bearing key/value forms.
    (
        re.compile(
            r"(?i)\b(bearer|token|api[_-]?key|secret|password|session(?:id)?|cookie)"
            r"\s*[:=]\s*[^\s,;]+"
        ),
        r"\1=[REDACTED_SECRET]",
    ),
    (re.compile(r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}\b"), "[REDACTED_PHONE]"),
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[REDACTED_SSN]"),
    (re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE), "[REDACTED_EMAIL]"),
    # Common US street-address shape; retain neither house number nor street.
    (
        re.compile(
            r"\b\d{1,6}\s+(?:[A-Za-z0-9.'-]+\s+){0,5}"
            r"(?:Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Drive|Dr|Lane|Ln|Court|Ct|Way)\b\.?,?",
            re.IGNORECASE,
        ),
        "[REDACTED_ADDRESS]",
    ),
)


def redact_log_value(value: Any) -> Any:
    """Return a logging-safe copy of common scalar/container values."""
    if isinstance(value, str):
        for pattern, replacement in _REDACTIONS:
            value = pattern.sub(replacement, value)
        return value
    if isinstance(value, Mapping):
        return {key: redact_log_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return tuple(redact_log_value(item) for item in value)
    if isinstance(value, list):
        return [redact_log_value(item) for item in value]
    return value


class SensitiveDataRedactionFilter(logging.Filter):
    """Remove common PII and credentials from message and structured fields."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact_log_value(record.msg)
        record.args = redact_log_value(record.args)

        standard_fields = logging.makeLogRecord({}).__dict__
        for key, value in vars(record).items():
            if key not in standard_fields and key not in {"message", "asctime"}:
                setattr(record, key, redact_log_value(value))
        return True

