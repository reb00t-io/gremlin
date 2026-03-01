from __future__ import annotations

from pathlib import Path

from bug_generation import (
    fix_patch_files_for_source,
    fix_patch_path_for_source,
    patch_files_for_source,
    patch_path_for_source,
)


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
