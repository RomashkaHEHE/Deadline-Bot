from __future__ import annotations

from dataclasses import dataclass
import os

from dotenv import load_dotenv


load_dotenv()


@dataclass(frozen=True)
class MessageTemplate:
    # Each template returns the exact payload that will be sent to Telegram.
    # Keep Telegram HTML and channel-only footer text here, not in app.py.
    text: str
    parse_mode: str | None = "HTML"


BUTTON_LIST = "Список дедлайнов"
BUTTON_ARCHIVE = "Архив"
BUTTON_REFRESH_POSTS = "Обновить посты"
BUTTON_SKIP = "Пропустить"
BUTTON_ABORT = "Отмена"

EMOJIS = {
    # Central registry for reusable Telegram custom emoji snippets.
    "soon": '<tg-emoji emoji-id="5440621591387980068">🔜</tg-emoji>',
}


def env_flag(name: str, default: bool = True) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


INCLUDE_DEADLINE_HASHTAG = env_flag("INCLUDE_DEADLINE_HASHTAG", True)
DEADLINE_CHANNEL_FOOTER = f"{EMOJIS['soon']}  #дедлайн" if INCLUDE_DEADLINE_HASHTAG else ""


def access_denied() -> MessageTemplate:
    return MessageTemplate("У вас нет доступа к этому боту.")


def start_message() -> MessageTemplate:
    return MessageTemplate(
        "Выберите действие на клавиатуре ниже.\n"
        "Новый дедлайн можно создать через <code>/new</code> или кнопкой внутри списка дедлайнов."
    )


def current_time_message(current_time: str, timezone_label: str) -> MessageTemplate:
    return MessageTemplate(
        f"Сейчас по мнению бота:\n<b>{current_time}</b>\nЧасовой пояс: <b>{timezone_label}</b>"
    )


def no_visible_deadlines() -> MessageTemplate:
    return MessageTemplate(
        "В основном списке пока нет дедлайнов.\n"
        "Создайте новый дедлайн кнопкой ниже или командой <code>/new</code>."
    )


def no_archive_deadlines() -> MessageTemplate:
    return MessageTemplate("Архив пока пуст.")


def no_channel_posts_to_refresh() -> MessageTemplate:
    return MessageTemplate("В канале пока нет сообщений, которые можно обновить.")


def paginated_list_message(title: str, body: str, page: int, total_pages: int) -> MessageTemplate:
    return MessageTemplate(
        f"<b>{title}</b>\n"
        f"Страница <b>{page}</b> / <b>{total_pages}</b>\n\n"
        f"{body}"
    )


def deadline_card_message(title: str, body: str) -> MessageTemplate:
    return MessageTemplate(f"<b>{title}</b>\n\n{body}")


def deadline_details_message(title: str, body: str, history: str) -> MessageTemplate:
    return MessageTemplate(
        f"<b>{title}</b>\n\n"
        f"{body}\n\n"
        f"<b>История</b>\n"
        f"{history}"
    )


def create_prompt_description() -> MessageTemplate:
    return MessageTemplate("Отправьте описание дедлайна.")


def create_prompt_datetime(timezone_label: str) -> MessageTemplate:
    return MessageTemplate(
        f"Теперь отправьте дату и опционально время в часовом поясе <b>{timezone_label}</b>.\n"
        "Пример: <code>13.04.2026</code> или <code>13.04.2026 18:30</code>"
    )


def invalid_datetime_format() -> MessageTemplate:
    return MessageTemplate(
        "Введите дату как <code>DD.MM.YYYY</code> и опционально время как <code>HH:MM</code>."
    )


def invalid_date_format() -> MessageTemplate:
    return MessageTemplate("Дата должна быть в формате <code>DD.MM.YYYY</code>.")


def invalid_date_value() -> MessageTemplate:
    return MessageTemplate("Такой даты не существует.")


def invalid_time_format() -> MessageTemplate:
    return MessageTemplate("Время должно быть в формате <code>HH:MM</code>.")


def invalid_time_value() -> MessageTemplate:
    return MessageTemplate("Такого времени не существует.")


def deadline_must_be_future() -> MessageTemplate:
    return MessageTemplate("Дедлайн должен быть в будущем.")


def initial_publish_question(deadline_summary_html: str) -> MessageTemplate:
    return MessageTemplate(
        "До дедлайна больше недели.\n"
        "Опубликовать первое сообщение сейчас?\n\n"
        f"{deadline_summary_html}"
    )


def deadline_saved_and_published() -> MessageTemplate:
    return MessageTemplate("Дедлайн сохранен и опубликован в канал.")


def deadline_saved_and_published_now() -> MessageTemplate:
    return MessageTemplate("Дедлайн сохранен и сразу опубликован.")


def deadline_saved_skip_initial() -> MessageTemplate:
    return MessageTemplate(
        "Дедлайн сохранен. Первое сообщение пропущено, но напоминания за 7 дней и 24 часа останутся."
    )


def deadline_missing() -> MessageTemplate:
    return MessageTemplate("Дедлайн уже не найден.")


def deadline_cancelled_private() -> MessageTemplate:
    return MessageTemplate("Дедлайн отменен.")


def deadline_deleted_private() -> MessageTemplate:
    return MessageTemplate("Дедлайн удален и отправлен в архив.")


def deadline_reminded_private() -> MessageTemplate:
    return MessageTemplate("Напоминание опубликовано. Старые сообщения по дедлайну удалены.")


def refreshed_channel_posts(updated: int, unchanged: int, skipped: int, failed: int) -> MessageTemplate:
    lines = [
        "Обновление постов завершено.",
        f"Обновлено: <b>{updated}</b>",
        f"Без изменений: <b>{unchanged}</b>",
    ]
    if skipped:
        lines.append(f"Пропущено: <b>{skipped}</b>")
        lines.append("Обычно это старые посты, для которых бот еще не хранил структурированные данные шаблона.")
    if failed:
        lines.append(f"С ошибкой: <b>{failed}</b>")
    return MessageTemplate("\n".join(lines))


def edit_prompt_description(deadline: dict) -> MessageTemplate:
    return MessageTemplate(
        "Отправьте новое описание дедлайна или нажмите <b>Пропустить</b>.\n\n"
        f"Текущее:\n{deadline['description_html']}"
    )


def edit_prompt_datetime(timezone_label: str) -> MessageTemplate:
    return MessageTemplate(
        f"Теперь отправьте новую дату и опционально время в часовом поясе <b>{timezone_label}</b> "
        "или нажмите <b>Пропустить</b>.\n"
        "Пример: <code>13.04.2026</code> или <code>13.04.2026 18:30</code>"
    )


def no_changes() -> MessageTemplate:
    return MessageTemplate("Изменений нет, дедлайн оставлен без изменений.")


def deadline_changed_post(changes: list[dict], old_deadline: dict, new_deadline: dict) -> MessageTemplate:
    lines: list[str] = []
    for change in changes:
        if change["field"] == "description":
            lines.append(f"<s>{change['old_html']}</s>\n↓\n{change['new_html']}")
        elif change["field"] == "deadline":
            lines.append(f"<s>{change['old_html']}</s> → {change['new_html']}")

    if not lines:
        lines.append(f"{old_deadline['description_html']}\n{new_deadline['deadline_line_html']}")
    footer = f"\n\n{DEADLINE_CHANNEL_FOOTER}" if DEADLINE_CHANNEL_FOOTER else ""
    return MessageTemplate("\n".join(lines) + footer)


def deadline_changed_notice() -> MessageTemplate:
    return MessageTemplate("Дедлайн изменен. Сообщение о переносе отправлено в канал.")


def deadline_changed_actual_published() -> MessageTemplate:
    return MessageTemplate("Дедлайн изменен. Актуальная версия опубликована в канал.")


def edit_publish_question_with_change() -> MessageTemplate:
    return MessageTemplate(
        "Дедлайн изменен. Сообщение о переносе уйдет в канал. Отдельно опубликовать обновленный дедлайн прямо сейчас?"
    )


def edit_publish_question_without_change() -> MessageTemplate:
    return MessageTemplate("До нового дедлайна больше недели.\nОпубликовать обновленный дедлайн сейчас?")


def edit_saved_with_change_and_publish() -> MessageTemplate:
    return MessageTemplate(
        "Изменение сохранено: в канал отправлены сообщение о переносе и обновленный дедлайн."
    )


def edit_saved_published() -> MessageTemplate:
    return MessageTemplate("Обновленный дедлайн сохранен и опубликован.")


def edit_saved_with_change_no_publish() -> MessageTemplate:
    return MessageTemplate(
        "Изменение сохранено: в канал отправлено сообщение о переносе без отдельной немедленной публикации."
    )


def edit_saved_no_publish() -> MessageTemplate:
    return MessageTemplate("Обновленный дедлайн сохранен без немедленной публикации.")


def cancelled() -> MessageTemplate:
    return MessageTemplate("Сценарий отменен.")


def active_deadline_post(deadline: dict) -> MessageTemplate:
    footer = f"\n\n{DEADLINE_CHANNEL_FOOTER}" if DEADLINE_CHANNEL_FOOTER else ""
    return MessageTemplate(
        f"{deadline['description_html']}\n\n"
        f"{deadline['remaining_label_html']}: <b>{deadline['remaining_value']}</b>\n"
        f"До: <b>{deadline['deadline_line_html']}</b>"
        f"{footer}"
    )


def deadline_cancelled_post(deadline: dict) -> MessageTemplate:
    footer = f"\n\n{DEADLINE_CHANNEL_FOOTER}" if DEADLINE_CHANNEL_FOOTER else ""
    return MessageTemplate(
        f"{deadline['description_html']}\n"
        "отменён, отдыхаем"
        f"{footer}"
    )


def deadline_completed_post(deadline: dict) -> MessageTemplate:
    footer = f"\n\n{DEADLINE_CHANNEL_FOOTER}" if DEADLINE_CHANNEL_FOOTER else ""
    return MessageTemplate(
        f"{deadline['description_html']}\n"
        f"{deadline['deadline_line_html']}\n"
        "<b>дедлайн завершён</b>"
        f"{footer}"
    )
