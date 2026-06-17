"""Handlers for /start, /cancel, /whoami, /adduser, /removeuser, /listusers."""
import logging
import os

from telegram import Update
from telegram.ext import ContextTypes

import state
import user_manager

logger = logging.getLogger(__name__)


def _is_authorized(update: Update) -> bool:
    user = update.effective_user
    if user is None or not user_manager.is_authorized(user.id):
        logger.warning(
            "Unauthorized access attempt from user_id=%s username=%s",
            user.id if user else "unknown",
            user.username if user else "unknown",
        )
        return False
    return True


def _is_owner(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Only the original owner (first ID in env) can manage users."""
    user = update.effective_user
    return user is not None and user.id == context.bot_data["owner_id"]


async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update):
        await update.message.reply_text("Not authorized.")
        return

    await update.message.reply_text(
        "👋 *Google Drive Uploader Bot*\n\n"
        "Send me any file, photo, video, or audio and I'll help you upload it to Google Drive.\n\n"
        "*Commands:*\n"
        "• /start — show this message\n"
        "• /cancel — cancel the current pending upload\n"
        "• /whoami — show your Telegram user ID\n"
        "• /adduser ID — add a friend (owner only)\n"
        "• /removeuser ID — remove a friend (owner only)\n"
        "• /listusers — list all authorized users (owner only)\n\n"
        "Just send a file to get started!",
        parse_mode="Markdown",
    )


async def cancel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update):
        await update.message.reply_text("Not authorized.")
        return

    user_id = update.effective_user.id
    pending = state.get_pending(user_id)
    if pending:
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


async def adduser_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_owner(update, context):
        await update.message.reply_text("Only the bot owner can add users.")
        return

    args = context.args
    if not args:
        await update.message.reply_text(
            "Usage: `/adduser 123456789`\n\nAsk your friend to send /whoami to get their ID.",
            parse_mode="Markdown",
        )
        return

    try:
        new_id = int(args[0])
    except ValueError:
        await update.message.reply_text("Invalid ID. Must be a number like `123456789`.", parse_mode="Markdown")
        return

    if user_manager.add(new_id):
        # Keep bot_data in sync
        context.bot_data["authorized_user_ids"] = user_manager.get_all()
        await update.message.reply_text(f"✅ User `{new_id}` added successfully!", parse_mode="Markdown")
        logger.info("Owner added user %d", new_id)
    else:
        await update.message.reply_text(f"User `{new_id}` is already authorized.", parse_mode="Markdown")


async def removeuser_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_owner(update, context):
        await update.message.reply_text("Only the bot owner can remove users.")
        return

    args = context.args
    if not args:
        await update.message.reply_text("Usage: `/removeuser 123456789`", parse_mode="Markdown")
        return

    try:
        rem_id = int(args[0])
    except ValueError:
        await update.message.reply_text("Invalid ID. Must be a number.", parse_mode="Markdown")
        return

    if rem_id == context.bot_data["owner_id"]:
        await update.message.reply_text("❌ You cannot remove yourself (the owner).")
        return

    if user_manager.remove(rem_id):
        context.bot_data["authorized_user_ids"] = user_manager.get_all()
        await update.message.reply_text(f"✅ User `{rem_id}` removed.", parse_mode="Markdown")
        logger.info("Owner removed user %d", rem_id)
    else:
        await update.message.reply_text(f"User `{rem_id}` was not in the authorized list.", parse_mode="Markdown")


async def listusers_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_owner(update, context):
        await update.message.reply_text("Only the bot owner can list users.")
        return

    ids = user_manager.get_all()
    owner_id = context.bot_data["owner_id"]
    lines = []
    for uid in sorted(ids):
        tag = " (you)" if uid == owner_id else ""
        lines.append(f"• `{uid}`{tag}")

    text = "*Authorized users:*\n" + "\n".join(lines)
    await update.message.reply_text(text, parse_mode="Markdown")
