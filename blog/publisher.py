"""
Wix Blog Auto-Publisher for Shamrock Bail Bonds
================================================
Converts markdown blog posts with YAML frontmatter to Wix Rich Content (Ricos)
format and publishes them via the Wix Blog REST API v3.

Usage:
    python -m blog.publisher              # Publish next scheduled post
    python -m blog.publisher --all        # Publish all due posts
    python -m blog.publisher --dry-run    # Preview without publishing

Environment Variables:
    WIX_BLOG_API_KEY  — Wix API Key with "Manage Blog" permission
    WIX_SITE_ID       — Wix Site ID (defaults to Shamrock production site)
"""

import os
import re
import json
import logging
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml
import requests

logger = logging.getLogger("blog.publisher")

# ── Constants ──────────────────────────────────────────────────────────────────
WIX_SITE_ID = os.getenv("WIX_SITE_ID", "7dd020de-a409-4a2c-bcc8-f81e3a7b6cc1")
WIX_BLOG_API_BASE = "https://www.wixapis.com/blog/v3"
POSTS_DIR = Path(__file__).parent / "posts"
DEFAULT_CATEGORY_SLUGS = {
    "How Bail Bonds Work": "how-bail-bonds-work",
    "Bail Bond Tips": "bail-bond-tips",
    "Florida Bail Laws": "florida-bail-laws",
    "Shamrock News": "shamrock-news",
    "Legal Guides": "legal-guides",
    "Arrest & Booking": "arrest-and-booking",
}


class WixBlogPublisher:
    """Publishes markdown blog posts to the Wix Blog via REST API v3."""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("WIX_BLOG_API_KEY", "")
        if not self.api_key:
            raise ValueError(
                "WIX_BLOG_API_KEY is required. Generate one at:\n"
                "  Wix Dashboard → Settings → API Keys → Create API Key\n"
                "  Grant permission: 'Manage Blog'"
            )
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": self.api_key,
            "wix-site-id": WIX_SITE_ID,
            "Content-Type": "application/json",
        })
        self._category_cache: dict[str, str] = {}

    # ── Public API ─────────────────────────────────────────────────────────

    def publish_post(self, md_path: Path, dry_run: bool = False) -> dict:
        """Parse a markdown file and publish it to Wix Blog."""
        logger.info(f"📝 Processing: {md_path.name}")

        frontmatter, body = self._parse_markdown(md_path)
        if not frontmatter or not body:
            return {"success": False, "error": f"Failed to parse {md_path.name}"}

        # Check if publish_date is in the future
        publish_date = frontmatter.get("publish_date", "")
        if publish_date:
            try:
                target = datetime.strptime(str(publish_date), "%Y-%m-%d").replace(
                    tzinfo=timezone.utc
                )
                if target > datetime.now(timezone.utc):
                    logger.info(f"⏳ Scheduled for {publish_date}, skipping for now")
                    return {"success": False, "skipped": True, "reason": "not_due"}
            except ValueError:
                pass

        # Convert markdown body to Ricos nodes
        rich_content = self._markdown_to_ricos(body)

        # Resolve category IDs
        category_ids = []
        for cat_name in frontmatter.get("categories", []):
            cat_id = self._resolve_category(cat_name)
            if cat_id:
                category_ids.append(cat_id)

        # Build the draft post payload
        payload = {
            "draftPost": {
                "title": frontmatter.get("title", md_path.stem.replace("_", " ").title()),
                "richContent": rich_content,
                "excerpt": frontmatter.get("excerpt", ""),
                "featured": frontmatter.get("featured", False),
                "categoryIds": category_ids,
                "tags": frontmatter.get("tags", []),
                "seoData": {
                    "tags": [
                        {
                            "type": "title",
                            "children": frontmatter.get("seo_title", frontmatter.get("title", "")),
                            "props": {},
                            "custom": False,
                            "disabled": False,
                        },
                        {
                            "type": "meta",
                            "props": {
                                "name": "description",
                                "content": frontmatter.get("excerpt", ""),
                            },
                            "children": "",
                            "custom": False,
                            "disabled": False,
                        },
                    ]
                },
            },
            "publish": True,  # Create AND publish in one call
        }

        # Set slug if provided
        slug = frontmatter.get("slug", "")
        if slug:
            payload["draftPost"]["slug"] = slug

        if dry_run:
            logger.info(f"🔍 DRY RUN — would publish: {frontmatter.get('title')}")
            logger.info(f"   Categories: {frontmatter.get('categories', [])}")
            logger.info(f"   Slug: {slug}")
            logger.info(f"   Excerpt: {frontmatter.get('excerpt', '')[:80]}...")
            return {"success": True, "dry_run": True, "title": frontmatter.get("title")}

        # Call Wix Blog API
        try:
            resp = self.session.post(
                f"{WIX_BLOG_API_BASE}/draft-posts",
                json=payload,
                timeout=30,
            )
            if resp.status_code in (200, 201):
                data = resp.json()
                post_id = data.get("draftPost", {}).get("id", "unknown")
                post_url = data.get("draftPost", {}).get("url", "")
                logger.info(f"✅ Published: {frontmatter.get('title')}")
                logger.info(f"   Post ID: {post_id}")
                logger.info(f"   URL: {post_url}")

                # Move to published directory
                self._archive_post(md_path)

                return {
                    "success": True,
                    "post_id": post_id,
                    "title": frontmatter.get("title"),
                    "url": post_url,
                    "published_at": datetime.now(timezone.utc).isoformat(),
                }
            else:
                error_body = resp.text[:500]
                logger.error(f"❌ Wix API error {resp.status_code}: {error_body}")
                return {
                    "success": False,
                    "status_code": resp.status_code,
                    "error": error_body,
                }
        except requests.RequestException as e:
            logger.error(f"❌ Network error: {e}")
            return {"success": False, "error": str(e)}

    def publish_all_due(self, dry_run: bool = False) -> list[dict]:
        """Publish all posts whose publish_date is today or earlier."""
        results = []
        for md_file in sorted(POSTS_DIR.glob("*.md")):
            result = self.publish_post(md_file, dry_run=dry_run)
            results.append(result)
        return results

    def get_pending_posts(self) -> list[dict]:
        """List all pending posts with their metadata."""
        posts = []
        for md_file in sorted(POSTS_DIR.glob("*.md")):
            try:
                fm, _ = self._parse_markdown(md_file)
                if fm:
                    posts.append({
                        "file": md_file.name,
                        "title": fm.get("title", md_file.stem),
                        "publish_date": str(fm.get("publish_date", "unscheduled")),
                        "categories": fm.get("categories", []),
                        "slug": fm.get("slug", ""),
                    })
            except Exception as e:
                logger.warning(f"Failed to parse {md_file.name}: {e}")
        return posts

    # ── Markdown → Ricos Conversion ────────────────────────────────────────

    def _markdown_to_ricos(self, md_body: str) -> dict:
        """
        Convert markdown body to Wix Ricos (Rich Content) JSON format.
        Supports: headings, paragraphs, bold, italic, links, lists, blockquotes.
        """
        nodes = []
        lines = md_body.strip().split("\n")
        i = 0

        while i < len(lines):
            line = lines[i]

            # Skip empty lines
            if not line.strip():
                i += 1
                continue

            # Heading
            heading_match = re.match(r"^(#{1,6})\s+(.*)", line)
            if heading_match:
                level = len(heading_match.group(1))
                text = heading_match.group(2).strip()
                nodes.append(self._heading_node(text, level))
                i += 1
                continue

            # Unordered list
            if re.match(r"^[\-\*]\s+", line):
                items = []
                while i < len(lines) and re.match(r"^[\-\*]\s+", lines[i]):
                    items.append(re.sub(r"^[\-\*]\s+", "", lines[i]).strip())
                    i += 1
                nodes.append(self._list_node(items, ordered=False))
                continue

            # Ordered list
            if re.match(r"^\d+\.\s+", line):
                items = []
                while i < len(lines) and re.match(r"^\d+\.\s+", lines[i]):
                    items.append(re.sub(r"^\d+\.\s+", "", lines[i]).strip())
                    i += 1
                nodes.append(self._list_node(items, ordered=True))
                continue

            # Blockquote
            if line.startswith(">"):
                quote_lines = []
                while i < len(lines) and lines[i].startswith(">"):
                    quote_lines.append(lines[i].lstrip("> ").strip())
                    i += 1
                nodes.append(self._blockquote_node(" ".join(quote_lines)))
                continue

            # Horizontal rule
            if re.match(r"^---+$", line.strip()):
                nodes.append(self._divider_node())
                i += 1
                continue

            # Regular paragraph (collect consecutive non-empty, non-special lines)
            para_lines = []
            while i < len(lines) and lines[i].strip() and not re.match(
                r"^(#{1,6}\s|[\-\*]\s|\d+\.\s|>|---+$)", lines[i]
            ):
                para_lines.append(lines[i].strip())
                i += 1
            if para_lines:
                nodes.append(self._paragraph_node(" ".join(para_lines)))

        return {"nodes": nodes, "metadata": {"version": 1, "createdTimestamp": datetime.now(timezone.utc).isoformat()}}

    def _heading_node(self, text: str, level: int) -> dict:
        """Create a Ricos heading node."""
        return {
            "type": "HEADING",
            "id": self._gen_id(),
            "nodes": [self._text_node(text)],
            "headingData": {"level": level},
        }

    def _paragraph_node(self, text: str) -> dict:
        """Create a Ricos paragraph node with inline formatting."""
        return {
            "type": "PARAGRAPH",
            "id": self._gen_id(),
            "nodes": self._parse_inline(text),
            "paragraphData": {"textStyle": {"textAlignment": "AUTO"}},
        }

    def _list_node(self, items: list[str], ordered: bool = False) -> dict:
        """Create a Ricos ordered/unordered list node."""
        list_items = []
        for item in items:
            list_items.append({
                "type": "LIST_ITEM",
                "id": self._gen_id(),
                "nodes": [{
                    "type": "PARAGRAPH",
                    "id": self._gen_id(),
                    "nodes": self._parse_inline(item),
                    "paragraphData": {"textStyle": {"textAlignment": "AUTO"}},
                }],
            })
        return {
            "type": "ORDERED_LIST" if ordered else "BULLETED_LIST",
            "id": self._gen_id(),
            "nodes": list_items,
        }

    def _blockquote_node(self, text: str) -> dict:
        """Create a Ricos blockquote node."""
        return {
            "type": "BLOCKQUOTE",
            "id": self._gen_id(),
            "nodes": [{
                "type": "PARAGRAPH",
                "id": self._gen_id(),
                "nodes": self._parse_inline(text),
                "paragraphData": {"textStyle": {"textAlignment": "AUTO"}},
            }],
        }

    def _divider_node(self) -> dict:
        """Create a Ricos divider node."""
        return {
            "type": "DIVIDER",
            "id": self._gen_id(),
            "nodes": [],
            "dividerData": {"lineStyle": "SINGLE", "width": "LARGE"},
        }

    def _text_node(self, text: str) -> dict:
        """Create a plain text node."""
        return {
            "type": "TEXT",
            "id": self._gen_id(),
            "nodes": [],
            "textData": {"text": text, "decorations": []},
        }

    def _parse_inline(self, text: str) -> list[dict]:
        """
        Parse inline markdown (bold, italic, links) into Ricos text nodes.
        Handles: **bold**, *italic*, [text](url)
        """
        nodes = []
        # Regex to find inline patterns
        pattern = re.compile(
            r"(\*\*(.+?)\*\*)"         # bold
            r"|(\*(.+?)\*)"             # italic
            r"|(\[(.+?)\]\((.+?)\))"    # link
        )

        last_end = 0
        for match in pattern.finditer(text):
            # Add any text before this match
            if match.start() > last_end:
                plain = text[last_end:match.start()]
                if plain:
                    nodes.append(self._text_node(plain))

            if match.group(1):  # Bold
                nodes.append({
                    "type": "TEXT",
                    "id": self._gen_id(),
                    "nodes": [],
                    "textData": {
                        "text": match.group(2),
                        "decorations": [{"type": "BOLD"}],
                    },
                })
            elif match.group(3):  # Italic
                nodes.append({
                    "type": "TEXT",
                    "id": self._gen_id(),
                    "nodes": [],
                    "textData": {
                        "text": match.group(4),
                        "decorations": [{"type": "ITALIC"}],
                    },
                })
            elif match.group(5):  # Link
                nodes.append({
                    "type": "TEXT",
                    "id": self._gen_id(),
                    "nodes": [],
                    "textData": {
                        "text": match.group(6),
                        "decorations": [{
                            "type": "LINK",
                            "linkData": {
                                "link": {"url": match.group(7), "target": "_blank"},
                            },
                        }],
                    },
                })

            last_end = match.end()

        # Add any remaining text
        if last_end < len(text):
            remaining = text[last_end:]
            if remaining:
                nodes.append(self._text_node(remaining))

        # If no inline formatting found, return a single text node
        if not nodes:
            nodes.append(self._text_node(text))

        return nodes

    # ── Category Resolution ────────────────────────────────────────────────

    def _resolve_category(self, category_name: str) -> Optional[str]:
        """Resolve a category name to its Wix Blog category ID."""
        if category_name in self._category_cache:
            return self._category_cache[category_name]

        try:
            resp = self.session.get(
                f"{WIX_BLOG_API_BASE}/categories",
                params={"paging.limit": 50},
                timeout=15,
            )
            if resp.status_code == 200:
                cats = resp.json().get("categories", [])
                for cat in cats:
                    label = cat.get("label", "")
                    self._category_cache[label] = cat.get("id", "")

                if category_name in self._category_cache:
                    return self._category_cache[category_name]

                # Category not found — try creating it
                logger.info(f"📁 Creating category: {category_name}")
                return self._create_category(category_name)
        except Exception as e:
            logger.warning(f"Failed to resolve category '{category_name}': {e}")

        return None

    def _create_category(self, name: str) -> Optional[str]:
        """Create a new blog category and return its ID."""
        slug = DEFAULT_CATEGORY_SLUGS.get(name, name.lower().replace(" ", "-"))
        try:
            resp = self.session.post(
                f"{WIX_BLOG_API_BASE}/categories",
                json={"category": {"label": name, "slug": slug}},
                timeout=15,
            )
            if resp.status_code in (200, 201):
                cat_id = resp.json().get("category", {}).get("id", "")
                self._category_cache[name] = cat_id
                logger.info(f"✅ Created category: {name} ({cat_id})")
                return cat_id
        except Exception as e:
            logger.warning(f"Failed to create category '{name}': {e}")
        return None

    # ── File Management ────────────────────────────────────────────────────

    def _parse_markdown(self, path: Path) -> tuple[Optional[dict], Optional[str]]:
        """Parse a markdown file with YAML frontmatter."""
        try:
            content = path.read_text(encoding="utf-8")
        except Exception as e:
            logger.error(f"Failed to read {path}: {e}")
            return None, None

        # Split frontmatter from body
        fm_match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)", content, re.DOTALL)
        if not fm_match:
            logger.warning(f"No frontmatter found in {path.name}")
            return {}, content

        try:
            frontmatter = yaml.safe_load(fm_match.group(1))
        except yaml.YAMLError as e:
            logger.error(f"Invalid YAML in {path.name}: {e}")
            return None, None

        body = fm_match.group(2).strip()
        return frontmatter, body

    def _archive_post(self, md_path: Path):
        """Move published post to the published/ subdirectory."""
        published_dir = md_path.parent / "published"
        published_dir.mkdir(exist_ok=True)
        dest = published_dir / md_path.name
        md_path.rename(dest)
        logger.info(f"📦 Archived: {md_path.name} → published/")

    def _gen_id(self) -> str:
        """Generate a deterministic short ID for Ricos nodes."""
        return hashlib.md5(
            f"{datetime.now().timestamp()}{os.urandom(4).hex()}".encode()
        ).hexdigest()[:8]


# ── CLI Entry Point ────────────────────────────────────────────────────────────

def main():
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    parser = argparse.ArgumentParser(description="Shamrock Blog Auto-Publisher")
    parser.add_argument("--all", action="store_true", help="Publish all due posts")
    parser.add_argument("--dry-run", action="store_true", help="Preview without publishing")
    parser.add_argument("--list", action="store_true", help="List pending posts")
    parser.add_argument("--file", type=str, help="Publish a specific file")
    args = parser.parse_args()

    try:
        publisher = WixBlogPublisher()
    except ValueError as e:
        logger.error(str(e))
        return

    if args.list:
        posts = publisher.get_pending_posts()
        if not posts:
            print("No pending posts found.")
        else:
            print(f"\n📋 {len(posts)} pending post(s):\n")
            for p in posts:
                print(f"  • {p['title']}")
                print(f"    File: {p['file']} | Date: {p['publish_date']}")
                print(f"    Categories: {', '.join(p['categories'])}")
                print()
        return

    if args.file:
        path = Path(args.file)
        if not path.exists():
            path = POSTS_DIR / args.file
        result = publisher.publish_post(path, dry_run=args.dry_run)
        print(json.dumps(result, indent=2))
    elif args.all:
        results = publisher.publish_all_due(dry_run=args.dry_run)
        for r in results:
            status = "✅" if r.get("success") else "⏳" if r.get("skipped") else "❌"
            title = r.get("title", "unknown")
            print(f"  {status} {title}")
    else:
        # Publish the next due post
        results = publisher.publish_all_due(dry_run=args.dry_run)
        published = [r for r in results if r.get("success") and not r.get("dry_run")]
        print(f"\n{'🔍 Dry run' if args.dry_run else '✅ Published'}: {len(published)} post(s)")


if __name__ == "__main__":
    main()
