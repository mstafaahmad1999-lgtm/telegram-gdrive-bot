"""Google Drive API wrapper: auth, folder listing, file upload, and management."""
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


def list_files(parent_id: str = "root", page_token: str | None = None) -> tuple[list[dict], str | None]:
    """List non-folder files in a folder."""
    service = get_drive_service()
    query = (
        f"mimeType!='application/vnd.google-apps.folder' "
        f"and trashed=false "
        f"and '{parent_id}' in parents"
    )
    params = {
        "q": query,
        "fields": "nextPageToken, files(id, name, size, mimeType, webViewLink, createdTime)",
        "pageSize": PAGE_SIZE,
        "orderBy": "createdTime desc",
    }
    if page_token:
        params["pageToken"] = page_token

    result = service.files().list(**params).execute()
    files = result.get("files", [])
    next_token = result.get("nextPageToken")
    return files, next_token


def get_folder_name(folder_id: str) -> str:
    if folder_id == "root":
        return "My Drive"
    try:
        service = get_drive_service()
        meta = service.files().get(fileId=folder_id, fields="name").execute()
        return meta.get("name", folder_id)
    except Exception:
        return folder_id


def create_folder(name: str, parent_id: str = "root") -> dict:
    """Create a new folder and return its metadata."""
    service = get_drive_service()
    body = {
        "name": name,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [parent_id],
    }
    folder = service.files().create(body=body, fields="id, name").execute()
    return folder


def delete_file(file_id: str) -> None:
    """Permanently delete a file from Drive."""
    service = get_drive_service()
    service.files().delete(fileId=file_id).execute()


def get_file_info(file_id: str) -> dict:
    """Return metadata for a single file."""
    return get_drive_service().files().get(
        fileId=file_id,
        fields="id, name, size, mimeType, webViewLink, parents, createdTime",
    ).execute()


def rename_file(file_id: str, new_name: str) -> dict:
    """Rename a file on Drive."""
    return get_drive_service().files().update(
        fileId=file_id,
        body={"name": new_name},
        fields="id, name, webViewLink",
    ).execute()


def move_file(file_id: str, new_parent_id: str, old_parent_id: str) -> dict:
    """Move a file to a different folder."""
    return get_drive_service().files().update(
        fileId=file_id,
        addParents=new_parent_id,
        removeParents=old_parent_id,
        fields="id, name, webViewLink, parents",
    ).execute()


def get_storage_quota() -> dict:
    """Return Drive storage quota info."""
    result = get_drive_service().about().get(fields="storageQuota").execute()
    return result.get("storageQuota", {})


def list_folder_contents(parent_id: str = "root") -> dict:
    """Return folders and files inside a folder for the browser."""
    service = get_drive_service()
    fq = f"mimeType='application/vnd.google-apps.folder' and trashed=false and '{parent_id}' in parents"
    folders = service.files().list(
        q=fq, fields="files(id,name)", orderBy="name", pageSize=100
    ).execute().get("files", [])
    fq2 = f"mimeType!='application/vnd.google-apps.folder' and trashed=false and '{parent_id}' in parents"
    files = service.files().list(
        q=fq2,
        fields="files(id,name,size,mimeType,webViewLink,webContentLink,thumbnailLink,modifiedTime)",
        orderBy="name", pageSize=100,
    ).execute().get("files", [])
    return {"folders": folders, "files": files}


def check_duplicate(folder_id: str, file_name: str) -> bool:
    """Return True if a file with this exact name exists in the folder."""
    safe = file_name.replace("'", "\\'")
    q = f"name='{safe}' and '{folder_id}' in parents and trashed=false"
    result = get_drive_service().files().list(q=q, fields="files(id)", pageSize=1).execute()
    return len(result.get("files", [])) > 0


def search_files(query: str, max_results: int = 10) -> list[dict]:
    """Search Drive for files whose name contains query."""
    safe = query.replace("'", "\\'")
    q = f"name contains '{safe}' and trashed=false and mimeType!='application/vnd.google-apps.folder'"
    result = get_drive_service().files().list(
        q=q,
        fields="files(id, name, size, mimeType, webViewLink, parents, createdTime)",
        pageSize=max_results,
        orderBy="createdTime desc",
    ).execute()
    return result.get("files", [])


def upload_file(
    file_path: str,
    file_name: str,
    mime_type: str,
    folder_id: str,
    progress_callback: Callable[[int], None] | None = None,
) -> dict:
    service = get_drive_service()
    body = {"name": file_name, "parents": [folder_id]}

    file_size = os.path.getsize(file_path)
    SIMPLE_UPLOAD_MAX = 50 * 1024 * 1024  # files at/under this → single fast upload

    # Small files: one-shot upload (much faster — no resumable session handshake)
    if file_size <= SIMPLE_UPLOAD_MAX:
        media = MediaFileUpload(file_path, mimetype=mime_type, resumable=False)
        response = service.files().create(
            body=body,
            media_body=media,
            fields="id, name, size, webViewLink",
        ).execute()
        if progress_callback:
            try:
                progress_callback(100)
            except Exception:
                pass
        return response

    # Large files: resumable with bigger chunks (fewer round-trips)
    media = MediaFileUpload(
        file_path,
        mimetype=mime_type,
        resumable=True,
        chunksize=16 * 1024 * 1024,
    )
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
                    pass

    return response
