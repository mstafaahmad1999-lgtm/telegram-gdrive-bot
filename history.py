"""Upload history store backed by history.json."""
import json
import logging
import os
from datetime import datetime

logger = logging.getLogger(__name__)

_HISTORY_FILE = "history.json"
_MAX_ENTRIES = 100
_history: list[dict] = []


def load() -> None:
    global _history
    if os.path.exists(_HISTORY_FILE):
        try:
            with open(_HISTORY_FILE) as f:
                _history = json.load(f)
        except Exception as exc:
            logger.warning("Could not read history.json: %s", exc)
            _history = []


def _save() -> None:
    with open(_HISTORY_FILE, "w") as f:
        json.dump(_history[-_MAX_ENTRIES:], f)


def add(user_id: int, file_name: str, file_size: int, folder_name: str, web_link: str) -> None:
    _history.append({
        "user_id": user_id,
        "file_name": file_name,
        "file_size": file_size,
        "folder_name": folder_name,
        "web_link": web_link,
        "timestamp": datetime.utcnow().isoformat(),
    })
    _save()


def get_recent(n: int = 5) -> list[dict]:
    return list(reversed(_history[-n:]))


def get_stats() -> dict:
    total_files = len(_history)
    total_size = sum(e.get("file_size", 0) for e in _history)
    uploaders: dict[int, int] = {}
    for e in _history:
        uid = e.get("user_id", 0)
        uploaders[uid] = uploaders.get(uid, 0) + 1
    top_uploader = max(uploaders, key=uploaders.get) if uploaders else None
    return {
        "total_files": total_files,
        "total_size": total_size,
        "uploaders": uploaders,
        "top_uploader": top_uploader,
    }
