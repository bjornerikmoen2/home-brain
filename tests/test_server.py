from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from home_brain.config import Settings
from home_brain.server import build_instructions, create_server
from home_brain.vault import Vault

TOOLS = {"search_notes", "read_note", "list_notes", "write_note", "patch_note", "move_note", "delete_note"}


@pytest.fixture
async def client(settings: Settings) -> AsyncIterator[Client]:
    async with Client(create_server(settings)) as client:
        yield client


async def call(client: Client, tool: str, **arguments) -> dict:
    result = await client.call_tool(tool, arguments)
    return result.structured_content


async def test_lists_tools_with_annotations(client: Client) -> None:
    tools = {t.name: t for t in await client.list_tools()}
    assert set(tools) == TOOLS
    assert tools["search_notes"].annotations.read_only_hint is True
    assert tools["delete_note"].annotations.destructive_hint is True
    assert tools["write_note"].annotations.destructive_hint is False


def test_instructions_include_conventions(settings: Settings) -> None:
    instructions = build_instructions(Vault(settings.vault_path))
    assert "long-term memory" in instructions
    assert "# Vault conventions" in instructions


async def test_save_school_week_then_find_it(client: Client) -> None:
    content = (
        "---\ntype: school-week\nwho: ola\nweek: 2026-W44\nevents:\n"
        "  - { date: 2026-10-28, title: Museum visit, bring: [packed lunch, bus card] }\n---\n\n"
        "# Ola – week 44\n"
    )
    saved = await call(client, "write_note", path="school/ola/2026-w44", content=content)
    assert saved == {"path": "school/ola/2026-w44.md", "created": True}

    found = await call(client, "search_notes", query="museum")
    assert found["results"][0]["path"] == "school/ola/2026-w44.md"
    assert "**Museum**" in found["results"][0]["snippet"]

    note = await call(client, "read_note", path="school/ola/2026-w44.md")
    assert note["content"] == content


async def test_patch_note_tool(client: Client) -> None:
    result = await call(
        client,
        "patch_note",
        path="people/kari.md",
        set_frontmatter={"sizes": {"shoes": 40}},
        append_text="- Silver earrings (seen in town 2026-10-15)",
        under_heading="Gift ideas",
    )
    assert result["frontmatter"]["sizes"] == {"clothes": "M", "shoes": 40, "ring": 54}
    found = await call(client, "search_notes", query="earrings")
    assert found["results"][0]["path"] == "people/kari.md"


async def test_move_and_delete_tools(client: Client) -> None:
    await call(client, "write_note", path="inbox/quote.md", content="# Dekktilbud\n\nVinterdekk: 8 000 kr\n")
    moved = await call(client, "move_note", path="inbox/quote.md", new_path="vehicles/tire-quote.md")
    assert moved == {"from": "inbox/quote.md", "to": "vehicles/tire-quote.md"}
    listed = await call(client, "list_notes", folder="vehicles")
    assert [n["path"] for n in listed["notes"]] == ["vehicles/family-car.md", "vehicles/tire-quote.md"]

    deleted = await call(client, "delete_note", path="vehicles/tire-quote.md")
    assert deleted["trashed_to"].endswith("/vehicles/tire-quote.md")
    assert (await call(client, "search_notes", query="dekktilbud"))["results"] == []


async def test_empty_search_gives_a_hint(client: Client) -> None:
    result = await call(client, "search_notes", query="xylophone")
    assert result["results"] == []
    assert "hint" in result


@pytest.mark.parametrize(
    ("tool", "arguments", "message"),
    [
        ("read_note", {"path": "people/nobody.md"}, "note not found"),
        ("read_note", {"path": "../secrets.md"}, "'..' is not allowed"),
        ("write_note", {"path": "people/kari.md", "content": "x"}, "already exists"),
        ("patch_note", {"path": "people/kari.md"}, "nothing to change"),
        ("list_notes", {"folder": ".git"}, "hidden"),
    ],
)
async def test_errors_reach_the_model(client: Client, tool: str, arguments: dict, message: str) -> None:
    with pytest.raises(ToolError, match=message):
        await client.call_tool(tool, arguments)
