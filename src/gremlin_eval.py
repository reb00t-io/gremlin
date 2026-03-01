#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import random
import re
import shutil
import shlex
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from bug_generation import fix_patch_path_for_source, overview_path_for_source, patch_number_from_bug_patch
from gremlin_core import append_jsonl, run_cmd, test_command_for_source
from repo_root import discover_repo_root


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate a fixer tool against randomly sampled Gremlin bug patches."
        )
    )
    parser.add_argument(
        "tool_command",
        help=(
            "Command template to run the fixer tool. Use <PROMPT> as the placeholder, "
            'for example: "claude -p <PROMPT>"'
        ),
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Maximum number of patches to evaluate (default: 10).",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Random seed for deterministic sampling.",
    )
    parser.add_argument(
        "--case",
        choices=["1", "2", "both"],
        default="both",
        help=(
            "Evaluation case: 1=bug patch only (failing tests), "
            "2=bug patch + modified tests, both=run both cases (default)."
        ),
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=None,
        help="Repository root (default: auto-discover from current working directory).",
    )
    parser.add_argument(
        "--results-file",
        type=Path,
        default=Path(".gremlin/eval_results.jsonl"),
        help="Path to append per-patch evaluation results (default: .gremlin/eval_results.jsonl).",
    )
    return parser.parse_args()


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
        "Task:\n"
        "- Fix the bug in source code.\n"
        "- Add or adjust test cases to verify the bug is fixed.\n"
        "- Keep code syntactically valid.\n"
        "- Do not commit.\n"
    )


def run_tool(tool_template: str, prompt: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    quoted_prompt = shlex.quote(prompt)
    command = tool_template.replace("<PROMPT>", quoted_prompt)
    if "<PROMPT>" not in tool_template:
        command = f"{tool_template} {quoted_prompt}"

    return subprocess.run(
        command,
        cwd=str(cwd),
        shell=True,
        capture_output=True,
        text=True,
        check=False,
    )


def checkout_path(path: Path, repo_root: Path) -> None:
    run_cmd(["git", "checkout", "--", path.as_posix()], cwd=repo_root, check=False)


def cleanup_bug_report(repo_root: Path) -> None:
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


def prepare_temp_checkout(source_repo_root: Path, base_commit: str) -> Path:
    tmp_root = Path(tempfile.mkdtemp(prefix="gremlin-eval-"))
    run_cmd(["git", "clone", "--quiet", source_repo_root.as_posix(), tmp_root.as_posix()], cwd=source_repo_root, check=True)
    run_cmd(["git", "checkout", "--quiet", base_commit], cwd=tmp_root, check=True)
    return tmp_root


def copy_patch_to_checkout(source_repo_root: Path, checkout_root: Path, patch_path: Path) -> Path:
    rel = patch_path.relative_to(source_repo_root)
    dest = checkout_root / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(patch_path.read_text(encoding="utf-8"), encoding="utf-8")
    return dest


def remove_checkout(checkout_root: Path) -> None:
    shutil.rmtree(checkout_root, ignore_errors=True)


def evaluate_case_1(patch_path: Path, repo_root: Path, tool_template: str) -> dict:
    record: dict = {
        "timestamp": datetime.now(UTC).isoformat(),
        "case": "1",
        "patch": patch_path.relative_to(repo_root).as_posix(),
        "success": False,
        "error": None,
    }
    source_file = source_file_for_patch(patch_path, repo_root)
    test_file = source_file.with_name(f"{source_file.stem}_test{source_file.suffix}")
    test_cmd = test_command_for_source(source_file, test_file)
    record["source_file"] = source_file.as_posix()
    record["test_file"] = test_file.as_posix()
    record["test_command"] = shlex.join(test_cmd)
    record["bug_report"] = "bug_report.txt"

    bug_report_abs = repo_root / "bug_report.txt"
    if bug_report_abs.exists():
        record["error"] = "unexpected_bug_report_present_case1"
        return record

    baseline_all = run_cmd(["pytest"], cwd=repo_root, check=False)
    record["baseline_all_exit_code"] = baseline_all.returncode
    if baseline_all.returncode != 0:
        print("baseline all-tests failed before applying patch", file=sys.stderr)
        record["error"] = "baseline_all_tests_failed"
        return record

    apply_result = run_cmd(["git", "apply", patch_path.as_posix()], cwd=repo_root, check=False)
    record["patch_apply_exit_code"] = apply_result.returncode
    if apply_result.returncode != 0:
        print("failed to apply patch", file=sys.stderr)
        record["error"] = "patch_apply_failed"
        return record

    try:
        failing_test = run_cmd(test_cmd, cwd=repo_root, check=False)
        record["failing_test_exit_code"] = failing_test.returncode
        if failing_test.returncode == 0 or failing_test.returncode == 5:
            record["error"] = "patched_test_did_not_fail"
            return record

        prompt = build_fix_prompt()
        tool_result = run_tool(tool_template=tool_template, prompt=prompt, cwd=repo_root)
        record["tool_exit_code"] = tool_result.returncode
        if tool_result.returncode != 0:
            record["error"] = "tool_failed"
            return record

        fixed_test = run_cmd(test_cmd, cwd=repo_root, check=False)
        record["fixed_test_exit_code"] = fixed_test.returncode
        if bug_report_abs.exists():
            record["error"] = "bug_report_created_in_case1"
            return record
        record["success"] = fixed_test.returncode == 0
        if not record["success"]:
            record["error"] = "fix_test_still_failing"
        return record
    finally:
        checkout_path(source_file, repo_root)
        checkout_path(test_file, repo_root)
        cleanup_bug_report(repo_root)


def evaluate_case_2(patch_path: Path, repo_root: Path, tool_template: str) -> dict:
    record: dict = {
        "timestamp": datetime.now(UTC).isoformat(),
        "case": "2",
        "patch": patch_path.relative_to(repo_root).as_posix(),
        "success": False,
        "error": None,
    }

    source_file = source_file_for_patch(patch_path, repo_root)
    test_file = source_file.with_name(f"{source_file.stem}_test{source_file.suffix}")
    test_cmd = test_command_for_source(source_file, test_file)
    patch_no = patch_number_from_bug_patch(patch_path)
    test_patch_path = fix_patch_path_for_source(source_file, repo_root, patch_no)
    bug_report_rel = Path("bug_report.txt")
    bug_report_abs = repo_root / bug_report_rel

    record["source_file"] = source_file.as_posix()
    record["test_file"] = test_file.as_posix()
    record["test_command"] = shlex.join(test_cmd)
    record["test_patch"] = test_patch_path.relative_to(repo_root).as_posix()
    record["bug_report"] = bug_report_rel.as_posix()

    if not test_patch_path.is_file():
        record["error"] = "missing_test_patch"
        return record

    baseline_all = run_cmd(["pytest"], cwd=repo_root, check=False)
    record["baseline_all_exit_code"] = baseline_all.returncode
    if baseline_all.returncode != 0:
        print("baseline all-tests failed before applying patch", file=sys.stderr)
        record["error"] = "baseline_all_tests_failed"
        return record

    apply_bug_result = run_cmd(["git", "apply", patch_path.as_posix()], cwd=repo_root, check=False)
    record["bug_patch_apply_exit_code"] = apply_bug_result.returncode
    if apply_bug_result.returncode != 0:
        print("failed to apply patch", file=sys.stderr)
        record["error"] = "bug_patch_apply_failed"
        return record

    try:
        failing_test = run_cmd(test_cmd, cwd=repo_root, check=False)
        record["failing_test_exit_code"] = failing_test.returncode
        if failing_test.returncode == 0 or failing_test.returncode == 5:
            record["error"] = "bug_patch_did_not_fail_test"
            return record

        apply_test_result = run_cmd(["git", "apply", test_patch_path.as_posix()], cwd=repo_root, check=False)
        record["test_patch_apply_exit_code"] = apply_test_result.returncode
        if apply_test_result.returncode != 0:
            record["error"] = "test_patch_apply_failed"
            return record

        masked_all = run_cmd(["pytest"], cwd=repo_root, check=False)
        record["masked_all_tests_exit_code"] = masked_all.returncode
        if masked_all.returncode != 0:
            record["error"] = "tests_not_passing_after_test_patch"
            return record

        if not bug_report_abs.is_file():
            record["error"] = "missing_bug_report_in_case2"
            return record

        bug_report_content = bug_report_abs.read_text(encoding="utf-8", errors="replace")

        prompt = build_fix_prompt_case2(
            bug_report_path=bug_report_rel,
            bug_report_content=bug_report_content,
        )
        tool_result = run_tool(tool_template=tool_template, prompt=prompt, cwd=repo_root)
        record["tool_exit_code"] = tool_result.returncode
        if tool_result.returncode != 0:
            record["error"] = "tool_failed"
            return record

        checkout_path(test_file, repo_root)
        restored_test = run_cmd(test_cmd, cwd=repo_root, check=False)
        record["restored_test_exit_code"] = restored_test.returncode
        record["success"] = restored_test.returncode == 0
        if not record["success"]:
            record["error"] = "fix_fails_with_original_tests"
        return record
    finally:
        checkout_path(source_file, repo_root)
        checkout_path(test_file, repo_root)
        cleanup_bug_report(repo_root)


def evaluate_patch(patch_path: Path, repo_root: Path, tool_template: str, case_id: str) -> dict:
    if case_id == "1":
        return evaluate_case_1(patch_path=patch_path, repo_root=repo_root, tool_template=tool_template)
    return evaluate_case_2(patch_path=patch_path, repo_root=repo_root, tool_template=tool_template)


def evaluate_patch_at_overview_commit(
    source_repo_root: Path,
    source_patch_path: Path,
    tool_template: str,
    case_id: str,
) -> dict:
    try:
        overview = load_patch_overview(source_repo_root, source_patch_path)
    except RuntimeError as err:
        return {
            "timestamp": datetime.now(UTC).isoformat(),
            "case": case_id,
            "patch": source_patch_path.relative_to(source_repo_root).as_posix(),
            "success": False,
            "error": str(err),
        }

    checkout_root = prepare_temp_checkout(source_repo_root, overview["base_commit"])
    try:
        checkout_bug_patch = copy_patch_to_checkout(source_repo_root, checkout_root, source_patch_path)
        if case_id == "2":
            source_file = source_file_for_patch(source_patch_path, source_repo_root)
            patch_no = patch_number_from_bug_patch(source_patch_path)
            source_test_patch = fix_patch_path_for_source(source_file, source_repo_root, patch_no)
            if source_test_patch.is_file():
                copy_patch_to_checkout(source_repo_root, checkout_root, source_test_patch)

        result = evaluate_patch(
            patch_path=checkout_bug_patch,
            repo_root=checkout_root,
            tool_template=tool_template,
            case_id=case_id,
        )
        result["source_patch"] = source_patch_path.relative_to(source_repo_root).as_posix()
        result["overview"] = overview.get("_overview_path")
        result["base_commit"] = overview.get("base_commit")
        result["evaluated_in_temp_checkout"] = True
        return result
    finally:
        remove_checkout(checkout_root)


def main() -> int:
    args = parse_args()

    if args.limit <= 0:
        print("--limit must be > 0", file=sys.stderr)
        return 2

    repo_root = args.repo_root.resolve() if args.repo_root else discover_repo_root(Path.cwd())
    results_file = args.results_file if args.results_file.is_absolute() else repo_root / args.results_file
    patches = list_bug_patches(repo_root)
    if not patches:
        print("No bug patches found under .gremlin/bugs", file=sys.stderr)
        return 1

    count = min(args.limit, len(patches))
    random_gen = random.Random(args.seed)
    selected = random_gen.sample(patches, count)

    cases = ["1", "2"] if args.case == "both" else [args.case]
    print(f"Evaluating {len(selected)} patches (seed={args.seed}, case={args.case})")

    for case_id in cases:
        print(f"\n=== Case {case_id} ===")
        successes = 0
        for index, patch_path in enumerate(selected, start=1):
            patch_rel = patch_path.relative_to(repo_root).as_posix()
            print(f"[{index}/{len(selected)}] {patch_rel}")
            result = evaluate_patch_at_overview_commit(
                source_repo_root=repo_root,
                source_patch_path=patch_path,
                tool_template=args.tool_command,
                case_id=case_id,
            )
            append_jsonl(results_file, result)
            if result["success"]:
                print("  PASS")
            else:
                error_reason = result.get("error") or "unknown"
                print(f"  FAIL ({error_reason})")
            if result["success"]:
                successes += 1

        rate = successes / len(selected)
        print(f"Case {case_id} success rate: {successes}/{len(selected)} ({rate:.1%})")

    print(f"Results: {results_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
