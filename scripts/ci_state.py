#!/usr/bin/env python3
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from typing import Any

from prompt_toolkit.shortcuts import radiolist_dialog


def run_gh_json(args: list[str]) -> Any:
    proc = subprocess.run(
        ["gh", *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or "gh command failed")
    return json.loads(proc.stdout)


def list_running_actions() -> list[dict[str, Any]]:
    return run_gh_json(
        [
            "run",
            "list",
            "--status",
            "in_progress",
            "--json",
            "databaseId,workflowName,displayTitle,headBranch,event,createdAt,url",
            "--limit",
            "30",
        ]
    )


def list_recent_runs(limit: int = 10) -> list[dict[str, Any]]:
    return run_gh_json(
        [
            "run",
            "list",
            "--json",
            "databaseId,workflowName,displayTitle,status,conclusion,headBranch,createdAt",
            "--limit",
            str(limit),
        ]
    )


def run_state(run: dict[str, Any]) -> str:
    status = str(run.get("status") or "").lower()
    conclusion = str(run.get("conclusion") or "").lower()
    if status != "completed":
        return "running"
    if conclusion in {"success", "neutral", "skipped"}:
        return "completed"
    return "failed"


def run_state_emoji(state: str) -> str:
    if state == "running":
        return "🟡"
    if state == "completed":
        return "✅"
    return "❌"


def select_run(runs: list[dict[str, Any]]) -> int | None:
    values: list[tuple[int, str]] = []
    for item in runs:
        run_id = int(item["databaseId"])
        workflow = item.get("workflowName") or "<unknown workflow>"
        title = item.get("displayTitle") or "<no title>"
        branch = item.get("headBranch") or "<no branch>"
        created = item.get("createdAt") or "<no time>"
        label = f"{workflow} | {title} | {branch} | {created}"
        values.append((run_id, label))

    return radiolist_dialog(
        title="Running GitHub Actions",
        text="Select a run to view details:",
        values=values,
        ok_text="Show details",
        cancel_text="Cancel",
    ).run()


def show_run_details(run_id: int) -> int:
    proc = subprocess.run(["gh", "run", "view", str(run_id)], check=False)
    return proc.returncode


def main() -> int:
    if shutil.which("gh") is None:
        print("Error: gh CLI is not installed or not on PATH.", file=sys.stderr)
        return 1

    try:
        recent_runs = list_recent_runs(limit=10)
        runs = list_running_actions()
    except Exception as exc:
        print(f"Error listing running actions: {exc}", file=sys.stderr)
        return 1

    print("Last 10 runs:")
    for item in recent_runs:
        run_id = item.get("databaseId")
        workflow = item.get("workflowName") or "<unknown workflow>"
        title = item.get("displayTitle") or "<no title>"
        state = run_state(item)
        emoji = run_state_emoji(state)
        print(f"- {run_id}: {emoji} {workflow} | {title}")
    print()

    print(f"Running actions: {len(runs)}")
    for item in runs:
        run_id = item.get("databaseId")
        workflow = item.get("workflowName") or "<unknown workflow>"
        title = item.get("displayTitle") or "<no title>"
        print(f"- {run_id}: {workflow} | {title}")

    if not runs:
        return 0

    selected = select_run(runs)
    if selected is None:
        print("No run selected.")
        return 0

    print(f"\nShowing details for run {selected}...\n")
    return show_run_details(selected)


if __name__ == "__main__":
    raise SystemExit(main())
