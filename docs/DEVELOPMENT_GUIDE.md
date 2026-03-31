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

### Change wording, message formatting, hashtags, footer text, custom emoji

Edit `bot_messages.py`.

That file owns:

- reply-keyboard labels
- local texts in private chat
- channel post templates
- Telegram HTML
- reusable emoji snippets in `EMOJIS`

If a channel footer or hashtag should disappear, remove it from the concrete channel templates there rather than injecting or stripping it in `app.py`.

### Change business logic

Edit `app.py`.

Typical examples:

- create/edit/cancel/delete flows
- visible-list vs archive behavior
- pagination
- reminder timing
- cleanup timing
- refresh-post logic
- parsing and validation

### Change storage schema

Edit:

- `Deadline`
- `ChannelMessageRecord`
- `DeadlineEvent`
- `DeadlineStore`
- `migrate_storage(...)`

Important rule:

- do not keep permanent “support old schema forever” branches in normal runtime logic
- instead, add a one-time migration and let the JSON file rewrite itself into the new format

### Add Telegram debugging or one-off tooling

Edit `tools.py`.

Keep experiments there instead of leaking them into production bot flow.

## Practical Code Map

### Core data structures

- `Deadline`
- `ChannelMessageRecord`
- `DeadlineEvent`
- `DeadlineStore`

### Rendering and view helpers

- `deadline_context(...)`
- `live_deadline_context(...)`
- `build_list_screen(...)`
- `build_deadline_card_screen(...)`
- `build_deadline_details_screen(...)`

### Channel side effects

- `post_channel_template(...)`
- `delete_all_deadline_messages(...)`
- `publish_live_deadline_post(...)`
- `mark_deadline_completed(...)`
- `archive_after_cleanup(...)`

### Conversations

- `create_start`
- `edit_start`
- `maybe_handle_menu_navigation`
- `abort_conversation`

### Inline navigation

- `list_page_callback`
- `open_deadline_callback`
- `details_callback`
- `deadline_action_callback`

### Background behavior

- `reminder_loop`

## Common Tasks

### Add a new channel message kind

1. Add a template in `bot_messages.py`.
2. Choose a `kind` string.
3. Add render logic to `render_channel_template(...)`.
4. Store enough `template_data` to rebuild that message later.
5. Send it only through `post_channel_template(...)`.

If the message is about a deadline and should participate in cleanup or refresh, it must be tracked.

### Change visible-list semantics

Visible list behavior is centralized around:

- `DeadlineStore.list_visible(...)`
- `source_for_deadline(...)`
- `build_list_screen(...)`
- `build_deadline_card_screen(...)`
- `reminder_loop(...)`

Current rule:

- visible list shows every deadline that is not archived

So if that rule changes, update both data filtering and UI expectations.

### Change archive behavior

Archive behavior currently depends on:

- `delete_deadline(...)`
- `archive_after_cleanup(...)`
- `DeadlineStore.list_archive(...)`

Keep in mind that cancelled/completed deadlines do not go to archive immediately. They move only after channel cleanup is done.

### Change input format

Input parsing is centralized in `parse_deadline_input(...)`.

If the format changes:

1. update parser logic
2. update validation texts in `bot_messages.py`
3. update `README.md`
4. update architecture docs if the change affects semantics

### Change formatting preservation

Rendered deadline descriptions depend on storing both:

- raw text in `description`
- Telegram-ready HTML in `description_html`

If you touch this area, manually verify:

- bold
- italic
- links
- block quotes
- custom emoji

Also re-check edit diffs and channel post refresh.

### Change template refresh behavior

Template refresh relies on:

- `ChannelMessageRecord.kind`
- `ChannelMessageRecord.template_data`
- `render_channel_template(...)`
- `refresh_channel_posts(...)`

If you add new message kinds but forget to preserve enough structured data, future refreshes will not be able to rebuild those posts.

## Safe Workflow For Changes

When changing behavior, this sequence is usually enough:

1. Update templates in `bot_messages.py` if visible output changes.
2. Update logic in `app.py`.
3. Run syntax verification:

```bash
py -m py_compile app.py bot_messages.py tools.py
```

4. If storage schema changed, update `CURRENT_SCHEMA_VERSION` and add a migration.
5. Test the affected Telegram flow manually.

## Manual Test Checklist

For deadline-related changes, the minimum useful checks are:

- open visible list
- paginate when there are many deadlines
- open a deadline card from the list
- open details/history
- create a deadline farther than 7 days away
- create a deadline within 7 days
- create without time
- create with explicit `00:00`
- edit description only
- edit date only
- edit with no real changes
- cancel a deadline
- delete a deadline into archive
- verify a cancelled/completed deadline stays visible until cleanup
- verify archive screen
- verify formatted description survives round-trip into channel posts
- verify `Обновить посты`

## Notes For Production Changes

- production JSON should live outside the repo
- deployment uses `app.py`, not `tools.py`
- compile checks should include every runtime-critical Python file

If you add a new module that production depends on, make sure it is included in CI checks and in the deployment artifact.

## What Not To Assume

- do not assume only active deadlines appear in the visible list
- do not assume archive means “everything non-active”
- do not assume direct `send_message(...)` calls are safe for deadline posts
- do not assume `tools.py` runs alongside `app.py`
- do not assume old JSON shapes should stay supported forever once a migration exists
