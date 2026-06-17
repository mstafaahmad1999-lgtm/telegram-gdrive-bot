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
    removeuser_handler,
    start_handler,
    whoami_handler,
)
from handlers.files import file_handler
from handlers.navigation import callback_handler
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

    # Load persistent user store (merges env IDs with saved users.json)
    user_manager.load(initial_ids)

    # First ID in env is the owner
    owner_id = int(authorized_user_ids_str.split(",")[0].strip())

    app = ApplicationBuilder().token(token).build()

    app.bot_data["authorized_user_ids"] = user_manager.get_all()
    app.bot_data["owner_id"] = owner_id

    # Command handlers
    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CommandHandler("cancel", cancel_handler))
    app.add_handler(CommandHandler("whoami", whoami_handler))
    app.add_handler(CommandHandler("adduser", adduser_handler))
    app.add_handler(CommandHandler("removeuser", removeuser_handler))
    app.add_handler(CommandHandler("listusers", listusers_handler))

    # File handlers
    file_filter = (
        filters.Document.ALL
        | filters.VIDEO
        | filters.PHOTO
        | filters.AUDIO
    )
    app.add_handler(MessageHandler(file_filter, file_handler))

    # Inline keyboard callbacks
    app.add_handler(CallbackQueryHandler(callback_handler))

    logger.info("Bot starting. Owner ID: %d | Authorized users: %s", owner_id, user_manager.get_all())
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
