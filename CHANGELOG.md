# CHANGELOG — qoder-cn-usage-tracker

Original project, **no upstream**: nothing here is a deviation from somebody else's code, so this
record is not a divergence list. It records the repairs, the performance work, the drift guards and
the withdrawals — the things a Qoder update, a Textual update, or a transcript-schema change can
invalidate.

Coverage: c795913..HEAD
Check with `python3 -m unittest scripts.tests.test_record_coverage`. Entries are `D-###`, monotonic,
never reused. An entry states what was true **as of its commit**, not current state, and aggregate
counts are printed by the check, never copied into this file.

> **Why the record is short.** The initial import (`c795913`) bundled the product itself — the
> parser, the eight-tab TUI, the headless CLI and the first cut of the `—`/`-` rendering — and it is
> the coverage anchor, so its content sits outside the window by construction. Features are
> documented in the READMEs; only the non-feature commits that touch a production path appear below.

`kind` ∈ `fix` | `perf` | `taste` | `guard` | `revert` | `chore` — see AGENTS.md § Recording
conventions for the cut.

---

### D-001 · 2026-10-12 · guard
Symptom  The registry at the top of `qcut-sync.py` was believed while it drifted: 16 listed fields
         were never read (`uuid`, `parentUuid`, `durationMs`, …), three names the parser *does* read
         were missing (the `command` alias of `skill`, the `tool_name` alias of `toolName`, and the
         `text` content block), `system` sat in `CLAIMED_RECORD_TYPES` with no branch — so a record
         type read as "parsed" while nothing read it — and `DIR_PLUGINS` was declared and never
         used. Nothing failed; a stale registry reads as authoritative.
Change   `test_format_registry.py` locks the registry to the parser **in both directions** (a field
         read without being registered fails; a registered field nobody reads fails), forbids
         re-typing layout values, separators and tool names at a call site, and fails on a
         declared-but-unused constant. The registry was corrected to match the parser, `system`
         moved to the dropped set, and `test_sync.py` gained a case for each of the two aliases.
Evidence L0 2026-10-12 `python3 -m unittest discover -s scripts/tests` — 262 tests OK across 13
         suites.
Cost     A rename is now one edit that the suite reports with line numbers; the registry can no
         longer be silently ahead of or behind the parser. It checks the coupling, not the *values*
         Qoder writes.
Commit   20a9b33

### D-002 · 2026-10-12 · fix
Symptom  `inventory` appended a fresh copy of every installed entry on every sync: the table has no
         unique key, so the `INSERT OR REPLACE` that writes it never conflicted. After three syncs,
         35 installed things were 89 rows, `qcut inventory` printed each one three times, and
         `qcut health` reported the inflated count.
Change   `scan_inventory()` clears the table before it re-scans — correct because the table is a
         snapshot of what is installed *now*, and nothing keys these rows (history lives in the
         usage tables). An existing database self-heals on its next sync; no rebuild is owed.
Evidence L0 2026-10-12 `test_repeated_syncs_stabilise_every_table_in_the_schema` — with the clear
         removed it reports `{'inventory': (27, 81)}`; with it in place, three syncs are stable. The
         case enumerates tables from `sqlite_master` and carries a positive control
         (`inventory >= 25`).
Cost     The idempotency promise now covers every table, not the two the old case looked at. It is a
         targeted clear, not a general fix: a future table with the same no-key append shape still
         needs its own.
Commit   a02480a

### D-003 · 2026-10-12 · fix
Symptom  Three ways the "way back" failed. `snapshot()` named itself from a one-second timestamp, so
         two snapshots in the same second asked for the same file and the later overwrote the
         earlier — including the `pre-restore` copy that makes a restore reversible. Retention ran
         only in `qcut backup`, so snapshots taken by `sync --full` and `restore` grew `backups/`
         forever while the docs promised "keeps five". `qcut backup` on a machine with no index
         opened the path (creating it), snapshotted the emptiness and printed `wrote …`. Separately,
         `--limit` was registered on eleven read subcommands and read by six, and `--range` on
         eleven and read by one, so `qcut stats --limit 5` printed every row.
Change   Snapshot names take a `-1`/`-2` suffix; pruning moved into `db.snapshot()` (ordered by
         mtime, because once the suffixes exist `-10` sorts before `-2`); `qcut backup` refuses when
         the index holds nothing; `sync --full` skips the snapshot when there is nothing to save;
         and each flag is registered only where its command passes it to a query (`--limit`: the
         five table reports plus `show`; `--range`: `show` alone).
Evidence L0 2026-10-12 `test_snapshots.py` (13 cases) and `test_stats.py` (32 cases) arrived with
         the fix.
Cost     A destructive step is reversible again, and the documented retention holds on every path
         that snapshots. The flag change is a small behaviour break: an argument the parser used to
         accept is now refused.
Commit   29a7816

### D-004 · 2026-10-12 · taste
Symptom  The app decorated its own chrome: the eight tab titles carried emoji (`🧭 Dashboard`), and
         it overrode the canvas with `Screen { background: $surface; }` and the status line with
         `background: $panel`. The claim is that this is a look-alike of the tool it mirrors, and the
         chrome is part of that claim.
Change   Tab titles are plain words, and the canvas and status-bar background are left to Textual's
         defaults. Measured through Textual's harness, both apps resolve `screen.bg` to
         `Color(18, 18, 18)` and both tab strips read
         `Dashboard · Tools · Skills · Agents · Plugins · MCP · Tokens · Usage`.
Evidence L0 2026-10-12 `test_tui.py::ChromeTest` pins both shape facts.
Cost     The look-alike claim is now checked rather than asserted. Only taste is at stake — the
         change is discardable on a Textual upgrade.
Commit   8434247

### D-005 · 2026-10-13 · fix
Symptom  Two defects on the second drill-down level, both invisible because every existing case
         pushed the screen it wanted to see instead of driving a row selection. (1)
         `TrackerApp.on_data_table_row_selected` answered for **every** `DataTable`, including the
         tables of pushed screens, whose row keys mean something else there: a call id was read as
         an entity name, so Enter on a call row opened its detail and then an empty
         `HistoryScreen("tool", "<call id>")` on top of it — the detail was never what the user saw,
         and the model response list's rows routed to a second response list named after a message
         id. (2) `ModelResponsesScreen` filled its table with `key_index=11` *and* one-element
         `key_rows`, but `key_index` indexes the key source, not the rendered row, so every
         non-empty response list raised `IndexError` on mount.
Change   The tab router answers only for the tables in its own `WIDE_TABLES`;
         `ModelResponsesScreen` owns its rows and opens the call detail. `RowRoutingTest` drives
         `action_select_cursor` (Enter on a row) through three routings, one of which selects a
         plugin page row and asserts the leaf pushes nothing — with a keyed row inserted first,
         because an empty page would make a leaf assertion true for the wrong reason. A structural
         case enumerates the `Screen` hosts that own a table and fails on any that neither handles
         the selection nor declares itself a leaf, so a fifth screen cannot inherit the bug.
Evidence L0 2026-10-13 269 tests OK (3.11.15 / 3.13.14 / 3.14.4). Provoked on a throwaway copy: with
         the router guard removed, three cases fail (history, response, plugin leaf); with the
         response screen's handler removed, the response case and the structural case fail; with
         `key_index=11` restored, the response case errors on the fill.
Cost     A pushed screen that wants its rows to do something must now say so in source. The bug
         class it prevents was two commits deep: the same `_fill_wide` serves tabs and screens, so
         one shared key vocabulary silently meant two different things.
Commit   c26710a

### D-006 · 2026-10-13 · fix
Symptom  `top_n` was declared in `DEFAULTS`, given an env name and a bound, printed by `qcut health`
         and documented in both READMEs — and read by nothing. The Dashboard sliced a class constant
         (`DASH_TOP`), so `QCUT_TOP_N` was a knob the owner could edit for no effect, which is the
         exact failure this project's settings rule forbids. Two further constants
         (`USAGE_LOG_LIMIT`, `DETAIL_LIMIT`) had no references at all and duplicated live knobs.
Change   The three Dashboard sites read `cfg["top_n"]`; the dead constants are deleted.
         `SettingsHaveConsumersTest` scans both entry scripts' syntax trees for subscripts of a
         `cfg` host and fails on a declared setting no renderer consults, on a key read that was
         never declared, and — positive control — when the scanned set is not the shipped knob set.
Evidence L0 2026-10-13 275 tests OK. Provoked on a throwaway copy: re-pointing the three reads at
         `cfg["name_cap"]` fails two cases naming `top_n`.
Cost     A knob cannot ship unhooked again. The scan reads `qcut-tui.py` and `qcut-stats.py` only —
         a third surface with its own `cfg` host has to be added to `CONSUMERS`.
Commit   83560fc

### D-007 · 2026-10-13 · fix
Symptom  `qcut health` — the command reached for when something is wrong — died with a raw
         `qcut_db.ConfigError` traceback whenever the settings file was malformed and an index
         already existed. The message inside the exception named the file and the key, but only to
         someone able to read a stack, and the command's own exit status was lost.
Change   `cmd_health` catches `ConfigError`, prints `config is not usable: …` to stderr and returns
         1. The case hides the environment first (`no_settings_env`, now shared from `_helpers`
         rather than re-typed per suite), because the environment outranks the file and an isolated
         case would otherwise be asserting someone else's shell.
Evidence L0 2026-10-13 `test_stats.py::HealthReportTest::test_a_broken_settings_file_is_reported_and_not_raised`.
         Removing the handler on a copy raises the `ConfigError` out of the case.
Cost     One more refusal path in the CLI. The traceback still happens for any *other* exception —
         this handler is scoped to the settings layer's own error type.
Commit   83560fc

### D-008 · 2026-10-13 · fix
Symptom  `sync --full` asks whether the index holds anything before deciding to snapshot it, and the
         predicate counted rows in `sync_state` alone. A database holding usage rows whose offsets
         were lost — a restored or hand-copied file — read as empty, so the rebuild that deletes
         those rows took no way back. That state is the one nobody can regenerate: the transcripts
         may since have moved on.
Change   `_has_indexed_state` checks the tables `reset` clears (`sync_state`, `model_calls`,
         `tool_calls`) with `SELECT 1 … LIMIT 1`. It is the helper D-003 wired in; this widens what
         it calls indexed.
Evidence L0 2026-10-13 `test_snapshots.py::LostCursorTest`. With the single-table version restored
         on a copy the case fails: `a rebuild of an index with rows took no snapshot: []`.
Cost     Three counts instead of one, on a path that runs once per rebuild.
Commit   83560fc
