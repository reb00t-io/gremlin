#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from bug_generation import (
    append_run_log,
    generate_bug_patches_for_file,
    patch_files_for_source,
    revert_source_file,
)
from repo_root import discover_repo_root


@dataclass
class CmdResult:
    returncode: int
    stdout: str
    stderr: str


def run_cmd(cmd: list[str], cwd: Path, check: bool = False) -> CmdResult:
    proc = subprocess.run(
        cmd,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
    )
    result = CmdResult(proc.returncode, proc.stdout, proc.stderr)
    if check and proc.returncode != 0:
        raise RuntimeError(
            f"Command failed ({proc.returncode}): {shlex.join(cmd)}\n"
            f"stdout:\n{proc.stdout}\n"
            f"stderr:\n{proc.stderr}"
        )
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Generate bug patches with claude for files with adjacent _test files, "
            "then verify each patch by applying it and running tests."
        )
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=None,
        help=(
            "Repository root (default: auto-discover from current working "
            "directory)."
        ),
    )
    parser.add_argument(
        "--steps-per-file",
        type=int,
        default=1,
        help="How many bug patches to generate per file.",
    )
    parser.add_argument(
        "--max-files",
        type=int,
        default=10,
        help="Maximum number of files to process.",
    )
    parser.add_argument(
        "--results-file",
        type=Path,
        default=Path(".gremlin/verification_results.jsonl"),
        help="Path to append patch verification results.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List candidate files and planned actions without mutating the repository.",
    )
    return parser.parse_args()


def git_tracked_files(repo_root: Path) -> list[Path]:
    result = run_cmd(["git", "ls-files", "-z"], cwd=repo_root, check=True)
    paths = result.stdout.split("\x00")
    return sorted(Path(p) for p in paths if p)


def has_adjacent_test_file(file_path: Path, repo_root: Path) -> bool:
    test_file = file_path.with_name(f"{file_path.stem}_test{file_path.suffix}")
    return (repo_root / test_file).is_file()


def is_source_candidate(file_path: Path, repo_root: Path) -> bool:
    if file_path.name.endswith(".patch"):
        return False
    if file_path.stem.endswith("_test") or file_path.stem.startswith("test_"):
        return False
    return has_adjacent_test_file(file_path, repo_root)


def test_file_for_source(source_file: Path) -> Path:
    return source_file.with_name(f"{source_file.stem}_test{source_file.suffix}")


def test_command_for_source(source_file: Path, test_file: Path) -> list[str]:
    suffix = test_file.suffix.lower()
    if suffix == ".go":
        return ["go", "test", f"./{test_file.parent.as_posix()}"]
    return ["pytest", test_file.as_posix()]


def append_jsonl(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")


def build_run_log_path(repo_root: Path) -> Path:
    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return repo_root / ".gremlin" / "log" / f"{timestamp}-{os.getpid()}.log"


def verify_patch(
    source_file: Path,
    patch_path: Path,
    repo_root: Path,
    results_file: Path,
    dry_run: bool,
) -> None:
    test_file = test_file_for_source(source_file)
    test_cmd = test_command_for_source(source_file, test_file)

    record: dict = {
        "timestamp": datetime.now(UTC).isoformat(),
        "file": source_file.as_posix(),
        "patch": patch_path.relative_to(repo_root).as_posix(),
        "test_file": test_file.as_posix(),
        "test_command": shlex.join(test_cmd),
    }

    if dry_run:
        record.update({"applied": False, "works": None, "note": "dry-run"})
        append_jsonl(results_file, record)
        return

    apply_result = run_cmd(["git", "apply", patch_path.as_posix()], cwd=repo_root, check=False)
    if apply_result.returncode != 0:
        record.update(
            {
                "applied": False,
                "works": False,
                "error": "patch_apply_failed",
                "stderr": apply_result.stderr,
            }
        )
        append_jsonl(results_file, record)
        return

    test_result = run_cmd(test_cmd, cwd=repo_root, check=False)
    # A bug patch "works" if it causes real test failures (nonzero exit).
    # pytest exit code 5 means "no tests were collected" – that's not a real
    # failure caused by the patch, so treat it the same as a pass (works=False).
    works = test_result.returncode != 0 and test_result.returncode != 5
    record.update(
        {
            "applied": True,
            "works": works,
            "test_exit_code": test_result.returncode,
            "stdout_tail": test_result.stdout[-2000:],
            "stderr_tail": test_result.stderr[-2000:],
        }
    )
    append_jsonl(results_file, record)

    revert_result = run_cmd(["git", "apply", "-R", patch_path.as_posix()], cwd=repo_root, check=False)
    if revert_result.returncode != 0:
        revert_source_file(source_file, repo_root, run_cmd=run_cmd)


def main() -> int:
    args = parse_args()
    repo_root = args.repo_root.resolve() if args.repo_root else discover_repo_root(Path.cwd())
    results_file = args.results_file if args.results_file.is_absolute() else repo_root / args.results_file
    run_log_path = build_run_log_path(repo_root)
    append_run_log(run_log_path, f"gremlin start repo_root={repo_root.as_posix()}")

    tracked_files = git_tracked_files(repo_root)
    candidates = [file for file in tracked_files if is_source_candidate(file, repo_root)]
    selected = candidates[: args.max_files]

    print(f"Found {len(candidates)} candidate files with adjacent _test files")
    print(f"Processing up to {len(selected)} files, {args.steps_per_file} steps per file")

    for source_file in selected:
        try:
            print(f"\n[generate] {source_file.as_posix()}")
            generated = generate_bug_patches_for_file(
                source_file=source_file,
                repo_root=repo_root,
                steps_per_file=args.steps_per_file,
                dry_run=args.dry_run,
                run_cmd=run_cmd,
                log_path=run_log_path,
            )
            print(f"generated {len(generated)} patches")

            print(f"[verify] {source_file.as_posix()}")
            for patch_path in patch_files_for_source(source_file, repo_root):
                verify_patch(
                    source_file=source_file,
                    patch_path=patch_path,
                    repo_root=repo_root,
                    results_file=results_file,
                    dry_run=args.dry_run,
                )
        except RuntimeError as err:
            append_run_log(
                run_log_path,
                f"error while processing {source_file.as_posix()}: {err}",
            )
            print(f"Error while processing {source_file.as_posix()}: {err}", file=sys.stderr)
            if "pre-existing non-patch changes" in str(err):
                print(
                    "Tip: commit/stash/revert local changes in that target file, "
                    "or run with --dry-run.",
                    file=sys.stderr,
                )
            print(f"Details logged to {run_log_path}", file=sys.stderr)
            return 1

    print(f"\nVerification results written to {results_file}")
    append_run_log(run_log_path, f"gremlin completed results_file={results_file.as_posix()}")
    print(f"Run log: {run_log_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
