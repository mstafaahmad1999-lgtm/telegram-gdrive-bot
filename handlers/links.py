"""Handle messages containing a media URL: download via yt-dlp, then feed the
result into the normal folder-picker → Drive upload pipeline."""
import asyncio
import logging
import os
import re

from telegram import Update
from telegram.ext import ContextTypes

import downloader
import state
import user_manager
from handlers.files import enqueue_pending, _format_size

logger = logging.getLogger(__name__)

URL_RE = re.compile(r"https?://[^\s]+")


async def link_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None or not user_manager.is_authorized(user.id):
        await update.message.reply_text("Not authorized.")
        return

    text = update.message.text or ""
    match = URL_RE.search(text)
    if not match:
        return
    url = match.group(0).rstrip(".,);]")

    status = await update.message.reply_text(
        "⬇️ *Downloading media…*\nThis can take a few seconds.",
        parse_mode="Markdown",
    )

    tmp_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "tmp")
    loop = asyncio.get_event_loop()
    try:
        path, file_name, mime_type, size = await loop.run_in_executor(
            None, lambda: downloader.download_from_url(url, tmp_dir)
        )
    except Exception as exc:
        logger.warning("Download failed for %s: %s", url, exc)
        reason = (str(exc).splitlines() or [""])[-1].strip()[:300] or "unknown error"
        await status.edit_text(
            "❌ Couldn't download from that link.\n\n"
            f"Reason: {reason}\n\n"
            "Note: Instagram & Facebook usually need login cookies "
            "(see YTDLP_COOKIES). TikTok, X/Twitter, and YouTube normally work without."
        )
        return

    await status.edit_text(
        f"✅ Downloaded *{file_name}* ({_format_size(size)})\n\nChoose a destination folder below 👇",
        parse_mode="Markdown",
    )

    pending = state.PendingUpload(path, file_name, mime_type, size)
    await enqueue_pending(update, context, pending)
