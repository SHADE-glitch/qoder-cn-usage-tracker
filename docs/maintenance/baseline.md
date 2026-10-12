# Size & performance baseline

Every number here was measured on the machine this tool was written on (Ubuntu,
Qoder CN agent version `1.1.57`), on **2026-10-12 at 22:34 local**.

The source tree is live: it is the transcript directory of the agent the owner is
working with, so it grows between two `find` calls in the same minute. These
counts are a snapshot, not a property of the tool. Re-measure before restating
any of them; a stale baseline is worse than none, because it reads as a guarantee.

## The source

```bash
find ~/.qoder-cn/projects -name '*.jsonl' | wc -l     # 176
du -sh ~/.qoder-cn/projects                           # 370M
find ~/.qoder-cn/projects -name 'agent-a*.jsonl' | wc -l   # 136
```

Of those 176 files, **136 are subagent transcripts** named
`agent-a<Type>-<hash>.jsonl` whose records carry the *parent* session id. That is
why 176 files yield 39 sessions, not 176.

## A cold sync

```bash
QCUT_DB=/tmp/qcut-base.db qcut sync --quiet           # cold
QCUT_DB=/tmp/qcut-base.db qcut sync --quiet           # warm
du -h /tmp/qcut-base.db
```

Timed with `/usr/bin/time -f '%e s %M KB peak'` on `python3 scripts/qcut-sync.py`:

| | |
|---|---|
| cold (full parse of 370 MB) | **4.76 s**, 133,732 KB peak RSS |
| warm (nothing new) | **0.12 s**, 20,132 KB peak RSS |
| database | **19 MB** |

Warm is fast because each file remembers a byte offset in `sync_state` and is
skipped outright when both its size and its mtime are unchanged.

## What gets indexed

```bash
qcut health
```

| table | rows |
|---|---:|
| `sessions` | 39 |
| `model_calls` | 30,478 |
| `tool_calls` | 34,432 |
| `skill_usage` | 2 |
| `agent_usage` | 136 |
| `mcp_usage` | 10 |
| `plugin_usage` | 1 |
| `commands` | 2 |
| `inventory` | 35 |
| `sync_state` | 176 |
| `unparsed` | 1 reason, 6 lines |

`model_calls` is 30,478 of the 70,587 `assistant` records on disk: the rest carry
no `message.usage` and are not model responses. Every `message.id` among the
30,478 is unique, which is what makes it the primary key.

The 6 `unparsed` lines are `unparseable_line` — JSON that did not parse at all.
No record *type* is unclaimed: a renamed type would show up here as a new reason,
and that is the point of the table.

## Route split — why the report has two dash marks

```bash
qcut models
```

| model | provider | requests | tokens | credits |
|---|---|---:|---|---|
| `qfmodel` | builtin | 28,410 | `—` (all four fields report 0) | 17,700.03 |
| `qmodel_38max` | builtin | 1,985 | `—` | 5,932.63 |
| `qmodel` | builtin | 11 | `—` | 6.78 |
| `auto` | builtin | 1 | `—` | 1.31 |
| `ark-code-latest` | Volcano ARK | 71 | 12,992,235 in / 53,096 out / 11,740,032 read | `-` (no field) |

This is the clearest statement of the split, and it is a **complementary** one,
not a missing one:

- 30,407 responses on Qoder's own routes carry a real credit and a **literal `0`**
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
| `Bash` | 19,311 | 642 |
| `Edit` | 7,094 | 393 |
| `Read` | 4,718 | 13 |
| `Write` | 958 | 40 |
| `Grep` | 774 | 3 |
| `TaskUpdate` | 687 | 0 |
| `TaskCreate` | 485 | 0 |
| `Agent` | 136 | 3 |

Status split across all 34,432 calls: 33,082 `completed`, 1,116 `error`,
213 `backgrounded`, 21 with no result at all (pending).

Separately, of the 34,417 records that carry a `toolUseResult`, **15,161 have no
`kind`** — nearly half. Treating those as unfinished reported finished work as
pending, which is why a missing `kind` means *completed*.

## Tests

```bash
python3 -m unittest discover -s scripts/tests
```

| suite | cases |
|---|---:|
| `test_usage.py` | 53 |
| `test_sync.py` | 43 |
| `test_tui.py` | 45 |
| `test_stats.py` | 33 |
| `test_config.py` | 19 |
| `test_snapshots.py` | 14 |
| `test_format_registry.py` | 10 |
| `test_maintenance_docs.py` | 11 |
| `test_hermetic.py` | 10 |
| `test_privacy.py` | 9 |
| `test_dispatcher.py` | 9 |
| `test_readme_bilingual.py` | 7 |
| `test_readme_counts.py` | 6 |
| `test_record_coverage.py` | 6 |
| **total** | **275** |

Wall time is dominated by Textual's harness: `test_tui.py` alone is 71 s, the other
thirteen together ~9 s.

Run green on all three interpreters with `textual 8.2.8` installed in each — this
is what the CI matrix pins (floor from `requires-python`, plus the highest version
measured):

| interpreter | result |
|---|---|
| Python 3.11.15 | 275 OK |
| Python 3.13.14 (the `install.sh` venv) | 275 OK |
| Python 3.14.4 | 275 OK |

`test_config.py` and `test_tui.py` need `textual`; the other twelve are
stdlib-only.
Running the suite on a bare interpreter without `textual` gives three *errors* in
`test_config.py`, because its wiring cases exec `qcut-tui.py` — that is a missing
dependency, not a Python-version problem, and it is the one way to mistake an
instrument fault for a finding.
