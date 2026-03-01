from __future__ import annotations

import shlex
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

from bug_generation import fix_patch_path_for_source, patch_number_from_bug_patch


RunCmd = Callable[[list[str], Path, bool], Any]
RunTool = Callable[[str, str, Path], SimpleNamespace]
TestCommandForSource = Callable[[Path, Path], list[str]]
PathForRecord = Callable[[Path, Path], str]
CheckoutPath = Callable[[Path, Path], None]
CleanupBugReport = Callable[[Path], None]
LogCase = Callable[..., None]
LogCommandResult = Callable[..., None]


def build_fix_prompt() -> str:
    return (
        "You are fixing a bug in a git repository.\n"
        f"The bug is causing a test failure.\n"
        "Task:\n"
        "- Fix the issue that is causing the test failure.\n"
        "- Keep the code syntactically valid.\n"
        "- Prefer minimal changes.\n"
        "- Do not commit.\n\n"
    )


def build_fix_prompt_case2(
    bug_report_path: Path,
    bug_report_content: str,
) -> str:
    return (
        "You are fixing a bug in a git repository.\n"
        "Bug report describing symptoms and impact of the issue:\n"
        f"- File: {bug_report_path.as_posix()}\n"
        f"{bug_report_content}\n"
        "Task:\n"
        "- Fix the bug in source code.\n"
        "- Add or adjust test cases to verify the bug is fixed.\n"
        "- Keep code syntactically valid.\n"
        "- Do not commit.\n"
    )


def run_tool_impl(
    tool_template: str,
    prompt: str,
    cwd: Path,
    *,
    run_claude_fn: Callable[..., Any],
    popen_factory: Callable[..., Any],
) -> SimpleNamespace:
    if tool_template.strip() == "claude":
        claude_result = run_claude_fn(prompt=prompt, repo_root=cwd, claude_bin="claude")
        return SimpleNamespace(
            returncode=claude_result.returncode,
            stdout=claude_result.stdout,
            stderr=claude_result.stderr,
        )

    prompt_token = "__GREMLIN_PROMPT__"
    if "<PROMPT>" in tool_template:
        template = tool_template.replace("<PROMPT>", prompt_token)
        command = [part.replace(prompt_token, prompt) for part in shlex.split(template)]
    else:
        command = [*shlex.split(tool_template), prompt]

    stdout_lines: list[str] = []
    proc = popen_factory(
        command,
        cwd=str(cwd),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    if proc.stdout is None:
        raise RuntimeError("Popen stdout is None despite stdout=PIPE")

    for line in proc.stdout:
        stdout_lines.append(line)
        sys.stdout.write(line)
        sys.stdout.flush()

    proc.wait()
    return SimpleNamespace(returncode=proc.returncode, stdout="".join(stdout_lines), stderr="")


def evaluate_case_1_impl(
    patch_path: Path,
    repo_root: Path,
    tool_template: str,
    *,
    source_file_for_patch: Callable[[Path, Path], Path],
    test_command_for_source: TestCommandForSource,
    path_for_record: PathForRecord,
    run_cmd: RunCmd,
    run_tool: RunTool,
    checkout_path: CheckoutPath,
    cleanup_bug_report: CleanupBugReport,
    log_case: LogCase,
    log_command_result: LogCommandResult,
    source_file: Path | None = None,
    verbose: bool = False,
) -> dict:
    record: dict = {
        "timestamp": datetime.now(UTC).isoformat(),
        "case": "1",
        "patch": path_for_record(patch_path, repo_root),
        "success": False,
        "error": None,
    }
    if source_file is None:
        source_file = source_file_for_patch(patch_path, repo_root)
    test_file = source_file.with_name(f"{source_file.stem}_test{source_file.suffix}")
    test_cmd = test_command_for_source(source_file, test_file)
    record["source_file"] = source_file.as_posix()
    record["test_file"] = test_file.as_posix()
    record["test_command"] = shlex.join(test_cmd)
    record["bug_report"] = "bug_report.txt"

    bug_report_abs = repo_root / "bug_report.txt"
    log_case("1", f"start patch={path_for_record(patch_path, repo_root)} source={source_file.as_posix()}")
    if bug_report_abs.exists():
        log_case("1", "abort: bug_report.txt already exists before evaluation")
        record["error"] = "unexpected_bug_report_present_case1"
        return record

    log_case("1", "running baseline all-tests (pytest)")
    baseline_all = run_cmd(["pytest"], cwd=repo_root, check=False)
    record["baseline_all_exit_code"] = baseline_all.returncode
    log_command_result(
        "1",
        "baseline all-tests",
        baseline_all.returncode,
        baseline_all.stdout,
        baseline_all.stderr,
        verbose=verbose,
    )
    if baseline_all.returncode != 0:
        print("baseline all-tests failed before applying patch", file=sys.stderr)
        record["error"] = "baseline_all_tests_failed"
        return record

    log_case("1", f"applying bug patch {patch_path.as_posix()}")
    apply_result = run_cmd(["git", "apply", patch_path.as_posix()], cwd=repo_root, check=False)
    record["patch_apply_exit_code"] = apply_result.returncode
    log_case("1", f"bug patch apply exit={apply_result.returncode}")
    try:
        if apply_result.returncode != 0:
            print("failed to apply patch", file=sys.stderr)
            record["error"] = "patch_apply_failed"
            return record

        log_case("1", f"running target test command: {shlex.join(test_cmd)}")
        failing_test = run_cmd(test_cmd, cwd=repo_root, check=False)
        record["failing_test_exit_code"] = failing_test.returncode
        log_command_result(
            "1",
            "post-bug test",
            failing_test.returncode,
            failing_test.stdout,
            failing_test.stderr,
            verbose=verbose,
        )
        if failing_test.returncode == 0 or failing_test.returncode == 5:
            record["error"] = "patched_test_did_not_fail"
            return record

        prompt = build_fix_prompt()
        log_case("1", "invoking fixer tool")
        tool_result = run_tool(tool_template=tool_template, prompt=prompt, cwd=repo_root)
        record["tool_exit_code"] = tool_result.returncode
        log_command_result(
            "1",
            "tool",
            tool_result.returncode,
            tool_result.stdout,
            tool_result.stderr,
            verbose=verbose,
        )
        if tool_result.returncode != 0:
            record["error"] = "tool_failed"
            return record

        log_case("1", "running target tests after fix")
        fixed_test = run_cmd(test_cmd, cwd=repo_root, check=False)
        record["fixed_test_exit_code"] = fixed_test.returncode
        log_command_result(
            "1",
            "post-fix test",
            fixed_test.returncode,
            fixed_test.stdout,
            fixed_test.stderr,
            verbose=verbose,
        )
        if bug_report_abs.exists():
            record["error"] = "bug_report_created_in_case1"
            return record
        record["success"] = fixed_test.returncode == 0
        if not record["success"]:
            record["error"] = "fix_test_still_failing"
        log_case("1", f"completed success={record['success']}")
        return record
    finally:
        log_case("1", "cleanup: restoring source/test/bug_report")
        checkout_path(source_file, repo_root)
        checkout_path(test_file, repo_root)
        cleanup_bug_report(repo_root)


def evaluate_case_2_impl(
    patch_path: Path,
    repo_root: Path,
    tool_template: str,
    *,
    source_file_for_patch: Callable[[Path, Path], Path],
    test_command_for_source: TestCommandForSource,
    path_for_record: PathForRecord,
    run_cmd: RunCmd,
    run_tool: RunTool,
    checkout_path: CheckoutPath,
    cleanup_bug_report: CleanupBugReport,
    log_case: LogCase,
    log_command_result: LogCommandResult,
    source_file: Path | None = None,
    test_patch_path: Path | None = None,
    verbose: bool = False,
) -> dict:
    record: dict = {
        "timestamp": datetime.now(UTC).isoformat(),
        "case": "2",
        "patch": path_for_record(patch_path, repo_root),
        "success": False,
        "error": None,
    }

    if source_file is None:
        source_file = source_file_for_patch(patch_path, repo_root)
    test_file = source_file.with_name(f"{source_file.stem}_test{source_file.suffix}")
    test_cmd = test_command_for_source(source_file, test_file)
    if test_patch_path is None:
        patch_no = patch_number_from_bug_patch(patch_path)
        test_patch_path = fix_patch_path_for_source(source_file, repo_root, patch_no)
    bug_report_rel = Path("bug_report.txt")
    bug_report_abs = repo_root / bug_report_rel

    record["source_file"] = source_file.as_posix()
    record["test_file"] = test_file.as_posix()
    record["test_command"] = shlex.join(test_cmd)
    record["test_patch"] = path_for_record(test_patch_path, repo_root)
    record["bug_report"] = bug_report_rel.as_posix()
    log_case(
        "2",
        (
            f"start bug_patch={path_for_record(patch_path, repo_root)} "
            f"test_patch={path_for_record(test_patch_path, repo_root)} "
            f"source={source_file.as_posix()}"
        ),
    )

    if not test_patch_path.is_file():
        log_case("2", "abort: missing test patch")
        record["error"] = "missing_test_patch"
        return record

    log_case("2", "running baseline all-tests (pytest)")
    baseline_all = run_cmd(["pytest"], cwd=repo_root, check=False)
    record["baseline_all_exit_code"] = baseline_all.returncode
    log_command_result(
        "2",
        "baseline all-tests",
        baseline_all.returncode,
        baseline_all.stdout,
        baseline_all.stderr,
        verbose=verbose,
    )
    if baseline_all.returncode != 0:
        print("baseline all-tests failed before applying patch", file=sys.stderr)
        record["error"] = "baseline_all_tests_failed"
        return record

    log_case("2", f"applying bug patch {patch_path.as_posix()}")
    apply_bug_result = run_cmd(["git", "apply", patch_path.as_posix()], cwd=repo_root, check=False)
    record["bug_patch_apply_exit_code"] = apply_bug_result.returncode
    log_case("2", f"bug patch apply exit={apply_bug_result.returncode}")
    try:
        if apply_bug_result.returncode != 0:
            print("failed to apply patch", file=sys.stderr)
            record["error"] = "bug_patch_apply_failed"
            return record

        log_case("2", f"running target test command: {shlex.join(test_cmd)}")
        failing_test = run_cmd(test_cmd, cwd=repo_root, check=False)
        record["failing_test_exit_code"] = failing_test.returncode
        log_command_result(
            "2",
            "post-bug test",
            failing_test.returncode,
            failing_test.stdout,
            failing_test.stderr,
            verbose=verbose,
        )
        if failing_test.returncode == 0 or failing_test.returncode == 5:
            record["error"] = "bug_patch_did_not_fail_test"
            return record

        log_case("2", f"applying test patch {test_patch_path.as_posix()}")
        apply_test_result = run_cmd(["git", "apply", test_patch_path.as_posix()], cwd=repo_root, check=False)
        record["test_patch_apply_exit_code"] = apply_test_result.returncode
        log_case("2", f"test patch apply exit={apply_test_result.returncode}")
        if apply_test_result.returncode != 0:
            record["error"] = "test_patch_apply_failed"
            return record

        log_case("2", "running all tests with modified tests")
        masked_all = run_cmd(["pytest"], cwd=repo_root, check=False)
        record["masked_all_tests_exit_code"] = masked_all.returncode
        log_command_result(
            "2",
            "masked all-tests",
            masked_all.returncode,
            masked_all.stdout,
            masked_all.stderr,
            verbose=verbose,
        )
        if masked_all.returncode != 0:
            record["error"] = "tests_not_passing_after_test_patch"
            return record

        if not bug_report_abs.is_file():
            log_case("2", "abort: bug_report.txt missing after applying test patch")
            record["error"] = "missing_bug_report_in_case2"
            return record

        bug_report_content = bug_report_abs.read_text(encoding="utf-8", errors="replace")

        prompt = build_fix_prompt_case2(
            bug_report_path=bug_report_rel,
            bug_report_content=bug_report_content,
        )
        log_case("2", "invoking fixer tool")
        tool_result = run_tool(tool_template=tool_template, prompt=prompt, cwd=repo_root)
        record["tool_exit_code"] = tool_result.returncode
        log_command_result(
            "2",
            "tool",
            tool_result.returncode,
            tool_result.stdout,
            tool_result.stderr,
            verbose=verbose,
        )
        if tool_result.returncode != 0:
            record["error"] = "tool_failed"
            return record

        log_case("2", "restoring original test file and rerunning target tests")
        checkout_path(test_file, repo_root)
        restored_test = run_cmd(test_cmd, cwd=repo_root, check=False)
        record["restored_test_exit_code"] = restored_test.returncode
        log_command_result(
            "2",
            "restored-test",
            restored_test.returncode,
            restored_test.stdout,
            restored_test.stderr,
            verbose=verbose,
        )
        record["success"] = restored_test.returncode == 0
        if not record["success"]:
            record["error"] = "fix_fails_with_original_tests"
        log_case("2", f"completed success={record['success']}")
        return record
    finally:
        log_case("2", "cleanup: restoring source/test/bug_report")
        checkout_path(source_file, repo_root)
        checkout_path(test_file, repo_root)
        cleanup_bug_report(repo_root)
