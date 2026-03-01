from __future__ import annotations

import json
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any, Protocol

from bug_generation import fix_patch_path_for_source, overview_path_for_source, patch_number_from_bug_patch


class RunCmd(Protocol):
    def __call__(self, cmd: list[str], cwd: Path, check: bool = False) -> Any: ...


def list_bug_patches(repo_root: Path) -> list[Path]:
    patches_root = repo_root / ".gremlin" / "bugs"
    if not patches_root.is_dir():
        return []
    return sorted(patches_root.rglob("*.bug-*.patch"))


def source_file_for_patch(patch_path: Path, repo_root: Path) -> Path:
    rel = patch_path.relative_to(repo_root / ".gremlin" / "bugs")
    source_rel = re.sub(r"\.bug-\d+\.patch$", "", rel.as_posix())
    if source_rel == rel.as_posix():
        raise RuntimeError(f"Patch filename does not match expected pattern: {patch_path}")
    return Path(source_rel)


def path_for_record(path: Path, repo_root: Path) -> str:
    try:
        return path.relative_to(repo_root).as_posix()
    except ValueError:
        return path.as_posix()


def checkout_path(path: Path, repo_root: Path, run_cmd: RunCmd) -> None:
    run_cmd(["git", "checkout", "--", path.as_posix()], cwd=repo_root, check=False)


def cleanup_bug_report(repo_root: Path, run_cmd: RunCmd) -> None:
    bug_report = Path("bug_report.txt")
    run_cmd(["git", "checkout", "--", bug_report.as_posix()], cwd=repo_root, check=False)
    bug_report_abs = repo_root / bug_report
    if bug_report_abs.exists():
        bug_report_abs.unlink()


def load_patch_overview(source_repo_root: Path, bug_patch_path: Path) -> dict:
    source_file = source_file_for_patch(bug_patch_path, source_repo_root)
    patch_no = patch_number_from_bug_patch(bug_patch_path)
    overview_path = overview_path_for_source(source_file, source_repo_root, patch_no)
    if not overview_path.is_file():
        raise RuntimeError(f"missing_overview:{overview_path.relative_to(source_repo_root).as_posix()}")

    try:
        payload = json.loads(overview_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"invalid_overview_json:{overview_path.relative_to(source_repo_root).as_posix()}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"invalid_overview:{overview_path.relative_to(source_repo_root).as_posix()}")

    base_commit = payload.get("base_commit")
    if not isinstance(base_commit, str) or not base_commit.strip():
        raise RuntimeError(f"invalid_overview_base_commit:{overview_path.relative_to(source_repo_root).as_posix()}")

    payload["_overview_path"] = overview_path.relative_to(source_repo_root).as_posix()
    payload["base_commit"] = base_commit.strip()
    return payload


def prepare_temp_checkout(source_repo_root: Path, base_commit: str, run_cmd: RunCmd, case_id: str = "eval") -> Path:
    tmp_root = Path(tempfile.mkdtemp(prefix=f"gremlin-eval-case{case_id}-"))
    run_cmd(["git", "clone", "--quiet", source_repo_root.as_posix(), tmp_root.as_posix()], cwd=source_repo_root, check=True)
    run_cmd(["git", "checkout", "--quiet", base_commit], cwd=tmp_root, check=True)
    return tmp_root


def remove_checkout(checkout_root: Path) -> None:
    shutil.rmtree(checkout_root, ignore_errors=True)


def resolve_test_patch_path(
    *,
    overview: dict,
    source_patch_path: Path,
    source_file: Path,
    source_repo_root: Path,
) -> Path:
    test_patch_value = overview.get("test_patch")
    if isinstance(test_patch_value, str) and test_patch_value.strip():
        return source_repo_root / Path(test_patch_value)

    patch_no = patch_number_from_bug_patch(source_patch_path)
    return fix_patch_path_for_source(source_file, source_repo_root, patch_no)
