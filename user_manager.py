"""Persistent authorized user store backed by users.json."""
import json
import logging
import os
import sync

logger = logging.getLogger(__name__)

_USERS_FILE = os.getenv("USERS_FILE", "users.json")
_authorized_ids: set[int] = set()


def load(initial_ids: set[int]) -> None:
    """Load users from users.json, seeding with initial_ids if file doesn't exist."""
    global _authorized_ids
    if os.path.exists(_USERS_FILE):
        try:
            with open(_USERS_FILE) as f:
                _authorized_ids = set(json.load(f))
            logger.info("Loaded %d authorized users from %s", len(_authorized_ids), _USERS_FILE)
            return
        except Exception as exc:
            logger.warning("Could not read %s: %s — using env IDs", _USERS_FILE, exc)
    _authorized_ids = set(initial_ids)
    _save()


def _save() -> None:
    with open(_USERS_FILE, "w") as f:
        json.dump(list(_authorized_ids), f)


def get_all() -> set[int]:
    return set(_authorized_ids)


def add(user_id: int) -> bool:
    """Return True if newly added, False if already existed."""
    if user_id in _authorized_ids:
        return False
    _authorized_ids.add(user_id)
    _save()
    sync.push_users(list(_authorized_ids))
    return True


def remove(user_id: int) -> bool:
    """Return True if removed, False if wasn't present."""
    if user_id not in _authorized_ids:
        return False
    _authorized_ids.discard(user_id)
    _save()
    sync.push_users(list(_authorized_ids))
    return True


def is_authorized(user_id: int) -> bool:
    return user_id in _authorized_ids
