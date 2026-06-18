"""Persist per-category folder preferences to suggest smart upload destinations."""
import json
import logging
import os

logger = logging.getLogger(__name__)

_PATH = os.path.join(os.path.dirname(__file__), "folder_prefs.json")


def _category(mime_type: str) -> str:
    if mime_type.startswith("video/"):
        return "video"
    if mime_type.startswith("image/"):
        return "image"
    if mime_type.startswith("audio/"):
        return "audio"
    return "document"


def _load() -> dict:
    try:
        with open(_PATH) as f:
            return json.load(f)
    except Exception:
        return {}


def _save(data: dict) -> None:
    try:
        with open(_PATH, "w") as f:
            json.dump(data, f)
    except Exception as exc:
        logger.warning("Could not save folder prefs: %s", exc)


def get_suggestion(mime_type: str) -> dict | None:
    """Return {'folder_id', 'folder_name', 'count'} for this mime category, or None."""
    return _load().get(_category(mime_type))


def record(mime_type: str, folder_id: str, folder_name: str) -> None:
    """Update the preferred folder for this mime category after a successful upload."""
    cat = _category(mime_type)
    data = _load()
    existing = data.get(cat, {})
    if existing.get("folder_id") == folder_id:
        count = existing.get("count", 0) + 1
    else:
        count = 1
    data[cat] = {"folder_id": folder_id, "folder_name": folder_name, "count": count}
    _save(data)
