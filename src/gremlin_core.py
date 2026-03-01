from __future__ import annotations

import json
import shlex
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from bug_generation import (
    append_run_log,
    fix_patch_path_for_source,
    generate_bug_patches_for_file,
    patch_number_from_bug_patch,
    patch_files_for_source,
    revert_source_file,
)


@dataclass
class CmdResult:
    returncode: int
    stdout: str
    stderr: str


def run_cmd(cmd: list[str], cwd: Path, check: bool = False) -> CmdResult:
    proc = subprocess.run(
        cmd,
        cwd=str(cwd),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )
    result = CmdResult(proc.returncode, proc.stdout, proc.stderr)
    if check and proc.returncode != 0:
        raise RuntimeError(
            f"Command failed ({proc.returncode}): {shlex.join(cmd)}\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )
    return result


def git_tracked_files(repo_root: Path) -> list[Path]:
    result = run_cmd(["git", "ls-files", "-z"], cwd=repo_root, check=True)
    paths = result.stdout.split("\x00")
    return sorted(Path(p) for p in paths if p)


def is_source_candidate(file_path: Path, repo_root: Path) -> bool:
    if file_path.name.endswith(".patch"):
        return False
    if file_path.stem.endswith("_test") or file_path.stem.startswith("test_"):
        return False

    test_file = file_path.with_name(f"{file_path.stem}_test{file_path.suffix}")
    return (repo_root / test_file).is_file()


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


def verify_patch(
    source_file: Path,
    bug_patch_path: Path,
    test_patch_path: Path,
    repo_root: Path,
    results_file: Path,
    dry_run: bool,
) -> None:
    test_file = test_file_for_source(source_file)
    test_cmd = test_command_for_source(source_file, test_file)

    record: dict = {
        "timestamp": datetime.now(UTC).isoformat(),
        "file": source_file.as_posix(),
        "bug_patch": bug_patch_path.relative_to(repo_root).as_posix(),
        "test_patch": test_patch_path.relative_to(repo_root).as_posix(),
        "test_file": test_file.as_posix(),
        "test_command": shlex.join(test_cmd),
    }

    if dry_run:
        record.update({"applied": False, "works": None, "note": "dry-run"})
        append_jsonl(results_file, record)
        return

    if not test_patch_path.is_file():
        record.update(
            {
                "applied": False,
                "works": False,
                "error": "missing_test_patch",
            }
        )
        append_jsonl(results_file, record)
        return

    pre_test_result = run_cmd(test_cmd, cwd=repo_root, check=False)
    record.update(
        {
            "precheck_exit_code": pre_test_result.returncode,
            "precheck_stdout_tail": pre_test_result.stdout[-2000:],
            "precheck_stderr_tail": pre_test_result.stderr[-2000:],
        }
    )
    if pre_test_result.returncode != 0:
        record.update(
            {
                "applied": False,
                "works": False,
                "error": "precheck_failed",
            }
        )
        append_jsonl(results_file, record)
        return

    apply_bug_result = run_cmd(["git", "apply", bug_patch_path.as_posix()], cwd=repo_root, check=False)
    if apply_bug_result.returncode != 0:
        record.update(
            {
                "applied": False,
                "works": False,
                "error": "bug_patch_apply_failed",
                "stderr": apply_bug_result.stderr,
            }
        )
        append_jsonl(results_file, record)
        return

    apply_test_result = None
    try:
        failing_test_result = run_cmd(test_cmd, cwd=repo_root, check=False)
        bug_causes_failure = failing_test_result.returncode != 0 and failing_test_result.returncode != 5
        record.update(
            {
                "bug_applied": True,
                "bug_test_exit_code": failing_test_result.returncode,
                "bug_test_stdout_tail": failing_test_result.stdout[-2000:],
                "bug_test_stderr_tail": failing_test_result.stderr[-2000:],
                "bug_causes_failure": bug_causes_failure,
            }
        )

        if not bug_causes_failure:
            record.update(
                {
                    "applied": True,
                    "works": False,
                    "error": "bug_patch_did_not_fail_test",
                }
            )
            append_jsonl(results_file, record)
            return

        apply_test_result = run_cmd(["git", "apply", test_patch_path.as_posix()], cwd=repo_root, check=False)
        if apply_test_result.returncode != 0:
            record.update(
                {
                    "applied": True,
                    "works": False,
                    "error": "test_patch_apply_failed",
                    "test_patch_stderr": apply_test_result.stderr,
                }
            )
            append_jsonl(results_file, record)
            return

        all_tests_result = run_cmd(["pytest"], cwd=repo_root, check=False)
        works = all_tests_result.returncode == 0
        record.update(
            {
                "applied": True,
                "works": works,
                "all_tests_exit_code": all_tests_result.returncode,
                "all_tests_stdout_tail": all_tests_result.stdout[-2000:],
                "all_tests_stderr_tail": all_tests_result.stderr[-2000:],
            }
        )
        append_jsonl(results_file, record)
    finally:
        if apply_test_result is not None and apply_test_result.returncode == 0:
            revert_test_result = run_cmd(
                ["git", "apply", "-R", test_patch_path.as_posix()],
                cwd=repo_root,
                check=False,
            )
            if revert_test_result.returncode != 0:
                revert_source_file(test_file, repo_root, run_cmd=run_cmd)

        revert_bug_result = run_cmd(["git", "apply", "-R", bug_patch_path.as_posix()], cwd=repo_root, check=False)
        if revert_bug_result.returncode != 0:
            revert_source_file(source_file, repo_root, run_cmd=run_cmd)
            revert_source_file(test_file, repo_root, run_cmd=run_cmd)


def process_source_file(
    source_file: Path,
    repo_root: Path,
    steps_per_file: int,
    dry_run: bool,
    results_file: Path,
    run_log_path: Path,
) -> None:
    print(f"\n[generate] {source_file.as_posix()}")
    generated = generate_bug_patches_for_file(
        source_file=source_file,
        repo_root=repo_root,
        steps_per_file=steps_per_file,
        dry_run=dry_run,
        run_cmd=run_cmd,
        log_path=run_log_path,
    )
    print(f"generated {len(generated)} patches")

    print(f"[verify] {source_file.as_posix()}")
    for bug_patch_path in patch_files_for_source(source_file, repo_root):
        patch_no = patch_number_from_bug_patch(bug_patch_path)
        test_patch_path = fix_patch_path_for_source(source_file, repo_root, patch_no)
        verify_patch(
            source_file=source_file,
            bug_patch_path=bug_patch_path,
            test_patch_path=test_patch_path,
            repo_root=repo_root,
            results_file=results_file,
            dry_run=dry_run,
        )


def run_generation_and_verification(
    repo_root: Path,
    max_files: int,
    steps_per_file: int,
    dry_run: bool,
    results_file: Path,
    run_log_path: Path,
) -> int:
    append_run_log(run_log_path, f"gremlin start repo_root={repo_root.as_posix()}")

    tracked_files = git_tracked_files(repo_root)
    candidates = [file for file in tracked_files if is_source_candidate(file, repo_root)]
    selected = candidates[:max_files]

    print(f"Found {len(candidates)} candidate files with adjacent _test files")
    print(f"Processing up to {len(selected)} files, {steps_per_file} steps per file")

    for source_file in selected:
        try:
            process_source_file(
                source_file=source_file,
                repo_root=repo_root,
                steps_per_file=steps_per_file,
                dry_run=dry_run,
                results_file=results_file,
                run_log_path=run_log_path,
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
        except KeyboardInterrupt:
            append_run_log(run_log_path, "interrupted by user (Ctrl-C)")
            print("Interrupted by user (Ctrl-C).", file=sys.stderr)
            print(f"Details logged to {run_log_path}", file=sys.stderr)
            return 130

    print(f"\nVerification results written to {results_file}")
    append_run_log(run_log_path, f"gremlin completed results_file={results_file.as_posix()}")
    print(f"Run log: {run_log_path}")
    return 0
