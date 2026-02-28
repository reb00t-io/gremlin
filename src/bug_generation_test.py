from __future__ import annotations

from pathlib import Path

from bug_generation import patch_files_for_source, patch_path_for_source


def test_patch_paths_are_stored_under_gremlin_bugs_with_source_structure(tmp_path: Path) -> None:
    repo_root = tmp_path
    source_file = Path("src/pkg/module.py")

    patch1 = patch_path_for_source(source_file, repo_root, 1)
    patch2 = patch_path_for_source(source_file, repo_root, 2)
    patch1.parent.mkdir(parents=True, exist_ok=True)
    patch1.write_text("diff1\n", encoding="utf-8")
    patch2.write_text("diff2\n", encoding="utf-8")

    assert patch1 == repo_root / ".gremlin" / "bugs" / "src" / "pkg" / "module.py.bug-1.patch"
    assert patch2 == repo_root / ".gremlin" / "bugs" / "src" / "pkg" / "module.py.bug-2.patch"

    patches = patch_files_for_source(source_file, repo_root)
    assert patches == [patch1, patch2]
