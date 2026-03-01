from __future__ import annotations

import argparse
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Evaluate a fixer tool against randomly sampled Gremlin bug patches."
        ),
        epilog=(
            "Examples:\n"
            "  gremlin-eval \"claude\"\n"
            "  gremlin-eval \"claude -p <PROMPT>\"\n"
            "  gremlin-eval \"claude -p <PROMPT>\" --case 2 --limit 5 --seed 42\n"
            "  gremlin-eval \"claude\" --case 2 --verbose"
        ),
    )
    parser.add_argument(
        "tool_command",
        help=(
            "Command template to run the fixer tool. Use <PROMPT> as the placeholder, "
            'for example: "claude" or "claude -p <PROMPT>"'
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
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show detailed command output during evaluation.",
    )
    return parser.parse_args()
