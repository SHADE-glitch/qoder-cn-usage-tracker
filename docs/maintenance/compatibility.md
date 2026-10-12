# Compatibility

What this tool claims to run on, and what each claim rests on. Every line is a measurement with a
date, not a policy — re-measure before restating any of it.

## Python

- **Floor: 3.11** — `requires-python` in `pyproject.toml`, and the README's Requirements table.
- **CI runs 3.11 and 3.14** (`.github/workflows/ci.yml`) — the declared floor and the highest
  version measured green here. A declared range is covered at both ends.
- **The development venv is 3.13** (`install.sh`). It is a middle version that speaks for neither
  end of the matrix: a green run there says nothing about 3.11 or 3.14.

| Interpreter | Suite |
|---|---|
| Python 3.11.15 | green |
| Python 3.13.14 (the `install.sh` venv) | green |
| Python 3.14.4 | green |

Measured 2026-10-12 with `textual 8.2.8` installed in each. The per-suite counts are in
[`baseline.md`](baseline.md).

## Qoder CN

- The schema is verified against **the transcripts on this machine**, not against a version number:
  Qoder CN agent version `1.1.57` was the one measured (2026-10-12). No transcript line carries a
  version the tool relies on, so the version string is context, not a contract — see
  [`qoder-format.md`](qoder-format.md) for what is actually read.

## Textual

- The interactive TUI needs `textual>=8.2,<9` (`requirements.txt`), the only non-stdlib runtime
  dependency. Measured green on `textual 8.2.8`.
- `test_config.py` and `test_tui.py` need `textual`; the other twelve suites are standard-library
  only. On a bare interpreter without `textual`, `test_config.py` reports three *errors* — a missing
  package, not a Python-version failure.

## SQLite

- The standard library's `sqlite3`; no third-party driver. The index carries schema version `1`
  (`db.SCHEMA_VERSION`).

## Linux

- Developed and verified on Ubuntu. Other distributions are **unverified**.
