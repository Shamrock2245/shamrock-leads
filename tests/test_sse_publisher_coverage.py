"""SSE contract test — every named event the frontend listens for must have a
backend publisher (or a documented external relay).

Guards against the "dead listener" regression class fixed in v2.16.x:
frontend `es.addEventListener('<event>')` handlers silently never fire if no
backend code publishes that event name.
"""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# Events that arrive through the scraper-event webhook relay
# (POST /api/webhooks/scraper-event → publish_event(event_type, ...)) rather
# than a literal publish_event("<name>") call in dashboard code.
RELAYED_VIA_SCRAPER_WEBHOOK = {"new_arrest", "hot_lead", "scraper_error"}

# SSE plumbing / DOM events that are not domain events.
NON_DOMAIN = {"heartbeat", "connected", "click", "keydown", "message"}

# payment_confirmed is an intentional legacy alias of payment_received in the
# frontend; publishing both would double-toast. Documented exemption.
DOCUMENTED_EXEMPT = {"payment_confirmed"}


def _frontend_listeners() -> set:
    src = (REPO / "dashboard" / "sl-core.js").read_text()
    return set(re.findall(r"addEventListener\('([a-z_]+)'", src))


def _backend_published() -> set:
    events = set()
    pattern = re.compile(r"(?:publish_event|emit_event)\(\s*['\"]([a-z_]+)['\"]")
    for base in ("dashboard", "scrapers", "writers"):
        for py in (REPO / base).rglob("*.py"):
            try:
                events |= set(pattern.findall(py.read_text()))
            except Exception:
                continue
    return events


def test_all_frontend_listeners_have_publishers():
    listeners = _frontend_listeners()
    published = _backend_published()
    exempt = RELAYED_VIA_SCRAPER_WEBHOOK | NON_DOMAIN | DOCUMENTED_EXEMPT
    missing = listeners - published - exempt
    assert not missing, (
        f"Frontend listens for SSE events with no backend publisher: {sorted(missing)}. "
        "Either add a publish_event() call or add a documented exemption."
    )


def test_scraper_relay_events_still_emitted_by_base_scraper():
    """The relayed events must be emitted by BaseScraper's broadcast hook."""
    src = (REPO / "scrapers" / "base_scraper.py").read_text()
    for evt in RELAYED_VIA_SCRAPER_WEBHOOK:
        assert evt in src, f"BaseScraper no longer emits '{evt}'"
