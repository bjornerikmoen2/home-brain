"""The MCP server: vault tools for Claude."""

from __future__ import annotations

import datetime as dt
import logging
import re
import secrets
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated, Any

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from pydantic import Field
from ruamel.yaml.comments import CommentedMap
from starlette.requests import Request
from starlette.responses import PlainTextResponse

from . import __version__, frontmatter
from .agenda import Item, Note, as_date, as_moment
from .agenda import build as build_agenda
from .auth import build_auth
from .calendars import Calendars
from .config import Settings
from .documents import is_document
from .gitsync import GitError, VaultGit
from .homeboard import (
    HomeBoard,
    HomeBoardError,
    HomeBoardRefused,
    agenda_items,
    chore_report,
    find_child,
    points_report,
)
from .index import Index
from .notify import Message, Notifier, NotifyError, Ntfy, Priority
from .scheduler import Scheduler
from .texts import LANGUAGES
from .vault import Vault, VaultError

log = logging.getLogger(__name__)

CONVENTIONS = "_conventions.md"

INTRO = """\
home-brain is this family's long-term memory: markdown notes about the people, the kids' school,
cars, house and events. Search it before answering questions about the family, and save new facts
the user shares. Prefer updating an existing note (patch_note) over creating a duplicate.
For anything about dates ("what's on tomorrow?", "what do the kids need for school?", "upcoming
birthdays or deadlines?") call `agenda` first; save reminders with `remind`.
PDFs the user copied into the vault (insurance policies, contracts, manuals) are attachments:
search_notes also searches their text, and read_attachment reads them.
The vault's filing conventions follow; read them again with read_note('_conventions.md') if needed.
"""

REMINDERS = "reminders.md"
CHORE_DAYS = 7
"""`agenda` lists home-board chores for ranges up to this many days."""
_FROM_NOW = re.compile(r"^\+\s*([1-9]\d*)\s*([mhd])$")
_UNITS = {"m": "minutes", "h": "hours", "d": "days"}
REMINDERS_BODY = "\n# Reminders\n\nOne-off reminders saved with the remind tool. Mark old ones `done: true` or delete them.\n"


def today() -> dt.date:
    return dt.date.today()


def now() -> dt.datetime:
    return dt.datetime.now()


def _parse_day(text: str) -> dt.date:
    word = text.strip().lower()
    if word in ("today", ""):
        return today()
    if word == "tomorrow":
        return today() + dt.timedelta(days=1)
    if word == "yesterday":
        return today() - dt.timedelta(days=1)
    day = as_date(word)
    if day is None or len(word) != 10:
        raise VaultError("start must be 'today', 'tomorrow' or a date like 2026-10-21")
    return day

READ_ONLY = {"readOnlyHint": True, "openWorldHint": False}
EDIT = {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False}
DELETE = {"readOnlyHint": False, "destructiveHint": True, "openWorldHint": False}
SEND = {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": True}


@contextmanager
def _tool_errors() -> Iterator[None]:
    try:
        yield
    except VaultError as exc:
        raise ToolError(str(exc)) from exc


def build_instructions(vault: Vault) -> str:
    try:
        _, conventions, _ = vault.read(CONVENTIONS)
    except VaultError:
        log.warning("%s not found in the vault; run `home-brain init` to add the starter files", CONVENTIONS)
        return INTRO
    return f"{INTRO}\n{conventions}"


def open_vault(settings: Settings) -> Vault:
    """The vault, with a git commit after every change unless versioning is off or unavailable."""
    if not settings.vault_git:
        return Vault(settings.vault_path)
    if not VaultGit.available():
        log.warning("git is not installed; vault changes will not be versioned")
        return Vault(settings.vault_path)
    git = VaultGit(settings.vault_path, settings.git_author_name, settings.git_author_email)
    try:
        git.ensure_repo()
    except GitError:
        log.exception("could not set up git in %s; vault changes will not be versioned", settings.vault_path)
        return Vault(settings.vault_path)

    def commit(paths: list[str], description: str) -> None:
        # The note is already saved; a failed commit must not fail the tool call.
        try:
            git.commit(paths, description)
        except GitError:
            log.exception("git commit failed for %s", ", ".join(paths))

    return Vault(settings.vault_path, on_change=commit)


def create_server(
    settings: Settings,
    notifier: Notifier | None = None,
    run_scheduler: bool = False,
    homeboard: HomeBoard | None = None,
    calendars: Calendars | None = None,
) -> FastMCP:
    """The MCP server. With a notifier (from NTFY_URL unless given), it also has the `notify` tool,
    and `run_scheduler` starts pushing reminders and briefings in the background. With home-board
    (from HOMEBOARD_URL unless given) it has the `chores` tool and chores in `agenda`. With
    calendars (from CALENDAR_URLS unless given) their events are in `agenda` and the briefings."""
    vault = open_vault(settings)
    index = Index(vault, settings.data_path / "index.db")
    stats = index.refresh(force=True)
    log.info("vault %s: %d notes indexed (%s)", vault.root, index.count(), stats)
    notifier = notifier or Ntfy.from_settings(settings)
    if notifier is None:
        log.info("push notifications are off (NTFY_URL is not set)")
    homeboard = homeboard or HomeBoard.from_settings(settings)
    if homeboard is None:
        log.info("chores are off (HOMEBOARD_URL is not set)")
    calendars = calendars or Calendars.from_settings(settings)
    if calendars is None:
        log.info("calendar events are off (CALENDAR_URLS is not set)")

    def chore_items(first: dt.date, last: dt.date) -> tuple[list[Item], str | None]:
        if homeboard is None:
            return [], None
        try:
            return agenda_items(homeboard.chores(first, last), today()), None
        except HomeBoardError as exc:
            log.warning("agenda without chores: %s", exc)
            return [], f"Chores are missing: {exc}"

    def attachment_warnings(meta: dict[str, Any]) -> list[str]:
        """Problems with the `attachments` a note lists, so a typo doesn't leave a dead link."""
        links = meta.get("attachments")
        if links is None:
            return []
        if isinstance(links, str):
            links = [links]
        if not isinstance(links, list) or not all(isinstance(link, str) for link in links):
            return ["attachments: list the vault paths of PDFs, like [insurance/home-policy-2026.pdf]"]
        warnings = []
        for link in links:
            try:
                full = vault.attachment_path(link)[1]
            except VaultError as exc:
                warnings.append(f"attachments: {link}: {exc}")
                continue
            if not full.is_file():
                warnings.append(f"attachments: {link} is not in the vault (list_notes shows the attachments)")
        return warnings

    def relink(old: str, new: str | None) -> list[str]:
        """Point the notes that list attachment `old` at `new`, or drop it; returns the notes changed."""
        changed = []
        for note, links in index.linked_from(old).items():
            if isinstance(links, list):
                value = [new if link == old else link for link in links if new or link != old] or None
            else:
                value = new
            vault.patch(note, set_frontmatter={"attachments": value})
            changed.append(note)
        return changed

    auth, middleware = build_auth(settings)
    mcp = FastMCP(
        name="home-brain",
        instructions=build_instructions(vault),
        version=__version__,
        auth=auth,
        middleware=middleware,
        mask_error_details=True,
    )

    @mcp.tool(annotations=READ_ONLY)
    def search_notes(
        query: Annotated[str, Field(description="Keywords, not a question: e.g. 'gym ola', 'winter tires', 'kari birthday'.")],
        folder: Annotated[str, Field(description="Only search under this folder, e.g. 'school/ola'.")] = "",
        note_type: Annotated[str | None, Field(description="Only notes whose frontmatter `type` equals this.")] = None,
        limit: Annotated[int, Field(ge=1, le=50)] = 10,
    ) -> dict[str, Any]:
        """Full-text search over all notes (title, tags, frontmatter and body), best matches first.

        Every keyword is prefix-matched and any keyword may match, so a few distinctive words work best.
        Returns paths and snippets; use read_note to get a whole note.

        It also searches the text of attachments (PDFs in the vault, e.g. insurance policies):
        matches are under `attachments`, with the best `page` and other matching pages; read them
        with read_attachment. Prefer the notes for key facts and the attachments for fine print.
        """
        with _tool_errors():
            folder_rel = vault.folder_path(folder)[0]
            results = index.search(query, folder_rel, note_type, limit)
            attachments = [] if note_type else index.search_attachments(query, folder_rel, limit)
        response: dict[str, Any] = {"query": query, "results": results}
        if attachments:
            response["attachments"] = attachments
        if not results and not attachments:
            response["hint"] = "No matches. Try other or fewer keywords, or browse with list_notes."
        return response

    @mcp.tool(annotations=READ_ONLY)
    def read_note(
        path: Annotated[str, Field(description="Vault-relative path, e.g. 'vehicles/family-car.md'.")],
    ) -> dict[str, Any]:
        """Read a whole note: YAML frontmatter plus markdown body."""
        with _tool_errors():
            rel, text, stat = vault.read(path)
        modified = dt.datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat(timespec="seconds")
        return {"path": rel, "content": text, "modified": modified}

    @mcp.tool(annotations=READ_ONLY)
    def read_attachment(
        path: Annotated[str, Field(description="Vault-relative path of the PDF, e.g. 'insurance/home-policy-2026.pdf'.")],
        first_page: Annotated[int, Field(ge=1, description="First page to read.")] = 1,
        last_page: Annotated[int | None, Field(ge=1, description="Last page to read; leave out to read on as far as fits.")] = None,
    ) -> dict[str, Any]:
        """Read the text of an attachment: a PDF the user copied into the vault, such as an
        insurance policy, contract or manual. Notes list theirs under `attachments`, and
        search_notes finds the pages that match. Long documents come in parts: continue at
        `next_page`. Name the page when you answer from it. The text is taken from the PDF, so
        tables may lose their layout. `linked_from` lists the notes that link to it.
        """
        with _tool_errors():
            rel = vault.attachment_path(path)[0]
            result = index.read_attachment(rel, first_page, last_page)
        result["linked_from"] = list(index.linked_from(rel))
        return result

    @mcp.tool(annotations=READ_ONLY)
    def list_notes(
        folder: Annotated[str, Field(description="Folder to list; empty for the whole vault.")] = "",
        recursive: Annotated[bool, Field(description="Include notes in subfolders.")] = True,
        note_type: Annotated[str | None, Field(description="Only notes whose frontmatter `type` equals this.")] = None,
    ) -> dict[str, Any]:
        """List notes (path, title, type, modified) under a folder, plus its subfolders, and the
        attachments (PDFs) there, e.g. new ones the user copied into inbox/."""
        with _tool_errors():
            folder_rel = vault.folder_path(folder)[0]
        return index.list_notes(folder_rel, note_type, recursive)

    @mcp.tool(annotations=EDIT)
    def write_note(
        path: Annotated[str, Field(description="Vault-relative path; lowercase-kebab-case, e.g. 'events/2026-10-24-party.md'.")],
        content: Annotated[str, Field(description="Full note text: optional '---' YAML frontmatter block, then markdown.")],
        overwrite: Annotated[bool, Field(description="Replace an existing note. Prefer patch_note for edits.")] = False,
    ) -> dict[str, Any]:
        """Create a new note (or replace one with overwrite=true). Missing folders are created.

        Follow the vault conventions for folder, file name and frontmatter keys. Search first so you
        don't create a duplicate of an existing note.
        """
        with _tool_errors():
            rel, created = vault.write(path, content, overwrite)
        index.refresh(force=True)
        result: dict[str, Any] = {"path": rel, "created": created}
        if warnings := attachment_warnings(frontmatter.to_plain(frontmatter.parse(content.replace("\r\n", "\n"))[0])):
            result["warnings"] = warnings
        return result

    @mcp.tool(annotations=EDIT)
    def patch_note(
        path: Annotated[str, Field(description="Vault-relative path of an existing note.")],
        set_frontmatter: Annotated[
            dict[str, Any] | None,
            Field(description="JSON merge patch for the frontmatter: nested objects merge, null deletes a key, other values replace."),
        ] = None,
        append_to_list: Annotated[
            dict[str, Any] | None,
            Field(description="Items to append to frontmatter lists, keyed by dotted path, e.g. {\"events\": [{...}], \"tire_changes\": [\"2026-10-28\"]}."),
        ] = None,
        append_text: Annotated[str | None, Field(description="Markdown to append to the body.")] = None,
        under_heading: Annotated[
            str | None,
            Field(description="Append the text at the end of this section (e.g. 'Log'); the heading is created if missing."),
        ] = None,
    ) -> dict[str, Any]:
        """Update an existing note in place without rewriting it: merge frontmatter fields, append to
        frontmatter lists, and/or append text (optionally under a heading). Untouched parts, including
        comments and formatting, are preserved. Returns the updated frontmatter.
        """
        with _tool_errors():
            rel, meta = vault.patch(
                path,
                set_frontmatter=set_frontmatter,
                append_to_list=append_to_list,
                append_text=append_text,
                under_heading=under_heading,
            )
        index.refresh(force=True)
        result: dict[str, Any] = {"path": rel, "frontmatter": meta}
        if warnings := attachment_warnings(meta):
            result["warnings"] = warnings
        return result

    @mcp.tool(annotations=EDIT)
    def move_note(
        path: Annotated[str, Field(description="Current vault-relative path of a note or attachment (PDF).")],
        new_path: Annotated[str, Field(description="New vault-relative path; must not exist yet.")],
    ) -> dict[str, Any]:
        """Rename a note or attachment, or file it into another folder (e.g. out of inbox/).
        Notes that list a moved attachment under `attachments` are updated to the new path."""
        with _tool_errors():
            old, new = vault.move(path, new_path)
            relinked = relink(old, new) if is_document(old) else []
        index.refresh(force=True)
        result: dict[str, Any] = {"from": old, "to": new}
        if relinked:
            result["links_updated"] = relinked
        return result

    @mcp.tool(annotations=DELETE)
    def delete_note(
        path: Annotated[str, Field(description="Vault-relative path of the note or attachment (PDF) to delete.")],
    ) -> dict[str, Any]:
        """Delete a note or attachment. It is moved to the vault's .trash folder, so it can be
        recovered by hand. A deleted attachment is also removed from the notes that list it."""
        with _tool_errors():
            rel, trashed = vault.delete(path)
            unlinked = relink(rel, None) if is_document(rel) else []
        index.refresh(force=True)
        result: dict[str, Any] = {"path": rel, "trashed_to": trashed}
        if unlinked:
            result["links_removed"] = unlinked
        return result

    @mcp.tool(annotations=READ_ONLY)
    def agenda(
        start: Annotated[str, Field(description="First day: 'today', 'tomorrow' or a date like 2026-10-21.")] = "today",
        days: Annotated[int, Field(ge=1, le=366, description="How many days from `start`: 1 = just that day, 7 = a week.")] = 1,
        who: Annotated[str | None, Field(description="Only items for this person (note slug, e.g. 'ola').")] = None,
        folder: Annotated[str, Field(description="Only notes under this folder, e.g. 'school'.")] = "",
    ) -> dict[str, Any]:
        """Everything dated in the vault for one or more days, grouped by day: events, weekly routines
        (`recurring`), birthdays, deadlines (`due`) and reminders, with a per-person `bring` list for
        each day. Also returns deadlines coming up within their `warn_days` or recently missed
        (`upcoming`), and invalid dates it had to skip (`problems`; fix them with patch_note).

        Call it first for questions like "what do the kids need for school tomorrow?", "what's on this
        week?" (days=7) or "any birthdays coming up?" (days=60). Read the `sources` notes for details
        such as homework or school hours.

        Routine items are skipped on days covered by an event with `break: true` and an `end_date` in
        the same folder (school holidays), and on days with an event whose `cancels` text appears in
        their title (e.g. an event {date: ..., title: PE cancelled, cancels: PE}).

        Without a `folder` it also lists the family's calendar events (source `calendar`; daily or
        weekly ones as kind `recurring`), merged into a note's item when both have the same title
        that day. Calendar events have no `who`, so a `who` filter leaves them out.

        For up to 7 days (and no `folder`) it also lists the chores from Home-Board (kind `chore`,
        with their `status`); the `chores` tool tells who has done what.
        """
        with _tool_errors():
            first = _parse_day(start)
            folder_rel = vault.folder_path(folder)[0]
        last = first + dt.timedelta(days=days - 1)
        notes = [Note(path, title, meta) for path, title, meta in index.frontmatter(folder_rel)]
        extra, warnings = [], []
        if calendars is not None and not folder_rel:
            extra, warnings = calendars.items(first, last)
        if days <= CHORE_DAYS and not folder_rel:
            chores, warning = chore_items(first, last)
            extra += chores
            warnings += [warning] if warning else []
        result = build_agenda(notes, first, last, today(), who, extra)
        if warnings:
            result["warnings"] = warnings
        return result

    @mcp.tool(annotations=EDIT)
    def remind(
        at: Annotated[
            str,
            Field(
                description='From now: "+15m", "+2h", "+3d" (always use this for "in 20 minutes" etc.). '
                'Or a clock time: "YYYY-MM-DDTHH:MM" (local time), or just "YYYY-MM-DD".'
            ),
        ],
        text: Annotated[str, Field(description="The reminder, written so it makes sense on its own.")],
        who: Annotated[str | None, Field(description="Person it concerns (note slug, e.g. 'ola').")] = None,
        note: Annotated[str, Field(description="Note to store it in; reminders.md unless it belongs with an event note.")] = REMINDERS,
    ) -> dict[str, Any]:
        """Save a one-off reminder ("remind me to buy a present on Thursday at 18:00"). It is stored
        under `reminders` in the note and shows up in `agenda` on its day. When push notifications
        are set up, it is also sent to the phone at that time (at 08:00 if it has no time).

        For a time relative to now ("in 20 minutes", "in 2 hours") always pass at="+20m" / "+2h":
        you may not know the exact current time, the server does. The result has the time it was
        set for and the server's current time (`now`); tell the user the time from the result.
        """
        with _tool_errors():
            if match := _FROM_NOW.match(at.strip().lower()):
                at = (now() + dt.timedelta(**{_UNITS[match.group(2)]: int(match.group(1))})).strftime("%Y-%m-%dT%H:%M")
            moment = as_moment(at)
            if moment is None or (len(at.strip()) > 10 and moment[1] is None):
                raise VaultError('at must be "YYYY-MM-DDTHH:MM", "YYYY-MM-DD" or like "+15m"')
            day, clock = moment
            if day < today() or (clock and dt.datetime.combine(day, dt.time.fromisoformat(clock)) < now()):
                raise VaultError(f"{at} is in the past (it is now {now():%Y-%m-%dT%H:%M})")
            if not text.strip():
                raise VaultError("text is empty")
            reminder: dict[str, Any] = {
                "id": f"r-{secrets.token_hex(4)}",
                "at": f"{day.isoformat()}T{clock}" if clock else day.isoformat(),
                "text": text.strip(),
            }
            if who:
                reminder["who"] = who.strip().lstrip("@").lower()
            reminder["created"] = today().isoformat()
            rel, full = vault.note_path(note)
            if full.exists():
                vault.patch(rel, append_to_list={"reminders": [reminder]})
            else:
                meta = CommentedMap(type="reminders", reminders=frontmatter.from_json([reminder]))
                vault.write(rel, frontmatter.render(meta, REMINDERS_BODY))
        index.refresh(force=True)
        return {"path": rel, "reminder": reminder, "now": f"{now():%Y-%m-%dT%H:%M}"}

    if notifier is not None:

        @mcp.tool(annotations=SEND)
        def notify(
            text: Annotated[str, Field(description="The message, written so it makes sense on its own.")],
            title: Annotated[str | None, Field(description="Short title shown above the message.")] = None,
            priority: Annotated[
                Priority,
                Field(description="'high' and 'urgent' pop up and vibrate longer; use them only when asked."),
            ] = "default",
        ) -> dict[str, Any]:
            """Send a push notification to the user's phone right now, e.g. "send the shopping list to
            my phone". For something at a later time, use `remind` instead.
            """
            if not text.strip():
                raise ToolError("text is empty")
            try:
                notifier.send(Message(text.strip(), title=(title or "").strip() or None, priority=priority))
            except NotifyError as exc:
                log.warning("notify failed: %s", exc)
                raise ToolError("the notification could not be sent; the ntfy server is not reachable or refused it") from exc
            return {"sent": True}

    if homeboard is not None:

        @mcp.tool(annotations=READ_ONLY)
        def chores(
            day: Annotated[str, Field(description="'today', 'yesterday', 'tomorrow' or a date like 2026-10-21.")] = "today",
            who: Annotated[str | None, Field(description="Only this person: name or note slug, e.g. 'ola'.")] = None,
            days: Annotated[int, Field(ge=1, le=31, description="How many days from `day`.")] = 1,
        ) -> dict[str, Any]:
            """The chores from Home-Board, the family's chore and points app: per person what is done
            and what is still to do, plus the chores the kids share. Use it for "has Ola done her
            chores today?", "which chores are left?" or "what did the kids do yesterday?".

            `status` is todo, done (waiting for a parent to check it), verified or rejected. `late`
            marks a chore past its due time that isn't done; `any_day` chores can be done on any day
            this week or month.
            """
            with _tool_errors():
                first = _parse_day(day)
            try:
                found = homeboard.chores(first, first + dt.timedelta(days=days - 1))
            except HomeBoardError as exc:
                log.warning("chores failed: %s", exc)
                raise ToolError("Home-Board could not be reached, so the chores are unknown right now") from exc
            return chore_report(found, now(), who)

        @mcp.tool(annotations=READ_ONLY)
        def scoreboard(
            who: Annotated[str | None, Field(description="Only this child: name or note slug, e.g. 'ola'.")] = None,
        ) -> dict[str, Any]:
            """Points from Home-Board per child: the scoreboard total, points earned this week and this
            month, the next payout (`money_to_pay`, in the family's currency at `point_to_money_rate`)
            and done chores still waiting for a parent's approval (their points count once approved).
            Use it for "how many points does Ola have?", "how much allowance do I owe the kids?" or
            "which chores do I need to approve?". Each waiting chore has the `id` that
            `approve_chores` takes, when that tool is available. Payouts happen in Home-Board itself.
            """
            try:
                raw = homeboard.points()
            except HomeBoardError as exc:
                log.warning("scoreboard failed: %s", exc)
                raise ToolError("Home-Board could not be reached, so the points are unknown right now") from exc
            return points_report(raw, who)

    if homeboard is not None and homeboard.can_write:

        @mcp.tool(annotations=EDIT)
        def approve_chores(
            ids: Annotated[
                list[str],
                Field(min_length=1, max_length=50, description="The `id` of each done chore, from `scoreboard` (waiting_for_approval) or `chores`."),
            ],
            points: Annotated[
                int | None,
                Field(ge=0, le=1000, description="Points to give instead of the chore's own; only when approving a single chore."),
            ] = None,
        ) -> dict[str, Any]:
            """Approve done chores in Home-Board, as a parent would in the app: the child who did
            each one gets its points. Use it when the user asks, e.g. "approve Ola's chores" or
            "approve everything that's waiting". Call `scoreboard` first for the ids, approve only
            the chores the user meant, and tell them what was approved and the points given.
            """
            if points is not None and len(ids) != 1:
                raise ToolError("points can only be given when approving a single chore")
            approved, failed = [], []
            for chore_id in ids:
                try:
                    done = homeboard.approve(chore_id, points)
                except HomeBoardError as exc:
                    log.warning("approving %s failed: %s", chore_id, exc)
                    failed.append({"id": chore_id, "error": str(exc) if isinstance(exc, HomeBoardRefused) else "Home-Board could not be reached"})
                else:
                    approved.append({"title": done.get("title"), "who": done.get("completedBy"), "points": done.get("pointsAwarded", 0)})
            result: dict[str, Any] = {"approved": approved}
            if failed:
                result["failed"] = failed
            return result

        @mcp.tool(annotations=DELETE)
        def reject_chore(
            id: Annotated[str, Field(description="The `id` of the done chore, from `scoreboard` or `chores`.")],
            reason: Annotated[str | None, Field(description="Why, shown to the child, e.g. 'The bed isn't made'.")] = None,
        ) -> dict[str, Any]:
            """Reject a chore a child marked as done that wasn't really done: no points, and it
            can't be approved afterwards. Only when the user clearly asks for it.
            """
            try:
                done = homeboard.reject(id, (reason or "").strip() or None)
            except HomeBoardRefused as exc:
                raise ToolError(str(exc)) from exc
            except HomeBoardError as exc:
                log.warning("rejecting %s failed: %s", id, exc)
                raise ToolError("Home-Board could not be reached, so nothing was rejected") from exc
            return {"rejected": {"title": done.get("title"), "who": done.get("completedBy")}}

        @mcp.tool(annotations=EDIT)
        def award_points(
            who: Annotated[str, Field(description="The child: name or note slug, e.g. 'ola'.")],
            points: Annotated[int, Field(ge=1, le=1000, description="Bonus points to add.")],
            reason: Annotated[str, Field(min_length=1, max_length=200, description="What for, shown in the child's points history, e.g. 'Carried the shopping'.")],
        ) -> dict[str, Any]:
            """Give a child bonus points in Home-Board, e.g. "give Ola 20 points for helping with
            dinner". They count towards the scoreboard and the next payout like chore points. Only
            when the user asks for it; for done chores use `approve_chores` instead.
            """
            try:
                child = find_child(homeboard.points(), who)
                done = homeboard.award(str(child.get("userId")), points, reason.strip())
            except HomeBoardRefused as exc:
                raise ToolError(str(exc)) from exc
            except HomeBoardError as exc:
                log.warning("award_points failed: %s", exc)
                raise ToolError("Home-Board could not be reached, so no points were given") from exc
            return {"awarded": {"name": done.get("name"), "points": done.get("pointsAwarded"), "reason": reason.strip()}}

    if run_scheduler and notifier is not None:
        Scheduler(
            vault, index, notifier, settings.data_path / "scheduler.db", settings.briefing_time,
            homeboard=homeboard, calendars=calendars, texts=LANGUAGES[settings.notify_language],
        ).start()

    @mcp.custom_route("/health", methods=["GET"])
    async def health(_: Request) -> PlainTextResponse:
        return PlainTextResponse("ok")

    return mcp
