"""Folder navigation keyboard, upload execution, file listing, and folder creation."""
import asyncio
import logging
import os
import uuid

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

import drive_service
import history
import state
import user_manager

_MANAGE_PREFIXES = ("fileinfo:", "renamefile:", "movefile:", "movehere:", "drivedelete:")

logger = logging.getLogger(__name__)

LABEL_MAX = 28


def _truncate(name: str, max_len: int = LABEL_MAX) -> str:
    return name if len(name) <= max_len else name[: max_len - 1] + "…"


def _disambiguate(folders: list[dict]) -> list[dict]:
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
    store: dict = context.bot_data.setdefault("page_tokens", {})
    key = uuid.uuid4().hex[:8]
    store[key] = token
    return key


def _get_page_token(context: ContextTypes.DEFAULT_TYPE, key: str) -> str | None:
    return context.bot_data.get("page_tokens", {}).get(key)


def _format_size(size_bytes: int) -> str:
    if size_bytes >= 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    if size_bytes >= 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes} B"


def _progress_bar(pct: int) -> str:
    filled = int(pct / 10)
    bar = "⬛" * filled + "⬜" * (10 - filled)
    return f"{bar} {pct}%"


async def show_folder_picker(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    parent_id: str,
    page_token: str | None = None,
    status_message=None,
    mode: str = "upload",
) -> None:
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
    is_owner = update.effective_user.id == context.bot_data.get("owner_id")

    buttons: list[list[InlineKeyboardButton]] = []

    # Upload/Move here + Back row
    if mode == "move":
        action_row = [InlineKeyboardButton("📁 Move here", callback_data=f"movehere:{parent_id}")]
    else:
        action_row = [InlineKeyboardButton("✅ Upload here", callback_data=f"upload:{parent_id}")]
    if len(nav_stack) > 1:
        action_row.append(InlineKeyboardButton("⬅️ Back", callback_data=f"back:{parent_id}"))
    buttons.append(action_row)

    # List files + New folder row (owner only for new folder)
    extra_row = [InlineKeyboardButton("📋 List files", callback_data=f"listfiles:{parent_id}")]
    if is_owner:
        extra_row.append(InlineKeyboardButton("➕ New folder", callback_data=f"newfolder:{parent_id}"))
    buttons.append(extra_row)

    # Folder buttons
    disambiguated = _disambiguate(folders)
    for f in disambiguated:
        label = _truncate(f["label"])
        buttons.append([InlineKeyboardButton(f"📁 {label}", callback_data=f"nav:{f['id']}")])

    # Pagination
    prev_tokens: list[str] = context.user_data.get("prev_tokens", [])
    pagination_row: list[InlineKeyboardButton] = []
    if prev_tokens:
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


async def show_file_list(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    parent_id: str,
    page_token: str | None = None,
) -> None:
    query = update.callback_query
    is_owner = update.effective_user.id == context.bot_data.get("owner_id")

    try:
        files, next_token = drive_service.list_files(parent_id, page_token)
    except Exception as exc:
        await query.edit_message_text(f"❌ Could not list files: {exc}")
        return

    if not files:
        buttons = [[InlineKeyboardButton("⬅️ Back to folders", callback_data=f"nav:{parent_id}")]]
        await query.edit_message_text(
            "📭 No files in this folder.",
            reply_markup=InlineKeyboardMarkup(buttons),
        )
        return

    buttons = []
    lines = ["📋 *Files in this folder:*\n"]
    for f in files:
        name = _truncate(f.get("name", "unknown"), 25)
        size = _format_size(int(f.get("size", 0) or 0))
        link = f.get("webViewLink", "")
        lines.append(f"• [{name}]({link}) — {size}")
        if is_owner:
            context.user_data[f"fp_{f['id'][-8:]}"] = parent_id
            buttons.append([
                InlineKeyboardButton(f"🗑 Delete: {_truncate(f.get('name','?'), 20)}", callback_data=f"deletefile:{f['id']}")
            ])

    # Pagination
    pagination_row = []
    if next_token:
        key = _store_page_token(context, next_token)
        pagination_row.append(InlineKeyboardButton("Next ▶️", callback_data=f"listpage:{parent_id}:{key}"))
    if pagination_row:
        buttons.append(pagination_row)

    buttons.append([InlineKeyboardButton("⬅️ Back to folders", callback_data=f"nav:{parent_id}")])

    await query.edit_message_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode="Markdown",
        disable_web_page_preview=True,
    )


async def do_upload(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    folder_id: str,
    pending_override=None,
) -> None:
    query = update.callback_query
    user_id = update.effective_user.id
    owner_id = context.bot_data.get("owner_id")
    pending = pending_override or state.get_pending(user_id)

    if not pending:
        await query.edit_message_text("❌ No pending file found. Please send the file again.")
        return

    folder_name = drive_service.get_folder_name(folder_id)
    await query.edit_message_text(f"⏳ Uploading…\n{_progress_bar(0)}")

    loop = asyncio.get_event_loop()
    last_edit = {"pct": -1}

    def progress_callback(pct: int) -> None:
        if pct != last_edit["pct"] and pct % 10 == 0:
            last_edit["pct"] = pct
            asyncio.run_coroutine_threadsafe(
                query.edit_message_text(f"⏳ Uploading…\n{_progress_bar(pct)}"),
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

    name = file_resource.get("name", pending.file_name)
    size_str = _format_size(int(file_resource.get("size", pending.file_size) or pending.file_size))
    link = file_resource.get("webViewLink", "")

    await query.edit_message_text(
        f"✅ *Uploaded successfully!*\n\n"
        f"📄 *{name}*\n"
        f"📦 Size: {size_str}\n"
        f"📁 Folder: {folder_name}\n"
        f"🔗 [Open in Google Drive]({link})",
        parse_mode="Markdown",
        disable_web_page_preview=True,
    )

    # Save to history
    history.add(user_id, name, int(file_resource.get("size", pending.file_size) or 0), folder_name, link, file_resource.get("id", ""))

    # Notify owner if uploaded by a friend
    if user_id != owner_id and owner_id:
        try:
            await context.bot.send_message(
                owner_id,
                f"📤 *Friend upload!*\n"
                f"👤 User ID: `{user_id}`\n"
                f"📄 File: *{name}*\n"
                f"📦 Size: {size_str}\n"
                f"📁 Folder: {folder_name}\n"
                f"🔗 [Open in Drive]({link})",
                parse_mode="Markdown",
                disable_web_page_preview=True,
            )
        except Exception as exc:
            logger.warning("Could not notify owner: %s", exc)

    # Cleanup
    try:
        os.unlink(pending.file_path)
    except OSError as exc:
        logger.warning("Could not delete temp file %s: %s", pending.file_path, exc)

    if not pending_override:
        state.clear_pending(user_id)
    context.user_data.pop("nav_stack", None)
    context.user_data.pop("prev_tokens", None)


async def do_upload_album(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    folder_id: str,
    files: list,
) -> None:
    query = update.callback_query
    user_id = update.effective_user.id
    owner_id = context.bot_data.get("owner_id")
    folder_name = drive_service.get_folder_name(folder_id)
    total = len(files)

    await query.edit_message_text(f"⏳ Uploading {total} files…\n{_progress_bar(0)}")

    loop = asyncio.get_event_loop()
    uploaded = []
    failed = []

    for idx, pending in enumerate(files):
        overall_pct = int((idx / total) * 100)
        await query.edit_message_text(
            f"⏳ Uploading file {idx + 1}/{total}…\n{_progress_bar(overall_pct)}"
        )
        try:
            file_resource = await loop.run_in_executor(
                None,
                lambda p=pending: drive_service.upload_file(
                    p.file_path, p.file_name, p.mime_type, folder_id
                ),
            )
            uploaded.append(file_resource)
            history.add(
                user_id,
                file_resource.get("name", pending.file_name),
                int(file_resource.get("size", pending.file_size) or 0),
                folder_name,
                file_resource.get("webViewLink", ""),
                file_resource.get("id", ""),
            )
            try:
                os.unlink(pending.file_path)
            except OSError:
                pass
        except Exception as exc:
            logger.error("Album upload failed for %s: %s", pending.file_name, exc)
            failed.append(pending.file_name)

    lines = [f"✅ *Uploaded {len(uploaded)}/{total} files to {folder_name}*\n"]
    for r in uploaded:
        link = r.get("webViewLink", "")
        name = r.get("name", "?")
        size = _format_size(int(r.get("size", 0) or 0))
        lines.append(f"• [{_truncate(name)}]({link}) — {size}")
    if failed:
        lines.append(f"\n❌ Failed: {', '.join(failed)}")

    await query.edit_message_text(
        "\n".join(lines),
        parse_mode="Markdown",
        disable_web_page_preview=True,
    )

    # Notify owner
    if user_id != owner_id and owner_id and uploaded:
        try:
            await context.bot.send_message(
                owner_id,
                f"📤 *Friend uploaded {len(uploaded)} files!*\n"
                f"👤 User ID: `{user_id}`\n"
                f"📁 Folder: {folder_name}",
                parse_mode="Markdown",
            )
        except Exception:
            pass

    state.clear_album(user_id)
    context.user_data.pop("nav_stack", None)
    context.user_data.pop("prev_tokens", None)


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    authorized_ids: set = context.bot_data["authorized_user_ids"]
    user = update.effective_user
    if user is None or not user_manager.is_authorized(user.id):
        await update.callback_query.answer("Not authorized.")
        return

    query = update.callback_query
    await query.answer()
    data: str = query.data or ""
    is_owner = user.id == context.bot_data.get("owner_id")

    if data.startswith("nav:"):
        folder_id = data[4:]
        folder_name = drive_service.get_folder_name(folder_id)
        nav_stack: list = context.user_data.setdefault("nav_stack", [("root", "My Drive")])
        nav_stack.append((folder_id, folder_name))
        context.user_data["prev_tokens"] = []
        await show_folder_picker(update, context, parent_id=folder_id, status_message=query.message)

    elif data.startswith("upload:"):
        folder_id = data[7:]
        album = state.get_album(user.id)
        if album and album.files:
            if album.timer_task:
                album.timer_task.cancel()
            await do_upload_album(update, context, folder_id, album.files)
        else:
            await do_upload(update, context, folder_id)

    elif data.startswith("back:"):
        nav_stack: list = context.user_data.get("nav_stack", [("root", "My Drive")])
        if len(nav_stack) > 1:
            nav_stack.pop()
        parent_id, _ = nav_stack[-1]
        context.user_data["prev_tokens"] = []
        await show_folder_picker(update, context, parent_id=parent_id, status_message=query.message)

    elif data.startswith("page:"):
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
        await show_folder_picker(update, context, parent_id=parent_id, page_token=page_token, status_message=query.message)

    elif data.startswith("prevpage:"):
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
        await show_folder_picker(update, context, parent_id=parent_id, page_token=page_token, status_message=query.message)

    elif data.startswith("listfiles:"):
        folder_id = data[10:]
        await show_file_list(update, context, folder_id)

    elif data.startswith("listpage:"):
        parts = data.split(":", 2)
        if len(parts) != 3:
            return
        _, parent_id, token_key = parts
        page_token = _get_page_token(context, token_key)
        await show_file_list(update, context, parent_id, page_token)

    elif data.startswith("newfolder:") and is_owner:
        parent_id = data[10:]
        context.user_data["awaiting_folder_name"] = parent_id
        await query.edit_message_text(
            "📁 *Create new folder*\n\nReply with the folder name:",
            parse_mode="Markdown",
        )

    elif data.startswith("deletefile:") and is_owner:
        file_id = data[11:]
        parent_id = context.user_data.get(f"fp_{file_id[-8:]}", "root")
        try:
            await asyncio.get_event_loop().run_in_executor(
                None, lambda: drive_service.delete_file(file_id)
            )
            await query.edit_message_text("🗑 File deleted successfully.")
            await asyncio.sleep(1)
            await show_file_list(update, context, parent_id)
        except Exception as exc:
            await query.edit_message_text(f"❌ Could not delete file: {exc}")

    elif data.startswith("fileinfo:"):
        file_id = data[9:]
        loop = asyncio.get_event_loop()
        try:
            info = await loop.run_in_executor(None, lambda: drive_service.get_file_info(file_id))
        except Exception as exc:
            await query.edit_message_text(f"❌ Could not get file info: {exc}")
            return
        name = info.get("name", "?")
        size = _format_size(int(info.get("size", 0) or 0))
        link = info.get("webViewLink", "")
        parents = info.get("parents", ["root"])
        old_parent = parents[0] if parents else "root"

        # Store old_parent so movefile: callback_data stays under 64 bytes
        context.user_data[f"fp_{file_id[-8:]}"] = old_parent

        buttons = []
        if is_owner:
            buttons.append([InlineKeyboardButton("✏️ Rename", callback_data=f"renamefile:{file_id}")])
            buttons.append([InlineKeyboardButton("📁 Move to folder", callback_data=f"movefile:{file_id}")])
            buttons.append([InlineKeyboardButton("🗑 Delete from Drive", callback_data=f"drivedelete:{file_id}")])
        if link:
            buttons.append([InlineKeyboardButton("🔗 Open in Drive", url=link)])

        await query.edit_message_text(
            f"📄 *{name}*\n📦 {size}\n\nWhat do you want to do?",
            reply_markup=InlineKeyboardMarkup(buttons),
            parse_mode="Markdown",
        )

    elif data.startswith("renamefile:") and is_owner:
        file_id = data[11:]
        state.set_file_action(user.id, {"action": "rename", "file_id": file_id})
        await query.edit_message_text(
            "✏️ *Rename file*\n\nReply with the new name:",
            parse_mode="Markdown",
        )

    elif data.startswith("movefile:") and is_owner:
        file_id = data[9:]
        old_parent = context.user_data.get(f"fp_{file_id[-8:]}", "root")
        state.set_file_action(user.id, {"action": "move", "file_id": file_id, "old_parent": old_parent})
        context.user_data["nav_stack"] = [("root", "My Drive")]
        context.user_data["prev_tokens"] = []
        await show_folder_picker(update, context, parent_id="root", status_message=query.message, mode="move")

    elif data.startswith("movehere:") and is_owner:
        new_parent_id = data[9:]
        action = state.get_file_action(user.id)
        if not action or action.get("action") != "move":
            await query.edit_message_text("❌ No file selected to move.")
            return
        file_id = action["file_id"]
        old_parent = action["old_parent"]
        state.clear_file_action(user.id)
        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(
                None, lambda: drive_service.move_file(file_id, new_parent_id, old_parent)
            )
            folder_name = drive_service.get_folder_name(new_parent_id)
            await query.edit_message_text(
                f"✅ File moved to *{folder_name}*",
                parse_mode="Markdown",
            )
        except Exception as exc:
            await query.edit_message_text(f"❌ Move failed: {exc}")

    elif data.startswith("drivedelete:") and is_owner:
        file_id = data[12:]
        try:
            await asyncio.get_event_loop().run_in_executor(
                None, lambda: drive_service.delete_file(file_id)
            )
            await query.edit_message_text("🗑 File deleted from Google Drive.")
        except Exception as exc:
            await query.edit_message_text(f"❌ Delete failed: {exc}")
