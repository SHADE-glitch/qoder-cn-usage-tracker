# Size & performance baseline

Every number here was measured on the machine this tool was written on (Ubuntu,
Qoder CN agent version `1.1.57`), on **2026-10-13 at 10:28 local**, with the tree at `102b446`.

The source tree is live: it is the transcript directory of the agent the owner is
working with, so it grows between two `find` calls in the same minute. These
counts are a snapshot, not a property of the tool. Re-measure before restating
any of them; a stale baseline is worse than none, because it reads as a guarantee.

## The source

```bash
find ~/.qoder-cn/projects -name '*.jsonl' | wc -l     # 183
du -sh ~/.qoder-cn/projects                           # 384M
find ~/.qoder-cn/projects -name 'agent-a*.jsonl' | wc -l   # 143
```

Of those 183 files, **143 are subagent transcripts** named
`agent-a<Type>-<hash>.jsonl` whose records carry the *parent* session id. That is
why 183 files yield 39 sessions, not 183.

## A cold sync

```bash
QCUT_DB=/tmp/qcut-base.db qcut sync --quiet           # cold
QCUT_DB=/tmp/qcut-base.db qcut sync --quiet           # warm
du -h /tmp/qcut-base.db
```

Timed with `/usr/bin/time -f '%e s %M KB peak'` on `python3 scripts/qcut-sync.py`:

| | |
|---|---|
| cold (full parse of 384 MB) | **4.81 s**, 134,128 KB peak RSS |
| warm (nothing new) | **0.10 s**, 20,188 KB peak RSS |
| database | **20 MB** |

Warm is fast because each file remembers a byte offset in `sync_state` and is
skipped outright when both its size and its mtime are unchanged.

## What gets indexed

```bash
qcut health
```
`qcut stats` prints the span the index actually covers, which is the difference
between "all time" and a claim that can be checked: at this snapshot it read
`range 2026-09-22 16:04 .. 2026-10-13 10:28`.

| table | rows |
|---|---:|
| `sessions` | 39 |
| `model_calls` | 31,644 |
| `tool_calls` | 35,666 |
| `skill_usage` | 2 |
| `agent_usage` | 143 |
| `mcp_usage` | 10 |
| `plugin_usage` | 1 |
| `commands` | 2 |
| `inventory` | 35 |
| `sync_state` | 183 |
| `unparsed` | 1 reason, 6 lines |

`model_calls` is 31,644 of the 73,067 `assistant` records on disk: the rest carry
no `message.usage` and are not model responses. Every `message.id` among the
31,644 is unique, which is what makes it the primary key.

The 6 `unparsed` lines are `unparseable_line` — JSON that did not parse at all.
The table carries no file attribution, so a re-read of a rewritten transcript adds
that file's earlier bad lines again: read this number as **at least**, and use
`qcut sync --full` when an exact figure is needed.
No record *type* is unclaimed: a renamed type would show up here as a new reason,
and that is the point of the table.

## Route split — why the report has two dash marks

```bash
qcut models
```

| model | provider | requests | tokens | credits |
|---|---|---:|---|---|
| `qfmodel` | builtin | 29,576 | `—` (all four fields report 0) | 18,321.35 |
| `qmodel_38max` | builtin | 1,985 | `—` | 5,932.63 |
| `qmodel` | builtin | 11 | `—` | 6.78 |
| `auto` | builtin | 1 | `—` | 1.31 |
| `ark-code-latest` | Volcano ARK | 71 | 12,992,235 in / 53,096 out / 11,740,032 read | `-` (no field) |

This is the clearest statement of the split, and it is a **complementary** one,
not a missing one:

- 31,573 responses on Qoder's own routes carry a real credit and a **literal `0`**
  in every token field.
- 71 responses on the BYOK route carry real tokens and **no `credits` field at
  all**, which is stored NULL.
- The two ways of asking "did this row report a token?" (`credits IS NULL` and
  "some token field is non-zero") disagree on **0 rows** here, which is why
  `fmt_tok` and the `Tok reported` column can be driven by one predicate.

So the report distinguishes three states: `-` nothing contributed to this number,
`—` there is a number and it is a route-level zero, and a numeral. Printing `0`
where a route never counts in tokens, or `0` credits where a route is never billed
in credits, are the same mistake in opposite directions.

## Tool mix

| tool | calls | failed |
|---|---:|---:|
| `Bash` | 20,107 | 666 |
| `Edit` | 7,300 | 408 |
| `Read` | 4,882 | 13 |
| `Write` | 981 | 41 |
| `Grep` | 788 | 4 |
| `TaskUpdate` | 702 | 0 |
| `TaskCreate` | 493 | 0 |
| `Agent` | 143 | 3 |

Status split across all 35,666 calls: 34,267 `completed`, 1,157 `error`,
220 `backgrounded`, 22 with no result at all (pending).

Separately, of the 35,636 records that carry a `toolUseResult`, **15,598 have no
`kind`** — nearly half. Treating those as unfinished reported finished work as
pending, which is why a missing `kind` means *completed*.

## Tests

```bash
python3 -m unittest discover -s scripts/tests
```

| suite | cases |
|---|---:|
| `test_usage.py` | 54 |
| `test_sync.py` | 56 |
| `test_tui.py` | 52 |
| `test_stats.py` | 42 |
| `test_config.py` | 19 |
| `test_snapshots.py` | 14 |
| `test_format_registry.py` | 10 |
| `test_maintenance_docs.py` | 11 |
| `test_hermetic.py` | 13 |
| `test_privacy.py` | 15 |
| `test_dispatcher.py` | 9 |
| `test_readme_bilingual.py` | 7 |
| `test_readme_counts.py` | 6 |
| `test_record_coverage.py` | 6 |
| `test_conventions.py` | 9 |
| **total** | **323** |

Wall time is dominated by Textual's harness: `test_tui.py` alone is 71 s, the other
thirteen together ~9 s.

Run green on all three interpreters with `textual 8.2.8` installed in each — this
is what the CI matrix pins (floor from `requires-python`, plus the highest version
measured):

| interpreter | result |
|---|---|
| Python 3.11.15 | 310 OK |
| Python 3.13.14 (the `install.sh` venv) | 310 OK |
| Python 3.14.4 | 310 OK |

`test_config.py` and `test_tui.py` need `textual`; the other thirteen are
stdlib-only.
Running the suite on a bare interpreter without `textual` gives three *errors* in
`test_config.py`, because its wiring cases exec `qcut-tui.py` — that is a missing
dependency, not a Python-version problem, and it is the one way to mistake an
instrument fault for a finding.
