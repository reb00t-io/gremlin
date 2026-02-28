from __future__ import annotations

from pathlib import Path


def discover_repo_root(start: Path) -> Path:
    for candidate in (start, *start.parents):
        if (candidate / ".git").exists():
            return candidate
    raise RuntimeError(
        f"Could not find git repository root from: {start}. "
        "Use --repo-root to set it explicitly."
    )
