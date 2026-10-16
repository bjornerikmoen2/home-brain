# home-brain

A self-hosted personal assistant backend: a markdown vault containing the information an AI needs
to work as an efficient personal assistant, exposed to Claude as an MCP server. Claude searches the
vault to answer questions and keeps it up to date as you share new information. Later phases add
proactive reminders pushed to your phone via ntfy.

Sibling project to [home-board](https://github.com/bjornerikmoen2/home-board).
Architecture and roadmap: [docs/plan.md](docs/plan.md).

**Status:** Phase 2. Running on a home server behind GitHub login and a Cloudflare Tunnel
([docs/deploy.md](docs/deploy.md)), with the vault on a NAS and one git commit per change. The
`agenda` and `remind` tools answer date questions; notifications (Phase 3) come next.

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
| `search_notes` | Keyword search over titles, tags, frontmatter and body (SQLite FTS5, prefix matching, diacritics-insensitive) |
| `read_note` | Read a whole note |
| `list_notes` | Browse a folder: notes plus subfolders, optional `note_type` filter |
| `write_note` | Create a note (or replace with `overwrite`), validating the frontmatter |
| `patch_note` | Merge frontmatter (JSON merge patch), append to frontmatter lists, append text under a heading; preserves comments and formatting |
| `move_note` | Rename/refile a note, never overwrites |
| `delete_note` | Soft delete into `.trash/` |
| `agenda` | Everything dated for one or more days: events, weekly routines (RRULE), birthdays, deadlines and reminders, with a per-person packing list; plus upcoming deadlines within their warning window |
| `remind` | Save a one-off reminder in `reminders.md` |

Dates in `events`, `recurring`, `due`, `reminders` and `birthday` are validated when a note is
saved; see the vault's `_conventions.md` for the fields, including `cancels` (skip a routine item
on one day) and `break: true` (school holidays).

The server's instructions to Claude are the contents of the vault's `_conventions.md`, so editing
that file (and restarting) changes how Claude files things.

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
| `DATA_PATH` | `data` | Search index and login state. Keep it on a local disk, not NFS |
| `HOST` / `PORT` | `127.0.0.1` / `8000` | HTTP bind address |
| `VAULT_GIT` | `true` | Keep the vault in git, one commit per change |
| `VAULT_GIT_AUTHOR_NAME` / `_EMAIL` | `home-brain` / `home-brain@localhost` | Author of those commits |
| `AUTH_PROVIDER` | `none` | `github` turns on GitHub login (HTTP only; stdio never needs it) |
| `PUBLIC_URL` | | Public HTTPS address, e.g. `https://brain.example.com` |
| `GITHUB_CLIENT_ID` / `GITHUB_CLIENT_SECRET` | | GitHub OAuth App credentials |
| `ALLOWED_USERS` | | Comma-separated GitHub logins allowed to use the server |
| `OAUTH_JWT_SIGNING_KEY` | derived from the client secret | Key for the tokens home-brain issues to Claude |

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
  server.py        FastMCP server and tools
  vault_template/  starter vault
```

## License

MIT, see [LICENSE](LICENSE).
