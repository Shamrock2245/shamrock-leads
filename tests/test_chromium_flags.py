"""Chromium memory-flag helpers used by every browser launcher."""

from scrapers.chromium_flags import (
    CHROMIUM_MEMORY_ARGS,
    chromium_launch_args,
    playwright_launch_kwargs,
)


def test_memory_args_include_low_end_mode():
    joined = " ".join(CHROMIUM_MEMORY_ARGS)
    assert "--enable-low-end-device-mode" in joined
    assert "--renderer-process-limit=2" in joined
    assert "IsolateOrigins" in joined
    assert "--disable-dev-shm-usage" in joined


def test_extras_override_duplicate_keys():
    args = chromium_launch_args(["--renderer-process-limit=4"])
    limits = [a for a in args if a.startswith("--renderer-process-limit")]
    assert limits == ["--renderer-process-limit=4"]


def test_playwright_kwargs_headless():
    kw = playwright_launch_kwargs(headless=True)
    assert kw["headless"] is True
    assert "--no-sandbox" in kw["args"]
    assert "--enable-low-end-device-mode" in kw["args"]
