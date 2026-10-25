# home-brain

A self-hosted personal assistant backend: a markdown vault containing the information an AI needs
to work as an efficient personal assistant, exposed to Claude as an MCP server. Claude searches the
vault to answer questions and keeps it up to date as you share new information. Reminders and
daily briefings are pushed to your phone through a self-hosted [ntfy](https://ntfy.sh).

Sibling project to [home-board](https://github.com/bjornerikmoen2/home-board).
Architecture and roadmap: [docs/plan.md](docs/plan.md).

**Status:** Phase 3. Running on a home server behind GitHub login and a Cloudflare Tunnel
([docs/deploy.md](docs/deploy.md)), with the vault on a NAS and one git commit per change. The
`agenda` and `remind` tools answer date questions, and a scheduler pushes reminders and briefings
to the phone.

## Quick start (local, Windows)

```powershell
py -3.12 -m venv .venv
.venv\Scripts\pip install -e ".[dev]"
.venv\Scripts\home-brain serve
```

The MCP endpoint is `http://127.0.0.1:8000/mcp` (health check: `/health`). On first start an empty
vault is seeded from the starter template: example notes about "Ola" and "Kari Nordmann" marked
`example: true`, plus `_conventions.md` with the filing rules. Replace the examples with real notes.

The vault is its own git repository (created on first start) with one commit per change, so
anything Claude does can be undone. `./vault` and `./data` are git-ignored here: personal data
never goes in this repo.

## Connect Claude

**Claude Code** (HTTP, while `home-brain serve` is running):

```bash
claude mcp add --transport http home-brain http://127.0.0.1:8000/mcp
```

**Claude Desktop** (stdio; Settings → Developer → Edit Config → `claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "home-brain": {
      "command": "C:\\coding_fun\\Code\\home-brain\\.venv\\Scripts\\home-brain.exe",
      "args": ["serve", "--transport", "stdio"],
      "env": {
        "VAULT_PATH": "C:\\coding_fun\\Code\\home-brain\\vault",
        "DATA_PATH": "C:\\coding_fun\\Code\\home-brain\\data"
      }
    }
  }
}
```

The Claude app on the web and phone needs the server on a public HTTPS URL with GitHub login:
see [docs/deploy.md](docs/deploy.md).

## Tools

| Tool | What it does |
|---|---|
| `search_notes` | Keyword search over titles, tags, frontmatter and body (SQLite FTS5, prefix matching, diacritics-insensitive), plus the pages of PDF attachments |
| `read_note` | Read a whole note |
| `read_attachment` | Read the text of a PDF in the vault, page by page |
| `list_notes` | Browse a folder: notes, PDF attachments and subfolders, optional `note_type` filter |
| `write_note` | Create a note (or replace with `overwrite`), validating the frontmatter |
| `patch_note` | Merge frontmatter (JSON merge patch), append to frontmatter lists, append text under a heading; preserves comments and formatting |
| `move_note` | Rename/refile a note or PDF, never overwrites; notes linking to a moved PDF follow it |
| `delete_note` | Soft delete of a note or PDF into `.trash/` |
| `agenda` | Everything dated for one or more days: events, weekly routines (RRULE), birthdays, deadlines and reminders, with a per-person packing list; plus upcoming deadlines within their warning window |
| `remind` | Save a one-off reminder in `reminders.md` (at a date-time, or `+15m` / `+2h` / `+3d` from now); it is pushed to the phone when due |
| `notify` | Push a message to the phone right now (only when ntfy is configured) |
| `chores` | The chores from [Home-Board](https://github.com/bjornerikmoen2/home-board) per person: done and still to do, plus shared ones (only when Home-Board is configured) |
| `scoreboard` | Points per child from Home-Board: scoreboard total, this week and month, the next payout, chores waiting for a parent's approval |

With Home-Board configured, `agenda` (up to 7 days) and the evening briefing also list the chores,
and the evening briefing says which done chores are waiting for approval.

Dates in `events`, `recurring`, `due`, `reminders` and `birthday` are validated when a note is
saved; see the vault's `_conventions.md` for the fields, including `cancels` (skip a routine item
on one day) and `break: true` (school holidays).

**PDF attachments.** Claude can't store a file you attach in the chat, so copy PDFs (insurance
policies, contracts, manuals) into the vault yourself, e.g. into `inbox/` over an SMB share. home-brain
extracts their text page by page ([pypdf](https://pypi.org/project/pypdf/)) into the search index;
Claude files them next to their notes and lists them under `attachments` in the frontmatter.
Scanned PDFs without a text layer can't be read (no OCR).

The server's instructions to Claude are the contents of the vault's `_conventions.md`, so editing
that file (and restarting) changes how Claude files things.

## Push notifications

With `NTFY_URL` set, the HTTP server checks every minute what is due and pushes it to one ntfy
topic (the stdio server never does, so a local copy can't send duplicates):

| What | When |
|---|---|
| Reminders (`reminders` in any note) | At their time; a date without a time at 08:00 |
| Tomorrow: the agenda for tomorrow, with the packing list | Daily at `BRIEFING_TIME` (19:00) |
| Week ahead: events, birthdays and deadlines, without the weekly routine | Sundays at 18:00 |
| Monday check: deadlines within their warning time, birthdays in the next 14 days with gift ideas, tire tread, age and change season, broken dates | Mondays at 08:00, only if there is something to report |

The texts are fixed templates built from `agenda`, so they match what Claude answers; they are in
English or Norwegian (`NOTIFY_LANGUAGE`). What was
sent is recorded in `DATA_PATH/scheduler.db`, not in the vault, so nothing is sent twice. After a
restart or an ntfy outage, anything missed in the last 6 hours (the same day, for briefings) is
sent late. Notes with `example: true` are ignored.

## Docker

```bash
docker compose up -d --build
```

Serves on `127.0.0.1:${HOME_BRAIN_PORT:-8000}` with `./vault` bind-mounted and the index in the
`home-brain-data` volume, without login (local testing only). The container runs as UID 1000.

For the real deployment (NAS vault over NFS, GitHub login, Cloudflare Tunnel) use
`server-docker-compose.yml` and follow [docs/deploy.md](docs/deploy.md). GitHub Actions builds
`ghcr.io/bjornerikmoen2/home-brain` on every push to `main`.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `VAULT_PATH` | `vault` | Vault root folder |
| `DATA_PATH` | `data` | Search index, login state and the record of sent notifications. Keep it on a local disk, not NFS |
| `HOST` / `PORT` | `127.0.0.1` / `8000` | HTTP bind address |
| `VAULT_GIT` | `true` | Keep the vault in git, one commit per change |
| `VAULT_GIT_AUTHOR_NAME` / `_EMAIL` | `home-brain` / `home-brain@localhost` | Author of those commits |
| `AUTH_PROVIDER` | `none` | `github` turns on GitHub login (HTTP only; stdio never needs it) |
| `PUBLIC_URL` | | Public HTTPS address, e.g. `https://brain.example.com` |
| `GITHUB_CLIENT_ID` / `GITHUB_CLIENT_SECRET` | | GitHub OAuth App credentials |
| `ALLOWED_USERS` | | Comma-separated GitHub user IDs (preferred; a login can change hands) or logins allowed to use the server |
| `OAUTH_JWT_SIGNING_KEY` | derived from the client secret | Key for the tokens home-brain issues to Claude |
| `NTFY_URL` | | ntfy server to publish to, e.g. `http://ntfy`; push notifications are off when empty |
| `NTFY_TOPIC` | `home-brain` | ntfy topic for all messages |
| `NTFY_TOKEN` | | Access token of the ntfy user home-brain publishes as |
| `BRIEFING_TIME` | `19:00` | When the evening briefing about tomorrow is sent |
| `NOTIFY_LANGUAGE` | `en` | Language of the pushed messages: `en`, or `nb` for Norwegian (Bokmål) |
| `HOMEBOARD_URL` | | Home-Board's API, e.g. `http://home-board-api:8080`; chores are off when empty |
| `HOMEBOARD_API_KEY` | | Home-Board's `Integrations__ApiKey` |

Commands: `home-brain serve [--transport http|stdio] [--host H] [--port P]`, `home-brain init`
(copy missing starter files into the vault, never overwrites), `home-brain reindex`.

## Development

```powershell
.venv\Scripts\python -m pytest
```

```
src/home_brain/
  frontmatter.py   parse/render notes, merge patch, list append, append under heading (ruamel.yaml, YAML 1.2)
  agenda.py        dated items: validation, RRULE expansion, birthdays, deadlines, breaks and cancellations
  vault.py         path safety, atomic writes, move, soft delete, starter template
  index.py         SQLite FTS5 index, refreshed by mtime scan (works on NFS)
  gitsync.py       vault git history: one commit per change, outside edits included
  auth.py          GitHub OAuth (FastMCP OAuth proxy) and the ALLOWED_USERS check
  config.py        settings from environment variables, with validation
  notify.py        ntfy client
  briefing.py      texts of the pushed reminders, briefings and Monday check
  texts.py         their words and dates in English and Norwegian (NOTIFY_LANGUAGE)
  scheduler.py     background loop: what is due, sending, record of what was sent
  homeboard.py     chores from Home-Board's API-key endpoint, as a report and as agenda items
  server.py        FastMCP server and tools
  vault_template/  starter vault
```

## License

MIT, see [LICENSE](LICENSE).
