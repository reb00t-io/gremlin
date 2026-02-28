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


def test_gremlin_scan_runs_on_repo() -> None:
    repo_root = Path(__file__).resolve().parents[1]

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "gremlin_scan",
            "--repo-root",
            str(repo_root),
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "Tracked files scanned: " in result.stdout
    assert "Output totals:" in result.stdout
    assert "Saved plot to " in result.stdout
    assert (repo_root / ".gremlin" / "token_report.json").is_file()
    assert (repo_root / ".gremlin" / "token_report_plot.png").is_file()
