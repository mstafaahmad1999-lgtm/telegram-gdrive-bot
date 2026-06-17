"""Handlers for incoming file/photo/video/audio messages with album support."""
import asyncio
import logging
import os
import tempfile

from telegram import Update
from telegram.ext import ContextTypes

import state
import user_manager
from handlers.navigation import show_folder_picker

logger = logging.getLogger(__name__)

MAX_FILE_BYTES = 20 * 1024 * 1024  # 20 MB
ALBUM_WAIT_SECONDS = 3  # wait this long for more files before showing picker


def _is_authorized(update: Update) -> bool:
    user = update.effective_user
    if user is None or not user_manager.is_authorized(user.id):
        logger.warning("Unauthorized file upload attempt from user_id=%s", user.id if user else "unknown")
        return False
    return True


def _format_size(size_bytes: int) -> str:
    if size_bytes >= 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    if size_bytes >= 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes} B"


def _extract_file_info(message) -> tuple[str, str, str, int] | None:
    if message.document:
        d = message.document
        return d.file_id, d.file_name or "file", d.mime_type or "application/octet-stream", d.file_size or 0
    if message.video:
        v = message.video
        return v.file_id, v.file_name or f"video_{v.file_id[:8]}.mp4", v.mime_type or "video/mp4", v.file_size or 0
    if message.audio:
        a = message.audio
        return a.file_id, a.file_name or f"audio_{a.file_id[:8]}.mp3", a.mime_type or "audio/mpeg", a.file_size or 0
    if message.photo:
        p = message.photo[-1]
        return p.file_id, f"photo_{p.file_id[:8]}.jpg", "image/jpeg", p.file_size or 0
    return None


async def _flush_album(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int) -> None:
    """Called after album wait timer expires — show folder picker for the buffered files."""
    album = state.get_album(user_id)
    if not album or not album.files:
        return

    count = len(album.files)
    total_size = sum(f.file_size for f in album.files)
    context.user_data["nav_stack"] = [("root", "My Drive")]

    status_msg = await context.bot.send_message(
        update.effective_chat.id,
        f"📦 *{count} file{'s' if count > 1 else ''}* ({_format_size(total_size)})\n\nChoose a destination folder:",
        parse_mode="Markdown",
    )
    await show_folder_picker(update, context, parent_id="root", status_message=status_msg)


async def file_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update):
        await update.message.reply_text("Not authorized.")
        return

    message = update.message
    info = _extract_file_info(message)
    if info is None:
        await message.reply_text("Unsupported file type.")
        return

    file_id, file_name, mime_type, file_size = info

    if file_size > MAX_FILE_BYTES:
        await message.reply_text(
            f"⚠️ *File too large* ({_format_size(file_size)})\n\n"
            "The standard Telegram Bot API only supports downloading files up to *20 MB*.\n\n"
            "For larger files (up to 2 GB), you need a self-hosted "
            "[Telegram Bot API server](https://core.telegram.org/bots/api#using-a-local-bot-api-server).",
            parse_mode="Markdown",
        )
        return

    user_id = update.effective_user.id

    # Download to temp file
    tmp_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "tmp")
    os.makedirs(tmp_dir, exist_ok=True)
    suffix = os.path.splitext(file_name)[1] or ""
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=suffix, dir=tmp_dir)
    os.close(tmp_fd)

    try:
        tg_file = await context.bot.get_file(file_id)
        await tg_file.download_to_drive(tmp_path)
        actual_size = os.path.getsize(tmp_path)
    except Exception as exc:
        logger.error("Failed to download file %s: %s", file_id, exc)
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        await message.reply_text(f"❌ Failed to download the file: {exc}")
        return

    pending = state.PendingUpload(tmp_path, file_name, mime_type, actual_size)

    # Check if this is part of an album (multiple files sent quickly)
    album = state.get_album(user_id)
    if album is None:
        album = state.AlbumBuffer()
        state.set_album(user_id, album)

    # Cancel existing timer
    if album.timer_task and not album.timer_task.done():
        album.timer_task.cancel()

    album.files.append(pending)
    logger.info("Buffered file %s for user %s (album size: %d)", file_name, user_id, len(album.files))

    # Set timer to flush album after wait period
    async def _timer():
        await asyncio.sleep(ALBUM_WAIT_SECONDS)
        await _flush_album(update, context, user_id)

    album.timer_task = asyncio.create_task(_timer())
    state.set_album(user_id, album)

    # Also update single pending for backward compat
    state.set_pending(user_id, tmp_path, file_name, mime_type, actual_size)


async def new_folder_name_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle text message when bot is waiting for a new folder name."""
    if not _is_authorized(update):
        return

    parent_id = context.user_data.get("awaiting_folder_name")
    if not parent_id:
        return

    folder_name = update.message.text.strip()
    if not folder_name:
        await update.message.reply_text("❌ Folder name cannot be empty.")
        return

    context.user_data.pop("awaiting_folder_name", None)

    try:
        import drive_service
        loop = asyncio.get_event_loop()
        new_folder = await loop.run_in_executor(
            None, lambda: drive_service.create_folder(folder_name, parent_id)
        )
        await update.message.reply_text(
            f"✅ Folder *{new_folder['name']}* created!",
            parse_mode="Markdown",
        )
        # Navigate into the new folder
        nav_stack: list = context.user_data.setdefault("nav_stack", [("root", "My Drive")])
        nav_stack.append((new_folder["id"], new_folder["name"]))
        msg = await update.message.reply_text("Loading…")
        await show_folder_picker(update, context, parent_id=new_folder["id"], status_message=msg)
    except Exception as exc:
        await update.message.reply_text(f"❌ Could not create folder: {exc}")
