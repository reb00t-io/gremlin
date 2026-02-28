#!/usr/bin/env python3
from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.axes import Axes


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create token-count bar plots for markdown and code files from token_report.json"
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=Path(".gremlin/token_report.json"),
        help="Path to token report JSON (default: .gremlin/token_report.json)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(".gremlin/token_report_plot.png"),
        help="Path for output plot image (default: .gremlin/token_report_plot.png)",
    )
    return parser.parse_args()


def load_files(report_path: Path) -> list[dict]:
    data = json.loads(report_path.read_text(encoding="utf-8"))
    return data.get("files", [])


def is_test_code(item: dict) -> bool:
    explicit = item.get("is_test_code")
    if isinstance(explicit, bool):
        return explicit
    path = str(item.get("path", ""))
    file_name = Path(path).name
    stem = Path(file_name).stem
    return stem.endswith("_test")


def sorted_entries(files: list[dict], kind: str, test_selector: bool | None = None) -> list[dict]:
    selected: list[dict] = []
    for item in files:
        if item.get("kind") != kind:
            continue
        if test_selector is True and not is_test_code(item):
            continue
        if test_selector is False and is_test_code(item):
            continue
        selected.append(item)
    return sorted(selected, key=lambda entry: int(entry.get("tokens", 0)), reverse=True)


def total_lines(entries: list[dict]) -> int:
    return sum(int(item.get("lines", 0)) for item in entries)


def plot_kind(ax: Axes, entries: list[dict], label: str, line_label: str) -> None:
    tokens = [int(item.get("tokens", 0)) for item in entries]
    total = sum(tokens)
    lines = total_lines(entries)
    if not tokens:
        ax.set_title(f"{label} total tokens: 0 | total {line_label}: 0")
        ax.set_xlabel("File index")
        ax.set_ylabel("# tokens")
        ax.text(0.5, 0.5, "No files", ha="center", va="center", transform=ax.transAxes)
        return

    x = range(len(tokens))
    ax.bar(x, tokens)
    ax.set_title(f"{label} total tokens: {total} | total {line_label}: {lines}")
    ax.set_xlabel("File index (ordered by tokens)")
    ax.set_ylabel("# tokens")


def top_level_directory(path_value: str) -> str:
    path = Path(path_value)
    parts = path.parts
    if not parts:
        return "."
    if len(parts) == 1:
        return "."
    return parts[0]


def aggregate_code_by_dir(files: list[dict]) -> list[tuple[str, int, int, int]]:
    totals: dict[str, int] = defaultdict(int)
    loc_totals: dict[str, int] = defaultdict(int)
    file_counts: dict[str, int] = defaultdict(int)

    for item in files:
        if item.get("kind") != "code":
            continue
        directory = top_level_directory(str(item.get("path", "")))
        totals[directory] += int(item.get("tokens", 0))
        loc_totals[directory] += int(item.get("lines", 0))
        file_counts[directory] += 1

    aggregated = [
        (directory, tokens, file_counts[directory], loc_totals[directory])
        for directory, tokens in totals.items()
    ]
    aggregated.sort(key=lambda item: item[1], reverse=True)
    return aggregated


def plot_code_by_dir(ax: Axes, aggregated: list[tuple[str, int, int, int]]) -> None:
    if not aggregated:
        ax.set_title("Code by top-level directory total tokens: 0 | total LoC: 0")
        ax.set_xlabel("Top-level directory")
        ax.set_ylabel("# tokens")
        ax.text(0.5, 0.5, "No code files", ha="center", va="center", transform=ax.transAxes)
        return

    labels = [item[0] for item in aggregated]
    tokens = [item[1] for item in aggregated]
    loc_values = [item[3] for item in aggregated]
    total_tokens = sum(tokens)
    total_loc = sum(loc_values)

    ax.bar(range(len(labels)), tokens)
    ax.set_title(f"Code by top-level directory total tokens: {total_tokens} | total LoC: {total_loc}")
    ax.set_xlabel("Top-level directory (ordered by tokens)")
    ax.set_ylabel("# tokens")
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, rotation=45, ha="right")


def generate_token_report_plot(report_path: Path, output_path: Path) -> list[tuple[str, int, int, int]]:
    files = load_files(report_path)

    docs_entries = sorted_entries(files, "markdown")
    code_entries = sorted_entries(files, "code", test_selector=False)
    test_code_entries = sorted_entries(files, "code", test_selector=True)
    aggregated_code_by_dir = aggregate_code_by_dir(files)

    fig, axes = plt.subplots(4, 1, figsize=(14, 18), constrained_layout=True)
    plot_kind(axes[0], docs_entries, "Docs", "lines")
    plot_kind(axes[1], code_entries, "Code (non-test)", "LoC")
    plot_kind(axes[2], test_code_entries, "Code (*_test.*)", "LoC")
    plot_code_by_dir(axes[3], aggregated_code_by_dir)

    fig.savefig(output_path, dpi=150)
    plt.close(fig)

    return aggregated_code_by_dir


def main() -> None:
    args = parse_args()
    aggregated_code_by_dir = generate_token_report_plot(args.report, args.output)

    print(f"Saved plot to {args.output}")

    print("\nAggregated code tokens by top-level directory:")
    for directory, tokens, file_count, loc in aggregated_code_by_dir:
        print(f"- {directory}: {tokens} tokens, {loc} LoC across {file_count} files")


if __name__ == "__main__":
    main()
