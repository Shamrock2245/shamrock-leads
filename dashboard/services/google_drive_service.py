"""
Google Drive Service — ShamrockLeads
====================================
Archives signed bond PDFs under Completed Bonds.

Auth priority (auto mode — first usable wins):
  1. OAuth user refresh token with Drive scope
     GOOGLE_DRIVE_REFRESH_TOKEN or GOOGLE_GMAIL_REFRESH_TOKEN
     → files use admin@ storage quota (required for personal My Drive folders)
  2. Service account JSON
     GOOGLE_APPLICATION_CREDENTIALS / GOOGLE_SERVICE_ACCOUNT_JSON
     → works for Shared Drives; FAILS on personal My Drive (storageQuotaExceeded)

Why OAuth is preferred for Completed Bonds:
  Service accounts have **zero** My Drive storage quota. Uploading into a
  user-owned folder shared with the SA returns HTTP 403 storageQuotaExceeded.
  Re-auth with Drive scope fixes this for the standard Shamrock layout.

Re-auth:
  python scripts/get_gmail_token.py   # Gmail + Calendar + Drive
  → update GOOGLE_GMAIL_REFRESH_TOKEN in .env

Verify:
  python scripts/verify_drive_auth.py
"""

from __future__ import annotations

import base64
import io
import json
import logging
import os
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

DRIVE_SCOPE = "https://www.googleapis.com/auth/drive"
DRIVE_SCOPES = [DRIVE_SCOPE]

INVALID_SCOPE_HINT = (
    "Google OAuth refresh token lacks Drive scope (invalid_scope). "
    "Re-run `python scripts/get_gmail_token.py` (grants Gmail + Calendar + Drive), "
    "then update GOOGLE_GMAIL_REFRESH_TOKEN (or GOOGLE_DRIVE_REFRESH_TOKEN) in .env "
    "and restart the dashboard. Service-account upload into personal My Drive "
    "will fail with storageQuotaExceeded — OAuth is required for that layout."
)

SA_QUOTA_HINT = (
    "Service accounts have no My Drive storage quota. Completed Bonds is a "
    "personal Drive folder, so SA uploads fail (storageQuotaExceeded). "
    "Fix: re-run `python scripts/get_gmail_token.py` with Drive scope and set "
    "GOOGLE_GMAIL_REFRESH_TOKEN, OR move Completed Bonds into a Shared Drive "
    "and add the SA as a Content Manager."
)


class GoogleDriveAuthError(RuntimeError):
    """Raised when Drive credentials are missing or fail scope/token checks."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


class GoogleDriveService:
    """
    Upload / folder helpers for Completed Bonds archival.

    Surfaces last_error for callers that only check None return values.
    """

    def __init__(self):
        self._client_id = (os.getenv("GOOGLE_CLIENT_ID") or "").strip()
        self._client_secret = (os.getenv("GOOGLE_CLIENT_SECRET") or "").strip()
        # Dedicated Drive token first; shared multi-scope token second
        self._refresh_token = (
            os.getenv("GOOGLE_DRIVE_REFRESH_TOKEN")
            or os.getenv("GOOGLE_GMAIL_REFRESH_TOKEN")
            or ""
        ).strip()
        self._service = None
        self._auth_mode: Optional[str] = None  # "oauth" | "service_account"
        self._last_error: Optional[str] = None
        self._last_error_code: Optional[str] = None
        self._oauth_error: Optional[Tuple[str, str]] = None
        self._sa_error: Optional[Tuple[str, str]] = None

    # ── Configuration ───────────────────────────────────────────────────────

    @property
    def last_error(self) -> Optional[str]:
        return self._last_error

    @property
    def last_error_code(self) -> Optional[str]:
        return self._last_error_code

    @property
    def auth_mode(self) -> Optional[str]:
        return self._auth_mode

    def _set_error(self, code: str, message: str) -> None:
        self._last_error_code = code
        self._last_error = message
        logger.error("[Drive] %s: %s", code, message)

    def _clear_error(self) -> None:
        self._last_error = None
        self._last_error_code = None

    @property
    def is_configured(self) -> bool:
        """True if either service-account or OAuth credentials look present."""
        return self._has_service_account() or self._has_oauth()

    def _auth_preference(self) -> str:
        """
        auto (default) → OAuth first (My Drive quota), then service account.
        Override: GOOGLE_DRIVE_AUTH_MODE=oauth|service_account|auto
        """
        mode = (os.getenv("GOOGLE_DRIVE_AUTH_MODE") or "auto").strip().lower()
        if mode in ("oauth", "service_account", "auto"):
            return mode
        return "auto"

    def _has_oauth(self) -> bool:
        return bool(self._client_id and self._client_secret and self._refresh_token)

    def _service_account_candidates(self) -> List[str]:
        """
        Ordered candidate paths for the SA JSON.

        Handles Docker .env values like /app/creds/... when running on the host
        by falling back to repo-relative creds/service-account-key.json.
        """
        candidates: List[str] = []
        for key in (
            "GOOGLE_SERVICE_ACCOUNT_KEY_PATH",
            "GOOGLE_APPLICATION_CREDENTIALS",
        ):
            val = (os.getenv(key) or "").strip()
            if val:
                candidates.append(val)

        here = os.path.abspath(os.path.dirname(__file__))
        project_root = os.path.abspath(os.path.join(here, "..", ".."))
        candidates.extend(
            [
                os.path.join(project_root, "creds", "service-account-key.json"),
                os.path.join(os.getcwd(), "creds", "service-account-key.json"),
                "/app/creds/service-account-key.json",
            ]
        )

        seen = set()
        ordered: List[str] = []
        for p in candidates:
            if p not in seen:
                seen.add(p)
                ordered.append(p)
        return ordered

    def _resolve_service_account_path(self) -> Optional[str]:
        for path in self._service_account_candidates():
            if path and os.path.isfile(path):
                return path
        return None

    def _has_service_account(self) -> bool:
        if (os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON") or os.getenv("GOOGLE_SA_KEY_JSON") or "").strip():
            return True
        return self._resolve_service_account_path() is not None

    def completed_bonds_folder_id(self) -> str:
        return (
            os.getenv("COMPLETED_BONDS_FOLDER_ID")
            or os.getenv("GOOGLE_DRIVE_OUTPUT_FOLDER_ID")
            or ""
        ).strip()

    # ── Auth ────────────────────────────────────────────────────────────────

    def _load_service_account_credentials(self):
        from google.oauth2 import service_account

        env_var = (
            os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
            or os.getenv("GOOGLE_SA_KEY_JSON")
            or ""
        ).strip()
        if env_var:
            content = env_var
            if not content.startswith("{"):
                content = base64.b64decode(content).decode("utf-8")
            info = json.loads(content)
            return service_account.Credentials.from_service_account_info(
                info, scopes=DRIVE_SCOPES
            )

        path = self._resolve_service_account_path()
        if path:
            logger.info("[Drive] Using service account file: %s", path)
            return service_account.Credentials.from_service_account_file(
                path, scopes=DRIVE_SCOPES
            )
        return None

    def _load_oauth_credentials(self):
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request

        if not self._has_oauth():
            return None

        creds = Credentials(
            token=None,
            refresh_token=self._refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=self._client_id,
            client_secret=self._client_secret,
            scopes=DRIVE_SCOPES,
        )
        # Force refresh now so invalid_scope fails at auth time, not mid-upload
        creds.refresh(Request())
        return creds

    def _classify_exception(self, exc: Exception) -> Tuple[str, str]:
        text = str(exc)
        low = text.lower()
        if "invalid_scope" in low or ("bad request" in low and "scope" in low):
            return "invalid_scope", INVALID_SCOPE_HINT
        if "storagequotaexceeded" in low or "storage quota" in low:
            return "storage_quota_exceeded", SA_QUOTA_HINT
        if "invalid_grant" in low:
            return (
                "invalid_grant",
                "Google OAuth refresh token revoked or expired. "
                "Re-run `python scripts/get_gmail_token.py` and update .env.",
            )
        if "access_denied" in low or "accessdenied" in low.replace(" ", ""):
            return (
                "access_denied",
                "Drive access denied. Share Completed Bonds with the OAuth user "
                "or service account as Editor.",
            )
        if "404" in low or "not found" in low or "notfound" in low.replace(" ", ""):
            return (
                "folder_not_found",
                "Folder not found or not shared with the authenticated identity.",
            )
        return "auth_failed", f"Drive error: {text}"[:400]

    def _mode_order(self) -> List[str]:
        pref = self._auth_preference()
        if pref == "oauth":
            return ["oauth", "service_account"]
        if pref == "service_account":
            return ["service_account", "oauth"]
        # auto: OAuth first (My Drive), SA second (Shared Drives)
        return ["oauth", "service_account"]

    def _build_for_mode(self, mode: str):
        from googleapiclient.discovery import build

        if mode == "oauth":
            if not self._has_oauth():
                return None
            creds = self._load_oauth_credentials()
        elif mode == "service_account":
            if not self._has_service_account():
                return None
            creds = self._load_service_account_credentials()
        else:
            return None

        if not creds:
            return None
        return build("drive", "v3", credentials=creds, cache_discovery=False)

    def _get_service(self, *, force_mode: Optional[str] = None):
        if self._service and not force_mode:
            return self._service
        if force_mode and self._service and self._auth_mode == force_mode:
            return self._service

        if not self.is_configured:
            self._set_error(
                "not_configured",
                "Google Drive not configured — set OAuth (GOOGLE_CLIENT_ID/SECRET + "
                "refresh token with Drive scope) or GOOGLE_APPLICATION_CREDENTIALS.",
            )
            return None

        modes = [force_mode] if force_mode else self._mode_order()
        last_code, last_msg = "not_configured", "No usable Drive credentials."

        for mode in modes:
            if mode == "oauth" and not self._has_oauth():
                continue
            if mode == "service_account" and not self._has_service_account():
                continue
            try:
                service = self._build_for_mode(mode)
                if not service:
                    continue
                self._service = service
                self._auth_mode = mode
                self._clear_error()
                logger.info("[Drive] ✅ Google Drive API authenticated (mode=%s)", mode)
                return self._service
            except Exception as exc:
                code, msg = self._classify_exception(exc)
                if mode == "oauth":
                    self._oauth_error = (code, msg)
                else:
                    self._sa_error = (code, msg)
                last_code, last_msg = code, msg
                logger.warning("[Drive] Auth mode %s failed: %s", mode, code)

        # Combine hints when both paths failed
        if self._oauth_error and self._sa_error:
            last_msg = (
                f"OAuth: {self._oauth_error[1]} | Service account: {self._sa_error[1]}"
            )
            last_code = self._oauth_error[0]
        self._set_error(last_code, last_msg)
        return None

    def _switch_auth_mode(self, mode: str) -> bool:
        """Force rebuild with a different auth mode (upload fallback)."""
        self._service = None
        self._auth_mode = None
        return self._get_service(force_mode=mode) is not None

    # ── Health / preflight ──────────────────────────────────────────────────

    def health_check(
        self,
        folder_id: Optional[str] = None,
        *,
        probe_write: bool = True,
    ) -> Dict[str, Any]:
        """
        Validate auth + Completed Bonds folder access.

        probe_write=True (default) creates + trashes a tiny probe file so we
        catch storageQuotaExceeded before production packet archive.
        """
        result: Dict[str, Any] = {
            "ok": False,
            "configured": self.is_configured,
            "has_service_account": self._has_service_account(),
            "has_oauth": self._has_oauth(),
            "auth_mode": None,
            "folder_id": folder_id or self.completed_bonds_folder_id() or None,
            "folder_accessible": False,
            "folder_writable": False,
            "folder_name": None,
            "error": None,
            "error_code": None,
            "hint": None,
            "oauth_error": self._oauth_error[0] if self._oauth_error else None,
        }

        if not self.is_configured:
            result["error"] = "not_configured"
            result["error_code"] = "not_configured"
            result["hint"] = INVALID_SCOPE_HINT
            return result

        service = self._get_service()
        if not service:
            result["error"] = self._last_error
            result["error_code"] = self._last_error_code
            result["hint"] = self._last_error
            result["oauth_error"] = self._oauth_error[0] if self._oauth_error else None
            return result

        result["auth_mode"] = self._auth_mode
        result["oauth_error"] = self._oauth_error[0] if self._oauth_error else None

        target = result["folder_id"]
        if not target:
            result["ok"] = True  # auth works; folder id not set
            result["hint"] = "Auth OK but COMPLETED_BONDS_FOLDER_ID is unset"
            return result

        try:
            meta = (
                service.files()
                .get(
                    fileId=target,
                    fields="id, name, mimeType, capabilities",
                    supportsAllDrives=True,
                )
                .execute()
            )
            result["folder_name"] = meta.get("name")
            result["folder_accessible"] = True
            can_add = (meta.get("capabilities") or {}).get("canAddChildren")
            if can_add is False:
                result["ok"] = False
                result["error_code"] = "folder_not_writable"
                result["error"] = (
                    f"Folder '{meta.get('name')}' is visible but not writable."
                )
                result["hint"] = result["error"]
                return result

            if not probe_write:
                result["ok"] = True
                result["folder_writable"] = can_add is not False
                return result

            # Write probe — catches SA My Drive quota before production traffic
            writable, write_err = self._probe_write(target)
            result["folder_writable"] = writable
            if writable:
                result["ok"] = True
                return result

            # SA quota on My Drive: try OAuth fallback once for health
            if (
                write_err
                and write_err[0] == "storage_quota_exceeded"
                and self._auth_mode == "service_account"
                and self._has_oauth()
            ):
                logger.warning(
                    "[Drive] SA write probe hit quota — retrying health with OAuth"
                )
                if self._switch_auth_mode("oauth"):
                    result["auth_mode"] = self._auth_mode
                    writable2, write_err2 = self._probe_write(target)
                    result["folder_writable"] = writable2
                    if writable2:
                        result["ok"] = True
                        result["hint"] = (
                            "Service account cannot write to My Drive; "
                            "using OAuth successfully."
                        )
                        return result
                    write_err = write_err2 or write_err

            code, msg = write_err or ("write_failed", "Write probe failed")
            # If OAuth was invalid_scope and SA can't write, lead with OAuth fix
            if self._oauth_error and self._oauth_error[0] == "invalid_scope":
                result["error_code"] = "invalid_scope"
                result["error"] = INVALID_SCOPE_HINT
                result["hint"] = SA_QUOTA_HINT
            else:
                result["error_code"] = code
                result["error"] = msg
                result["hint"] = msg
            result["ok"] = False
            return result

        except Exception as e:
            code, msg = self._classify_exception(e)
            self._set_error(code, msg)
            result["error"] = msg
            result["error_code"] = code
            result["hint"] = msg
            return result

    def _probe_write(self, folder_id: str) -> Tuple[bool, Optional[Tuple[str, str]]]:
        """Create and trash a tiny probe file. Returns (ok, error_tuple)."""
        service = self._get_service()
        if not service:
            return False, (self._last_error_code or "not_configured", self._last_error or "")

        try:
            meta = {
                "name": ".shamrock_drive_write_probe",
                "parents": [folder_id],
                "mimeType": "text/plain",
            }
            media = None
            from googleapiclient.http import MediaIoBaseUpload

            media = MediaIoBaseUpload(
                io.BytesIO(b"probe"),
                mimetype="text/plain",
                resumable=False,
            )
            created = (
                service.files()
                .create(
                    body=meta,
                    media_body=media,
                    fields="id",
                    supportsAllDrives=True,
                )
                .execute()
            )
            fid = created.get("id")
            if fid:
                try:
                    service.files().update(
                        fileId=fid,
                        body={"trashed": True},
                        supportsAllDrives=True,
                    ).execute()
                except Exception:
                    pass
            return True, None
        except Exception as exc:
            return False, self._classify_exception(exc)

    # ── Folder / file ops ───────────────────────────────────────────────────

    def get_or_create_folder(self, folder_name: str, parent_id: str) -> Optional[str]:
        service = self._get_service()
        if not service:
            return None

        safe_name = (folder_name or "").replace("'", "\\'")
        query = (
            f"name='{safe_name}' and '{parent_id}' in parents "
            f"and mimeType='application/vnd.google-apps.folder' and trashed=false"
        )
        try:
            response = (
                service.files()
                .list(
                    q=query,
                    spaces="drive",
                    fields="files(id, name)",
                    pageSize=5,
                    supportsAllDrives=True,
                    includeItemsFromAllDrives=True,
                )
                .execute()
            )
            files = response.get("files", [])
            if files:
                self._clear_error()
                return files[0].get("id")

            file_metadata = {
                "name": folder_name,
                "mimeType": "application/vnd.google-apps.folder",
                "parents": [parent_id],
            }
            folder = (
                service.files()
                .create(body=file_metadata, fields="id", supportsAllDrives=True)
                .execute()
            )
            self._clear_error()
            return folder.get("id")
        except Exception as e:
            code, msg = self._classify_exception(e)
            # SA quota when creating folders under My Drive
            if code == "storage_quota_exceeded" and self._auth_mode == "service_account":
                if self._has_oauth() and self._switch_auth_mode("oauth"):
                    return self.get_or_create_folder(folder_name, parent_id)
            if code in ("invalid_scope", "invalid_grant", "access_denied", "storage_quota_exceeded"):
                self._set_error(code, msg)
                self._service = None
            else:
                self._set_error("folder_error", f"Failed to get/create folder {folder_name}: {e}")
            return None

    def upload_file(
        self,
        file_bytes: bytes,
        filename: str,
        folder_id: str,
        mimetype: str,
        *,
        _retried: bool = False,
    ) -> Optional[str]:
        from googleapiclient.http import MediaIoBaseUpload

        service = self._get_service()
        if not service:
            return None

        if not file_bytes:
            self._set_error("empty_file", "upload_file called with empty bytes")
            return None

        file_metadata = {"name": filename, "parents": [folder_id]}
        media = MediaIoBaseUpload(
            io.BytesIO(file_bytes),
            mimetype=mimetype,
            resumable=True,
        )

        try:
            file = (
                service.files()
                .create(
                    body=file_metadata,
                    media_body=media,
                    fields="id, webViewLink",
                    supportsAllDrives=True,
                )
                .execute()
            )
            self._clear_error()
            logger.info(
                "[Drive] Uploaded %s to %s (ID: %s, mode=%s)",
                filename,
                folder_id,
                file.get("id"),
                self._auth_mode,
            )
            return file.get("webViewLink")
        except Exception as e:
            code, msg = self._classify_exception(e)
            # Automatic SA → OAuth fallback for My Drive quota
            if (
                not _retried
                and code == "storage_quota_exceeded"
                and self._auth_mode == "service_account"
                and self._has_oauth()
            ):
                logger.warning(
                    "[Drive] SA upload hit storage quota — falling back to OAuth"
                )
                if self._switch_auth_mode("oauth"):
                    return self.upload_file(
                        file_bytes, filename, folder_id, mimetype, _retried=True
                    )
                # OAuth switch failed (likely invalid_scope)
                if self._oauth_error:
                    self._set_error(self._oauth_error[0], self._oauth_error[1])
                else:
                    self._set_error(code, msg)
                return None

            if code in (
                "invalid_scope",
                "invalid_grant",
                "access_denied",
                "storage_quota_exceeded",
            ):
                self._set_error(code, msg)
                self._service = None
            else:
                self._set_error("upload_failed", f"Failed to upload {filename}: {e}")
            return None

    def upload_pdf(self, pdf_bytes: bytes, filename: str, folder_id: str) -> Optional[str]:
        return self.upload_file(pdf_bytes, filename, folder_id, "application/pdf")

    def list_files_in_folder(self, folder_id: str, limit: int = 50) -> list:
        service = self._get_service()
        if not service:
            return []

        try:
            query = f"'{folder_id}' in parents and trashed=false"
            response = (
                service.files()
                .list(
                    q=query,
                    spaces="drive",
                    fields="files(id, name, webViewLink, createdTime, size)",
                    orderBy="createdTime desc",
                    pageSize=limit,
                    supportsAllDrives=True,
                    includeItemsFromAllDrives=True,
                )
                .execute()
            )
            self._clear_error()
            return response.get("files", [])
        except Exception as e:
            self._set_error("list_failed", f"Failed to list files in folder {folder_id}: {e}")
            return []

    def export_sheet_as_xlsx(self, spreadsheet_id: str) -> Optional[bytes]:
        service = self._get_service()
        if not service:
            return None

        try:
            from googleapiclient.http import MediaIoBaseDownload

            request = service.files().export_media(
                fileId=spreadsheet_id,
                mimeType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
            file_data = io.BytesIO()
            downloader = MediaIoBaseDownload(file_data, request)
            done = False
            while done is False:
                _status, done = downloader.next_chunk()

            self._clear_error()
            return file_data.getvalue()
        except Exception as e:
            self._set_error("export_failed", f"Failed to export sheet {spreadsheet_id}: {e}")
            return None

    def error_payload(self) -> Dict[str, Any]:
        """Structured error for API / E2E consumers."""
        return {
            "error": self._last_error or "unknown_drive_error",
            "error_code": self._last_error_code or "unknown",
            "auth_mode": self._auth_mode,
            "configured": self.is_configured,
            "oauth_error": self._oauth_error[0] if self._oauth_error else None,
            "sa_error": self._sa_error[0] if self._sa_error else None,
        }
