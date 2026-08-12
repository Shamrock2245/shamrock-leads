"""Repo inventory: every official Shamrock host has a tracked nginx vhost."""
from __future__ import annotations

from config.subdomains import (
    NGINX_DIR,
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


def test_edit_is_opencut_on_vps_nginx():
    edit = by_host("edit.shamrockbailbonds.biz")
    assert edit is not None
    assert edit.origin == "vps_nginx"
    assert edit.nginx_conf == "edit.shamrockbailbonds.biz.conf"
    assert edit.upstream and "100.119.187.33" in edit.upstream
    assert "social" not in edit.role.lower()


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
