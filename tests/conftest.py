"""Shared test setup.

Production must provide SECRET_KEY (session signing fails closed without it,
see dashboard/auth/dev_mode.py). Mirror that here with a synthetic value so
suites exercising signed sessions behave like a correctly configured deploy.
Tests that cover the missing-key path delete it explicitly via monkeypatch.
"""

import os

os.environ.setdefault("SECRET_KEY", "synthetic-test-session-key-not-a-secret")
