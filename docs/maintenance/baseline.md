# Size & performance baseline

Every number here was measured on the machine this tool was written on (Ubuntu,
Qoder CN CLI `1.1.75`), on **2026-10-21 at 18:08 local**, at tree `6a3283e` plus the
uncommitted CLI-first upgrade. The transcripts still carry `1.1.57` desktop sessions
alongside the CLI ones; the reader does not depend on either version string.

The source tree is live: it is the transcript directory of the agent the owner is
working with, so it grows between two `find` calls in the same minute — including
during these measurements. These counts are a snapshot, not a property of the tool.
Re-measure before restating any of them; a stale baseline is worse than none,
because it reads as a guarantee.

## The source

```bash
find ~/.qoder-cn/projects -name '*.jsonl' | wc -l     # 489
du -sh ~/.qoder-cn/projects                           # 522M
find ~/.qoder-cn/projects -name 'agent-a*.jsonl' | wc -l   # 444
```

Of those 489 files, **444 are subagent transcripts** named
`agent-a<Type>-<hash>.jsonl` whose records carry the *parent* session id. That is
why 489 files yield 44 sessions, not 489. The file count is exact at the `find`;
the byte size moved from 521M to 522M across the measurements below.

## A cold sync

```bash
QCUT_DB=/tmp/qcut-base.db qcut sync --quiet           # cold
QCUT_DB=/tmp/qcut-base.db qcut sync --quiet           # warm
du -b /tmp/qcut-base.db
```

Timed with `/usr/bin/time -f '%e s %M KB peak'` on `python3 scripts/qcut-sync.py`:

| | |
|---|---|
| cold (full parse of 522 MB) | **6.79 s**, 133,928 KB peak RSS |
| warm (nothing new) | **0.10 s**, 20,768 KB peak RSS |
| database | **26,189,824 bytes** (~25 MB) |

Warm is fast because each file remembers a byte offset in `sync_state` and is
skipped outright when both its size and its mtime are unchanged.

## What gets indexed

```bash
qcut health
```
`qcut stats` prints the span the index actually covers, which is the difference
between "all time" and a claim that can be checked: at this snapshot it read
`range 2026-09-22 16:04 .. 2026-10-21 18:08`.

| table | rows |
|---|---:|
| `sessions` | 44 |
| `model_calls` | 40,544 |
| `tool_calls` | 46,059 |
| `skill_usage` | 2 |
| `agent_usage` | 516 |
| `mcp_usage` | 18 |
| `plugin_usage` | 1 |
| `commands` | 2 |
| `inventory` | 35 |
| `sync_state` | 489 |
| `unparsed` | 1 reason, 6 lines |

`model_calls` is 40,544 of the 93,708 `assistant` records on disk: the rest carry
no `message.usage` and are not model responses. A separate scan moments after the
index counted 40,549–40,550 usage records — this session writes transcripts while
it is being measured, and the index only sees what existed when it ran. Every
`message.id` among the indexed rows is unique, which is what makes it the primary
key.

The 6 `unparsed` lines are `unparseable_line` — JSON that did not parse at all.
The table carries no file attribution, so a re-read of a rewritten transcript adds
that file's earlier bad lines again: read this number as **at least**, and use
`qcut sync --full` when an exact figure is needed.
No record *type* is unclaimed: the CLI `1.1.75` `ai-title` record (4 lines, carrying
the AI-written title) is now in `NOOP_RECORD_TYPES` — claimed and dropped, with a
sentinel case proving the title is not read — so a genuinely new type still shows
up here as a new reason, and that is the point of the table.

## Route split — coverage, not complementarity

```bash
qcut models
```

| model | provider | requests | coverage | tokens | credits |
|---|---|---|---:|---|---|
| `qfmodel` | builtin | 37,993 | 0.0% (0/37,993) | `—` | 21,678.87 |
| `qmodel_38max` | builtin | 1,985 | 0.0% (0/1,985) | `—` | 5,932.63 |
| `dfmodel` | builtin | 483 | 0.0% (0/483) | `—` | 169.66 |
| `qmodel` | builtin | 11 | 0.0% (0/11) | `—` | 6.78 |
| `auto` | builtin | 1 | 0.0% (0/1) | `—` | 1.31 |
| `ark-code-latest` | Volcano ARK | 71 | 100.0% (71/71) | 12,992,235 in / 53,096 out / 13,045,331 API / 11,740,032 read | `-` (no field) |

The three-state partition, read off `qcut stats`:

| state | requests |
|---|---:|
| token-covered (at least one token field > 0) | 71 |
| credits-only (no token > 0, `credits` present) | 40,473 |
| unmeasured (no token > 0, `credits` NULL) | 0 |
| **total** | **40,544** |

On this index every Qoder-hosted call predates the flag, so 0 rows carry both
credits and tokens. That is history, not the shape: with
`QODERCN_EXPOSE_TOKEN_USAGE=1` a hosted response records real tokens **and** credits
in the same row. The BYOK route is the opposite legacy shape — tokens and no
`credits` field at all, stored NULL.

The display keeps three marks because the storage keeps three facts: `-` nothing
contributed to this number, `—` a call (or aggregate of calls) that reported no
token, and a numeral for a measured value. Printing `0` where a route never counts
in tokens, or `0` credits where a route is never billed in credits, are the same
mistake in opposite directions.

Two arithmetic notes that the stored rows prove rather than assume:

- API total = input + output. The largest request on disk is 357,240 input +
  3,821 output = 358,703 API total; adding its 356,352 cache-read tokens would
  make 715,055, which exceeds the model's context — so `input_tokens` already
  contains the cache read, and it is a subset, not an extra.
- Cache hit rate = cache read ÷ (read + miss + write). With no cache field the
  report prints `-`, never a fabricated `0%`.

## Tool mix

| tool | calls | failed |
|---|---:|---:|
| `Bash` | 26,217 | 939 |
| `Edit` | 8,034 | 432 |
| `Read` | 6,782 | 18 |
| `Write` | 1,808 | 43 |
| `Grep` | 832 | 5 |
| `TaskUpdate` | 753 | 0 |
| `TaskCreate` | 529 | 0 |
| `Agent` | 516 | 76 |
| `WebSearch` | 251 | 1 |
| `AskUserQuestion` | 115 | 6 |

Status split across all 46,059 calls: 44,227 `completed`, 1,544 `error`, 259
`backgrounded`, 29 with no result at all (pending).

Subagent types: 391 `general-purpose`, 96 `Explore`, 29 `Plan`.
MCP: 18 calls across 3 servers — `node-repl` 5, `builtin` 8, a QMind plugin server
2, and 3 admin rows with no server or tool. Slash commands: 2 distinct names, one
call each.

Separately, of the 46,011 records that carry a `toolUseResult` dict, **19,816 have
no `kind`** — more than a third. Treating those as unfinished reported finished
work as pending, which is why a missing `kind` means *completed*.

## Tests

```bash
TZ=UTC .venv/bin/python -m unittest discover -s scripts/tests
```

| suite | cases |
|---|---:|
| `test_usage.py` | 60 |
| `test_sync.py` | 60 |
| `test_tui.py` | 58 |
| `test_stats.py` | 50 |
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
| **total** | **347** |

Wall time is dominated by Textual's harness: `test_tui.py` alone is the bulk of
the run, the other fourteen together a few seconds.

The suite was green on all three interpreters at the 310-case snapshot, with
`textual 8.2.8` installed in each — that pair is what the CI matrix pins (floor
from `requires-python`, plus the highest version measured). The 347-case suite has
been re-run on the `install.sh` venv (Python 3.13); CI is the record for the 3.11
and 3.14 ends until they are re-measured locally:

| interpreter | result |
|---|---|
| Python 3.11.15 | 310 OK (2026-10-12) |
| Python 3.13.14 (the `install.sh` venv) | green at 347 (2026-10-21) |
| Python 3.14.4 | 310 OK (2026-10-12) |

`test_config.py` and `test_tui.py` need `textual`; the other thirteen are
stdlib-only.
Running the suite on a bare interpreter without `textual` gives three *errors* in
`test_config.py`, because its wiring cases exec `qcut-tui.py` — that is a missing
dependency, not a Python-version problem, and it is the one way to mistake an
instrument fault for a finding.
