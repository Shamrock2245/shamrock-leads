#!/usr/bin/env python3
"""Probe public Shamrock CRM health routes and keep a single prod-down issue.

Unauthenticated routes (dashboard/auth/pin_middleware.py OPEN_PATHS, served by
dashboard/main.py) on the two public vhosts that proxy to the dashboard:

  https://leads.shamrockbailbonds.biz/health
  https://leads.shamrockbailbonds.biz/health/live
  https://paperwork.shamrockbailbonds.biz/health
  https://paperwork.shamrockbailbonds.biz/health/live

/health is healthy only when JSON status is "ok" (MongoDB ping succeeded and
no router module failed). /health/live is process liveness. Shannon's watchdog
already probes leads /health and fails that job; this script is the incident
record. It does not call PIN-gated status routes.

One open issue labeled prod-down. A repeat failure comments on it (at most
once an hour unless the failing set changes). Recovery closes it.

No Slack webhook secret is referenced by existing workflows, so this script
does not read or require one.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum

LABEL = "prod-down"
MARKER = "<!-- shamrock-prod-uptime"
COMMENT_COOLDOWN = timedelta(hours=1)
ATTEMPTS = 3
RETRY_SECONDS = 4
TIMEOUT_SECONDS = 20
USER_AGENT = "ShamrockUptime/1.0 (GitHubActions; HealthCheck)"

CHECKS: tuple[tuple[str, str], ...] = (
    ("leads /health", "https://leads.shamrockbailbonds.biz/health"),
    ("leads /health/live", "https://leads.shamrockbailbonds.biz/health/live"),
    ("paperwork /health", "https://paperwork.shamrockbailbonds.biz/health"),
    ("paperwork /health/live", "https://paperwork.shamrockbailbonds.biz/health/live"),
)


class Action(Enum):
    NONE = "none"
    OPEN = "open"
    COMMENT = "comment"
    SKIP_COMMENT = "skip_comment"
    CLOSE = "close"


@dataclass(frozen=True)
class ProbeResult:
    name: str
    url: str
    ok: bool
    detail: str


@dataclass(frozen=True)
class OpenIssue:
    number: int
    last_signature: str | None
    last_marker_at: datetime | None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def judge_body(status: int | None, body: str, error: str | None) -> tuple[bool, str]:
    """Return (ok, public detail). Detail never includes the raw body."""
    if error and status is None:
        return False, error
    if status != 200:
        return False, f"HTTP {status}"
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return False, "HTTP 200 but body was not JSON"
    if not isinstance(payload, dict):
        return False, "HTTP 200 but JSON was not an object"
    state = payload.get("status")
    if state != "ok":
        return False, f"HTTP 200 status={state!r}"
    parts = [f"status={state}"]
    for key in ("engine", "database"):
        if key in payload:
            parts.append(f"{key}={payload.get(key)}")
    return True, " ".join(parts)


def probe_once(url: str, timeout: int = TIMEOUT_SECONDS) -> tuple[int | None, str, str | None]:
    request = urllib.request.Request(
        url,
        method="GET",
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(4096)
            return response.status, raw.decode("utf-8", errors="replace"), None
    except urllib.error.HTTPError as exc:
        raw = exc.read(4096) if exc.fp is not None else b""
        return exc.code, raw.decode("utf-8", errors="replace"), None
    except Exception as exc:  # noqa: BLE001 — network failures are the signal
        return None, "", f"{type(exc).__name__}"


def probe_check(
    name: str,
    url: str,
    fetch=probe_once,
    sleep=time.sleep,
    attempts: int = ATTEMPTS,
) -> ProbeResult:
    detail = "no attempt"
    for attempt in range(1, attempts + 1):
        status, body, error = fetch(url)
        ok, detail = judge_body(status, body, error)
        if ok:
            return ProbeResult(name, url, True, detail)
        if attempt < attempts:
            sleep(RETRY_SECONDS)
    return ProbeResult(name, url, False, detail)


def signature_for(results: list[ProbeResult]) -> str:
    failed = [f"{item.name}:{item.detail}" for item in results if not item.ok]
    if not failed:
        return "ok"
    return " | ".join(failed)


def plan_action(
    healthy: bool,
    signature: str,
    issue: OpenIssue | None,
    now: datetime,
    cooldown: timedelta = COMMENT_COOLDOWN,
) -> Action:
    if issue is None:
        return Action.NONE if healthy else Action.OPEN
    if healthy:
        return Action.CLOSE
    same = issue.last_signature == signature
    recent = (
        issue.last_marker_at is not None
        and now - issue.last_marker_at < cooldown
    )
    if same and recent:
        return Action.SKIP_COMMENT
    return Action.COMMENT


def _marker_line(signature: str) -> str:
    safe = signature.replace("--", "-")
    return f"{MARKER} signature={safe} -->"


def _signature_from_text(text: str) -> str | None:
    needle = f"{MARKER} signature="
    start = text.rfind(needle)
    if start < 0:
        return None
    rest = text[start + len(needle):]
    end = rest.find("-->")
    if end < 0:
        return None
    return rest[:end].strip()


def render_down_body(results: list[ProbeResult], run_url: str) -> str:
    lines = [
        "Public CRM health checks failed.",
        "",
        "| Check | Result |",
        "| --- | --- |",
    ]
    for item in results:
        state = "pass" if item.ok else "FAIL"
        lines.append(f"| {item.name} | {state}: {item.detail} |")
    lines.extend([
        "",
        f"Workflow run: {run_url}" if run_url else "Workflow run: (local)",
        "",
        "This issue stays open until a later run sees every check pass.",
        "Disable the schedule from the Actions tab: Production uptime → Disable workflow.",
        "",
        _marker_line(signature_for(results)),
    ])
    return "\n".join(lines)


def render_up_body(run_url: str) -> str:
    where = run_url or "(local)"
    return "\n".join([
        "Public CRM health checks are passing again. Closing this issue.",
        "",
        f"Workflow run: {where}",
        "",
        _marker_line("ok"),
    ])


def _redact(text: str) -> str:
    token = os.environ.get("GITHUB_TOKEN") or ""
    if token and token in text:
        text = text.replace(token, "<redacted>")
    return text


class GitHubIssues:
    """Minimal Issues API client. Never prints the token."""

    def __init__(self, repo: str, token: str, api_url: str = "https://api.github.com"):
        self.repo = repo
        self._token = token
        self._api = api_url.rstrip("/")

    def _request(
        self,
        method: str,
        path: str,
        payload: dict | None = None,
        quiet: tuple[int, ...] = (),
    ) -> tuple[int, dict | list | None]:
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{self._api}{path}",
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {self._token}",
                "Accept": "application/vnd.github+json",
                "User-Agent": USER_AGENT,
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                raw = response.read().decode("utf-8")
                return response.status, json.loads(raw) if raw else None
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            if exc.code not in quiet:
                print(f"GitHub API {method} {path} -> HTTP {exc.code}: {_redact(raw)[:300]}", file=sys.stderr)
            return exc.code, None

    def ensure_label(self) -> None:
        status, _ = self._request("GET", f"/repos/{self.repo}/labels/{LABEL}", quiet=(404,))
        if status == 200:
            return
        self._request(
            "POST",
            f"/repos/{self.repo}/labels",
            {
                "name": LABEL,
                "color": "b60205",
                "description": "Public leads/paperwork health checks are failing",
            },
        )

    def open_prod_down_issues(self) -> list[dict] | None:
        """Open prod-down issues, or None when the lookup itself failed."""
        status, payload = self._request(
            "GET",
            f"/repos/{self.repo}/issues?state=open&labels={LABEL}&per_page=20",
        )
        if status != 200 or not isinstance(payload, list):
            return None
        issues = [item for item in payload if isinstance(item, dict) and "pull_request" not in item]
        issues.sort(key=lambda item: int(item.get("number") or 0))
        return issues

    def _comments(self, number: int) -> list[dict]:
        status, payload = self._request(
            "GET",
            f"/repos/{self.repo}/issues/{number}/comments?per_page=100",
        )
        if status != 200 or not isinstance(payload, list):
            return []
        return [item for item in payload if isinstance(item, dict)]

    def describe(self, issue: dict) -> OpenIssue:
        number = int(issue["number"])
        signature = _signature_from_text(issue.get("body") or "")
        marker_at = _parse_time(issue.get("created_at")) if signature else None
        for comment in self._comments(number):
            found = _signature_from_text(comment.get("body") or "")
            if not found:
                continue
            signature = found
            marker_at = _parse_time(comment.get("created_at")) or marker_at
        return OpenIssue(number=number, last_signature=signature, last_marker_at=marker_at)

    def create(self, title: str, body: str) -> int | None:
        self.ensure_label()
        status, payload = self._request(
            "POST",
            f"/repos/{self.repo}/issues",
            {"title": title, "body": body, "labels": [LABEL]},
        )
        if status not in (200, 201) or not isinstance(payload, dict):
            return None
        return int(payload["number"])

    def comment(self, number: int, body: str) -> bool:
        status, _ = self._request(
            "POST",
            f"/repos/{self.repo}/issues/{number}/comments",
            {"body": body},
        )
        return status in (200, 201)

    def close(self, number: int) -> bool:
        status, _ = self._request(
            "PATCH",
            f"/repos/{self.repo}/issues/{number}",
            {"state": "closed", "state_reason": "completed"},
        )
        return status == 200


def _run_url() -> str:
    server = os.environ.get("GITHUB_SERVER_URL") or "https://github.com"
    repo = os.environ.get("GITHUB_REPOSITORY") or ""
    run_id = os.environ.get("GITHUB_RUN_ID") or ""
    if repo and run_id:
        return f"{server}/{repo}/actions/runs/{run_id}"
    return ""


def apply_action(
    action: Action,
    results: list[ProbeResult],
    issues: list[OpenIssue],
    client: GitHubIssues,
    run_url: str,
) -> bool:
    """Perform the plan. Return True when production is healthy."""
    if action is Action.NONE:
        print("all public health checks passed")
        return True
    if action is Action.OPEN:
        body = render_down_body(results, run_url)
        number = client.create("[prod-down] Public CRM health checks failing", body)
        if number is None:
            print("failed to open prod-down issue", file=sys.stderr)
        else:
            print(f"opened issue #{number}")
        return False
    primary = issues[0]
    if action is Action.SKIP_COMMENT:
        print(f"still failing; issue #{primary.number} already has this failure within the last hour")
        return False
    if action is Action.COMMENT:
        if client.comment(primary.number, render_down_body(results, run_url)):
            print(f"commented on issue #{primary.number}")
        else:
            print(f"failed to comment on issue #{primary.number}", file=sys.stderr)
        return False
    if action is Action.CLOSE:
        note = render_up_body(run_url)
        for issue in issues:
            client.comment(issue.number, note)
            if client.close(issue.number):
                print(f"closed issue #{issue.number}")
            else:
                print(f"failed to close issue #{issue.number}", file=sys.stderr)
                return False
        return True
    return False


def main() -> int:
    results = [probe_check(name, url) for name, url in CHECKS]
    for item in results:
        mark = "ok" if item.ok else "FAIL"
        print(f"{mark} {item.name} {item.detail}")
    healthy = all(item.ok for item in results)
    signature = signature_for(results)

    repo = os.environ.get("GITHUB_REPOSITORY") or ""
    token = os.environ.get("GITHUB_TOKEN") or ""
    if not repo or not token:
        print("GITHUB_REPOSITORY and GITHUB_TOKEN are required to file issues", file=sys.stderr)
        return 0 if healthy else 1

    client = GitHubIssues(repo, token, os.environ.get("GITHUB_API_URL") or "https://api.github.com")
    raw_issues = client.open_prod_down_issues()
    if raw_issues is None:
        print("could not list open prod-down issues; not opening another", file=sys.stderr)
        return 1
    described = [client.describe(item) for item in raw_issues]
    current = described[0] if described else None
    action = plan_action(healthy, signature, current, _utc_now())
    print(f"action={action.value}")
    ok = apply_action(action, results, described, client, _run_url())
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
