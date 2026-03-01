#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def run_checked(cmd: list[str], cwd: Path) -> None:
    subprocess.run(cmd, cwd=cwd, check=True, capture_output=True, text=True)


def create_temp_repo() -> Path:
    tmpdir = Path(tempfile.mkdtemp(prefix="gremlin-claude-test-"))
    src_dir = tmpdir / "src"
    src_dir.mkdir(parents=True)

    (src_dir / "demo.py").write_text(
        "def add(a, b):\n"
        "    return a + b\n",
        encoding="utf-8",
    )
    (src_dir / "demo_test.py").write_text(
        "from src.demo import add\n\n"
        "def test_add():\n"
        "    assert add(1, 2) == 3\n",
        encoding="utf-8",
    )

    run_checked(["git", "init"], cwd=tmpdir)
    run_checked(["git", "config", "user.email", "test@example.com"], cwd=tmpdir)
    run_checked(["git", "config", "user.name", "Test User"], cwd=tmpdir)
    run_checked(["git", "add", "."], cwd=tmpdir)
    run_checked(["git", "commit", "-m", "init"], cwd=tmpdir)
    return tmpdir


def main() -> int:
    parser = argparse.ArgumentParser(description="Interrupt test for gremlin Claude streaming")
    parser.add_argument("--real", action="store_true", help="Use real claude instead of mock")
    parser.add_argument("--repo-root", type=Path, default=None, help="Repo root to run against")
    parser.add_argument("--delay", type=float, default=1.0, help="Seconds before sending Ctrl-C")
    parser.add_argument("--timeout", type=float, default=10.0, help="Seconds to wait for process exit")
    args = parser.parse_args()

    repo_root = args.repo_root.resolve() if args.repo_root else create_temp_repo()

    env = os.environ.copy()
    if not args.real:
        mock_path = Path(__file__).resolve().parent / "mock_claude.py"
        env["GREMLIN_CLAUDE_BIN"] = str(mock_path)

    cmd = [sys.executable, "-m", "gremlin", "--repo-root", str(repo_root), "--max-files", "1"]
    print(f"Running: {' '.join(cmd)}")
    if not args.real:
        print(f"Using mock claude: {env['GREMLIN_CLAUDE_BIN']}")

    proc = subprocess.Popen(
        cmd,
        cwd=repo_root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        preexec_fn=os.setsid,
    )

    time.sleep(args.delay)
    print("Sending Ctrl-C (SIGINT) ...")
    os.killpg(proc.pid, signal.SIGINT)

    try:
        stdout, stderr = proc.communicate(timeout=args.timeout)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        stdout, stderr = proc.communicate()
        print("FAIL: process did not exit after SIGINT", file=sys.stderr)
        print(stdout)
        print(stderr, file=sys.stderr)
        return 2

    print("--- stdout ---")
    print(stdout)
    print("--- stderr ---", file=sys.stderr)
    print(stderr, file=sys.stderr)
    print(f"exit code: {proc.returncode}")

    if proc.returncode == 130:
        print("PASS: interrupted cleanly")
        return 0

    print("FAIL: expected exit code 130 on Ctrl-C", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
