"""In-memory store for pending uploads and album grouping."""
import asyncio
from dataclasses import dataclass, field

@dataclass
class PendingUpload:
    file_path: str
    file_name: str
    mime_type: str
    file_size: int

@dataclass
class AlbumBuffer:
    files: list[PendingUpload] = field(default_factory=list)
    timer_task: asyncio.Task | None = None

_pending: dict[int, PendingUpload] = {}
_albums: dict[int, AlbumBuffer] = {}


def set_pending(user_id: int, file_path: str, file_name: str, mime_type: str, file_size: int) -> None:
    _pending[user_id] = PendingUpload(file_path, file_name, mime_type, file_size)


def get_pending(user_id: int) -> PendingUpload | None:
    return _pending.get(user_id)


def clear_pending(user_id: int) -> None:
    _pending.pop(user_id, None)


# Album support
def get_album(user_id: int) -> AlbumBuffer | None:
    return _albums.get(user_id)


def set_album(user_id: int, album: AlbumBuffer) -> None:
    _albums[user_id] = album


def clear_album(user_id: int) -> None:
    _albums.pop(user_id, None)


# File action state (rename / move)
_file_actions: dict[int, dict] = {}


def set_file_action(user_id: int, action_data: dict) -> None:
    _file_actions[user_id] = action_data


def get_file_action(user_id: int) -> dict | None:
    return _file_actions.get(user_id)


def clear_file_action(user_id: int) -> None:
    _file_actions.pop(user_id, None)
