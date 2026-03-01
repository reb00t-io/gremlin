#!/usr/bin/env python3
from __future__ import annotations

from datetime import UTC, datetime
import os
from pathlib import Path

from gremlin_cmd import parse_args
from gremlin_core import run_generation_and_verification
from repo_root import discover_repo_root

def build_run_log_path(repo_root: Path) -> Path:
    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return repo_root / ".gremlin" / "log" / f"{timestamp}-{os.getpid()}.log"

def main() -> int:
    args = parse_args()
    repo_root = args.repo_root.resolve() if args.repo_root else discover_repo_root(Path.cwd())
    results_file = args.results_file if args.results_file.is_absolute() else repo_root / args.results_file
    run_log_path = build_run_log_path(repo_root)
    return run_generation_and_verification(
        repo_root=repo_root,
        max_files=args.max_files,
        steps_per_file=args.steps_per_file,
        dry_run=args.dry_run,
        results_file=results_file,
        run_log_path=run_log_path,
    )


if __name__ == "__main__":
    raise SystemExit(main())
