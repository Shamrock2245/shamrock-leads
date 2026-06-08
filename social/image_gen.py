"""
Shamrock Social Engine — Image Generator
==========================================
Creates branded social media cards using Pillow.
Generates text-overlay images with Shamrock brand colors.

Templates:
  - 1:1 (1080x1080)  — Instagram feed
  - 16:9 (1200x675)  — Twitter/LinkedIn
  - 4:5 (1080x1350)  — Instagram stories / reels cover
  - 9:16 (1080x1920) — Stories / TikTok

Brand colors:
  - Primary Green: #1B6B3A
  - Gold Accent: #D4AF37
  - Dark Background: #0F1419
  - White Text: #FFFFFF
  - Light Gray: #E8E8E8
"""

from __future__ import annotations

import logging
import os
import textwrap
from pathlib import Path
from typing import Optional

from social.models import MediaAsset

logger = logging.getLogger("social.image_gen")

# ── Brand Colors ──────────────────────────────────────────────────────────────
SHAMROCK_GREEN = (27, 107, 58)
SHAMROCK_GOLD = (212, 175, 55)
DARK_BG = (15, 20, 25)
WHITE = (255, 255, 255)
LIGHT_GRAY = (232, 232, 232)

# ── Template Sizes ────────────────────────────────────────────────────────────
TEMPLATES = {
    "square": (1080, 1080),      # Instagram feed
    "landscape": (1200, 675),    # Twitter / LinkedIn
    "portrait": (1080, 1350),    # Instagram portrait
    "story": (1080, 1920),       # Stories / TikTok
}

# Output directory
OUTPUT_DIR = Path("/tmp/social_images")


class ImageGenerator:
    """Generates branded social media card images using Pillow."""

    def __init__(self):
        self._pil_available = False
        try:
            from PIL import Image, ImageDraw, ImageFont
            self._pil_available = True
        except ImportError:
            logger.warning("⚠️  Pillow not installed — image generation disabled")

    def generate_blog_card(
        self,
        title: str,
        subtitle: str = "",
        template: str = "landscape",
        output_filename: Optional[str] = None,
    ) -> Optional[MediaAsset]:
        """
        Generate a branded blog card image.

        Args:
            title: Main headline text
            subtitle: Secondary text (excerpt, CTA, etc.)
            template: Size template ("square", "landscape", "portrait", "story")
            output_filename: Override output filename

        Returns:
            MediaAsset with the file path, or None if generation fails.
        """
        if not self._pil_available:
            return None

        try:
            from PIL import Image, ImageDraw, ImageFont

            width, height = TEMPLATES.get(template, TEMPLATES["landscape"])
            img = Image.new("RGB", (width, height), DARK_BG)
            draw = ImageDraw.Draw(img)

            # ── Green accent bar at top ──
            bar_height = int(height * 0.02)
            draw.rectangle(
                [(0, 0), (width, bar_height)],
                fill=SHAMROCK_GREEN,
            )

            # ── Gold accent line ──
            gold_y = bar_height + 2
            draw.rectangle(
                [(0, gold_y), (width, gold_y + 3)],
                fill=SHAMROCK_GOLD,
            )

            # ── Load fonts (fallback to default) ──
            try:
                title_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 48)
                subtitle_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 28)
                brand_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 24)
            except OSError:
                title_font = ImageFont.load_default()
                subtitle_font = ImageFont.load_default()
                brand_font = ImageFont.load_default()

            # ── Title text (word-wrapped) ──
            margin = int(width * 0.08)
            max_chars = int((width - margin * 2) / 25)
            wrapped_title = textwrap.fill(title, width=max_chars)

            title_y = int(height * 0.25)
            draw.multiline_text(
                (margin, title_y),
                wrapped_title,
                font=title_font,
                fill=WHITE,
                spacing=12,
            )

            # ── Subtitle ──
            if subtitle:
                wrapped_sub = textwrap.fill(subtitle, width=max_chars + 10)
                subtitle_y = int(height * 0.55)
                draw.multiline_text(
                    (margin, subtitle_y),
                    wrapped_sub,
                    font=subtitle_font,
                    fill=LIGHT_GRAY,
                    spacing=8,
                )

            # ── Brand footer ──
            footer_y = height - int(height * 0.12)
            draw.rectangle(
                [(0, footer_y - 10), (width, height)],
                fill=SHAMROCK_GREEN,
            )
            brand_text = "☘️ SHAMROCK BAIL BONDS  •  (239) 332-2245  •  shamrockbailbonds.biz"
            # Center the brand text
            bbox = draw.textbbox((0, 0), brand_text, font=brand_font)
            text_width = bbox[2] - bbox[0]
            brand_x = (width - text_width) // 2
            draw.text(
                (brand_x, footer_y + 5),
                brand_text,
                font=brand_font,
                fill=WHITE,
            )

            # ── Gold dots accent ──
            dot_y = footer_y - 25
            for i in range(5):
                dot_x = margin + i * 15
                draw.ellipse(
                    [(dot_x, dot_y), (dot_x + 6, dot_y + 6)],
                    fill=SHAMROCK_GOLD,
                )

            # ── Save ──
            OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            if not output_filename:
                safe_title = title[:30].replace(" ", "_").replace("/", "_").lower()
                output_filename = f"social_{template}_{safe_title}.png"

            output_path = OUTPUT_DIR / output_filename
            img.save(str(output_path), "PNG", quality=95)

            logger.info("🖼️  Generated: %s (%dx%d)", output_filename, width, height)

            return MediaAsset(
                url=str(output_path),
                alt_text=title,
                media_type="image",
                width=width,
                height=height,
                generated=True,
            )

        except Exception as e:
            logger.error("❌ Image generation failed: %s", e)
            return None

    def generate_stat_card(
        self,
        headline: str,
        stat_value: str,
        stat_label: str,
        template: str = "square",
    ) -> Optional[MediaAsset]:
        """Generate a statistics highlight card (e.g., '127 Arrests This Week')."""
        if not self._pil_available:
            return None

        try:
            from PIL import Image, ImageDraw, ImageFont

            width, height = TEMPLATES.get(template, TEMPLATES["square"])
            img = Image.new("RGB", (width, height), DARK_BG)
            draw = ImageDraw.Draw(img)

            # Green border
            border = 8
            draw.rectangle(
                [(border, border), (width - border, height - border)],
                outline=SHAMROCK_GREEN, width=border,
            )

            # Load fonts
            try:
                stat_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 120)
                label_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 36)
                headline_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 32)
            except OSError:
                stat_font = ImageFont.load_default()
                label_font = ImageFont.load_default()
                headline_font = ImageFont.load_default()

            # Headline at top
            headline_y = int(height * 0.12)
            bbox = draw.textbbox((0, 0), headline, font=headline_font)
            text_w = bbox[2] - bbox[0]
            draw.text(
                ((width - text_w) // 2, headline_y),
                headline,
                font=headline_font,
                fill=SHAMROCK_GOLD,
            )

            # Big stat number
            stat_y = int(height * 0.3)
            bbox = draw.textbbox((0, 0), stat_value, font=stat_font)
            text_w = bbox[2] - bbox[0]
            draw.text(
                ((width - text_w) // 2, stat_y),
                stat_value,
                font=stat_font,
                fill=WHITE,
            )

            # Label below stat
            label_y = stat_y + 140
            bbox = draw.textbbox((0, 0), stat_label, font=label_font)
            text_w = bbox[2] - bbox[0]
            draw.text(
                ((width - text_w) // 2, label_y),
                stat_label,
                font=label_font,
                fill=LIGHT_GRAY,
            )

            # Brand footer
            footer_y = height - int(height * 0.12)
            draw.rectangle(
                [(0, footer_y), (width, height)],
                fill=SHAMROCK_GREEN,
            )
            brand = "☘️ SHAMROCK BAIL BONDS  •  (239) 332-2245"
            try:
                brand_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 22)
            except OSError:
                brand_font = ImageFont.load_default()
            bbox = draw.textbbox((0, 0), brand, font=brand_font)
            text_w = bbox[2] - bbox[0]
            draw.text(
                ((width - text_w) // 2, footer_y + 15),
                brand,
                font=brand_font,
                fill=WHITE,
            )

            # Save
            OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            fname = f"stat_{stat_value.replace(' ', '_')}_{template}.png"
            output_path = OUTPUT_DIR / fname
            img.save(str(output_path), "PNG", quality=95)

            logger.info("📊 Generated stat card: %s", fname)
            return MediaAsset(
                url=str(output_path),
                alt_text=f"{stat_value} {stat_label}",
                media_type="image",
                width=width,
                height=height,
                generated=True,
            )

        except Exception as e:
            logger.error("❌ Stat card generation failed: %s", e)
            return None
