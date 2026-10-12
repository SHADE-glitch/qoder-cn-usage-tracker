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

**Guards added (and what they found on the first run)**

- `guard` `test_format_registry.py` locks the registry at the top of
  `qcut-sync.py` to the parser **in both directions**: a field read without being
  registered fails, and a registered field nobody reads fails. Layout values,
  separators and tool names may no longer be re-typed at a call site, and a
  registry constant that nothing uses is itself a failure.
- `fix` The registry went on being believed while drifting: **16 listed fields were
  never read** (`uuid`, `parentUuid`, `userType`, `modelSource`, `subtype`,
  `stop_sequence`, `role`, `exitCode`, `durationMs`, `signal`, `interrupted`,
  `timedOut`, `isImage`, `agentType`, `status`, `sourceToolAssistantUUID`) —
  including `durationMs`, which the parser does *not* use: duration is computed
  from the row's own start timestamp. Three names the parser **does** read were
  missing: the `command` alias of `skill`, the `tool_name` alias of `toolName`, and
  the `text` content block. `system` sat in `CLAIMED_RECORD_TYPES` with no branch,
  which reads as "parsed"; it is now in the dropped set, where it belongs.
  `DIR_PLUGINS` was declared and never used.
- `guard` Those two aliases are a real branch, not a spelling: a Skill call that
  names itself only in `command`, or an MCP target only in `tool_name`, was
  recorded by code no test ever entered. `test_sync.py` now has a case going
  through each, plus one asserting an unrelated `command` value on a non-Skill call
  still reaches no column.
- `guard` `test_maintenance_docs.py` requires
  `docs/maintenance/qoder-format.md` to equal what `qcut format` prints at that
  moment, the routers to resolve (relative links checked against the document's own
  directory, not the repo root), no orphaned maintenance asset, CI to pin the floor
  `pyproject.toml` declares, both ends of the matrix to be documented in both
  READMEs, and `baseline.md` to carry its measurement date.
- `fix` The generated format document told the reader to "see docs/maintenance/"
  while sitting inside `docs/maintenance/`. It now links the router that names the
  regeneration command.

**Safety — the way back actually works**

- `fix` `snapshot()` names itself from a one-second timestamp, so two snapshots in
  the same second asked for the **same file** and the later one overwrote the
  earlier. The pair that mattered was not two `qcut backup`s: `restore` writes a
  `pre-restore` copy of the index it is about to replace, so a second restore in
  the same second overwrote the snapshot it was about to read — undoing the undo,
  silently, in the one mechanism that makes a destructive step reversible. Names
  now take a `-1`/`-2` suffix.
- `fix` Retention ran only in `qcut backup`, so the snapshots that `sync --full`
  and `restore` take grew `backups/` forever while the documented promise said
  "keeps five". `db.snapshot()` prunes now, and the CLI names the files that went.
  Pruning orders by mtime, not name: once the suffixes exist, `-10` sorts before
  `-2`, which would have made retention delete the newer file.
- `fix` `qcut backup` on a machine with no index opened the path — which *creates*
  it — and then snapshotted the emptiness and printed `wrote …`. It refuses now and
  says to run `qcut sync`, and the index is left uncreated.
- `fix` `sync --full` on an index that holds nothing wrote a snapshot of nothing.
  `db._has_indexed_state()` had been ported for exactly this decision and wired to
  no caller, so every first run and every test left clutter that could not be
  restored to anything.
- `fix` `--limit` was registered on all eleven read subcommands and read by six;
  `--range` on all eleven and read by one, so `qcut stats --limit 5` printed every
  row. Each flag is now registered only where its command passes it to a query
  (`--limit`: the five table reports plus `show`; `--range`: `show` alone), and the
  parser refuses the rest.

**Chrome — the look-alike claim, checked**

- `taste` The eight tab titles lost their emoji (`🧭 Dashboard` → `Dashboard`), and
  the app stopped overriding the canvas with `Screen { background: $surface; }` and
  the status line with `background: $panel`. Measured through Textual's harness,
  both apps resolve `screen.bg` to `Color(18, 18, 18)` and both tab strips read
  `Dashboard · Tools · Skills · Agents · Plugins · MCP · Tokens · Usage`.
- `guard` `test_tui.py::ChromeTest` pins both shape facts, so the chrome cannot be
  re-decorated by an edit that reads as harmless.

**Guards ported from the reference project**

- `guard` Five suites arrived from the tool this one mirrors, re-aimed at this
  repository's shapes: `test_stats.py` (the headless CLI end to end, 32 cases),
  `test_snapshots.py` (the destructive step's way back, 13),
  `test_record_coverage.py` (a commit touching a production script must leave a
  bullet of the matching kind in this file, 13), `test_readme_counts.py` (every
  count in either README and in `baseline.md`'s suite table equals the number read
  off the suites' syntax trees, 6)
  and `test_readme_bilingual.py` (the two READMEs keep the same sections in the
  same order, 7). Assertions that would be hollow here were left unported and are
  named in each file's docstring — `D-###` citation mechanics, status-code parity,
  a pre-migration snapshot at `SCHEMA_VERSION` 1.
- `fix` The suite total is 165 → **262** across 13 suites. Every documented count
  moved with it: both READMEs' tables and totals, and
  `docs/maintenance/baseline.md`, whose source measurements were taken again the
  same evening (176 files / 370 MB, cold 4.76 s, `inventory` 62 → 35 rows — the
  inflation the Indexing group below removed).

**Indexing**

- `fix` `inventory` duplicated on every sync. The table has no unique key, so the
  `INSERT OR REPLACE` that writes it never conflicted and simply appended another
  copy of every installed entry: after three syncs, 35 installed things were 89
  rows, and `qcut inventory` printed each one three times while `qcut health`
  reported the inflated count. `scan_inventory()` now clears the table before it
  re-scans — correct because the table is a snapshot of what is installed *now*,
  and nothing keys these rows (history lives in the usage tables). An existing
  database self-heals on its next sync; no rebuild or migration is owed.
- `guard` The idempotency promise said "repeated syncs must not duplicate rows" and
  the case that supposedly pinned it looked at two of nine tables — which is
  exactly how `inventory` stayed broken.
  `test_repeated_syncs_stabilise_every_table_in_the_schema` now enumerates the
  tables from `sqlite_master` rather than from a typed list, so a table added
  later is covered without somebody remembering to add it, and it carries a
  positive control (`inventory >= 25`) so a scan that indexed nothing cannot pass
  by comparing two empties. Provoked: with the clear removed it reports
  `{'inventory': (27, 81)}`; with it in place, three syncs are stable.

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
