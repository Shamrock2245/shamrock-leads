#!/usr/bin/env python3
"""Inventory + live probe for official Shamrock hostnames.

Default (no flags): print the registry and verify every vps_nginx / optional
TLS-front host has a matching nginx/<host>.conf with the right server_name.

  python scripts/check_subdomains.py
  python scripts/check_subdomains.py --live
"""
from __future__ import annotations

import argparse
import socket
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config.subdomains import (  # noqa: E402
    NGINX_DIR,
    SUBDOMAINS,
    required_nginx_confs,
)


def _server_names(conf_text: str) -> set[str]:
    names: set[str] = set()
    for line in conf_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("server_name") and stripped.endswith(";"):
            rest = stripped[len("server_name") :].strip().rstrip(";")
            names.update(tok for tok in rest.split() if tok)
    return names


def check_repo() -> list[str]:
    errors: list[str] = []
    hosts = [s.host for s in SUBDOMAINS]
    if len(hosts) != len(set(hosts)):
        errors.append("Duplicate host in config.subdomains.SUBDOMAINS")

    for sub in required_nginx_confs():
        path = NGINX_DIR / sub.nginx_conf
        if not path.is_file():
            errors.append(f"Missing nginx conf: {path.relative_to(ROOT)} ({sub.host})")
            continue
        text = path.read_text(encoding="utf-8")
        names = _server_names(text)
        if sub.host not in names:
            errors.append(
                f"{path.name} server_name {sorted(names)} does not include {sub.host}"
            )
    return errors


def _dns_a(host: str) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, None, socket.AF_INET, socket.SOCK_STREAM)
    except socket.gaierror:
        return []
    seen: list[str] = []
    for info in infos:
        ip = info[4][0]
        if ip not in seen:
            seen.append(ip)
    return seen


def _http_status(host: str, timeout: float) -> str:
    url = f"https://{host}/"
    for method in ("HEAD", "GET"):
        req = urllib.request.Request(url, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return str(resp.status)
        except urllib.error.HTTPError as exc:
            # FastAPI GET-only routes 404 on HEAD; retry GET.
            if method == "HEAD" and exc.code in (404, 405):
                continue
            return str(exc.code)
        except Exception as exc:  # noqa: BLE001 — ops probe, any failure is a status
            if method == "HEAD":
                continue
            return type(exc).__name__
    return "unreachable"


def check_live(timeout: float) -> list[str]:
    warnings: list[str] = []
    print(f"\n{'Host':42} {'DNS':28} HTTPS")
    print("-" * 80)
    for sub in SUBDOMAINS:
        ips = _dns_a(sub.host)
        dns = ", ".join(ips) if ips else "(no A)"
        if not ips:
            https = "—"
            # Cloudflare tunnels often have no A (proxied CNAME / off when Mac sleeps).
            if sub.origin != "cloudflare_tunnel":
                warnings.append(f"DNS missing: {sub.host}")
        elif sub.host.startswith("imac."):
            https = "n/a (ssh)"
        elif sub.host.startswith("trape."):
            https = _http_status(sub.host, timeout)
            # On-demand skip-trace lure — down/cert-mismatch is expected.
        else:
            https = _http_status(sub.host, timeout)
            if https not in {"200", "301", "302", "303", "307", "308", "401", "403"}:
                warnings.append(f"{sub.host} HTTPS {https}")
        print(f"{sub.host:42} {dns:28} {https}")
    return warnings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live",
        action="store_true",
        help="Resolve DNS and HEAD https://<host>/ for every official hostname",
    )
    parser.add_argument("--timeout", type=float, default=8.0)
    args = parser.parse_args()

    print("🍀 Shamrock subdomain inventory")
    print(f"{'Host':42} {'Origin':20} Role")
    print("-" * 90)
    for sub in SUBDOMAINS:
        print(f"{sub.host:42} {sub.origin:20} {sub.role}")

    errors = check_repo()
    if errors:
        print("\n❌ Repo inventory errors:")
        for err in errors:
            print(f"   • {err}")
        return 1
    print(f"\n✅ Repo: {len(SUBDOMAINS)} hosts, {len(required_nginx_confs())} nginx vhosts")

    if args.live:
        warnings = check_live(args.timeout)
        if warnings:
            print("\n⚠️  Live gaps:")
            for warn in warnings:
                print(f"   • {warn}")
            return 2
        print("\n✅ Live DNS present for all official hosts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
