"""Canonical Shamrock public hostname inventory.

Every official ``*.shamrockbailbonds.biz`` host lives here. Nginx vhosts,
``scripts/check_subdomains.py``, and ``docs/SUBDOMAINS.md`` must stay aligned.

DNS is Wix (apex + most A records). Cloudflare tunnels are only ``bb`` + ``imac``.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

REPO_ROOT = Path(__file__).resolve().parent.parent
NGINX_DIR = REPO_ROOT / "nginx"

VPS_IP = "178.156.179.237"
OPENCUT_DOCKER_ORIGIN = "127.0.0.1:5320"  # docker compose --profile edit

Origin = Literal["wix", "vps_nginx", "netlify", "cloudflare_tunnel"]


@dataclass(frozen=True)
class Subdomain:
    host: str
    role: str
    origin: Origin
    nginx_conf: str | None = None  # filename under nginx/
    upstream: str | None = None
    notes: str = ""
    public_client: bool = False  # True = client-facing (never staff CRM)


SUBDOMAINS: tuple[Subdomain, ...] = (
    Subdomain(
        host="shamrockbailbonds.biz",
        role="Brand apex / Wix portal",
        origin="wix",
        public_client=True,
        notes="Wix site. Client-facing intake and marketing.",
    ),
    Subdomain(
        host="www.shamrockbailbonds.biz",
        role="Brand www (Wix CDN)",
        origin="wix",
        public_client=True,
        notes="CNAME to Wix CDN.",
    ),
    Subdomain(
        host="leads.shamrockbailbonds.biz",
        role="Bond Auto-CRM dashboard",
        origin="vps_nginx",
        nginx_conf="leads.shamrockbailbonds.biz.conf",
        upstream="127.0.0.1:8088",
        notes="Staff PIN portal. Docker dashboard :5050 → host :8088.",
    ),
    Subdomain(
        host="school.shamrockbailbonds.biz",
        role="Bail School LMS",
        origin="netlify",
        public_client=True,
        notes="shamrock-bail-school on Netlify. Not this repo.",
    ),
    Subdomain(
        host="sign.shamrockbailbonds.biz",
        role="DocuSeal e-sign",
        origin="vps_nginx",
        nginx_conf="sign.shamrockbailbonds.biz.conf",
        upstream="127.0.0.1:5300",
        public_client=True,
        notes="Self-hosted DocuSeal. docker compose --profile paperwork.",
    ),
    Subdomain(
        host="paperwork.shamrockbailbonds.biz",
        role="Indemnitor / defendant signing portal",
        origin="vps_nginx",
        nginx_conf="paperwork.shamrockbailbonds.biz.conf",
        upstream="127.0.0.1:8088",
        public_client=True,
        notes="PIN portal via dashboard pin_portal (host-aware). Not a separate :5310 app.",
    ),
    Subdomain(
        host="social.shamrockbailbonds.biz",
        role="Postiz social scheduler + MCP",
        origin="vps_nginx",
        nginx_conf="social.shamrockbailbonds.biz.conf",
        upstream="127.0.0.1:5200",
        notes="Postiz Docker. Not OpenCut — video editor is edit.*.",
    ),
    Subdomain(
        host="edit.shamrockbailbonds.biz",
        role="OpenCut video editor",
        origin="vps_nginx",
        nginx_conf="edit.shamrockbailbonds.biz.conf",
        upstream=OPENCUT_DOCKER_ORIGIN,
        notes=(
            "VPS nginx → Docker shamrock-opencut :5320 "
            "(compose profile edit). Not Postiz, not the laptop."
        ),
    ),
    Subdomain(
        host="bb.shamrockbailbonds.biz",
        role="BlueBubbles iMessage bridge",
        origin="cloudflare_tunnel",
        nginx_conf="bb.shamrockbailbonds.biz.conf",
        upstream="127.0.0.1:12434",
        notes="Primary: Cloudflare named tunnel. Optional VPS nginx → frp :12434.",
    ),
    Subdomain(
        host="imac.shamrockbailbonds.biz",
        role="Office iMac SSH (Cloudflare tunnel)",
        origin="cloudflare_tunnel",
        notes="SSH via named tunnel. Not a public HTTP service.",
    ),
    Subdomain(
        host="trape.shamrockbailbonds.biz",
        role="Trape OSINT lure (on-demand)",
        origin="vps_nginx",
        nginx_conf="trape.shamrockbailbonds.biz.conf",
        upstream="127.0.0.1:8099",
        notes="Started per skip-trace session. 502 when Trape is down is expected.",
    ),
)


def all_hosts() -> list[str]:
    return [s.host for s in SUBDOMAINS]


def by_host(host: str) -> Subdomain | None:
    needle = host.strip().lower().removeprefix("https://").removeprefix("http://").split("/")[0]
    for s in SUBDOMAINS:
        if s.host == needle:
            return s
    return None


def vps_nginx_hosts() -> list[Subdomain]:
    return [s for s in SUBDOMAINS if s.origin == "vps_nginx"]


def required_nginx_confs() -> list[Subdomain]:
    return [s for s in SUBDOMAINS if s.nginx_conf]
