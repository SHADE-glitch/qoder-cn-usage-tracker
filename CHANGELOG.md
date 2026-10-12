# Changelog

One entry per released change, newest first.

## [Unreleased] — 2026-10-12

**Reporting — a route-level zero is no longer printed as a measurement**

- `fix` Qoder's own routes answer `input`/`output`/`cache read`/`cache write` with
  a literal `0` while billing real credits. Every token cell now reads `—` for
  those rows and windows, so a window of only hosted responses no longer adds up
  to "0 tokens" — a claim that the route never made. An absent value keeps `-`;
  the two marks answer different questions.
- `fix` The mirror case on the credits axis: a BYOK route carries **no `credits`
  field** (71 responses here, all NULL), so `SUM(COALESCE(credits,0))` printed `0`
  — "billed nothing" — where the truth is "never billed in credits". Aggregates
  now report how many rows carried the field and draw `-` when none did.
- `guard` One predicate decides both marks and the `Tok reported` column:
  `db._TOKENS_REPORTED_EXPR` in SQL, `db.row_reports_tokens()` in Python, pinned
  against each other row-for-row. Before this, three window aggregates each
  hand-typed the test and **two of them omitted `cache_creation_tokens`** — a row
  reporting only a cache write was "no tokens" on the Dashboard and "reported" on
  the Tokens tab, in the same app.
- `fix` Both renderers guard on the value as well as the flag
  (`not reported and not value`), so a wrong flag can suppress a zero but can
  never swallow a non-zero measurement. The first cut of `fmt_tok` did exactly
  that, and the invariant case found it before it shipped.
- `fix` The Usage page note claimed a BYOK route's "credits read 0". They are
  absent; the UI already drew `-`. The note now names both marks.
- `taste` The headless CLI and the TUI now agree cell-for-cell (the CLI's
  `stats`/`models`/`show` draw the same `—` and `-`), and
  `test_tui.py::test_fmt_tok_is_the_same_function_in_both_renderers` pins the two
  definitions against a value matrix. The pre-existing disagreement they inherit
  — `fmt_credits` shortens `1,234` in the TUI and writes `1,234.50` in the CLI —
  is left alone: a formatting question, not a truth question.
- `chore` Token column names live in one list (`db.TOKEN_COLUMNS`) shared by the
  detail screen and the CLI, instead of two hand-copied sets.

**Project shell**

- `chore` The repository is now under git (`master`); it was previously untracked,
  so this first commit carries the tool as it stood *including* the rendering work
  above — the pre-change sources were never committed and are not reconstructed.
- `chore` `.github/workflows/ci.yml`: `unittest discover` over the whole suite on
  Python **3.11 and 3.14**, `fetch-depth: 0`. The floor is `requires-python`;
  3.14 is the highest version measured. Both ends were run green here (165 tests,
  `textual 8.2.8`) before being declared, and 3.13 — the `install.sh` venv — is a
  middle version that speaks for neither.
- `chore` `.gitignore` widened from `usage.db*` to `*.db`/`*.db-wal`/`*.db-shm`
  plus `venv/`, so no throwaway index or second virtualenv can be committed by
  accident.

**Docs corrected against the code**

- `fix` `AGENTS.md` said the four non-TUI suites are "stdlib-only and run
  anywhere". `test_config.py` execs `qcut-tui.py`, so it needs `textual` — on a
  bare interpreter it raises three errors that look like a Python-version failure
  and are a missing package.
- `fix` Both READMEs said a BYOK route's credits rows "carry `0`" (English) /
  "这类行是 `0`" (Chinese). Measured: the field is absent in all 71.
- `fix` Both READMEs named the Tokens tab column `tokens?`; that is the CLI's
  name. The TUI ships `Tok reported`.
- `fix` Test counts: 121 → **165** (`test_usage` 33 → 53, `test_tui` 15 → 39).
- `fix` `docs/maintenance/baseline.md` re-measured end to end: 173 files / 362 MB,
  5.17 s cold at 133 MB peak RSS, 0.10 s warm, 19 MB index, 29,628 model rows,
  33,547 tool calls, 14,770 of 33,531 results with no `kind`, 133 of 173 files
  subagent transcripts, plus the interpreter matrix and the route-split table
  that the `—`/`-` marks are read against.
- `docs` A new `MAINTENANCE.md` / `MAINTENANCE.zh-CN.md` pair: where to re-measure,
  which command produces which number, and what to do when a panel goes empty.

## [0.1.0] — 2026-10-12

First release. `qcut` indexes the transcripts Qoder CN already writes and shows
them in an eight-tab Textual UI plus a headless CLI.

**Indexing**

- `qcut sync` parses `~/.qoder-cn/projects/**/*.jsonl` incrementally, remembering
  a byte offset per file; `--full` snapshots and rebuilds from scratch. Measured
  on this machine: 157 files / 341 MB in 4.9 s cold, 0.08 s warm, 18 MB of index.
- One row per model response, keyed by `message.id` (28,098 rows, all unique).
  Token columns come only from that response's own `message.usage`; a reported 0
  stays 0 and an absent field stays NULL.
- Route origin is read from the model string plus `settings.json`'s
  `providers[].baseUrl` host — never inferred from the model name. A built-in
  route is `builtin`; a BYOK route is a vendor label (`Volcano ARK`), else its
  host, else the provider id.
- Tool calls are keyed by the `tool_use` block id and paired with their
  `tool_result` by id, so a result that lands after the call was indexed in an
  earlier run still completes it. A result with no `toolUseResult.kind` — about
  14k of 31.7k here — means *completed*, not *pending*.
- Subagent transcripts (`agent-a<Type>-<hash>.jsonl`, 120 of 157 files here) are
  folded back into the session they name, so they do not create 120 sessions.
- MCP is recognised in Qoder's own shape: `mcp_call` / `mcp_get` / `mcp_list`
  with the target in the `toolName` argument, not a `mcp__server__tool` tool name.
- Unclaimed record types and unparseable lines are counted in `unparsed`, so a
  format change shows up instead of quietly emptying a panel.

**Reporting**

- Eight tabs — Dashboard, Tools, Skills, Agents, Plugins, MCP, Tokens, Usage —
  with a range selector whose value is kept **per tab**. Windows are whole local
  calendar days ending now; the Plugins tab has no window.
- Two-level drill-down on every entity tab: an entity's windowed summary above
  its recent calls, then every stored field of one call.
- Credits are a first-class metric. Qoder's own routes report credits and a
  literal 0 for every token field; a BYOK route is the reverse. Both are stored
  as reported, and the UI says which one a window is showing.
- Every table keeps the longest prefix of its columns that fits and names the
  dropped ones, so a narrow terminal costs columns rather than clipping a label
  mid-word.
- Headless subcommands: `stats`, `tools`, `skills`, `agents`, `plugins`, `mcp`,
  `models`, `show`, `recent`, `inventory`, `export`, `health`, `backup`,
  `restore`, `format`.

**Settings**

- `~/.config/qcut/config.toml` (or `QCUT_CONFIG`), with environment > file >
  default precedence. A broken file names its path and stops the TUI; unknown
  keys and out-of-range values are refused.

**Guarantees**

- `~/.qoder-cn` is opened for reading only — proven by fingerprinting a fixture
  tree around a real index run, not by review.
- No production script imports a socket, HTTP client or process-spawning module;
  `test_privacy.py` holds the allowlist.
- No free-text value from a transcript reaches any column — a sentinel in every
  slot the parser reads is scanned across every text column of every table.
