import os
import logging
import io
from typing import Optional
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

logger = logging.getLogger(__name__)

class GoogleDriveService:
    def __init__(self):
        self._client_id = os.getenv("GOOGLE_CLIENT_ID", "")
        self._client_secret = os.getenv("GOOGLE_CLIENT_SECRET", "")
        self._refresh_token = os.getenv("GOOGLE_GMAIL_REFRESH_TOKEN", "") # Use the same refresh token, assuming it has drive scope
        self._service = None

    @property
    def is_configured(self) -> bool:
        return bool(self._client_id and self._client_secret and self._refresh_token)

    def _get_service(self):
        if self._service:
            return self._service

        if not self.is_configured:
            logger.warning("[Drive] OAuth not configured — operating in dry-run mode")
            return None

        try:
            creds = Credentials(
                token=None,
                refresh_token=self._refresh_token,
                token_uri="https://oauth2.googleapis.com/token",
                client_id=self._client_id,
                client_secret=self._client_secret,
                scopes=["https://www.googleapis.com/auth/drive"],
            )

            self._service = build("drive", "v3", credentials=creds)
            logger.info("[Drive] ✅ Google Drive API authenticated")
            return self._service

        except Exception as e:
            logger.error("[Drive] Authentication failed: %s", e)
            return None

    def get_or_create_folder(self, folder_name: str, parent_id: str) -> Optional[str]:
        service = self._get_service()
        if not service:
            return None
            
        # Search for folder
        query = f"name='{folder_name}' and '{parent_id}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false"
        response = service.files().list(
            q=query,
            spaces='drive',
            fields='files(id, name)'
        ).execute()
        
        files = response.get('files', [])
        if files:
            return files[0].get('id')
            
        # Create folder if it doesn't exist
        file_metadata = {
            'name': folder_name,
            'mimeType': 'application/vnd.google-apps.folder',
            'parents': [parent_id]
        }
        
        try:
            folder = service.files().create(body=file_metadata, fields='id').execute()
            return folder.get('id')
        except Exception as e:
            logger.error(f"[Drive] Failed to create folder {folder_name}: {e}")
            return None

    def upload_pdf(self, pdf_bytes: bytes, filename: str, folder_id: str) -> Optional[str]:
        service = self._get_service()
        if not service:
            return None
            
        file_metadata = {
            'name': filename,
            'parents': [folder_id]
        }
        
        media = MediaIoBaseUpload(
            io.BytesIO(pdf_bytes),
            mimetype='application/pdf',
            resumable=True
        )
        
        try:
            file = service.files().create(
                body=file_metadata,
                media_body=media,
                fields='id, webViewLink'
            ).execute()
            logger.info(f"[Drive] Uploaded {filename} to {folder_id} (ID: {file.get('id')})")
            return file.get('webViewLink')
        except Exception as e:
            logger.error(f"[Drive] Failed to upload {filename}: {e}")
            return None

