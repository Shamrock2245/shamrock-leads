#!/bin/bash
# ═══════════════════════════════════════════════════════
# shamrock-post.sh — Quick social media posting wrapper
# ═══════════════════════════════════════════════════════
# Usage:
#   ./shamrock-post.sh "Your caption here"
#   ./shamrock-post.sh "Caption" --video ~/Downloads/reel.mp4
#   ./shamrock-post.sh "Caption" --video reel.mp4 --schedule "2026-06-15 10:00"
#   ./shamrock-post.sh "Caption" --platforms x instagram
#   ./shamrock-post.sh "Caption" --dry-run
#
# This is a thin wrapper around shamrock_poster.py

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
POSTER="$SCRIPT_DIR/shamrock_poster.py"

if [ ! -f "$POSTER" ]; then
    echo "❌ shamrock_poster.py not found at $POSTER"
    exit 1
fi

if [ $# -lt 1 ]; then
    echo "🍀 Shamrock Quick Post"
    echo "Usage: $0 \"Your caption\" [--video file.mp4] [--schedule \"YYYY-MM-DD HH:MM\"] [--platforms x instagram] [--dry-run]"
    exit 1
fi

# First arg is the caption, rest are pass-through
CAPTION="$1"
shift

python3 "$POSTER" --caption "$CAPTION" "$@"
