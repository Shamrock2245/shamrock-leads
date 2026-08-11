"""Unit tests for GoogleDriveService auth hardening."""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from dashboard.services.google_drive_service import (
    DRIVE_SCOPE,
    INVALID_SCOPE_HINT,
    GoogleDriveService,
)


@pytest.fixture(autouse=True)
def _clear_google_env(monkeypatch):
    for key in (
        "GOOGLE_CLIENT_ID",
        "GOOGLE_CLIENT_SECRET",
        "GOOGLE_GMAIL_REFRESH_TOKEN",
        "GOOGLE_DRIVE_REFRESH_TOKEN",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "GOOGLE_SERVICE_ACCOUNT_KEY_PATH",
        "GOOGLE_SERVICE_ACCOUNT_JSON",
        "GOOGLE_SA_KEY_JSON",
        "COMPLETED_BONDS_FOLDER_ID",
        "GOOGLE_DRIVE_OUTPUT_FOLDER_ID",
    ):
        monkeypatch.delenv(key, raising=False)
    yield


def test_not_configured():
    drive = GoogleDriveService()
    # Repo may ship a local SA JSON; force both paths off for this unit test
    with patch.object(drive, "_has_service_account", return_value=False), patch.object(
        drive, "_has_oauth", return_value=False
    ):
        assert drive.is_configured is False
        assert drive._get_service() is None
        assert drive.last_error_code == "not_configured"
        health = drive.health_check()
        assert health["ok"] is False
        assert health["error_code"] == "not_configured"


def test_oauth_preferred_token_env(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "cid")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "sec")
    monkeypatch.setenv("GOOGLE_DRIVE_REFRESH_TOKEN", "drive-token")
    monkeypatch.setenv("GOOGLE_GMAIL_REFRESH_TOKEN", "gmail-token")
    drive = GoogleDriveService()
    assert drive._refresh_token == "drive-token"
    assert drive._has_oauth() is True


def test_oauth_falls_back_to_gmail_token(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "cid")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "sec")
    monkeypatch.setenv("GOOGLE_GMAIL_REFRESH_TOKEN", "gmail-token")
    drive = GoogleDriveService()
    assert drive._refresh_token == "gmail-token"


def test_invalid_scope_surfaces_hint(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "cid")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "sec")
    monkeypatch.setenv("GOOGLE_GMAIL_REFRESH_TOKEN", "bad-token")

    drive = GoogleDriveService()

    with patch.object(drive, "_has_service_account", return_value=False), patch.object(
        drive, "_load_oauth_credentials"
    ) as load:
        load.side_effect = Exception("invalid_scope: Bad Request")
        svc = drive._get_service()
        assert svc is None
        assert drive.last_error_code == "invalid_scope"
        assert "Drive scope" in (drive.last_error or "")
        assert "get_gmail_token" in (drive.last_error or "")


def test_auto_prefers_oauth_over_service_account(monkeypatch, tmp_path):
    """My Drive filing needs user OAuth quota; OAuth is first in auto mode."""
    sa_path = tmp_path / "sa.json"
    sa_path.write_text('{"type": "service_account"}')
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", str(sa_path))
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "cid")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "sec")
    monkeypatch.setenv("GOOGLE_GMAIL_REFRESH_TOKEN", "token")
    monkeypatch.setenv("GOOGLE_DRIVE_AUTH_MODE", "auto")

    drive = GoogleDriveService()
    mock_service = MagicMock()

    with patch.object(drive, "_load_oauth_credentials", return_value=MagicMock()), patch.object(
        drive, "_load_service_account_credentials", return_value=MagicMock()
    ), patch("googleapiclient.discovery.build", return_value=mock_service):
        svc = drive._get_service()
        assert svc is mock_service
        assert drive.auth_mode == "oauth"


def test_sa_quota_falls_back_to_oauth_on_upload(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "cid")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "sec")
    monkeypatch.setenv("GOOGLE_GMAIL_REFRESH_TOKEN", "token")

    drive = GoogleDriveService()
    drive._auth_mode = "service_account"
    sa_service = MagicMock()
    sa_service.files.return_value.create.return_value.execute.side_effect = Exception(
        "storageQuotaExceeded: Service Accounts do not have storage quota"
    )
    oauth_service = MagicMock()
    oauth_service.files.return_value.create.return_value.execute.return_value = {
        "id": "file1",
        "webViewLink": "https://drive.google.com/file/d/file1",
    }

    calls = {"n": 0}

    def fake_get_service(force_mode=None):
        calls["n"] += 1
        if force_mode == "oauth" or drive._auth_mode == "oauth":
            drive._auth_mode = "oauth"
            drive._service = oauth_service
            return oauth_service
        drive._auth_mode = "service_account"
        drive._service = sa_service
        return sa_service

    with patch.object(drive, "_get_service", side_effect=fake_get_service), patch.object(
        drive, "_switch_auth_mode", side_effect=lambda m: fake_get_service(force_mode=m) is not None
    ), patch.object(drive, "_has_oauth", return_value=True):
        link = drive.upload_pdf(b"%PDF", "t.pdf", "folder1")
        assert link == "https://drive.google.com/file/d/file1"
        assert drive.auth_mode == "oauth"


def test_health_check_folder_ok(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "cid")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "sec")
    monkeypatch.setenv("GOOGLE_GMAIL_REFRESH_TOKEN", "token")
    monkeypatch.setenv("COMPLETED_BONDS_FOLDER_ID", "folder123")

    drive = GoogleDriveService()
    mock_service = MagicMock()
    mock_service.files.return_value.get.return_value.execute.return_value = {
        "id": "folder123",
        "name": "Completed Bonds",
        "mimeType": "application/vnd.google-apps.folder",
        "capabilities": {"canAddChildren": True},
    }

    with patch.object(drive, "_get_service", return_value=mock_service), patch.object(
        drive, "_probe_write", return_value=(True, None)
    ):
        result = drive.health_check()
        assert result["ok"] is True
        assert result["folder_accessible"] is True
        assert result["folder_writable"] is True
        assert result["folder_name"] == "Completed Bonds"


def test_health_check_folder_not_writable(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "cid")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "sec")
    monkeypatch.setenv("GOOGLE_GMAIL_REFRESH_TOKEN", "token")
    monkeypatch.setenv("COMPLETED_BONDS_FOLDER_ID", "folder123")

    drive = GoogleDriveService()
    mock_service = MagicMock()
    mock_service.files.return_value.get.return_value.execute.return_value = {
        "id": "folder123",
        "name": "Completed Bonds",
        "capabilities": {"canAddChildren": False},
    }

    with patch.object(drive, "_get_service", return_value=mock_service):
        result = drive.health_check(probe_write=False)
        assert result["ok"] is False
        assert result["error_code"] == "folder_not_writable"


def test_upload_pdf_propagates_last_error(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "cid")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "sec")
    monkeypatch.setenv("GOOGLE_GMAIL_REFRESH_TOKEN", "token")

    drive = GoogleDriveService()
    mock_service = MagicMock()
    mock_service.files.return_value.create.return_value.execute.side_effect = Exception(
        "invalid_scope: Bad Request"
    )

    with patch.object(drive, "_get_service", return_value=mock_service):
        link = drive.upload_pdf(b"%PDF-1.4", "test.pdf", "folder1")
        assert link is None
        assert drive.last_error_code == "invalid_scope"
        assert INVALID_SCOPE_HINT[:20] in (drive.last_error or "")


def test_drive_scope_constant():
    assert DRIVE_SCOPE.endswith("/auth/drive")


def test_sa_path_fallback_when_docker_path_missing(monkeypatch, tmp_path):
    """Docker .env may set /app/creds/... which is absent on the host."""
    sa_path = tmp_path / "service-account-key.json"
    sa_path.write_text('{"type": "service_account"}')
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/app/creds/missing.json")

    drive = GoogleDriveService()
    with patch.object(
        drive,
        "_service_account_candidates",
        return_value=["/app/creds/missing.json", str(sa_path)],
    ):
        assert drive._resolve_service_account_path() == str(sa_path)
        assert drive._has_service_account() is True
