"""Local filesystem storage backend.

Drop-in replacement for the old Google Drive wrapper: the public function
names, signatures, and return-dict shapes are unchanged, so every caller
(dashboard.py and the bot handlers) keeps working without edits. Files are
stored on the phone's own storage instead of Google Drive.

IDs are URL-safe base64 of the path relative to ROOT, so they are opaque
strings, safe inside URL path segments, and round-trippable. The literal
string "root" maps to ROOT itself.
"""
import base64
import logging
import mimetypes
import os
import shutil
from datetime import datetime, timezone
from typing import Callable

logger = logging.getLogger(__name__)

PAGE_SIZE = 8


def _resolve_root() -> str:
    env = os.getenv("STORAGE_ROOT")
    if env:
        root = env
    elif os.path.isdir(os.path.expanduser("~/storage/shared")):
        # Termux shared storage (visible in the phone's file manager / gallery)
        root = os.path.expanduser("~/storage/shared/SOVAN_Archive")
    else:
        # PC / dev fallback
        root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "storage_data")
    os.makedirs(root, exist_ok=True)
    return root


ROOT = _resolve_root()

# Public base for click-through links shown in Telegram messages (optional).
PUBLIC_BASE = os.getenv("PUBLIC_DASHBOARD_URL", os.getenv("DASHBOARD_URL", "")).rstrip("/")


# ── id <-> path helpers ─────────────────────────────────────────────────────────

def _encode(rel: str) -> str:
    if not rel:
        return "root"
    return base64.urlsafe_b64encode(rel.encode()).decode().rstrip("=")


def _decode(fid: str) -> str:
    if not fid or fid == "root":
        return ""
    pad = "=" * (-len(fid) % 4)
    return base64.urlsafe_b64decode(fid + pad).decode()


def _abspath(fid: str) -> str:
    """Resolve an id to an absolute path, blocking traversal outside ROOT."""
    rel = _decode(fid)
    p = os.path.realpath(os.path.join(ROOT, rel))
    root = os.path.realpath(ROOT)
    if p != root and not p.startswith(root + os.sep):
        raise ValueError("path outside storage root")
    return p


def _id_of(abspath: str) -> str:
    rel = os.path.relpath(abspath, ROOT).replace(os.sep, "/")
    return _encode("" if rel == "." else rel)


def _link(fid: str) -> str:
    """webViewLink replacement — points at the dashboard download route."""
    return f"{PUBLIC_BASE}/api/browser/download/{fid}" if PUBLIC_BASE else f"/api/browser/download/{fid}"


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


def _safe_name(name: str) -> str:
    """Strip any path components from a user/Telegram supplied name."""
    return os.path.basename(name).replace("/", "_").replace("\\", "_") or "file"


def _unique_path(folder: str, name: str) -> str:
    """Return a non-colliding path inside folder, auto-suffixing '(n)' as needed."""
    candidate = os.path.join(folder, name)
    if not os.path.exists(candidate):
        return candidate
    stem, ext = os.path.splitext(name)
    i = 1
    while True:
        candidate = os.path.join(folder, f"{stem} ({i}){ext}")
        if not os.path.exists(candidate):
            return candidate
        i += 1


def _file_meta(abspath: str) -> dict:
    st = os.stat(abspath)
    fid = _id_of(abspath)
    name = os.path.basename(abspath)
    mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
    entry = {
        "id": fid,
        "name": name,
        "size": str(st.st_size),
        "mimeType": mime,
        "webViewLink": _link(fid),
        "webContentLink": _link(fid),
        "createdTime": _iso(st.st_mtime),
        "modifiedTime": _iso(st.st_mtime),
        "parents": [_id_of(os.path.dirname(abspath))],
    }
    if mime.startswith("image/"):
        entry["thumbnailLink"] = _link(fid)
    return entry


def _dir_size(path: str) -> int:
    total = 0
    for dirpath, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(dirpath, f))
            except OSError:
                pass
    return total


# ── listing ─────────────────────────────────────────────────────────────────────

def list_folders(parent_id: str = "root", page_token: str | None = None) -> tuple[list[dict], str | None]:
    base = _abspath(parent_id)
    dirs = []
    if os.path.isdir(base):
        with os.scandir(base) as it:
            for e in it:
                if e.is_dir():
                    dirs.append(e.path)
    dirs.sort(key=lambda p: os.path.basename(p).lower())

    offset = int(page_token) if page_token else 0
    page = dirs[offset:offset + PAGE_SIZE]
    next_token = str(offset + PAGE_SIZE) if offset + PAGE_SIZE < len(dirs) else None
    folders = [{"id": _id_of(p), "name": os.path.basename(p)} for p in page]
    return folders, next_token


def list_files(parent_id: str = "root", page_token: str | None = None) -> tuple[list[dict], str | None]:
    base = _abspath(parent_id)
    files = []
    if os.path.isdir(base):
        with os.scandir(base) as it:
            for e in it:
                if e.is_file():
                    files.append(e.path)
    files.sort(key=lambda p: os.path.getmtime(p), reverse=True)

    offset = int(page_token) if page_token else 0
    page = files[offset:offset + PAGE_SIZE]
    next_token = str(offset + PAGE_SIZE) if offset + PAGE_SIZE < len(files) else None
    out = []
    for p in page:
        m = _file_meta(p)
        out.append({
            "id": m["id"], "name": m["name"], "size": m["size"],
            "mimeType": m["mimeType"], "webViewLink": m["webViewLink"],
            "createdTime": m["createdTime"],
        })
    return out, next_token


def list_folder_contents(parent_id: str = "root") -> dict:
    base = _abspath(parent_id)
    folders, files = [], []
    if os.path.isdir(base):
        with os.scandir(base) as it:
            for e in it:
                if e.is_dir():
                    folders.append({"id": _id_of(e.path), "name": e.name})
                elif e.is_file():
                    files.append(_file_meta(e.path))
    folders.sort(key=lambda f: f["name"].lower())
    files.sort(key=lambda f: f["name"].lower())
    return {"folders": folders, "files": files}


def get_folder_name(folder_id: str) -> str:
    if folder_id == "root":
        return "SOVAN Archive"
    try:
        return os.path.basename(_abspath(folder_id)) or "SOVAN Archive"
    except Exception:
        return folder_id


def get_file_info(file_id: str) -> dict:
    p = _abspath(file_id)
    if not os.path.exists(p):
        raise FileNotFoundError(file_id)
    return _file_meta(p)


# ── mutations ─────────────────────────────────────────────────────────────────────

def create_folder(name: str, parent_id: str = "root") -> dict:
    parent = _abspath(parent_id)
    path = os.path.join(parent, _safe_name(name))
    os.makedirs(path, exist_ok=True)
    return {"id": _id_of(path), "name": os.path.basename(path)}


def delete_file(file_id: str) -> None:
    p = _abspath(file_id)
    if os.path.isdir(p):
        shutil.rmtree(p)
    elif os.path.exists(p):
        os.remove(p)


def rename_file(file_id: str, new_name: str) -> dict:
    p = _abspath(file_id)
    folder = os.path.dirname(p)
    target = _unique_path(folder, _safe_name(new_name))
    os.rename(p, target)
    fid = _id_of(target)
    return {"id": fid, "name": os.path.basename(target), "webViewLink": _link(fid)}


def move_file(file_id: str, new_parent_id: str, old_parent_id: str = "root") -> dict:
    p = _abspath(file_id)
    new_parent = _abspath(new_parent_id)
    os.makedirs(new_parent, exist_ok=True)
    target = _unique_path(new_parent, os.path.basename(p))
    shutil.move(p, target)
    fid = _id_of(target)
    return {
        "id": fid,
        "name": os.path.basename(target),
        "webViewLink": _link(fid),
        "parents": [_id_of(new_parent)],
    }


def check_duplicate(folder_id: str, file_name: str) -> bool:
    folder = _abspath(folder_id)
    return os.path.exists(os.path.join(folder, _safe_name(file_name)))


def search_files(query: str, max_results: int = 10) -> list[dict]:
    q = query.lower()
    results = []
    for dirpath, _dirs, files in os.walk(ROOT):
        for f in files:
            if q in f.lower():
                m = _file_meta(os.path.join(dirpath, f))
                results.append({
                    "id": m["id"], "name": m["name"], "size": m["size"],
                    "mimeType": m["mimeType"], "webViewLink": m["webViewLink"],
                    "parents": m["parents"], "createdTime": m["createdTime"],
                })
                if len(results) >= max_results:
                    return results
    return results


def get_storage_quota() -> dict:
    used = _dir_size(ROOT)
    try:
        free = shutil.disk_usage(ROOT).free
    except OSError:
        free = 0
    limit = used + free
    return {
        "limit": str(limit),
        "usage": str(used),
        "usageInDrive": str(used),
        "usageInGooglePhotos": "0",
    }


def upload_file(
    file_path: str,
    file_name: str,
    mime_type: str,
    folder_id: str,
    progress_callback: Callable[[int], None] | None = None,
) -> dict:
    folder = _abspath(folder_id)
    os.makedirs(folder, exist_ok=True)
    target = _unique_path(folder, _safe_name(file_name))
    shutil.copy2(file_path, target)

    if progress_callback:
        try:
            progress_callback(100)
        except Exception:
            pass

    st = os.stat(target)
    fid = _id_of(target)
    return {
        "id": fid,
        "name": os.path.basename(target),
        "size": str(st.st_size),
        "webViewLink": _link(fid),
    }
