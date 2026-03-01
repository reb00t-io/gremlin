#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable

import tiktoken

from plot_token_report import generate_token_report_plot
from repo_root import discover_repo_root

MARKDOWN_EXTENSIONS = {".md", ".markdown", ".mdx"}
CODE_EXTENSIONS = {
    ".go",
    ".py",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".svelte",
    ".c",
    ".cc",
    ".cpp",
    ".h",
    ".hh",
    ".hpp",
    ".java",
    ".kt",
    ".rs",
    ".swift",
    ".rb",
    ".php",
    ".cs",
    ".scala",
    ".lua",
    ".m",
    ".mm",
    ".sql",
    ".sh",
    ".bash",
    ".zsh",
    ".fish",
    ".ps1",
    ".yaml",
    ".yml",
    ".toml",
    ".json",
    ".xml",
    ".proto",
    ".nix",
    ".css",
    ".scss",
    ".less",
    ".html",
    ".htm",
    ".vue",
    ".ini",
    ".cfg",
    ".conf",
}
CODE_FILENAMES = {
    "Dockerfile",
    "Containerfile",
    "Makefile",
    "Justfile",
    "CMakeLists.txt",
    "Jenkinsfile",
    "Vagrantfile",
    "Tiltfile",
    "build.gradle",
    "gradlew",
}
DEFAULT_GREMLIN_CONFIG = {
    "exclude_file_names": [
        "package-lock.json",
        ".gremlin",
    ]
}


@dataclass
class FileMetric:
    path: str
    kind: str
    is_test_code: bool
    bytes: int
    tokens: int
    lines: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Collect tracked git files, combine markdown/code into docs.md/code.md, "
            "and report bytes + tiktoken tokens per file."
        )
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=None,
        help=(
            "Path to repository root (default: auto-discover from current "
            "working directory)."
        ),
    )
    parser.add_argument(
        "--encoding",
        default="cl100k_base",
        help="tiktoken encoding name (default: cl100k_base).",
    )
    parser.add_argument(
        "--filter",
        type=Path,
        default=Path(".gremlin/config.json"),
        help="Path to filename exclusion filter JSON (default: .gremlin/config.json).",
    )
    return parser.parse_args()


def load_excluded_file_names(filter_path: Path) -> set[str]:
    if not filter_path.exists():
        return set()

    data = json.loads(filter_path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        raw_names = data
    elif isinstance(data, dict):
        raw_names = data.get("exclude_file_names", [])
    else:
        raw_names = []

    excluded: set[str] = set()
    for item in raw_names:
        if isinstance(item, str) and item:
            excluded.add(item)
    return excluded


def ensure_default_gremlin_config(repo_root: Path) -> Path:
    config_path = repo_root / ".gremlin" / "config.json"
    if config_path.exists():
        return config_path
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(DEFAULT_GREMLIN_CONFIG, indent=2) + "\n", encoding="utf-8")
    return config_path


def get_tracked_files(repo_root: Path) -> list[Path]:
    result = subprocess.run(
        ["git", "-C", str(repo_root), "ls-files", "-z"],
        check=False,
        capture_output=True,
    )
    if result.returncode == 0:
        raw_paths = result.stdout.decode("utf-8", errors="replace").split("\x00")
        return [Path(p) for p in raw_paths if p]
    return [p.relative_to(repo_root) for p in repo_root.rglob("*") if p.is_file() and ".git" not in p.parts]


def classify_file(path: Path) -> str | None:
    if path.parts and path.parts[0] == ".gremlin":
        return None
    suffix = path.suffix.lower()
    if suffix in MARKDOWN_EXTENSIONS:
        return "markdown"
    if path.name in CODE_FILENAMES or suffix in CODE_EXTENSIONS:
        return "code"
    return None


def is_test_code_file(path: Path) -> bool:
    in_root_test_dir = len(path.parts) > 1 and path.parts[0] in {"test", "tests"}
    has_test_suffix = path.suffix != "" and path.stem.endswith("_test")
    has_test_prefix = path.suffix != "" and path.stem.startswith("test_")
    return in_root_test_dir or has_test_suffix or has_test_prefix


def read_text_file(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return None


def count_lines(content: str) -> int:
    return len(content.splitlines())


def token_count(encoding: tiktoken.Encoding, content: str) -> int:
    return len(encoding.encode(content, disallowed_special=()))


def language_hint(path: Path) -> str:
    if path.suffix:
        return path.suffix.lstrip(".").lower()
    if path.name in {"Dockerfile", "Containerfile"}:
        return "dockerfile"
    if path.name in {"Makefile", "Justfile"}:
        return "makefile"
    return "text"


def build_combined_text(entries: Iterable[tuple[Path, str]], kind: str) -> str:
    chunks: list[str] = []
    for file_path, content in entries:
        chunks.append("\n---\n")
        chunks.append(f"\n## Source: {file_path.as_posix()}\n\n")
        if kind == "code":
            lang = language_hint(file_path)
            chunks.append(f"```{lang}\n{content.rstrip()}\n```\n")
        else:
            chunks.append(content.rstrip() + "\n")
    return "".join(chunks).lstrip("\n")


def main() -> None:
    args = parse_args()
    repo_root = args.repo_root.resolve() if args.repo_root else discover_repo_root(Path.cwd())
    default_filter_path = ensure_default_gremlin_config(repo_root)
    encoding = tiktoken.get_encoding(args.encoding)
    if args.filter == Path(".gremlin/config.json"):
        filter_path = default_filter_path
    else:
        filter_path = args.filter if args.filter.is_absolute() else repo_root / args.filter
    excluded_file_names = load_excluded_file_names(filter_path)

    tracked_files = sorted(get_tracked_files(repo_root))

    markdown_entries: list[tuple[Path, str]] = []
    code_entries: list[tuple[Path, str]] = []
    metrics: list[FileMetric] = []
    skipped_non_utf8: list[str] = []

    for tracked_path in tracked_files:
        if tracked_path.name in excluded_file_names:
            continue

        kind = classify_file(tracked_path)
        if kind is None:
            continue

        absolute = repo_root / tracked_path
        if not absolute.is_file():
            continue

        content = read_text_file(absolute)
        if content is None:
            skipped_non_utf8.append(tracked_path.as_posix())
            continue

        size_bytes = len(content.encode("utf-8"))
        tokens = token_count(encoding, content)
        lines = count_lines(content)
        is_test_code = kind == "code" and is_test_code_file(tracked_path)
        metrics.append(
            FileMetric(
                path=tracked_path.as_posix(),
                kind=kind,
                is_test_code=is_test_code,
                bytes=size_bytes,
                tokens=tokens,
                lines=lines,
            )
        )

        if kind == "markdown":
            markdown_entries.append((tracked_path, content))
        else:
            code_entries.append((tracked_path, content))

    docs_content = build_combined_text(markdown_entries, "markdown")
    code_content = build_combined_text(code_entries, "code")

    output_dir = repo_root / ".gremlin"
    output_dir.mkdir(parents=True, exist_ok=True)
    docs_path = output_dir / "docs.md"
    code_path = output_dir / "code.md"
    report_path = output_dir / "token_report.json"
    plot_path = output_dir / "token_report_plot.png"

    docs_path.write_text(docs_content, encoding="utf-8")
    code_path.write_text(code_content, encoding="utf-8")

    outputs = {
        "docs.md": {
            "bytes": len(docs_content.encode("utf-8")),
            "tokens": token_count(encoding, docs_content),
            "source_files": len(markdown_entries),
        },
        "code.md": {
            "bytes": len(code_content.encode("utf-8")),
            "tokens": token_count(encoding, code_content),
            "source_files": len(code_entries),
        },
    }

    code_metrics = [item for item in metrics if item.kind == "code"]
    test_code_metrics = [item for item in code_metrics if item.is_test_code]
    non_test_code_metrics = [item for item in code_metrics if not item.is_test_code]

    reporting_totals = {
        "docs": {
            "source_files": len(markdown_entries),
            "tokens": sum(item.tokens for item in metrics if item.kind == "markdown"),
            "bytes": sum(item.bytes for item in metrics if item.kind == "markdown"),
            "lines": sum(item.lines for item in metrics if item.kind == "markdown"),
        },
        "code_non_test": {
            "source_files": len(non_test_code_metrics),
            "tokens": sum(item.tokens for item in non_test_code_metrics),
            "bytes": sum(item.bytes for item in non_test_code_metrics),
            "loc": sum(item.lines for item in non_test_code_metrics),
        },
        "code_test": {
            "source_files": len(test_code_metrics),
            "tokens": sum(item.tokens for item in test_code_metrics),
            "bytes": sum(item.bytes for item in test_code_metrics),
            "loc": sum(item.lines for item in test_code_metrics),
        },
    }

    metrics.sort(key=lambda item: item.tokens, reverse=True)

    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "repo_root": repo_root.as_posix(),
        "encoding": args.encoding,
        "filter": {
            "path": str(filter_path.relative_to(repo_root))
            if filter_path.is_relative_to(repo_root)
            else str(filter_path),
            "exclude_file_names": sorted(excluded_file_names),
        },
        "files": [asdict(item) for item in metrics],
        "outputs": outputs,
        "reporting_totals": reporting_totals,
        "skipped_non_utf8": skipped_non_utf8,
    }
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    generate_token_report_plot(report_path, plot_path)

    print(f"Tracked files scanned: {len(tracked_files)}")
    print(f"Filtered by name: {len(excluded_file_names)}")
    print(f"Markdown files combined: {len(markdown_entries)}")
    print(f"Code files combined: {len(code_entries)}")
    print(f"- non-test code files: {len(non_test_code_metrics)}")
    print(f"- test code files (_test): {len(test_code_metrics)}")
    if skipped_non_utf8:
        print(f"Skipped non-UTF8 files: {len(skipped_non_utf8)}")

    print("\nPer-file metrics:")
    for item in metrics:
        print(
            f"- [{item.kind}] {item.path}: "
            f"{item.bytes} bytes, {item.tokens} tokens, {item.lines} lines"
        )

    print("\nOutput totals:")
    for output_name, stats in outputs.items():
        print(
            f"- {output_name}: {stats['bytes']} bytes, {stats['tokens']} tokens "
            f"(from {stats['source_files']} files)"
        )
    print(f"\nSaved plot to {plot_path}")

    print("\nReporting totals:")
    for label, stats in reporting_totals.items():
        line_label = "loc" if label.startswith("code") else "lines"
        print(
            f"- {label}: {stats['bytes']} bytes, {stats['tokens']} tokens, "
            f"{stats[line_label]} {line_label} (from {stats['source_files']} files)"
        )


if __name__ == "__main__":
    main()
