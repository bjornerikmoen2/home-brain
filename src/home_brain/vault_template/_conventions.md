# Vault conventions

This vault is the family's long-term memory. Claude reads and writes it through the home-brain
tools. These rules keep it tidy and machine-readable. Edit this file to change how things are filed
(home-brain loads it as instructions at startup).

## General rules

- **One topic per note.** Free text goes in the markdown body. Facts that must be looked up
  reliably (dates, sizes, numbers, people) also go in the YAML frontmatter.
- **Search before creating.** Update an existing note with `patch_note` instead of making a
  duplicate.
- **Keep history.** When a fact changes (new tires, new shoe size), update the frontmatter *and*
  add a dated line under the note's `## Log` heading, e.g. `- 2026-10-28: Changed to winter tires.`
- **File names:** lowercase kebab-case, `.md`, no spaces or special characters: `vehicles/family-car.md`.
- **Dates:** ISO `2026-10-21`. **Times:** quoted `"14:00"`. **Date-times:** quoted `"2026-10-20T19:00"`.
- **People** are referred to by their note's file name (slug): `who: ola`, `who: [ola, kari]`.
  The user's own note is `people/me.md` (`who: me`).
- **Language:** write note text in the language the user used. Frontmatter keys stay in English.
- **Unsure where something goes?** Save it in `inbox/` and tell the user.
- **Never store secrets:** no passwords, PIN codes, card or account numbers, or ID numbers.
- Notes with `example: true` are placeholders from the starter template. Don't treat them as
  facts about the family; offer to replace them with real information.

## Folders

| Folder | What goes there | `type` |
|---|---|---|
| `people/` | `me.md`, partner, relatives, close friends — one note per person | `person` |
| `people/kids/` | one note per child | `person` |
| `school/<child>/` | `_weekly.md`: the recurring weekly routine. `YYYY-wNN.md`: one note per school week | `school-weekly`, `school-week` |
| `vehicles/` | one note per vehicle | `vehicle` |
| `house/` | `house.md` facts about the house; `maintenance-log.md` | `house`, `log` |
| `events/` | one-off events: `YYYY-MM-DD-short-name.md` (parties, appointments, trips) | `event` |
| `inbox/` | anything not filed yet | — |

## Frontmatter keys

| Key | Meaning | Example |
|---|---|---|
| `type` | Kind of note (see folders) | `type: vehicle` |
| `title` | Display title (optional; defaults to the first `#` heading or file name) | |
| `tags` | Free tags | `tags: [school, gym]` |
| `who` | Person slug(s) the note is about | `who: ola` |
| `birthday` | Birth date; repeats yearly | `birthday: 2016-05-12` |
| `events` | Dated items: `date`, `title`, optional `end_date`, `time`, `end_time`, `who`, `where`, `bring`, `notes`, `cancels`, `break` | see below |
| `recurring` | Routine items: `rrule` (RFC 5545), `title`, optional `who`, `time`, `end_time`, `where`, `bring`, `from`, `until`, `exdates` | `rrule: "FREQ=WEEKLY;BYDAY=TU"` |
| `due` | Deadlines: `date`, `title`, `warn_days` (how early to warn, default 14), `done` | EU-kontroll, insurance |
| `reminders` | One-off reminders: `at` (date or date-time), `text`, optional `who`, `done`. Add them with the `remind` tool | |
| `attachments` | PDFs in the vault that belong to this note (vault paths) | `attachments: [house/home-insurance-2026.pdf]` |

```yaml
events:
  - {date: 2026-10-21, title: Forest trip, who: ola, bring: [packed lunch, rain gear]}
  - {date: 2026-10-22, title: Swimming cancelled, cancels: Swimming}
  - {date: 2026-12-21, end_date: 2027-01-01, title: Christmas break, break: true}
recurring:
  - {rrule: "FREQ=WEEKLY;BYDAY=TU", title: Gym, who: ola, bring: [gym clothes, indoor shoes]}
  - {rrule: "FREQ=WEEKLY;INTERVAL=2;BYDAY=MO", from: 2026-10-05, title: Paper bin out}
due:
  - {date: 2027-03-01, title: EU-kontroll, warn_days: 60}
reminders:
  - {at: "2026-10-22T18:00", text: Buy a present for Per's party}
```

How the `agenda` tool reads them:
- `who` on an item falls back to the note's own `who`.
- **`cancels`**: on that day, routine items for the same person whose title contains this word
  are skipped.
- **`break: true`** with an `end_date` (school holidays): routine items from the same folder are
  skipped for the whole period.
- A routine whose dates depend on its start (every other week, `COUNT`, monthly without a day)
  needs `from`. `exdates` lists single days to skip.
- Invalid dates are refused when saving, and reported by `agenda` when found in older notes.

## Recipes

**Weekly school letter (ukeplan), shared as a photo or PDF**
1. Read `school/<child>/_weekly.md` for the normal routine.
2. Create `school/<child>/YYYY-wNN.md` with `type: school-week`, `who`, `week: 2026-W43` and an
   `events` item for every dated thing (trips, tests, special clothing, cancelled lessons, things to
   bring). Put homework and the teacher's message in the body.
3. Don't repeat routine items as events; if the letter says a routine lesson is cancelled, add an
   event with `cancels`. If it changes the regular routine, update `_weekly.md` instead.
4. School holidays go in `_weekly.md` as events with `break: true` and an `end_date`.

**"What do we need to remember for school tomorrow?"**
Call `agenda` with `start: tomorrow`: it combines the routine, that week's events, breaks and
cancellations, and lists what each child needs to bring. Read its `sources` for homework details.

**"Remind me ..."**
Use the `remind` tool; the reminder is stored in `reminders.md`, shows up in `agenda`, and is
pushed to the phone at its time (a date without a time at 08:00). For "in 20 minutes" pass
`at: "+20m"`. To send something to the phone right now, use `notify`.

**Calendar**
`agenda` also lists the family calendar's events (`"source": "calendar"`). They live in the
calendar, so don't copy them into notes; add a note only for details the calendar lacks (what to
bring, who goes), with the same title so `agenda` shows them as one item.

**Chores**
The kids' chores and points live in Home-Board, not in the vault: don't make notes for them. Use
the `chores` tool for "has Ola done her chores?" (`agenda` lists them too) and `scoreboard` for
points, allowance and chores waiting for approval. When the user asks, approve done chores with
`approve_chores` (the `id`s from `scoreboard`), reject one that wasn't done with `reject_chore`,
and give bonus points with `award_points`; say what was approved and how many points were given.

**Documents (PDF attachments)**
You can't save a file the user attaches in the chat. To keep the PDF itself (insurance policies,
contracts, manuals), ask them to copy it into the vault's `inbox/` folder over the network share.
1. `list_notes` lists PDFs under `attachments`; read one with `read_attachment`.
2. File it with `move_note` into the folder of the note it belongs to, with a kebab-case name that
   says what and when: `house/home-insurance-2026.pdf`.
3. List it under `attachments` in every note it concerns. Moving or deleting a PDF with the tools
   updates these lists.
4. Put the key facts (price, cover, deductibles, renewal date as `due`) in the notes. For details,
   `search_notes` also finds pages inside PDFs; read them and name the page you answer from.
5. A scanned PDF has no text to read: ask the user to attach it in the chat instead.

**Tires and car service**
Keep current tire facts in the vehicle's `tires` frontmatter (size, bought, tread depth and when it
was measured), append each change date to `tire_changes`, and log it under `## Log`. In Norway the
legal minimum tread is 3 mm for winter tires and 1.6 mm for summer tires; suggest new tires well
before that.

**Gift ideas**
Keep `## Likes`, `## Gift ideas` and `## Gifts given` (with year and occasion) in each person's
note. When suggesting gifts, use these and avoid repeating earlier gifts.
