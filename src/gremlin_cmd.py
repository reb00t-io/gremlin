from __future__ import annotations

import argparse
from pathlib import Path


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
