"""
Shamrock Social Engine — Content Humanizer
=============================================
Post-processing layer that removes signs of AI-generated writing.

Inspired by blader/humanizer (MIT) — based on Wikipedia's "Signs of AI writing"
guide maintained by WikiProject AI Cleanup. Adapted for social media content
with bail bond industry-specific voice calibration.

This module operates as a two-pass system:
  Pass 1: LLM rewrites content to remove 29 known AI patterns
  Pass 2: LLM audits the rewrite for remaining tells, then fixes them

The humanizer runs AFTER the repurposer generates platform-specific content,
ensuring final output reads like a real Florida bail bondsman wrote it — not
a marketing algorithm.

Reference: https://github.com/blader/humanizer
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from social.config import settings

logger = logging.getLogger("social.humanizer")


# ── The 29 AI Writing Patterns to Eliminate ───────────────────────────────────
# Condensed from Wikipedia's "Signs of AI writing" for LLM system prompt use.

HUMANIZER_SYSTEM_PROMPT = """You are a writing editor who removes every trace of AI-generated text. Your output must read like a real person wrote it — someone with opinions, an actual voice, and occasional rough edges.

## THE 29 AI PATTERNS YOU MUST ELIMINATE

### Content Patterns
1. **Significance inflation** — Kill "pivotal moment", "testament to", "vital role", "marks a shift", "indelible mark", "setting the stage for". Just state the fact.
2. **Notability name-dropping** — Don't list media outlets for credibility. Cite ONE specific claim from ONE source.
3. **Superficial -ing analyses** — Nuke every "highlighting...", "showcasing...", "reflecting...", "emphasizing...", "fostering...", "underscoring..." dangling off sentences.
4. **Promotional language** — Kill "nestled", "vibrant", "breathtaking", "stunning", "groundbreaking", "must-visit", "renowned", "boasts", "rich cultural heritage".
5. **Vague attributions** — No "experts believe", "industry reports suggest", "observers note". Name the source or cut it.
6. **Formulaic challenges** — No "Despite challenges, continues to thrive" patterns. State specific problems with specific details.

### Language Patterns
7. **AI vocabulary** — BAN these words entirely: actually, additionally, delve, enhance, foster, garner, interplay, intricate, landscape (figurative), pivotal, showcase, tapestry (figurative), testament, underscore, vibrant, crucial, valuable, enduring, align with, key (overused adjective).
8. **Copula avoidance** — Use "is", "are", "has" instead of "serves as", "stands as", "features", "boasts", "represents".
9. **Negative parallelisms** — No "It's not just X, it's Y" or "not only...but also..." or tailing negations like "no guessing", "no wasted motion".
10. **Rule of three** — Don't always list three things. Two is fine. Four is fine. One is fine.
11. **Synonym cycling** — Stop rotating synonyms for the same concept. Pick one word and repeat it.
12. **False ranges** — No "from X to Y" sweeping claims. Just say what you mean.
13. **Passive voice / subjectless fragments** — Name the actor. "We" is fine. "You" is fine.

### Style Patterns
14. **Em dash overuse** — Use commas or periods instead.
15. **Boldface overuse** — Bold only actual proper nouns or truly critical terms, not every keyword.
16. **Emoji as bullet points** — Don't use 🔑💡🚀✅ as decorative bullets. Keep emojis conversational if used at all.
17. **Curly quote artifacts** — Use straight quotes.
18. **Formulaic filler** — Cut "In order to", "At its core", "In the realm of", "It's worth noting", "It should be noted".
19. **Excessive hedging** — No "could potentially", "it might be argued", "there's a possibility". Commit to your point or skip it.
20. **Knowledge-cutoff hedging** — No "As of my last update" or "Based on available information".
21. **Disclaimer sandwiching** — Don't wrap claims in softeners from both sides.

### Structural Patterns
22. **Chatbot artifacts** — No "Great question!", "I hope this helps!", "Let me know if...", "Feel free to..."
23. **Transition stacking** — Don't use "Furthermore", "Moreover", "Additionally" to start every paragraph. Just start.
24. **Tl;dr or summary as last paragraph** — Don't restate everything at the end. Either end with your strongest point or a call to action.
25. **Section overload** — Social posts don't need headers and sub-bullets. Write in flowing sentences.
26. **Numbered lists as prose substitute** — For social: write sentences, not formatted lists (unless it's a genuinely visual thread).

### Social-Specific Patterns
27. **Generic positive conclusion** — No "The future looks bright" or "Exciting times lie ahead" or "continues to inspire". End with something specific.
28. **Dive-in openers** — No "Let's dive into", "Here's what you need to know", "Let's unpack". Just start talking.
29. **Fragmented headers restated** — Don't follow a heading with a one-liner restating it.

## YOUR VOICE

You are writing for Shamrock Bail Bonds — a Florida bail bond agency that's modern, tech-savvy, and genuinely empathetic. The voice should sound like:

- A knowledgeable friend who happens to work in bail bonds
- Someone who's seen a lot and doesn't sugarcoat, but isn't cynical
- Direct, warm, occasionally wry — never corporate, never preachy
- Uses "we" and "you" naturally
- Contractions are mandatory ("we're", "you'll", "it's" — not "we are", "you will", "it is")
- Short sentences mixed with longer ones. Rhythm matters.
- Specific details over vague reassurance ("Call us at (239) 332-2245 and we'll walk you through it" beats "Don't hesitate to reach out to our dedicated team of professionals")
- No fear-mongering, but honest about how stressful this situation is

## WHAT TO PRESERVE

- Core facts and claims (don't invent or remove information)
- Platform-specific formatting (hashtags, character limits, thread markers)
- CTAs (phone number, website)
- Compliance disclaimers (keep as-is)
- Hashtags (keep them but make them less generic if possible)

## PROCESS

1. Rewrite the content eliminating all 29 patterns
2. Read it back and ask: "Does this sound like a real person posted this, or like a social media manager ran it through ChatGPT?"
3. If any AI smell remains, fix it
4. Output ONLY the final cleaned text — no explanations, no "here's the revised version", no meta-commentary
"""


# ── Word-level Pattern Detection (for scoring / pre-check) ────────────────────

# These words appear far more frequently in AI text than human text
AI_VOCABULARY = {
    "actually", "additionally", "align with", "crucial", "delve",
    "emphasizing", "enduring", "enhance", "fostering", "garner",
    "highlight", "interplay", "intricate", "intricacies", "key",
    "landscape", "pivotal", "showcase", "showcasing", "tapestry",
    "testament", "underscore", "underscoring", "valuable", "vibrant",
    "groundbreaking", "nestled", "breathtaking", "renowned", "stunning",
    "must-visit", "boasts", "rich cultural heritage", "at its core",
    "in the realm of", "it's worth noting", "it should be noted",
    "in order to", "let's dive into", "here's what you need to know",
    "the future looks bright", "exciting times", "continues to inspire",
    "serves as", "stands as", "marks a", "represents a",
    "setting the stage", "indelible mark", "deeply rooted",
    "evolving landscape", "focal point", "key turning point",
}

# Structural tells
AI_STRUCTURAL_PATTERNS = [
    r"(?i)great question",
    r"(?i)i hope this helps",
    r"(?i)let me know if",
    r"(?i)feel free to",
    r"(?i)it's not just .+, it's",
    r"(?i)not only .+ but also",
    r"(?i)here's what you need to know",
    r"(?i)let's dive into",
    r"(?i)let's unpack",
    r"(?i)as of my last",
    r"(?i)based on available information",
    r"(?i)could potentially",
    r"(?i)it might be argued",
    r"(?i)furthermore,",
    r"(?i)moreover,",
    r"—.+—",  # Em dash sandwich
]


class ContentHumanizer:
    """
    Post-processing humanizer that removes AI writing patterns.

    Usage:
        humanizer = ContentHumanizer()
        clean_text = await humanizer.humanize(ai_generated_text, platform="twitter")
    """

    def __init__(self):
        self._client = None

    def _get_client(self):
        """Lazy-load OpenAI client."""
        if self._client is None:
            if not settings.openai_api_key:
                logger.warning("⚠️  OPENAI_API_KEY not set — humanizer disabled")
                return None
            try:
                from openai import OpenAI
                self._client = OpenAI(api_key=settings.openai_api_key)
            except ImportError:
                logger.warning("openai package not installed")
                return None
        return self._client

    # ── Main Humanization Pipeline ────────────────────────────────────────

    async def humanize(
        self,
        text: str,
        platform: str = "twitter",
        max_length: Optional[int] = None,
    ) -> str:
        """
        Two-pass humanization:
          1. Rewrite to eliminate AI patterns
          2. Audit + fix any remaining tells

        Falls back to rule-based cleanup if OpenAI is unavailable.
        """
        if not text or not text.strip():
            return text

        client = self._get_client()

        if not client:
            # Fallback: rule-based cleanup only
            return self._rule_based_cleanup(text)

        try:
            # Pass 1: Full rewrite
            draft = await self._llm_humanize(client, text, platform, max_length)

            if not draft:
                return self._rule_based_cleanup(text)

            # Pass 2: Audit + fix remaining tells
            final = await self._llm_audit_and_fix(client, draft, platform, max_length)

            return final or draft

        except Exception as e:
            logger.error("❌ Humanizer failed: %s — falling back to rule-based", e)
            return self._rule_based_cleanup(text)

    # ── LLM Pass 1: Rewrite ──────────────────────────────────────────────

    async def _llm_humanize(
        self,
        client,
        text: str,
        platform: str,
        max_length: Optional[int],
    ) -> Optional[str]:
        """First pass: full rewrite eliminating AI patterns."""
        import asyncio

        length_constraint = ""
        if max_length:
            length_constraint = f"\n\nCRITICAL: The output MUST be {max_length} characters or fewer. This is a hard limit for {platform}."

        try:
            response = await asyncio.to_thread(
                client.chat.completions.create,
                model=settings.openai_model,
                messages=[
                    {"role": "system", "content": HUMANIZER_SYSTEM_PROMPT},
                    {"role": "user", "content": f"Humanize this {platform} post. Output ONLY the cleaned text, nothing else.{length_constraint}\n\n---\n\n{text}"},
                ],
                max_tokens=1200,
                temperature=0.9,  # Higher temp = more human variation
            )

            result = response.choices[0].message.content.strip()

            # Strip any meta-commentary the LLM might add
            result = self._strip_meta(result)

            return result

        except Exception as e:
            logger.error("LLM humanize pass 1 failed: %s", e)
            return None

    # ── LLM Pass 2: Audit + Fix ───────────────────────────────────────────

    async def _llm_audit_and_fix(
        self,
        client,
        draft: str,
        platform: str,
        max_length: Optional[int],
    ) -> Optional[str]:
        """Second pass: audit for remaining AI tells, then fix them."""
        import asyncio

        length_constraint = ""
        if max_length:
            length_constraint = f" Keep it under {max_length} characters."

        try:
            response = await asyncio.to_thread(
                client.chat.completions.create,
                model=settings.openai_model,
                messages=[
                    {"role": "system", "content": HUMANIZER_SYSTEM_PROMPT},
                    {"role": "user", "content": f"Read this {platform} post. What makes it still sound AI-generated? List 1-3 remaining tells briefly, then rewrite it one more time to fix them. Output format: first the tells (bulleted), then '---', then the final text only.{length_constraint}\n\n---\n\n{draft}"},
                ],
                max_tokens=1200,
                temperature=0.85,
            )

            result = response.choices[0].message.content.strip()

            # Extract the final text after "---" separator
            if "---" in result:
                parts = result.split("---")
                final = parts[-1].strip()
                tells = parts[0].strip()

                if tells:
                    logger.debug("🔍 Remaining AI tells found: %s", tells[:200])

                final = self._strip_meta(final)
                return final if final else draft

            # No separator found — treat entire response as the fix
            return self._strip_meta(result)

        except Exception as e:
            logger.warning("LLM audit pass failed: %s — using first draft", e)
            return None

    # ── Rule-Based Cleanup (Fallback) ─────────────────────────────────────

    def _rule_based_cleanup(self, text: str) -> str:
        """
        Simple regex-based cleanup when OpenAI is unavailable.
        Won't catch everything, but removes the most obvious AI tells.
        """
        cleaned = text

        # Remove chatbot artifacts
        chatbot_patterns = [
            r"(?i)^great question[!.]?\s*",
            r"(?i)\s*i hope this helps[!.]?\s*$",
            r"(?i)\s*let me know if you[^.!]*[.!]\s*$",
            r"(?i)\s*feel free to[^.!]*[.!]\s*$",
            r"(?i)^here's what you need to know[:.]\s*",
            r"(?i)^let's dive into\s+",
            r"(?i)^let's unpack\s+",
        ]
        for pattern in chatbot_patterns:
            cleaned = re.sub(pattern, "", cleaned)

        # Replace copula avoidance
        copula_replacements = [
            (r"(?i)\bserves as\b", "is"),
            (r"(?i)\bstands as\b", "is"),
            (r"(?i)\bfunctions as\b", "is"),
            (r"(?i)\bacts as\b", "is"),
        ]
        for pattern, replacement in copula_replacements:
            cleaned = re.sub(pattern, replacement, cleaned)

        # Replace AI vocabulary (simple substitutions)
        vocab_replacements = [
            (r"(?i)\badditionally\b", "also"),
            (r"(?i)\bfurthermore\b", "also"),
            (r"(?i)\bmoreover\b", "and"),
            (r"(?i)\bin order to\b", "to"),
            (r"(?i)\butilize\b", "use"),
            (r"(?i)\bleverag(?:e|ing)\b", "use"),
            (r"(?i)\bensure that\b", "make sure"),
            (r"(?i)\bfacilitat(?:e|ing)\b", "help"),
            (r"(?i)\bdelve into\b", "look at"),
            (r"(?i)\bat its core\b,?\s*", ""),
            (r"(?i)\bit's worth noting that\b\s*", ""),
            (r"(?i)\bit should be noted that\b\s*", ""),
            (r"(?i)\bin the realm of\b", "in"),
        ]
        for pattern, replacement in vocab_replacements:
            cleaned = re.sub(pattern, replacement, cleaned)

        # Fix em dash sandwiches → commas
        cleaned = re.sub(r"\s*—\s*", ", ", cleaned)
        # Fix double commas
        cleaned = re.sub(r",\s*,", ",", cleaned)

        # Expand contractions (wait — we WANT contractions. Make sure we have them)
        formal_to_contraction = [
            (r"\bwe are\b", "we're"),
            (r"\byou will\b", "you'll"),
            (r"\bit is\b", "it's"),
            (r"\bdo not\b", "don't"),
            (r"\bcan not\b", "can't"),
            (r"\bcannot\b", "can't"),
            (r"\bwill not\b", "won't"),
            (r"\bthey are\b", "they're"),
            (r"\bwe have\b", "we've"),
            (r"\byou are\b", "you're"),
        ]
        for pattern, replacement in formal_to_contraction:
            cleaned = re.sub(pattern, replacement, cleaned, flags=re.IGNORECASE)

        # Clean up whitespace
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        cleaned = re.sub(r"  +", " ", cleaned)

        return cleaned.strip()

    # ── Scoring: How AI does this text sound? ─────────────────────────────

    def score_ai_likelihood(self, text: str) -> dict:
        """
        Score how AI-generated the text sounds (0-100, lower = more human).
        Uses the vocabulary and structural pattern lists for fast local scoring.
        No LLM call needed.
        """
        if not text:
            return {"score": 0, "flags": [], "word_count": 0}

        text_lower = text.lower()
        words = text_lower.split()
        word_count = len(words)

        flags = []
        score = 0

        # Check AI vocabulary hits
        vocab_hits = 0
        for word in AI_VOCABULARY:
            if word in text_lower:
                vocab_hits += 1
                if vocab_hits <= 5:  # Only report first 5
                    flags.append(f"AI word: '{word}'")

        # Scale: each hit adds points, but diminishing
        score += min(vocab_hits * 6, 40)

        # Check structural patterns
        struct_hits = 0
        for pattern in AI_STRUCTURAL_PATTERNS:
            if re.search(pattern, text):
                struct_hits += 1
                if struct_hits <= 3:
                    flags.append(f"Structural: {pattern[:30]}")

        score += min(struct_hits * 10, 30)

        # Check for contraction avoidance (formal = AI-ish in social media)
        formal_count = len(re.findall(r"\b(we are|you will|it is|do not|cannot|will not|they are)\b", text_lower))
        if formal_count >= 2:
            score += min(formal_count * 5, 15)
            flags.append(f"Formal (no contractions): {formal_count} instances")

        # Check sentence length uniformity (AI tends to be uniform)
        sentences = re.split(r'[.!?]+', text)
        sentences = [s.strip() for s in sentences if s.strip()]
        if len(sentences) >= 3:
            lengths = [len(s.split()) for s in sentences]
            avg = sum(lengths) / len(lengths)
            variance = sum((l - avg) ** 2 for l in lengths) / len(lengths)
            if variance < 10:  # Very uniform
                score += 10
                flags.append("Uniform sentence length (low variance)")

        # Check em dash density
        em_dashes = text.count("—")
        if em_dashes >= 2 and word_count > 0:
            ratio = em_dashes / (word_count / 100)
            if ratio > 1:
                score += 5
                flags.append(f"Em dash heavy: {em_dashes} dashes")

        # Cap at 100
        score = min(score, 100)

        return {
            "score": score,
            "flags": flags,
            "word_count": word_count,
            "verdict": (
                "human" if score < 20
                else "mostly_human" if score < 40
                else "suspicious" if score < 60
                else "likely_ai" if score < 80
                else "obvious_ai"
            ),
        }

    # ── Helpers ───────────────────────────────────────────────────────────

    def _strip_meta(self, text: str) -> str:
        """Remove LLM meta-commentary that wraps the actual content."""
        # Remove "Here's the revised version:" type prefixes
        meta_prefixes = [
            r"(?i)^here'?s?\s+(?:the|a|my)\s+(?:revised|cleaned|humanized|final|updated)\s+(?:version|text|post|draft)[:\s]*\n*",
            r"(?i)^revised\s*(?:version|text|post)?[:\s]*\n*",
            r"(?i)^final\s*(?:version|text|post)?[:\s]*\n*",
            r"(?i)^cleaned\s*(?:up)?\s*(?:version|text|post)?[:\s]*\n*",
        ]
        result = text
        for pattern in meta_prefixes:
            result = re.sub(pattern, "", result)
        return result.strip()
