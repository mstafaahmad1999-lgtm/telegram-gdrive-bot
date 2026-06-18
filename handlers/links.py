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
        path, file_name, mime_type, size = await asyncio.wait_for(
            loop.run_in_executor(None, lambda: downloader.fetch_media(url, tmp_dir)),
            timeout=150,
        )
    except asyncio.TimeoutError:
        logger.warning("Download timed out for %s", url)
        await status.edit_text(
            "❌ Download timed out (took too long).\n\n"
            "The site is likely blocking anonymous access. Instagram & Facebook "
            "usually need login cookies (see YTDLP_COOKIES). Try a TikTok, X, or YouTube link to test."
        )
        return
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
        f"✅ Downloaded *{file_name}* ({_format_size(size)})",
        parse_mode="Markdown",
    )

    # Send a preview of the media so the user can check it before uploading
    caption = "Choose a folder below to upload 👇"
    try:
        if mime_type.startswith("video/"):
            with open(path, "rb") as fh:
                await update.message.reply_video(video=fh, caption=caption)
        elif mime_type.startswith("image/"):
            with open(path, "rb") as fh:
                await update.message.reply_photo(photo=fh, caption=caption)
    except Exception as exc:
        logger.warning("Could not send preview for %s: %s", file_name, exc)

    pending = state.PendingUpload(path, file_name, mime_type, size)
    # short batch window: a single link shouldn't wait the full album timer
    await enqueue_pending(update, context, pending, wait=0.8)
