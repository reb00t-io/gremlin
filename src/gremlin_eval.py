#!/usr/bin/env python3
from __future__ import annotations

import argparse
import random
import re
import shlex
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

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


def build_fix_prompt(source_file: Path, test_file: Path, patch_path: Path, failing_output: str) -> str:
    failure_tail = failing_output[-4000:]
    return (
        "You are fixing a bug in a git repository.\n"
        f"Target source file: {source_file.as_posix()}\n"
        f"Failing test file: {test_file.as_posix()}\n"
        f"Bug patch id: {patch_path.name}\n\n"
        "Task:\n"
        "- Fix the issue that is causing the test failure.\n"
        "- Keep the code syntactically valid.\n"
        "- Prefer minimal changes.\n"
        "- Do not modify tests.\n"
        "- Do not commit.\n\n"
        "Observed failing test output (tail):\n"
        f"{failure_tail}\n"
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


def evaluate_patch(patch_path: Path, repo_root: Path, tool_template: str) -> dict:
    record: dict = {
        "timestamp": datetime.now(UTC).isoformat(),
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

        prompt = build_fix_prompt(
            source_file=source_file,
            test_file=test_file,
            patch_path=patch_path,
            failing_output=f"{failing_test.stdout}\n{failing_test.stderr}",
        )
        tool_result = run_tool(tool_template=tool_template, prompt=prompt, cwd=repo_root)
        record["tool_exit_code"] = tool_result.returncode
        if tool_result.returncode != 0:
            record["error"] = "tool_failed"
            return record

        fixed_test = run_cmd(test_cmd, cwd=repo_root, check=False)
        record["fixed_test_exit_code"] = fixed_test.returncode
        record["success"] = fixed_test.returncode == 0
        if not record["success"]:
            record["error"] = "fix_test_still_failing"
        return record
    finally:
        checkout_path(source_file, repo_root)
        checkout_path(test_file, repo_root)


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

    print(f"Evaluating {len(selected)} patches (seed={args.seed})")

    successes = 0
    for index, patch_path in enumerate(selected, start=1):
        patch_rel = patch_path.relative_to(repo_root).as_posix()
        print(f"[{index}/{len(selected)}] {patch_rel}")
        result = evaluate_patch(patch_path=patch_path, repo_root=repo_root, tool_template=args.tool_command)
        append_jsonl(results_file, result)
        print("  PASS" if result["success"] else "  FAIL")
        if result["success"]:
            successes += 1

    rate = successes / len(selected)
    print(f"\nSuccess rate: {successes}/{len(selected)} ({rate:.1%})")
    print(f"Results: {results_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
