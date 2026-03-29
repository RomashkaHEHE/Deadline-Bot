import asyncio
import json
import logging
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Message, ReplyKeyboardMarkup, Update
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

import bot_messages as msg


logging.basicConfig(
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    level=logging.INFO,
)
LOGGER = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
STORAGE_PATH = Path(os.getenv("DEADLINES_STORAGE_PATH", BASE_DIR / "deadlines.json")).expanduser()

CREATE_DESCRIPTION, CREATE_DATETIME, CREATE_CONFIRM = range(3)
CANCEL_SELECT = 10
DELETE_SELECT = 11
REMIND_SELECT = 12
EDIT_SELECT, EDIT_DESCRIPTION, EDIT_DATETIME, EDIT_CONFIRM = range(20, 24)

IMMEDIATE_CALLBACK = "immediate_publish"
EDIT_IMMEDIATE_CALLBACK = "edit_immediate_publish"

BUTTON_NEW = msg.BUTTON_NEW
BUTTON_LIST = msg.BUTTON_LIST
BUTTON_ARCHIVE = msg.BUTTON_ARCHIVE
BUTTON_EDIT = msg.BUTTON_EDIT
BUTTON_CANCEL_DEADLINE = msg.BUTTON_CANCEL_DEADLINE
BUTTON_DELETE_DEADLINE = msg.BUTTON_DELETE_DEADLINE
BUTTON_REMIND = msg.BUTTON_REMIND
BUTTON_REFRESH_POSTS = msg.BUTTON_REFRESH_POSTS
BUTTON_SKIP = msg.BUTTON_SKIP
BUTTON_ABORT = msg.BUTTON_ABORT

BOT_TIMEZONE = timezone(timedelta(hours=5))
BOT_TIMEZONE_LABEL = "UTC+5"

STATUS_ACTIVE = "active"
STATUS_CANCELLED = "cancelled"
STATUS_COMPLETED = "completed"
STATUS_ARCHIVED = "archived"


@dataclass
class ChannelMessageRecord:
    message_id: int
    text: str
    parse_mode: str | None
    kind: str
    created_at: str
    # Structured data needed to rebuild this message with a newer template.
    # Legacy records may have it empty; such posts are skipped during refresh.
    template_data: dict = field(default_factory=dict)


@dataclass
class Deadline:
    # We keep both plain text and Telegram-renderable HTML so that the bot can
    # preserve formatting from the original user message in channel posts.
    id: int
    description: str
    description_html: str
    deadline_at: str
    time_was_provided: bool
    time_was_explicit_midnight: bool
    created_by: int
    created_by_name: str
    created_at: str
    immediate_publish_skipped: bool = False
    initial_published: bool = False
    reminded_7d: bool = False
    reminded_24h: bool = False
    status: str = STATUS_ACTIVE
    cleanup_after: str | None = None
    archived_at: str | None = None
    channel_messages: list[ChannelMessageRecord] = field(default_factory=list)

    @property
    def deadline_datetime(self) -> datetime:
        return ensure_bot_timezone(datetime.fromisoformat(self.deadline_at))

    @property
    def cleanup_after_datetime(self) -> datetime | None:
        if not self.cleanup_after:
            return None
        return ensure_bot_timezone(datetime.fromisoformat(self.cleanup_after))


class DeadlineStore:
    # JSON file storage is the only persistence layer in this project.
    # Schema changes should stay explicit and usually come with a one-time
    # migration of the existing JSON file rather than permanent fallback code.
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = asyncio.Lock()
        self._deadlines: list[Deadline] = []
        self._next_id = 1
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        self._next_id = raw.get("next_id", 1)
        deadlines: list[Deadline] = []
        for item in raw.get("deadlines", []):
            messages = [ChannelMessageRecord(**record) for record in item.get("channel_messages", [])]
            payload = dict(item)
            payload["channel_messages"] = messages
            deadlines.append(Deadline(**payload))
        self._deadlines = deadlines

    async def _save(self) -> None:
        payload = {
            "next_id": self._next_id,
            "deadlines": [asdict(item) for item in self._deadlines],
        }
        self.path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    async def add(self, deadline: Deadline) -> Deadline:
        async with self._lock:
            deadline.id = self._next_id
            self._next_id += 1
            self._deadlines.append(deadline)
            await self._save()
            return deadline

    async def update(self, deadline: Deadline) -> None:
        async with self._lock:
            for index, item in enumerate(self._deadlines):
                if item.id == deadline.id:
                    self._deadlines[index] = deadline
                    await self._save()
                    return
            raise KeyError(f"Deadline {deadline.id} not found")

    def get(self, deadline_id: int) -> Deadline | None:
        for item in self._deadlines:
            if item.id == deadline_id:
                return item
        return None

    def list_active(self) -> list[Deadline]:
        items = [item for item in self._deadlines if item.status == STATUS_ACTIVE]
        return sorted(items, key=lambda item: item.deadline_datetime)

    def list_all(self) -> list[Deadline]:
        return sorted(self._deadlines, key=lambda item: item.deadline_datetime)

    def list_archive(self) -> list[Deadline]:
        items = [item for item in self._deadlines if item.status != STATUS_ACTIVE]
        return sorted(items, key=lambda item: item.deadline_datetime, reverse=True)


STORE = DeadlineStore(STORAGE_PATH)


def ensure_bot_timezone(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=BOT_TIMEZONE)
    return value.astimezone(BOT_TIMEZONE)


def bot_now() -> datetime:
    return datetime.now(BOT_TIMEZONE)


def get_required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Environment variable {name} is required")
    return value.strip()


def get_optional_int_env(name: str) -> int | None:
    value = os.getenv(name)
    if not value:
        return None
    return int(value.strip())


BOT_TOKEN = get_required_env("TOKEN")
CHANNEL_ID = get_required_env("CHANNEL_ID")
CHANNEL_THREAD_ID = get_optional_int_env("CHANNEL_THREAD_ID")
WHITELIST_USER_IDS = {
    int(item.strip())
    for item in os.getenv("WHITELIST_USER_IDS", "").split(",")
    if item.strip()
}


def is_allowed(update: Update) -> bool:
    user = update.effective_user
    return bool(user and user.id in WHITELIST_USER_IDS)


def main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [BUTTON_NEW, BUTTON_EDIT],
            [BUTTON_LIST, BUTTON_ARCHIVE],
            [BUTTON_CANCEL_DEADLINE, BUTTON_DELETE_DEADLINE],
            [BUTTON_REMIND, BUTTON_REFRESH_POSTS],
        ],
        resize_keyboard=True,
    )


def input_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup([[BUTTON_ABORT]], resize_keyboard=True)


def edit_input_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup([[BUTTON_SKIP], [BUTTON_ABORT]], resize_keyboard=True)


async def reply(
    message: Message,
    template: msg.MessageTemplate,
    *,
    reply_markup: ReplyKeyboardMarkup | InlineKeyboardMarkup | None = None,
) -> None:
    await message.reply_text(
        template.text,
        parse_mode=template.parse_mode,
        reply_markup=reply_markup,
    )


async def require_whitelist(update: Update) -> bool:
    if is_allowed(update):
        return True

    message = update.effective_message
    if message:
        await reply(message, msg.access_denied())
    return False

def format_deadline_line(deadline_at: datetime, time_was_provided: bool) -> str:
    dt = ensure_bot_timezone(deadline_at)
    date_part = dt.strftime("%d.%m.%Y")
    if time_was_provided:
        return f"{date_part} {dt.strftime('%H:%M')}"
    return date_part


def format_deadline(deadline: Deadline) -> str:
    return f"{deadline.description}\n{format_deadline_line(deadline.deadline_datetime, deadline.time_was_provided)}"


def deadline_context(deadline: Deadline) -> dict:
    # Templates in bot_messages.py work with a precomputed render context so the
    # message file stays declarative and does not need to know model internals.
    line = format_deadline_line(deadline.deadline_datetime, deadline.time_was_provided)
    status_map = {
        STATUS_ACTIVE: "активный",
        STATUS_CANCELLED: "отменённый",
        STATUS_COMPLETED: "завершённый",
        STATUS_ARCHIVED: "архивный",
    }
    return {
        "id": deadline.id,
        "description": deadline.description,
        "description_html": deadline.description_html,
        "deadline_line": line,
        "deadline_line_html": escape(line),
        "formatted_deadline": format_deadline(deadline),
        "formatted_deadline_html": escape(format_deadline(deadline)),
        "created_by_name": deadline.created_by_name,
        "created_by_name_html": escape(deadline.created_by_name),
        "status": deadline.status,
        "status_label": status_map.get(deadline.status, deadline.status),
        "status_label_html": escape(status_map.get(deadline.status, deadline.status)),
        "timezone_label": BOT_TIMEZONE_LABEL,
        "deadline_iso": deadline.deadline_at,
        "cleanup_after_iso": deadline.cleanup_after,
    }


def remaining_message_context(deadline_at: datetime) -> dict:
    remaining = max(now_until(deadline_at), timedelta())
    total_seconds = int(remaining.total_seconds())

    if remaining > timedelta(days=1):
        value = max(1, total_seconds // 86400)
        label = "Осталось дней"
    elif remaining >= timedelta(hours=2):
        value = max(1, total_seconds // 3600)
        label = "Осталось часов"
    else:
        value = max(1, total_seconds // 60)
        label = "Осталось минут"

    return {
        "remaining_value": value,
        "remaining_label": label,
        "remaining_label_html": escape(label),
    }


def live_deadline_context(deadline: Deadline) -> dict:
    context = deadline_context(deadline)
    context.update(remaining_message_context(deadline.deadline_datetime))
    return context


def live_deadline_context_from_payload(payload: dict) -> dict:
    context = dict(payload)
    deadline_at = ensure_bot_timezone(datetime.fromisoformat(payload["deadline_iso"]))
    context.update(remaining_message_context(deadline_at))
    return context


def build_changes(old_deadline: Deadline, new_deadline: Deadline) -> list[dict]:
    changes: list[dict] = []
    if old_deadline.description != new_deadline.description:
        changes.append(
            {
                "field": "description",
                "old": old_deadline.description,
                "new": new_deadline.description,
                "old_html": old_deadline.description_html,
                "new_html": new_deadline.description_html,
            }
        )

    old_line = format_deadline_line(old_deadline.deadline_datetime, old_deadline.time_was_provided)
    new_line = format_deadline_line(new_deadline.deadline_datetime, new_deadline.time_was_provided)
    if old_line != new_line:
        changes.append(
            {
                "field": "deadline",
                "old": old_line,
                "new": new_line,
                "old_html": escape(old_line),
                "new_html": escape(new_line),
            }
        )
    return changes


async def maybe_handle_menu_navigation(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> tuple[bool, int | None]:
    message = update.effective_message
    if message is None or not message.text:
        return False, None

    text = message.text.strip()
    if text == BUTTON_ABORT:
        return True, await abort_conversation(update, context)
    if text == BUTTON_NEW:
        return True, await create_start(update, context)
    if text == BUTTON_LIST:
        await list_deadlines(update, context)
        return True, ConversationHandler.END
    if text == BUTTON_ARCHIVE:
        await list_archive(update, context)
        return True, ConversationHandler.END
    if text == BUTTON_EDIT:
        return True, await edit_start(update, context)
    if text == BUTTON_CANCEL_DEADLINE:
        return True, await cancel_deadline_start(update, context)
    if text == BUTTON_DELETE_DEADLINE:
        return True, await delete_deadline_start(update, context)
    if text == BUTTON_REMIND:
        return True, await remind_deadline_start(update, context)
    if text == BUTTON_REFRESH_POSTS:
        await refresh_channel_posts(update, context)
        return True, ConversationHandler.END
    return False, None


def parse_deadline_input(raw_text: str) -> tuple[datetime, bool, bool]:
    parts = raw_text.split()
    if len(parts) not in (1, 2):
        raise ValueError(msg.invalid_datetime_format().text)

    now = bot_now()
    if not re.fullmatch(r"\d{2}\.\d{2}\.\d{4}", parts[0]):
        raise ValueError(msg.invalid_date_format().text)
    try:
        date_value = datetime.strptime(parts[0], "%d.%m.%Y")
    except ValueError as exc:
        raise ValueError(msg.invalid_date_value().text) from exc

    deadline_date = date_value.replace(tzinfo=BOT_TIMEZONE)

    time_was_provided = len(parts) == 2
    time_was_explicit_midnight = False

    if time_was_provided:
        if not re.fullmatch(r"\d{2}:\d{2}", parts[1]):
            raise ValueError(msg.invalid_time_format().text)
        try:
            time_value = datetime.strptime(parts[1], "%H:%M")
        except ValueError as exc:
            raise ValueError(msg.invalid_time_value().text) from exc
        deadline_date = deadline_date.replace(hour=time_value.hour, minute=time_value.minute)
        time_was_explicit_midnight = parts[1] == "00:00"
    else:
        deadline_date = deadline_date.replace(hour=0, minute=0)

    deadline_date = deadline_date.replace(second=0, microsecond=0)
    if deadline_date <= now:
        raise ValueError(msg.deadline_must_be_future().text)
    return deadline_date, time_was_provided, time_was_explicit_midnight


def now_until(deadline_at: datetime) -> timedelta:
    return ensure_bot_timezone(deadline_at) - bot_now()


def deadline_template_data(deadline: Deadline) -> dict:
    return {"deadline": deadline_context(deadline)}


def live_deadline_template_data(deadline: Deadline) -> dict:
    return {"deadline": live_deadline_context(deadline)}


def changed_template_data(changes: list[dict], old_deadline: dict, new_deadline: dict) -> dict:
    return {
        "changes": changes,
        "old_deadline": old_deadline,
        "new_deadline": new_deadline,
    }


def render_channel_template(record: ChannelMessageRecord) -> msg.MessageTemplate:
    # Refreshing channel posts must use the same structured payload that was
    # stored when the post was first created, otherwise old reminders/changes
    # could silently turn into messages about the current state instead.
    data = record.template_data
    if not data:
        raise ValueError("legacy channel message without template data")

    if record.kind in {"initial", "reminder_7d", "reminder_24h", "reminder_manual"}:
        return msg.active_deadline_post(live_deadline_context_from_payload(data["deadline"]))
    if record.kind == "cancelled":
        return msg.deadline_cancelled_post(data["deadline"])
    if record.kind == "completed":
        return msg.deadline_completed_post(data["deadline"])
    if record.kind == "changed":
        return msg.deadline_changed_post(data["changes"], data["old_deadline"], data["new_deadline"])
    raise ValueError(f"unknown channel message kind: {record.kind}")


async def post_channel_template(
    context: ContextTypes.DEFAULT_TYPE,
    deadline: Deadline,
    template: msg.MessageTemplate,
    *,
    kind: str,
    template_data: dict,
) -> Message:
    # Every deadline-related channel post must go through this helper so we can
    # later edit or delete the full message history for that deadline.
    # We also persist template_data here, because retroactive refresh must
    # rebuild old posts from structured data rather than by mutating raw text.
    sent = await context.bot.send_message(
        chat_id=CHANNEL_ID,
        text=template.text,
        parse_mode=template.parse_mode,
        message_thread_id=CHANNEL_THREAD_ID,
    )
    deadline.channel_messages.append(
        ChannelMessageRecord(
            message_id=sent.message_id,
            text=template.text,
            parse_mode=template.parse_mode,
            kind=kind,
            created_at=bot_now().isoformat(),
            template_data=template_data,
        )
    )
    await STORE.update(deadline)
    return sent


async def publish_live_deadline_post(
    context: ContextTypes.DEFAULT_TYPE,
    deadline: Deadline,
    *,
    kind: str,
    replace_existing: bool,
) -> None:
    # The channel should keep only one "current state" post for an active
    # deadline. Auto reminders and manual reminders therefore replace older
    # posts instead of appending another copy of the same deadline.
    if replace_existing:
        await delete_all_deadline_messages(context, deadline)

    template_data = live_deadline_template_data(deadline)
    await post_channel_template(
        context,
        deadline,
        msg.active_deadline_post(template_data["deadline"]),
        kind=kind,
        template_data=template_data,
    )
    deadline.initial_published = True
    await STORE.update(deadline)


async def delete_all_deadline_messages(context: ContextTypes.DEFAULT_TYPE, deadline: Deadline) -> None:
    for record in list(deadline.channel_messages):
        try:
            await context.bot.delete_message(chat_id=CHANNEL_ID, message_id=record.message_id)
        except Exception as exc:
            LOGGER.warning("Failed to delete message %s for deadline %s: %s", record.message_id, deadline.id, exc)
    deadline.channel_messages = []
    await STORE.update(deadline)


async def mark_deadline_completed(context: ContextTypes.DEFAULT_TYPE, deadline: Deadline) -> None:
    if deadline.status != STATUS_ACTIVE:
        return

    deadline.status = STATUS_COMPLETED
    deadline.cleanup_after = (bot_now() + timedelta(days=3)).isoformat()
    completed_template_data = deadline_template_data(deadline)

    if deadline.channel_messages:
        # Product choice: completion does not create a new post. Instead we edit
        # the latest deadline-related channel message and schedule cleanup.
        last_record = deadline.channel_messages[-1]
        template = msg.deadline_completed_post(completed_template_data["deadline"])
        try:
            await context.bot.edit_message_text(
                chat_id=CHANNEL_ID,
                message_id=last_record.message_id,
                text=template.text,
                parse_mode=template.parse_mode,
            )
            last_record.text = template.text
            last_record.parse_mode = template.parse_mode
            last_record.kind = "completed"
            last_record.template_data = completed_template_data
        except Exception as exc:
            LOGGER.warning("Failed to edit completion message for deadline %s: %s", deadline.id, exc)

    await STORE.update(deadline)


async def maybe_send_initial_publication(
    context: ContextTypes.DEFAULT_TYPE,
    deadline: Deadline,
    *,
    force: bool,
) -> None:
    if now_until(deadline.deadline_datetime) <= timedelta(days=7) or force:
        await publish_live_deadline_post(context, deadline, kind="initial", replace_existing=False)


async def align_reminder_flags(deadline: Deadline) -> None:
    remaining = now_until(deadline.deadline_datetime)
    if remaining <= timedelta(days=7):
        deadline.reminded_7d = True
    if remaining <= timedelta(hours=24):
        deadline.reminded_24h = True
    await STORE.update(deadline)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await require_whitelist(update):
        return ConversationHandler.END
    await reply(update.message, msg.start_message(), reply_markup=main_keyboard())
    return ConversationHandler.END


async def show_current_time(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_whitelist(update):
        return
    current_time = bot_now().strftime("%d.%m.%Y %H:%M")
    await reply(
        update.message,
        msg.current_time_message(current_time, BOT_TIMEZONE_LABEL),
        reply_markup=main_keyboard(),
    )


async def list_deadlines(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_whitelist(update):
        return
    deadlines = STORE.list_active()
    if not deadlines:
        await reply(update.message, msg.no_active_deadlines(), reply_markup=main_keyboard())
        return
    await reply(
        update.message,
        msg.list_deadlines_message([deadline_context(item) for item in deadlines]),
        reply_markup=main_keyboard(),
    )


async def list_archive(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_whitelist(update):
        return
    deadlines = STORE.list_archive()
    if not deadlines:
        await reply(update.message, msg.no_archive_deadlines(), reply_markup=main_keyboard())
        return
    await reply(
        update.message,
        msg.archive_deadlines_message([deadline_context(item) for item in deadlines]),
        reply_markup=main_keyboard(),
    )


async def refresh_channel_posts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_whitelist(update):
        return

    all_deadlines = STORE.list_all()
    total_messages = sum(len(deadline.channel_messages) for deadline in all_deadlines)
    if total_messages == 0:
        await reply(update.message, msg.no_channel_posts_to_refresh(), reply_markup=main_keyboard())
        return

    updated = 0
    unchanged = 0
    skipped = 0
    failed = 0

    for deadline in all_deadlines:
        dirty = False
        for record in deadline.channel_messages:
            try:
                template = render_channel_template(record)
            except Exception as exc:
                skipped += 1
                LOGGER.warning(
                    "Skipping refresh for deadline %s message %s: %s",
                    deadline.id,
                    record.message_id,
                    exc,
                )
                continue

            if template.text == record.text and template.parse_mode == record.parse_mode:
                unchanged += 1
                continue

            try:
                await context.bot.edit_message_text(
                    chat_id=CHANNEL_ID,
                    message_id=record.message_id,
                    text=template.text,
                    parse_mode=template.parse_mode,
                )
            except BadRequest as exc:
                if "message is not modified" in str(exc).lower():
                    unchanged += 1
                    record.text = template.text
                    record.parse_mode = template.parse_mode
                    dirty = True
                    continue

                failed += 1
                LOGGER.warning(
                    "Failed to refresh deadline %s message %s: %s",
                    deadline.id,
                    record.message_id,
                    exc,
                )
                continue
            except Exception as exc:
                failed += 1
                LOGGER.warning(
                    "Failed to refresh deadline %s message %s: %s",
                    deadline.id,
                    record.message_id,
                    exc,
                )
                continue

            record.text = template.text
            record.parse_mode = template.parse_mode
            updated += 1
            dirty = True

        if dirty:
            await STORE.update(deadline)

    await reply(
        update.message,
        msg.refreshed_channel_posts(updated, unchanged, skipped, failed),
        reply_markup=main_keyboard(),
    )


async def remind_deadline_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await require_whitelist(update):
        return ConversationHandler.END
    deadlines = STORE.list_active()
    if not deadlines:
        await reply(update.message, msg.no_active_deadlines(), reply_markup=main_keyboard())
        return ConversationHandler.END
    await reply(
        update.message,
        msg.choose_remind_id([deadline_context(item) for item in deadlines]),
        reply_markup=input_keyboard(),
    )
    return REMIND_SELECT


async def remind_deadline_finish(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    handled, next_state = await maybe_handle_menu_navigation(update, context)
    if handled:
        return next_state if next_state is not None else ConversationHandler.END

    try:
        deadline_id = int(update.message.text.strip())
    except ValueError:
        await reply(update.message, msg.numeric_id_required(), reply_markup=input_keyboard())
        return REMIND_SELECT

    deadline = STORE.get(deadline_id)
    if deadline is None or deadline.status != STATUS_ACTIVE:
        await reply(update.message, msg.deadline_not_found_by_id(), reply_markup=input_keyboard())
        return REMIND_SELECT

    await publish_live_deadline_post(context, deadline, kind="reminder_manual", replace_existing=True)
    await align_reminder_flags(deadline)
    await reply(update.message, msg.deadline_reminded_private(), reply_markup=main_keyboard())
    return ConversationHandler.END


async def create_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await require_whitelist(update):
        return ConversationHandler.END
    await reply(update.message, msg.create_prompt_description(), reply_markup=input_keyboard())
    return CREATE_DESCRIPTION


async def create_description(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    handled, next_state = await maybe_handle_menu_navigation(update, context)
    if handled:
        return next_state if next_state is not None else ConversationHandler.END

    context.user_data["new_description"] = update.message.text.strip()
    context.user_data["new_description_html"] = update.message.text_html or escape(update.message.text.strip())
    await reply(
        update.message,
        msg.create_prompt_datetime(BOT_TIMEZONE_LABEL),
        reply_markup=input_keyboard(),
    )
    return CREATE_DATETIME


async def create_datetime(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    handled, next_state = await maybe_handle_menu_navigation(update, context)
    if handled:
        return next_state if next_state is not None else ConversationHandler.END

    try:
        deadline_at, time_was_provided, time_was_explicit_midnight = parse_deadline_input(update.message.text.strip())
    except ValueError as exc:
        await reply(update.message, msg.MessageTemplate(str(exc)), reply_markup=input_keyboard())
        return CREATE_DATETIME

    deadline = Deadline(
        id=0,
        description=context.user_data["new_description"],
        description_html=context.user_data["new_description_html"],
        deadline_at=deadline_at.isoformat(),
        time_was_provided=time_was_provided,
        time_was_explicit_midnight=time_was_explicit_midnight,
        created_by=update.effective_user.id,
        created_by_name=update.effective_user.full_name,
        created_at=bot_now().isoformat(),
    )
    saved = await STORE.add(deadline)

    if now_until(deadline_at) > timedelta(days=7):
        keyboard = InlineKeyboardMarkup(
            [[
                InlineKeyboardButton("Опубликовать", callback_data=f"{IMMEDIATE_CALLBACK}:yes:{saved.id}"),
                InlineKeyboardButton("Не публиковать", callback_data=f"{IMMEDIATE_CALLBACK}:no:{saved.id}"),
            ]]
        )
        await reply(update.message, msg.initial_publish_question(deadline_context(saved)), reply_markup=keyboard)
        return CREATE_CONFIRM

    await maybe_send_initial_publication(context, saved, force=False)
    await align_reminder_flags(saved)
    await reply(update.message, msg.deadline_saved_and_published(), reply_markup=main_keyboard())
    return ConversationHandler.END


async def create_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    _, answer, raw_id = query.data.split(":")
    deadline = STORE.get(int(raw_id))
    if deadline is None:
        template = msg.deadline_missing()
        await query.edit_message_text(template.text, parse_mode=template.parse_mode)
        return ConversationHandler.END

    if answer == "yes":
        await maybe_send_initial_publication(context, deadline, force=True)
        template = msg.deadline_saved_and_published_now()
    else:
        deadline.immediate_publish_skipped = True
        await STORE.update(deadline)
        template = msg.deadline_saved_skip_initial()
    await align_reminder_flags(deadline)
    await query.edit_message_text(template.text, parse_mode=template.parse_mode)
    if query.message:
        await reply(query.message, msg.start_message(), reply_markup=main_keyboard())
    return ConversationHandler.END


async def cancel_deadline_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await require_whitelist(update):
        return ConversationHandler.END
    deadlines = STORE.list_active()
    if not deadlines:
        await reply(update.message, msg.no_active_deadlines(), reply_markup=main_keyboard())
        return ConversationHandler.END
    await reply(
        update.message,
        msg.choose_cancel_id([deadline_context(item) for item in deadlines]),
        reply_markup=input_keyboard(),
    )
    return CANCEL_SELECT


async def cancel_deadline_finish(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    handled, next_state = await maybe_handle_menu_navigation(update, context)
    if handled:
        return next_state if next_state is not None else ConversationHandler.END

    try:
        deadline_id = int(update.message.text.strip())
    except ValueError:
        await reply(update.message, msg.numeric_id_required(), reply_markup=input_keyboard())
        return CANCEL_SELECT

    deadline = STORE.get(deadline_id)
    if deadline is None or deadline.status != STATUS_ACTIVE:
        await reply(update.message, msg.deadline_not_found_by_id(), reply_markup=input_keyboard())
        return CANCEL_SELECT

    deadline.status = STATUS_CANCELLED
    # Cancelled deadlines stay in archive history, but their channel posts are
    # removed only after the 3-day cleanup window.
    deadline.cleanup_after = (bot_now() + timedelta(days=3)).isoformat()
    await STORE.update(deadline)
    template_data = deadline_template_data(deadline)
    await post_channel_template(
        context,
        deadline,
        msg.deadline_cancelled_post(template_data["deadline"]),
        kind="cancelled",
        template_data=template_data,
    )
    await reply(update.message, msg.deadline_cancelled_private(), reply_markup=main_keyboard())
    return ConversationHandler.END


async def delete_deadline_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await require_whitelist(update):
        return ConversationHandler.END
    deadlines = STORE.list_active()
    if not deadlines:
        await reply(update.message, msg.no_active_deadlines(), reply_markup=main_keyboard())
        return ConversationHandler.END
    await reply(
        update.message,
        msg.choose_delete_id([deadline_context(item) for item in deadlines]),
        reply_markup=input_keyboard(),
    )
    return DELETE_SELECT


async def delete_deadline_finish(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    handled, next_state = await maybe_handle_menu_navigation(update, context)
    if handled:
        return next_state if next_state is not None else ConversationHandler.END

    try:
        deadline_id = int(update.message.text.strip())
    except ValueError:
        await reply(update.message, msg.numeric_id_required(), reply_markup=input_keyboard())
        return DELETE_SELECT

    deadline = STORE.get(deadline_id)
    if deadline is None or deadline.status != STATUS_ACTIVE:
        await reply(update.message, msg.deadline_not_found_by_id(), reply_markup=input_keyboard())
        return DELETE_SELECT

    # Manual delete is stronger than cancel: remove all channel traces now and
    # move the deadline directly into archive state.
    await delete_all_deadline_messages(context, deadline)
    deadline.status = STATUS_ARCHIVED
    deadline.archived_at = bot_now().isoformat()
    deadline.cleanup_after = None
    await STORE.update(deadline)
    await reply(update.message, msg.deadline_deleted_private(), reply_markup=main_keyboard())
    return ConversationHandler.END

async def edit_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await require_whitelist(update):
        return ConversationHandler.END
    deadlines = STORE.list_active()
    if not deadlines:
        await reply(update.message, msg.no_active_deadlines(), reply_markup=main_keyboard())
        return ConversationHandler.END
    await reply(
        update.message,
        msg.choose_edit_id([deadline_context(item) for item in deadlines]),
        reply_markup=input_keyboard(),
    )
    return EDIT_SELECT


async def edit_select(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    handled, next_state = await maybe_handle_menu_navigation(update, context)
    if handled:
        return next_state if next_state is not None else ConversationHandler.END

    try:
        deadline_id = int(update.message.text.strip())
    except ValueError:
        await reply(update.message, msg.numeric_id_required(), reply_markup=input_keyboard())
        return EDIT_SELECT

    deadline = STORE.get(deadline_id)
    if deadline is None or deadline.status != STATUS_ACTIVE:
        await reply(update.message, msg.deadline_not_found_by_id(), reply_markup=input_keyboard())
        return EDIT_SELECT

    context.user_data["edit_deadline_id"] = deadline_id
    context.user_data["edit_original_description"] = deadline.description
    context.user_data["edit_original_description_html"] = deadline.description_html
    context.user_data["edit_original_deadline_at"] = deadline.deadline_at
    context.user_data["edit_original_time_was_provided"] = deadline.time_was_provided
    context.user_data["edit_original_time_was_explicit_midnight"] = deadline.time_was_explicit_midnight
    await reply(
        update.message,
        msg.edit_prompt_description(deadline_context(deadline)),
        reply_markup=edit_input_keyboard(),
    )
    return EDIT_DESCRIPTION


async def edit_description(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    handled, next_state = await maybe_handle_menu_navigation(update, context)
    if handled:
        return next_state if next_state is not None else ConversationHandler.END

    raw_text = update.message.text.strip()
    if raw_text == BUTTON_SKIP:
        context.user_data["edit_description"] = context.user_data["edit_original_description"]
        context.user_data["edit_description_html"] = context.user_data["edit_original_description_html"]
    else:
        context.user_data["edit_description"] = raw_text
        context.user_data["edit_description_html"] = update.message.text_html or escape(raw_text)
    await reply(
        update.message,
        msg.edit_prompt_datetime(BOT_TIMEZONE_LABEL),
        reply_markup=edit_input_keyboard(),
    )
    return EDIT_DATETIME


async def edit_datetime(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    handled, next_state = await maybe_handle_menu_navigation(update, context)
    if handled:
        return next_state if next_state is not None else ConversationHandler.END

    deadline = STORE.get(context.user_data["edit_deadline_id"])
    if deadline is None or deadline.status != STATUS_ACTIVE:
        await reply(update.message, msg.deadline_missing(), reply_markup=main_keyboard())
        return ConversationHandler.END

    raw_text = update.message.text.strip()
    if raw_text == BUTTON_SKIP:
        deadline_at = datetime.fromisoformat(context.user_data["edit_original_deadline_at"])
        time_was_provided = context.user_data["edit_original_time_was_provided"]
        time_was_explicit_midnight = context.user_data["edit_original_time_was_explicit_midnight"]
    else:
        try:
            deadline_at, time_was_provided, time_was_explicit_midnight = parse_deadline_input(raw_text)
        except ValueError as exc:
            await reply(update.message, msg.MessageTemplate(str(exc)), reply_markup=edit_input_keyboard())
            return EDIT_DATETIME

    new_description = context.user_data["edit_description"]
    new_description_html = context.user_data["edit_description_html"]
    if (
        new_description == deadline.description
        and new_description_html == deadline.description_html
        and ensure_bot_timezone(deadline_at) == deadline.deadline_datetime
        and time_was_provided == deadline.time_was_provided
        and time_was_explicit_midnight == deadline.time_was_explicit_midnight
    ):
        await reply(update.message, msg.no_changes(), reply_markup=main_keyboard())
        return ConversationHandler.END

    had_any_publication = bool(deadline.channel_messages)
    old_payload = asdict(deadline)
    old_payload["channel_messages"] = [asdict(item) for item in deadline.channel_messages]
    old_deadline = Deadline(
        **{**old_payload, "channel_messages": [ChannelMessageRecord(**record) for record in old_payload["channel_messages"]]}
    )

    deadline.description = new_description
    deadline.description_html = new_description_html
    deadline.deadline_at = ensure_bot_timezone(deadline_at).isoformat()
    deadline.time_was_provided = time_was_provided
    deadline.time_was_explicit_midnight = time_was_explicit_midnight
    deadline.reminded_7d = False
    deadline.reminded_24h = False
    deadline.cleanup_after = None

    changes = build_changes(old_deadline, deadline)
    context.user_data["edit_changes"] = changes
    context.user_data["edit_old_context"] = deadline_context(old_deadline)
    context.user_data["edit_new_context"] = deadline_context(deadline)

    if now_until(deadline_at) <= timedelta(days=7):
        deadline.immediate_publish_skipped = False
        await STORE.update(deadline)
        await align_reminder_flags(deadline)
        if had_any_publication:
            template_data = changed_template_data(changes, deadline_context(old_deadline), deadline_context(deadline))
            await post_channel_template(
                context,
                deadline,
                msg.deadline_changed_post(changes, template_data["old_deadline"], template_data["new_deadline"]),
                kind="changed",
                template_data=template_data,
            )
            await reply(update.message, msg.deadline_changed_notice(), reply_markup=main_keyboard())
        else:
            await maybe_send_initial_publication(context, deadline, force=False)
            await reply(update.message, msg.deadline_changed_actual_published(), reply_markup=main_keyboard())
        return ConversationHandler.END

    await STORE.update(deadline)
    keyboard = InlineKeyboardMarkup(
        [[
            InlineKeyboardButton("Опубликовать", callback_data=f"{EDIT_IMMEDIATE_CALLBACK}:yes:{deadline.id}"),
            InlineKeyboardButton("Не публиковать", callback_data=f"{EDIT_IMMEDIATE_CALLBACK}:no:{deadline.id}"),
        ]]
    )
    question = msg.edit_publish_question_with_change() if had_any_publication else msg.edit_publish_question_without_change()
    context.user_data["edit_had_any_publication"] = had_any_publication
    await reply(update.message, question, reply_markup=keyboard)
    return EDIT_CONFIRM


async def edit_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    _, answer, raw_id = query.data.split(":")
    deadline = STORE.get(int(raw_id))
    if deadline is None or deadline.status != STATUS_ACTIVE:
        template = msg.deadline_missing()
        await query.edit_message_text(template.text, parse_mode=template.parse_mode)
        return ConversationHandler.END

    had_any_publication = context.user_data.get("edit_had_any_publication", False)
    changes = context.user_data.get("edit_changes", [])
    old_context = context.user_data.get("edit_old_context", deadline_context(deadline))
    new_context = deadline_context(deadline)

    if answer == "yes":
        deadline.initial_published = False
        deadline.immediate_publish_skipped = False
        await STORE.update(deadline)
        await maybe_send_initial_publication(context, deadline, force=True)
        template = msg.edit_saved_with_change_and_publish() if had_any_publication else msg.edit_saved_published()
    else:
        deadline.immediate_publish_skipped = True
        await STORE.update(deadline)
        template = msg.edit_saved_with_change_no_publish() if had_any_publication else msg.edit_saved_no_publish()

    if had_any_publication:
        template_data = changed_template_data(changes, old_context, new_context)
        await post_channel_template(
            context,
            deadline,
            msg.deadline_changed_post(changes, template_data["old_deadline"], template_data["new_deadline"]),
            kind="changed",
            template_data=template_data,
        )
    await align_reminder_flags(deadline)
    await query.edit_message_text(template.text, parse_mode=template.parse_mode)
    if query.message:
        await reply(query.message, msg.start_message(), reply_markup=main_keyboard())
    return ConversationHandler.END


async def abort_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await reply(update.message, msg.cancelled(), reply_markup=main_keyboard())
    return ConversationHandler.END

async def reminder_loop(context: ContextTypes.DEFAULT_TYPE) -> None:
    # One repeating loop handles both reminder delivery and delayed cleanup.
    # This keeps scheduling logic in a single place and makes restart behavior
    # deterministic: after restart, the loop simply reconciles current state.
    for deadline in STORE.list_all():
        if deadline.status == STATUS_ACTIVE:
            remaining = now_until(deadline.deadline_datetime)
            if remaining <= timedelta(0):
                await mark_deadline_completed(context, deadline)
                continue

            if not deadline.reminded_7d and remaining <= timedelta(days=7):
                await publish_live_deadline_post(context, deadline, kind="reminder_7d", replace_existing=True)
                deadline.reminded_7d = True
                await STORE.update(deadline)

            if not deadline.reminded_24h and remaining <= timedelta(hours=24):
                await publish_live_deadline_post(context, deadline, kind="reminder_24h", replace_existing=True)
                deadline.reminded_24h = True
                await STORE.update(deadline)
            continue

        if deadline.status in {STATUS_CANCELLED, STATUS_COMPLETED}:
            cleanup_at = deadline.cleanup_after_datetime
            if cleanup_at and bot_now() >= cleanup_at:
                await delete_all_deadline_messages(context, deadline)
                deadline.status = STATUS_ARCHIVED
                deadline.archived_at = bot_now().isoformat()
                deadline.cleanup_after = None
                await STORE.update(deadline)


def build_application() -> Application:
    if not WHITELIST_USER_IDS:
        raise RuntimeError("WHITELIST_USER_IDS is empty. Add one or more Telegram user ids to .env.")

    application = Application.builder().token(BOT_TOKEN).build()

    create_conversation = ConversationHandler(
        entry_points=[
            CommandHandler("new", create_start),
            MessageHandler(filters.Regex(f"^{BUTTON_NEW}$"), create_start),
        ],
        states={
            CREATE_DESCRIPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, create_description)],
            CREATE_DATETIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, create_datetime)],
            CREATE_CONFIRM: [CallbackQueryHandler(create_confirm, pattern=f"^{IMMEDIATE_CALLBACK}:")],
        },
        fallbacks=[
            CommandHandler("start", start),
            CommandHandler("cancel", abort_conversation),
            MessageHandler(filters.Regex(f"^{BUTTON_ABORT}$"), abort_conversation),
        ],
    )

    cancel_deadline_conversation = ConversationHandler(
        entry_points=[
            CommandHandler("cancel_deadline", cancel_deadline_start),
            MessageHandler(filters.Regex(f"^{BUTTON_CANCEL_DEADLINE}$"), cancel_deadline_start),
        ],
        states={CANCEL_SELECT: [MessageHandler(filters.TEXT & ~filters.COMMAND, cancel_deadline_finish)]},
        fallbacks=[
            CommandHandler("start", start),
            CommandHandler("cancel", abort_conversation),
            MessageHandler(filters.Regex(f"^{BUTTON_ABORT}$"), abort_conversation),
        ],
    )

    delete_deadline_conversation = ConversationHandler(
        entry_points=[
            CommandHandler("delete", delete_deadline_start),
            MessageHandler(filters.Regex(f"^{BUTTON_DELETE_DEADLINE}$"), delete_deadline_start),
        ],
        states={DELETE_SELECT: [MessageHandler(filters.TEXT & ~filters.COMMAND, delete_deadline_finish)]},
        fallbacks=[
            CommandHandler("start", start),
            CommandHandler("cancel", abort_conversation),
            MessageHandler(filters.Regex(f"^{BUTTON_ABORT}$"), abort_conversation),
        ],
    )

    remind_deadline_conversation = ConversationHandler(
        entry_points=[
            CommandHandler("remind", remind_deadline_start),
            MessageHandler(filters.Regex(f"^{BUTTON_REMIND}$"), remind_deadline_start),
        ],
        states={REMIND_SELECT: [MessageHandler(filters.TEXT & ~filters.COMMAND, remind_deadline_finish)]},
        fallbacks=[
            CommandHandler("start", start),
            CommandHandler("cancel", abort_conversation),
            MessageHandler(filters.Regex(f"^{BUTTON_ABORT}$"), abort_conversation),
        ],
    )

    edit_conversation = ConversationHandler(
        entry_points=[
            CommandHandler("edit", edit_start),
            MessageHandler(filters.Regex(f"^{BUTTON_EDIT}$"), edit_start),
        ],
        states={
            EDIT_SELECT: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_select)],
            EDIT_DESCRIPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_description)],
            EDIT_DATETIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_datetime)],
            EDIT_CONFIRM: [CallbackQueryHandler(edit_confirm, pattern=f"^{EDIT_IMMEDIATE_CALLBACK}:")],
        },
        fallbacks=[
            CommandHandler("start", start),
            CommandHandler("cancel", abort_conversation),
            MessageHandler(filters.Regex(f"^{BUTTON_ABORT}$"), abort_conversation),
        ],
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("now", show_current_time))
    application.add_handler(CommandHandler("list", list_deadlines))
    application.add_handler(CommandHandler("archive", list_archive))
    application.add_handler(CommandHandler("refresh_posts", refresh_channel_posts))
    application.add_handler(CommandHandler("cancel", abort_conversation))
    application.add_handler(create_conversation)
    application.add_handler(cancel_deadline_conversation)
    application.add_handler(delete_deadline_conversation)
    application.add_handler(remind_deadline_conversation)
    application.add_handler(edit_conversation)
    application.add_handler(MessageHandler(filters.Regex(f"^{BUTTON_LIST}$"), list_deadlines))
    application.add_handler(MessageHandler(filters.Regex(f"^{BUTTON_ARCHIVE}$"), list_archive))
    application.add_handler(MessageHandler(filters.Regex(f"^{BUTTON_REFRESH_POSTS}$"), refresh_channel_posts))
    application.add_handler(MessageHandler(filters.Regex(f"^{BUTTON_ABORT}$"), abort_conversation))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, start))

    job_queue = application.job_queue
    if job_queue is None:
        raise RuntimeError("Job queue is not available. Install python-telegram-bot[job-queue].")
    job_queue.run_repeating(reminder_loop, interval=60, first=10, name="deadline-reminders")
    return application


def main() -> None:
    asyncio.set_event_loop(asyncio.new_event_loop())
    application = build_application()
    application.run_polling()


if __name__ == "__main__":
    main()
