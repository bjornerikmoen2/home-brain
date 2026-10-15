"""Command line: `home-brain serve | init | reindex`."""

from __future__ import annotations

import argparse
import logging
import sys

from .config import Settings
from .index import Index
from .vault import Vault

log = logging.getLogger("home_brain")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="home-brain", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    serve = commands.add_parser("serve", help="run the MCP server")
    serve.add_argument("--transport", choices=["http", "stdio"], default="http")
    serve.add_argument("--host", help="HTTP bind address (default: $HOST or 127.0.0.1)")
    serve.add_argument("--port", type=int, help="HTTP port (default: $PORT or 8000)")

    commands.add_parser("init", help="copy the starter vault into VAULT_PATH (never overwrites)")
    commands.add_parser("reindex", help="rebuild the search index from scratch")

    args = parser.parse_args(argv)
    # stdout belongs to the MCP protocol in stdio mode, so always log to stderr.
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = Settings.from_env()

    if args.command == "init":
        copied = Vault(settings.vault_path).seed()
        print(f"copied {len(copied)} starter files into {settings.vault_path.resolve()}", file=sys.stderr)
    elif args.command == "reindex":
        index = Index(Vault(settings.vault_path), settings.data_path / "index.db")
        print(f"reindexed: {index.rebuild()}", file=sys.stderr)
    else:
        _serve(settings, args)


def _serve(settings: Settings, args: argparse.Namespace) -> None:
    from .server import create_server

    vault = Vault(settings.vault_path)
    if vault.is_empty():
        copied = vault.seed()
        log.info("empty vault: copied %d starter files into %s", len(copied), vault.root)

    mcp = create_server(settings)
    if args.transport == "stdio":
        mcp.run(transport="stdio", show_banner=False)
    else:
        mcp.run(
            transport="http",
            host=args.host or settings.host,
            port=args.port or settings.port,
            show_banner=False,
        )


if __name__ == "__main__":
    main()
