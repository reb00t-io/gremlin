from __future__ import annotations

import subprocess
from pathlib import Path

from bug_generation import (
    create_patch_for_source,
    create_patch_for_test,
    fix_patch_files_for_source,
    fix_patch_path_for_source,
    patch_files_for_source,
    patch_path_for_source,
)
from gremlin_core import run_cmd


def test_patch_paths_are_stored_under_gremlin_bugs_with_source_structure(tmp_path: Path) -> None:
    repo_root = tmp_path
    source_file = Path("src/pkg/module.py")

    patch1 = patch_path_for_source(source_file, repo_root, 1)
    patch2 = patch_path_for_source(source_file, repo_root, 2)
    patch1.parent.mkdir(parents=True, exist_ok=True)
    patch1.write_text("diff1\n", encoding="utf-8")
    patch2.write_text("diff2\n", encoding="utf-8")

    test_patch1 = fix_patch_path_for_source(source_file, repo_root, 1)
    test_patch2 = fix_patch_path_for_source(source_file, repo_root, 2)
    test_patch1.write_text("testdiff1\n", encoding="utf-8")
    test_patch2.write_text("testdiff2\n", encoding="utf-8")

    assert patch1 == repo_root / ".gremlin" / "bugs" / "src" / "pkg" / "module.py.bug-1.patch"
    assert patch2 == repo_root / ".gremlin" / "bugs" / "src" / "pkg" / "module.py.bug-2.patch"
    assert test_patch1 == repo_root / ".gremlin" / "bugs" / "src" / "pkg" / "module.py.test-1.patch"
    assert test_patch2 == repo_root / ".gremlin" / "bugs" / "src" / "pkg" / "module.py.test-2.patch"

    patches = patch_files_for_source(source_file, repo_root)
    assert patches == [patch1, patch2]
    test_patches = fix_patch_files_for_source(source_file, repo_root)
    assert test_patches == [test_patch1, test_patch2]


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def test_bug_report_is_only_in_test_patch(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir(parents=True)

    source_file = Path("src/module.py")
    test_file = Path("src/module_test.py")
    report_file = Path("bug_report.txt")

    (repo_root / source_file).parent.mkdir(parents=True, exist_ok=True)
    (repo_root / source_file).write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (repo_root / test_file).write_text("from src.module import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n", encoding="utf-8")

    _git(repo_root, "init")
    _git(repo_root, "config", "user.email", "test@example.com")
    _git(repo_root, "config", "user.name", "Test User")
    _git(repo_root, "add", ".")
    _git(repo_root, "commit", "-m", "init")

    bug_patch_path = patch_path_for_source(source_file, repo_root, 1)
    test_patch_path = fix_patch_path_for_source(source_file, repo_root, 1)

    (repo_root / source_file).write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    created_bug = create_patch_for_source(source_file, bug_patch_path, repo_root, run_cmd=run_cmd)
    assert created_bug
    _git(repo_root, "checkout", "--", source_file.as_posix())

    (repo_root / test_file).write_text("from src.module import add\n\n\ndef test_add():\n    assert add(1, 2) == -1\n", encoding="utf-8")
    (repo_root / report_file).write_text(
        "Observed behavior: arithmetic results are incorrect for simple inputs.\n"
        "Impact: users may see wrong calculations in normal flows.\n"
        "Repro: call add(1, 2) and compare against expected output.\n",
        encoding="utf-8",
    )
    created_test = create_patch_for_test(
        test_file=test_file,
        report_file=report_file,
        patch_path=test_patch_path,
        repo_root=repo_root,
        run_cmd=run_cmd,
    )
    assert created_test

    bug_patch = bug_patch_path.read_text(encoding="utf-8")
    test_patch = test_patch_path.read_text(encoding="utf-8")

    assert "bug_report.txt" not in bug_patch
    assert "bug_report.txt" in test_patch
