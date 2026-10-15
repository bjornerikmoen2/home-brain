"""The MCP server: vault tools for Claude."""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated, Any

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from pydantic import Field
from starlette.requests import Request
from starlette.responses import PlainTextResponse

from . import __version__
from .config import Settings
from .index import Index
from .vault import Vault, VaultError

log = logging.getLogger(__name__)

CONVENTIONS = "_conventions.md"

INTRO = """\
home-brain is this family's long-term memory: markdown notes about the people, the kids' school,
cars, house and events. Search it before answering questions about the family, and save new facts
the user shares. Prefer updating an existing note (patch_note) over creating a duplicate.
The vault's filing conventions follow; read them again with read_note('_conventions.md') if needed.
"""

READ_ONLY = {"readOnlyHint": True, "openWorldHint": False}
EDIT = {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False}
DELETE = {"readOnlyHint": False, "destructiveHint": True, "openWorldHint": False}


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


def create_server(settings: Settings) -> FastMCP:
    vault = Vault(settings.vault_path)
    index = Index(vault, settings.data_path / "index.db")
    stats = index.refresh(force=True)
    log.info("vault %s: %d notes indexed (%s)", vault.root, index.count(), stats)

    mcp = FastMCP(
        name="home-brain",
        instructions=build_instructions(vault),
        version=__version__,
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
        """
        with _tool_errors():
            folder_rel = vault.folder_path(folder)[0]
            results = index.search(query, folder_rel, note_type, limit)
        response: dict[str, Any] = {"query": query, "results": results}
        if not results:
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
    def list_notes(
        folder: Annotated[str, Field(description="Folder to list; empty for the whole vault.")] = "",
        recursive: Annotated[bool, Field(description="Include notes in subfolders.")] = True,
        note_type: Annotated[str | None, Field(description="Only notes whose frontmatter `type` equals this.")] = None,
    ) -> dict[str, Any]:
        """List notes (path, title, type, modified) under a folder, plus its subfolders."""
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
        return {"path": rel, "created": created}

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
        return {"path": rel, "frontmatter": meta}

    @mcp.tool(annotations=EDIT)
    def move_note(
        path: Annotated[str, Field(description="Current vault-relative path.")],
        new_path: Annotated[str, Field(description="New vault-relative path; must not exist yet.")],
    ) -> dict[str, Any]:
        """Rename a note or file it into another folder (e.g. out of inbox/)."""
        with _tool_errors():
            old, new = vault.move(path, new_path)
        index.refresh(force=True)
        return {"from": old, "to": new}

    @mcp.tool(annotations=DELETE)
    def delete_note(
        path: Annotated[str, Field(description="Vault-relative path of the note to delete.")],
    ) -> dict[str, Any]:
        """Delete a note. It is moved to the vault's .trash folder, so it can be recovered by hand."""
        with _tool_errors():
            rel, trashed = vault.delete(path)
        index.refresh(force=True)
        return {"path": rel, "trashed_to": trashed}

    @mcp.custom_route("/health", methods=["GET"])
    async def health(_: Request) -> PlainTextResponse:
        return PlainTextResponse("ok")

    return mcp
