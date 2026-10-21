# MAINTENANCE.md — qoder-cn-usage-tracker

Procedure file. This is a **router**: the long-form maintenance assets live under
[`docs/maintenance/README.md`](docs/maintenance/README.md), and the rules for working in this repository live in
[`AGENTS.md`](AGENTS.md). Do not duplicate their tables here — a duplicated fact is the thing that
drifts.

The one maintenance risk that matters: **Qoder changing its transcript format**. A renamed field
reads as `NULL`, a renamed record type is silently unclaimed, the panels empty out, `qcut sync`
prints "done", and CI stays green. The format surface this tool depends on is registered in
[`docs/maintenance/qoder-format.md`](docs/maintenance/qoder-format.md), which is generated from the
code — regenerate it with `qcut format --write` and change the registry and the handler **in the
same commit**.

The second risk is specific to this tool and easy to get wrong: tokens and credits are **not
complementary**. With `QODERCN_EXPOSE_TOKEN_USAGE=1` in the CLI's own environment, a hosted response
carries real tokens **and** credits; without it, the same route carries credits and a literal `0` in
every token field, and qcut cannot repair that history. Those rows keep their credits and their
token cells read `—`; there is no coverage percentage or capture-boundary ledger. The reading rules
(`—` / `-` / a number, and the one predicate behind them) are in `AGENTS.md` § Hard rules; the
measured split is in [`docs/maintenance/baseline.md`](docs/maintenance/baseline.md). Never convert
one axis into the other.

## After a Qoder CN CLI upgrade

1. Sync into a throwaway store (`QCUT_DB=/tmp/qcut-check.db qcut sync --quiet`) and read
   `qcut health`. A new `unparsed` reason naming a record type means the format moved: claim it as a
   no-op or handle it, and regenerate [`qoder-format.md`](docs/maintenance/qoder-format.md) with
   `qcut format --write` in the same change.
2. Re-verify token capture in an **isolated shell**: set `QODERCN_EXPOSE_TOKEN_USAGE=1`, make one
   hosted call, sync that session into the temp store, then read `qcut stats` / `qcut models`. The
   new hosted call must show non-zero input / output tokens (credits may be present too). Without
   the flag, the same call stores zero tokens and its cells render `—` — that difference is the
   proof the variable still works.
3. Never probe against the live index, and never write to `~/.qoder-cn`.

## Verification tiers
- **L0** — `python3 -m unittest discover -s scripts/tests` (temp database, no host). Two suites
  need `textual`: `test_tui.py` and `test_config.py`; run it with `.venv/bin/python`.
- **L1** — a throwaway store outside the repository, fed by the real transcripts:
  `QCUT_DB=/tmp/qcut-check.db python3 scripts/qcut-sync.py`. This is where parser changes are
  proved, and the sync report's per-table row counts are how a regression in a table you did not
  touch stays visible.
- **L2** — the real `~/.qoder-cn` transcripts and the real
  `~/.local/share/qoder-cn-usage-tracker/usage.db`. Needs the owner's go-ahead; a destructive step
  takes a snapshot first (`qcut sync --full` does this automatically; retention to
  the newest five is enforced inside `db.snapshot()`, so no snapshot path can grow
  the directory).

## Commands that produce the numbers
```bash
find ~/.qoder-cn/projects -name '*.jsonl' | wc -l      # source file count
QCUT_DB=/tmp/qcut-check.db qcut sync --quiet           # re-index; prints rows per table
QCUT_DB=/tmp/qcut-check.db qcut health                 # settings, table counts, unparsed reasons
QCUT_DB=/tmp/qcut-check.db qcut models                 # usage total, credits, tokens per route
QCUT_DB=/tmp/qcut-check.db qcut env                    # capture-flag state and index freshness
```

`qcut health` is the first thing to read when a panel goes empty: an `unparsed` reason naming a
record type means the format moved, and a table at zero while its neighbours grew means a field
was renamed.

## CI
`.github/workflows/ci.yml` runs the offline layer on a Python 3.11 / 3.14 matrix — the declared
floor and the highest version measured green here. It is not the interpreter you develop on
(`install.sh` builds a 3.13 venv), and a green run says nothing about a version it did not run.
**The runner's clock is UTC**, your shell's probably is not: reproduce a date-sensitive failure
with `TZ=UTC python3 -m unittest discover -s scripts/tests`, and note that CI only ever sees
*pushed* commits.

## Assets
| Document | Read it when |
|---|---|
| [`qoder-format.md`](docs/maintenance/qoder-format.md) | Qoder shipped a new version, or a panel shows nothing it used to show |
| [`compatibility.md`](docs/maintenance/compatibility.md) | Choosing what to claim is supported, or a version string needs updating |
| [`baseline.md`](docs/maintenance/baseline.md) | Restating a size, a timing, a row count, or the route split |

Both carry numbers measured on one machine on one day. The transcript tree is live and grows during
the session you are measuring it in — re-measure rather than copying a number forward.
