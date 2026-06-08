#!/usr/bin/env python3
"""
Shamrock Social Poster — "The Publisher"
=========================================
One-command posting to all Shamrock social platforms via Postiz CLI.
Handles video uploads, auto-hashtags, phone number injection, and scheduling.

REQUIRES: Postiz CLI installed on VPS (npm install -g postiz)
          POSTIZ_API_KEY and POSTIZ_API_URL set in VPS .bashrc

Usage:
    python shamrock_poster.py --video ~/Downloads/shamrock_branded_reel.mp4 --caption "Check it out!"
    python shamrock_poster.py --video reel.mp4 --caption "Bail tips" --schedule "2026-06-15 10:00"
    python shamrock_poster.py --caption "Know your rights in Florida!"
    python shamrock_poster.py --video reel.mp4 --caption "Tips" --platforms x instagram
    python shamrock_poster.py --image ~/Downloads/graphic.png --caption "New guide!"
    python shamrock_poster.py --caption "Test post" --dry-run
"""

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ═══════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════
VPS_HOST = "root@178.156.179.237"
SHAMROCK_PHONE = "(239) 332-2245"
SHAMROCK_PHONE_LINE = f"\n\n🍀 Shamrock Bail Bonds\n📞 {SHAMROCK_PHONE}"

# Integration IDs (from Postiz)
INTEGRATIONS = {
    "facebook":  "cmpzsi3ir0003k36vw5hrex7x",
    "instagram": "cmpzsilta0005k36vzt951r2y",
    "x":         "cmpztrc580001k36p5kz86taa",
    "youtube":   "cmpzshji10001k36v6o88d0n7",
    "gmb":       "cmpzsktm50007k36vw1cxyrzb",
}

WRONG_PHONE_PATTERNS = [
    r"\(239\)\s*552-\d{4}",
    r"239-555-\d{4}",
    r"\(800\)\s*\d{3}-\d{4}",
    r"1-800-\d{3}-\d{4}",
]

# ── Hashtag pools ──
HASHTAG_POOLS = {
    "core": [
        "#ShamrockBailBonds", "#BailBonds", "#FloridaBail",
        "#FortMyers", "#SWFL", "#BailBondAgent",
    ],
    "legal": [
        "#KnowYourRights", "#FloridaLaw", "#CriminalDefense",
        "#PretrialRelease", "#DueProcess", "#BailReform",
    ],
    "action": [
        "#GetOutFast", "#24HourBail", "#ArrestHelp",
        "#NeedBail", "#CallNow", "#BailHelp",
    ],
    "geo": [
        "#LeeCounty", "#SWFL", "#CapeCoral", "#Naples",
        "#FortMyersFL", "#SouthwestFlorida",
    ],
    "awareness": [
        "#BailEducation", "#LegalTips", "#FloridaCourts",
        "#JailRelease", "#BondProcess", "#CourtDates",
    ],
}

PLATFORM_HASHTAG_LIMITS = {
    "x": 5, "instagram": 20, "facebook": 8,
    "youtube": 10, "tiktok": 8, "gmb": 0,
}


def log(msg, level="INFO"):
    colors = {"INFO": "\033[36m", "OK": "\033[32m", "WARN": "\033[33m", "ERR": "\033[31m"}
    reset = "\033[0m"
    print(f"{colors.get(level, '')}{msg}{reset}")


# ═══════════════════════════════════════════════════════
# VPS COMMAND HELPERS (uses Postiz CLI on VPS)
# ═══════════════════════════════════════════════════════
def vps_cmd(cmd: str) -> str:
    """Run a command on the VPS via SSH. Returns stdout."""
    # Source .bashrc to get env vars, then run command
    full_cmd = f'source /root/.bashrc 2>/dev/null; {cmd}'
    result = subprocess.run(
        ["ssh", "-o", "StrictHostKeyChecking=no", VPS_HOST, full_cmd],
        capture_output=True, text=True,
    )
    if result.returncode != 0 and result.stderr:
        log(f"VPS error: {result.stderr[:200]}", "WARN")
    return result.stdout.strip()


def scp_to_vps(local_path: str, remote_path: str) -> bool:
    """Copy a file from local machine to VPS."""
    result = subprocess.run(
        ["scp", "-o", "StrictHostKeyChecking=no", local_path, f"{VPS_HOST}:{remote_path}"],
        capture_output=True, text=True,
    )
    return result.returncode == 0


# ═══════════════════════════════════════════════════════
# CONTENT ENHANCEMENT
# ═══════════════════════════════════════════════════════
def fix_phone_number(content: str) -> str:
    """Replace any wrong phone numbers with the correct Shamrock number."""
    for pattern in WRONG_PHONE_PATTERNS:
        content = re.sub(pattern, SHAMROCK_PHONE, content)
    return content


def has_phone(content: str) -> bool:
    return SHAMROCK_PHONE in content or "239-332-2245" in content


def auto_hashtags(content: str, platform: str = "x", extra_tags: list = None) -> str:
    """Generate and append relevant hashtags based on content and platform."""
    limit = PLATFORM_HASHTAG_LIMITS.get(platform, 8)
    if limit == 0:
        return content

    existing = set(re.findall(r"#\w+", content))
    if len(existing) >= limit:
        return content

    selected = []
    content_lower = content.lower()

    # Core tags first
    for tag in HASHTAG_POOLS["core"]:
        if tag not in existing and len(selected) < limit:
            selected.append(tag)

    # Content-aware selection
    keyword_map = {
        "legal": ["right", "law", "court", "attorney", "defense", "reform", "hearing"],
        "action": ["call", "help", "fast", "24", "emergency", "arrested", "jail"],
        "awareness": ["tip", "guide", "how to", "education", "process", "bond schedule"],
        "geo": ["lee county", "fort myers", "cape coral", "naples", "swfl", "collier"],
    }
    for pool_name, keywords in keyword_map.items():
        if any(kw in content_lower for kw in keywords):
            for tag in HASHTAG_POOLS[pool_name]:
                if tag not in existing and tag not in selected and len(selected) < limit:
                    selected.append(tag)

    if extra_tags:
        for tag in extra_tags:
            tag = tag if tag.startswith("#") else f"#{tag}"
            if tag not in existing and tag not in selected and len(selected) < limit:
                selected.append(tag)

    # Fill remaining
    for pool in ["action", "awareness", "geo"]:
        for tag in HASHTAG_POOLS[pool]:
            if tag not in existing and tag not in selected and len(selected) < limit:
                selected.append(tag)

    # Strip existing trailing hashtag block and rewrite
    content_stripped = re.sub(r"\n\n(#\w+\s*)+$", "", content.strip())
    if selected:
        return f"{content_stripped}\n\n{' '.join(selected[:limit])}"
    return content


def enhance_content(content: str, platform: str, add_phone: bool, add_hashtags: bool, extra_tags: list = None) -> str:
    content = fix_phone_number(content)
    if add_phone and not has_phone(content):
        content = content.rstrip() + SHAMROCK_PHONE_LINE
    if add_hashtags:
        content = auto_hashtags(content, platform=platform, extra_tags=extra_tags)
    return content


# ═══════════════════════════════════════════════════════
# CORE PUBLISHING
# ═══════════════════════════════════════════════════════
def upload_media(local_path: str) -> str | None:
    """Upload media: SCP to VPS → postiz upload → return URL."""
    local_path = os.path.expanduser(local_path)
    if not os.path.exists(local_path):
        # Try Downloads folder
        dl = os.path.expanduser(f"~/Downloads/{local_path}")
        if os.path.exists(dl):
            local_path = dl
        else:
            log(f"File not found: {local_path}", "ERR")
            return None

    size_mb = os.path.getsize(local_path) / (1024 * 1024)
    filename = os.path.basename(local_path)
    remote_tmp = f"/tmp/shamrock_upload_{filename}"

    log(f"📤 Uploading {filename} ({size_mb:.1f} MB) to VPS...")
    if not scp_to_vps(local_path, remote_tmp):
        log("SCP transfer failed", "ERR")
        return None

    log(f"📎 Running postiz upload on VPS...")
    result = vps_cmd(f'postiz upload "{remote_tmp}" 2>&1; rm -f "{remote_tmp}"')

    # Parse the CLI output for the path/URL
    try:
        # CLI outputs JSON with {path: "..."}
        data = json.loads(result)
        url = data.get("path") or data.get("url")
        if url:
            log(f"✅ Uploaded: {url}", "OK")
            return url
    except json.JSONDecodeError:
        pass

    # Try to extract path from non-JSON output
    match = re.search(r'(https?://\S+|/uploads/\S+)', result)
    if match:
        url = match.group(1)
        log(f"✅ Uploaded: {url}", "OK")
        return url

    log(f"Upload result: {result[:300]}", "WARN")
    return None


def create_post_cli(content: str, integration_ids: str, schedule_iso: str, media_url: str = None) -> str:
    """Create a post via Postiz CLI on the VPS."""
    # Escape content for shell
    escaped = content.replace("'", "'\\''")

    cmd = f"postiz posts:create -c '{escaped}' -s \"{schedule_iso}\" -i \"{integration_ids}\""
    if media_url:
        cmd += f' -m "{media_url}"'

    return vps_cmd(cmd + " 2>&1")


def publish(
    caption: str,
    video_path: str = None,
    image_path: str = None,
    platforms: list = None,
    schedule_str: str = None,
    add_phone: bool = True,
    add_hashtags: bool = True,
    extra_tags: list = None,
    dry_run: bool = False,
):
    """Main publish function — handles everything."""

    # 1. Determine target platforms
    if platforms:
        targets = {k: v for k, v in INTEGRATIONS.items() if k in platforms}
    else:
        targets = {k: v for k, v in INTEGRATIONS.items() if k != "gmb"}

    if not targets:
        log(f"No matching platforms. Available: {list(INTEGRATIONS.keys())}", "ERR")
        return False

    log(f"🎯 Targets: {', '.join(t.upper() for t in targets)}")

    # 2. Upload media if provided
    media_url = None
    media_file = video_path or image_path
    if media_file and not dry_run:
        media_url = upload_media(media_file)
        if not media_url:
            log("Media upload failed. Continuing without media.", "WARN")

    # 3. Parse schedule date
    if schedule_str:
        try:
            dt = datetime.strptime(schedule_str, "%Y-%m-%d %H:%M")
            dt_utc = dt + timedelta(hours=4)  # ET → UTC
            schedule_iso = dt_utc.strftime("%Y-%m-%dT%H:%M:%S.000Z")
            log(f"📅 Scheduled: {schedule_str} ET → {schedule_iso} UTC")
        except ValueError:
            log(f"Invalid date format: {schedule_str}. Use: YYYY-MM-DD HH:MM", "ERR")
            return False
    else:
        # 5 minutes from now
        dt_utc = datetime.now(timezone.utc) + timedelta(minutes=5)
        schedule_iso = dt_utc.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        log(f"📅 Posting in ~5 min: {schedule_iso} UTC")

    # 4. For each platform, enhance content and create post
    results = []
    for platform, integration_id in targets.items():
        enhanced = enhance_content(caption, platform, add_phone, add_hashtags, extra_tags)

        if dry_run:
            print(f"\n{'='*60}")
            print(f"[DRY RUN] Platform: {platform.upper()}")
            print(f"Integration: {integration_id}")
            print(f"Media: {media_file or 'None'}")
            print(f"Schedule: {schedule_str or '~5 min'}")
            print(f"Content:\n{enhanced}")
            print(f"{'='*60}")
            results.append(True)
            continue

        log(f"📬 Posting to {platform.upper()}...")
        result = create_post_cli(enhanced, integration_id, schedule_iso, media_url)

        if "error" in result.lower() or "failed" in result.lower():
            log(f"  → {platform}: {result[:150]}", "ERR")
            results.append(False)
        else:
            log(f"  → {platform}: ✅ Success", "OK")
            results.append(True)

    ok = sum(1 for r in results if r)
    action = "previewed" if dry_run else "created"
    log(f"\n🍀 {ok}/{len(results)} posts {action}", "OK")
    return ok == len(results)


# ═══════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════
def main():
    parser = argparse.ArgumentParser(
        description="🍀 Shamrock Social Poster — Post to all platforms with one command",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --video ~/Downloads/reel.mp4 --caption "Know your rights!"
  %(prog)s --caption "Call us 24/7!" --platforms x instagram
  %(prog)s --video reel.mp4 --caption "Tips" --schedule "2026-06-15 10:00"
  %(prog)s --caption "Test" --dry-run
        """,
    )
    parser.add_argument("--caption", "-c", required=True, help="Post caption/content")
    parser.add_argument("--video", "-v", help="Path to video file (.mp4)")
    parser.add_argument("--image", "-i", help="Path to image file (.png/.jpg)")
    parser.add_argument("--platforms", "-p", nargs="+",
                        choices=["x", "instagram", "facebook", "youtube", "tiktok", "gmb"],
                        help="Target platforms (default: all except GMB)")
    parser.add_argument("--schedule", "-s", help="Schedule: 'YYYY-MM-DD HH:MM' in ET")
    parser.add_argument("--tags", "-t", nargs="+", help="Extra hashtags to include")
    parser.add_argument("--no-phone", action="store_true", help="Don't auto-add phone number")
    parser.add_argument("--no-hashtags", action="store_true", help="Don't auto-add hashtags")
    parser.add_argument("--dry-run", "-d", action="store_true", help="Preview without posting")

    args = parser.parse_args()

    print("\n🍀 Shamrock Social Poster v2.0 (CLI-backed)")
    print("=" * 45)

    publish(
        caption=args.caption,
        video_path=args.video,
        image_path=args.image,
        platforms=args.platforms,
        schedule_str=args.schedule,
        add_phone=not args.no_phone,
        add_hashtags=not args.no_hashtags,
        extra_tags=args.tags,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
