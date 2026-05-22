# Shamrock Social Engine — Content Repurposing & Multi-Platform Posting
# Converts blog posts and arrest intelligence into platform-specific social content.
# Runs as a separate Docker service on shamrock-net (port 5060 internal).

__version__ = "0.1.0"

# ── Canonical exports (for clean imports) ──
# These match what main.py and external code expect.

from social.models import SocialPost, PostStatus, Platform, ContentTone
from social.humanizer import ContentHumanizer
from social.grok_client import GrokClient
from social.queue_manager import QueueManager
from social.repurposer import ContentRepurposer
from social.scheduler import SocialScheduler
from social.ingestion import ContentIngester
from social.image_gen import ImageGenerator
from social.gmail_scanner import GmailGrokScanner

# ── Convenience aliases (for discoverability) ──
Humanizer = ContentHumanizer
PostScheduler = SocialScheduler
ContentIngestion = ContentIngester

__all__ = [
    "SocialPost",
    "PostStatus",
    "Platform",
    "ContentTone",
    "ContentHumanizer",
    "Humanizer",
    "GrokClient",
    "QueueManager",
    "ContentRepurposer",
    "SocialScheduler",
    "PostScheduler",
    "ContentIngester",
    "ContentIngestion",
    "ImageGenerator",
    "GmailGrokScanner",
]
