# iCloud (mail, iCal, contacts) MCP

A local [MCP](https://modelcontextprotocol.io/) server that lets Claude Desktop or Claude Code work with your iCloud **Mail** (read, search, triage, draft), **Calendar** (see and add events) and **Contacts** (look people up, add them). It runs on your own Mac or PC and signs in with one Apple app-specific password.

Inspired by [bufordeeds/icloud-mail-mcp](https://github.com/bufordeeds/icloud-mail-mcp) by its original author, [@bufordeeds](https://github.com/bufordeeds). Thanks to them for the original idea and code. See [Changes from the original](#changes-from-the-original).

## Tools

**Mail**

| Tool | What it does | Changes anything? |
|------|--------------|---------------|
| `list_folders` | All folders with their flags | No |
| `unread_summary` | Unread counts per folder | No |
| `recent_emails` | Newest messages, headers only, with paging and `unread_only` | No |
| `find_emails` | Search by from, to, subject, body, date range, unread, flagged | No |
| `open_email` | Headers, body (capped at 8,000 characters) and a numbered attachment list. Only the text is downloaded | No (does not mark as read) |
| `get_attachment` | Save one attachment, by filename or index, to `~/Downloads/icloud-mail`. Only that attachment is downloaded | Writes a local file |
| `mark_emails` | Mark read/unread, flagged/unflagged | Yes |
| `move_emails` / `archive_emails` | Move messages to another folder or to Archive | Yes |
| `draft_email` | Save a draft to Drafts | Yes |
| `reply_to_email` | Threaded reply (Re:, In-Reply-To, quoted original). Saves a draft by default | Yes |
| `send_email` | Send immediately. **Disabled unless `ICLOUD_ALLOW_SEND=true`** | Sends email |

**Calendar**

| Tool | What it does | Changes anything? |
|------|--------------|---------------|
| `list_calendars` | Your calendars and whether each is writable | No |
| `list_events` | Events in a date range (default: the next 7 days), repeating events expanded, optional text filter | No |
| `create_event` | Add a timed or all-day event | Yes |
| `delete_event` | Delete an event (for a repeating event, the whole series) | Yes, **destructive** |

**Contacts**

| Tool | What it does | Changes anything? |
|------|--------------|---------------|
| `search_contacts` | Find people by name, email, company or phone number | No |
| `get_contact` | Full details: emails, phones, addresses, birthday, note | No |
| `create_contact` | Add a contact | Yes |

## Install

You need **Claude Desktop or Claude Code**. You don't need to install Python: `uv` downloads one for you.

### 1. Create an app-specific password

Go to [appleid.apple.com](https://appleid.apple.com/), open **Sign-In and Security**, then **App-Specific Passwords**, and create one called "Claude". The same password works for Mail, Calendar and Contacts.

### 2. Install uv

**Mac (Terminal):**
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```
**Windows (PowerShell):**
```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```
Close the terminal and open a new one, so the `uv` command is found.

### 3. Install the server

```bash
git clone https://github.com/Newhouse-productions/iCloud-mail-iCal-contacts-MCP.git
uv tool install ./iCloud-mail-iCal-contacts-MCP
```
This gives you an `icloud-mail-mcp` command.

### 4. Run the guided setup

```bash
icloud-mail-mcp --setup
```
Setup asks for your email address, suggests your mail login and Apple ID, and stores the app-specific password in your **macOS Keychain or Windows Credential Manager**. It never writes the password to a file. It then tests Mail, Calendar and Contacts, and prints the exact settings to connect Claude.

Settings are saved to `~/.config/icloud-mail-mcp/.env` (Mac) or `%APPDATA%\icloud-mail-mcp\.env` (Windows). Run `icloud-mail-mcp --config-path` to see where.

### 5. Connect to Claude

Copy what `--setup` printed. It looks like this:

**Claude Code**
```bash
claude mcp add -s user --transport stdio icloud-mail -- icloud-mail-mcp
```
Then run `/mcp` in Claude Code to confirm it's connected.

**Claude Desktop.** Add this to `claude_desktop_config.json`, then fully quit and reopen Claude Desktop:
- Mac file: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows file: `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "icloud-mail": {
      "command": "/Users/YOU/.local/bin/icloud-mail-mcp"
    }
  }
}
```
Claude Desktop needs the full path. `--setup` prints yours. On Windows it is `C:\\Users\\YOU\\.local\\bin\\icloud-mail-mcp.exe`, and the double backslashes are required.

### Updating and uninstalling

```bash
git -C iCloud-mail-iCal-contacts-MCP pull
uv tool install --force ./iCloud-mail-iCal-contacts-MCP
```
Then restart Claude. To remove it: `uv tool uninstall icloud-mail-mcp`. To remove the password, delete the `icloud-mail-mcp` entry in Keychain Access or Credential Manager, and revoke it at appleid.apple.com.

### Upgrading from the earlier `python server.py` setup

The old setup keeps working: `python server.py` and the `.env` next to it are still read. To switch, run steps 2 to 5 above, then remove the old `icloud-mail` entry from your Claude config. Settings in the new file take priority over the old `.env`.

## Permissions

The read-only tools are safe to auto-approve. Keep anything that changes, deletes or sends on "ask".

**Claude Code** (`~/.claude/settings.json`):
```json
{
  "permissions": {
    "allow": [
      "mcp__icloud-mail__list_folders",
      "mcp__icloud-mail__unread_summary",
      "mcp__icloud-mail__recent_emails",
      "mcp__icloud-mail__find_emails",
      "mcp__icloud-mail__open_email",
      "mcp__icloud-mail__draft_email",
      "mcp__icloud-mail__list_calendars",
      "mcp__icloud-mail__list_events",
      "mcp__icloud-mail__search_contacts",
      "mcp__icloud-mail__get_contact"
    ],
    "ask": [
      "mcp__icloud-mail__reply_to_email",
      "mcp__icloud-mail__mark_emails",
      "mcp__icloud-mail__move_emails",
      "mcp__icloud-mail__archive_emails",
      "mcp__icloud-mail__get_attachment",
      "mcp__icloud-mail__send_email",
      "mcp__icloud-mail__create_event",
      "mcp__icloud-mail__delete_event",
      "mcp__icloud-mail__create_contact"
    ]
  }
}
```

**Claude Desktop.** In the connector's tool settings, set the read tools to "Always allow" and leave the rest on "Needs approval".

## Settings

`--setup` writes the first three. Change any setting from the command line:

```bash
icloud-mail-mcp --settings                          # every setting, its value and its default
icloud-mail-mcp --set ALLOW_SEND=true TIMEZONE=Europe/London
icloud-mail-mcp --set DEFAULT_CALENDAR="Work"
icloud-mail-mcp --unset ALLOW_SEND                  # back to the default
```

Names work with or without `ICLOUD_` and in any case (`allow_send` is fine). Each value is checked before anything is saved, so a typo changes nothing. Restart Claude afterwards. The password isn't set this way, because command lines end up in your shell history: use `--store-password`. You can also edit the settings file directly.

| Setting | Default | What it does |
|---------|---------|--------------|
| `ICLOUD_EMAIL` | | Your iCloud address, used as the From address and the SMTP login |
| `ICLOUD_IMAP_USER` | `ICLOUD_EMAIL` | Mail login, usually the part before `@icloud.com` |
| `ICLOUD_APPLE_ID` | `ICLOUD_EMAIL` | Apple ID email, for Calendar and Contacts |
| `ICLOUD_APP_PASSWORD` | | Only if you can't use the keychain |
| `ICLOUD_ALIASES` | | Your other addresses (custom domain, Hide My Email), comma-separated, so replies never go to yourself |
| `ICLOUD_READ_ONLY` | `false` | Offer Claude only the 9 tools that read: nothing can be changed, sent or saved |
| `ICLOUD_ALLOW_SEND` | `false` | Allow sending email, not just drafts |
| `ICLOUD_SEND_ALLOWLIST` | | If sending is on, only these recipients (`@domain.com` matches a whole domain) |
| `ICLOUD_SAVE_SENT` | `true` | Save a copy of sent mail to Sent Messages |
| `ICLOUD_DEFAULT_CALENDAR` | "Calendar" or "Home" | Calendar for new events |
| `ICLOUD_TIMEZONE` | your computer's | Time zone for event times, e.g. `Europe/London` |
| `ICLOUD_CONNECT_TIMEOUT` / `ICLOUD_TIMEOUT` | `10` / `30` | Seconds to wait to connect, and for each response |
| `ICLOUD_MAX_BODY_CHARS` | `8000` | Email body length returned by `open_email` |
| `ICLOUD_MAX_ATTACHMENT_MB` | `25` | Largest attachment `get_attachment` saves |
| `ICLOUD_ALLOW_EXECUTABLES` | `false` | Let `get_attachment` save programs and scripts (`.exe`, `.app`, `.command`, `.js` …) |
| `ICLOUD_ATTACHMENT_DIR` | `~/Downloads/icloud-mail` | Where attachments are saved |
| `ICLOUD_CALDAV_URL` / `ICLOUD_CARDDAV_URL` | iCloud's | Calendar and Contacts servers (https only) |
| `ICLOUD_LOG_LEVEL` | `INFO` | `DEBUG` logs each IMAP/WebDAV call and its timing (never content) |

Settings already in the environment, for example from an `"env"` block in the Claude config, override the file.

## Safety model

- **Drafts by default.** `send_email` and `reply_to_email(send=true)` refuse to send unless `ICLOUD_ALLOW_SEND=true`. Claude writes drafts and you send them from Mail.
- **Recipient allowlist.** If you do enable sending, `ICLOUD_SEND_ALLOWLIST=@yourcompany.com,partner@example.com` blocks any other To, Cc or Bcc recipient.
- **Untrusted content.** Email bodies, event notes and contact notes are labelled as untrusted, so Claude is told not to follow instructions written inside them. Meeting invitations can come from anyone. Bodies are also truncated, so one huge newsletter can't flood the conversation.
- **Nothing is marked read by accident.** Listing, searching and reading use read-only folder access and `BODY.PEEK`.
- **Read-only mode.** With `ICLOUD_READ_ONLY=true`, Claude is only offered the tools that read. An email written to trick Claude has nothing to act through. Each changing tool also refuses on its own.
- **Limits on changes.** One mark, move or archive call can change at most 50 messages, so a single instruction can't sweep a mailbox.
- **Encrypted, verified connections.** Mail, Calendar and Contacts all verify Apple's TLS certificates. Server URLs must be `https://` (plain HTTP is allowed only to `localhost`, for tests), and redirects can't carry your password to another server.
- **Spam invitations can't freeze it.** Repeating events are expanded within strict limits: the cost is estimated before expanding, there's a per-event and total cap and a time budget, and the range is at most 366 days. An event that hits a limit (one repeating every second, say) shows at most 3 times, with a warning, so it can't push your real events out of the list.
- **Attachments are saved defensively.**
  - Existing files are never overwritten, and links are never followed.
  - Files are readable only by you.
  - Files are marked as downloaded from the internet, so Gatekeeper (Mac) and SmartScreen (Windows) check them when opened.
  - Programs and scripts are refused unless `ICLOUD_ALLOW_EXECUTABLES=true`.
- **Deleting is limited.** `delete_event` only deletes events in your own writable calendars, and there is no tool to delete email or contacts.
- **Settings file check.** If a settings file holds `ICLOUD_APP_PASSWORD` and other users can read it, a warning is logged. The keychain is better.
- **Revoke any time.** Deleting the app-specific password at appleid.apple.com cuts off access immediately.
- Anything Claude reads becomes part of your Claude conversation, so avoid pointing it at highly sensitive mail.

## Example prompts

- "What's unread? Summarise anything that needs a reply."
- "What's on my calendar this week?" / "Am I free Thursday afternoon?"
- "Add a dentist appointment next Tuesday at 3pm."
- "What's Sarah Jones's email? Draft her a note asking to move our Friday meeting." This combines contacts, calendar and mail.
- "Find emails from my bank since 2026-09-01 and list the amounts."
- "Archive all the newsletters in my inbox from this week." Claude asks before moving them.
- "Save the PDF attached to the invoice email from Acme."

## Troubleshooting

Run `icloud-mail-mcp --check` first. It tests Mail, Calendar and Contacts separately and says which one fails.

| Symptom | Fix |
|---------|-----|
| `iCloud login failed` (Mail) | Set `ICLOUD_IMAP_USER` to the part before `@icloud.com`. Create a new app-specific password and run `--setup` again. |
| `iCloud Calendar/Contacts login failed` | Set `ICLOUD_APPLE_ID` to your Apple ID email. It can differ from your mail address if you use a custom domain. |
| `Could not reach iCloud …` | Check your internet connection, and that no VPN or firewall blocks iCloud (ports 993, 587 and 443). |
| `No app-specific password found` | Run `icloud-mail-mcp --setup`, or `--store-password`. |
| `icloud-mail-mcp: command not found` | Open a new terminal after installing uv, or run `uv tool update-shell`. |
| Server not showing in Claude Desktop | Use the full path that `--setup` printed. Logs are in `~/Library/Logs/Claude/` (Mac) or `%APPDATA%\Claude\logs\` (Windows). |
| Event times are off by hours | Set `ICLOUD_TIMEZONE`, e.g. `America/New_York`. |
| New events go to the wrong calendar | Set `ICLOUD_DEFAULT_CALENDAR`, or ask Claude to use a specific calendar. |
| Duplicate messages in Sent | Set `ICLOUD_SAVE_SENT=false`. |
| `Message N not found` / `None of the IDs … exist` | Message IDs are per folder. Pass the same `folder` the ID came from. |
| `… over the ICLOUD_MAX_ATTACHMENT_MB limit` | Save that attachment from Mail, or raise `ICLOUD_MAX_ATTACHMENT_MB`. |

## Development

```bash
uv sync --locked --extra dev              # exact, hash-checked versions from uv.lock
uv run pytest                             # tests
uv run ruff check . && uv run ruff format --check . && uv run python -m mypy   # lint, format, types
```
GitHub Actions runs all of these on every push that touches this folder, on Ubuntu and Windows with Python 3.10 and 3.13 (`.github/workflows/icloud-mail-mcp.yml`). CI installs only from `uv.lock`, and every Action is pinned to a commit SHA. Dependabot (`.github/dependabot.yml`) proposes weekly updates to both. After changing dependencies in `pyproject.toml`, run `uv lock` and commit `uv.lock`.

None of the tests use your iCloud account:
- **Unit tests** use a fake IMAP server (`tests/fakes.py`). Its `BODYSTRUCTURE` responses are built independently of the code under test.
- **`tests/test_integration_dav.py`** starts a local [Radicale](https://radicale.org/) server (a dev dependency) and runs the Calendar and Contacts tools against real CalDAV and CardDAV.
- **`tests/test_integration_imap.py`** runs the Mail tools against a real IMAP server. It's skipped unless `ICLOUD_TEST_IMAP` is set. Point it at a disposable server, never your iCloud account; `tests/ci/dovecot.conf` is a ready-made local Dovecot:
  ```bash
  sudo mkdir -p /run/dvt /run/dvt-mail && sudo chown dovecot:dovecot /run/dvt-mail
  sudo dovecot -c tests/ci/dovecot.conf
  ICLOUD_TEST_IMAP=127.0.0.1:10143:testpw uv run pytest tests/test_integration_imap.py
  ```
- **`tests/test_schemas.py`** calls every tool through the MCP server and fails if a result has a key its declared type is missing. The SDK silently drops such keys from structured output.

Set `ICLOUD_LOG_LEVEL=DEBUG` to log each IMAP and WebDAV call with its timing to stderr, where Claude shows it in its MCP logs. The log includes the server name and status only, never message content or URL paths (which contain your Apple ID).

### Code layout

| Module | Responsibility |
|--------|----------------|
| `config.py` | The immutable `Settings`, where settings files live, the keychain password |
| `app.py` | The MCP server instance, logging, loading the tool modules |
| `annotations.py` | Tool safety hints: `READ_ONLY`, `CREATES`, `MODIFIES`, `DESTRUCTIVE`, `SENDS` |
| `schemas.py` | The result type of every tool (published as its output schema) |
| `mime.py` | Message structure, decoding, filenames. No network access |
| `imap.py` | A pool of up to 3 reused IMAP connections; listing; opening messages part by part |
| `compose.py` | Building, sending and drafting email; send guardrails; reply addressing |
| `mail.py`, `calendars.py`, `contacts.py` | The tools |
| `dav.py` | A small WebDAV client for CalDAV and CardDAV, with keep-alive connections (proxy-aware) |
| `files.py` | Saving attachments safely |
| `cache.py` | A thread-safe TTL cache (tools run on worker threads) |
| `cli.py` | `--setup`, `--check`, `--store-password`, `--settings`/`--set`/`--unset`, and starting the server |
| `options.py` | Every setting, with the check `--set` runs on a new value |

The root `server.py` keeps `python server.py` working.

## Changes from the original

- **One-command install** (`uv tool install`), a guided `--setup`, and a per-user settings file. Calendar and contacts are new.
- **Reused connections:** a pool of up to 3 for mail, and keep-alive for calendar and contacts, instead of a new login or TLS handshake per call. Also typed, schema-published tool results.
- **Security hardening:** read-only mode, limits on changes and on repeating-event expansion, safe attachment saving, HTTPS-only URLs, a hash-locked dependency file, and SHA-pinned CI actions.
- **Works with MCP SDK 2.x.** Upstream's `mcp[cli]>=1.0.0` now installs 2.x, which removed `FastMCP`, so a fresh upstream install fails at startup. Dependencies are now pinned.
- **Header-only listing and search.** Upstream downloaded every full message, attachments included, just to build previews.
- **Reading fetches only what's needed.** `open_email` downloads just the text part (capped at 512 KB). `get_attachment` downloads only the attachment you asked for, and checks its size first.
- **Sending is off by default**, with an optional recipient allowlist and a copy saved to Sent. Recipients the server refuses are reported (`partially_sent`).
- **TLS certificate checking when sending.** Upstream's `starttls()` didn't verify iCloud's certificate, so someone on the same network could intercept the app password.
- **Keychain storage** for the password.
- **Bodies are labelled untrusted and truncated.**
- **New tool and option names** throughout (for example `find_emails` with `sender`, `subject_has`, `start_date` and `limit`).
- **Bug fixes:**
  - Drafts folder detection now works (flags are bytes).
  - Invalid dates now raise an error instead of being silently ignored.
  - Connections have timeouts.
  - Login failures give a clear message.
  - HTML-to-text conversion no longer leaks CSS and JavaScript.
  - Attachments are found however deeply they are nested, including messages that are only an attachment. Attached emails are saved as `.eml` files, and attachments with the same name can be picked by index.
  - Saved filenames are safe on Windows (no reserved names like `CON`, no over-long names).
- **Safer triage:** move, mark and archive check that the message IDs exist in the folder and report any that weren't found. Moving never purges other deleted mail. Folder names match regardless of case. Replies are never addressed to your own addresses, and reply-all keeps the author when the email has a Reply-To address.
- **New mail tools:** `unread_summary`, `reply_to_email`, `mark_emails`, `move_emails`, `archive_emails`, `get_attachment`.
- **Paging** (`offset`, `total`) and `unread`/`flagged` fields in list and search results.
- **MCP tool annotations** (read-only and destructive hints).
- **Tests:** unit tests, plus integration tests against real IMAP, CalDAV and CardDAV servers.

## License

[PolyForm Noncommercial 1.0.0](LICENSE.md). You're free to use, change and share this for any noncommercial purpose: personal use, study, hobby projects, and use by charities, schools and public bodies. Commercial use needs permission; open an issue to ask.

Copyright 2026 Newhouse Productions.

This project began from [bufordeeds/icloud-mail-mcp](https://github.com/bufordeeds/icloud-mail-mcp) by [@bufordeeds](https://github.com/bufordeeds), whose work inspired it, and has since been substantially rewritten and extended (see [Changes from the original](#changes-from-the-original)).
