"""Handlers for incoming file/photo/video/audio messages."""
import logging
import os
import tempfile

from telegram import Update
from telegram.ext import ContextTypes

import state
import user_manager
from handlers.navigation import show_folder_picker

logger = logging.getLogger(__name__)

# Telegram bot API download limit (standard API)
MAX_FILE_BYTES = 20 * 1024 * 1024  # 20 MB


def _is_authorized(update: Update) -> bool:
    user = update.effective_user
    if user is None or not user_manager.is_authorized(user.id):
        logger.warning(
            "Unauthorized file upload attempt from user_id=%s",
            user.id if user else "unknown",
        )
        return False
    return True


def _format_size(size_bytes: int) -> str:
    if size_bytes >= 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    if size_bytes >= 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes} B"


def _extract_file_info(message) -> tuple[str, str, str, int] | None:
    """Return (file_id, file_name, mime_type, file_size) or None."""
    if message.document:
        d = message.document
        return d.file_id, d.file_name or "file", d.mime_type or "application/octet-stream", d.file_size or 0
    if message.video:
        v = message.video
        name = v.file_name or f"video_{v.file_id[:8]}.mp4"
        return v.file_id, name, v.mime_type or "video/mp4", v.file_size or 0
    if message.audio:
        a = message.audio
        name = a.file_name or f"audio_{a.file_id[:8]}.mp3"
        return a.file_id, name, a.mime_type or "audio/mpeg", a.file_size or 0
    if message.photo:
        # Largest photo is last in the list
        p = message.photo[-1]
        return p.file_id, f"photo_{p.file_id[:8]}.jpg", "image/jpeg", p.file_size or 0
    return None


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
            "For larger files (up to 2 GB), you need to run a self-hosted "
            "[Telegram Bot API server](https://core.telegram.org/bots/api#using-a-local-bot-api-server) "
            "and set `BOT_API_SERVER` in your `.env` to point to it.",
            parse_mode="Markdown",
        )
        return

    user_id = update.effective_user.id

    # Warn if replacing an existing pending upload
    existing = state.get_pending(user_id)
    if existing:
        try:
            os.unlink(existing.file_path)
        except OSError:
            pass
        await message.reply_text(
            f"ℹ️ Previous pending upload (*{existing.file_name}*) was replaced.",
            parse_mode="Markdown",
        )

    # Download to a temp file
    tmp_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "tmp")
    os.makedirs(tmp_dir, exist_ok=True)
    suffix = os.path.splitext(file_name)[1] or ""
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=suffix, dir=tmp_dir)
    os.close(tmp_fd)

    status_msg = await message.reply_text("⏬ Downloading file from Telegram…")

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
        await status_msg.edit_text(f"❌ Failed to download the file: {exc}")
        return

    state.set_pending(user_id, tmp_path, file_name, mime_type, actual_size)
    logger.info("Stored pending upload for user %s: %s (%s)", user_id, file_name, _format_size(actual_size))

    await status_msg.edit_text(
        f"📎 *{file_name}* ({_format_size(actual_size)})\n\nChoose a destination folder:",
        parse_mode="Markdown",
    )

    # Reset navigation stack and show root folder picker
    context.user_data["nav_stack"] = [("root", "My Drive")]
    await show_folder_picker(update, context, parent_id="root", status_message=status_msg)
