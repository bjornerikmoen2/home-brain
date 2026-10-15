"""Runtime settings, read from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    vault_path: Path
    """Root folder of the markdown vault."""

    data_path: Path
    """Folder for the search index and other state. Must be a local disk, not NFS (SQLite locking)."""

    host: str = "127.0.0.1"
    port: int = 8000

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            vault_path=Path(os.environ.get("VAULT_PATH", "vault")),
            data_path=Path(os.environ.get("DATA_PATH", "data")),
            host=os.environ.get("HOST", "127.0.0.1"),
            port=int(os.environ.get("PORT", "8000")),
        )
