from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from agents.agent import CmdResult, CmdResultLike

def build_claude_command(prompt: str, claude_bin: str | None = None) -> list[str]:
    binary = claude_bin or os.environ.get("GREMLIN_CLAUDE_BIN", "claude")
    return [
        binary,
        "-p",
        prompt,
        "--dangerously-skip-permissions",
        "--output-format",
        "stream-json",
        "--verbose",
        "--include-partial-messages",
    ]


def run_claude(
    prompt: str,
    repo_root: Path,
    claude_bin: str | None = None,
) -> CmdResultLike:
    command = build_claude_command(prompt=prompt, claude_bin=claude_bin)
    stdout_lines: list[str] = []

    proc = subprocess.Popen(
        command,
        cwd=str(repo_root),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    assert proc.stdout is not None

    def emit(line: str) -> None:
        try:
            event = json.loads(line)
            event_type = event.get("type")
            if event_type == "assistant":
                for block in event.get("message", {}).get("content", []):
                    if block.get("type") == "text":
                        sys.stdout.write(block["text"])
                        sys.stdout.flush()
            elif event_type == "content_block_delta":
                delta = event.get("delta", {})
                if delta.get("type") == "text_delta":
                    sys.stdout.write(delta["text"])
                    sys.stdout.flush()
        except json.JSONDecodeError:
            pass

    for line in proc.stdout:
        stdout_lines.append(line)
        emit(line)

    proc.wait()
    return CmdResult(proc.returncode, "".join(stdout_lines), "")
