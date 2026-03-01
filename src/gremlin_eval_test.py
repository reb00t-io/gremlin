from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import gremlin_eval as ge


def _mk_bug_patch(repo_root: Path, rel_source: str = "src/mod.py", patch_no: int = 1) -> Path:
    patch = repo_root / ".gremlin" / "bugs" / f"{rel_source}.bug-{patch_no}.patch"
    patch.parent.mkdir(parents=True, exist_ok=True)
    patch.write_text("diff --git a/src/mod.py b/src/mod.py\n", encoding="utf-8")
    return patch


def test_evaluate_patch_routes_by_case(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    called: list[str] = []

    def fake_case_1(*args, **kwargs):  # type: ignore[no-untyped-def]
        called.append("1")
        return {"success": True}

    def fake_case_2(*args, **kwargs):  # type: ignore[no-untyped-def]
        called.append("2")
        return {"success": True}

    monkeypatch.setattr(ge, "evaluate_case_1", fake_case_1)
    monkeypatch.setattr(ge, "evaluate_case_2", fake_case_2)

    repo_root = Path("/")
    patch = Path("/.gremlin/bugs/src/mod.py.bug-1.patch")

    ge.evaluate_patch(patch_path=patch, repo_root=repo_root, tool_template="tool", case_id="1")
    ge.evaluate_patch(patch_path=patch, repo_root=repo_root, tool_template="tool", case_id="2")

    assert called == ["1", "2"]


def test_evaluate_case_1_rejects_existing_bug_report(tmp_path: Path) -> None:
    repo_root = tmp_path
    patch = _mk_bug_patch(repo_root)
    (repo_root / "bug_report.txt").write_text("should not exist in case1\n", encoding="utf-8")

    result = ge.evaluate_case_1(patch_path=patch, repo_root=repo_root, tool_template="echo <PROMPT>")

    assert result["success"] is False
    assert result["error"] == "unexpected_bug_report_present_case1"


def test_evaluate_case_2_missing_test_patch(tmp_path: Path) -> None:
    repo_root = tmp_path
    patch = _mk_bug_patch(repo_root)

    result = ge.evaluate_case_2(patch_path=patch, repo_root=repo_root, tool_template="echo <PROMPT>")

    assert result["success"] is False
    assert result["error"] == "missing_test_patch"


def test_evaluate_case_2_missing_bug_report(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    repo_root = tmp_path
    patch = _mk_bug_patch(repo_root)
    source_file = ge.source_file_for_patch(patch, repo_root)
    test_patch = ge.fix_patch_path_for_source(source_file, repo_root, 1)
    test_patch.parent.mkdir(parents=True, exist_ok=True)
    test_patch.write_text("dummy patch\n", encoding="utf-8")

    def fake_run_cmd(cmd, cwd, check=False):  # type: ignore[no-untyped-def]
        if cmd == ["pytest"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if cmd and cmd[0] == "pytest" and len(cmd) > 1:
            return SimpleNamespace(returncode=1, stdout="failing test", stderr="")
        if cmd[:2] == ["git", "apply"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if cmd[:3] == ["git", "checkout", "--"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(ge, "run_cmd", fake_run_cmd)

    result = ge.evaluate_case_2(patch_path=patch, repo_root=repo_root, tool_template="echo <PROMPT>")

    assert result["success"] is False
    assert result["error"] == "missing_bug_report_in_case2"


def test_cleanup_bug_report_removes_file(tmp_path: Path) -> None:
    repo_root = tmp_path
    report = repo_root / "bug_report.txt"
    report.write_text("temporary\n", encoding="utf-8")

    ge.cleanup_bug_report(repo_root)

    assert not report.exists()


def test_main_runs_both_cases_by_default(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    repo_root = tmp_path
    patch = _mk_bug_patch(repo_root)
    results_path = repo_root / ".gremlin" / "eval_results.jsonl"

    monkeypatch.setattr(
        ge,
        "parse_args",
        lambda: SimpleNamespace(
            tool_command="echo <PROMPT>",
            limit=10,
            seed=42,
            case="both",
            repo_root=repo_root,
            results_file=Path(".gremlin/eval_results.jsonl"),
        ),
    )
    monkeypatch.setattr(ge, "discover_repo_root", lambda _cwd: repo_root)
    monkeypatch.setattr(ge, "list_bug_patches", lambda _repo_root: [patch])

    seen_cases: list[str] = []

    def fake_evaluate_patch(*, patch_path, repo_root, tool_template, case_id):  # type: ignore[no-untyped-def]
        seen_cases.append(case_id)
        return {
            "timestamp": "now",
            "case": case_id,
            "patch": patch_path.relative_to(repo_root).as_posix(),
            "success": True,
            "error": None,
        }

    monkeypatch.setattr(ge, "evaluate_patch", fake_evaluate_patch)

    code = ge.main()

    assert code == 0
    assert seen_cases == ["1", "2"]
    assert results_path.exists()
