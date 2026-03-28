import asyncio
import json
import logging
import os

from dotenv import load_dotenv
from telegram import ReplyKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)


load_dotenv()
logging.basicConfig(
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


DEBUG_INPUT = 1

BUTTON_DEBUG_INPUT = "Debug Input"
BUTTON_BACK = "Назад"


def get_required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Environment variable {name} is required")
    return value.strip()


BOT_TOKEN = get_required_env("TOKEN")


def main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup([[BUTTON_DEBUG_INPUT]], resize_keyboard=True)


def debug_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup([[BUTTON_BACK]], resize_keyboard=True)


def entity_to_dict(entity) -> dict:
    data = {
        "type": entity.type,
        "offset": entity.offset,
        "length": entity.length,
    }
    if getattr(entity, "custom_emoji_id", None):
        data["custom_emoji_id"] = entity.custom_emoji_id
    if getattr(entity, "url", None):
        data["url"] = entity.url
    if getattr(entity, "language", None):
        data["language"] = entity.language
    return data


def payload_from_update(update: Update) -> dict:
    message = update.effective_message
    return {
        "chat_id": message.chat_id,
        "from_user_id": message.from_user.id if message.from_user else None,
        "from_user_name": message.from_user.full_name if message.from_user else None,
        "text": message.text,
        "text_html": message.text_html,
        "caption": message.caption,
        "caption_html": message.caption_html,
        "entities": [entity_to_dict(item) for item in (message.entities or [])],
        "caption_entities": [entity_to_dict(item) for item in (message.caption_entities or [])],
    }


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Tools mode.\nНажмите кнопку ниже.",
        reply_markup=main_keyboard(),
    )


async def debug_input_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        "Отправьте сообщение, эмодзи или кастомный emoji. Я верну всю полезную информацию.",
        reply_markup=debug_keyboard(),
    )
    return DEBUG_INPUT


async def debug_input_receive(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message.text == BUTTON_BACK:
        await update.message.reply_text(
            "Вернул главное меню.",
            reply_markup=main_keyboard(),
        )
        return ConversationHandler.END

    payload = payload_from_update(update)
    await update.message.reply_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        reply_markup=debug_keyboard(),
    )
    return DEBUG_INPUT


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        "Вернул главное меню.",
        reply_markup=main_keyboard(),
    )
    return ConversationHandler.END


def build_application() -> Application:
    application = Application.builder().token(BOT_TOKEN).build()

    debug_conversation = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            MessageHandler(filters.Regex(f"^{BUTTON_DEBUG_INPUT}$"), debug_input_start),
        ],
        states={
            DEBUG_INPUT: [MessageHandler(filters.ALL & ~filters.COMMAND, debug_input_receive)],
        },
        fallbacks=[
            MessageHandler(filters.Regex(f"^{BUTTON_BACK}$"), cancel),
            CommandHandler("cancel", cancel),
        ],
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(debug_conversation)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, start))
    return application


def main() -> None:
    asyncio.set_event_loop(asyncio.new_event_loop())
    application = build_application()
    application.run_polling()


if __name__ == "__main__":
    main()
