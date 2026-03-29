import asyncio
import json
import logging
import os

from dotenv import load_dotenv
from telegram import ReplyKeyboardMarkup, Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters


load_dotenv()
logging.basicConfig(
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


BUTTON_DEBUG_PRIVATE = "Debug Private Messages"
BUTTON_DEBUG_PUBLIC = "Debug Public Chats"
BUTTON_BACK = "Назад"

MODE_PRIVATE = "private"
MODE_PUBLIC = "public"


def get_required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Environment variable {name} is required")
    return value.strip()


BOT_TOKEN = get_required_env("TOKEN")


def main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [BUTTON_DEBUG_PRIVATE],
            [BUTTON_DEBUG_PUBLIC],
        ],
        resize_keyboard=True,
    )


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


def message_brief(message) -> dict | None:
    if message is None:
        return None
    return {
        "message_id": message.message_id,
        "chat_id": message.chat_id,
        "message_thread_id": getattr(message, "message_thread_id", None),
        "is_topic_message": getattr(message, "is_topic_message", None),
        "text": message.text,
        "text_html": message.text_html,
    }


def payload_from_update(update: Update) -> dict:
    message = update.effective_message
    chat = message.chat if message else None
    return {
        "chat_id": message.chat_id if message else None,
        "chat_type": chat.type if chat else None,
        "chat_title": chat.title if chat else None,
        "message_id": message.message_id if message else None,
        "message_thread_id": getattr(message, "message_thread_id", None),
        "is_topic_message": getattr(message, "is_topic_message", None),
        "from_user_id": message.from_user.id if message and message.from_user else None,
        "from_user_name": message.from_user.full_name if message and message.from_user else None,
        "text": message.text if message else None,
        "text_html": message.text_html if message else None,
        "caption": message.caption if message else None,
        "caption_html": message.caption_html if message else None,
        "entities": [entity_to_dict(item) for item in (message.entities or [])] if message else [],
        "caption_entities": [entity_to_dict(item) for item in (message.caption_entities or [])] if message else [],
        "reply_to_message": message_brief(message.reply_to_message) if message else None,
    }


def set_mode(context: ContextTypes.DEFAULT_TYPE, mode: str | None) -> None:
    if mode is None:
        context.user_data.pop("tools_mode", None)
        return
    context.user_data["tools_mode"] = mode


def get_mode(context: ContextTypes.DEFAULT_TYPE) -> str | None:
    return context.user_data.get("tools_mode")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # tools.py is intentionally isolated from app.py logic. It is a separate
    # one-off utility bot that reuses the same token for inspection tasks.
    set_mode(context, None)
    await update.effective_message.reply_text(
        "Tools mode.\nВыберите режим ниже.",
        reply_markup=main_keyboard(),
    )


async def debug_private_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    set_mode(context, MODE_PRIVATE)
    await update.effective_message.reply_text(
        "Режим личных сообщений включен. Отправьте сообщение боту в личку, и я верну payload.",
        reply_markup=debug_keyboard(),
    )


async def debug_public_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    set_mode(context, MODE_PUBLIC)
    await update.effective_message.reply_text(
        "Режим публичных чатов включен.\n"
        "Теперь отправьте или дождитесь сообщения в группе/супергруппе, где есть бот.\n"
        "Если бот в группе молчит, вероятно, у него включен privacy mode и он не видит обычные сообщения.",
        reply_markup=debug_keyboard(),
    )


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    set_mode(context, None)
    await update.effective_message.reply_text(
        "Вернул главное меню.",
        reply_markup=main_keyboard(),
    )


async def debug_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return

    if message.text == BUTTON_BACK:
        await cancel(update, context)
        return

    mode = get_mode(context)
    if mode == MODE_PRIVATE:
        if update.effective_chat and update.effective_chat.type != "private":
            return
        await message.reply_text(
            json.dumps(payload_from_update(update), ensure_ascii=False, indent=2),
            reply_markup=debug_keyboard(),
        )
        return

    if mode == MODE_PUBLIC:
        if update.effective_chat and update.effective_chat.type == "private":
            await message.reply_text(
                "Сейчас включен режим публичных чатов. Пришлите сообщение из группы или супергруппы.",
                reply_markup=debug_keyboard(),
            )
            return
        await message.reply_text(
            json.dumps(payload_from_update(update), ensure_ascii=False, indent=2),
            reply_markup=debug_keyboard(),
        )


def build_application() -> Application:
    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.Regex(f"^{BUTTON_DEBUG_PRIVATE}$"), debug_private_start))
    application.add_handler(MessageHandler(filters.Regex(f"^{BUTTON_DEBUG_PUBLIC}$"), debug_public_start))
    application.add_handler(MessageHandler(filters.Regex(f"^{BUTTON_BACK}$"), cancel))
    application.add_handler(CommandHandler("cancel", cancel))
    application.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, debug_router))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, start))
    return application


def main() -> None:
    asyncio.set_event_loop(asyncio.new_event_loop())
    application = build_application()
    application.run_polling()


if __name__ == "__main__":
    main()
