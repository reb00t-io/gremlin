from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol


class CmdResultLike(Protocol):
    returncode: int
    stdout: str
    stderr: str


class RunCmd(Protocol):
    def __call__(self, cmd: list[str], cwd: Path, check: bool = False) -> CmdResultLike: ...


def append_run_log(log_path: Path | None, message: str) -> None:
    if log_path is None:
        return
    log_path.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).isoformat()
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(f"[{timestamp}] {message}\n")


def patch_dir_for_source(source_file: Path, repo_root: Path) -> Path:
    return repo_root / ".gremlin" / "bugs" / source_file.parent


def patch_path_for_source(source_file: Path, repo_root: Path, patch_no: int) -> Path:
    return patch_dir_for_source(source_file, repo_root) / f"{source_file.name}.bug-{patch_no}.patch"


def patch_files_for_source(source_file: Path, repo_root: Path) -> list[Path]:
    pattern = f"{source_file.name}.bug-*.patch"
    return sorted(patch_dir_for_source(source_file, repo_root).glob(pattern))


def next_patch_number(source_file: Path, repo_root: Path) -> int:
    highest = 0
    for patch in patch_files_for_source(source_file, repo_root):
        match = re.search(r"\.bug-(\d+)\.patch$", patch.name)
        if not match:
            continue
        highest = max(highest, int(match.group(1)))
    return highest + 1


def read_existing_patch_context(source_file: Path, repo_root: Path) -> str:
    parts: list[str] = []
    for patch in patch_files_for_source(source_file, repo_root):
        content = patch.read_text(encoding="utf-8", errors="replace")
        parts.append(f"--- {patch.relative_to(repo_root).as_posix()} ---\n{content}\n")
    return "\n".join(parts).strip()


def ensure_clean_worktree(
    repo_root: Path,
    source_file: Path,
    run_cmd: RunCmd,
) -> None:
    status = run_cmd(
        ["git", "status", "--porcelain", "--", source_file.as_posix()],
        cwd=repo_root,
        check=True,
    )
    dirty_lines = [line for line in status.stdout.splitlines() if line.strip()]
    dirty_non_patch = []
    for line in dirty_lines:
        path = line[3:] if len(line) > 3 else ""
        if path and not path.endswith(".patch"):
            dirty_non_patch.append(path)

    if dirty_non_patch:
        raise RuntimeError(
            "Target file has pre-existing non-patch changes; aborting for safety: "
            + ", ".join(dirty_non_patch)
        )


def build_claude_prompt(source_file: Path, existing_patch_context: str) -> str:
    if existing_patch_context:
        existing_section = (
            "Previously generated bug patches for this file are below. "
            "Your new bug must be materially different from all of them:\n\n"
            f"{existing_patch_context}"
        )
    else:
        existing_section = "No previous bug patches exist for this file."

    return (
        "You are editing a git repository via Claude Code.\n"
        f"Target file: {source_file.as_posix()}\n\n"
        "Task:\n"
        "- Introduce exactly one bug in the target file that should cause the adjacent _test file to fail.\n"
        "- Keep the code syntactically valid.\n"
        "- Modify ONLY the target file.\n"
        "- Do not create patch files.\n"
        "- Do not run git commit.\n\n"
        f"{existing_section}\n\n"
        "When done, stop after applying that single bug."
    )


def create_patch_for_source(
    source_file: Path,
    patch_path: Path,
    repo_root: Path,
    run_cmd: RunCmd,
) -> bool:
    diff = run_cmd(["git", "diff", "--", source_file.as_posix()], cwd=repo_root, check=True)
    if not diff.stdout.strip():
        return False
    patch_path.parent.mkdir(parents=True, exist_ok=True)
    patch_path.write_text(diff.stdout, encoding="utf-8")
    return True


def revert_source_file(
    source_file: Path,
    repo_root: Path,
    run_cmd: RunCmd,
) -> None:
    run_cmd(["git", "checkout", "--", source_file.as_posix()], cwd=repo_root, check=True)


def generate_bug_patches_for_file(
    source_file: Path,
    repo_root: Path,
    steps_per_file: int,
    dry_run: bool,
    run_cmd: RunCmd,
    log_path: Path | None = None,
) -> list[Path]:
    generated: list[Path] = []

    for step_index in range(steps_per_file):
        patch_no = next_patch_number(source_file, repo_root)
        patch_path = patch_path_for_source(source_file, repo_root, patch_no)

        if dry_run:
            generated.append(patch_path)
            append_run_log(
                log_path,
                f"dry-run generated placeholder for {source_file.as_posix()} -> "
                f"{patch_path.relative_to(repo_root).as_posix()}",
            )
            continue

        ensure_clean_worktree(repo_root, source_file, run_cmd)

        existing_context = read_existing_patch_context(source_file, repo_root)
        prompt = build_claude_prompt(source_file, existing_context)
        print(
            f"[claude] {source_file.as_posix()} step {step_index + 1}/{steps_per_file} "
            f"-> {patch_path.relative_to(repo_root).as_posix()}"
        )
        append_run_log(
            log_path,
            f"claude start file={source_file.as_posix()} "
            f"step={step_index + 1}/{steps_per_file} patch={patch_path.relative_to(repo_root).as_posix()}",
        )
        claude = run_cmd(["claude", "-p", prompt, "--dangerously-skip-permissions"], cwd=repo_root, check=False)
        print(f"[claude] exit code: {claude.returncode}")
        append_run_log(log_path, f"claude exit code={claude.returncode}")
        append_run_log(log_path, f"claude stdout:\n{claude.stdout}")
        append_run_log(log_path, f"claude stderr:\n{claude.stderr}")
        if claude.returncode != 0:
            raise RuntimeError(
                f"claude failed for {source_file.as_posix()}\n"
                f"stdout:\n{claude.stdout}\n"
                f"stderr:\n{claude.stderr}"
            )

        patch_created = create_patch_for_source(source_file, patch_path, repo_root, run_cmd)
        if not patch_created:
            status = run_cmd(
                ["git", "status", "--porcelain", "--", source_file.as_posix()],
                cwd=repo_root,
                check=False,
            )
            diff = run_cmd(["git", "diff", "--", source_file.as_posix()], cwd=repo_root, check=False)
            append_run_log(log_path, f"no-diff git status for {source_file.as_posix()}:\n{status.stdout}")
            append_run_log(log_path, f"no-diff git diff for {source_file.as_posix()}:\n{diff.stdout}")
            raise RuntimeError(
                f"No diff produced for {source_file.as_posix()} after claude run; "
                "cannot create patch"
            )

        generated.append(patch_path)
        revert_source_file(source_file, repo_root, run_cmd)

    return generated
