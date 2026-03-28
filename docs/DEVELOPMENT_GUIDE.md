# Development Guide

## Recommended Reading Order

If you are new to the project, read files in this order:

1. `README.md`
2. `docs/ARCHITECTURE.md`
3. `bot_messages.py`
4. `app.py`
5. `tools.py`
6. deployment files only if you need server work

## Where To Make Changes

### Change wording, message formatting, custom emoji, hashtags, footer text

Edit `bot_messages.py`.

That file owns:

- button labels
- local user-facing texts
- channel post texts
- HTML formatting
- custom emoji snippets in `EMOJIS`

If you want to remove or change the `#дедлайн` footer, change it in the concrete channel templates there.

### Change business logic

Edit `app.py`.

Examples:

- reminder timing
- archive behavior
- cleanup timing
- creation/edit/cancel/delete flows
- active/archive selection logic
- input parsing

### Change data schema

Edit `Deadline`, `ChannelMessageRecord`, and `DeadlineStore` in `app.py`.

Important:

- do not leave permanent “support old schema forever” code unless there is a strong reason
- instead, migrate `deadlines.json` once and keep runtime code clean

### Add one-off Telegram inspection or tooling behavior

Edit `tools.py`.

Keep it isolated from the main bot logic so experiments do not pollute production behavior.

## Common Tasks

### Add a new channel message kind

1. Add a template to `bot_messages.py`.
2. Route sending through `post_channel_template(...)`.
3. Choose a new `kind` label.
4. Make sure the message should or should not be deleted during cleanup.

If it is part of deadline history, it should usually go through tracked channel posting.

### Change active/archive semantics

Status values are defined in `app.py`:

- `active`
- `cancelled`
- `completed`
- `archived`

Before changing them, inspect:

- `DeadlineStore.list_active`
- `DeadlineStore.list_archive`
- `cancel_deadline_finish`
- `delete_deadline_finish`
- `mark_deadline_completed`
- `reminder_loop`

### Change input format

Input parsing is centralized in `parse_deadline_input(...)`.

If the format changes:

- update parser logic
- update `bot_messages.py` prompts and validation texts
- update `README.md`
- update any docs that mention the input format

### Change how formatted descriptions are stored

Formatted description preservation currently depends on `message.text_html`.

If this is changed:

- preserve both raw text and renderable text
- verify bold, links, quotes, and custom emoji still survive round-trips
- verify edit diff rendering still behaves sensibly

## Practical Code Map

### Core data structures

- `ChannelMessageRecord`
- `Deadline`
- `DeadlineStore`

### Rendering helpers

- `deadline_context(...)`
- `build_changes(...)`
- `reply(...)`

### Channel side effects

- `post_channel_template(...)`
- `delete_all_deadline_messages(...)`
- `mark_deadline_completed(...)`

### Conversation entry points

- `create_start`
- `cancel_deadline_start`
- `delete_deadline_start`
- `edit_start`

### Background behavior

- `reminder_loop`

## Safe Workflow For Changes

When changing behavior, this sequence is usually enough:

1. Update templates in `bot_messages.py` if visible output changes.
2. Update logic in `app.py`.
3. Run syntax verification:

```bash
py -m py_compile app.py bot_messages.py tools.py
```

4. If JSON schema changed, migrate the current `deadlines.json`.
5. Manually test the affected flow in Telegram.

## Manual Test Checklist

When changing deadline behavior, the minimum useful checks are:

- create a deadline farther than 7 days away
- create a deadline within 7 days
- create without time
- create with explicit `00:00`
- edit description only
- edit date only
- edit with no real changes
- cancel a deadline
- delete a deadline
- verify archive list
- verify formatted description survives posting

## Deployment Notes For Developers

Production deploy is file-based, not container-based.

Useful facts:

- `deadlines.json` should live outside the repo in production
- workflow compile-checks `app.py`, `bot_messages.py`, and `tools.py`
- the service runs `app.py`, not `tools.py`

If you add a new runtime-critical Python file, consider whether:

- it should be compiled in CI
- it should be packaged into the release archive

## What Not To Assume

- do not assume `README.md` alone is the full architecture reference
- do not assume `tools.py` runs alongside `app.py`
- do not assume direct `send_message` calls are safe for deadline history
- do not assume old JSON schema variants must stay supported forever
