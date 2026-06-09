"""
Shamrock Social Engine — Grok (xAI) Client
=============================================
Integrates xAI's Grok API for:
  1. Content generation with real-time news awareness (Grok has live web access)
  2. Image generation via Grok Imagine (Aurora architecture)
  3. Content personality — Grok's natural voice is less "corporate AI"

The xAI API is OpenAI-compatible, so we use the openai SDK with a custom base_url.

API base: https://api.x.ai/v1
Chat model: grok-3-mini (fast) or grok-3 (full)
Image model: grok-2-image (current stable)
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
from pathlib import Path
from typing import Optional

from social.config import settings
from social.models import (
    SocialPost, PostStatus, Platform, SourceType,
    ContentTone, ContentVariant, MediaAsset,
)

logger = logging.getLogger("social.grok_client")

XAI_API_BASE = "https://api.x.ai/v1"

# ── Grok System Prompt for Social Content ─────────────────────────────────────

GROK_SOCIAL_SYSTEM_PROMPT = """You write social media posts for Shamrock Bail Bonds, a Florida bail bond agency.

WHO YOU ARE:
- You're the social media voice of a modern, tech-forward bail bond company in Fort Myers, FL
- You have a real personality — wry, direct, occasionally funny, never corporate
- You know what's happening in the world RIGHT NOW and can reference current events
- You sound like a real person who works in bail bonds, not a marketing bot

THE COMPANY:
- Shamrock Bail Bonds | 1528 Broadway, Ft. Myers, FL 33901 | 239-332-BAIL
- 24/7 availability, digital paperwork, AI-powered intake
- Serves Lee, Charlotte, Collier, DeSoto, Hendry, Manatee, Sarasota counties
- Website: shamrockbailbonds.biz

YOUR VOICE RULES:
- Use contractions (we're, you'll, don't — never "we are", "you will")
- Short sentences mixed with longer ones
- Have opinions. React to things. Don't just report.
- No corporate buzzwords (leverage, synergy, innovative, cutting-edge)
- No AI clichés (delve, landscape, testament, pivotal, foster, underscore)
- No fear-mongering, but honest about stressful situations
- Include relevant current events or news hooks when possible
- Always include a CTA (phone, website, or both)
- Be helpful to people whose loved ones just got arrested

BANNED PHRASES:
- "Let's dive into", "Here's what you need to know"
- "In today's rapidly evolving..."
- "It's not just X, it's Y"
- "At its core", "In the realm of"
- "The future looks bright", "Exciting times"
- Any sentence starting with "Furthermore", "Moreover", "Additionally"

OUTPUT: Return ONLY the post text. No explanations. No "here's your post". Just the content.
"""


class GrokClient:
    """
    xAI Grok API client for content generation and image creation.
    Uses the OpenAI SDK with xAI's base_url for compatibility.
    """

    def __init__(self):
        self._client = None
        self._api_key = settings.xai_api_key

    @property
    def is_configured(self) -> bool:
        return bool(self._api_key)

    def _get_client(self):
        """Lazy-load OpenAI-compatible client pointing to xAI."""
        if self._client is None:
            if not self._api_key:
                logger.warning("⚠️  XAI_API_KEY not set — Grok client disabled")
                return None
            try:
                from openai import OpenAI
                self._client = OpenAI(
                    api_key=self._api_key,
                    base_url=XAI_API_BASE,
                )
            except ImportError:
                logger.error("openai package not installed")
                return None
        return self._client

    # ── Content Generation ────────────────────────────────────────────────

    async def generate_post(
        self,
        topic: str,
        platform: Platform = Platform.TWITTER,
        tone: ContentTone = ContentTone.CASUAL,
        include_news: bool = True,
        max_length: Optional[int] = None,
    ) -> Optional[SocialPost]:
        """
        Generate a social post using Grok with live news awareness.

        Args:
            topic: What to post about (e.g., "bail bonds 101", "Florida arrest rights")
            platform: Target platform
            tone: Desired tone
            include_news: Ask Grok to incorporate current events
            max_length: Character limit

        Returns:
            SocialPost ready for queue, or None on failure
        """
        client = self._get_client()
        if not client:
            return None

        platform_rules = {
            Platform.TWITTER: "Max 280 characters. Hook first. 2-3 hashtags. No links in first tweet of threads.",
            Platform.LINKEDIN: "1200-1500 chars optimal. Professional but real. 3-5 hashtags at bottom.",
            Platform.FACEBOOK: "300-500 chars. Open with a question. Include link. 2-3 hashtags max.",
            Platform.INSTAGRAM: "2200 char caption max. Strong first line. 20-30 hashtags after dots separator.",
        }

        news_instruction = ""
        if include_news:
            news_instruction = (
                "\n\nIMPORTANT: Reference something happening RIGHT NOW in the news, "
                "Florida criminal justice, or local SWFL events to make this feel timely. "
                "Tie it naturally to bail bonds or know-your-rights education."
            )

        length_rule = ""
        if max_length:
            length_rule = f"\n\nHARD LIMIT: {max_length} characters maximum."

        prompt = (
            f"Write a {platform.value} post about: {topic}\n\n"
            f"PLATFORM RULES: {platform_rules.get(platform, '')}"
            f"{news_instruction}"
            f"{length_rule}"
        )

        try:
            model = settings.xai_chat_model

            response = await asyncio.to_thread(
                client.chat.completions.create,
                model=model,
                messages=[
                    {"role": "system", "content": GROK_SOCIAL_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=800,
                temperature=0.9,
            )

            content = response.choices[0].message.content.strip()

            # Extract hashtags from content
            import re
            hashtags = re.findall(r"#(\w+)", content)

            variant = ContentVariant.SINGLE
            if platform == Platform.TWITTER and len(content) > 280:
                variant = ContentVariant.THREAD

            return SocialPost(
                source_type=SourceType.MANUAL,
                source_id=f"grok_{topic[:30].replace(' ', '_').lower()}",
                source_title=f"Grok: {topic}",
                platform=platform,
                content=content,
                hashtags=hashtags,
                variant=variant,
                tone=tone,
                tone_confidence=0.95,
                cta="📞 239-332-BAIL | 🌐 shamrockbailbonds.biz",
                compliance_disclaimer=settings.compliance_disclaimer,
                status=PostStatus.PENDING,
            )

        except Exception as e:
            logger.error("❌ Grok content generation failed: %s", e)
            return None

    async def generate_news_hook_post(
        self,
        platform: Platform = Platform.TWITTER,
    ) -> Optional[SocialPost]:
        """
        Ask Grok to generate a post based on whatever's trending in
        Florida criminal justice / bail bonds news RIGHT NOW.
        Grok has live web access, so it can pull real-time news.
        """
        client = self._get_client()
        if not client:
            return None

        prompt = (
            "What's the most interesting thing happening RIGHT NOW in Florida "
            "criminal justice, bail reform, or SWFL local news? Write a social "
            "media post that ties it to bail bonds education. Make it feel "
            "timely — like you just read the news and had a take."
        )

        return await self.generate_post(
            topic=prompt,
            platform=platform,
            tone=ContentTone.CASUAL,
            include_news=False,  # The prompt IS the news hook
        )

    # ── Image Generation ──────────────────────────────────────────────────

    async def generate_image(
        self,
        prompt: str,
        output_dir: str = "/tmp/social_images",
        filename: Optional[str] = None,
        size: str = "1024x1024",
    ) -> Optional[MediaAsset]:
        """
        Generate an image using Grok Imagine (Aurora).

        Args:
            prompt: Image description
            output_dir: Where to save the image
            filename: Override filename
            size: Image dimensions

        Returns:
            MediaAsset with the local file path
        """
        client = self._get_client()
        if not client:
            return None

        try:
            model = settings.xai_image_model

            response = await asyncio.to_thread(
                client.images.generate,
                model=model,
                prompt=prompt,
                n=1,
            )

            if not response.data:
                logger.warning("Grok image generation returned no data")
                return None

            image_data = response.data[0]

            # Save image to local file
            output_path = Path(output_dir)
            output_path.mkdir(parents=True, exist_ok=True)

            if not filename:
                safe_prompt = prompt[:30].replace(" ", "_").lower()
                safe_prompt = "".join(c for c in safe_prompt if c.isalnum() or c == "_")
                filename = f"grok_{safe_prompt}.png"

            file_path = output_path / filename

            # Handle base64 response
            if hasattr(image_data, "b64_json") and image_data.b64_json:
                img_bytes = base64.b64decode(image_data.b64_json)
                file_path.write_bytes(img_bytes)
            elif hasattr(image_data, "url") and image_data.url:
                # Download from URL
                import httpx
                async with httpx.AsyncClient() as http_client:
                    resp = await http_client.get(image_data.url, timeout=30)
                    file_path.write_bytes(resp.content)
            else:
                logger.warning("Grok image response has no data or URL")
                return None

            # Parse dimensions
            w, h = 1024, 1024
            if "x" in size:
                parts = size.split("x")
                w, h = int(parts[0]), int(parts[1])

            logger.info("🖼️  Grok image generated: %s", filename)

            return MediaAsset(
                url=str(file_path),
                alt_text=prompt[:100],
                media_type="image",
                width=w,
                height=h,
                generated=True,
            )

        except Exception as e:
            logger.error("❌ Grok image generation failed: %s", e)
            return None

    async def generate_social_card(
        self,
        headline: str,
        platform: Platform = Platform.TWITTER,
    ) -> Optional[MediaAsset]:
        """Generate a branded social media card image for a post."""

        prompt = (
            f"A clean, modern social media card for a bail bonds company called "
            f"'Shamrock Bail Bonds'. Shamrock green (#1B6B3A) and gold (#D4AF37) "
            f"color scheme on dark background. Headline text: '{headline}'. "
            f"Professional, premium feel. No clip art. Minimalist design with "
            f"subtle shamrock/clover motif. Phone: 239-332-BAIL."
        )

        size = "1200x675" if platform in (Platform.TWITTER, Platform.LINKEDIN) else "1080x1080"

        return await self.generate_image(
            prompt=prompt,
            filename=f"card_{headline[:20].replace(' ', '_').lower()}.png",
            size=size,
        )

    # ── Content Enhancement ───────────────────────────────────────────────

    async def enhance_with_news(self, existing_content: str, platform: Platform) -> Optional[str]:
        """
        Take existing content and ask Grok to add a timely news hook.
        Returns enhanced content, or None if enhancement fails.
        """
        client = self._get_client()
        if not client:
            return None

        prompt = (
            f"Here's a social media post for Shamrock Bail Bonds:\n\n"
            f"{existing_content}\n\n"
            f"Add a timely news hook — reference something happening NOW in "
            f"Florida or SWFL that connects to this topic. Keep the same length "
            f"and tone. Return ONLY the enhanced post, nothing else."
        )

        try:
            model = settings.xai_chat_model

            response = await asyncio.to_thread(
                client.chat.completions.create,
                model=model,
                messages=[
                    {"role": "system", "content": GROK_SOCIAL_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=600,
                temperature=0.85,
            )

            return response.choices[0].message.content.strip()

        except Exception as e:
            logger.error("❌ Grok news enhancement failed: %s", e)
            return None
