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

from handlers.commands import cancel_handler, start_handler, whoami_handler
from handlers.files import file_handler
from handlers.navigation import callback_handler

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
        authorized_user_ids = {int(uid.strip()) for uid in authorized_user_ids_str.split(",") if uid.strip()}
    except ValueError:
        raise RuntimeError("AUTHORIZED_USER_IDS must be comma-separated numeric Telegram user IDs")

    app = ApplicationBuilder().token(token).build()

    # Store authorized user IDs in bot_data so all handlers can access it
    app.bot_data["authorized_user_ids"] = authorized_user_ids

    # Command handlers
    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CommandHandler("cancel", cancel_handler))
    app.add_handler(CommandHandler("whoami", whoami_handler))

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

    logger.info("Bot starting. Authorized user IDs: %s", authorized_user_ids)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
