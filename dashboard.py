"""Web dashboard for the Telegram → Google Drive bot.

On VPS: served by gunicorn behind nginx at dashboard.sovan.info
Locally: python dashboard.py → http://localhost:5000

The Android bot pushes data here via POST /api/internal/push
protected by the shared DASHBOARD_SYNC_TOKEN.
"""
import json
import mimetypes
import os
import tempfile
import uuid
from datetime import datetime, timedelta
from functools import wraps

from dotenv import load_dotenv
from flask import Flask, jsonify, redirect, render_template, request, session, url_for, Response, send_file
from werkzeug.security import generate_password_hash, check_password_hash

load_dotenv()

app = Flask(__name__)

# Persist secret key so sessions survive server restarts
_KEY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".secret_key")

def _get_secret_key() -> str:
    key = os.getenv("DASHBOARD_SECRET_KEY")
    if key:
        return key
    if os.path.exists(_KEY_FILE):
        with open(_KEY_FILE) as f:
            k = f.read().strip()
            if k:
                return k
    key = os.urandom(32).hex()
    with open(_KEY_FILE, "w") as f:
        f.write(key)
    return key

app.secret_key = _get_secret_key()
app.permanent_session_lifetime = timedelta(days=30)

DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "admin")
DASHBOARD_SYNC_TOKEN = os.getenv("DASHBOARD_SYNC_TOKEN", "")
HISTORY_FILE = "history.json"
USERS_FILE = os.getenv("USERS_FILE", "users.json")
ACCOUNTS_FILE = os.getenv("ACCOUNTS_FILE", "accounts.json")
NOTIFICATIONS_FILE = "notifications.json"
FEATURED_FILE = "featured.json"
MAX_HISTORY = 500
MAX_NOTIFICATIONS = 200
PER_PAGE = 20

AUTHORIZED_USER_IDS_STR = os.getenv("AUTHORIZED_USER_IDS", os.getenv("AUTHORIZED_USER_ID", ""))
OWNER_ID = int(AUTHORIZED_USER_IDS_STR.split(",")[0].strip()) if AUTHORIZED_USER_IDS_STR else 0

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", os.getenv("BOT_TOKEN", ""))
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "sovan2026")
ADMIN_USERNAME = "mstafa"
ADMIN_EMAIL = "mstafa.ahmad.1999@gmail.com"


# ── accounts helpers ──────────────────────────────────────────────────────────

def _load_accounts() -> list[dict]:
    if os.path.exists(ACCOUNTS_FILE):
        try:
            with open(ACCOUNTS_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return []


def _save_accounts(accounts: list[dict]) -> None:
    with open(ACCOUNTS_FILE, "w") as f:
        json.dump(accounts, f, indent=2)


def _get_account_by_username(username: str) -> dict | None:
    for a in _load_accounts():
        if a.get("username", "").lower() == username.lower():
            return a
    return None


def _get_account_by_id(account_id: str) -> dict | None:
    for a in _load_accounts():
        if a.get("id") == account_id:
            return a
    return None


def _ensure_admin_exists() -> None:
    """Seed the admin account on first boot if accounts.json is missing or empty."""
    accounts = _load_accounts()
    if any(a.get("role") == "admin" for a in accounts):
        return
    accounts.append({
        "id": str(uuid.uuid4()),
        "username": ADMIN_USERNAME,
        "email": ADMIN_EMAIL,
        "password_hash": generate_password_hash(ADMIN_PASSWORD),
        "role": "admin",
        "approved": True,
        "created_at": datetime.utcnow().isoformat(),
    })
    _save_accounts(accounts)


# ── notifications helpers ─────────────────────────────────────────────────────

def _load_notifications() -> list[dict]:
    if os.path.exists(NOTIFICATIONS_FILE):
        try:
            with open(NOTIFICATIONS_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return []


def _save_notifications(notifications: list[dict]) -> None:
    with open(NOTIFICATIONS_FILE, "w") as f:
        json.dump(notifications[-MAX_NOTIFICATIONS:], f, indent=2)


def _load_featured() -> list[str]:
    """Owner-curated list of featured file_ids (newest pin last)."""
    if os.path.exists(FEATURED_FILE):
        try:
            with open(FEATURED_FILE) as f:
                data = json.load(f)
                return [str(x) for x in data] if isinstance(data, list) else []
        except Exception:
            pass
    return []


def _save_featured(ids: list[str]) -> None:
    with open(FEATURED_FILE, "w") as f:
        json.dump(ids[-60:], f, indent=2)


def _notify(type_: str, message: str, data: dict | None = None) -> None:
    """Save a notification and optionally push to Telegram for important events."""
    notifications = _load_notifications()
    notifications.append({
        "id": str(uuid.uuid4()),
        "type": type_,
        "message": message,
        "data": data or {},
        "read": False,
        "timestamp": datetime.utcnow().isoformat(),
    })
    _save_notifications(notifications)

    # Telegram push for high-priority events
    if type_ in ("signup", "upload") and BOT_TOKEN and OWNER_ID:
        try:
            import urllib.request
            payload = json.dumps({
                "chat_id": OWNER_ID,
                "text": f"🔔 <b>SOVAN Dashboard</b>\n{message}",
                "parse_mode": "HTML",
            }).encode()
            req = urllib.request.Request(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                data=payload,
                headers={"Content-Type": "application/json"},
            )
            urllib.request.urlopen(req, timeout=5)
        except Exception:
            pass


# ── history/users helpers ────────────────────────────────────────────────────

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


def _compute_stats(history: list[dict], user_id=None) -> dict:
    if user_id is not None:
        history = [e for e in history if str(e.get("user_id", "")) == str(user_id)]
    total_files = len(history)
    total_bytes = sum(e.get("file_size", 0) for e in history)
    uploaders: dict[int, int] = {}
    for e in history:
        uid = e.get("user_id", 0)
        uploaders[uid] = uploaders.get(uid, 0) + 1
    top_uploader = max(uploaders, key=uploaders.get) if uploaders else None
    top_count = uploaders[top_uploader] if top_uploader else 0

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


# ── auth decorators ───────────────────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("user_id"):
            # Legacy support: old sessions used "logged_in"
            if session.get("logged_in"):
                return f(*args, **kwargs)
            return redirect(url_for("login"))
        # Check if approved
        account = _get_account_by_id(session["user_id"])
        if account and not account.get("approved"):
            return redirect(url_for("pending"))
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if session.get("role") != "admin":
            return jsonify({"ok": False, "error": "Forbidden"}), 403
        return f(*args, **kwargs)
    return decorated


@app.context_processor
def inject_session_info():
    """Make session role/username available in all templates."""
    return {
        "current_user": session.get("username", ""),
        "current_role": session.get("role", "user"),
        "is_admin": session.get("role") == "admin",
    }


# ── auth routes ───────────────────────────────────────────────────────────────

@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        remember = request.form.get("remember")

        # Try new account-based auth first
        account = _get_account_by_username(username)
        if account and check_password_hash(account["password_hash"], password):
            if not account.get("approved"):
                session["user_id"] = account["id"]
                session["username"] = account["username"]
                session["role"] = account.get("role", "user")
                return redirect(url_for("pending"))
            if remember:
                session.permanent = True
            session["user_id"] = account["id"]
            session["username"] = account["username"]
            session["role"] = account.get("role", "user")
            session["logged_in"] = True  # legacy compat
            _notify("login", f"<b>{account['username']}</b> logged in")
            return redirect(url_for("index"))

        # Legacy fallback: single password
        elif not username and password == DASHBOARD_PASSWORD:
            if remember:
                session.permanent = True
            session["logged_in"] = True
            return redirect(url_for("index"))
        elif username and password == DASHBOARD_PASSWORD:
            # old-style single-password login with username field filled
            if remember:
                session.permanent = True
            session["logged_in"] = True
            session["username"] = username
            return redirect(url_for("index"))

        error = "Invalid username or password."

    return render_template("login.html", error=error)


@app.route("/signup", methods=["GET", "POST"])
def signup():
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm", "")

        if not username or not email or not password:
            error = "All fields are required."
        elif password != confirm:
            error = "Passwords do not match."
        elif len(password) < 6:
            error = "Password must be at least 6 characters."
        elif _get_account_by_username(username):
            error = "Username already taken."
        else:
            accounts = _load_accounts()
            # Check email uniqueness
            if any(a.get("email", "").lower() == email.lower() for a in accounts):
                error = "An account with this email already exists."
            else:
                new_account = {
                    "id": str(uuid.uuid4()),
                    "username": username,
                    "email": email,
                    "password_hash": generate_password_hash(password),
                    "role": "user",
                    "approved": False,
                    "created_at": datetime.utcnow().isoformat(),
                }
                accounts.append(new_account)
                _save_accounts(accounts)
                _notify("signup", f"New signup request: <b>{username}</b> ({email})")
                session["user_id"] = new_account["id"]
                session["username"] = username
                session["role"] = "user"
                return redirect(url_for("pending"))

    return render_template("signup.html", error=error)


@app.route("/pending")
def pending():
    user_id = session.get("user_id")
    if not user_id:
        return redirect(url_for("login"))
    account = _get_account_by_id(user_id)
    if account and account.get("approved"):
        return redirect(url_for("index"))
    return render_template("pending.html", username=session.get("username", ""))


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
    accounts = _load_accounts()
    stats_uid = None if session.get("role") == "admin" else session.get("user_id")
    stats = _compute_stats(history, user_id=stats_uid)

    page = max(1, int(request.args.get("page", 1)))
    all_reversed = list(reversed(history))
    total = len(all_reversed)
    total_pages = max(1, (total + PER_PAGE - 1) // PER_PAGE)
    page = min(page, total_pages)
    recent = all_reversed[(page - 1) * PER_PAGE: page * PER_PAGE]

    for e in recent:
        ts = e.get("timestamp", "")
        try:
            e["_dt"] = datetime.fromisoformat(ts).strftime("%b %d, %H:%M")
        except Exception:
            e["_dt"] = ts[:10]
        e["_size"] = _format_size(e.get("file_size", 0))
        e["_owner"] = e.get("user_id") == OWNER_ID
        ext = os.path.splitext(e.get("file_name", ""))[1].lower()
        e["_previewable"] = ext in {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".mp4", ".webm", ".mov", ".m4v"}

    # Pending approvals count for admin badge
    pending_count = sum(1 for a in accounts if not a.get("approved"))
    # Unread notifications count
    unread_count = sum(1 for n in _load_notifications() if not n.get("read"))

    return render_template(
        "index.html",
        stats=stats,
        recent=recent,
        users=users,
        accounts=accounts,
        owner_id=OWNER_ID,
        page=page,
        total_pages=total_pages,
        total=total,
        pending_count=pending_count,
        unread_count=unread_count,
        is_admin=session.get("role") == "admin",
        current_user=session.get("username", ""),
        display_name=next((a.get("display_name") or a.get("username","") for a in accounts if a.get("id")==session.get("user_id")), session.get("username","")),
    )


@app.route("/browser")
@login_required
def browser():
    return render_template("browser.html")


# ── notifications API ─────────────────────────────────────────────────────────

@app.route("/api/notifications")
@login_required
def api_notifications():
    notifications = list(reversed(_load_notifications()))
    if session.get("role") != "admin":
        allowed = {"upload", "download", "general"}
        notifications = [n for n in notifications if n.get("type") in allowed]
    unread = sum(1 for n in notifications if not n.get("read"))
    return jsonify({"ok": True, "notifications": notifications[:50], "unread": unread})


@app.route("/api/notifications/read", methods=["POST"])
@login_required
def api_notifications_read():
    nid = request.get_json(force=True).get("id")
    notifications = _load_notifications()
    for n in notifications:
        if n.get("id") == nid:
            n["read"] = True
    _save_notifications(notifications)
    return jsonify({"ok": True})


@app.route("/api/notifications/read-all", methods=["POST"])
@login_required
def api_notifications_read_all():
    notifications = _load_notifications()
    for n in notifications:
        n["read"] = True
    _save_notifications(notifications)
    return jsonify({"ok": True})


# ── admin API ─────────────────────────────────────────────────────────────────

@app.route("/api/admin/pending")
@login_required
@admin_required
def api_admin_pending():
    accounts = _load_accounts()
    pending = [a for a in accounts if not a.get("approved")]
    # Strip password hashes before sending to client
    safe = [{k: v for k, v in a.items() if k != "password_hash"} for a in pending]
    return jsonify({"ok": True, "pending": safe})


@app.route("/api/admin/users/approve", methods=["POST"])
@login_required
@admin_required
def api_admin_approve():
    account_id = request.get_json(force=True).get("account_id")
    accounts = _load_accounts()
    for a in accounts:
        if a.get("id") == account_id:
            a["approved"] = True
            _save_accounts(accounts)
            _notify("approved", f"Account approved: <b>{a['username']}</b>")
            # Telegram push for approval
            if BOT_TOKEN and OWNER_ID:
                try:
                    import urllib.request
                    payload = json.dumps({
                        "chat_id": OWNER_ID,
                        "text": f"✅ <b>SOVAN Dashboard</b>\nAccount approved: {a['username']}",
                        "parse_mode": "HTML",
                    }).encode()
                    req = urllib.request.Request(
                        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                        data=payload,
                        headers={"Content-Type": "application/json"},
                    )
                    urllib.request.urlopen(req, timeout=5)
                except Exception:
                    pass
            return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "Account not found"}), 404


@app.route("/api/admin/users/reject", methods=["POST"])
@login_required
@admin_required
def api_admin_reject():
    account_id = request.get_json(force=True).get("account_id")
    accounts = _load_accounts()
    before = len(accounts)
    accounts = [a for a in accounts if a.get("id") != account_id]
    if len(accounts) == before:
        return jsonify({"ok": False, "error": "Account not found"}), 404
    _save_accounts(accounts)
    return jsonify({"ok": True})


@app.route("/api/profile/avatar", methods=["POST"])
@login_required
def api_profile_avatar():
    if "avatar" not in request.files:
        return jsonify({"ok": False, "error": "No file"}), 400
    f = request.files["avatar"]
    username = session.get("username", "unknown").lower()
    avatars_dir = os.path.join("static", "avatars")
    os.makedirs(avatars_dir, exist_ok=True)
    for old_ext in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
        old = os.path.join(avatars_dir, f"{username}{old_ext}")
        if os.path.exists(old):
            os.remove(old)
    ext = os.path.splitext(f.filename or "")[1].lower() or ".jpg"
    if ext not in (".jpg", ".jpeg", ".png", ".webp"):
        ext = ".jpg"
    filename = f"{username}{ext}"
    f.save(os.path.join(avatars_dir, filename))
    return jsonify({"ok": True, "url": f"/static/avatars/{filename}"})


@app.route("/api/profile/avatar-url")
@login_required
def api_profile_avatar_url():
    username = session.get("username", "unknown").lower()
    avatars_dir = os.path.join("static", "avatars")
    for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
        path = os.path.join(avatars_dir, f"{username}{ext}")
        if os.path.exists(path):
            return jsonify({"ok": True, "url": f"/static/avatars/{username}{ext}"})
    return jsonify({"ok": True, "url": None})


@app.route("/api/profile/update", methods=["POST"])
@login_required
def api_profile_update():
    data = request.get_json(force=True)
    field = data.get("field", "name")
    accounts = _load_accounts()
    acct = next((a for a in accounts if a.get("id") == session.get("user_id")), None)
    if not acct:
        return jsonify({"ok": False, "error": "Account not found"}), 404

    if field == "name":
        val = data.get("value", "").strip()
        if not val:
            return jsonify({"ok": False, "error": "Name cannot be empty"}), 400
        acct["display_name"] = val
        session["username"] = val

    elif field == "email":
        val = data.get("value", "").strip().lower()
        if not val or "@" not in val:
            return jsonify({"ok": False, "error": "Invalid email"}), 400
        if any(a.get("email") == val and a.get("id") != acct["id"] for a in accounts):
            return jsonify({"ok": False, "error": "Email already in use"}), 400
        acct["email"] = val

    elif field == "password":
        current = data.get("current", "")
        new_pw = data.get("value", "")
        if not check_password_hash(acct.get("password_hash", ""), current):
            return jsonify({"ok": False, "error": "Current password is wrong"}), 400
        if len(new_pw) < 6:
            return jsonify({"ok": False, "error": "Password must be at least 6 characters"}), 400
        acct["password_hash"] = generate_password_hash(new_pw)

    else:
        return jsonify({"ok": False, "error": "Unknown field"}), 400

    _save_accounts(accounts)
    return jsonify({"ok": True})


@app.route("/api/admin/users/reset-password", methods=["POST"])
@login_required
@admin_required
def api_admin_reset_password():
    data = request.get_json(force=True)
    account_id = data.get("account_id")
    new_pw = data.get("password", "").strip()
    if len(new_pw) < 6:
        return jsonify({"ok": False, "error": "Password must be at least 6 characters"}), 400
    accounts = _load_accounts()
    acct = next((a for a in accounts if a.get("id") == account_id), None)
    if not acct:
        return jsonify({"ok": False, "error": "Account not found"}), 404
    if acct.get("role") == "admin" and acct.get("id") != session.get("user_id"):
        return jsonify({"ok": False, "error": "Cannot reset another admin's password"}), 403
    acct["password_hash"] = generate_password_hash(new_pw)
    _save_accounts(accounts)
    return jsonify({"ok": True})


@app.route("/api/admin/users/delete", methods=["POST"])
@login_required
@admin_required
def api_admin_delete_user():
    account_id = request.get_json(force=True).get("account_id")
    # Cannot delete yourself
    if account_id == session.get("user_id"):
        return jsonify({"ok": False, "error": "Cannot delete your own account"}), 400
    accounts = _load_accounts()
    before = len(accounts)
    accounts = [a for a in accounts if a.get("id") != account_id]
    if len(accounts) == before:
        return jsonify({"ok": False, "error": "Account not found"}), 404
    _save_accounts(accounts)
    return jsonify({"ok": True})


# ── existing user API (Telegram IDs) ─────────────────────────────────────────

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

    try:
        import drive_service
        drive_service.delete_file(file_id)
    except Exception as exc:
        return jsonify({"ok": False, "error": f"Drive error: {exc}"}), 500

    history = _load_history()
    history = [e for e in history if e.get("file_id") != file_id]
    with open(HISTORY_FILE, "w") as f:
        json.dump(history, f)

    return jsonify({"ok": True})


# ── storage ──────────────────────────────────────────────────────────────────

@app.route("/api/storage")
@login_required
def api_storage():
    try:
        import drive_service
        quota = drive_service.get_storage_quota()
        return jsonify({"ok": True, "quota": quota})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


# ── file browser ──────────────────────────────────────────────────────────────

@app.route("/api/browser")
@login_required
def api_browser_list():
    parent = request.args.get("parent", "root")
    try:
        import drive_service
        contents = drive_service.list_folder_contents(parent)
        name = drive_service.get_folder_name(parent)
        return jsonify({"ok": True, "name": name, **contents})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/browser/rename", methods=["POST"])
@login_required
def api_browser_rename():
    data = request.get_json(force=True)
    file_id = data.get("file_id", "").strip()
    new_name = data.get("new_name", "").strip()
    if not file_id or not new_name:
        return jsonify({"ok": False, "error": "Missing file_id or new_name"}), 400
    try:
        import drive_service
        updated = drive_service.rename_file(file_id, new_name)
        return jsonify({"ok": True, "name": updated.get("name")})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/browser/move", methods=["POST"])
@login_required
def api_browser_move():
    data = request.get_json(force=True)
    file_id = data.get("file_id", "").strip()
    new_parent = data.get("new_parent", "root").strip()
    old_parent = data.get("old_parent", "root").strip()
    if not file_id:
        return jsonify({"ok": False, "error": "Missing file_id"}), 400
    try:
        import drive_service
        drive_service.move_file(file_id, new_parent, old_parent)
        return jsonify({"ok": True})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/browser/thumbnail/<file_id>")
@login_required
def api_thumbnail(file_id):
    import drive_service
    try:
        path = drive_service._abspath(file_id)
        mime = mimetypes.guess_type(path)[0] or ""
        if os.path.isfile(path) and mime.startswith("image/"):
            return send_file(path, conditional=True)
        return ("", 404)
    except Exception:
        return ("", 404)


@app.route("/api/browser/download/<file_id>")
@login_required
def api_browser_download(file_id):
    try:
        import drive_service
        path = drive_service._abspath(file_id)
        if not os.path.isfile(path):
            return jsonify({"ok": False, "error": "Not found"}), 404
        # conditional=True → honors HTTP Range so video scrubbing / large files
        # stream instead of loading into memory. Inline (no as_attachment) so
        # images/video render in the preview; the browser's download link still saves.
        return send_file(path, conditional=True, download_name=os.path.basename(path))
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


# ── download folder as zip ───────────────────────────────────────────────────

@app.route("/api/browser/download-folder/<folder_id>")
@login_required
def api_browser_download_folder(folder_id):
    import drive_service, zipfile, io
    try:
        folder_name = drive_service.get_folder_name(folder_id)
        folder_path = drive_service._abspath(folder_id)
        if not os.path.isdir(folder_path):
            return jsonify({"ok": False, "error": "Folder not found"}), 404

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, dirs, files in os.walk(folder_path):
                for fname in files:
                    abs_path = os.path.join(root, fname)
                    arc_name = os.path.relpath(abs_path, folder_path)
                    zf.write(abs_path, arc_name)
        buf.seek(0)
        safe_name = "".join(c if c.isalnum() or c in "._- " else "_" for c in folder_name)
        return send_file(
            buf,
            mimetype="application/zip",
            as_attachment=True,
            download_name=f"{safe_name}.zip"
        )
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


# ── batch delete ─────────────────────────────────────────────────────────────

@app.route("/api/files/delete-batch", methods=["POST"])
@login_required
def api_delete_files_batch():
    file_ids = request.get_json(force=True).get("file_ids", [])
    if not file_ids:
        return jsonify({"ok": False, "error": "No file IDs provided"}), 400

    import drive_service
    deleted, failed = [], []
    for fid in file_ids:
        try:
            drive_service.delete_file(fid)
            deleted.append(fid)
        except Exception:
            failed.append(fid)

    history = [e for e in _load_history() if e.get("file_id") not in deleted]
    with open(HISTORY_FILE, "w") as f:
        json.dump(history, f)

    return jsonify({"ok": True, "deleted": len(deleted), "failed": len(failed)})


# ── file upload from dashboard ───────────────────────────────────────────────

@app.route("/api/folders")
@login_required
def api_folders():
    parent = request.args.get("parent", "root")
    try:
        import drive_service
        folders, _ = drive_service.list_folders(parent)
        return jsonify({"ok": True, "folders": [{"id": f["id"], "name": f["name"]} for f in folders]})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/folders/create", methods=["POST"])
@login_required
def api_create_folder():
    data = request.get_json(force=True)
    name = (data.get("name") or "").strip()
    parent = data.get("parent", "root")
    if not name:
        return jsonify({"ok": False, "error": "Folder name required"}), 400
    try:
        import drive_service
        folder = drive_service.create_folder(name, parent)
        return jsonify({"ok": True, "folder": folder})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/files/upload", methods=["POST"])
@login_required
def api_upload_file():
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

        _notify("upload", f"📁 <b>{entry['file_name']}</b> uploaded by {session.get('username', 'user')} ({_format_size(entry['file_size'])})")

        return jsonify({"ok": True, "entry": entry})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def _link_tmp_dir() -> str:
    d = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tmp")
    os.makedirs(d, exist_ok=True)
    return d


def _cleanup_link_tmp(max_age_sec: int = 1800) -> None:
    import time
    d = _link_tmp_dir()
    now = time.time()
    for name in os.listdir(d):
        p = os.path.join(d, name)
        try:
            if os.path.isfile(p) and now - os.path.getmtime(p) > max_age_sec:
                os.unlink(p)
        except OSError:
            pass


@app.route("/api/link/fetch", methods=["POST"])
@login_required
def api_link_fetch():
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    if not url:
        return jsonify({"ok": False, "error": "No URL provided"}), 400

    _cleanup_link_tmp()
    tmp_dir = _link_tmp_dir()
    try:
        import downloader
        path, file_name, mime_type, size, meta = downloader.fetch_media(url, tmp_dir)
        return jsonify({
            "ok": True,
            "tmp": os.path.basename(path),
            "file_name": file_name,
            "mime": mime_type,
            "size": size,
            "is_video": mime_type.startswith("video/"),
            "is_image": mime_type.startswith("image/"),
            "meta": meta or {},
        })
    except Exception as exc:
        reason = (str(exc).splitlines() or [""])[-1].strip()[:300] or "download failed"
        return jsonify({"ok": False, "error": reason}), 500


@app.route("/api/link/preview/<path:tmp>")
@login_required
def api_link_preview(tmp):
    safe = os.path.basename(tmp)
    p = os.path.join(_link_tmp_dir(), safe)
    if not os.path.isfile(p):
        return jsonify({"ok": False, "error": "Preview expired"}), 404
    if request.args.get("dl"):
        name = os.path.basename(request.args.get("name") or safe)
        return send_file(p, as_attachment=True, download_name=name)
    return send_file(p)


@app.route("/api/link/cancel", methods=["POST"])
@login_required
def api_link_cancel():
    data = request.get_json(silent=True) or {}
    safe = os.path.basename(data.get("tmp") or "")
    p = os.path.join(_link_tmp_dir(), safe)
    if safe and os.path.isfile(p):
        try:
            os.unlink(p)
        except OSError:
            pass
    return jsonify({"ok": True})


@app.route("/api/link/upload", methods=["POST"])
@login_required
def api_link_upload():
    data = request.get_json(silent=True) or {}
    safe = os.path.basename(data.get("tmp") or "")
    file_name = data.get("file_name") or safe
    mime_type = data.get("mime") or "application/octet-stream"
    folder_id = data.get("folder_id", "root")
    p = os.path.join(_link_tmp_dir(), safe)
    if not safe or not os.path.isfile(p):
        return jsonify({"ok": False, "error": "File expired — fetch again."}), 404

    try:
        import drive_service
        size = os.path.getsize(p)
        resource = drive_service.upload_file(p, file_name, mime_type, folder_id)
        folder_name = drive_service.get_folder_name(folder_id)

        entry = {
            "user_id": OWNER_ID,
            "file_name": resource.get("name", file_name),
            "file_size": int(resource.get("size", size) or size),
            "folder_name": folder_name,
            "web_link": resource.get("webViewLink", ""),
            "file_id": resource.get("id", ""),
            "timestamp": datetime.utcnow().isoformat(),
        }
        history = _load_history()
        history.append(entry)
        with open(HISTORY_FILE, "w") as fh:
            json.dump(history[-MAX_HISTORY:], fh)

        _notify("upload", f"📁 <b>{entry['file_name']}</b> uploaded via link by {session.get('username', 'user')} ({_format_size(entry['file_size'])})")

        return jsonify({"ok": True, "entry": entry})
    except Exception as exc:
        reason = (str(exc).splitlines() or [""])[-1].strip()[:300] or "upload failed"
        return jsonify({"ok": False, "error": reason}), 500
    finally:
        try:
            os.unlink(p)
        except OSError:
            pass


# ── internal push (called by Android bot) ────────────────────────────────────

@app.route("/api/internal/push", methods=["POST"])
def api_internal_push():
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
        _notify("upload", f"📁 <b>{entry.get('file_name', 'file')}</b> uploaded via bot ({_format_size(entry.get('file_size', 0))})")
        return jsonify({"ok": True})

    if kind == "users":
        users = data.get("users")
        if not isinstance(users, list):
            return jsonify({"ok": False, "error": "Missing users list"}), 400
        _save_users([int(u) for u in users])
        return jsonify({"ok": True})

    return jsonify({"ok": False, "error": f"Unknown type: {kind}"}), 400


# ── public archive (no auth) ──────────────────────────────────────────────────

@app.route("/archive")
def archive():
    return render_template("archive.html")


def _build_archive_manifest():
    """Walk the archive tree and return {images:[...], videos:[...]} newest first.

    Recursive scan of EVERY folder so any upload anywhere in the archive shows
    up on Home, not just files in folders named 'IMAGE'/'VIDEO'.
    """
    import drive_service as ds

    IMAGE_MIME = ("image/",)
    VIDEO_MIME = ("video/",)
    ANIME_HINTS = ("anime",)

    images: list[dict] = []
    videos: list[dict] = []
    SEEN: set[str] = set()
    MAX_DEPTH = 4
    MAX_FILES = 800   # hard cap so a huge archive doesn't blow the response

    def _label_for(folder_path_lower: str, mime: str) -> str:
        if any(h in folder_path_lower for h in ANIME_HINTS):
            return "anime"
        if mime.startswith("video/"):
            return "video"
        return "image"

    def _scan(folder_id: str, path_lower: str, depth: int):
        # files in this folder
        token = None
        while True:
            batch, token = ds.list_files(folder_id, page_token=token)
            for f in batch:
                fid = f["id"]
                if fid in SEEN:
                    continue
                SEEN.add(fid)
                mime = (f.get("mimeType") or "").lower()
                entry = {
                    "id": fid,
                    "name": f["name"],
                    "date": f.get("createdTime", f.get("modifiedTime", "")),
                    "folder": _label_for(path_lower, mime),
                }
                if mime.startswith(IMAGE_MIME):
                    images.append(entry)
                elif mime.startswith(VIDEO_MIME):
                    videos.append(entry)
                if len(images) + len(videos) >= MAX_FILES:
                    return
            if not token:
                break
        if depth >= MAX_DEPTH:
            return
        # recurse into sub-folders
        sub_token = None
        while True:
            subs, sub_token = ds.list_folders(folder_id, page_token=sub_token)
            for sf in subs:
                child_path = (path_lower + "/" + sf["name"].lower()).strip("/")
                _scan(sf["id"], child_path, depth + 1)
                if len(images) + len(videos) >= MAX_FILES:
                    return
            if not sub_token:
                break

    try:
        _scan("root", "", 0)
    except Exception:
        pass

    images.sort(key=lambda x: x["date"], reverse=True)
    videos.sort(key=lambda x: x["date"], reverse=True)
    return {"images": images, "videos": videos}


@app.route("/api/archive/manifest")
def archive_manifest():
    try:
        manifest = _build_archive_manifest()
        resp = jsonify(manifest)
        resp.headers["Cache-Control"] = "public, max-age=300"
        return resp
    except Exception as exc:
        return jsonify({"images": [], "videos": [], "error": str(exc)}), 200


@app.route("/api/archive/thumb/<file_id>")
def archive_thumb(file_id):
    try:
        import drive_service as ds
        path = ds._abspath(file_id)
        mime = mimetypes.guess_type(path)[0] or ""
        if os.path.isfile(path) and mime.startswith("image/"):
            return send_file(path, conditional=True)
        return ("", 404)
    except Exception:
        return ("", 404)


@app.route("/api/archive/media/<file_id>")
def archive_media(file_id):
    try:
        import drive_service as ds
        path = ds._abspath(file_id)
        if not os.path.isfile(path):
            return ("", 404)
        return send_file(path, conditional=True, download_name=os.path.basename(path))
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


# ── featured (owner-curated home highlights) ──────────────────────────────────

def _media_type(mime: str) -> str:
    if (mime or "").startswith("video/"):
        return "video"
    if (mime or "").startswith("image/"):
        return "image"
    return "other"


@app.route("/api/featured")
@login_required
def api_featured():
    import drive_service as ds
    out = []
    changed = False
    ids = _load_featured()
    for fid in list(ids):
        try:
            m = ds.get_file_info(fid)
            out.append({
                "id": m["id"],
                "name": m["name"],
                "date": m.get("createdTime", ""),
                "type": _media_type(m.get("mimeType", "")),
            })
        except Exception:
            ids.remove(fid)   # prune files that no longer exist
            changed = True
    if changed:
        _save_featured(ids)
    out.reverse()  # newest pin first
    return jsonify({"ok": True, "items": out})


@app.route("/api/featured/toggle", methods=["POST"])
@login_required
@admin_required
def api_featured_toggle():
    data = request.get_json(force=True)
    fid = (data.get("file_id") or "").strip()
    if not fid:
        return jsonify({"ok": False, "error": "Missing file_id"}), 400
    ids = _load_featured()
    if fid in ids:
        ids.remove(fid)
        featured = False
    else:
        ids.append(fid)
        featured = True
    _save_featured(ids)
    return jsonify({"ok": True, "featured": featured})


# ── run ───────────────────────────────────────────────────────────────────────

_ensure_admin_exists()

if __name__ == "__main__":
    port = int(os.getenv("DASHBOARD_PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
