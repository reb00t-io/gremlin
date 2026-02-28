from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_gremlin_runs_on_repo() -> None:
    repo_root = Path(__file__).resolve().parents[1]

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "gremlin",
            "--repo-root",
            str(repo_root),
            "--dry-run",
            "--max-files",
            "1",
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "Found " in result.stdout
    assert "Processing up to " in result.stdout
    assert "Verification results written to " in result.stdout
    assert ".gremlin/verification_results.jsonl" in result.stdout
