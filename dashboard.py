"""Web dashboard for the Telegram → Google Drive bot.

On VPS: served by gunicorn behind nginx at dashboard.sovan.info
Locally: python dashboard.py → http://localhost:5000

The Android bot pushes data here via POST /api/internal/push
protected by the shared DASHBOARD_SYNC_TOKEN.
"""
import json
import os
import tempfile
from datetime import datetime
from functools import wraps

from dotenv import load_dotenv
from flask import Flask, jsonify, redirect, render_template, request, session, url_for

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("DASHBOARD_SECRET_KEY", os.urandom(24).hex())

DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "admin")
DASHBOARD_SYNC_TOKEN = os.getenv("DASHBOARD_SYNC_TOKEN", "")
HISTORY_FILE = "history.json"
USERS_FILE = os.getenv("USERS_FILE", "users.json")
MAX_HISTORY = 200

AUTHORIZED_USER_IDS_STR = os.getenv("AUTHORIZED_USER_IDS", os.getenv("AUTHORIZED_USER_ID", ""))
OWNER_ID = int(AUTHORIZED_USER_IDS_STR.split(",")[0].strip()) if AUTHORIZED_USER_IDS_STR else 0


# ── helpers ──────────────────────────────────────────────────────────────────

def _load_history() -> list[dict]:
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return []


def _load_users() -> list[int]:
    if os.path.exists(USERS_FILE):
        try:
            with open(USERS_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    raw = AUTHORIZED_USER_IDS_STR
    if raw:
        try:
            return [int(x.strip()) for x in raw.split(",") if x.strip()]
        except ValueError:
            pass
    return []


def _save_users(users: list[int]) -> None:
    with open(USERS_FILE, "w") as f:
        json.dump(users, f)


def _format_size(size_bytes: int) -> str:
    if size_bytes >= 1024 ** 3:
        return f"{size_bytes / 1024 ** 3:.2f} GB"
    if size_bytes >= 1024 ** 2:
        return f"{size_bytes / 1024 ** 2:.1f} MB"
    if size_bytes >= 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes} B"


def _compute_stats(history: list[dict]) -> dict:
    total_files = len(history)
    total_bytes = sum(e.get("file_size", 0) for e in history)
    uploaders: dict[int, int] = {}
    for e in history:
        uid = e.get("user_id", 0)
        uploaders[uid] = uploaders.get(uid, 0) + 1
    top_uploader = max(uploaders, key=uploaders.get) if uploaders else None
    top_count = uploaders[top_uploader] if top_uploader else 0

    # uploads per day (last 7 days)
    daily: dict[str, int] = {}
    for e in history:
        ts = e.get("timestamp", "")
        try:
            day = datetime.fromisoformat(ts).strftime("%b %d")
        except Exception:
            day = "?"
        daily[day] = daily.get(day, 0) + 1

    return {
        "total_files": total_files,
        "total_size": _format_size(total_bytes),
        "num_uploaders": len(uploaders),
        "top_uploader": top_uploader,
        "top_count": top_count,
        "daily": daily,
    }


# ── auth ──────────────────────────────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        if request.form.get("password") == DASHBOARD_PASSWORD:
            session["logged_in"] = True
            return redirect(url_for("index"))
        error = "Wrong password."
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ── pages ─────────────────────────────────────────────────────────────────────

@app.route("/")
@login_required
def index():
    history = _load_history()
    users = _load_users()
    stats = _compute_stats(history)
    recent = list(reversed(history[-20:]))

    for e in recent:
        ts = e.get("timestamp", "")
        try:
            e["_dt"] = datetime.fromisoformat(ts).strftime("%b %d, %H:%M")
        except Exception:
            e["_dt"] = ts[:10]
        e["_size"] = _format_size(e.get("file_size", 0))
        e["_owner"] = e.get("user_id") == OWNER_ID

    return render_template(
        "index.html",
        stats=stats,
        recent=recent,
        users=users,
        owner_id=OWNER_ID,
    )


# ── API ───────────────────────────────────────────────────────────────────────

@app.route("/api/users/add", methods=["POST"])
@login_required
def api_add_user():
    data = request.get_json(force=True)
    try:
        uid = int(data.get("user_id", ""))
    except (ValueError, TypeError):
        return jsonify({"ok": False, "error": "Invalid user ID"}), 400

    users = _load_users()
    if uid in users:
        return jsonify({"ok": False, "error": "Already authorized"})
    users.append(uid)
    _save_users(users)
    return jsonify({"ok": True})


@app.route("/api/users/remove", methods=["POST"])
@login_required
def api_remove_user():
    data = request.get_json(force=True)
    try:
        uid = int(data.get("user_id", ""))
    except (ValueError, TypeError):
        return jsonify({"ok": False, "error": "Invalid user ID"}), 400

    if uid == OWNER_ID:
        return jsonify({"ok": False, "error": "Cannot remove owner"}), 400

    users = _load_users()
    if uid not in users:
        return jsonify({"ok": False, "error": "User not found"})
    users.remove(uid)
    _save_users(users)
    return jsonify({"ok": True})


@app.route("/api/stats")
@login_required
def api_stats():
    history = _load_history()
    return jsonify(_compute_stats(history))


# ── file delete ───────────────────────────────────────────────────────────────

@app.route("/api/files/delete", methods=["POST"])
@login_required
def api_delete_file():
    data = request.get_json(force=True)
    file_id = data.get("file_id", "").strip()
    if not file_id:
        return jsonify({"ok": False, "error": "Missing file_id"}), 400

    # Delete from Google Drive
    try:
        import drive_service
        drive_service.delete_file(file_id)
    except Exception as exc:
        return jsonify({"ok": False, "error": f"Drive error: {exc}"}), 500

    # Remove from local history.json
    history = _load_history()
    history = [e for e in history if e.get("file_id") != file_id]
    with open(HISTORY_FILE, "w") as f:
        json.dump(history, f)

    return jsonify({"ok": True})


# ── file upload from dashboard ───────────────────────────────────────────────

@app.route("/api/folders")
@login_required
def api_folders():
    """Return top-level Drive folders for the upload folder picker."""
    try:
        import drive_service
        folders, _ = drive_service.list_folders("root")
        return jsonify({"ok": True, "folders": [{"id": f["id"], "name": f["name"]} for f in folders]})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/files/upload", methods=["POST"])
@login_required
def api_upload_file():
    """Upload a file from the browser directly to Google Drive."""
    if "file" not in request.files:
        return jsonify({"ok": False, "error": "No file provided"}), 400

    f = request.files["file"]
    folder_id = request.form.get("folder_id", "root")

    if not f.filename:
        return jsonify({"ok": False, "error": "Empty filename"}), 400

    suffix = os.path.splitext(f.filename)[1] or ""
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=suffix)
    os.close(tmp_fd)
    try:
        f.save(tmp_path)
        file_size = os.path.getsize(tmp_path)

        import drive_service
        resource = drive_service.upload_file(
            tmp_path, f.filename, f.content_type or "application/octet-stream", folder_id
        )
        folder_name = drive_service.get_folder_name(folder_id)

        entry = {
            "user_id": OWNER_ID,
            "file_name": resource.get("name", f.filename),
            "file_size": int(resource.get("size", file_size) or file_size),
            "folder_name": folder_name,
            "web_link": resource.get("webViewLink", ""),
            "file_id": resource.get("id", ""),
            "timestamp": datetime.utcnow().isoformat(),
        }
        history = _load_history()
        history.append(entry)
        with open(HISTORY_FILE, "w") as fh:
            json.dump(history[-MAX_HISTORY:], fh)

        return jsonify({"ok": True, "entry": entry})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


# ── internal push (called by Android bot) ────────────────────────────────────

@app.route("/api/internal/push", methods=["POST"])
def api_internal_push():
    """Receive data pushed from the Android bot. Protected by shared token."""
    if not DASHBOARD_SYNC_TOKEN:
        return jsonify({"ok": False, "error": "Sync token not configured"}), 500

    token = request.headers.get("X-Sync-Token", "")
    if token != DASHBOARD_SYNC_TOKEN:
        return jsonify({"ok": False, "error": "Forbidden"}), 403

    data = request.get_json(force=True, silent=True) or {}
    kind = data.get("type")

    if kind == "history":
        entry = data.get("entry")
        if not entry or not isinstance(entry, dict):
            return jsonify({"ok": False, "error": "Missing entry"}), 400
        history = _load_history()
        history.append(entry)
        with open(HISTORY_FILE, "w") as f:
            json.dump(history[-MAX_HISTORY:], f)
        return jsonify({"ok": True})

    if kind == "users":
        users = data.get("users")
        if not isinstance(users, list):
            return jsonify({"ok": False, "error": "Missing users list"}), 400
        _save_users([int(u) for u in users])
        return jsonify({"ok": True})

    return jsonify({"ok": False, "error": f"Unknown type: {kind}"}), 400


# ── run ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.getenv("DASHBOARD_PORT", 5000))
    # host 0.0.0.0 so it's reachable from other devices on the same Wi-Fi
    app.run(host="0.0.0.0", port=port, debug=False)
