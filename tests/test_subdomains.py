"""Repo inventory: every official Shamrock host has a tracked nginx vhost."""
from __future__ import annotations

from pathlib import Path

from config.subdomains import (
    NGINX_DIR,
    REPO_ROOT,
    SUBDOMAINS,
    all_hosts,
    by_host,
    required_nginx_confs,
    vps_nginx_hosts,
)


REQUIRED_HOSTS = {
    "shamrockbailbonds.biz",
    "www.shamrockbailbonds.biz",
    "leads.shamrockbailbonds.biz",
    "school.shamrockbailbonds.biz",
    "sign.shamrockbailbonds.biz",
    "paperwork.shamrockbailbonds.biz",
    "social.shamrockbailbonds.biz",
    "edit.shamrockbailbonds.biz",
    "bb.shamrockbailbonds.biz",
    "imac.shamrockbailbonds.biz",
    "trape.shamrockbailbonds.biz",
}


def test_official_hosts_are_registered():
    missing = REQUIRED_HOSTS - set(all_hosts())
    assert not missing, f"Official hosts missing from config.subdomains: {sorted(missing)}"


def test_hosts_are_unique():
    hosts = all_hosts()
    assert len(hosts) == len(set(hosts))


def test_edit_is_opencut_on_vps_docker():
    edit = by_host("edit.shamrockbailbonds.biz")
    assert edit is not None
    assert edit.origin == "vps_nginx"
    assert edit.nginx_conf == "edit.shamrockbailbonds.biz.conf"
    assert edit.upstream == "127.0.0.1:5320"
    assert "social" not in edit.role.lower()
    assert "100.119.187.33" not in (edit.notes or "")


def test_edit_nginx_proxies_to_local_docker():
    text = (NGINX_DIR / "edit.shamrockbailbonds.biz.conf").read_text(encoding="utf-8")
    assert "127.0.0.1:5320" in text
    assert "100.119.187.33" not in text
    assert "opencut_ts" not in text
    assert "server_name edit.shamrockbailbonds.biz" in text
    assert "listen 443 ssl" in text
    assert "ssl_certificate" in text
    assert "return 301 https://$host$request_uri" in text


def test_social_is_postiz_not_opencut():
    social = by_host("social.shamrockbailbonds.biz")
    assert social is not None
    assert "postiz" in social.role.lower()
    assert social.upstream == "127.0.0.1:5200"


def test_every_vps_host_has_nginx_conf():
    missing = [s.host for s in vps_nginx_hosts() if not s.nginx_conf]
    assert not missing, f"vps_nginx hosts without nginx_conf: {missing}"


def test_required_nginx_confs_exist_and_name_the_host():
    for sub in required_nginx_confs():
        path = NGINX_DIR / sub.nginx_conf
        assert path.is_file(), f"missing {path}"
        text = path.read_text(encoding="utf-8")
        assert f"server_name {sub.host}" in text, f"{path.name} missing server_name {sub.host}"


def test_edit_nginx_not_duplicated_at_repo_root():
    leftover = Path(REPO_ROOT) / "edit.shamrockbailbonds.biz.conf"
    assert not leftover.exists(), "edit vhost lives in nginx/, not the repo root"


def test_opencut_compose_profile_exists():
    compose = (Path(REPO_ROOT) / "docker-compose.yml").read_text(encoding="utf-8")
    assert "container_name: shamrock-opencut" in compose
    assert "5320:3000" in compose
    dockerfile = Path(REPO_ROOT) / "opencut" / "Dockerfile"
    assert dockerfile.is_file()
    assert "opencut-classic" in dockerfile.read_text(encoding="utf-8")
