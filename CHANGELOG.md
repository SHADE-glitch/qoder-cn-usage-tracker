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

### D-009 · 2026-10-13 · fix
Symptom  `_record_commands` scanned the text of **assistant** records for a
         `<command-name>…</command-name>` marker and stored the capture as an invoked
         command. Across the transcripts on this machine — 182 files, 181,907 lines — the marker
         appears 7 times and all 7 are an agent quoting the tag inside an explanation, 4 of them
         in subagent transcripts, which `projects/**/*.jsonl` indexes; `CMD_RE` matched none of
         them, so the path had never produced a row while being the one place in the parser that
         stores a slice of message text. The shape that does carry an echo — a marker opening a
         **user** text block — was never read. Neither source had a test: the only command ever
         recorded here arrives through `toolUseResult.commandName`, 2 records.
Change   The scan moved to the `user` branch and the registry comment now says why: an echo can
         only come from a user block, and a marker inside assistant prose is a worked example.
         `test_sync.CommandTest` adds three cases — both shapes in one file, so the prose must not
         store while the echo must; the `commandName` source; and a sentinel on each side of the
         marker, scanned across every text column, proving nothing outside the marker is kept.
         `docs/maintenance/qoder-format.md` gained a hand-written section outside the generated
         block recording both sources and their measured frequency.
Evidence L0 2026-10-13 278 tests OK. Provoked against `git show HEAD:scripts/qcut-sync.py` on a
         copy: two of the three fail — the fixture's quoted example became a command, and the user
         echo produced none; the third (`commandName`) passed before and after, which is why it is
         kept as the regression half. After the change all three pass and `commands` still holds
         its measured 2 rows. `qcut format --write` over the appended section is byte-stable on a
         second run.
Cost     A slash command the model quotes is no longer recorded — it never was, on measured data.
         If Qoder starts echoing the marker on an assistant record, the row is missed rather than
         invented: the miss shows up as a `commands` count of 0, where the invention showed up as
         usage that looked real.
Commit   7136e8b

### D-010 · 2026-10-13 · guard
Symptom  Both READMEs, AGENTS.md § Invariants and the suite's own docstring called the privacy
         import check an **allowlist**. It was a denylist of forbidden roots, so `import poplib`
         in a production script was a convention change nothing reported — "nothing leaves the
         machine" held only for escapes somebody had already thought to name. AGENTS.md carried a
         second lie on the same subject: a `threading` exemption living in `ALLOWED_EXTRA` for the
         TUI's sync worker, which had moved to Textual's thread pool, and the dict had been
         emptied and commented away in the test. Two blind spots stayed open regardless: `os` is
         imported by three scripts, so `os.system` needs no new dependency for the import surface
         to see; and `dynamic_import_calls` looked for `import_module` and then *filtered out*
         every `import_module` finding, while never knowing `spec_from_file_location` — the only
         mechanism production actually uses. It returned nothing for all four files, so its case
         was green whether or not it could detect anything.
Change   `MODULE_ALLOWLIST` declares each script's whole surface, measured off the file: an import
         outside it fails, a declared module the file no longer imports fails the other way, and
         `FORBIDDEN_ROOTS` survives as a check **on the list** instead of on the code.
         `FORBIDDEN_CALLS` covers the shell reachable through a declared import, matched by dotted
         name because a bare-attribute match reports `sqlite3.connect`. `DYNAMIC_LOADERS` names the
         permitted loaders per file — exactly one, `load_sync` — and the detector now knows the
         mechanism it is meant to catch. Every check carries a control on a synthetic source. The
         suite went from 9 cases to 15, and the three doc claims now describe what runs.
Evidence L0 2026-10-13 284 tests OK. Seven provocations on a throwaway copy, each naming the case
         it must fail: `import socket` → declared surface; `os.system('ls')` → call sites;
         `importlib.import_module('http.client')` → dynamic loaders **and** declared surface;
         `import threading` → thread rule **and** declared surface; allowlisting `poplib` →
         guard-on-the-list **and** stale entry; allowlisting an unused `typing` → stale entry only;
         allowlisting `urllib.request` → guard-on-the-list, while `urllib.parse` fails only the
         stale half, which is the distinction the comment claims.
Cost     Adding an import is now a two-edit change — the file and its declared surface — and
         dropping one has to shrink the list. Scope is unchanged and still stated: this inspects
         this repository's four scripts, not a third-party package's imports.
Commit   5830604

### D-011 · 2026-10-13 · fix
Symptom  Three places where a report answered about something it had never seen. (1)
         `qcut show <unknown name>` printed a complete report of zeros:
         `q_entity_summary` is an aggregate over `WHERE name = ?`, so an entity that
         does not exist and an entity idle in this window both return `COUNT(*) = 0`,
         and only one of them is a measurement. (2) `qcut stats` claimed `all time`
         and never said how far the index reaches; the span it did compute
         (`q_overview["last_ts"]`) was `MAX(model_calls)` falling back to tool calls
         only when that table was empty — and the TUI's status line reads the same
         key, so a newer MCP or skill call was invisible in both places. (3)
         `format --write` **appended** its block when the markers were gone, leaving a
         document with two copies of the same surface of which only one is ever
         regenerated again.
Change   `db.q_entity_exists` asks existence deliberately **without** a window; `show`
         refuses an unknown name with rc 1 on stderr and keeps the honest zeros for a
         known-but-idle one. `q_overview` now takes a true min/max over every usage
         table carrying a `ts` and `stats` prints `range  <first> .. <last>`, with `-`
         when there is no endpoint at all rather than a pair of dashes that would read
         as one instant. `format --write` refuses and names the marker it could not
         find, writing nothing, and gained `--out` — the only way to test a splice, a
         refusal and a fixed point without a case rewriting a tracked file.
Evidence L0 2026-10-13 294 tests OK (3.11.15 / 3.13.14 / 3.14.4). L1 against the live
         transcripts into a throwaway store: `range 2026-09-22 16:04 .. 2026-10-13 10:19`
         over 31,588 responses, `show tool NoSuchTool` → rc 1, `show tool Bash --range 1d`
         → rc 0 with 497 calls. Provoked on a copy with the pre-patch data layer and CLI:
         9 of the 10 new cases fail; the tenth (`show` on a known-but-idle name) passes
         before and after on purpose — it is the half that keeps the new refusal from
         swallowing real zeros.
Cost     One extra `SELECT 1 … LIMIT 1` per `show`, and seven min/max scans instead of
         two, on a report that already reads the whole index. AGENTS.md now states the
         rule: an existence test may not be inferred from an aggregate.
Commit   bc941f8
