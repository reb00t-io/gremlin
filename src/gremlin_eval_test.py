from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import gremlin_eval as ge
import gremlin_eval_cases as ge_cases
from bug_generation import fix_patch_path_for_source


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


def test_evaluate_case_1_hides_git_during_tool_run(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    repo_root = tmp_path
    patch = _mk_bug_patch(repo_root)
    state = {"hidden": False, "restored": False}
    target_test_runs = 0

    def fake_hide_git_metadata(_repo_root):  # type: ignore[no-untyped-def]
        state["hidden"] = True
        return Path("/tmp/fake-git-stash")

    def fake_restore_git_metadata(_repo_root, _stash_root):  # type: ignore[no-untyped-def]
        state["hidden"] = False
        state["restored"] = True

    def fake_run_cmd(cmd, cwd, check=False):  # type: ignore[no-untyped-def]
        nonlocal target_test_runs
        if cmd == ["pytest"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if cmd == ["git", "status", "--porcelain"]:
            return SimpleNamespace(returncode=0, stdout=" M src/mod.py\n", stderr="")
        if cmd[:2] == ["git", "apply"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if cmd and cmd[0] == "pytest" and len(cmd) > 1:
            target_test_runs += 1
            return SimpleNamespace(returncode=1 if target_test_runs == 1 else 0, stdout="", stderr="")
        if cmd[:3] == ["git", "checkout", "--"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    def fake_run_agent_impl(*, tool_template, prompt, cwd, case_id):  # type: ignore[no-untyped-def]
        assert state["hidden"] is True
        assert case_id == "1"
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(ge_cases, "hide_git_metadata", fake_hide_git_metadata)
    monkeypatch.setattr(ge_cases, "restore_git_metadata", fake_restore_git_metadata)
    monkeypatch.setattr(ge_cases, "_snapshot_repo_for_debug", lambda _repo_root, *, case_id, bug_id: None)
    monkeypatch.setattr(ge, "run_cmd", fake_run_cmd)
    monkeypatch.setattr(ge_cases, "run_agent_impl", fake_run_agent_impl)

    result = ge.evaluate_case_1(patch_path=patch, repo_root=repo_root, tool_template="echo <PROMPT>")

    assert result["success"] is True
    assert state["restored"] is True
    assert state["hidden"] is False


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
    test_patch = fix_patch_path_for_source(source_file, repo_root, 1)
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


def test_evaluate_case_2_resets_all_changed_tests_before_final_check(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    repo_root = tmp_path
    patch = _mk_bug_patch(repo_root)
    source_file = ge.source_file_for_patch(patch, repo_root)
    test_patch = fix_patch_path_for_source(source_file, repo_root, 1)
    test_patch.parent.mkdir(parents=True, exist_ok=True)
    test_patch.write_text("dummy patch\n", encoding="utf-8")
    (repo_root / "bug_report.txt").write_text("symptoms\n", encoding="utf-8")

    checkout_paths: list[str] = []
    target_test_runs = 0
    status_calls = 0

    def fake_run_cmd(cmd, cwd, check=False):  # type: ignore[no-untyped-def]
        nonlocal target_test_runs, status_calls
        if cmd == ["pytest"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if cmd[:3] == ["git", "checkout", "--"]:
            checkout_paths.append(cmd[3])
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if cmd == ["git", "status", "--porcelain"]:
            status_calls += 1
            if status_calls == 1:
                return SimpleNamespace(returncode=0, stdout=" M src/mod.py\n", stderr="")
            return SimpleNamespace(
                returncode=0,
                stdout=(
                    " M src/mod_test.py\n"
                    " M tests/extra_test.py\n"
                    " M src/notatest.py\n"
                ),
                stderr="",
            )
        if cmd[:2] == ["git", "apply"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if cmd and cmd[0] == "pytest" and len(cmd) > 1:
            target_test_runs += 1
            return SimpleNamespace(returncode=1 if target_test_runs == 1 else 0, stdout="", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    def fake_run_agent(*, tool_template, prompt, cwd, case_id, bug_id):  # type: ignore[no-untyped-def]
        assert case_id == "2"
        assert bug_id == 1
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(ge, "run_cmd", fake_run_cmd)
    monkeypatch.setattr(ge_cases, "run_agent", fake_run_agent)

    result = ge.evaluate_case_2(patch_path=patch, repo_root=repo_root, tool_template="echo <PROMPT>")

    assert result["success"] is True
    assert "src/mod_test.py" in result["reset_test_files"]
    assert "tests/extra_test.py" in result["reset_test_files"]
    assert "src/notatest.py" not in result["reset_test_files"]
    assert "src/mod_test.py" in checkout_paths
    assert "tests/extra_test.py" in checkout_paths
    assert "src/notatest.py" not in checkout_paths


def test_restore_git_metadata_removes_tool_created_git_dir(tmp_path: Path) -> None:
    from gremlin_eval_checkout import hide_git_metadata, restore_git_metadata

    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    git_dir = repo_root / ".git"
    git_dir.mkdir()
    (git_dir / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")

    stash = hide_git_metadata(repo_root)
    assert stash is not None
    assert not git_dir.exists()

    # Simulate tool creating its own .git directory
    git_dir.mkdir()
    (git_dir / "config").write_text("[core]\n", encoding="utf-8")

    restore_git_metadata(repo_root, stash)

    assert git_dir.is_dir()
    assert (git_dir / "HEAD").exists(), "original .git/HEAD should be restored"
    assert not (git_dir / "config").exists(), "tool-created .git/config should be gone"
    assert not stash.exists()


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

    def fake_evaluate_patch(*, source_repo_root, source_patch_path, tool_template, case_id, verbose=False):  # type: ignore[no-untyped-def]
        seen_cases.append(case_id)
        return {
            "timestamp": "now",
            "case": case_id,
            "patch": source_patch_path.relative_to(source_repo_root).as_posix(),
            "success": True,
            "error": None,
        }

    monkeypatch.setattr(ge, "evaluate_patch_at_overview_commit", fake_evaluate_patch)

    code = ge.main()

    assert code == 0
    assert seen_cases == ["1", "2"]
    assert results_path.exists()


def test_run_agent_uses_claude_runner_for_plain_claude(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    called = {"claude": False, "popen": False}
    logs: list[tuple[str, str]] = []

    def fake_run_claude(*, prompt, repo_root, claude_bin=None):  # type: ignore[no-untyped-def]
        called["claude"] = True
        assert prompt == "hello"
        assert repo_root == tmp_path
        assert claude_bin == "claude"
        return SimpleNamespace(returncode=0, stdout="streamed", stderr="")

    def fake_subprocess_popen(*args, **kwargs):  # type: ignore[no-untyped-def]
        called["popen"] = True
        raise AssertionError("subprocess.Popen should not be used for plain 'claude'")

    def fake_log_case(case_id: str, message: str, **kwargs):  # type: ignore[no-untyped-def]
        logs.append((case_id, message))

    monkeypatch.setattr(ge_cases, "run_claude", fake_run_claude)
    monkeypatch.setattr(ge_cases.subprocess, "Popen", fake_subprocess_popen)
    monkeypatch.setattr(ge_cases, "log_case", fake_log_case)

    result = ge_cases.run_agent("claude", "hello", tmp_path, case_id="1", bug_id=1)

    assert called["claude"] is True
    assert called["popen"] is False
    assert ("1", "run agent template=claude") in logs
    assert result.returncode == 0
    assert result.stdout == "streamed"


def test_run_agent_uses_opencode_runner_for_plain_opencode(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    called = {"opencode": False, "popen": False}
    logs: list[tuple[str, str]] = []

    def fake_run_opencode(*, prompt, repo_root, opencode_bin=None):  # type: ignore[no-untyped-def]
        called["opencode"] = True
        assert prompt == "hello"
        assert repo_root == tmp_path
        assert opencode_bin == "opencode"
        return SimpleNamespace(returncode=0, stdout="streamed", stderr="")

    def fake_subprocess_popen(*args, **kwargs):  # type: ignore[no-untyped-def]
        called["popen"] = True
        raise AssertionError("subprocess.Popen should not be used for plain 'opencode'")

    def fake_log_case(case_id: str, message: str, **kwargs):  # type: ignore[no-untyped-def]
        logs.append((case_id, message))

    monkeypatch.setattr(ge_cases, "run_opencode", fake_run_opencode)
    monkeypatch.setattr(ge_cases.subprocess, "Popen", fake_subprocess_popen)
    monkeypatch.setattr(ge_cases, "log_case", fake_log_case)

    result = ge_cases.run_agent("opencode", "hello", tmp_path, case_id="1", bug_id=1)

    assert called["opencode"] is True
    assert called["popen"] is False
    assert ("1", "run agent template=opencode") in logs
    assert result.returncode == 0
    assert result.stdout == "streamed"


def test_run_agent_non_claude_uses_mock_claude_streaming(tmp_path: Path) -> None:
    mock_path = Path(__file__).resolve().parent / "agents" / "mock_claude.py"
    tool_template = (
        f"{shlex.quote(sys.executable)} {shlex.quote(str(mock_path))} "
        "-p <PROMPT> --max-ticks 2 --dangerously-skip-permissions "
        "--output-format stream-json --verbose --include-partial-messages"
    )

    result = ge_cases.run_agent(tool_template=tool_template, prompt="hello from test", cwd=tmp_path, case_id="2", bug_id=2)

    assert result.returncode == 0
    assert "mock claude start" in result.stdout
    assert "tick-0" in result.stdout
    assert "tick-1" in result.stdout


def test_run_agent_hides_git_and_snapshots_after_restore(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    state = {"hidden": False, "restored": False, "snapshotted": False}

    def fake_hide_git_metadata(_repo_root):  # type: ignore[no-untyped-def]
        state["hidden"] = True
        return Path("/tmp/fake-git-stash")

    def fake_snapshot(_repo_root, *, case_id, bug_id):  # type: ignore[no-untyped-def]
        assert state["hidden"] is False
        assert state["restored"] is True
        assert case_id == "1"
        assert bug_id == 1
        state["snapshotted"] = True
        return Path("/tmp/fake-debug.zip")

    def fake_restore_git_metadata(_repo_root, _stash_root):  # type: ignore[no-untyped-def]
        assert state["snapshotted"] is False
        state["hidden"] = False
        state["restored"] = True

    def fake_run_agent_impl(*, tool_template, prompt, cwd, case_id):  # type: ignore[no-untyped-def]
        assert state["hidden"] is True
        assert case_id == "1"
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(ge_cases, "hide_git_metadata", fake_hide_git_metadata)
    monkeypatch.setattr(ge_cases, "_snapshot_repo_for_debug", fake_snapshot)
    monkeypatch.setattr(ge_cases, "restore_git_metadata", fake_restore_git_metadata)
    monkeypatch.setattr(ge_cases, "run_agent_impl", fake_run_agent_impl)

    result = ge_cases.run_agent(tool_template="echo <PROMPT>", prompt="hi", cwd=tmp_path, case_id="1", bug_id=1)

    assert result.returncode == 0
    assert state["snapshotted"] is True
    assert state["restored"] is True
    assert state["hidden"] is False


def test_evaluate_patch_at_overview_commit_missing_overview(tmp_path: Path) -> None:
    repo_root = tmp_path
    patch = _mk_bug_patch(repo_root)

    result = ge.evaluate_patch_at_overview_commit(
        source_repo_root=repo_root,
        source_patch_path=patch,
        tool_template="echo <PROMPT>",
        case_id="1",
    )

    assert result["success"] is False
    assert str(result["error"]).startswith("missing_overview:")


def test_evaluate_patch_at_overview_commit_malformed_overview(tmp_path: Path) -> None:
    repo_root = tmp_path
    patch = _mk_bug_patch(repo_root)
    overview_path = repo_root / ".gremlin" / "bugs" / "src" / "mod.py.overview-1.json"
    overview_path.parent.mkdir(parents=True, exist_ok=True)
    overview_path.write_text("not valid json{{{", encoding="utf-8")

    result = ge.evaluate_patch_at_overview_commit(
        source_repo_root=repo_root,
        source_patch_path=patch,
        tool_template="echo <PROMPT>",
        case_id="1",
    )

    assert result["success"] is False
    assert str(result["error"]).startswith("invalid_overview_json:")


def test_evaluate_patch_at_overview_commit_uses_real_checkout(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    source_repo_root = Path(__file__).resolve().parents[1]
    base_commit = ge.run_cmd(["git", "rev-parse", "HEAD"], cwd=source_repo_root, check=True).stdout.strip()

    token = uuid4().hex[:8]
    source_file = Path(f"src/eval_checkout_{token}.py")
    patch_path = source_repo_root / ".gremlin" / "bugs" / "src" / f"eval_checkout_{token}.py.bug-1.patch"
    overview_path = source_repo_root / ".gremlin" / "bugs" / "src" / f"eval_checkout_{token}.py.overview-1.json"

    patch_path.parent.mkdir(parents=True, exist_ok=True)
    patch_path.write_text("diff --git a/src/dummy.py b/src/dummy.py\n", encoding="utf-8")
    overview_payload = {
        "created_at": "2026-03-01T00:00:00+00:00",
        "base_commit": base_commit,
        "source_file": source_file.as_posix(),
        "bug_patch": patch_path.relative_to(source_repo_root).as_posix(),
        "test_patch": ".gremlin/bugs/src/dummy.test-1.patch",
        "bug_report": "bug_report.txt",
    }
    overview_path.write_text(json.dumps(overview_payload, indent=2) + "\n", encoding="utf-8")

    def fake_evaluate_patch(*, patch_path, repo_root, tool_template, **kwargs):  # type: ignore[no-untyped-def]
        checked_out_commit = ge.run_cmd(["git", "rev-parse", "HEAD"], cwd=repo_root, check=True).stdout.strip()
        assert checked_out_commit == base_commit
        assert patch_path == patch_path_outer
        assert patch_path.is_file()
        return {
            "timestamp": "now",
            "case": "1",
            "patch": patch_path.as_posix(),
            "success": True,
            "error": None,
        }

    patch_path_outer = patch_path
    monkeypatch.setattr(ge, "evaluate_case_1", fake_evaluate_patch)

    try:
        result = ge.evaluate_patch_at_overview_commit(
            source_repo_root=source_repo_root,
            source_patch_path=patch_path,
            tool_template="echo <PROMPT>",
            case_id="1",
        )
    finally:
        if patch_path.exists():
            patch_path.unlink()
        if overview_path.exists():
            overview_path.unlink()

    assert result["success"] is True
    assert result["base_commit"] == base_commit
