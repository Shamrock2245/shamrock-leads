"""Shared Chromium launch flags — keep every browser path lean.

Jail scrapers spawn a full Chromium per county. Default Chrome isolation
(process-per-site, GPU, extensions, background services) burns 150–300 MB
per instance. These flags keep stealth intact while cutting renderer
count and unused services.

Used by DrissionPage, Playwright/Patchright, and nodriver launchers.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

# Lean flags that do not disable JS or images (captchas / CF still work).
CHROMIUM_MEMORY_ARGS: List[str] = [
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--disable-software-rasterizer",
    "--in-process-gpu",
    "--disable-extensions",
    "--disable-component-extensions-with-background-pages",
    "--disable-background-networking",
    "--disable-background-timer-throttling",
    "--disable-backgrounding-occluded-windows",
    "--disable-renderer-backgrounding",
    "--disable-breakpad",
    "--disable-component-update",
    "--disable-domain-reliability",
    "--disable-sync",
    "--disable-translate",
    "--disable-default-apps",
    "--disable-hang-monitor",
    "--disable-ipc-flooding-protection",
    "--disable-client-side-phishing-detection",
    "--disable-features=TranslateUI,AudioServiceOutOfProcess,IsolateOrigins,site-per-process,MediaRouter,OptimizationHints,InterestFeedContentSuggestions",
    "--disable-site-isolation-trials",
    "--renderer-process-limit=2",
    "--enable-low-end-device-mode",
    "--mute-audio",
    "--no-first-run",
    "--no-default-browser-check",
    "--metrics-recording-only",
    "--disk-cache-size=1",
    "--media-cache-size=1",
    "--aggressive-cache-discard",
]


def chromium_launch_args(extra: Optional[List[str]] = None) -> List[str]:
    """Return de-duplicated Chromium CLI args, extras last (caller wins)."""
    seen = set()
    out: List[str] = []
    for arg in list(CHROMIUM_MEMORY_ARGS) + list(extra or []):
        key = arg.split("=", 1)[0]
        if key in seen:
            # Replace earlier value so extras override
            out = [a for a in out if a.split("=", 1)[0] != key]
        seen.add(key)
        out.append(arg)
    return out


def playwright_launch_kwargs(
    *,
    headless: bool = True,
    extra_args: Optional[List[str]] = None,
    **overrides: Any,
) -> Dict[str, Any]:
    """kwargs for ``pw.chromium.launch`` / Patchright."""
    kwargs: Dict[str, Any] = {
        "headless": headless,
        "args": chromium_launch_args(extra_args),
    }
    kwargs.update(overrides)
    return kwargs


def apply_drission_memory_flags(co) -> Any:
    """Apply lean flags to a DrissionPage ``ChromiumOptions`` instance."""
    for arg in CHROMIUM_MEMORY_ARGS:
        try:
            co.set_argument(arg)
        except Exception:
            continue
    return co
