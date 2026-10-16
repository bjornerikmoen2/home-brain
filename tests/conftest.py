from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from home_brain.config import Settings
from home_brain.index import Index
from home_brain.vault import Vault


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    vault = Vault(tmp_path / "vault")
    vault.seed()
    return vault


@pytest.fixture
def index(vault: Vault, tmp_path: Path) -> Iterator[Index]:
    index = Index(vault, tmp_path / "data" / "index.db", min_refresh_interval=0)
    yield index
    index.close()


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    # git is off here to keep the server tests fast; test_gitsync covers versioning.
    settings = Settings(vault_path=tmp_path / "vault", data_path=tmp_path / "data", vault_git=False)
    Vault(settings.vault_path).seed()
    return settings
