# Size & performance baseline

Every number here was measured on the machine this tool was written on (Ubuntu,
Qoder CN CLI `1.1.75`), on **2026-10-21 at 20:13 local**, at tree `6a3283e` plus the
uncommitted CLI-first upgrade. The transcripts still carry `1.1.57` desktop sessions
alongside the CLI ones; the reader does not depend on either version string. This
revision is the first measured **after the schema-v2 full reindex** (the backup taken
before it is `usage-20261021-201132-manual.db`).

The source tree is live: it is the transcript directory of the agent the owner is
working with, so it grows between two `find` calls in the same minute — including
during these measurements. These counts are a snapshot, not a property of the tool.
Re-measure before restating any of them; a stale baseline is worse than none,
because it reads as a guarantee.

## The source

```bash
find ~/.qoder-cn/projects -name '*.jsonl' | wc -l     # 492
du -sh ~/.qoder-cn/projects                           # 532M
find ~/.qoder-cn/projects -name 'agent-a*.jsonl' | wc -l   # 445
```

Of those 492 files, **445 are subagent transcripts** named
`agent-a<Type>-<hash>.jsonl` whose records carry the *parent* session id. That is
why 492 files yield 46 sessions, not 492. The file count is exact at the `find`;
the byte size moved across the measurements below.

## A cold sync

```bash
QCUT_DB=/tmp/qcut-base.db qcut sync --quiet           # cold
QCUT_DB=/tmp/qcut-base.db qcut sync --quiet           # warm
du -b /tmp/qcut-base.db
```

Timed with `/usr/bin/time -f '%e s %M KB peak'` on `python3 scripts/qcut-sync.py`:

| | |
|---|---|
| cold (full parse of 492 files, 532 MB) | **7.17 s**, 134,424 KB peak RSS |
| warm (nothing new) | **0.12 s**, 20,868 KB peak RSS |
| database | **28,303,360 bytes** (~27 MB) |

Warm is fast because each file remembers a byte offset in `sync_state` and is
skipped outright when both its size and its mtime are unchanged.

## What gets indexed

```bash
qcut health
```
`qcut stats` prints the span the index actually covers, which is the difference
between "all time" and a claim that can be checked: at this snapshot it read
`range 2026-09-22 16:04 .. 2026-10-21 20:11`.

| table | rows |
|---|---:|
| `sessions` | 46 |
| `model_calls` | 41,233 |
| `tool_calls` | 46,963 |
| `skill_usage` | 5 |
| `agent_usage` | 517 |
| `mcp_usage` | 18 |
| `plugin_usage` | 2 |
| `commands` | 5 |
| `compactions` | 99 |
| `inventory` | 35 |
| `sync_state` | 492 |
| `unparsed` | 1 reason, 6 lines |

The `commands` row was **5 before schema v3**: those five names came from Skill
results, not slash commands, and the v3 repair removes them (the repaired index
reports 0). Everything else in this table is the pre-v3 snapshot.

`model_calls` is 41,233 of the 95,328 `assistant` records on disk: the rest carry
no `message.usage` and are not model responses. A separate scan moments after the
index counted 41,244 usage records — this session writes transcripts while it is
being measured, and the index only sees what existed when it ran. Every
`message.id` among the indexed rows is unique, which is what makes it the primary
key.

Schema v2 adds the two relational ledgers. Of 517 agent runs, 445 match a
response through a non-NULL `parent_tool_use_id`, linking 10,509 responses to a
run; the other 72 runs stay unlinked and show 0 requests with `-` tokens rather
than a guess. The 99 `compactions` rows are all parsed from `compact_boundary`
metadata, with no duplicate and no missing-metadata row, and account for
**33,811,344** tokens dropped in **109.28 s**. That figure is displayed beside
the token totals, never added to them.

The 6 `unparsed` lines are `unparseable_line` — JSON that did not parse at all.
The table carries no file attribution, so a re-read of a rewritten transcript adds
that file's earlier bad lines again: read this number as **at least**, and use
`qcut sync --full` when an exact figure is needed.
No record *type* is unclaimed: the CLI `1.1.75` `ai-title` record (4 lines, carrying
the AI-written title) is now in `NOOP_RECORD_TYPES` — claimed and dropped, with a
sentinel case proving the title is not read — so a genuinely new type still shows
up here as a new reason, and that is the point of the table.

## Route split — tokens and credits are not complements

```bash
qcut models
```

| model | provider | requests | input | output | API | cache read | credits |
|---|---|---|---:|---:|---:|---:|---:|
| `qfmodel` | Built-in | 38,031 | 1,834,621 | 31,068 | 1,865,689 | 1,765,702 | 21,687.46 |
| `qmodel_38max` | Built-in | 1,985 | `—` | `—` | `—` | `—` | 5,932.63 |
| `dfmodel` | Built-in | 1,134 | 60,566,316 | 336,303 | 60,902,619 | 59,805,312 | 254.32 |
| `qmodel` | Built-in | 11 | `—` | `—` | `—` | `—` | 6.78 |
| `auto` | Built-in | 1 | `—` | `—` | `—` | `—` | 1.31 |
| `ark-code-latest` | Volcano ARK | 71 | 12,992,235 | 53,096 | 13,045,331 | 11,740,032 | `-` (no field) |

At this snapshot 660 rows reported at least one non-zero token and 40,573 did not;
most of the latter were hosted calls written without the flag, with a literal 0 in
every token field plus real credits. They are irrecoverable by qcut and are drawn
`—`. The coverage percentages that used to be printed were removed by request; the
underlying rows are unchanged, and a mixed model still shows its measured cells
beside the dashed ones. 589 of the 660 token-covered rows carried credits too — a
hosted response with `QODERCN_EXPOSE_TOKEN_USAGE=1` records tokens **and** credits
in one row. The 71 BYOK rows are the opposite shape: tokens and no `credits` field
at all, stored NULL.

The display keeps three marks because the storage keeps three facts: `-` nothing
contributed to this number, `—` a call (or aggregate of calls) that reported no
token, and a numeral for a measured value. Printing `0` where a route never counts
in tokens, or `0` credits where a route is never billed in credits, are the same
mistake in opposite directions.

Two arithmetic notes that the stored rows prove rather than assume:

- API total = input + output. The largest request on disk is 357,240 input +
  1,463 output = 358,703 API total; adding its 356,352 cache-read tokens would
  make 715,055, which exceeds the model's context — so `input_tokens` already
  contains the cache read, and it is a subset, not an extra.
- Cache hit rate = cache read ÷ (read + miss + write). With no cache field the
  report prints `-`, never a fabricated `0%`.

## Tool mix

| tool | calls | failed |
|---|---:|---:|
| `Bash` | 26,641 | 951 |
| `Edit` | 8,296 | 436 |
| `Read` | 6,930 | 18 |
| `Write` | 1,818 | 43 |
| `Grep` | 857 | 5 |
| `TaskUpdate` | 768 | 0 |
| `TaskCreate` | 539 | 0 |
| `Agent` | 517 | 76 |
| `WebSearch` | 251 | 1 |
| `AskUserQuestion` | 119 | 6 |

Status split across all 46,963 calls: 45,110 `completed`, 1,560 `error`, 264
`backgrounded`, 29 with no result at all (pending).

Subagent types: 391 `general-purpose`, 97 `Explore`, 29 `Plan`.
MCP: 18 calls across 3 servers — `node-repl` 5, `builtin` 8, a QMind plugin server
2, and 3 admin rows with no server or tool. The 5 “slash commands” this snapshot
once reported were Skill results naming their own skill; schema v3 drops them, so
the repaired index has **0** commands.

Separately, of the 46,928 records that carry a `toolUseResult` dict, **20,298 have
no `kind`** — more than a third. Treating those as unfinished would report
finished work as pending, which is why a missing `kind` means *completed*.

## Tests

```bash
TZ=UTC .venv/bin/python -m unittest discover -s scripts/tests
```

| suite | cases |
|---|---:|
| `test_usage.py` | 72 |
| `test_sync.py` | 68 |
| `test_tui.py` | 74 |
| `test_stats.py` | 56 |
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
| **total** | **389** |

Wall time is dominated by Textual's harness: `test_tui.py` alone is the bulk of
the run, the other fourteen together a few seconds.

The suite was green on all three interpreters at the 310-case snapshot, with
`textual 8.2.8` installed in each — that pair is what the CI matrix pins (floor
from `requires-python`, plus the highest version measured). The current 389-case
suite has been re-run locally on 3.13 (before its last two cases) and 3.14; the
3.11 row below is an earlier, smaller-suite run, so CI remains the record for
that end until it is re-measured here:

| interpreter | result |
|---|---|
| Python 3.11.15 | 310 OK (2026-10-12); CI is the record for 389 |
| Python 3.13.14 (the `install.sh` venv) | 387 OK (2026-10-22), 194 s (before the legend and ledger cases) |
| Python 3.14.4 | 389 OK (2026-10-22), 181 s |

`test_config.py` and `test_tui.py` need `textual`; the other thirteen are
stdlib-only.
Running the suite on a bare interpreter without `textual` gives three *errors* in
`test_config.py`, because its wiring cases exec `qcut-tui.py` — that is a missing
dependency, not a Python-version problem, and it is the one way to mistake an
instrument fault for a finding.
