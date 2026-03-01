from __future__ import annotations


def log_case(case_id: str, message: str, *, verbose: bool = False, verbose_only: bool = False) -> None:
    if verbose_only and not verbose:
        return
    print(f"[case {case_id}] {message}")


def log_eval(message: str, *, verbose: bool = False, verbose_only: bool = False) -> None:
    if verbose_only and not verbose:
        return
    print(f"[eval] {message}")


def summarize_result(stdout: str, stderr: str, tail: int = 300) -> str | None:
    out_tail = stdout[-tail:].strip()
    err_tail = stderr[-tail:].strip()
    parts: list[str] = []
    if out_tail:
        parts.append(f"stdout:\n{out_tail}")
    if err_tail:
        parts.append(f"stderr:\n{err_tail}")
    return "\n".join(parts) if parts else None


def log_command_result(
    case_id: str,
    label: str,
    returncode: int,
    stdout: str,
    stderr: str,
    *,
    verbose: bool,
) -> None:
    log_case(case_id, f"{label} exit={returncode}")
    if not verbose:
        return
    summary = summarize_result(stdout, stderr)
    if not summary:
        return
    indented = "\n".join(f"  {line}" for line in summary.splitlines())
    log_case(case_id, f"{label} output:\n{indented}", verbose=verbose, verbose_only=True)
