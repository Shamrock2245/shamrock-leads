#!/usr/bin/env python3
"""Inspect a public roster contract without retaining or printing person-level records.

This tool intentionally emits only status, content type, static field labels,
site-local asset/API paths, and pagination markers. It keeps response text in
memory only long enough to derive those structural facts, then releases it.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from html import unescape
from urllib.parse import urljoin, urlparse, urlunparse

import requests

FIELD_LABELS = ("booking", "booked", "inmate", "arrest", "name", "date")
ENDPOINT_RE = re.compile(r"(?:https?://[^\"'\s<>]+|/[^\"'\s<>]*(?:roster|inmate|booking|api)[^\"'\s<>]*)", re.IGNORECASE)
SCRIPT_RE = re.compile(r"<script[^>]+src=[\"']([^\"']+)[\"']", re.IGNORECASE)
PAGINATION_RE = re.compile(r"(?:page=|pagination|pager|next\s*(?:page|>|»)|\bpage\s*\d+)", re.IGNORECASE)


def _safe_path(candidate: str, origin: str) -> str | None:
    absolute = urljoin(origin, unescape(candidate).replace("\\/", "/"))
    parsed = urlparse(absolute)
    if parsed.scheme not in {"http", "https"} or parsed.netloc != urlparse(origin).netloc:
        return None
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))


def inspect(url: str) -> dict[str, object]:
    try:
        response = requests.get(
            url,
            headers={"User-Agent": "ShamrockSourceContractAudit/1.0 (+https://shamrockbailbonds.biz)"},
            timeout=20,
            allow_redirects=True,
        )
    except requests.RequestException as exc:
        return {
            "request_url": url,
            "transport_error": type(exc).__name__,
            "detail": str(exc).split("\\n", 1)[0][:240],
        }
    content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
    if content_type == "application/json":
        try:
            payload = response.json()
            rows = payload if isinstance(payload, list) else []
            keys = sorted({str(key) for row in rows[:5] if isinstance(row, dict) for key in row})
        except (ValueError, TypeError):
            keys = []
        return {
            "request_url": url,
            "final_origin": f"{urlparse(response.url).scheme}://{urlparse(response.url).netloc}",
            "status_code": response.status_code,
            "content_type": content_type,
            "record_schema_fields": keys,
        }
    html = response.text
    lower = html.lower()
    origin = f"{urlparse(response.url).scheme}://{urlparse(response.url).netloc}"
    paths = {
        path
        for candidate in [*SCRIPT_RE.findall(html), *ENDPOINT_RE.findall(html)]
        if (path := _safe_path(candidate, origin))
    }
    result = {
        "request_url": url,
        "final_origin": origin,
        "status_code": response.status_code,
        "content_type": content_type,
        "field_label_tokens_present": [token for token in FIELD_LABELS if token in lower],
        "pagination_marker_present": bool(PAGINATION_RE.search(html)),
        "site_local_structural_paths": sorted(paths)[:50],
    }
    del html
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url")
    args = parser.parse_args()
    print(json.dumps(inspect(args.url), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
