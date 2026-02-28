from __future__ import annotations

import json
from pathlib import Path

from gremlin_scan import (
    DEFAULT_GREMLIN_CONFIG,
    build_combined_text,
    classify_file,
    ensure_default_gremlin_config,
    is_test_code_file,
    load_excluded_file_names,
)


def test_load_excluded_file_names_from_dict(tmp_path: Path) -> None:
    filter_path = tmp_path / "config.json"
    filter_path.write_text(
        json.dumps({"exclude_file_names": ["a.txt", "b.txt", "", 1]}), encoding="utf-8"
    )

    excluded = load_excluded_file_names(filter_path)
    assert excluded == {"a.txt", "b.txt"}


def test_load_excluded_file_names_from_list(tmp_path: Path) -> None:
    filter_path = tmp_path / "config.json"
    filter_path.write_text(json.dumps(["x.md", "y.py"]), encoding="utf-8")

    excluded = load_excluded_file_names(filter_path)
    assert excluded == {"x.md", "y.py"}


def test_ensure_default_gremlin_config_creates_file(tmp_path: Path) -> None:
    config_path = ensure_default_gremlin_config(tmp_path)
    assert config_path == tmp_path / ".gremlin" / "config.json"
    assert config_path.is_file()

    content = json.loads(config_path.read_text(encoding="utf-8"))
    assert content == DEFAULT_GREMLIN_CONFIG


def test_classify_file_ignores_gremlin_dir() -> None:
    assert classify_file(Path(".gremlin/token_report.json")) is None


def test_classify_file_detects_markdown_and_code() -> None:
    assert classify_file(Path("README.md")) == "markdown"
    assert classify_file(Path("src/main.py")) == "code"
    assert classify_file(Path("Dockerfile")) == "code"
    assert classify_file(Path("notes.txt")) is None


def test_is_test_code_file_supports_test_and_tests_dirs() -> None:
    assert is_test_code_file(Path("test/example.py"))
    assert is_test_code_file(Path("tests/example.py"))
    assert is_test_code_file(Path("src/example_test.py"))
    assert is_test_code_file(Path("src/test_example.py"))
    assert is_test_code_file(Path("src/test_example.go"))
    assert not is_test_code_file(Path("src/example.py"))


def test_build_combined_text_formats_code_block() -> None:
    output = build_combined_text([(Path("src/app.py"), "print('hi')\n")], "code")
    assert "## Source: src/app.py" in output
    assert "```py" in output
    assert "print('hi')" in output
