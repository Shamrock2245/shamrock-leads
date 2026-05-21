"""
Shamrock Social Engine — Configuration
========================================
Loads platform credentials, feature flags, and engine settings from environment.
All secrets come from .env (shared with the shamrock-leads Docker stack).
"""

import os
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class PlatformConfig:
    """Credentials and settings for a single social platform."""
    enabled: bool = False
    name: str = ""


@dataclass
class TwitterConfig(PlatformConfig):
    name: str = "Twitter / X"
    api_key: str = ""
    api_secret: str = ""
    access_token: str = ""
    access_secret: str = ""
    bearer_token: str = ""
    max_tweet_length: int = 280
    max_thread_length: int = 5


@dataclass
class LinkedInConfig(PlatformConfig):
    name: str = "LinkedIn"
    client_id: str = ""
    client_secret: str = ""
    access_token: str = ""
    organization_urn: str = ""  # urn:li:organization:XXXXXXXX
    max_post_length: int = 3000


@dataclass
class FacebookConfig(PlatformConfig):
    name: str = "Facebook"
    page_id: str = ""
    page_access_token: str = ""
    max_post_length: int = 63206


@dataclass
class InstagramConfig(PlatformConfig):
    name: str = "Instagram"
    business_account_id: str = ""
    # Uses Facebook page access token
    max_caption_length: int = 2200
    max_hashtags: int = 30
    max_carousel_items: int = 10


@dataclass
class SocialEngineConfig:
    """Top-level configuration for the social engine."""

    # MongoDB
    mongodb_uri: str = ""
    mongodb_db_name: str = "ShamrockBailDB"

    # OpenAI (shared with shamrock-leads)
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"

    # Grok / xAI
    xai_api_key: str = ""
    xai_chat_model: str = "grok-3-mini"
    xai_image_model: str = "grok-2-image"
    grok_enabled: bool = False

    # Humanizer (29-pattern AI writing detector)
    humanizer_enabled: bool = True

    # Content settings
    use_dalle: bool = False
    auto_approve: bool = False
    posting_timezone: str = "America/New_York"
    compliance_disclaimer: str = (
        "Shamrock Bail Bonds | Licensed & Insured | "
        "1528 Broadway, Ft. Myers, FL 33901 | (239) 552-1349"
    )

    # Slack
    slack_webhook_social: str = ""

    # Blog directory (mounted read-only from shamrock-leads)
    blog_posts_dir: str = "/app/blog/posts"

    # Scheduling
    ingestion_interval_hours: int = 6
    posting_check_interval_minutes: int = 30
    analytics_pull_interval_hours: int = 24
    gmail_scan_interval_hours: int = 2    # Grok email harvesting interval
    grok_news_interval_hours: int = 12    # Auto-generate news-hook posts

    # Retry settings
    max_post_retries: int = 3
    retry_backoff_seconds: list = field(default_factory=lambda: [60, 300, 900])

    # Approval
    auto_expire_days: int = 7  # Reject unapproved posts after N days

    # Platform configs
    twitter: TwitterConfig = field(default_factory=TwitterConfig)
    linkedin: LinkedInConfig = field(default_factory=LinkedInConfig)
    facebook: FacebookConfig = field(default_factory=FacebookConfig)
    instagram: InstagramConfig = field(default_factory=InstagramConfig)


def load_config() -> SocialEngineConfig:
    """Load configuration from environment variables."""

    def _bool(key: str, default: str = "false") -> bool:
        return os.getenv(key, default).lower() in ("true", "1", "yes")

    return SocialEngineConfig(
        # MongoDB
        mongodb_uri=os.getenv("MONGODB_URI", ""),
        mongodb_db_name=os.getenv("MONGODB_DB_NAME", "ShamrockBailDB"),

        # OpenAI
        openai_api_key=os.getenv("OPENAI_API_KEY", ""),
        openai_model=os.getenv("SOCIAL_OPENAI_MODEL", "gpt-4o-mini"),

        # Grok / xAI (check both XAI_API_KEY and legacy GROK_API_KEY)
        xai_api_key=os.getenv("XAI_API_KEY", os.getenv("GROK_API_KEY", "")),
        xai_chat_model=os.getenv("XAI_CHAT_MODEL", "grok-3-mini"),
        xai_image_model=os.getenv("XAI_IMAGE_MODEL", "grok-2-image"),
        grok_enabled=_bool("SOCIAL_GROK_ENABLED"),

        # Humanizer
        humanizer_enabled=_bool("SOCIAL_HUMANIZER_ENABLED", "true"),

        # Content
        use_dalle=_bool("SOCIAL_USE_DALLE"),
        auto_approve=_bool("SOCIAL_AUTO_APPROVE"),
        posting_timezone=os.getenv("SOCIAL_POSTING_TIMEZONE", "America/New_York"),

        # Slack
        slack_webhook_social=os.getenv("SLACK_WEBHOOK_SOCIAL", ""),

        # Blog
        blog_posts_dir=os.getenv("SOCIAL_BLOG_POSTS_DIR", "/app/blog/posts"),

        # Scheduling
        ingestion_interval_hours=int(os.getenv("SOCIAL_INGESTION_INTERVAL_HOURS", "6")),
        posting_check_interval_minutes=int(os.getenv("SOCIAL_POSTING_INTERVAL_MINUTES", "30")),
        gmail_scan_interval_hours=int(os.getenv("SOCIAL_GMAIL_SCAN_INTERVAL_HOURS", "2")),
        grok_news_interval_hours=int(os.getenv("SOCIAL_GROK_NEWS_INTERVAL_HOURS", "12")),

        # Twitter
        twitter=TwitterConfig(
            enabled=_bool("SOCIAL_TWITTER_ENABLED"),
            api_key=os.getenv("TWITTER_API_KEY", ""),
            api_secret=os.getenv("TWITTER_API_SECRET", ""),
            access_token=os.getenv("TWITTER_ACCESS_TOKEN", ""),
            access_secret=os.getenv("TWITTER_ACCESS_SECRET", ""),
            bearer_token=os.getenv("TWITTER_BEARER_TOKEN", ""),
        ),

        # LinkedIn
        linkedin=LinkedInConfig(
            enabled=_bool("SOCIAL_LINKEDIN_ENABLED"),
            client_id=os.getenv("LINKEDIN_CLIENT_ID", ""),
            client_secret=os.getenv("LINKEDIN_CLIENT_SECRET", ""),
            access_token=os.getenv("LINKEDIN_ACCESS_TOKEN", ""),
            organization_urn=os.getenv("LINKEDIN_ORGANIZATION_URN", ""),
        ),

        # Facebook
        facebook=FacebookConfig(
            enabled=_bool("SOCIAL_FACEBOOK_ENABLED"),
            page_id=os.getenv("FACEBOOK_PAGE_ID", ""),
            page_access_token=os.getenv("FACEBOOK_PAGE_ACCESS_TOKEN", ""),
        ),

        # Instagram
        instagram=InstagramConfig(
            enabled=_bool("SOCIAL_INSTAGRAM_ENABLED"),
            business_account_id=os.getenv("INSTAGRAM_BUSINESS_ACCOUNT_ID", ""),
        ),
    )


# Singleton — loaded once on import
settings = load_config()


def get_enabled_platforms() -> list[str]:
    """Return list of enabled platform names."""
    platforms = []
    if settings.twitter.enabled:
        platforms.append("twitter")
    if settings.linkedin.enabled:
        platforms.append("linkedin")
    if settings.facebook.enabled:
        platforms.append("facebook")
    if settings.instagram.enabled:
        platforms.append("instagram")
    return platforms
