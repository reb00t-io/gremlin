#!/usr/bin/env python3
from __future__ import annotations

import random
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from claude.claude_runner import run_claude
from gremlin_core import append_jsonl, run_cmd, test_command_for_source
from gremlin_eval_cases import (
    evaluate_case_1_impl,
    evaluate_case_2_impl,
    run_tool_impl,
)
from gremlin_eval_checkout import (
    checkout_path as checkout_path_impl,
    cleanup_bug_report as cleanup_bug_report_impl,
    list_bug_patches,
    load_patch_overview,
    path_for_record,
    prepare_temp_checkout as prepare_temp_checkout_impl,
    remove_checkout,
    resolve_test_patch_path,
    source_file_for_patch,
)
from gremlin_eval_cli import parse_args as parse_args_impl
from gremlin_eval_logging import log_case, log_command_result, log_eval
from repo_root import discover_repo_root


def parse_args():
    return parse_args_impl()


def run_tool(tool_template: str, prompt: str, cwd: Path) -> SimpleNamespace:
    return run_tool_impl(
        tool_template=tool_template,
        prompt=prompt,
        cwd=cwd,
        run_claude_fn=run_claude,
        popen_factory=subprocess.Popen,
    )


def checkout_path(path: Path, repo_root: Path) -> None:
    checkout_path_impl(path, repo_root, run_cmd=run_cmd)


def cleanup_bug_report(repo_root: Path) -> None:
    cleanup_bug_report_impl(repo_root, run_cmd=run_cmd)


def prepare_temp_checkout(source_repo_root: Path, base_commit: str, case_id: str = "eval") -> Path:
    return prepare_temp_checkout_impl(source_repo_root, base_commit, run_cmd=run_cmd, case_id=case_id)


def evaluate_case_1(
    patch_path: Path,
    repo_root: Path,
    tool_template: str,
    source_file: Path | None = None,
    verbose: bool = False,
) -> dict:
    return evaluate_case_1_impl(
        patch_path=patch_path,
        repo_root=repo_root,
        tool_template=tool_template,
        source_file_for_patch=source_file_for_patch,
        test_command_for_source=test_command_for_source,
        path_for_record=path_for_record,
        run_cmd=run_cmd,
        run_tool=run_tool,
        checkout_path=checkout_path,
        cleanup_bug_report=cleanup_bug_report,
        log_case=log_case,
        log_command_result=log_command_result,
        source_file=source_file,
        verbose=verbose,
    )


def evaluate_case_2(
    patch_path: Path,
    repo_root: Path,
    tool_template: str,
    source_file: Path | None = None,
    test_patch_path: Path | None = None,
    verbose: bool = False,
) -> dict:
    return evaluate_case_2_impl(
        patch_path=patch_path,
        repo_root=repo_root,
        tool_template=tool_template,
        source_file_for_patch=source_file_for_patch,
        test_command_for_source=test_command_for_source,
        path_for_record=path_for_record,
        run_cmd=run_cmd,
        run_tool=run_tool,
        checkout_path=checkout_path,
        cleanup_bug_report=cleanup_bug_report,
        log_case=log_case,
        log_command_result=log_command_result,
        source_file=source_file,
        test_patch_path=test_patch_path,
        verbose=verbose,
    )


def evaluate_patch(patch_path: Path, repo_root: Path, tool_template: str, case_id: str, verbose: bool = False) -> dict:
    if case_id == "1":
        return evaluate_case_1(
            patch_path=patch_path,
            repo_root=repo_root,
            tool_template=tool_template,
            verbose=verbose,
        )
    return evaluate_case_2(
        patch_path=patch_path,
        repo_root=repo_root,
        tool_template=tool_template,
        verbose=verbose,
    )


def evaluate_patch_at_overview_commit(
    source_repo_root: Path,
    source_patch_path: Path,
    tool_template: str,
    case_id: str,
    verbose: bool = False,
) -> dict:
    log_eval(
        f"load overview for patch={source_patch_path.relative_to(source_repo_root).as_posix()}",
        verbose=verbose,
        verbose_only=True,
    )
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

    source_file_value = overview.get("source_file")
    if not isinstance(source_file_value, str) or not source_file_value.strip():
        return {
            "timestamp": datetime.now(UTC).isoformat(),
            "case": case_id,
            "patch": source_patch_path.relative_to(source_repo_root).as_posix(),
            "success": False,
            "error": f"invalid_overview_source_file:{overview.get('_overview_path')}",
        }
    source_file = Path(source_file_value)

    log_eval(
        (
            f"prepare temp checkout at commit={overview['base_commit']} "
            f"(overview={overview.get('_overview_path')})"
        ),
        verbose=verbose,
        verbose_only=True,
    )
    checkout_root = prepare_temp_checkout(source_repo_root, overview["base_commit"], case_id=case_id)
    log_eval(f"using temp checkout ({case_id}): {checkout_root}")
    try:
        if case_id == "2":
            source_test_patch = resolve_test_patch_path(
                overview=overview,
                source_patch_path=source_patch_path,
                source_file=source_file,
                source_repo_root=source_repo_root,
            )
            result = evaluate_case_2(
                patch_path=source_patch_path,
                repo_root=checkout_root,
                tool_template=tool_template,
                source_file=source_file,
                test_patch_path=source_test_patch,
                verbose=verbose,
            )
        else:
            result = evaluate_case_1(
                patch_path=source_patch_path,
                repo_root=checkout_root,
                tool_template=tool_template,
                source_file=source_file,
                verbose=verbose,
            )

        result["source_patch"] = source_patch_path.relative_to(source_repo_root).as_posix()
        result["overview"] = overview.get("_overview_path")
        result["base_commit"] = overview.get("base_commit")
        result["evaluated_in_temp_checkout"] = True
        return result
    finally:
        log_eval(f"remove temp checkout {checkout_root}", verbose=verbose, verbose_only=True)
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
    verbose = bool(getattr(args, "verbose", False))
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
                verbose=verbose,
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
