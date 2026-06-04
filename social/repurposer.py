"""
Shamrock Social Engine — Content Repurposer
=============================================
Uses GPT-4o to convert blog posts and arrest stats into platform-optimized
social media content with A/B tone variants.

Platform-specific rules:
  - Twitter/X: 280 chars max, thread format for long content (max 5 tweets)
  - LinkedIn: Professional tone, 3000 chars, 3-5 hashtags, CTA
  - Facebook: Casual-professional, 500 chars ideal, question hook
  - Instagram: Carousel script, 2200 char caption, 20-30 hashtags

Compliance: Bail industry disclaimers auto-appended where required.
"""

from __future__ import annotations

import logging
from typing import Optional

from social.config import settings
from social.models import (
    SocialPost,
    PostStatus,
    Platform,
    SourceType,
    ContentTone,
    ContentVariant,
)
from social.humanizer import ContentHumanizer

logger = logging.getLogger("social.repurposer")

# ── System Prompts ─────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are a social media content strategist for Shamrock Bail Bonds, a premium Florida bail bond agency.

BRAND IDENTITY:
- Company: Shamrock Bail Bonds
- Tagline: "The Uber of Bail Bonds — Fast. Frictionless. Everywhere."
- Location: 1528 Broadway, Ft. Myers, FL 33901
- Phone: (239) 552-1349
- Website: shamrockbailbonds.biz
- Tone: Professional yet empathetic. Modern, tech-savvy. Never sleazy or aggressive.
- Counties served: Lee, Charlotte, Collier, DeSoto, Hendry, Manatee, Sarasota

KEY DIFFERENTIATORS:
- 24/7 AI-powered intake (phone, web, Telegram)
- 100% digital paperwork (sign from your phone in 5 min)
- Real-time arrest monitoring across 50+ FL counties
- Payment plans available

COMPLIANCE RULES:
- Never promise specific outcomes
- Never disparage other bail bond companies
- Never use fear-mongering language
- Always include company info in longer posts
- Do NOT include any personally identifiable information (names, booking numbers, etc.)

HASHTAG GUIDELINES:
- Florida-specific: #FloridaBailBonds #SWFLBailBonds #LeeCountyBailBonds
- General: #BailBonds #GetThemHome #BailBondsman #ArrestHelp
- Topic-specific: generate 3-5 relevant to the content
"""


PLATFORM_INSTRUCTIONS = {
    Platform.TWITTER: """
ROLE: Sharp, punchy Florida bail bonds expert
TONE: Urgent, direct, scroll-stopping
FORMAT: Twitter/X
- Single tweet: max 280 characters (STRICT — count every character including spaces and hashtags)
- Thread: max 5 tweets, each max 280 chars, numbered (1/5, 2/5, etc.) — separate with ---TWEET---
- Lead with a hook emoji in the first tweet
- Thread format for educational content, single for quick tips
- Add 2-3 hashtags at the end (they count toward character limit!)
- Include a CTA in the last tweet: phone number (239) 332-2245 or website
- No links in the first tweet (hurts engagement)
VOICE: "We're the agency that picks up at 2AM."
AUDIENCE: Attorneys, families in crisis, true crime followers
""",
    Platform.LINKEDIN: """
ROLE: Licensed Florida bail bond industry authority
TONE: Professional, educational, thought-leadership
FORMAT: LinkedIn
- 1500-3000 characters. Paragraph structure. Cite statutes (Ch. 648, 903) where relevant.
- Open with a bold hook (stat, question, or industry insight)
- Use short paragraphs (2-3 sentences max), line breaks between each
- Include 3-5 hashtags at the bottom
- End with a call-to-action
- Use emojis sparingly (1-2 per post, professional ones only)
VOICE: "Industry insight from a licensed Florida bail bond agency."
AUDIENCE: Attorneys, insurance professionals, surety companies, lawmakers
""",
    Platform.FACEBOOK: """
ROLE: Local community business, trusted neighbor
TONE: Conversational, informative, community-oriented
FORMAT: Facebook Page Post
- 300-500 characters is optimal for engagement
- Open with a question or empathetic statement
- Conversational, warm tone — like talking to a worried family member
- Include a link to the full blog post if available
- 2-3 hashtags maximum
- End with a question to drive engagement or a CTA (call us, visit website)
- Use emojis moderately
VOICE: "Your Fort Myers bail bond neighbor since 2012."
AUDIENCE: Local community, families, referral network
""",
    Platform.INSTAGRAM: """
ROLE: Community-facing, empathetic, visual-first
TONE: Warm, supportive, informative but accessible
FORMAT: Instagram Caption
- Max 2200 characters for caption
- Open with a strong hook (first line shows in preview — make it count)
- Use line breaks for readability
- Include 15-20 hashtags (mix of broad and niche) — separate from content with: . . . . .
- For carousel posts: suggest 5 slide headlines
- End with a CTA: phone (239) 332-2245 or website
- Emoji-rich but not excessive
VOICE: "We help families navigate the hardest day of their lives."
AUDIENCE: General public, families, community
""",
}


class ContentRepurposer:
    """Converts blog content and arrest stats into platform-optimized social posts."""

    def __init__(self):
        self._client = None
        self.humanizer = ContentHumanizer()

    def _get_client(self):
        """Lazy-load OpenAI client."""
        if self._client is None:
            if not settings.openai_api_key:
                logger.warning("⚠️  OPENAI_API_KEY not set — repurposer will use fallback templates")
                return None
            try:
                from openai import OpenAI
                self._client = OpenAI(api_key=settings.openai_api_key)
            except ImportError:
                logger.warning("openai package not installed — using fallback templates")
                return None
        return self._client

    # ── Blog Repurposing ──────────────────────────────────────────────────

    async def repurpose_blog(
        self,
        title: str,
        body: str,
        excerpt: str,
        categories: list[str],
        tags: list[str],
        platform: Platform,
        blog_url: str,
    ) -> list[SocialPost]:
        """
        Generate social post variants from a blog post.
        Returns 1-2 variants per platform (educational + urgent tone).
        """
        client = self._get_client()

        if not client:
            # Fallback: simple excerpt-based post
            return [self._fallback_blog_post(title, excerpt, platform, blog_url, tags)]

        variants = []

        # Generate educational variant
        edu_post = await self._llm_repurpose(
            client=client,
            title=title,
            body=body,
            excerpt=excerpt,
            platform=platform,
            tone=ContentTone.EDUCATIONAL,
            blog_url=blog_url,
            tags=tags,
        )
        if edu_post:
            variants.append(edu_post)

        # Generate urgent/empathetic variant (different angle)
        urgent_post = await self._llm_repurpose(
            client=client,
            title=title,
            body=body,
            excerpt=excerpt,
            platform=platform,
            tone=ContentTone.URGENT,
            blog_url=blog_url,
            tags=tags,
        )
        if urgent_post:
            variants.append(urgent_post)

        return variants or [self._fallback_blog_post(title, excerpt, platform, blog_url, tags)]

    async def repurpose_arrest_stats(
        self,
        stats: list[dict],
        platform: Platform,
    ) -> list[SocialPost]:
        """
        Generate educational social content from arrest statistics.
        NO PII — county-level aggregates only.
        """
        client = self._get_client()

        stats_text = "\n".join(
            f"- {s['county']}: {s['total']} arrests, avg bond ${s['avg_bond']:,.0f}"
            for s in stats
        )

        if not client:
            return [self._fallback_stats_post(stats, platform)]

        prompt = f"""Create a social media post about recent arrest activity in Southwest Florida.

STATS (last 7 days):
{stats_text}

RULES:
- DO NOT include any names, booking numbers, or PII
- Focus on educational content (what to do if arrested, know your rights)
- Use the stats as a hook ("X arrests in Lee County this week — here's what you need to know")
- Include Shamrock Bail Bonds as the helpful resource
- Be empathetic, not fear-mongering

Return ONLY the post content, ready to publish. Include hashtags."""

        try:
            import asyncio
            response = await asyncio.to_thread(
                client.chat.completions.create,
                model=settings.openai_model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT + PLATFORM_INSTRUCTIONS[platform]},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=800,
                temperature=0.8,
            )

            content = response.choices[0].message.content.strip()
            hashtags = self._extract_hashtags(content)

            variant = ContentVariant.SINGLE
            if platform == Platform.TWITTER and len(content) > 280:
                variant = ContentVariant.THREAD

            return [SocialPost(
                source_type=SourceType.ARREST_INTEL,
                platform=platform,
                content=content,
                hashtags=hashtags,
                variant=variant,
                tone=ContentTone.EDUCATIONAL,
                tone_confidence=0.85,
                compliance_disclaimer=settings.compliance_disclaimer,
                status=PostStatus.PENDING,
            )]

        except Exception as e:
            logger.error("❌ LLM arrest stats repurpose failed: %s", e)
            return [self._fallback_stats_post(stats, platform)]

    # ── LLM Core ──────────────────────────────────────────────────────────

    async def _llm_repurpose(
        self,
        client,
        title: str,
        body: str,
        excerpt: str,
        platform: Platform,
        tone: ContentTone,
        blog_url: str,
        tags: list[str],
    ) -> Optional[SocialPost]:
        """Call GPT-4o to generate a single platform-specific post."""

        tone_instruction = {
            ContentTone.EDUCATIONAL: "TONE: Educational, authoritative. You are an expert helping people understand a process.",
            ContentTone.URGENT: "TONE: Urgent, empathetic. You are talking to a worried family member who needs help NOW.",
            ContentTone.PROFESSIONAL: "TONE: Professional, thought-leadership. Like a LinkedIn post from a respected industry expert.",
            ContentTone.CASUAL: "TONE: Casual, conversational. Like a friend explaining something simply.",
        }

        # Truncate body for context window
        body_truncated = body[:3000] if len(body) > 3000 else body

        prompt = f"""Repurpose this blog post into a social media post.

BLOG TITLE: {title}
BLOG EXCERPT: {excerpt}
BLOG URL: {blog_url}
TAGS: {', '.join(tags)}

FULL BLOG CONTENT:
{body_truncated}

{tone_instruction.get(tone, '')}

Return ONLY the post content, ready to publish. Include hashtags at the end.
For Twitter threads, separate each tweet with "---TWEET---" markers."""

        try:
            import asyncio
            response = await asyncio.to_thread(
                client.chat.completions.create,
                model=settings.openai_model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT + PLATFORM_INSTRUCTIONS[platform]},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=1000,
                temperature=0.8,
            )

            content = response.choices[0].message.content.strip()

            # ── Humanizer Post-Processing ──────────────────────────────
            if settings.humanizer_enabled:
                score_before = self.humanizer.score_ai_likelihood(content)
                logger.debug(
                    "🔍 Pre-humanize AI score: %d (%s)",
                    score_before["score"], score_before["verdict"],
                )
                content = await self.humanizer.humanize(
                    content,
                    platform=platform.value,
                    max_length=280 if platform == Platform.TWITTER else None,
                )
                score_after = self.humanizer.score_ai_likelihood(content)
                logger.info(
                    "✅ Humanized: %d→%d (%s→%s)",
                    score_before["score"], score_after["score"],
                    score_before["verdict"], score_after["verdict"],
                )

            hashtags = self._extract_hashtags(content)

            # Determine variant
            variant = ContentVariant.SINGLE
            thread_parts = []

            if platform == Platform.TWITTER:
                if "---TWEET---" in content:
                    thread_parts = [t.strip() for t in content.split("---TWEET---") if t.strip()]
                    variant = ContentVariant.THREAD
                    content = thread_parts[0] if thread_parts else content
                elif len(content) > 280:
                    variant = ContentVariant.THREAD
            elif platform == Platform.INSTAGRAM:
                variant = ContentVariant.CAROUSEL
            elif platform == Platform.LINKEDIN:
                variant = ContentVariant.ARTICLE

            return SocialPost(
                source_type=SourceType.BLOG,
                platform=platform,
                content=content,
                thread_parts=thread_parts,
                hashtags=hashtags,
                link_url=blog_url,
                cta="📞 (239) 552-1349 | 🌐 shamrockbailbonds.biz",
                variant=variant,
                tone=tone,
                tone_confidence=0.9,
                compliance_disclaimer=settings.compliance_disclaimer,
                status=PostStatus.PENDING,
            )

        except Exception as e:
            logger.error("❌ LLM repurpose failed for %s/%s: %s", platform.value, tone.value, e)
            return None

    # ── Fallback Templates ────────────────────────────────────────────────

    def _fallback_blog_post(
        self,
        title: str,
        excerpt: str,
        platform: Platform,
        blog_url: str,
        tags: list[str],
    ) -> SocialPost:
        """Simple template-based fallback when OpenAI is unavailable."""
        hashtag_str = " ".join(f"#{t.replace(' ', '')}" for t in tags[:5])

        templates = {
            Platform.TWITTER: f"📰 {title}\n\n{excerpt[:200]}...\n\n🔗 {blog_url}\n\n{hashtag_str}",
            Platform.LINKEDIN: (
                f"📰 {title}\n\n{excerpt}\n\n"
                f"Read the full article: {blog_url}\n\n"
                f"📞 Shamrock Bail Bonds | (239) 552-1349\n\n{hashtag_str}"
            ),
            Platform.FACEBOOK: f"📰 {title}\n\n{excerpt}\n\n🔗 Read more: {blog_url}\n\n{hashtag_str}",
            Platform.INSTAGRAM: (
                f"📰 {title}\n\n{excerpt}\n\n"
                f"📞 Call (239) 552-1349 or visit shamrockbailbonds.biz\n\n"
                f". . . . .\n{hashtag_str} #FloridaBailBonds #SWFLBailBonds #BailBonds"
            ),
        }

        content = templates.get(platform, templates[Platform.TWITTER])
        return SocialPost(
            source_type=SourceType.BLOG,
            platform=platform,
            content=content,
            hashtags=self._extract_hashtags(content),
            link_url=blog_url,
            variant=ContentVariant.SINGLE,
            tone=ContentTone.EDUCATIONAL,
            tone_confidence=0.5,
            compliance_disclaimer=settings.compliance_disclaimer,
            status=PostStatus.PENDING,
        )

    def _fallback_stats_post(self, stats: list[dict], platform: Platform) -> SocialPost:
        """Fallback arrest stats post without LLM."""
        top = stats[0] if stats else {"county": "SWFL", "total": 0}
        content = (
            f"📊 {top['total']} arrests in {top['county']} County this week.\n\n"
            f"If your loved one was arrested, know your rights. "
            f"Shamrock Bail Bonds is available 24/7.\n\n"
            f"📞 (239) 552-1349\n🌐 shamrockbailbonds.biz\n\n"
            f"#FloridaBailBonds #BailBonds #{top['county'].replace(' ', '')}County"
        )
        return SocialPost(
            source_type=SourceType.ARREST_INTEL,
            platform=platform,
            content=content,
            hashtags=self._extract_hashtags(content),
            variant=ContentVariant.SINGLE,
            tone=ContentTone.EDUCATIONAL,
            tone_confidence=0.5,
            compliance_disclaimer=settings.compliance_disclaimer,
            status=PostStatus.PENDING,
        )

    # ── Helpers ───────────────────────────────────────────────────────────

    def _extract_hashtags(self, text: str) -> list[str]:
        """Extract #hashtags from post content."""
        import re
        return re.findall(r"#(\w+)", text)
