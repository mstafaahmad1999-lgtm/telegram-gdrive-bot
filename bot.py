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

    authorized_user_id_str = os.getenv("AUTHORIZED_USER_ID")
    if not authorized_user_id_str:
        raise RuntimeError("AUTHORIZED_USER_ID is not set in .env")

    try:
        authorized_user_id = int(authorized_user_id_str)
    except ValueError:
        raise RuntimeError("AUTHORIZED_USER_ID must be a numeric Telegram user ID")

    app = ApplicationBuilder().token(token).build()

    # Store authorized user ID in bot_data so all handlers can access it
    app.bot_data["authorized_user_id"] = authorized_user_id

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

    logger.info("Bot starting. Authorized user ID: %d", authorized_user_id)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
