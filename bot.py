"""Entrypoint: builds the Telegram Application and registers all handlers."""
import asyncio
import logging
import os

from dotenv import load_dotenv
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from handlers.commands import (
    adduser_handler,
    cancel_handler,
    listusers_handler,
    recent_handler,
    removeuser_handler,
    search_handler,
    start_handler,
    stats_handler,
    whoami_handler,
)
from handlers.files import file_handler, new_folder_name_handler
from handlers.links import link_handler
from handlers.navigation import callback_handler
import history
import user_manager

load_dotenv()

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set in .env")

    authorized_user_ids_str = os.getenv("AUTHORIZED_USER_IDS", os.getenv("AUTHORIZED_USER_ID", ""))
    if not authorized_user_ids_str:
        raise RuntimeError("AUTHORIZED_USER_IDS is not set in .env")

    try:
        initial_ids = {int(uid.strip()) for uid in authorized_user_ids_str.split(",") if uid.strip()}
    except ValueError:
        raise RuntimeError("AUTHORIZED_USER_IDS must be comma-separated numeric Telegram user IDs")

    user_manager.load(initial_ids)
    history.load()

    owner_id = int(authorized_user_ids_str.split(",")[0].strip())

    builder = ApplicationBuilder().token(token)
    local_bot_api = os.getenv("LOCAL_BOT_API", "").lower() == "true"
    if local_bot_api:
        local_port = int(os.getenv("LOCAL_BOT_API_PORT", "8081"))
        builder = builder.base_url(f"http://127.0.0.1:{local_port}/bot").local_mode(True)
        logger.info("Using local Bot API server on port %d (2 GB upload limit)", local_port)
    app = builder.build()

    app.bot_data["authorized_user_ids"] = user_manager.get_all()
    app.bot_data["owner_id"] = owner_id

    # Commands
    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CommandHandler("cancel", cancel_handler))
    app.add_handler(CommandHandler("whoami", whoami_handler))
    app.add_handler(CommandHandler("recent", recent_handler))
    app.add_handler(CommandHandler("stats", stats_handler))
    app.add_handler(CommandHandler("search", search_handler))
    app.add_handler(CommandHandler("adduser", adduser_handler))
    app.add_handler(CommandHandler("removeuser", removeuser_handler))
    app.add_handler(CommandHandler("listusers", listusers_handler))

    # File handler
    file_filter = (
        filters.Document.ALL
        | filters.VIDEO
        | filters.PHOTO
        | filters.AUDIO
    )
    app.add_handler(MessageHandler(file_filter, file_handler))

    # Media link → download → upload (caught before the generic text handler)
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.Regex(r"https?://"),
        link_handler,
    ))

    # New folder name input (text while awaiting folder name)
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        new_folder_name_handler,
    ))

    # Inline keyboard callbacks
    app.add_handler(CallbackQueryHandler(callback_handler))

    logger.info("Bot starting. Owner: %d | Users: %s", owner_id, user_manager.get_all())
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
