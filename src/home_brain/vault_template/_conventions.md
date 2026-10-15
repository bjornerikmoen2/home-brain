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
| `events` | Dated items: `date`, optional `end_date`, `time`, `title`, `who`, `where`, `bring`, `notes` | see below |
| `recurring` | Repeating items: `rrule` (RFC 5545), `title`, `who`, `bring`, optional `from`/`until` | `rrule: "FREQ=WEEKLY;BYDAY=TU"` |
| `due` | Deadlines: `date`, `title`, `warn_days` (how early to warn) | EU-kontroll, insurance |
| `reminders` | Explicit reminders: `at` (date-time), `text`, optional `who` | |

```yaml
events:
  - {date: 2026-10-21, title: Forest trip, who: ola, bring: [packed lunch, rain gear]}
recurring:
  - {rrule: "FREQ=WEEKLY;BYDAY=TU", title: Gym, who: ola, bring: [gym clothes, indoor shoes]}
due:
  - {date: 2027-03-01, title: EU-kontroll, warn_days: 60}
reminders:
  - {at: "2026-10-22T18:00", text: Buy a present for Emil's party}
```

## Recipes

**Weekly school letter (ukeplan), shared as a photo or PDF**
1. Read `school/<child>/_weekly.md` for the normal routine.
2. Create `school/<child>/YYYY-wNN.md` with `type: school-week`, `who`, `week: 2026-W43` and an
   `events` item for every dated thing (trips, tests, special clothing, cancelled lessons, things to
   bring). Put homework and the teacher's message in the body.
3. If the letter changes the regular routine, update `_weekly.md` too.

**"What do we need to remember for school tomorrow?"**
Combine the child's `_weekly.md` recurring items for that weekday with the `events` for that date
in the current week's note, plus `events/` notes for that date.

**Tires and car service**
Keep current tire facts in the vehicle's `tires` frontmatter (size, bought, tread depth and when it
was measured), append each change date to `tire_changes`, and log it under `## Log`. In Norway the
legal minimum tread is 3 mm for winter tires and 1.6 mm for summer tires; suggest new tires well
before that.

**Gift ideas**
Keep `## Likes`, `## Gift ideas` and `## Gifts given` (with year and occasion) in each person's
note. When suggesting gifts, use these and avoid repeating earlier gifts.
