#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import signal
import sys
import time


interrupted = False


def on_sigint(_signum: int, _frame) -> None:  # type: ignore[no-untyped-def]
    global interrupted
    interrupted = True


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("-p", dest="prompt", default="")
    parser.add_argument("--max-ticks", type=int, default=None)
    args, _ = parser.parse_known_args()

    signal.signal(signal.SIGINT, on_sigint)

    sys.stdout.write(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "mock claude start\n"}]}}) + "\n")
    sys.stdout.flush()

    idx = 0
    while not interrupted:
        if args.max_ticks is not None and idx >= args.max_ticks:
            return 0
        payload = {
            "type": "content_block_delta",
            "delta": {"type": "text_delta", "text": f"tick-{idx} "},
        }
        sys.stdout.write(json.dumps(payload) + "\n")
        sys.stdout.flush()
        idx += 1
        time.sleep(0.2)

    sys.stderr.write("mock claude interrupted by SIGINT\n")
    sys.stderr.flush()
    return 130


if __name__ == "__main__":
    raise SystemExit(main())
