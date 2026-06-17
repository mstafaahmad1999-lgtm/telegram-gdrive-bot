"""Folder navigation keyboard and upload execution."""
import asyncio
import logging
import os
import uuid

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

import drive_service
import state
import user_manager

logger = logging.getLogger(__name__)

LABEL_MAX = 30  # truncate folder names longer than this in button labels


def _truncate(name: str, max_len: int = LABEL_MAX) -> str:
    return name if len(name) <= max_len else name[: max_len - 1] + "…"


def _disambiguate(folders: list[dict]) -> list[dict]:
    """Append last 4 chars of folder ID when multiple folders share a name."""
    seen: dict[str, int] = {}
    for f in folders:
        seen[f["name"]] = seen.get(f["name"], 0) + 1
    result = []
    for f in folders:
        label = f["name"]
        if seen[label] > 1:
            label = f"{label} ({f['id'][-4:]})"
        result.append({**f, "label": label})
    return result


def _store_page_token(context: ContextTypes.DEFAULT_TYPE, token: str) -> str:
    """Store a Drive page token and return a short key (≤8 chars)."""
    store: dict = context.bot_data.setdefault("page_tokens", {})
    key = uuid.uuid4().hex[:8]
    store[key] = token
    return key


def _get_page_token(context: ContextTypes.DEFAULT_TYPE, key: str) -> str | None:
    return context.bot_data.get("page_tokens", {}).get(key)


async def show_folder_picker(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    parent_id: str,
    page_token: str | None = None,
    status_message=None,
) -> None:
    """Build and send/edit the folder picker keyboard for parent_id."""
    try:
        folders, next_token = drive_service.list_folders(parent_id, page_token)
    except Exception as exc:
        logger.error("Failed to list folders for %s: %s", parent_id, exc)
        text = f"❌ Could not list folders: {exc}"
        if status_message:
            await status_message.edit_text(text)
        else:
            await update.effective_message.reply_text(text)
        return

    nav_stack: list[tuple[str, str]] = context.user_data.get("nav_stack", [("root", "My Drive")])
    current_name = nav_stack[-1][1] if nav_stack else "My Drive"

    buttons: list[list[InlineKeyboardButton]] = []

    # Upload here + Back row
    action_row = [InlineKeyboardButton("✅ Upload here", callback_data=f"upload:{parent_id}")]
    if len(nav_stack) > 1:
        action_row.append(InlineKeyboardButton("⬅️ Back", callback_data=f"back:{parent_id}"))
    buttons.append(action_row)

    # Folder buttons
    disambiguated = _disambiguate(folders)
    for f in disambiguated:
        label = _truncate(f["label"])
        buttons.append([InlineKeyboardButton(f"📁 {label}", callback_data=f"nav:{f['id']}")])

    # Pagination row
    prev_tokens: list[str] = context.user_data.get("prev_tokens", [])
    pagination_row: list[InlineKeyboardButton] = []

    if prev_tokens:
        prev_key = prev_tokens[-1] if prev_tokens else ""
        pagination_row.append(
            InlineKeyboardButton("◀️ Prev", callback_data=f"prevpage:{parent_id}:{len(prev_tokens) - 1}")
        )

    if next_token:
        key = _store_page_token(context, next_token)
        pagination_row.append(
            InlineKeyboardButton("Next ▶️", callback_data=f"page:{parent_id}:{key}")
        )

    if pagination_row:
        buttons.append(pagination_row)

    keyboard = InlineKeyboardMarkup(buttons)
    folder_count = len(folders)
    subfolder_text = f"{folder_count} subfolder{'s' if folder_count != 1 else ''}" if folders else "no subfolders"
    text = f"📂 *{_truncate(current_name, 40)}* — {subfolder_text}\n\nChoose a folder or upload here:"

    if status_message:
        await status_message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")
    else:
        await update.effective_message.reply_text(text, reply_markup=keyboard, parse_mode="Markdown")


async def do_upload(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    folder_id: str,
) -> None:
    """Execute the actual upload to Drive."""
    query = update.callback_query
    user_id = update.effective_user.id
    pending = state.get_pending(user_id)

    if not pending:
        await query.edit_message_text("❌ No pending file found. Please send the file again.")
        return

    await query.edit_message_text("⏳ Uploading… 0%")

    loop = asyncio.get_event_loop()
    last_edit = {"pct": -1}

    def progress_callback(pct: int) -> None:
        if pct != last_edit["pct"] and pct % 10 == 0:
            last_edit["pct"] = pct
            asyncio.run_coroutine_threadsafe(
                query.edit_message_text(f"⏳ Uploading… {pct}%"),
                loop,
            )

    file_resource = None
    last_error = None
    for attempt in range(2):
        try:
            file_resource = await loop.run_in_executor(
                None,
                lambda: drive_service.upload_file(
                    pending.file_path,
                    pending.file_name,
                    pending.mime_type,
                    folder_id,
                    progress_callback,
                ),
            )
            break
        except Exception as exc:
            last_error = exc
            logger.warning("Upload attempt %d failed: %s", attempt + 1, exc)
            if attempt == 0:
                await asyncio.sleep(2)

    if file_resource is None:
        logger.error("Upload failed after 2 attempts: %s", last_error)
        await query.edit_message_text(
            f"❌ Upload failed: {last_error}\n\nYour file is still saved locally. "
            "Please try again by sending the file."
        )
        return

    # Success
    name = file_resource.get("name", pending.file_name)
    size_str = _format_size(int(file_resource.get("size", pending.file_size) or pending.file_size))
    link = file_resource.get("webViewLink", "")

    await query.edit_message_text(
        f"✅ *Uploaded successfully!*\n\n"
        f"📄 *{name}*\n"
        f"📦 Size: {size_str}\n"
        f"🔗 [Open in Google Drive]({link})",
        parse_mode="Markdown",
        disable_web_page_preview=True,
    )

    # Cleanup
    try:
        os.unlink(pending.file_path)
    except OSError as exc:
        logger.warning("Could not delete temp file %s: %s", pending.file_path, exc)
    state.clear_pending(user_id)
    context.user_data.pop("nav_stack", None)
    context.user_data.pop("prev_tokens", None)


def _format_size(size_bytes: int) -> str:
    if size_bytes >= 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    if size_bytes >= 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes} B"


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Route all inline keyboard callbacks."""
    user = update.effective_user
    if user is None or not user_manager.is_authorized(user.id):
        await update.callback_query.answer("Not authorized.")
        return

    query = update.callback_query
    await query.answer()
    data: str = query.data or ""

    if data.startswith("nav:"):
        folder_id = data[4:]
        folder_name = drive_service.get_folder_name(folder_id)
        nav_stack: list = context.user_data.setdefault("nav_stack", [("root", "My Drive")])
        nav_stack.append((folder_id, folder_name))
        context.user_data["prev_tokens"] = []
        await show_folder_picker(
            update, context, parent_id=folder_id, status_message=query.message
        )

    elif data.startswith("upload:"):
        folder_id = data[7:]
        await do_upload(update, context, folder_id)

    elif data.startswith("back:"):
        nav_stack: list = context.user_data.get("nav_stack", [("root", "My Drive")])
        if len(nav_stack) > 1:
            nav_stack.pop()
        parent_id, _ = nav_stack[-1]
        context.user_data["prev_tokens"] = []
        await show_folder_picker(
            update, context, parent_id=parent_id, status_message=query.message
        )

    elif data.startswith("page:"):
        # page:<parent_id>:<token_key>
        parts = data.split(":", 2)
        if len(parts) != 3:
            return
        _, parent_id, token_key = parts
        page_token = _get_page_token(context, token_key)
        if not page_token:
            await query.edit_message_text("❌ Page token expired. Please send the file again.")
            return
        prev_tokens: list = context.user_data.setdefault("prev_tokens", [])
        prev_tokens.append(token_key)
        await show_folder_picker(
            update, context, parent_id=parent_id, page_token=page_token, status_message=query.message
        )

    elif data.startswith("prevpage:"):
        # prevpage:<parent_id>:<stack_index>
        parts = data.split(":", 2)
        if len(parts) != 3:
            return
        _, parent_id, idx_str = parts
        prev_tokens: list = context.user_data.get("prev_tokens", [])
        idx = int(idx_str)
        if idx > 0:
            context.user_data["prev_tokens"] = prev_tokens[:idx]
            page_token = _get_page_token(context, prev_tokens[idx - 1])
        else:
            context.user_data["prev_tokens"] = []
            page_token = None
        await show_folder_picker(
            update, context, parent_id=parent_id, page_token=page_token, status_message=query.message
        )
