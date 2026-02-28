from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from bug_generation import ensure_clean_worktree
from gremlin import is_source_candidate, run_cmd, test_command_for_source as build_test_command


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _create_repo_with_files(tmp_path: Path) -> tuple[Path, Path, Path]:
    repo_root = tmp_path / "repo"
    repo_root.mkdir(parents=True)

    source_file = repo_root / "src" / "module.py"
    other_file = repo_root / "README.md"
    source_file.parent.mkdir(parents=True)
    source_file.write_text("print('ok')\n", encoding="utf-8")
    other_file.write_text("initial\n", encoding="utf-8")

    _git(repo_root, "init")
    _git(repo_root, "config", "user.email", "test@example.com")
    _git(repo_root, "config", "user.name", "Test User")
    _git(repo_root, "add", ".")
    _git(repo_root, "commit", "-m", "init")

    return repo_root, source_file, other_file


def test_ensure_clean_worktree_allows_unrelated_dirty_files(tmp_path: Path) -> None:
    repo_root, source_file, other_file = _create_repo_with_files(tmp_path)
    other_file.write_text("changed\n", encoding="utf-8")

    ensure_clean_worktree(repo_root, source_file.relative_to(repo_root), run_cmd=run_cmd)


def test_ensure_clean_worktree_rejects_dirty_target_file(tmp_path: Path) -> None:
    repo_root, source_file, _ = _create_repo_with_files(tmp_path)
    source_file.write_text("print('changed')\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="Target file has pre-existing non-patch changes"):
        ensure_clean_worktree(repo_root, source_file.relative_to(repo_root), run_cmd=run_cmd)


def test_is_source_candidate_allows_non_py_go_extensions(tmp_path: Path) -> None:
    repo_root = tmp_path
    source = Path("src/widget.js")
    test_file = repo_root / "src" / "widget_test.js"
    test_file.parent.mkdir(parents=True)
    test_file.write_text("// test\n", encoding="utf-8")

    assert is_source_candidate(source, repo_root)


def test_test_command_for_unknown_extension_defaults_to_pytest() -> None:
    source_file = Path("src/widget.js")
    test_file = Path("src/widget_test.js")
    assert build_test_command(source_file, test_file) == ["pytest", "src/widget_test.js"]
