"""Handlers for /start, /cancel, and /whoami commands."""
import logging

from telegram import Update
from telegram.ext import ContextTypes

import state

logger = logging.getLogger(__name__)


def _is_authorized(update: Update, authorized_ids: set) -> bool:
    user = update.effective_user
    if user is None or user.id not in authorized_ids:
        logger.warning(
            "Unauthorized access attempt from user_id=%s username=%s",
            user.id if user else "unknown",
            user.username if user else "unknown",
        )
        return False
    return True


async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    authorized_ids: set = context.bot_data["authorized_user_ids"]
    if not _is_authorized(update, authorized_ids):
        await update.message.reply_text("Not authorized.")
        return

    await update.message.reply_text(
        "👋 *Google Drive Uploader Bot*\n\n"
        "Send me any file, photo, video, or audio and I'll help you upload it to Google Drive.\n\n"
        "*Commands:*\n"
        "• /start — show this message\n"
        "• /cancel — cancel the current pending upload\n"
        "• /whoami — show your Telegram user ID\n\n"
        "Just send a file to get started!",
        parse_mode="Markdown",
    )


async def cancel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    authorized_ids: set = context.bot_data["authorized_user_ids"]
    if not _is_authorized(update, authorized_ids):
        await update.message.reply_text("Not authorized.")
        return

    user_id = update.effective_user.id
    pending = state.get_pending(user_id)
    if pending:
        import os
        try:
            os.unlink(pending.file_path)
        except OSError:
            pass
        state.clear_pending(user_id)
        await update.message.reply_text("✅ Pending upload cancelled.")
    else:
        await update.message.reply_text("No pending upload to cancel.")


async def whoami_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    await update.message.reply_text(
        f"Your Telegram user ID is: `{user.id}`",
        parse_mode="Markdown",
    )
