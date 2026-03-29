# Architecture

## Purpose

`Deadline Bot` is a Telegram bot for a study group. It lets trusted users create, edit, cancel, delete, and archive deadlines, and it posts deadline updates into a Telegram channel.

The project is intentionally simple:

- one main runtime file: `app.py`
- one message-template file: `bot_messages.py`
- one standalone utility bot: `tools.py`
- one JSON file for persistence: `deadlines.json` or the path from `DEADLINES_STORAGE_PATH`

There is no database and no web server. The bot runs via Telegram polling.

## Main Modules

### `app.py`

The main bot runtime. It is responsible for:

- loading environment variables
- loading and saving deadlines
- building Telegram keyboards and conversation flows
- parsing user input
- posting messages into the channel
- tracking posted channel messages
- replacing older active-deadline posts with one fresh reminder post
- editing the latest channel message when a deadline completes
- deleting all deadline-related channel messages after the cleanup window
- serving archive and active lists

### `bot_messages.py`

Central place for all bot-facing message templates and button labels.

Important design choice:

- templates return complete Telegram-ready messages
- channel-specific footer text is defined directly inside channel-message templates
- HTML formatting is controlled here, not injected later in `app.py`

This file is the first place to edit if the wording, formatting, emoji, or visual style of the bot should change.

### `tools.py`

A separate helper bot script that uses the same token, but is meant for one-off tooling tasks.

Right now it is used to inspect incoming message payloads and extract things like:

- `text_html`
- Telegram entities
- `custom_emoji_id`

Because it also uses polling, it should not run at the same time as `app.py`.

## Persistence Model

The only persistence layer is the JSON file defined by:

- `DEADLINES_STORAGE_PATH`, if present
- otherwise `deadlines.json` next to `app.py`

The file stores:

- the next numeric deadline id
- a list of deadlines

Each deadline stores:

- plain description text
- HTML version of the description from Telegram entities
- deadline datetime
- whether the time was explicitly provided
- whether `00:00` was explicitly provided
- author metadata
- publish/reminder flags
- lifecycle status
- cleanup timestamp
- archive timestamp
- all channel messages tied to the deadline

Each channel message record stores:

- Telegram `message_id`
- exact text sent to Telegram
- `parse_mode`
- semantic kind, for example `initial`, `reminder_7d`, `changed`
- creation time
- `template_data` with the structured payload needed to rebuild that post later

## Deadline Lifecycle

The system has four statuses:

- `active`
- `cancelled`
- `completed`
- `archived`

### Active

An active deadline:

- appears in the main list
- can be edited
- can be cancelled
- can be manually deleted into archive
- can be manually reminded
- participates in reminder scheduling
- keeps at most one current "live" post in the channel; each reminder replaces older active posts

### Cancelled

When a deadline is cancelled:

- it leaves the active list immediately
- the bot posts a separate cancellation message into the channel
- all historical channel messages remain visible for 3 more days
- after 3 days, every channel message tied to that deadline is deleted
- the deadline then moves to `archived`

### Completed

When the deadline moment arrives:

- the bot does not send a new completion message
- instead, it edits the most recent channel message for that deadline
- then it schedules cleanup 3 days later
- after cleanup, all related channel messages are deleted
- the deadline then moves to `archived`

### Archived

Archived deadlines:

- are not shown in the active list
- are shown in the archive view
- are kept in JSON for history
- may already have no channel messages left if cleanup already ran

## Channel Message Tracking

This project depends on storing every posted channel message.

That is not optional metadata. It is required because:

- cancellation cleanup must delete all related posts
- manual delete must delete all related posts immediately
- completion must edit the latest posted channel message
- future message kinds may also need retroactive cleanup

Because of that, any new channel post must be sent through `post_channel_template(...)` in `app.py`.

If a new code path sends directly via `context.bot.send_message(...)`, cleanup will become incomplete.
It will also break retroactive template refresh, because the bot will not have the structured data required to rebuild the post.

## Formatting Rules

The project uses Telegram HTML formatting in templates.

Description formatting is preserved from user input by storing:

- `description` as plain text
- `description_html` as Telegram-generated HTML from the incoming message entities

This allows:

- bold
- italic
- links
- block quotes
- custom emoji tags like `<tg-emoji ...>`

The code should prefer `description_html` when rendering messages into the channel.

## Date and Time Rules

- input format is `DD.MM.YYYY` with optional `HH:MM`
- all calculations use fixed timezone `UTC+5`
- if time is omitted, logical time is `00:00`
- if time is omitted, that `00:00` is not shown in rendered text
- if the user explicitly sends `00:00`, it is shown

## Main User Flows

### Create

1. User sends description.
2. User sends full date or full date with time.
3. If the deadline is more than 7 days away, the bot asks whether to publish immediately.
4. Otherwise it publishes immediately.
5. The deadline is tracked for reminders and later cleanup.

### Edit

1. User chooses a deadline by id.
2. User can replace or skip description.
3. User can replace or skip date/time.
4. If nothing really changed, the bot does nothing.
5. If the deadline already had channel visibility, the bot posts a diff-style change message.

### Cancel

1. User chooses a deadline by id.
2. Bot posts a cancellation message.
3. Deadline leaves the active list.
4. Cleanup is scheduled 3 days later.

### Delete

1. User chooses a deadline by id.
2. Bot deletes all channel messages tied to the deadline immediately.
3. Deadline is moved to archive.

## Scheduling

The bot uses `python-telegram-bot` job queue.

The repeating job runs every minute and is responsible for:

- sending 7-day reminders
- sending 24-hour reminders
- transitioning active deadlines into completed state
- deleting all channel messages for cancelled/completed deadlines after the cleanup timestamp

## Deployment Shape

The repository includes a production deployment path for Ubuntu:

- `.github/workflows/deploy.yml`
- `deploy/install_service.sh`
- `deploy/deploy.sh`
- `deploy/deadline-bot.service`

Production layout expects:

- app code in `/opt/deadline-bot/app`
- virtualenv in `/opt/deadline-bot/.venv`
- env file in `/etc/deadline-bot/deadline-bot.env`
- persistent data in `/var/lib/deadline-bot/deadlines.json`

## Constraints To Keep In Mind

- JSON schema is treated as explicit and current, not backward-compatible forever
- message wording and HTML live in `bot_messages.py`
- channel posts must be tracked centrally
- `tools.py` and `app.py` should not poll at the same time
- changing deadline schema should be accompanied by a one-time JSON migration
