# Social platform adapters
# PostizAdapter routes ALL posts through self-hosted Postiz (30+ platforms).
# Legacy per-platform adapters (twitter.py, linkedin.py, etc.) kept for reference
# but no longer used in production.

from social.platforms.postiz import PostizAdapter, PostizClient, get_postiz_client
