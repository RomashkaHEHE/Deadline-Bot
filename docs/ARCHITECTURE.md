# Architecture

## Purpose

`Deadline Bot` is a Telegram bot for a study group. Trusted users create deadlines, the bot publishes deadline posts into a Telegram channel or topic, tracks every related message, keeps a change history, and gradually moves finished items into archive.

The project intentionally stays small:

- one main runtime: `app.py`
- one message-template module: `bot_messages.py`
- one utility bot: `tools.py`
- one JSON storage file

There is no database and no web server. The bot works through Telegram polling plus the `python-telegram-bot` job queue.

## Main Modules

### `app.py`

Owns the runtime logic:

- environment loading
- JSON loading and migration
- Telegram conversations and inline navigation
- paginated list and archive screens
- deadline cards and details screens
- posting, editing, deleting and refreshing channel messages
- reminder scheduling and delayed cleanup

### `bot_messages.py`

Contains every user-facing and channel-facing message template.

Important design rule:

- templates return the exact Telegram payload that should be sent
- HTML formatting lives here
- channel-only footer text also lives here
- reusable custom emoji snippets live in `EMOJIS`

If a message looks wrong, this file is usually the first place to edit.

### `tools.py`

A separate helper bot for one-off Telegram inspection.

It is intentionally isolated from production logic and can be used to inspect:

- `text_html`
- Telegram entities
- `custom_emoji_id`
- `message_thread_id`
- topic-related message metadata

Because it uses polling too, it should not run at the same time as `app.py`.

## Storage Model

The persistent file is selected like this:

- `DEADLINES_STORAGE_PATH`, if set
- otherwise `deadlines.json` next to `app.py`

The file is schema-versioned through `schema_version`.

Top-level structure:

- `schema_version`
- `next_id`
- `deadlines`

Each deadline stores:

- text description
- HTML-safe description as received from Telegram formatting
- deadline datetime
- time flags
- creator metadata
- publication/reminder flags
- lifecycle status
- cleanup timestamp
- archive timestamp
- tracked channel messages
- event history

Each channel message record stores:

- `message_id`
- the exact text sent to Telegram
- `parse_mode`
- semantic `kind`
- creation time
- `template_data`

`template_data` matters because it lets the bot rebuild old channel posts after template changes. That is what powers `Обновить посты`.

## Migration Strategy

The runtime does not try to support every historical JSON shape forever.

Instead:

1. the bot reads `schema_version`
2. it applies one-time migrations until the current schema is reached
3. it rewrites the JSON file in the new format

That approach keeps runtime code clean while still allowing server data to evolve in place.

If the storage file is malformed or has an unusable structure, the bot preserves the original bytes as `unformatted-<name>.json` in the same directory, using numbered suffixes when needed, and then recreates a fresh empty storage file at the original path.

If the schema changes again, add a new migration step in `migrate_storage(...)` and bump `CURRENT_SCHEMA_VERSION`.

## Deadline Lifecycle

There are four statuses:

- `active`
- `cancelled`
- `completed`
- `archived`

### Visible List

The main list intentionally shows every deadline that is not archived:

- active deadlines
- cancelled deadlines whose messages are still waiting for cleanup
- completed deadlines whose messages are still waiting for cleanup

This is important: archive is not “everything inactive”, archive is “everything fully removed from the working surface”.

### Active

An active deadline:

- appears in the main list
- can be edited
- can be reminded manually
- can be cancelled
- can be deleted into archive
- participates in automatic reminder scheduling

Channel-wise, it keeps only one current live deadline post. Each new reminder replaces older live posts.

### Cancelled

When cancelled:

- the deadline stays in the visible list with cancelled status
- the bot sends a separate cancellation post
- all related channel messages remain for 3 days
- after cleanup, all related channel messages are deleted
- only then does the deadline move to archive

### Completed

When the deadline moment arrives:

- the bot does not send a new completion post
- it edits the most recent tracked channel message
- cleanup is scheduled for 3 days later
- after cleanup, all related channel messages are deleted
- then the deadline moves to archive

### Archived

Archived deadlines:

- no longer appear in the working list
- appear in the archive screen
- remain available for viewing card/details/history
- usually already have zero channel messages left

## Main UI Model

### Main Reply Keyboard

The main reply keyboard is intentionally minimal:

- `Список дедлайнов`
- `Архив`
- `Обновить посты`

All deadline-specific actions happen after opening a concrete deadline.

### Paginated List Screens

Both visible list and archive are paginated inline screens.

Each list page:

- shows a compact summary of several deadlines
- provides one button per deadline
- provides prev/next navigation when needed

The visible list also exposes inline creation of a new deadline.

### Deadline Card

A deadline card is the action hub for one record.

Depending on status it may allow:

- edit
- manual remind
- cancel
- delete into archive
- open details/history

### Deadline Details

The details screen shows expanded metadata:

- description
- status
- deadline time
- creation time
- creator
- number of channel messages
- number of history records
- rendered event history

The history is capped in length so the screen stays within Telegram message limits.

## Channel Message Tracking

Every deadline-related channel post must be registered through `post_channel_template(...)`.

That is a hard rule because the project depends on complete message tracking for:

- reminder replacement
- cancellation cleanup
- completion edit-in-place
- manual delete
- bulk post refresh

If code sends directly via `context.bot.send_message(...)`, message cleanup and template refresh will become incomplete.

## Refreshing Existing Posts

When templates change in `bot_messages.py`, already published channel messages can be refreshed.

`refresh_channel_posts(...)` walks through stored `channel_messages`, rebuilds each post from its `kind` and `template_data`, and edits Telegram messages in place where possible.

This mechanism depends on structured `template_data` being stored with each channel message record.

## Time Rules

- input format is `DD.MM.YYYY` with optional `HH:MM`
- all calculations use fixed timezone `UTC+5`
- omitted time means logical `00:00`
- implicit `00:00` is hidden in rendered text
- explicit `00:00` is shown

## Background Jobs

The repeating job queue loop runs every minute and handles:

- background refresh of active live posts so remaining time stays current
- 7-day reminders
- 24-hour reminders
- transitions from active to completed
- cleanup of cancelled/completed deadlines
- transfer into archive after cleanup

Because scheduling is derived from stored state, bot restarts do not lose reminder logic.

## Deployment Shape

Production deployment in this repo expects:

- app code in `/opt/deadline-bot/app`
- virtualenv in `/opt/deadline-bot/.venv`
- env file in `/etc/deadline-bot/deadline-bot.env`
- persistent JSON in `/var/lib/deadline-bot/deadlines.json`

See [DEPLOYMENT.md](C:/Users/Roma/Desktop/projects/deadline%20bot/docs/DEPLOYMENT.md) for exact scripts and service setup.

## Constraints Worth Remembering

- JSON schema should evolve through migrations, not endless compatibility branches
- message wording and HTML live in `bot_messages.py`
- deadline-related channel posts must always be tracked centrally
- `tools.py` and `app.py` should not poll Telegram at the same time
- visible list means “not archived”, not “only active”
