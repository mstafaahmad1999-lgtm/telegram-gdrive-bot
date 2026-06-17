"""In-memory store for pending uploads awaiting folder selection."""
from dataclasses import dataclass

@dataclass
class PendingUpload:
    file_path: str
    file_name: str
    mime_type: str
    file_size: int  # bytes

_pending: dict[int, PendingUpload] = {}


def set_pending(user_id: int, file_path: str, file_name: str, mime_type: str, file_size: int) -> None:
    _pending[user_id] = PendingUpload(file_path, file_name, mime_type, file_size)


def get_pending(user_id: int) -> PendingUpload | None:
    return _pending.get(user_id)


def clear_pending(user_id: int) -> None:
    _pending.pop(user_id, None)
