# MAINTENANCE.md — qoder-cn-usage-tracker

Procedure file. This is a **router**: the long-form maintenance assets live under
[`docs/maintenance/`](docs/maintenance/), and the rules for working in this repository live in
[`AGENTS.md`](AGENTS.md). Do not duplicate their tables here — a duplicated fact is the thing that
drifts.

The one maintenance risk that matters: **Qoder changing its transcript format**. A renamed field
reads as `NULL`, a renamed record type is silently unclaimed, the panels empty out, `qcut sync`
prints "done", and CI stays green. The format surface this tool depends on is registered in
[`docs/maintenance/qoder-format.md`](docs/maintenance/qoder-format.md), which is generated from the
code — regenerate it with `qcut format --write` and change the registry and the handler **in the
same commit**.

The second risk is specific to this tool and easy to get wrong: the two measure axes are
**complementary**, not partially missing. Qoder's own routes report credits and a literal `0` for
every token field; a BYOK route reports tokens and no `credits` field at all. The reading rules
(`—` / `-` / a number, and the one predicate behind them) are in `AGENTS.md` § Hard rules; the
measured split is in [`docs/maintenance/baseline.md`](docs/maintenance/baseline.md). Never convert
one axis into the other.

## Verification tiers
- **L0** — `python3 -m unittest discover -s scripts/tests` (temp database, no host). Two suites
  need `textual`: `test_tui.py` and `test_config.py`; run it with `.venv/bin/python`.
- **L1** — a throwaway store outside the repository, fed by the real transcripts:
  `QCUT_DB=/tmp/qcut-check.db python3 scripts/qcut-sync.py`. This is where parser changes are
  proved, and the sync report's per-table row counts are how a regression in a table you did not
  touch stays visible.
- **L2** — the real `~/.qoder-cn` transcripts and the real
  `~/.local/share/qoder-cn-usage-tracker/usage.db`. Needs the owner's go-ahead; a destructive step
  takes a snapshot first (`qcut sync --full` does this automatically, keeping the last five).

## Commands that produce the numbers
```bash
find ~/.qoder-cn/projects -name '*.jsonl' | wc -l      # source file count
QCUT_DB=/tmp/qcut-check.db qcut sync --quiet           # re-index; prints rows per table
QCUT_DB=/tmp/qcut-check.db qcut health                 # settings, table counts, unparsed reasons
QCUT_DB=/tmp/qcut-check.db qcut models                 # the credits/token split, per route
```

`qcut health` is the first thing to read when a panel goes empty: an `unparsed` reason naming a
record type means the format moved, and a table at zero while its neighbours grew means a field
was renamed.

## CI
`.github/workflows/ci.yml` runs the offline layer on a Python 3.11 / 3.14 matrix — the declared
floor and the highest version measured green here. It is not the interpreter you develop on
(`install.sh` builds a 3.13 venv), and a green run says nothing about a version it did not run.

## Assets
| Document | Read it when |
|---|---|
| [`qoder-format.md`](docs/maintenance/qoder-format.md) | Qoder shipped a new version, or a panel shows nothing it used to show |
| [`baseline.md`](docs/maintenance/baseline.md) | Restating a size, a timing, a row count, or the route split |

Both carry numbers measured on one machine on one day. The transcript tree is live and grows during
the session you are measuring it in — re-measure rather than copying a number forward.
