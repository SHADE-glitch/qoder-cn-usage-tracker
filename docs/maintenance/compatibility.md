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
| Python 3.11.15 | 310 OK (2026-10-12; CI is the record for the 347-case suite) |
| Python 3.13.14 (the `install.sh` venv) | 347 OK (2026-10-21) |
| Python 3.14.4 | 310 OK (2026-10-12; CI is the record for the 347-case suite) |

The 310-case row is the last local measurement for that interpreter with `textual 8.2.8`; the suite
has since grown to 347 cases. Re-run before claiming a local green on 3.11 or 3.14. The per-suite
counts are in [`baseline.md`](baseline.md).

## Qoder CN

- The tool targets the **Qoder CN CLI**. The schema is verified against **the transcripts on this
  machine**, not against a version number: CLI version `1.1.75` was the one measured (2026-10-21).
  The same tree still carries desktop `1.1.57` sessions, which is the useful part — the reader
  handles both shapes. No transcript line carries a version the tool relies on, so the version
  string is context, not a contract — see [`qoder-format.md`](qoder-format.md) for what is actually
  read.
- **Hosted token capture depends on the CLI's environment.** The CLI writes real input / output /
  cache-read / cache-creation tokens for Qoder-hosted routes only when its own process sees
  `QODERCN_EXPOSE_TOKEN_USAGE=1` (`db.TOKEN_ENV_NAME`). qcut never sets it, never edits a shell
  profile, and cannot repair transcripts written without it — those rows hold literal zeros and
  real credits. Coverage (`token-covered / requests`) is the surface that says how much of the
  index was captured this way; `qcut env` reports the flag state and the line to add.

## Textual

- The interactive TUI needs `textual>=8.2,<9` (`requirements.txt`), the only non-stdlib runtime
  dependency. Measured green on `textual 8.2.8`.
- `test_config.py` and `test_tui.py` need `textual`; the other 13 suites are standard-library
  only. On a bare interpreter without `textual`, `test_config.py` reports three *errors* — a missing
  package, not a Python-version failure.

## SQLite

- The standard library's `sqlite3`; no third-party driver. The index carries schema version `1`
  (`db.SCHEMA_VERSION`). Coverage is derived at query time, so an index written before the
  CLI-first upgrade keeps working without a migration.

## Linux

- Developed and verified on Ubuntu. Other distributions are **unverified**.
