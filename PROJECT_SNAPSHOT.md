# PROJECT SNAPSHOT — Telegram → Google Drive Upload Bot
> Generated: 2026-06-18
> Share this file with any AI assistant to resume work with full context.

---

## 1. PROJECT OVERVIEW

A single-user (with multi-user support) Telegram bot that lets authorized users upload files, photos, and videos directly to Google Drive without opening the Drive app. The bot runs 24/7 on an old Android phone using Termux.

**Bot name:** SOVAN UPLOADER
**Owner Telegram ID:** 733991030
**Owner email:** mstafa.ahmad.1999@gmail.com
**GitHub repo:** https://github.com/mstafaahmad1999-lgtm/telegram-gdrive-bot (public)
**Running on:** Old Android phone via Termux
**Start command:** `nohup ./start.sh &` (inside `~/telegram-gdrive-bot/`)

---

## 2. TECH STACK

- Python 3.13 (Termux default)
- python-telegram-bot 21.10 (async)
- google-api-python-client 2.167.0
- google-auth-oauthlib 1.2.1
- google-auth 2.40.0
- python-dotenv 1.1.0
- Running on Android Termux (no Docker)

---

## 3. PROJECT STRUCTURE

```
telegram-gdrive-bot/
├── bot.py                   # Entrypoint, registers all handlers
├── drive_service.py         # Google Drive API wrapper
├── state.py                 # Pending upload + album buffer state
├── history.py               # Upload history (history.json)
├── user_manager.py          # Authorized users store (users.json)
├── setup_google_auth.py     # One-time OAuth script (run on PC)
├── start.sh                 # Auto-restart wrapper script
├── handlers/
│   ├── __init__.py
│   ├── commands.py          # /start /cancel /whoami /recent /stats /adduser /removeuser /listusers
│   ├── files.py             # File/photo/video/audio handler + album grouping
│   └── navigation.py        # Folder picker, upload, list files, delete, new folder
├── requirements.txt
├── .env                     # Secrets (NOT in git)
├── .env.example             # Template
├── .gitignore
├── credentials.json         # Google OAuth client (NOT in git)
├── token.json               # Google OAuth token (NOT in git)
├── users.json               # Authorized user IDs (auto-generated)
├── history.json             # Upload history (auto-generated)
├── tmp/                     # Temp downloaded files
├── nohup.out                # Bot logs
└── README.md
```

---

## 4. ENVIRONMENT VARIABLES (.env)

```
TELEGRAM_BOT_TOKEN=<bot token from BotFather>
AUTHORIZED_USER_IDS=733991030
GOOGLE_CREDENTIALS_PATH=credentials.json
GOOGLE_TOKEN_PATH=token.json
```

---

## 5. FULL SOURCE CODE

### bot.py
```python
"""Entrypoint: builds the Telegram Application and registers all handlers."""
import asyncio
import logging
import os

from dotenv import load_dotenv
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from handlers.commands import (
    adduser_handler,
    cancel_handler,
    listusers_handler,
    recent_handler,
    removeuser_handler,
    start_handler,
    stats_handler,
    whoami_handler,
)
from handlers.files import file_handler, new_folder_name_handler
from handlers.navigation import callback_handler
import history
import user_manager

load_dotenv()

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set in .env")

    authorized_user_ids_str = os.getenv("AUTHORIZED_USER_IDS", os.getenv("AUTHORIZED_USER_ID", ""))
    if not authorized_user_ids_str:
        raise RuntimeError("AUTHORIZED_USER_IDS is not set in .env")

    try:
        initial_ids = {int(uid.strip()) for uid in authorized_user_ids_str.split(",") if uid.strip()}
    except ValueError:
        raise RuntimeError("AUTHORIZED_USER_IDS must be comma-separated numeric Telegram user IDs")

    user_manager.load(initial_ids)
    history.load()

    owner_id = int(authorized_user_ids_str.split(",")[0].strip())

    app = ApplicationBuilder().token(token).build()

    app.bot_data["authorized_user_ids"] = user_manager.get_all()
    app.bot_data["owner_id"] = owner_id

    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CommandHandler("cancel", cancel_handler))
    app.add_handler(CommandHandler("whoami", whoami_handler))
    app.add_handler(CommandHandler("recent", recent_handler))
    app.add_handler(CommandHandler("stats", stats_handler))
    app.add_handler(CommandHandler("adduser", adduser_handler))
    app.add_handler(CommandHandler("removeuser", removeuser_handler))
    app.add_handler(CommandHandler("listusers", listusers_handler))

    file_filter = (
        filters.Document.ALL
        | filters.VIDEO
        | filters.PHOTO
        | filters.AUDIO
    )
    app.add_handler(MessageHandler(file_filter, file_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, new_folder_name_handler))
    app.add_handler(CallbackQueryHandler(callback_handler))

    logger.info("Bot starting. Owner: %d | Users: %s", owner_id, user_manager.get_all())
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
```

### state.py
```python
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

def set_pending(user_id, file_path, file_name, mime_type, file_size):
    _pending[user_id] = PendingUpload(file_path, file_name, mime_type, file_size)

def get_pending(user_id):
    return _pending.get(user_id)

def clear_pending(user_id):
    _pending.pop(user_id, None)

def get_album(user_id):
    return _albums.get(user_id)

def set_album(user_id, album):
    _albums[user_id] = album

def clear_album(user_id):
    _albums.pop(user_id, None)
```

### user_manager.py
```python
"""Persistent authorized user store backed by users.json."""
import json, logging, os

logger = logging.getLogger(__name__)
_USERS_FILE = os.getenv("USERS_FILE", "users.json")
_authorized_ids: set[int] = set()

def load(initial_ids):
    global _authorized_ids
    if os.path.exists(_USERS_FILE):
        try:
            with open(_USERS_FILE) as f:
                _authorized_ids = set(json.load(f))
            return
        except Exception as exc:
            logger.warning("Could not read %s: %s", _USERS_FILE, exc)
    _authorized_ids = set(initial_ids)
    _save()

def _save():
    with open(_USERS_FILE, "w") as f:
        json.dump(list(_authorized_ids), f)

def get_all(): return set(_authorized_ids)
def add(user_id):
    if user_id in _authorized_ids: return False
    _authorized_ids.add(user_id); _save(); return True
def remove(user_id):
    if user_id not in _authorized_ids: return False
    _authorized_ids.discard(user_id); _save(); return True
def is_authorized(user_id): return user_id in _authorized_ids
```

### history.py
```python
"""Upload history store backed by history.json."""
import json, logging, os
from datetime import datetime

logger = logging.getLogger(__name__)
_HISTORY_FILE = "history.json"
_MAX_ENTRIES = 100
_history: list[dict] = []

def load():
    global _history
    if os.path.exists(_HISTORY_FILE):
        try:
            with open(_HISTORY_FILE) as f:
                _history = json.load(f)
        except Exception as exc:
            logger.warning("Could not read history.json: %s", exc)

def _save():
    with open(_HISTORY_FILE, "w") as f:
        json.dump(_history[-_MAX_ENTRIES:], f)

def add(user_id, file_name, file_size, folder_name, web_link):
    _history.append({
        "user_id": user_id, "file_name": file_name, "file_size": file_size,
        "folder_name": folder_name, "web_link": web_link,
        "timestamp": datetime.utcnow().isoformat(),
    })
    _save()

def get_recent(n=5): return list(reversed(_history[-n:]))

def get_stats():
    total_files = len(_history)
    total_size = sum(e.get("file_size", 0) for e in _history)
    uploaders = {}
    for e in _history:
        uid = e.get("user_id", 0)
        uploaders[uid] = uploaders.get(uid, 0) + 1
    top = max(uploaders, key=uploaders.get) if uploaders else None
    return {"total_files": total_files, "total_size": total_size, "uploaders": uploaders, "top_uploader": top}
```

### drive_service.py
```python
"""Google Drive API wrapper."""
import logging, os
from google.auth.exceptions import RefreshError, TransportError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

logger = logging.getLogger(__name__)
SCOPES = ["https://www.googleapis.com/auth/drive"]
PAGE_SIZE = 8

def get_drive_service():
    token_path = os.getenv("GOOGLE_TOKEN_PATH", "token.json")
    credentials_path = os.getenv("GOOGLE_CREDENTIALS_PATH", "credentials.json")
    creds = None
    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            with open(token_path, "w") as f: f.write(creds.to_json())
        else:
            raise FileNotFoundError("No valid Drive credentials. Run setup_google_auth.py")
    return build("drive", "v3", credentials=creds)

def list_folders(parent_id="root", page_token=None):
    service = get_drive_service()
    q = f"mimeType='application/vnd.google-apps.folder' and trashed=false and '{parent_id}' in parents"
    params = {"q": q, "fields": "nextPageToken, files(id, name)", "pageSize": PAGE_SIZE, "orderBy": "name"}
    if page_token: params["pageToken"] = page_token
    result = service.files().list(**params).execute()
    return result.get("files", []), result.get("nextPageToken")

def list_files(parent_id="root", page_token=None):
    service = get_drive_service()
    q = f"mimeType!='application/vnd.google-apps.folder' and trashed=false and '{parent_id}' in parents"
    params = {"q": q, "fields": "nextPageToken, files(id, name, size, mimeType, webViewLink, createdTime)", "pageSize": PAGE_SIZE, "orderBy": "createdTime desc"}
    if page_token: params["pageToken"] = page_token
    result = service.files().list(**params).execute()
    return result.get("files", []), result.get("nextPageToken")

def get_folder_name(folder_id):
    if folder_id == "root": return "My Drive"
    try:
        return get_drive_service().files().get(fileId=folder_id, fields="name").execute().get("name", folder_id)
    except: return folder_id

def create_folder(name, parent_id="root"):
    return get_drive_service().files().create(
        body={"name": name, "mimeType": "application/vnd.google-apps.folder", "parents": [parent_id]},
        fields="id, name"
    ).execute()

def delete_file(file_id):
    get_drive_service().files().delete(fileId=file_id).execute()

def upload_file(file_path, file_name, mime_type, folder_id, progress_callback=None):
    service = get_drive_service()
    media = MediaFileUpload(file_path, mimetype=mime_type, resumable=True, chunksize=5*1024*1024)
    request = service.files().create(
        body={"name": file_name, "parents": [folder_id]},
        media_body=media, fields="id, name, size, webViewLink"
    )
    response = None
    last_pct = -1
    while response is None:
        status, response = request.next_chunk()
        if status and progress_callback:
            pct = int(status.progress() * 100)
            if pct != last_pct: last_pct = pct; progress_callback(pct)
    return response
```

### start.sh
```sh
#!/data/data/com.termux/files/usr/bin/sh
# Auto-restart bot if it crashes
cd /data/data/com.termux/files/home/telegram-gdrive-bot
while true; do
    echo "$(date): Starting bot..."
    python bot.py >> nohup.out 2>&1
    echo "$(date): Bot stopped. Restarting in 5 seconds..."
    sleep 5
done
```

---

## 6. FEATURES

| Feature | Status | How |
|---------|--------|-----|
| File upload to Drive | ✅ | Send any file to bot |
| Folder navigation | ✅ | Inline keyboard |
| Subfolder browsing | ✅ | Tap folder to go in |
| Back navigation | ✅ | ⬅️ Back button |
| Pagination | ✅ | ◀️ Prev / Next ▶️ |
| Progress bar | ✅ | ⬛⬛⬛⬜⬜⬜ 30% |
| Album support | ✅ | Send multiple files quickly |
| Create new folder | ✅ | ➕ New folder (owner only) |
| List files in folder | ✅ | 📋 List files button |
| Delete files | ✅ | 🗑 Delete button (owner only) |
| Recent uploads | ✅ | /recent |
| Upload stats | ✅ | /stats |
| Friend notifications | ✅ | Auto DM to owner |
| Add users | ✅ | /adduser ID |
| Remove users | ✅ | /removeuser ID |
| List users | ✅ | /listusers |
| Auto-restart | ✅ | start.sh loop |
| Auto-start on reboot | ✅ | Termux:Boot |
| 20MB file limit warning | ✅ | Clear error message |

---

## 7. BOT COMMANDS

| Command | Who | Description |
|---------|-----|-------------|
| /start | All | Welcome message |
| /cancel | All | Cancel pending upload |
| /whoami | All | Show Telegram user ID |
| /recent | All | Last 5 uploads with links |
| /stats | All | Upload statistics |
| /adduser ID | Owner | Add authorized user |
| /removeuser ID | Owner | Remove authorized user |
| /listusers | Owner | List all authorized users |

---

## 8. DEPLOYMENT (Android Termux)

### First time setup:
```sh
pkg update -y && pkg upgrade -y
pkg install python git -y
git clone https://github.com/mstafaahmad1999-lgtm/telegram-gdrive-bot.git
cd telegram-gdrive-bot
pip install -r requirements.txt
# Copy credentials.json and token.json manually
# Create .env file
nano .env
# Start bot
chmod +x start.sh
nohup ./start.sh &
```

### Daily management:
```sh
# Check logs
cat ~/telegram-gdrive-bot/nohup.out | tail -20

# Restart bot
pkill -f bot.py && cd ~/telegram-gdrive-bot && nohup ./start.sh &

# Update from GitHub
cd ~/telegram-gdrive-bot && git pull && pkill -f bot.py
# bot auto-restarts via start.sh

# Add friend (in Telegram)
/adduser 123456789
```

### Auto-start on reboot:
- Termux:Boot installed and opened once
- Script at: `~/.termux/boot/start-bot.sh`
- Termux battery optimization: Unrestricted

---

## 9. KNOWN ISSUES & NOTES

- Python 3.14 on Windows requires manual event loop creation (`asyncio.new_event_loop()`) — already fixed in bot.py
- Telegram bot API 20MB file download limit — bot warns user clearly
- Google OAuth token stored in `token.json` — auto-refreshes, re-run `setup_google_auth.py` on PC if it expires
- `users.json` persists across restarts — authorized users survive bot restarts
- `history.json` stores last 100 uploads
- Two bot instances (PC + Android) cause Conflict error — always stop PC bot before running on Android
- Android may kill Termux — set battery to Unrestricted, use Termux:Boot

---

## 10. FUTURE IDEAS (not yet built)

- Rename files before upload
- Search files in Drive from bot
- Share Drive links directly
- Scheduled uploads
- Web dashboard to manage users
- Support files > 20MB via self-hosted Bot API server
