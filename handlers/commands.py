"""Handlers for all bot commands."""
import asyncio
import logging
import os
from datetime import datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

import drive_service
import history
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
    user = update.effective_user
    return user is not None and user.id == context.bot_data["owner_id"]


def _format_size(size_bytes: int) -> str:
    if size_bytes >= 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"
    if size_bytes >= 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    if size_bytes >= 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes} B"


async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update):
        await update.message.reply_text("Not authorized.")
        return

    await update.message.reply_text(
        "👋 *Google Drive Uploader Bot*\n\n"
        "Send me any file, photo, video, or audio and I'll help you upload it to Google Drive.\n\n"
        "🔗 *Or paste a link* — Instagram reels, TikTok, Facebook, X/Twitter, YouTube — "
        "and I'll download the video and upload it for you.\n\n"
        "*Commands:*\n"
        "• /start — show this message\n"
        "• /cancel — cancel the current pending upload\n"
        "• /whoami — show your Telegram user ID\n"
        "• /recent — show last 5 uploads\n"
        "• /stats — show upload statistics\n"
        "• /search query — search files in Drive\n"
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

    # Cancel album buffer
    album = state.get_album(user_id)
    if album:
        if album.timer_task and not album.timer_task.done():
            album.timer_task.cancel()
        for f in album.files:
            try:
                os.unlink(f.file_path)
            except OSError:
                pass
        state.clear_album(user_id)

    pending = state.get_pending(user_id)
    if pending:
        try:
            os.unlink(pending.file_path)
        except OSError:
            pass
        state.clear_pending(user_id)
        await update.message.reply_text("✅ Pending upload cancelled.")
    elif album:
        await update.message.reply_text("✅ Pending upload cancelled.")
    else:
        await update.message.reply_text("No pending upload to cancel.")


async def whoami_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    await update.message.reply_text(
        f"Your Telegram user ID is: `{user.id}`",
        parse_mode="Markdown",
    )


async def recent_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update):
        await update.message.reply_text("Not authorized.")
        return

    entries = history.get_recent(5)
    if not entries:
        await update.message.reply_text("No uploads yet.")
        return

    lines = ["🗂️ *Last 5 uploads:*\n"]
    for e in entries:
        name = e.get("file_name", "?")
        size = _format_size(e.get("file_size", 0))
        folder = e.get("folder_name", "?")
        link = e.get("web_link", "")
        ts = e.get("timestamp", "")
        try:
            dt = datetime.fromisoformat(ts).strftime("%b %d, %H:%M")
        except Exception:
            dt = ts[:10]
        lines.append(f"• [{name}]({link})\n  📁 {folder} • {size} • {dt}")

    await update.message.reply_text(
        "\n\n".join(lines) if len(lines) > 1 else lines[0],
        parse_mode="Markdown",
        disable_web_page_preview=True,
    )


async def stats_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update):
        await update.message.reply_text("Not authorized.")
        return

    stats = history.get_stats()
    total_files = stats["total_files"]
    total_size = _format_size(stats["total_size"])
    num_users = len(stats["uploaders"])
    top = stats["top_uploader"]
    top_str = f"`{top}` ({stats['uploaders'][top]} files)" if top else "N/A"

    await update.message.reply_text(
        f"📈 *Upload Statistics*\n\n"
        f"📄 Total files uploaded: *{total_files}*\n"
        f"📦 Total size: *{total_size}*\n"
        f"👥 Active uploaders: *{num_users}*\n"
        f"🏆 Top uploader: {top_str}",
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
        await update.message.reply_text("Invalid ID. Must be a number.", parse_mode="Markdown")
        return

    if user_manager.add(new_id):
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

    await update.message.reply_text(
        "*Authorized users:*\n" + "\n".join(lines),
        parse_mode="Markdown",
    )


async def search_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update):
        await update.message.reply_text("Not authorized.")
        return

    query = " ".join(context.args).strip() if context.args else ""
    if not query:
        await update.message.reply_text(
            "Usage: `/search filename`\n\nExample: `/search photo` or `/search report`",
            parse_mode="Markdown",
        )
        return

    msg = await update.message.reply_text(f"🔍 Searching for *{query}*…", parse_mode="Markdown")

    try:
        loop = asyncio.get_event_loop()
        files = await loop.run_in_executor(None, lambda: drive_service.search_files(query))
    except Exception as exc:
        await msg.edit_text(f"❌ Search failed: {exc}")
        return

    if not files:
        await msg.edit_text(f"No files found matching *{query}*.", parse_mode="Markdown")
        return

    buttons = []
    for f in files[:10]:
        name = f.get("name", "?")
        size = _format_size(int(f.get("size", 0) or 0))
        label = f"{name[:28]}… ({size})" if len(name) > 28 else f"{name} ({size})"
        buttons.append([InlineKeyboardButton(f"📄 {label}", callback_data=f"fileinfo:{f['id']}")])

    await msg.edit_text(
        f"🔍 *{len(files)} result{'s' if len(files) != 1 else ''}* for _{query}_:",
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode="Markdown",
    )
