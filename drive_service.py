"""Google Drive API wrapper: auth, folder listing, and file upload."""
import logging
import os
from typing import Callable

from google.auth.exceptions import RefreshError, TransportError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/drive"]
PAGE_SIZE = 8
_service_cache = None


def get_drive_service():
    """Return an authenticated Drive service, refreshing token if needed."""
    global _service_cache

    token_path = os.getenv("GOOGLE_TOKEN_PATH", "token.json")
    credentials_path = os.getenv("GOOGLE_CREDENTIALS_PATH", "credentials.json")

    creds = None
    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                _save_token(creds, token_path)
            except (RefreshError, TransportError) as exc:
                logger.error("Drive token refresh failed: %s", exc)
                raise
        else:
            # No valid creds — user must run setup_google_auth.py
            raise FileNotFoundError(
                f"No valid Drive credentials found at '{token_path}'. "
                "Run setup_google_auth.py to authenticate."
            )

    _service_cache = build("drive", "v3", credentials=creds)
    return _service_cache


def _save_token(creds: Credentials, path: str) -> None:
    with open(path, "w") as fh:
        fh.write(creds.to_json())


def list_folders(parent_id: str = "root", page_token: str | None = None) -> tuple[list[dict], str | None]:
    """Return up to PAGE_SIZE folders in parent_id, plus next_page_token if more exist."""
    service = get_drive_service()
    query = (
        f"mimeType='application/vnd.google-apps.folder' "
        f"and trashed=false "
        f"and '{parent_id}' in parents"
    )
    params = {
        "q": query,
        "fields": "nextPageToken, files(id, name)",
        "pageSize": PAGE_SIZE,
        "orderBy": "name",
    }
    if page_token:
        params["pageToken"] = page_token

    result = service.files().list(**params).execute()
    folders = result.get("files", [])
    next_token = result.get("nextPageToken")
    return folders, next_token


def get_folder_name(folder_id: str) -> str:
    """Return the display name of a folder (falls back to ID on error)."""
    if folder_id == "root":
        return "My Drive"
    try:
        service = get_drive_service()
        meta = service.files().get(fileId=folder_id, fields="name").execute()
        return meta.get("name", folder_id)
    except Exception:
        return folder_id


def upload_file(
    file_path: str,
    file_name: str,
    mime_type: str,
    folder_id: str,
    progress_callback: Callable[[int], None] | None = None,
) -> dict:
    """Upload file to Drive folder. Returns file resource dict with webViewLink."""
    service = get_drive_service()
    media = MediaFileUpload(
        file_path,
        mimetype=mime_type,
        resumable=True,
        chunksize=5 * 1024 * 1024,  # 5 MB chunks
    )
    body = {"name": file_name, "parents": [folder_id]}
    request = service.files().create(
        body=body,
        media_body=media,
        fields="id, name, size, webViewLink",
    )

    response = None
    last_pct = -1
    while response is None:
        status, response = request.next_chunk()
        if status and progress_callback:
            pct = int(status.progress() * 100)
            if pct != last_pct:
                last_pct = pct
                try:
                    progress_callback(pct)
                except Exception:
                    pass  # progress reporting is best-effort

    return response
