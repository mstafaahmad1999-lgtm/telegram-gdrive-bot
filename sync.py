"""Push history and user data to the remote dashboard after local changes.

Runs in a background thread so it never blocks the async bot loop.
Set DASHBOARD_URL and DASHBOARD_SYNC_TOKEN in .env to enable.
If either is missing, sync is silently skipped.
"""
import json
import logging
import os
import threading
import urllib.request

logger = logging.getLogger(__name__)

_DASHBOARD_URL = None
_SYNC_TOKEN = None


def _load_config() -> None:
    global _DASHBOARD_URL, _SYNC_TOKEN
    _DASHBOARD_URL = os.getenv("DASHBOARD_URL", "").rstrip("/")
    _SYNC_TOKEN = os.getenv("DASHBOARD_SYNC_TOKEN", "")


_load_config()


def _push(payload: dict) -> None:
    if not _DASHBOARD_URL or not _SYNC_TOKEN:
        return
    url = f"{_DASHBOARD_URL}/api/internal/push"
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-Sync-Token": _SYNC_TOKEN,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status != 200:
                logger.warning("Dashboard sync returned HTTP %s", resp.status)
    except Exception as exc:
        logger.warning("Dashboard sync failed: %s", exc)


def push_history(entry: dict) -> None:
    threading.Thread(target=_push, args=({"type": "history", "entry": entry},), daemon=True).start()


def push_users(users: list[int]) -> None:
    threading.Thread(target=_push, args=({"type": "users", "users": users},), daemon=True).start()
