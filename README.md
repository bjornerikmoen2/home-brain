# home-brain

A personal/family assistant: a markdown vault about us, our kids, cars and house, exposed to Claude
as an MCP server, with proactive reminders pushed to our phones via ntfy (later phases).

Sibling project to [home-board](https://github.com/bjornerikmoen2/home-board).
Architecture and roadmap: [docs/plan.md](docs/plan.md).

**Status:** Phase 0 — local MVP. Vault, search index and note tools work over stdio and HTTP.
There is no authentication yet, so only run it on localhost.

## Quick start (local, Windows)

```powershell
py -3.12 -m venv .venv
.venv\Scripts\pip install -e ".[dev]"
.venv\Scripts\home-brain serve
```

The MCP endpoint is `http://127.0.0.1:8000/mcp` (health check: `/health`). On first start an empty
vault is seeded from the starter template: example notes about "Ola" and "Kari Nordmann" marked
`example: true`, plus `_conventions.md` with the filing rules. Replace the examples with real notes.

`./vault` and `./data` are git-ignored. Personal data never goes in this repo; in Phase 1 the vault
becomes its own private repo on the NAS.

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

The Claude app on the web and phone needs a public HTTPS URL with OAuth. That is Phase 1.

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

The server's instructions to Claude are the contents of the vault's `_conventions.md`, so editing
that file (and restarting) changes how Claude files things.

## Docker

```bash
docker compose up -d --build
```

Serves on `127.0.0.1:${HOME_BRAIN_PORT:-8000}` with `./vault` bind-mounted and the index in the
`home-brain-data` volume. The container runs as UID 1000.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `VAULT_PATH` | `vault` | Vault root folder |
| `DATA_PATH` | `data` | Search index and state. Keep it on a local disk, not NFS |
| `HOST` / `PORT` | `127.0.0.1` / `8000` | HTTP bind address |

Commands: `home-brain serve [--transport http|stdio] [--host H] [--port P]`, `home-brain init`
(copy missing starter files into the vault, never overwrites), `home-brain reindex`.

## Development

```powershell
.venv\Scripts\python -m pytest
```

```
src/home_brain/
  frontmatter.py   parse/render notes, merge patch, list append, append under heading (ruamel.yaml, YAML 1.2)
  vault.py         path safety, atomic writes, move, soft delete, starter template
  index.py         SQLite FTS5 index, refreshed by mtime scan (works on NFS)
  server.py        FastMCP server and tools
  vault_template/  starter vault
```
