from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MessageTemplate:
    # Each template returns the exact payload that will be sent to Telegram.
    # Keep channel-only footer text and HTML decisions here, not in app.py.
    text: str
    parse_mode: str | None = "HTML"


BUTTON_NEW = "Добавить дедлайн"
BUTTON_LIST = "Список дедлайнов"
BUTTON_ARCHIVE = "Архив"
BUTTON_EDIT = "Изменить дедлайн"
BUTTON_CANCEL_DEADLINE = "Отменить дедлайн"
BUTTON_DELETE_DEADLINE = "Удалить дедлайн"
BUTTON_SKIP = "Пропустить"
BUTTON_ABORT = "Отмена"

EMOJIS = {
    # Central registry for reusable Telegram custom emoji snippets.
    "soon": '<tg-emoji emoji-id="5440621591387980068">🔜</tg-emoji>',
}


def access_denied() -> MessageTemplate:
    return MessageTemplate("У вас нет доступа к этому боту.")


def start_message() -> MessageTemplate:
    return MessageTemplate("Выберите действие на клавиатуре ниже.")


def current_time_message(current_time: str, timezone_label: str) -> MessageTemplate:
    return MessageTemplate(
        f"Сейчас по мнению бота:\n<b>{current_time}</b>\nЧасовой пояс: <b>{timezone_label}</b>"
    )


def no_active_deadlines() -> MessageTemplate:
    return MessageTemplate("Пока нет активных дедлайнов.")


def no_archive_deadlines() -> MessageTemplate:
    return MessageTemplate("Архив пока пуст.")


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


def invalid_time_format() -> MessageTemplate:
    return MessageTemplate("Время должно быть в формате <code>HH:MM</code>.")


def deadline_must_be_future() -> MessageTemplate:
    return MessageTemplate("Дедлайн должен быть в будущем.")


def deadline_summary(deadline: dict) -> str:
    return (
        f"#{deadline['id']} • {deadline['description_html']}\n"
        f"{deadline['deadline_line_html']}\n"
        f"Автор: {deadline['created_by_name_html']}"
    )


def list_deadlines_message(items: list[dict]) -> MessageTemplate:
    text = "\n\n".join(deadline_summary(item) for item in items)
    return MessageTemplate(text)


def archive_deadlines_message(items: list[dict]) -> MessageTemplate:
    def archive_summary(deadline: dict) -> str:
        return (
            f"#{deadline['id']} • {deadline['description_html']}\n"
            f"{deadline['deadline_line_html']}\n"
            f"Статус: <b>{deadline['status_label_html']}</b>"
        )

    text = "\n\n".join(archive_summary(item) for item in items)
    return MessageTemplate(text)


def initial_publish_question(deadline: dict) -> MessageTemplate:
    return MessageTemplate(
        "До дедлайна больше недели.\nОпубликовать первое сообщение сейчас?\n\n"
        + deadline_summary(deadline)
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


def choose_cancel_id(items: list[dict]) -> MessageTemplate:
    text = "\n\n".join(deadline_summary(item) for item in items)
    return MessageTemplate("Отправьте id дедлайна для отмены:\n\n" + text)


def choose_delete_id(items: list[dict]) -> MessageTemplate:
    text = "\n\n".join(deadline_summary(item) for item in items)
    return MessageTemplate("Отправьте id дедлайна для удаления:\n\n" + text)


def choose_edit_id(items: list[dict]) -> MessageTemplate:
    text = "\n\n".join(deadline_summary(item) for item in items)
    return MessageTemplate("Отправьте id дедлайна, который нужно изменить:\n\n" + text)


def numeric_id_required() -> MessageTemplate:
    return MessageTemplate("Нужен числовой id дедлайна.")


def deadline_not_found_by_id() -> MessageTemplate:
    return MessageTemplate("Дедлайн с таким id не найден.")


def deadline_cancelled_private() -> MessageTemplate:
    return MessageTemplate("Дедлайн отменен.")


def deadline_deleted_private() -> MessageTemplate:
    return MessageTemplate("Дедлайн удален и отправлен в архив.")


def deadline_cancelled_post(deadline: dict) -> MessageTemplate:
    return MessageTemplate(
        f"{deadline['description_html']}\nотменён, отдыхаем\n\n{EMOJIS['soon']}  #дедлайн"
    )


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
            lines.append(
                f"<s>{change['old_html']}</s>\n↓\n{change['new_html']}"
            )
        elif change["field"] == "deadline":
            lines.append(f"<s>{change['old_html']}</s> → {change['new_html']}")

    if not lines:
        lines.append(
            f"{old_deadline['description_html']}\n{new_deadline['deadline_line_html']}"
        )
    return MessageTemplate("\n".join(lines) + f"\n\n{EMOJIS['soon']}  #дедлайн")


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


def new_deadline_post(deadline: dict) -> MessageTemplate:
    return MessageTemplate(
        f"{deadline['description_html']}\n\n- До: <b>{deadline['deadline_line_html']}</b>\n\n{EMOJIS['soon']}  #дедлайн"
    )


def reminder_7d_post(deadline: dict) -> MessageTemplate:
    return MessageTemplate(
        f"<b>Напоминание: до дедлайна 7 дней</b>\n{deadline['description_html']}\n{deadline['deadline_line_html']}\n\n{EMOJIS['soon']}  #дедлайн"
    )


def reminder_24h_post(deadline: dict) -> MessageTemplate:
    return MessageTemplate(
        f"<b>Напоминание: до дедлайна 24 часа</b>\n{deadline['description_html']}\n{deadline['deadline_line_html']}\n\n{EMOJIS['soon']}  #дедлайн"
    )


def deadline_completed_post(deadline: dict) -> MessageTemplate:
    return MessageTemplate(
        f"{deadline['description_html']}\n{deadline['deadline_line_html']}\n<b>дедлайн завершён</b>\n\n{EMOJIS['soon']}  #дедлайн"
    )


def archive_cleanup_done(deadline: dict) -> MessageTemplate:
    return MessageTemplate(
        f"Все сообщения по дедлайну <b>{deadline['description_html']}</b> удалены, запись отправлена в архив."
    )
