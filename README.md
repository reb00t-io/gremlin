# gremlin

Small CLI toolkit for repository analysis and patch-verification workflows.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'
```

## Commands

- `gremlin-scan`
  Scans tracked files and writes artifacts to `.gremlin/`:
  - `.gremlin/docs.md`
  - `.gremlin/code.md`
  - `.gremlin/token_report.json`
  - `.gremlin/token_report_plot.png`

- `gremlin`
  Generates/verifies bug patches for files that have adjacent `_test` files.

Both commands auto-detect repo root from current working directory unless `--repo-root` is provided.

## Config

`gremlin-scan` uses `.gremlin/config.json` and creates it automatically if missing.

## Tests

```bash
pytest -q tests/e2e.py
```

## CI

GitHub Actions runs the e2e test from `.github/workflows/ci.yml` on pushes and pull requests to `main`.
