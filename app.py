import asyncio
import json
import logging
import math
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
EDIT_DESCRIPTION, EDIT_DATETIME, EDIT_CONFIRM = range(10, 13)
CANCEL_REASON = 20

IMMEDIATE_CALLBACK = "immediate_publish"
EDIT_IMMEDIATE_CALLBACK = "edit_immediate_publish"

LIST_CALLBACK = "ls"
OPEN_CALLBACK = "op"
DETAILS_CALLBACK = "dt"
ACTION_CALLBACK = "ac"
CREATE_FROM_LIST_CALLBACK = "cr"

ACTION_EDIT = "ed"
ACTION_CANCEL = "cn"
ACTION_DELETE = "dl"
ACTION_REMIND = "rm"

SOURCE_VISIBLE = "visible"
SOURCE_ARCHIVE = "archive"

PAGE_SIZE = 6
CURRENT_SCHEMA_VERSION = 2

BUTTON_LIST = msg.BUTTON_LIST
BUTTON_ARCHIVE = msg.BUTTON_ARCHIVE
BUTTON_REFRESH_POSTS = msg.BUTTON_REFRESH_POSTS
BUTTON_SKIP = msg.BUTTON_SKIP
BUTTON_ABORT = msg.BUTTON_ABORT

BOT_TIMEZONE = timezone(timedelta(hours=5))
BOT_TIMEZONE_LABEL = "UTC+5"

STATUS_ACTIVE = "active"
STATUS_CANCELLED = "cancelled"
STATUS_COMPLETED = "completed"
STATUS_ARCHIVED = "archived"

# The visible working list is broader than "active only": cancelled/completed
# deadlines stay there until their channel messages are cleaned up and the item
# is finally archived.
STATUS_PRIORITY = {
    STATUS_ACTIVE: 0,
    STATUS_CANCELLED: 1,
    STATUS_COMPLETED: 2,
    STATUS_ARCHIVED: 3,
}


@dataclass
class ChannelMessageRecord:
    message_id: int
    text: str
    parse_mode: str | None
    kind: str
    created_at: str
    template_data: dict = field(default_factory=dict)


@dataclass
class DeadlineEvent:
    kind: str
    at: str
    actor_id: int | None = None
    actor_name: str | None = None
    details: dict = field(default_factory=dict)


@dataclass
class Deadline:
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
    history: list[DeadlineEvent] = field(default_factory=list)

    @property
    def deadline_datetime(self) -> datetime:
        return ensure_bot_timezone(datetime.fromisoformat(self.deadline_at))

    @property
    def cleanup_after_datetime(self) -> datetime | None:
        if not self.cleanup_after:
            return None
        return ensure_bot_timezone(datetime.fromisoformat(self.cleanup_after))

    @property
    def archived_at_datetime(self) -> datetime | None:
        if not self.archived_at:
            return None
        return ensure_bot_timezone(datetime.fromisoformat(self.archived_at))


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


def status_label(status: str) -> str:
    return {
        STATUS_ACTIVE: "активный",
        STATUS_CANCELLED: "отменённый",
        STATUS_COMPLETED: "завершённый",
        STATUS_ARCHIVED: "архивный",
    }.get(status, status)


def format_deadline_line(deadline_at: datetime, time_was_provided: bool) -> str:
    dt = ensure_bot_timezone(deadline_at)
    date_part = dt.strftime("%d.%m.%Y")
    if time_was_provided:
        return f"{date_part} {dt.strftime('%H:%M')}"
    return date_part


def format_timestamp(raw_iso: str | None) -> str:
    if not raw_iso:
        return "неизвестно"
    return ensure_bot_timezone(datetime.fromisoformat(raw_iso)).strftime("%d.%m.%Y %H:%M")


def compact_text(value: str, limit: int) -> str:
    flat = " ".join(value.split())
    if len(flat) <= limit:
        return flat
    return flat[: max(0, limit - 1)].rstrip() + "…"


def remaining_message_context(deadline_at: datetime) -> dict:
    remaining = max(ensure_bot_timezone(deadline_at) - bot_now(), timedelta())
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


def deadline_context(deadline: Deadline) -> dict:
    line = format_deadline_line(deadline.deadline_datetime, deadline.time_was_provided)
    return {
        "id": deadline.id,
        "description": deadline.description,
        "description_html": deadline.description_html,
        "description_preview_html": escape(compact_text(deadline.description, 70)),
        "deadline_line": line,
        "deadline_line_html": escape(line),
        "created_by_name": deadline.created_by_name,
        "created_by_name_html": escape(deadline.created_by_name),
        "status": deadline.status,
        "status_label": status_label(deadline.status),
        "status_label_html": escape(status_label(deadline.status)),
        "timezone_label": BOT_TIMEZONE_LABEL,
        "deadline_iso": deadline.deadline_at,
        "cleanup_after_iso": deadline.cleanup_after,
        "archived_at_iso": deadline.archived_at,
        "created_at_iso": deadline.created_at,
        "channel_messages_count": len(deadline.channel_messages),
    }


def live_deadline_context(deadline: Deadline) -> dict:
    payload = deadline_context(deadline)
    payload.update(remaining_message_context(deadline.deadline_datetime))
    return payload


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


def deadline_template_data(deadline: Deadline) -> dict:
    return {"deadline": deadline_context(deadline)}


def live_deadline_template_data(deadline: Deadline) -> dict:
    return {"deadline": live_deadline_context(deadline)}


def cancelled_template_data(deadline: dict, reason: str, reason_html: str) -> dict:
    return {
        "deadline": deadline,
        "reason": reason,
        "reason_html": reason_html,
    }


def changed_template_data(changes: list[dict], old_deadline: dict, new_deadline: dict) -> dict:
    return {
        "changes": changes,
        "old_deadline": old_deadline,
        "new_deadline": new_deadline,
    }


def make_event(
    kind: str,
    *,
    at: str | None = None,
    actor_id: int | None = None,
    actor_name: str | None = None,
    details: dict | None = None,
) -> DeadlineEvent:
    return DeadlineEvent(
        kind=kind,
        at=at or bot_now().isoformat(),
        actor_id=actor_id,
        actor_name=actor_name,
        details=details or {},
    )


def add_history_event(
    deadline: Deadline,
    kind: str,
    *,
    at: str | None = None,
    actor_id: int | None = None,
    actor_name: str | None = None,
    details: dict | None = None,
) -> None:
    deadline.history.append(
        make_event(
            kind,
            at=at,
            actor_id=actor_id,
            actor_name=actor_name,
            details=details,
        )
    )


def actor_from_update(update: Update) -> tuple[int | None, str | None]:
    user = update.effective_user
    if user is None:
        return None, None
    return user.id, user.full_name


def legacy_context_from_item(item: dict) -> dict:
    deadline_at = ensure_bot_timezone(datetime.fromisoformat(item["deadline_at"]))
    line = format_deadline_line(deadline_at, item.get("time_was_provided", False))
    status = item.get("status", STATUS_ACTIVE)
    return {
        "id": item["id"],
        "description": item["description"],
        "description_html": item.get("description_html", escape(item["description"])),
        "description_preview_html": escape(compact_text(item["description"], 70)),
        "deadline_line": line,
        "deadline_line_html": escape(line),
        "created_by_name": item.get("created_by_name", "неизвестно"),
        "created_by_name_html": escape(item.get("created_by_name", "неизвестно")),
        "status": status,
        "status_label": status_label(status),
        "status_label_html": escape(status_label(status)),
        "timezone_label": BOT_TIMEZONE_LABEL,
        "deadline_iso": item["deadline_at"],
        "cleanup_after_iso": item.get("cleanup_after"),
        "archived_at_iso": item.get("archived_at"),
        "created_at_iso": item.get("created_at"),
        "channel_messages_count": len(item.get("channel_messages", [])),
    }


def legacy_live_context_from_item(item: dict) -> dict:
    payload = legacy_context_from_item(item)
    payload.update(remaining_message_context(datetime.fromisoformat(item["deadline_at"])))
    return payload


def legacy_template_data_for_kind(item: dict, kind: str) -> dict:
    if kind in {"initial", "reminder_7d", "reminder_24h", "reminder_manual"}:
        return {"deadline": legacy_live_context_from_item(item)}
    if kind in {"cancelled", "completed"}:
        if kind == "cancelled":
            return {
                "deadline": legacy_context_from_item(item),
                "reason": "не указана",
                "reason_html": "не указана",
            }
        return {"deadline": legacy_context_from_item(item)}
    return {}


def migrate_storage(raw: dict, version: int) -> dict:
    if version != 1:
        raise RuntimeError(f"Unsupported storage schema version: {version}")

    # Migrations rewrite old JSON into the current schema once at load time, so
    # the rest of the runtime can work with one clean data shape.
    migrated_deadlines: list[dict] = []
    for item in raw.get("deadlines", []):
        payload = dict(item)
        payload.setdefault("description_html", escape(payload["description"]))
        payload.setdefault("immediate_publish_skipped", False)
        payload.setdefault("initial_published", False)
        payload.setdefault("reminded_7d", False)
        payload.setdefault("reminded_24h", False)
        payload.setdefault("status", STATUS_ACTIVE)
        payload.setdefault("cleanup_after", None)
        payload.setdefault("archived_at", None)

        created_at = payload.get("created_at") or bot_now().isoformat()
        payload["created_at"] = created_at

        history = payload.get("history") or []
        if not history:
            history.append(
                asdict(
                    make_event(
                        "created",
                        at=created_at,
                        actor_id=payload.get("created_by"),
                        actor_name=payload.get("created_by_name"),
                    )
                )
            )

            if payload["status"] == STATUS_CANCELLED and payload.get("cleanup_after"):
                cancelled_at = (
                    ensure_bot_timezone(datetime.fromisoformat(payload["cleanup_after"])) - timedelta(days=3)
                ).isoformat()
                history.append(asdict(make_event("cancelled", at=cancelled_at)))

            if payload["status"] == STATUS_COMPLETED and payload.get("cleanup_after"):
                completed_at = (
                    ensure_bot_timezone(datetime.fromisoformat(payload["cleanup_after"])) - timedelta(days=3)
                ).isoformat()
                history.append(asdict(make_event("completed", at=completed_at, actor_name="бот")))

            if payload["status"] == STATUS_ARCHIVED:
                archived_at = payload.get("archived_at") or created_at
                payload["archived_at"] = archived_at
                history.append(asdict(make_event("archived", at=archived_at, actor_name="бот", details={"reason": "legacy"})))

        payload["history"] = history

        migrated_messages = []
        for record in payload.get("channel_messages", []):
            migrated_record = dict(record)
            migrated_record.setdefault(
                "template_data",
                legacy_template_data_for_kind(payload, migrated_record.get("kind", "")),
            )
            migrated_messages.append(migrated_record)
        payload["channel_messages"] = migrated_messages
        migrated_deadlines.append(payload)

    return {
        "schema_version": CURRENT_SCHEMA_VERSION,
        "next_id": raw.get("next_id", 1),
        "deadlines": migrated_deadlines,
    }

class DeadlineStore:
    # JSON remains the persistence layer, but it is now schema-versioned and
    # migratable so server-side state can evolve safely without manual resets.
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = asyncio.Lock()
        self._deadlines: list[Deadline] = []
        self._next_id = 1
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._load()

    def _serialize(self) -> dict:
        return {
            "schema_version": CURRENT_SCHEMA_VERSION,
            "next_id": self._next_id,
            "deadlines": [asdict(item) for item in self._deadlines],
        }

    def _write_sync(self) -> None:
        self.path.write_text(
            json.dumps(self._serialize(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _next_unformatted_backup_path(self) -> Path:
        # Keep malformed storage files next to the live JSON and use
        # Windows-like suffixes so repeated recoveries never overwrite older
        # evidence.
        base_name = f"unformatted-{self.path.stem}"
        suffix = self.path.suffix
        candidate = self.path.with_name(f"{base_name}{suffix}")
        counter = 1
        while candidate.exists():
            candidate = self.path.with_name(f"{base_name} ({counter}){suffix}")
            counter += 1
        return candidate

    def _recover_invalid_storage(self, raw_bytes: bytes, exc: Exception) -> None:
        backup_path = self._next_unformatted_backup_path()
        backup_path.write_bytes(raw_bytes)
        self._deadlines = []
        self._next_id = 1
        self._write_sync()
        LOGGER.warning(
            "Storage file %s had an invalid format. Original content was saved to %s and a new empty storage was created. Reason: %s",
            self.path,
            backup_path,
            exc,
        )

    def _load(self) -> None:
        if not self.path.exists():
            return

        raw_bytes = self.path.read_bytes()
        try:
            raw = json.loads(raw_bytes.decode("utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("storage root must be a JSON object")
            if "deadlines" in raw and not isinstance(raw["deadlines"], list):
                raise ValueError("'deadlines' must be a JSON array")

            version = int(raw.get("schema_version", 1))
            if version > CURRENT_SCHEMA_VERSION:
                raise RuntimeError(f"Unsupported storage schema version: {version}")

            migrated = False
            while version < CURRENT_SCHEMA_VERSION:
                raw = migrate_storage(raw, version)
                version = raw["schema_version"]
                migrated = True

            self._next_id = int(raw.get("next_id", 1))
            deadlines: list[Deadline] = []
            for item in raw.get("deadlines", []):
                messages = [ChannelMessageRecord(**record) for record in item.get("channel_messages", [])]
                history = [DeadlineEvent(**event) for event in item.get("history", [])]
                payload = dict(item)
                payload["channel_messages"] = messages
                payload["history"] = history
                deadlines.append(Deadline(**payload))
            self._deadlines = deadlines

            if migrated:
                self._write_sync()
        except Exception as exc:
            self._recover_invalid_storage(raw_bytes, exc)

    async def _save(self) -> None:
        self._write_sync()

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

    def list_visible(self) -> list[Deadline]:
        # Visible list intentionally includes active + recently cancelled /
        # completed deadlines until cleanup removes their channel traces.
        items = [item for item in self._deadlines if item.status != STATUS_ARCHIVED]
        return sorted(items, key=lambda item: (STATUS_PRIORITY.get(item.status, 99), item.deadline_datetime, item.id))

    def list_archive(self) -> list[Deadline]:
        items = [item for item in self._deadlines if item.status == STATUS_ARCHIVED]
        return sorted(
            items,
            key=lambda item: (item.archived_at_datetime or item.deadline_datetime, item.id),
            reverse=True,
        )

    def list_all(self) -> list[Deadline]:
        return sorted(self._deadlines, key=lambda item: (item.deadline_datetime, item.id))


STORE = DeadlineStore(STORAGE_PATH)


def is_allowed(update: Update) -> bool:
    user = update.effective_user
    return bool(user and user.id in WHITELIST_USER_IDS)


def is_private_chat(update: Update) -> bool:
    chat = update.effective_chat
    return bool(chat and chat.type == "private")


def main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [BUTTON_LIST, BUTTON_ARCHIVE],
            [BUTTON_REFRESH_POSTS],
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
    if not is_private_chat(update):
        return False

    if is_allowed(update):
        return True

    if update.callback_query:
        await update.callback_query.answer(msg.access_denied().text, show_alert=True)
        return False

    message = update.effective_message
    if message:
        await reply(message, msg.access_denied())
    return False


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


def deadline_summary_html(deadline: Deadline) -> str:
    context = deadline_context(deadline)
    return (
        f"<b>#{context['id']}</b> • {context['description_preview_html']}\n"
        f"Срок: <b>{context['deadline_line_html']}</b>\n"
        f"Автор: {context['created_by_name_html']}"
    )


def source_title(source: str) -> str:
    if source == SOURCE_ARCHIVE:
        return "Архив"
    return "Список дедлайнов"


def source_for_deadline(deadline: Deadline) -> str:
    return SOURCE_ARCHIVE if deadline.status == STATUS_ARCHIVED else SOURCE_VISIBLE


def clamp_page(page: int, total_pages: int) -> int:
    if total_pages <= 1:
        return 0
    return max(0, min(page, total_pages - 1))


def paginate_items(items: list[Deadline], page: int) -> tuple[list[Deadline], int, int]:
    total_pages = max(1, math.ceil(len(items) / PAGE_SIZE)) if items else 1
    page = clamp_page(page, total_pages)
    start = page * PAGE_SIZE
    end = start + PAGE_SIZE
    return items[start:end], page, total_pages


def callback_list(source: str, page: int) -> str:
    return f"{LIST_CALLBACK}:{source}:{page}"


def callback_open(source: str, page: int, deadline_id: int) -> str:
    return f"{OPEN_CALLBACK}:{source}:{page}:{deadline_id}"


def callback_details(source: str, page: int, deadline_id: int) -> str:
    return f"{DETAILS_CALLBACK}:{source}:{page}:{deadline_id}"


def callback_action(action: str, source: str, page: int, deadline_id: int) -> str:
    return f"{ACTION_CALLBACK}:{action}:{source}:{page}:{deadline_id}"


def callback_create(source: str, page: int) -> str:
    return f"{CREATE_FROM_LIST_CALLBACK}:{source}:{page}"

def list_body_items(items: list[Deadline]) -> str:
    if not items:
        return "Здесь пока ничего нет."

    blocks: list[str] = []
    for deadline in items:
        context = deadline_context(deadline)
        lines = [
            f"<b>#{context['id']}</b> • {context['description_preview_html']}",
            f"Статус: <b>{context['status_label_html']}</b>",
            f"Срок: <b>{context['deadline_line_html']}</b>",
        ]
        if deadline.status in {STATUS_CANCELLED, STATUS_COMPLETED} and deadline.cleanup_after:
            lines.append(f"Очистка сообщений: <b>{escape(format_timestamp(deadline.cleanup_after))}</b>")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def deadline_button_label(deadline: Deadline) -> str:
    prefix = {
        STATUS_ACTIVE: "",
        STATUS_CANCELLED: "[отм] ",
        STATUS_COMPLETED: "[зав] ",
        STATUS_ARCHIVED: "[арх] ",
    }.get(deadline.status, "")
    date_label = deadline.deadline_datetime.strftime("%d.%m")
    return compact_text(f"{prefix}#{deadline.id} • {date_label} • {deadline.description}", 64)


def build_list_keyboard(source: str, page_items: list[Deadline], page: int, total_pages: int) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for deadline in page_items:
        rows.append(
            [
                InlineKeyboardButton(
                    deadline_button_label(deadline),
                    callback_data=callback_open(source, page, deadline.id),
                )
            ]
        )

    navigation: list[InlineKeyboardButton] = []
    if page > 0:
        navigation.append(InlineKeyboardButton("← Назад", callback_data=callback_list(source, page - 1)))
    if page + 1 < total_pages:
        navigation.append(InlineKeyboardButton("Вперёд →", callback_data=callback_list(source, page + 1)))
    if navigation:
        rows.append(navigation)

    if source == SOURCE_VISIBLE:
        rows.append([InlineKeyboardButton("Добавить дедлайн", callback_data=callback_create(source, page))])

    return InlineKeyboardMarkup(rows)


def build_list_screen(source: str, page: int) -> tuple[msg.MessageTemplate, InlineKeyboardMarkup]:
    items = STORE.list_archive() if source == SOURCE_ARCHIVE else STORE.list_visible()
    page_items, actual_page, total_pages = paginate_items(items, page)

    if not items:
        template = msg.no_archive_deadlines() if source == SOURCE_ARCHIVE else msg.no_visible_deadlines()
        keyboard = build_list_keyboard(source, [], actual_page, total_pages)
        return template, keyboard

    template = msg.paginated_list_message(
        source_title(source),
        list_body_items(page_items),
        actual_page + 1,
        total_pages,
    )
    keyboard = build_list_keyboard(source, page_items, actual_page, total_pages)
    return template, keyboard


def build_deadline_card_body(deadline: Deadline) -> str:
    context = deadline_context(deadline)
    lines = [
        context["description_html"],
        "",
        f"Статус: <b>{context['status_label_html']}</b>",
        f"Срок: <b>{context['deadline_line_html']}</b>",
        f"Сообщений в канале: <b>{context['channel_messages_count']}</b>",
    ]
    if deadline.status == STATUS_ACTIVE:
        live_context = live_deadline_context(deadline)
        lines.append(f"{live_context['remaining_label_html']}: <b>{live_context['remaining_value']}</b>")
    if deadline.status in {STATUS_CANCELLED, STATUS_COMPLETED} and deadline.cleanup_after:
        lines.append(f"Очистка сообщений: <b>{escape(format_timestamp(deadline.cleanup_after))}</b>")
    if deadline.status == STATUS_ARCHIVED and deadline.archived_at:
        lines.append(f"Архивирован: <b>{escape(format_timestamp(deadline.archived_at))}</b>")
    return "\n".join(lines)


def build_deadline_card_keyboard(deadline: Deadline, source: str, page: int) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []

    if source != SOURCE_ARCHIVE and deadline.status == STATUS_ACTIVE:
        rows.append(
            [
                InlineKeyboardButton("Изменить", callback_data=callback_action(ACTION_EDIT, source, page, deadline.id)),
                InlineKeyboardButton("Напомнить", callback_data=callback_action(ACTION_REMIND, source, page, deadline.id)),
            ]
        )
        rows.append(
            [
                InlineKeyboardButton("Отменить", callback_data=callback_action(ACTION_CANCEL, source, page, deadline.id)),
                InlineKeyboardButton("Удалить", callback_data=callback_action(ACTION_DELETE, source, page, deadline.id)),
            ]
        )
    elif source != SOURCE_ARCHIVE:
        rows.append(
            [InlineKeyboardButton("Удалить", callback_data=callback_action(ACTION_DELETE, source, page, deadline.id))]
        )

    rows.append([InlineKeyboardButton("Подробности", callback_data=callback_details(source, page, deadline.id))])
    rows.append(
        [
            InlineKeyboardButton(
                "← К списку" if source == SOURCE_VISIBLE else "← К архиву",
                callback_data=callback_list(source, page),
            )
        ]
    )
    return InlineKeyboardMarkup(rows)


def build_deadline_card_screen(deadline: Deadline, source: str, page: int) -> tuple[msg.MessageTemplate, InlineKeyboardMarkup]:
    template = msg.deadline_card_message(
        f"Дедлайн #{deadline.id}",
        build_deadline_card_body(deadline),
    )
    return template, build_deadline_card_keyboard(deadline, source, page)


def change_history_lines(changes: list[dict]) -> list[str]:
    lines: list[str] = []
    for change in changes:
        if change["field"] == "description":
            old_value = escape(compact_text(change["old"], 120))
            new_value = escape(compact_text(change["new"], 120))
            lines.append(f"описание: <code>{old_value}</code> → <code>{new_value}</code>")
        elif change["field"] == "deadline":
            old_value = escape(change["old"])
            new_value = escape(change["new"])
            lines.append(f"дата: <code>{old_value}</code> → <code>{new_value}</code>")
    return lines


def render_history_entry(event: DeadlineEvent) -> str:
    timestamp = escape(format_timestamp(event.at))
    actor = escape(event.actor_name or "бот")

    if event.kind == "created":
        return f"• <b>{timestamp}</b> — {actor} создал дедлайн"
    if event.kind == "initial_skipped":
        return f"• <b>{timestamp}</b> — {actor} пропустил первую публикацию"
    if event.kind == "published":
        source = event.details.get("source", "initial")
        source_label = {
            "initial": "опубликовал дедлайн в канал",
            "edit_publish": "опубликовал обновленный дедлайн в канал",
        }.get(source, "опубликовал дедлайн в канал")
        return f"• <b>{timestamp}</b> — {actor} {source_label}"
    if event.kind == "reminded":
        source = event.details.get("source", "manual")
        source_label = {
            "reminder_7d": "бот отправил напоминание за 7 дней",
            "reminder_24h": "бот отправил напоминание за 24 часа",
            "reminder_manual": f"{actor} отправил ручное напоминание",
        }.get(source, f"{actor} отправил напоминание")
        return f"• <b>{timestamp}</b> — {source_label}"
    if event.kind == "changed":
        lines = [f"• <b>{timestamp}</b> — {actor} изменил дедлайн"]
        for line in change_history_lines(event.details.get("changes", [])):
            lines.append(f"  {line}")
        return "\n".join(lines)
    if event.kind == "cancelled":
        reason = event.details.get("reason")
        if reason:
            return f"• <b>{timestamp}</b> — {actor} отменил дедлайн. Причина: <code>{escape(reason)}</code>"
        return f"• <b>{timestamp}</b> — {actor} отменил дедлайн"
    if event.kind == "completed":
        return f"• <b>{timestamp}</b> — дедлайн завершён"
    if event.kind == "deleted":
        return f"• <b>{timestamp}</b> — {actor} удалил дедлайн из списка и отправил в архив"
    if event.kind == "archived":
        reason = event.details.get("reason", "archive")
        reason_label = {
            "cleanup": "после очистки сообщений",
            "delete": "по команде удаления",
            "legacy": "после миграции данных",
        }.get(reason, "при переносе в архив")
        return f"• <b>{timestamp}</b> — дедлайн попал в архив {reason_label}"
    return f"• <b>{timestamp}</b> — {actor} выполнил действие <code>{escape(event.kind)}</code>"


def render_history(deadline: Deadline, max_chars: int = 3200) -> str:
    if not deadline.history:
        return "История пока пуста."

    entries = [render_history_entry(event) for event in reversed(deadline.history)]
    collected: list[str] = []
    remaining = 0
    for index, entry in enumerate(entries):
        candidate = "\n\n".join(collected + [entry])
        if len(candidate) > max_chars:
            remaining = len(entries) - index
            break
        collected.append(entry)

    if remaining:
        collected.append(f"… ещё {remaining} событий не показано.")
    return "\n\n".join(collected)


def build_deadline_details_body(deadline: Deadline) -> str:
    context = deadline_context(deadline)
    lines = [
        context["description_html"],
        "",
        f"Статус: <b>{context['status_label_html']}</b>",
        f"Срок: <b>{context['deadline_line_html']}</b>",
        f"Создан: <b>{escape(format_timestamp(deadline.created_at))}</b>",
        f"Автор: <b>{context['created_by_name_html']}</b>",
        f"Сообщений в канале: <b>{context['channel_messages_count']}</b>",
        f"Записей в истории: <b>{len(deadline.history)}</b>",
    ]

    if deadline.status == STATUS_ACTIVE:
        live_context = live_deadline_context(deadline)
        lines.append(f"{live_context['remaining_label_html']}: <b>{live_context['remaining_value']}</b>")
    if deadline.cleanup_after:
        lines.append(f"Очистка сообщений: <b>{escape(format_timestamp(deadline.cleanup_after))}</b>")
    if deadline.archived_at:
        lines.append(f"Архивирован: <b>{escape(format_timestamp(deadline.archived_at))}</b>")
    return "\n".join(lines)


def build_deadline_details_keyboard(deadline: Deadline, source: str, page: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("← К дедлайну", callback_data=callback_open(source_for_deadline(deadline), page, deadline.id))],
            [InlineKeyboardButton("← К списку" if source == SOURCE_VISIBLE else "← К архиву", callback_data=callback_list(source, page))],
        ]
    )


def build_deadline_details_screen(deadline: Deadline, source: str, page: int) -> tuple[msg.MessageTemplate, InlineKeyboardMarkup]:
    template = msg.deadline_details_message(
        f"Подробности дедлайна #{deadline.id}",
        build_deadline_details_body(deadline),
        render_history(deadline),
    )
    return template, build_deadline_details_keyboard(deadline, source, page)


async def send_list_screen(message: Message, source: str, page: int = 0) -> None:
    template, keyboard = build_list_screen(source, page)
    await reply(message, template, reply_markup=keyboard)


async def edit_query_screen(query, template: msg.MessageTemplate, keyboard: InlineKeyboardMarkup) -> None:
    try:
        await query.edit_message_text(
            template.text,
            parse_mode=template.parse_mode,
            reply_markup=keyboard,
        )
    except BadRequest as exc:
        if "message is not modified" not in str(exc).lower():
            raise


async def update_message_screen(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    message_id: int,
    template: msg.MessageTemplate,
    keyboard: InlineKeyboardMarkup,
) -> None:
    try:
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=template.text,
            parse_mode=template.parse_mode,
            reply_markup=keyboard,
        )
    except BadRequest as exc:
        if "message is not modified" not in str(exc).lower():
            LOGGER.warning("Failed to update message %s in chat %s: %s", message_id, chat_id, exc)
    except Exception as exc:
        LOGGER.warning("Failed to update message %s in chat %s: %s", message_id, chat_id, exc)


def remember_screen_origin(query, source: str, page: int) -> dict:
    # When a conversation starts from an inline screen, remember that message so
    # we can refresh the original list/card after the flow finishes.
    return {
        "source": source,
        "page": page,
        "chat_id": query.message.chat_id if query.message else None,
        "message_id": query.message.message_id if query.message else None,
    }


async def sync_create_origin(context: ContextTypes.DEFAULT_TYPE) -> None:
    origin = context.user_data.get("create_origin")
    if not origin:
        return
    template, keyboard = build_list_screen(origin["source"], origin["page"])
    await update_message_screen(context, origin["chat_id"], origin["message_id"], template, keyboard)


async def sync_edit_origin(context: ContextTypes.DEFAULT_TYPE, deadline: Deadline) -> None:
    origin = context.user_data.get("edit_origin")
    if not origin:
        return

    source = source_for_deadline(deadline)
    page = origin["page"] if source == origin["source"] else 0
    template, keyboard = build_deadline_card_screen(deadline, source, page)
    await update_message_screen(context, origin["chat_id"], origin["message_id"], template, keyboard)


async def sync_cancel_origin(context: ContextTypes.DEFAULT_TYPE, deadline: Deadline) -> None:
    origin = context.user_data.get("cancel_origin")
    if not origin:
        return

    source = source_for_deadline(deadline)
    page = origin["page"] if source == origin["source"] else 0
    template, keyboard = build_deadline_card_screen(deadline, source, page)
    await update_message_screen(context, origin["chat_id"], origin["message_id"], template, keyboard)

def render_channel_template(record: ChannelMessageRecord) -> msg.MessageTemplate:
    data = record.template_data
    if not data:
        raise ValueError("legacy channel message without template data")

    # Channel posts are rebuilt from structured template_data rather than from
    # their stored text so message templates can evolve later via refresh.
    if record.kind in {"initial", "reminder_7d", "reminder_24h", "reminder_manual"}:
        return msg.active_deadline_post(live_deadline_context_from_payload(data["deadline"]))
    if record.kind == "cancelled":
        return msg.deadline_cancelled_post_with_reason(
            data["deadline"],
            data.get("reason_html", escape(data.get("reason", "не указана"))),
        )
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


async def delete_all_deadline_messages(context: ContextTypes.DEFAULT_TYPE, deadline: Deadline) -> None:
    for record in list(deadline.channel_messages):
        try:
            await context.bot.delete_message(chat_id=CHANNEL_ID, message_id=record.message_id)
        except Exception as exc:
            LOGGER.warning("Failed to delete message %s for deadline %s: %s", record.message_id, deadline.id, exc)
    deadline.channel_messages = []
    await STORE.update(deadline)


async def delete_deadline_records(
    context: ContextTypes.DEFAULT_TYPE,
    deadline: Deadline,
    records: list[ChannelMessageRecord],
) -> list[ChannelMessageRecord]:
    failed: list[ChannelMessageRecord] = []
    for record in records:
        try:
            await context.bot.delete_message(chat_id=CHANNEL_ID, message_id=record.message_id)
        except Exception as exc:
            LOGGER.warning("Failed to delete message %s for deadline %s: %s", record.message_id, deadline.id, exc)
            failed.append(record)
    return failed


async def replace_deadline_messages_with_template(
    context: ContextTypes.DEFAULT_TYPE,
    deadline: Deadline,
    template: msg.MessageTemplate,
    *,
    kind: str,
    template_data: dict,
) -> Message:
    old_records = list(deadline.channel_messages)
    sent = await post_channel_template(
        context,
        deadline,
        template,
        kind=kind,
        template_data=template_data,
    )
    if not old_records:
        return sent

    new_record = deadline.channel_messages[-1]
    failed_old_records = await delete_deadline_records(context, deadline, old_records)
    deadline.channel_messages = failed_old_records + [new_record]
    await STORE.update(deadline)
    return sent


async def publish_live_deadline_post(
    context: ContextTypes.DEFAULT_TYPE,
    deadline: Deadline,
    *,
    kind: str,
    replace_existing: bool,
    actor_id: int | None = None,
    actor_name: str | None = None,
) -> None:
    template_data = live_deadline_template_data(deadline)
    if replace_existing:
        await replace_deadline_messages_with_template(
            context,
            deadline,
            msg.active_deadline_post(template_data["deadline"]),
            kind=kind,
            template_data=template_data,
        )
    else:
        await post_channel_template(
            context,
            deadline,
            msg.active_deadline_post(template_data["deadline"]),
            kind=kind,
            template_data=template_data,
        )
    deadline.initial_published = True
    if kind == "initial":
        add_history_event(
            deadline,
            "published",
            actor_id=actor_id,
            actor_name=actor_name,
            details={"source": "initial"},
        )
    else:
        add_history_event(
            deadline,
            "reminded",
            actor_id=actor_id,
            actor_name=actor_name,
            details={"source": kind},
        )
    await STORE.update(deadline)


async def maybe_send_initial_publication(
    context: ContextTypes.DEFAULT_TYPE,
    deadline: Deadline,
    *,
    force: bool,
    actor_id: int | None = None,
    actor_name: str | None = None,
) -> None:
    if now_until(deadline.deadline_datetime) <= timedelta(days=7) or force:
        await publish_live_deadline_post(
            context,
            deadline,
            kind="initial",
            replace_existing=True,
            actor_id=actor_id,
            actor_name=actor_name,
        )


async def align_reminder_flags(deadline: Deadline) -> None:
    remaining = now_until(deadline.deadline_datetime)
    if remaining <= timedelta(days=7):
        deadline.reminded_7d = True
    if remaining <= timedelta(hours=24):
        deadline.reminded_24h = True
    await STORE.update(deadline)


async def cancel_deadline(
    context: ContextTypes.DEFAULT_TYPE,
    deadline: Deadline,
    *,
    reason: str,
    reason_html: str,
    actor_id: int | None = None,
    actor_name: str | None = None,
) -> None:
    original_context = deadline_context(deadline)
    deadline.status = STATUS_CANCELLED
    deadline.cleanup_after = (bot_now() + timedelta(days=3)).isoformat()
    add_history_event(
        deadline,
        "cancelled",
        actor_id=actor_id,
        actor_name=actor_name,
        details={"reason": reason},
    )
    await STORE.update(deadline)

    template_data = cancelled_template_data(original_context, reason, reason_html)
    await replace_deadline_messages_with_template(
        context,
        deadline,
        msg.deadline_cancelled_post_with_reason(template_data["deadline"], template_data["reason_html"]),
        kind="cancelled",
        template_data=template_data,
    )


async def delete_deadline(
    context: ContextTypes.DEFAULT_TYPE,
    deadline: Deadline,
    *,
    actor_id: int | None = None,
    actor_name: str | None = None,
) -> None:
    await delete_all_deadline_messages(context, deadline)
    deadline.status = STATUS_ARCHIVED
    deadline.archived_at = bot_now().isoformat()
    deadline.cleanup_after = None
    add_history_event(deadline, "deleted", actor_id=actor_id, actor_name=actor_name)
    add_history_event(deadline, "archived", actor_name="бот", details={"reason": "delete"})
    await STORE.update(deadline)


async def remind_deadline(
    context: ContextTypes.DEFAULT_TYPE,
    deadline: Deadline,
    *,
    actor_id: int | None = None,
    actor_name: str | None = None,
) -> None:
    await publish_live_deadline_post(
        context,
        deadline,
        kind="reminder_manual",
        replace_existing=True,
        actor_id=actor_id,
        actor_name=actor_name,
    )
    await align_reminder_flags(deadline)


async def mark_deadline_completed(context: ContextTypes.DEFAULT_TYPE, deadline: Deadline) -> None:
    if deadline.status != STATUS_ACTIVE:
        return

    deadline.status = STATUS_COMPLETED
    deadline.cleanup_after = (bot_now() + timedelta(days=3)).isoformat()
    add_history_event(deadline, "completed", actor_name="бот")

    if deadline.channel_messages:
        if len(deadline.channel_messages) > 1:
            last_record = deadline.channel_messages[-1]
            failed_old_records = await delete_deadline_records(context, deadline, deadline.channel_messages[:-1])
            deadline.channel_messages = failed_old_records + [last_record]

        last_record = deadline.channel_messages[-1]
        template_data = deadline_template_data(deadline)
        template = msg.deadline_completed_post(template_data["deadline"])
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
            last_record.template_data = template_data
        except Exception as exc:
            LOGGER.warning("Failed to edit completion message for deadline %s: %s", deadline.id, exc)
            await replace_deadline_messages_with_template(
                context,
                deadline,
                template,
                kind="completed",
                template_data=template_data,
            )

    await STORE.update(deadline)


async def archive_after_cleanup(context: ContextTypes.DEFAULT_TYPE, deadline: Deadline) -> None:
    await delete_all_deadline_messages(context, deadline)
    deadline.status = STATUS_ARCHIVED
    deadline.archived_at = bot_now().isoformat()
    deadline.cleanup_after = None
    add_history_event(deadline, "archived", actor_name="бот", details={"reason": "cleanup"})
    await STORE.update(deadline)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await require_whitelist(update):
        return ConversationHandler.END
    context.user_data.clear()
    await reply(update.effective_message, msg.start_message(), reply_markup=main_keyboard())
    return ConversationHandler.END


async def show_current_time(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_whitelist(update):
        return
    current_time = bot_now().strftime("%d.%m.%Y %H:%M")
    await reply(
        update.effective_message,
        msg.current_time_message(current_time, BOT_TIMEZONE_LABEL),
        reply_markup=main_keyboard(),
    )


async def show_visible_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_whitelist(update):
        return
    await send_list_screen(update.effective_message, SOURCE_VISIBLE, page=0)


async def show_archive_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_whitelist(update):
        return
    await send_list_screen(update.effective_message, SOURCE_ARCHIVE, page=0)


async def refresh_channel_posts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_whitelist(update):
        return

    all_deadlines = STORE.list_all()
    total_messages = sum(len(deadline.channel_messages) for deadline in all_deadlines)
    if total_messages == 0:
        await reply(update.effective_message, msg.no_channel_posts_to_refresh(), reply_markup=main_keyboard())
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
        update.effective_message,
        msg.refreshed_channel_posts(updated, unchanged, skipped, failed),
        reply_markup=main_keyboard(),
    )


async def maybe_handle_menu_navigation(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> tuple[bool, int | None]:
    message = update.effective_message
    if message is None or not message.text:
        return False, None

    text = message.text.strip()
    if text == BUTTON_ABORT:
        return True, await abort_conversation(update, context)
    if text == BUTTON_LIST:
        await show_visible_list(update, context)
        return True, ConversationHandler.END
    if text == BUTTON_ARCHIVE:
        await show_archive_list(update, context)
        return True, ConversationHandler.END
    if text == BUTTON_REFRESH_POSTS:
        await refresh_channel_posts(update, context)
        return True, ConversationHandler.END
    return False, None


async def create_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await require_whitelist(update):
        return ConversationHandler.END

    context.user_data.pop("create_origin", None)
    target = update.effective_message
    query = update.callback_query
    if query:
        await query.answer()
        _, source, raw_page = query.data.split(":")
        context.user_data["create_origin"] = remember_screen_origin(query, source, int(raw_page))
        target = query.message

    await reply(target, msg.create_prompt_description(), reply_markup=input_keyboard())
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

    actor_id, actor_name = actor_from_update(update)
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
    add_history_event(deadline, "created", actor_id=actor_id, actor_name=actor_name, at=deadline.created_at)
    saved = await STORE.add(deadline)

    if now_until(deadline_at) > timedelta(days=7):
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("Опубликовать", callback_data=f"{IMMEDIATE_CALLBACK}:yes:{saved.id}"),
                    InlineKeyboardButton("Не публиковать", callback_data=f"{IMMEDIATE_CALLBACK}:no:{saved.id}"),
                ]
            ]
        )
        await reply(update.message, msg.initial_publish_question(deadline_summary_html(saved)), reply_markup=keyboard)
        return CREATE_CONFIRM

    await maybe_send_initial_publication(
        context,
        saved,
        force=False,
        actor_id=actor_id,
        actor_name=actor_name,
    )
    await align_reminder_flags(saved)
    await reply(update.message, msg.deadline_saved_and_published(), reply_markup=main_keyboard())
    await sync_create_origin(context)
    return ConversationHandler.END

async def create_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await require_whitelist(update):
        return ConversationHandler.END

    query = update.callback_query
    await query.answer()
    _, answer, raw_id = query.data.split(":")
    deadline = STORE.get(int(raw_id))
    if deadline is None:
        template = msg.deadline_missing()
        await query.edit_message_text(template.text, parse_mode=template.parse_mode)
        return ConversationHandler.END

    actor_id, actor_name = actor_from_update(update)
    if answer == "yes":
        await maybe_send_initial_publication(
            context,
            deadline,
            force=True,
            actor_id=actor_id,
            actor_name=actor_name,
        )
        template = msg.deadline_saved_and_published_now()
    else:
        deadline.immediate_publish_skipped = True
        add_history_event(deadline, "initial_skipped", actor_id=actor_id, actor_name=actor_name)
        await STORE.update(deadline)
        template = msg.deadline_saved_skip_initial()

    await align_reminder_flags(deadline)
    await query.edit_message_text(template.text, parse_mode=template.parse_mode)
    if query.message:
        await reply(query.message, msg.start_message(), reply_markup=main_keyboard())
    await sync_create_origin(context)
    return ConversationHandler.END


async def edit_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await require_whitelist(update):
        return ConversationHandler.END

    query = update.callback_query
    await query.answer()
    _, _, source, raw_page, raw_id = query.data.split(":")
    deadline = STORE.get(int(raw_id))
    if deadline is None or deadline.status != STATUS_ACTIVE:
        template = msg.deadline_missing()
        await query.edit_message_text(template.text, parse_mode=template.parse_mode)
        return ConversationHandler.END

    context.user_data["edit_deadline_id"] = deadline.id
    context.user_data["edit_original_description"] = deadline.description
    context.user_data["edit_original_description_html"] = deadline.description_html
    context.user_data["edit_original_deadline_at"] = deadline.deadline_at
    context.user_data["edit_original_time_was_provided"] = deadline.time_was_provided
    context.user_data["edit_original_time_was_explicit_midnight"] = deadline.time_was_explicit_midnight
    context.user_data["edit_origin"] = remember_screen_origin(query, source, int(raw_page))
    await reply(
        query.message,
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
    old_payload["history"] = [asdict(item) for item in deadline.history]
    old_deadline = Deadline(
        **{
            **old_payload,
            "channel_messages": [ChannelMessageRecord(**record) for record in old_payload["channel_messages"]],
            "history": [DeadlineEvent(**event) for event in old_payload["history"]],
        }
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
    actor_id, actor_name = actor_from_update(update)
    add_history_event(
        deadline,
        "changed",
        actor_id=actor_id,
        actor_name=actor_name,
        details={"changes": changes},
    )
    context.user_data["edit_changes"] = changes
    context.user_data["edit_old_context"] = deadline_context(old_deadline)
    context.user_data["edit_new_context"] = deadline_context(deadline)

    if had_any_publication:
        deadline.immediate_publish_skipped = False
        await STORE.update(deadline)
        template_data = changed_template_data(changes, deadline_context(old_deadline), deadline_context(deadline))
        await replace_deadline_messages_with_template(
            context,
            deadline,
            msg.deadline_changed_post(changes, template_data["old_deadline"], template_data["new_deadline"]),
            kind="changed",
            template_data=template_data,
        )
        await align_reminder_flags(deadline)
        await reply(update.message, msg.deadline_changed_notice(), reply_markup=main_keyboard())
        await sync_edit_origin(context, deadline)
        return ConversationHandler.END

    if now_until(deadline_at) <= timedelta(days=7):
        deadline.immediate_publish_skipped = False
        await STORE.update(deadline)
        await align_reminder_flags(deadline)
        await maybe_send_initial_publication(
            context,
            deadline,
            force=False,
            actor_id=actor_id,
            actor_name=actor_name,
        )
        await reply(update.message, msg.deadline_changed_actual_published(), reply_markup=main_keyboard())
        await sync_edit_origin(context, deadline)
        return ConversationHandler.END

    await STORE.update(deadline)
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Опубликовать", callback_data=f"{EDIT_IMMEDIATE_CALLBACK}:yes:{deadline.id}"),
                InlineKeyboardButton("Не публиковать", callback_data=f"{EDIT_IMMEDIATE_CALLBACK}:no:{deadline.id}"),
            ]
        ]
    )
    question = msg.edit_publish_question_with_change() if had_any_publication else msg.edit_publish_question_without_change()
    context.user_data["edit_had_any_publication"] = had_any_publication
    await reply(update.message, question, reply_markup=keyboard)
    return EDIT_CONFIRM


async def edit_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await require_whitelist(update):
        return ConversationHandler.END

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
    actor_id, actor_name = actor_from_update(update)

    if had_any_publication:
        template_data = changed_template_data(changes, old_context, new_context)
        await replace_deadline_messages_with_template(
            context,
            deadline,
            msg.deadline_changed_post(changes, template_data["old_deadline"], template_data["new_deadline"]),
            kind="changed",
            template_data=template_data,
        )
        await align_reminder_flags(deadline)
        template = msg.deadline_changed_notice()
        await query.edit_message_text(template.text, parse_mode=template.parse_mode)
        if query.message:
            await reply(query.message, msg.start_message(), reply_markup=main_keyboard())
        await sync_edit_origin(context, deadline)
        return ConversationHandler.END

    if answer == "yes":
        deadline.initial_published = False
        deadline.immediate_publish_skipped = False
        await STORE.update(deadline)
        await maybe_send_initial_publication(
            context,
            deadline,
            force=True,
            actor_id=actor_id,
            actor_name=actor_name,
        )
        template = msg.edit_saved_with_change_and_publish() if had_any_publication else msg.edit_saved_published()
    else:
        deadline.immediate_publish_skipped = True
        await STORE.update(deadline)
        template = msg.edit_saved_with_change_no_publish() if had_any_publication else msg.edit_saved_no_publish()
    await align_reminder_flags(deadline)
    await query.edit_message_text(template.text, parse_mode=template.parse_mode)
    if query.message:
        await reply(query.message, msg.start_message(), reply_markup=main_keyboard())
    await sync_edit_origin(context, deadline)
    return ConversationHandler.END


async def cancel_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await require_whitelist(update):
        return ConversationHandler.END

    query = update.callback_query
    await query.answer()
    _, _, source, raw_page, raw_id = query.data.split(":")
    deadline = STORE.get(int(raw_id))
    if deadline is None or deadline.status != STATUS_ACTIVE:
        template = msg.deadline_missing()
        await query.edit_message_text(template.text, parse_mode=template.parse_mode)
        return ConversationHandler.END

    context.user_data["cancel_deadline_id"] = deadline.id
    context.user_data["cancel_origin"] = remember_screen_origin(query, source, int(raw_page))
    await reply(
        query.message,
        msg.cancel_prompt_reason(deadline_context(deadline)),
        reply_markup=input_keyboard(),
    )
    return CANCEL_REASON


async def cancel_reason(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    handled, next_state = await maybe_handle_menu_navigation(update, context)
    if handled:
        return next_state if next_state is not None else ConversationHandler.END

    deadline = STORE.get(context.user_data["cancel_deadline_id"])
    if deadline is None or deadline.status != STATUS_ACTIVE:
        await reply(update.message, msg.deadline_missing(), reply_markup=main_keyboard())
        return ConversationHandler.END

    reason = update.message.text.strip()
    if not reason:
        await reply(
            update.message,
            msg.MessageTemplate("Причина отмены не должна быть пустой."),
            reply_markup=input_keyboard(),
        )
        return CANCEL_REASON
    reason_html = update.message.text_html or escape(reason)
    actor_id, actor_name = actor_from_update(update)
    await cancel_deadline(
        context,
        deadline,
        reason=reason,
        reason_html=reason_html,
        actor_id=actor_id,
        actor_name=actor_name,
    )
    await reply(update.message, msg.deadline_cancelled_private(), reply_markup=main_keyboard())
    await sync_cancel_origin(context, deadline)
    return ConversationHandler.END


async def list_page_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_whitelist(update):
        return

    query = update.callback_query
    await query.answer()
    _, source, raw_page = query.data.split(":")
    template, keyboard = build_list_screen(source, int(raw_page))
    await edit_query_screen(query, template, keyboard)


async def open_deadline_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_whitelist(update):
        return

    query = update.callback_query
    await query.answer()
    _, source, raw_page, raw_id = query.data.split(":")
    deadline = STORE.get(int(raw_id))
    if deadline is None:
        template = msg.deadline_missing()
        await query.edit_message_text(template.text, parse_mode=template.parse_mode)
        return

    actual_source = source_for_deadline(deadline)
    page = int(raw_page) if actual_source == source else 0
    template, keyboard = build_deadline_card_screen(deadline, actual_source, page)
    await edit_query_screen(query, template, keyboard)


async def details_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_whitelist(update):
        return

    query = update.callback_query
    await query.answer()
    _, source, raw_page, raw_id = query.data.split(":")
    deadline = STORE.get(int(raw_id))
    if deadline is None:
        template = msg.deadline_missing()
        await query.edit_message_text(template.text, parse_mode=template.parse_mode)
        return

    actual_source = source_for_deadline(deadline)
    page = int(raw_page) if actual_source == source else 0
    template, keyboard = build_deadline_details_screen(deadline, actual_source, page)
    await edit_query_screen(query, template, keyboard)


async def deadline_action_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_whitelist(update):
        return

    query = update.callback_query
    await query.answer()
    _, action, source, raw_page, raw_id = query.data.split(":")
    deadline = STORE.get(int(raw_id))
    if deadline is None:
        template = msg.deadline_missing()
        await query.edit_message_text(template.text, parse_mode=template.parse_mode)
        return

    page = int(raw_page)
    actor_id, actor_name = actor_from_update(update)

    if action == ACTION_REMIND:
        if deadline.status != STATUS_ACTIVE:
            await query.answer("Напоминание доступно только для активного дедлайна.", show_alert=True)
            return
        await remind_deadline(context, deadline, actor_id=actor_id, actor_name=actor_name)
        template, keyboard = build_deadline_card_screen(deadline, SOURCE_VISIBLE, page)
        await edit_query_screen(query, template, keyboard)
        return

    if action == ACTION_DELETE:
        await delete_deadline(context, deadline, actor_id=actor_id, actor_name=actor_name)
        template, keyboard = build_deadline_card_screen(deadline, SOURCE_ARCHIVE, 0)
        await edit_query_screen(query, template, keyboard)
        return

async def abort_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await reply(update.effective_message, msg.cancelled(), reply_markup=main_keyboard())
    return ConversationHandler.END


async def reminder_loop(context: ContextTypes.DEFAULT_TYPE) -> None:
    # One repeating loop handles reminders and delayed cleanup. After restart the
    # loop simply reconciles current state, so there are no in-memory schedules.
    for deadline in STORE.list_all():
        if deadline.status == STATUS_ACTIVE:
            remaining = now_until(deadline.deadline_datetime)
            if remaining <= timedelta(0):
                await mark_deadline_completed(context, deadline)
                continue

            if not deadline.reminded_7d and remaining <= timedelta(days=7):
                await publish_live_deadline_post(
                    context,
                    deadline,
                    kind="reminder_7d",
                    replace_existing=True,
                    actor_name="бот",
                )
                deadline.reminded_7d = True
                await STORE.update(deadline)

            if not deadline.reminded_24h and remaining <= timedelta(hours=24):
                await publish_live_deadline_post(
                    context,
                    deadline,
                    kind="reminder_24h",
                    replace_existing=True,
                    actor_name="бот",
                )
                deadline.reminded_24h = True
                await STORE.update(deadline)
            continue

        if deadline.status in {STATUS_CANCELLED, STATUS_COMPLETED}:
            cleanup_at = deadline.cleanup_after_datetime
            if cleanup_at and bot_now() >= cleanup_at:
                await archive_after_cleanup(context, deadline)


def build_application() -> Application:
    if not WHITELIST_USER_IDS:
        raise RuntimeError("WHITELIST_USER_IDS is empty. Add one or more Telegram user ids to .env.")

    application = Application.builder().token(BOT_TOKEN).build()
    private_chat = filters.ChatType.PRIVATE

    create_conversation = ConversationHandler(
        entry_points=[
            CommandHandler("new", create_start, filters=private_chat),
            CallbackQueryHandler(create_start, pattern=f"^{CREATE_FROM_LIST_CALLBACK}:"),
        ],
        states={
            CREATE_DESCRIPTION: [MessageHandler(private_chat & filters.TEXT & ~filters.COMMAND, create_description)],
            CREATE_DATETIME: [MessageHandler(private_chat & filters.TEXT & ~filters.COMMAND, create_datetime)],
            CREATE_CONFIRM: [CallbackQueryHandler(create_confirm, pattern=f"^{IMMEDIATE_CALLBACK}:")],
        },
        fallbacks=[
            CommandHandler("start", start, filters=private_chat),
            CommandHandler("cancel", abort_conversation, filters=private_chat),
            MessageHandler(private_chat & filters.Regex(f"^{BUTTON_ABORT}$"), abort_conversation),
        ],
    )

    edit_conversation = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(edit_start, pattern=f"^{ACTION_CALLBACK}:{ACTION_EDIT}:"),
        ],
        states={
            EDIT_DESCRIPTION: [MessageHandler(private_chat & filters.TEXT & ~filters.COMMAND, edit_description)],
            EDIT_DATETIME: [MessageHandler(private_chat & filters.TEXT & ~filters.COMMAND, edit_datetime)],
            EDIT_CONFIRM: [CallbackQueryHandler(edit_confirm, pattern=f"^{EDIT_IMMEDIATE_CALLBACK}:")],
        },
        fallbacks=[
            CommandHandler("start", start, filters=private_chat),
            CommandHandler("cancel", abort_conversation, filters=private_chat),
            MessageHandler(private_chat & filters.Regex(f"^{BUTTON_ABORT}$"), abort_conversation),
        ],
    )

    cancel_conversation = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(cancel_start, pattern=f"^{ACTION_CALLBACK}:{ACTION_CANCEL}:"),
        ],
        states={
            CANCEL_REASON: [MessageHandler(private_chat & filters.TEXT & ~filters.COMMAND, cancel_reason)],
        },
        fallbacks=[
            CommandHandler("start", start, filters=private_chat),
            CommandHandler("cancel", abort_conversation, filters=private_chat),
            MessageHandler(private_chat & filters.Regex(f"^{BUTTON_ABORT}$"), abort_conversation),
        ],
    )

    application.add_handler(CommandHandler("start", start, filters=private_chat))
    application.add_handler(CommandHandler("now", show_current_time, filters=private_chat))
    application.add_handler(CommandHandler("list", show_visible_list, filters=private_chat))
    application.add_handler(CommandHandler("archive", show_archive_list, filters=private_chat))
    application.add_handler(CommandHandler("refresh_posts", refresh_channel_posts, filters=private_chat))
    application.add_handler(CommandHandler("cancel", abort_conversation, filters=private_chat))
    application.add_handler(create_conversation)
    application.add_handler(edit_conversation)
    application.add_handler(cancel_conversation)
    application.add_handler(CallbackQueryHandler(list_page_callback, pattern=f"^{LIST_CALLBACK}:"))
    application.add_handler(CallbackQueryHandler(open_deadline_callback, pattern=f"^{OPEN_CALLBACK}:"))
    application.add_handler(CallbackQueryHandler(details_callback, pattern=f"^{DETAILS_CALLBACK}:"))
    application.add_handler(
        CallbackQueryHandler(
            deadline_action_callback,
            pattern=f"^{ACTION_CALLBACK}:({ACTION_DELETE}|{ACTION_REMIND}):",
        )
    )
    application.add_handler(MessageHandler(private_chat & filters.Regex(f"^{BUTTON_LIST}$"), show_visible_list))
    application.add_handler(MessageHandler(private_chat & filters.Regex(f"^{BUTTON_ARCHIVE}$"), show_archive_list))
    application.add_handler(MessageHandler(private_chat & filters.Regex(f"^{BUTTON_REFRESH_POSTS}$"), refresh_channel_posts))
    application.add_handler(MessageHandler(private_chat & filters.Regex(f"^{BUTTON_ABORT}$"), abort_conversation))
    application.add_handler(MessageHandler(private_chat & filters.TEXT & ~filters.COMMAND, start))

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
