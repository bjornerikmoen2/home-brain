# home-brain — plan

A self-hosted personal assistant backend. The information an AI needs to work as an efficient
personal assistant (people, schedules, vehicles, home, events) lives as markdown files in a vault;
Claude reads and writes it through an MCP server; a scheduler sends proactive reminders to the
user's phone.

Example questions it should answer:

- "What needs to be packed for school tomorrow?"
- "When were the tires last changed, and what size are they?" / "Is it time for new winter tires?"
- "What would be a good birthday present for <person>?"
- Push: "Reminder: <child> is invited to a birthday party tomorrow at 14:00."

Inspiration: [basic-memory](https://github.com/basicmachines-co/basic-memory),
[Khoj](https://github.com/khoj-ai/khoj), OpenClaw, me-db.

## Decisions

| Topic | Decision | Why |
|---|---|---|
| Repo | New repo `home-brain`, separate from `home-board` | Different domain, runtime and security posture; personal data must never sit next to public code |
| Language | Python 3.12 + [FastMCP](https://gofastmcp.com) | Its OAuth proxy makes Claude-app custom connectors work with GitHub/Google login; best LLM tooling |
| Vault storage | TrueNAS dataset (NFS-mounted into the container), git-versioned, pushed to a private GitHub repo `home-brain-vault` | Local-first, ZFS snapshots, every AI write is an undoable commit, offsite backup, Obsidian-friendly |
| Notifications | Self-hosted [ntfy](https://ntfy.sh) | One container, free iOS/Android app, trivial HTTP API |
| Exposure | Cloudflare Tunnel | Public HTTPS for claude.ai without port forwarding |
| Chat UI | Claude app (web/desktop/mobile) via custom connector | Uses existing subscription; no API cost for interactive use |
| Timezone | Europe/Oslo | Same as home-board |
| Scheduler | A 1-minute loop in the server, with a record of what was sent in SQLite (not APScheduler) | After a restart, missed reminders and briefings are sent late instead of lost; no extra dependency |
| Briefings | Fixed text templates, one ntfy topic, 19:00 | Deterministic and free; per-person topics can come with other household members (Phase 4) |

## Architecture

```
 Claude app (phone / desktop / web)                 phone (ntfy app)
        │ custom connector: HTTPS + OAuth                    ▲
        ▼                                                    │
   Cloudflare Tunnel ───────────────────────────────────┐    │
        │                                               │    │
┌─ docker server ───────────────────────────────────────┼────┼──┐
│  home-brain (Python, FastMCP, streamable HTTP)        ▼    │  │
│   ├─ MCP tools: search/read/write/patch/agenda/remind  ntfy ┘  │
│   ├─ index: SQLite FTS5 + frontmatter (local volume)    ▲      │
│   ├─ scheduler (1-minute loop): reminders, briefings ───┘      │
│   └─ gitsync: commit per write, debounced push                 │
│        │ /vault (NFS)                  │ REST (service acct)   │
└────────┼───────────────────────────────┼───────────────────────┘
         ▼                               ▼
   TrueNAS dataset ── git push ──► GitHub (private)   home-board API
```

**Who does the thinking?**

- Interactive: Claude in the Claude app (subscription). The server only stores, indexes and
  aggregates; it has no LLM of its own for this path.
- Ingestion: attach a photo/PDF of the weekly school letter in the Claude app and say "save this".
  Claude extracts it and calls `write` with structured frontmatter. No OCR pipeline needed.
- Proactive: the scheduler is deterministic (dates in frontmatter). Optionally the evening briefing
  is phrased by the Claude API (Haiku) — pennies per month, needs `ANTHROPIC_API_KEY`.

## Vault conventions

Plain markdown + YAML frontmatter. Facts that need to be *queried* (dates, sizes, numbers) go in
frontmatter; everything else is free text. Folder layout:

```
vault/
  _conventions.md        # filing rules; served to Claude as MCP server instructions
  people/
    me.md
    <name>.md            # birthday, sizes, likes/dislikes, gift ideas, gifts given (with year)
    kids/<name>.md       # birthday, school, class, teacher, friends, sizes, activities
  school/<kid>/
    _weekly.md           # recurring: gym Tuesdays, swimming Fridays, library day
    2026-w43.md          # one note per school week
  vehicles/<car>.md      # reg no, tires, tire changes, service, EU-kontroll
  house/
    house.md             # address facts, appliances, filters, paint colours…
    maintenance-log.md
  events/                # one-off things: parties, appointments, trips
  reminders.md           # explicit reminders (or `reminders:` in any note)
  inbox/                 # unfiled; Claude files it later
```

Common frontmatter keys understood by the agenda engine:

```yaml
type: school-week | person | vehicle | house | event | ...
tags: [..]
birthday: 2015-03-14            # yearly recurrence
events:                         # dated items
  - { date: 2026-10-21, time: "08:30", title: Gym, who: <kid>, bring: [gym clothes] }
recurring:                      # RFC 5545 RRULE
  - { rrule: "FREQ=WEEKLY;BYDAY=TU", title: Gym, who: <kid>, bring: [gym clothes, indoor shoes] }
due:                            # deadlines with lead time
  - { date: 2027-03-01, title: EU-kontroll, warn_days: 60 }
reminders:
  - { at: 2026-10-20T19:00, text: Buy birthday present for Saturday's party }
```

Vehicle example:

```yaml
type: vehicle
name: Family car
reg: AB 12345
tires:
  winter: { size: 235/55R18, studded: false, bought: 2022-10, tread_mm: 4.5, measured: 2026-04-10 }
  summer: { size: 235/55R18, bought: 2024-04, tread_mm: 6.0, measured: 2026-10-28 }
tire_changes: [2026-04-10, 2025-10-28]
due:
  - { date: 2027-03-01, title: EU-kontroll, warn_days: 60 }
```

## MCP tools

Keep the surface small and generic; domain logic lives in conventions + `agenda`.

| Tool | Purpose |
|---|---|
| `search_notes(query, folder?, note_type?, limit?)` | FTS5 search over body + frontmatter; returns paths + snippets, and matching pages of PDF attachments |
| `read_note(path)` | Full note (frontmatter + body) |
| `read_attachment(path, first_page?, last_page?)` | Text of a PDF in the vault, page by page (about 30 000 characters per call) |
| `list_notes(folder?, recursive?, note_type?)` | Browse the vault: notes and PDF attachments |
| `write_note(path, content, overwrite?)` | Create/replace a note (validated frontmatter; warns about `attachments` that don't exist) |
| `patch_note(path, set_frontmatter?, append_to_list?, append_text?, under_heading?)` | Merge frontmatter / append to lists / append under a heading |
| `move_note(path, new_path)` | Refile a note or PDF (e.g. out of `inbox/`); notes listing a moved PDF are updated |
| `delete_note(path)` | Soft delete of a note or PDF: move to `.trash/` |
| `agenda(from, to, who?)` | Everything dated in range: events, recurring, birthdays, due, reminders, and home-board chores (up to 7 days) |
| `chores(day?, who?, days?)` | home-board chores per person: done, still to do, shared |
| `scoreboard(who?)` | home-board points per child, next payout, chores waiting for approval |
| `remind(at, text, who?)` | Add a reminder (written to `reminders.md`) |
| `notify(text, title?, priority?)` | Push immediately via ntfy |

Plus: server `instructions` = contents of `vault/_conventions.md`, and an MCP prompt
`daily-briefing` for asking Claude for a summary of a day.

## Scheduler & notifications

A background thread in the home-brain HTTP server (single process, Europe/Oslo) checks every
minute what is due:

| Job | When | What |
|---|---|---|
| Due reminders | every minute | Send `reminders` whose `at` has passed (a date without a time at 08:00) |
| Tomorrow briefing | daily 19:00 (`BRIEFING_TIME`) | `agenda(tomorrow)` → "Gym (Ola) … Bring: Ola: gym clothes, indoor shoes"; "Nothing on the agenda." on quiet days, so silence means something is wrong |
| Week ahead | Sunday 18:00 | `agenda(next 7 days)` without the weekly routine |
| Maintenance check | Monday 08:00 | `due` items within `warn_days`; tread close to legal minimum (winter 3 mm / summer 1.6 mm); tires older than 6 years; tire-change season (Oct/Apr); birthdays within 14 days → include gift ideas from the person's note; invalid dates. Not sent when there is nothing to report |

State for "already sent" lives in SQLite (not in the vault) so the vault stays clean. Anything
missed while the server or ntfy was down is sent up to 6 hours late (briefings only on the same
day). Notes with `example: true` are skipped.

ntfy: self-hosted, `auth-default-access: deny-all`, a publish token for home-brain, a user for the
phone app. For instant iOS delivery set `upstream-base-url: https://ntfy.sh` (ntfy.sh only sees a
message ID, not the content).

## Deployment

```yaml
# docker-compose.yml (sketch)
services:
  home-brain:
    image: ghcr.io/bjornerikmoen2/home-brain
    environment:
      TZ: Europe/Oslo
      VAULT_PATH: /vault
      DATA_PATH: /data                 # SQLite index + scheduler state (NOT on NFS)
      PUBLIC_URL: https://brain.<domain>
      OAUTH_PROVIDER: github           # or google
      OAUTH_CLIENT_ID: ${OAUTH_CLIENT_ID}
      OAUTH_CLIENT_SECRET: ${OAUTH_CLIENT_SECRET}
      ALLOWED_USERS: ${ALLOWED_USERS}  # allowlist of GitHub logins / emails
      NTFY_URL: http://ntfy
      NTFY_TOPIC: ${NTFY_TOPIC}
      NTFY_TOKEN: ${NTFY_TOKEN}
      GIT_REMOTE: ${VAULT_GIT_REMOTE}  # the private vault repo, e.g. git@github.com:<you>/home-brain-vault.git
      ANTHROPIC_API_KEY: ${ANTHROPIC_API_KEY:-}   # optional
      HOMEBOARD_URL: http://home-board-api:8080   # phase 4
    volumes:
      - vault:/vault
      - home-brain-data:/data
      - ./secrets/deploy_key:/run/secrets/deploy_key:ro
    networks: [proxy]

  ntfy:
    image: binwiederhier/ntfy
    command: serve
    environment:
      NTFY_BASE_URL: https://ntfy.<domain>
      NTFY_UPSTREAM_BASE_URL: https://ntfy.sh
      NTFY_BEHIND_PROXY: "true"
      NTFY_AUTH_FILE: /var/lib/ntfy/auth.db
      NTFY_AUTH_DEFAULT_ACCESS: deny-all
    volumes: [ntfy-data:/var/lib/ntfy]
    networks: [proxy]

  # cloudflared: reuse the existing tunnel if there is one; add public hostnames
  #   brain.<domain> -> http://home-brain:8000
  #   ntfy.<domain>  -> http://ntfy:80

volumes:
  vault:
    driver: local
    driver_opts: { type: nfs, o: "addr=<nas-ip>,rw,nfsvers=4", device: ":/mnt/<pool>/home-brain/vault" }
  home-brain-data:
  ntfy-data:

networks:
  proxy: { external: true }
```

Notes:

- **Do not** put Cloudflare Access in front of `brain.<domain>` — Claude's cloud can't complete an
  Access login. Auth is handled by the app's OAuth (FastMCP OAuth proxy + allowlist).
- SQLite must not live on NFS (locking). The index is rebuildable from the vault at any time.
- inotify doesn't work over NFS; the index refreshes by mtime scan before queries and on a timer,
  so edits made in Obsidian over SMB are picked up.
- The OAuth app callback is `https://brain.<domain>/auth/callback`.

## Security

- OAuth 2.1 via FastMCP OAuth proxy (GitHub or Google), plus an explicit allowlist check on the
  authenticated identity — reject everyone else even if they have a valid provider login. The
  check runs when the token is verified (401 before the MCP handshake, whose instructions hold
  the vault conventions) and again on every tool/resource/prompt. Match GitHub user IDs, not
  logins: a renamed login can be registered by someone else.
- All paths resolved and confined to the vault root (no `..`, no symlinks out). No shell/exec tools.
- No hard delete: `.trash/` + git history. Every write = commit with a descriptive message.
- Secrets only in `.env` / Docker secrets; the code repo never contains vault data.
- Note content is data, not instructions — uploaded documents could contain text aimed at the
  model; tools only ever touch the vault, so the blast radius is limited to recoverable edits.
- Phase 4 multi-user: per-identity private folders (`private/<user>/`) so e.g. gift ideas for one
  household member stay hidden from that member's own connector.

## home-board integration (phase 4)

- home-board has a read-only `GET /api/integrations/chores` endpoint protected by a shared API key
  (`Integrations__ApiKey`) instead of a user login, so home-brain can read chores but not change
  anything. The containers talk over the `proxy` network.
- Done: `chores(day?, who?, days?)`; `agenda` (up to 7 days) and the evening briefing list chores.
- Done: `scoreboard(who?)` from `GET /api/integrations/points`: points, next payout, chores waiting
  for approval (also in the evening briefing).
- Done: `approve_chores(ids, points?)`, `reject_chore(id, reason?)` and
  `award_points(who, points, reason)`, through home-board's write endpoints. They take a separate
  key (`Integrations__WriteApiKey` / `HOMEBOARD_WRITE_KEY`), so read-only stays the default.
- Optional: home-board scoreboard tablet shows a "Tomorrow" panel from a read-only home-brain
  endpoint (Flutter change in home-board).

## Phases

### Phase 0 — Local MVP
- [x] Repo scaffold: `pyproject.toml` (hatchling; works with pip or uv), `src/home_brain/`, tests, Dockerfile, compose
- [x] `src/home_brain/vault_template/` with `_conventions.md` and one example note per type
- [x] Vault layer: safe paths, frontmatter parse/validate, write/patch/move/soft delete
- [x] SQLite FTS5 index with mtime refresh
- [x] Tools: `search_notes`, `read_note`, `list_notes`, `write_note`, `patch_note`, `move_note`, `delete_note`
- [x] Smoke-tested over HTTP, stdio and in Docker
- [x] Connect from Claude Desktop / Claude Code and try it with real notes

**Done when:** locally, "save this school letter" (photo attached) creates a correct
`school/<kid>/2026-wNN.md`, and "what's on Thursday?" finds it.

### Phase 1 — In the Claude app
- [x] FastMCP OAuth proxy (GitHub, `read:user` only) + `ALLOWED_USERS` check on every request (token) and every tool list/call
- [x] Git commit per write in the vault (outside edits included in the next commit)
- [x] `server-docker-compose.yml` with the NFS vault volume; all site values in `.env`
- [x] Image built and pushed to GHCR via GitHub Actions (same pattern as home-board)
- [x] Setup guide: [deploy.md](deploy.md)
- [x] NAS dataset + NFS share; move the local vault onto it
- [x] GitHub OAuth App; Cloudflare Tunnel hostname `brain.<domain>`; deploy
- [x] Add as custom connector in claude.ai; verify on phone
- [x] SMB share of the same dataset for browsing/editing from Windows (`force user` = vault owner)
- [ ] Later: debounced push of the vault history to a private `home-brain-vault` repo

**Done when:** from the phone, "the winter tires are 235/55R18, changed today" updates the car note
and shows up as a commit in GitHub.

### Phase 2 — Structured data & agenda
- [x] Validation of the agenda keys (`events`, `recurring`, `due`, `reminders`, `birthday`) on every
      write/patch, with readable errors; only *new* problems block a save (no per-`type` schemas:
      the shared keys are what the agenda depends on)
- [x] `agenda` with events, RRULE recurrence, birthdays, `due` + `warn_days`, reminders, school
      breaks (`break: true`), cancellations (`cancels`), per-person packing lists, and
      deduplication of routine items repeated as events
- [x] `remind` tool (stored in `reminders.md`)
- [x] Compact YAML for new items (one-line flow style, quoted times)
- [x] Real data seeded by the user; dry-run of `agenda` against the live vault

**Done when:** "what needs to be packed for school tomorrow?" is answered from one `agenda` call.

### Phase 3 — Proactive
- [x] ntfy container + auth; `notify` tool
- [x] Scheduler: due reminders, 19:00 tomorrow briefing, Sunday week-ahead, Monday maintenance
- [x] `remind` accepts times relative to now (`+15m`), since Claude doesn't always know the time
- [x] Deploy: `ntfy.<domain>` tunnel hostname, ntfy users and token, phone app
- [ ] Later, if wanted: Claude API phrasing for briefings (fixed templates for now)

**Done when:** a reminder added from the Claude app arrives as a push at the right time, and the
evening briefing arrives daily.

### Phase 4 — Extras
- [x] home-board chores: `chores` tool, chores in `agenda` and the evening briefing
- [x] home-board points: `scoreboard` (read-only, same key); approvals waiting in the evening briefing
- [x] Calendar events (Google Calendar's secret iCal address) in `agenda`, the evening briefing and
      the week ahead; a feed that can't be read is mentioned instead of silently missing
- [x] Approving chores from Claude: `approve_chores`, `reject_chore` and `award_points`, through
      home-board's write endpoints and a separate write key (`HOMEBOARD_WRITE_KEY`)
- [x] PDF attachments: the user copies PDFs in over SMB (Claude can't hand over the file it was
      shown); their text is indexed per page (pypdf), `read_attachment` reads it, notes link to
      them with `attachments`. No OCR for scans yet.
- [ ] Two-way chat channel (Slack/Telegram/WhatsApp) using Claude API + the same tools
- [ ] Access for other household members, with private folders
- [ ] Semantic search (sqlite-vec) — only if keyword search proves insufficient
- [ ] Auto-ingest: forward school e-mails / drop files in `inbox/` → Claude API files them

## Open questions

- Domain / hostnames for the tunnel (`brain.<domain>`, `ntfy.<domain>`); is cloudflared already running?
- NAS: IP, pool/dataset path, NFS export permissions (container UID).
- OAuth provider: GitHub (simplest) or Google?
- Which people to model, and how school information arrives (PDF, apps like Vigilo/Visma, e-mail?).
