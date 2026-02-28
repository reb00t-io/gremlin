from __future__ import annotations

from pathlib import Path

import pytest

from repo_root import discover_repo_root


def test_discover_repo_root_walks_up(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    nested = repo_root / "a" / "b" / "c"
    nested.mkdir(parents=True)
    (repo_root / ".git").mkdir()

    found = discover_repo_root(nested)
    assert found == repo_root


def test_discover_repo_root_raises_when_missing(tmp_path: Path) -> None:
    start = tmp_path / "no-repo"
    start.mkdir(parents=True)

    with pytest.raises(RuntimeError, match="Could not find git repository root"):
        discover_repo_root(start)
