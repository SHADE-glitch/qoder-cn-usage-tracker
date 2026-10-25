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

### D-012 · 2026-10-13 · fix
Symptom  The incremental cursor trusted a single staleness signal — the file shrank
         below the stored offset. Two shapes escaped it, and both failed without a
         sound: a **rewrite that produced the same byte length** (the offset sat at
         the end of the file, the read returned zero bytes, the run reported success
         and the new records were absent), and a shortened rewrite that stayed above
         the offset. The tail was the mirror mistake — a record and its newline are
         two writes, and an `rfind`-based reader treats the gap as a partial line, so
         a final record with no terminator was never indexed *and* the
         size/mtime short-circuit then skipped the file forever. All 183 transcripts
         here end with a newline, so neither path is firing on this machine; the
         crash window is real, just not currently occupied.
Change   `_needs_reset` names every way an offset stops being trustworthy — shrink
         below it, a moved mtime that did not grow the file, an offset that no longer
         follows a record boundary — with an early-out for a file that is untouched
         *and* fully consumed, without which a transcript ending without a terminator
         would be rebuilt on every run. `index_file` parses what follows the last
         newline and consumes it only if it yields a record; a genuinely half-written
         line still waits, and is deliberately not counted in `unparsed` (the drift
         signal would otherwise grow with the poll rate). `_starts_on_line_boundary`
         accepts a terminator that arrives **after** the offset, which is what a late
         newline looks like after the record was already read.
Evidence L0 2026-10-13 301 tests OK. 4 of the 7 new `test_sync.RewrittenFileTest` cases
         fail against `git show HEAD:scripts/qcut-sync.py`: the unterminated tail is
         never read, the same-length rewrite is never re-read, the late-terminator
         case's own precondition (`offset == size`) does not hold, and the predicate's
         unit shape disagrees. The other three pass before and after and are kept as
         controls: a plain append must not reset, a shortened file must, and a partial
         tail must not inflate `unparsed`.
Cost     A rewrite that grows the file by exactly the new content is still read as an
         append — stat cannot tell those apart, and claiming otherwise would be a
         guess. `unparsed(reason, count)` has no file attribution, so a re-read adds a
         file's earlier bad lines again; AGENTS.md now states the table is a lower
         bound and `qcut sync --full` is the only way to make it exact.
Commit   b85b1fd

### D-013 · 2026-10-13 · guard
Symptom  The column plan had only ever been measured at mount. `TrackerApp.on_resize`
         re-plans every wide table, and no case in 45 had changed a terminal size, so
         the dynamic half of the rule "a table never shows a half column" was
         unobserved in both directions: a plan that dropped columns at mount and never
         gave them back would have looked like intent, and a rebuild that lost rows
         would have looked like a filter. Two of the mount-time cases were also
         conditionals — `if note.display:` — which pass when nothing is hidden, so they
         could not report a plan that hid nothing and said nothing.
Change   `test_tui.py::ResizeTest` (5 cases): every table at a narrow width is a
         **prefix** of its registered plan and names each dropped column on the note
         (asserted, not conditional); widening at 200 columns returns every column,
         keeps `row_count`, and hides the note; narrowing drops columns, keeps the rows,
         and displays the note; a row selected after narrow→wide routes by the key its
         row carries; and two tool names longer than `name_cap` and identical past the
         cut point still open **their own** history — which is what turns "the key comes
         from the uncapped row" from a code comment into a check. The last case reads
         its expected names back from `tool_calls`, not from the router, so the router
         cannot agree with itself. AGENTS.md's rule now says the plan re-runs on resize.
Evidence L0 2026-10-13 306 tests OK. Three provocations on throwaway copies, each naming
         what it must break: `on_resize` returning immediately → the two resize cases;
         `key_source` built from the capped row → the shared-prefix case alone;
         `sorted(labels)[:keep]` instead of the prefix → the narrow-plan case and the
         narrowing case. Neither of the last two catches the other's break, which is why
         they are separate cases.
Cost     Five more Textual harness runs (the suite's slowest interpreter gains ~16 s), and
         a resize change must now keep rows and keys, not just render.
Commit   5a52c4c

### D-014 · 2026-10-13 · guard
Symptom  A transcript that cannot be opened was already handled — `index_file` raised
         through its `OSError`, the caller bumped a reason and stored no state for the
         file — and no case had ever looked at any of it. Uncovered behaviour of this
         kind has two failure modes, both silent: a future `try` that swallows the
         error loses the *report*, and a future bookkeeping change that records an
         offset for a skipped file retires it forever, with no reason attached and a
         row missing from the index. Separately, AGENTS.md claimed an offset lands on a
         **record** boundary while the only case proved the **newline** form, which the
         unterminated-tail fix (D-012) had just made incomplete.
Change   `test_sync.CrashSafetyTest` gains three cases on a chmod-000 transcript: the
         run still indexes every readable file, `unparsed` carries an `unreadable_file`
         reason, and nothing is remembered for the skipped file so the next run reads it
         whole. One more case pins the offset after a consumed unterminated record — at
         end of file, behind a `}`, a record boundary the line-boundary case cannot see.
         The reason string is now the constant `UNREADABLE_FILE`, next to
         `UNPARSEABLE_LINE`: a name an operator reads should not be typed at a call site.
Evidence L0 2026-10-13 309 tests OK. Provoked on copies: letting the `OSError` escape
         errors both unreadable cases; dropping the `_bump` fails the reported one alone;
         writing a `sync_state` row on the failure path fails the retry one alone.
Cost     One `os.chmod` and its cleanup per case, and any future error path for the
         source files has to keep a skipped file un-remembered.
Commit   926ba9a

### D-015 · 2026-10-13 · fix
Symptom  Two cases whose verdict was the runner's, not the code's. (1) CI's Python 3.11 job at
         5669440 failed with `'November 2026' not found in 'December 2026'` while 3.11, 3.13 and
         3.14 all passed locally: the calendar-stepping case issued `pilot.click("#dr-prev")`
         **twice with no `await pilot.pause()` between**, and Textual processes the message pump a
         click posts at the next pause — so the second click can arrive before the first has moved
         anything. (2) CI's 3.14 job at 5830604 had failed `test_the_window_is_whole_calendar_days_
         not_rolling_hours` by exactly 86,400,000 ms: it compared `window_bounds("7d", now)` with
         `window_bounds("7d", now - 3h)`, which is only a test of the rule while "three hours ago"
         stays inside the same local day — at 01:35 UTC it is yesterday, at 09:35 +08:00 it is not.
Change   Both click sites are paced, and `ClickCadenceTest` reads `test_tui.py`'s own source and
         fails on any two adjacent `await pilot.click(` lines — a cadence rule that only fires when
         the timing is unlucky is not a guard, and the check enumerates pairs instead of counting
         them so a new one cannot slip in. The window case now anchors to fixed hours: the start
         must not creep between 09:00 and 15:00, and one step of a 7-day window must be exactly one
         **calendar** day (compared as dates, so a 23- or 25-hour DST day cannot fail it either).
         AGENTS.md gains the convention, and MAINTENANCE.md/·zh-CN.md now say the runner's clock is
         UTC and give the reproduction command.
Evidence 310 tests OK on 3.11 and 3.14 under **`TZ=UTC`** (the exact shape that failed) and on the
         3.13 venv in local time. Provoked on copies: dropping `.replace(hour=0, …)` from
         `window_bounds` fails the rewritten case with "the window crept with the hour inside one
         day" (and two neighbours); removing one of the new pauses trips the cadence guard.
Cost     One more second of `pilot.pause()` per click pair, and a source-shape rule that will
         complain if someone ever wants two clicks to land together — that has to be a deliberate
         restructuring, not an accident.
Commit   a4918d6

### D-016 · 2026-10-13 · guard
Symptom  The tripwire guarded one path. `_hermetic.py` recognised the live `usage.db` and nothing
         else, so a fixture was free to read `~/.qoder-cn/**` — and a case whose correctness
         depended on a real transcript was then green on every machine except the one where that
         file exists, which is the one machine where it needed to be red. The sibling tracker has
         exactly that shape: a case that opens the developer's plugin-events sidecar, passing in CI
         because CI has no sidecar and failing at home.
Change   `PROTECTED_DIRS` carries the host tree beside the database, and the hooks cover three read
         paths instead of one — `open`, `os.scandir`/`os.listdir` (a glob reaches file contents
         without ever calling `open`) and `sqlite3.connect`. A child process must pin
         `QCUT_QODER_DIR` for the reason it must pin `QCUT_DB`: the launcher resolves them
         independently, so pinning the database left the host as wide open as before.
         Each refusal ships with the pair that keeps it honest —
         `test_an_ordinary_test_host_is_left_alone` walks a temp path through the same three hooks
         and must come back clean, and `test_the_same_child_is_allowed_once_its_paths_are_pinned`
         is the matched positive for the child refusal.
Evidence L0 2026-10-13 313 tests OK. Ten cases became thirteen; every new case is paired with a
         case that must keep passing, so a hook that refuses everything fails the suite rather than
         passing it.
Cost     A test that shells out now pins a second variable or it is refused — which is the failure
         this entry is about, only seen at commit time instead of at review time.
Commit   c12f980

### D-017 · 2026-10-13 · guard
Symptom  The eight tab slots are a contract shared with `cbut` and `tcut`, and nothing read it.
         `test_every_tab_has_its_widget_and_table` asserted the built panes equal
         `[tid for tid, _ in TUI.TABS]` — and `qcut-tui.py` builds those panes in a loop over that
         very tuple, so the comparison held for any tab set at all. Deleting `("mcp", "MCP")`, or
         the tab, left the case green.
Change   `test_the_eight_tabs_are_present_and_in_order` spells out the eight ids and the eight
         visible labels and compares the running app against them. Labels are read from the tab bar
         (`tc.get_tab(id).label_text`) because `TabPane` carries no `label` in Textual 8.2.8 —
         reading one raises `AttributeError`, and a case that dies on its first line checks nothing.
         The old assertion is kept, reframed as the one direction it can still catch: a pane built
         outside the declared tuple.
Evidence L0 2026-10-13 314 tests OK. Provoked, control green first: dropping `("mcp", "MCP")` from
         `TABS` turns the new case red; renaming `("usage", "Usage")` to `("usage", "Requests")`
         turns it red on the label half alone with the other three `TabWiringTest` cases still
         green — the rename is precisely what the old assertion could not see.
Cost     Renaming a tab now fails on purpose. That is the cost of the contract being read rather
         than assumed, and a sibling checkout is not importable from here, so this is the only place
         it can be read.
Commit   966ba27

### D-018 · 2026-10-13 · guard
Symptom  The root-file layer was a naming convention with nothing behind it. No check in this
         repository asserted that the finalized files sit at the repository root, that `LICENSE`
         exists, or that `AGENTS.md` points at the machine-wide standard instead of restating it.
         A renamed or deleted root file would only be noticed by a person reading the diff.
Change   Add `scripts/tests/test_conventions.py` (9 cases, standard library only, and it installs
         the same hermetic tripwire as its siblings). It asserts the finalized layer is present at
         the root and that `pyproject.toml` is the only manifest; that `AGENTS.md` carries the
         one-line dimension pointer and reproduces none of the standard's own numbered section
         headings; that `.gitignore` still covers the runtime state the standard lists and that
         `git ls-files` tracks none of it; and that no process draft is tracked anywhere. `AGENTS.md`
         gains the dimension line above the shared-standard pointer it already had. Three
         consequence edits follow, all of them count arithmetic that this repository already
         checks: the README pair moves from 314 to 323 in all three of its phrasings, the
         `docs/maintenance/baseline.md` table gains the new row and the new total, and the
         stdlib-only count becomes thirteen, because the new suite is stdlib-only. That last one
         edits a test — `test_the_stdlib_only_count_is_the_suite_list_minus_textual` carried
         `+ 12` — and it is edited in the direction the suite makes true, not to hide a failure.
         Nothing is fetched — every assertion reads this working tree.
Evidence `python3 -m pytest scripts/tests -q` 323 passed (147 subtests), run with the final content
         of this entry in the tree. Provoked for real: moving `LICENSE` out of the tree turns the
         root-layer case red with `AssertionError: Lists differ: [] != ['LICENSE: create this file
         at the repository root']`, and restoring it turns the suite green again. Two earlier
         drafts of the guard were themselves red and were corrected rather than suppressed — a
         heading match that also caught this repository's own `## CI`, and an ignore-list spelling
         that only matched `.venv/`. No test was skipped, widened or moved to make a check pass.
Cost     The manifest and ignore lists are hard-coded, so a deliberate second manifest or a new
         runtime artefact is a change to this file rather than a surprise failure. The stdlib-only
         count is derived by subtraction from a hard-coded constant, so adding a stdlib suite means
         editing that constant as well; this entry is the first time it has moved.

Commit   18c6668

### D-019 · 2026-10-16 · chore
Symptom  The pointer in AGENTS.md is followed only by an agent that chooses to follow it: it named
         `~/.agents/standards/`, but nothing made that text reach the context, and the section list
         it carried had drifted — §5 (the conflict rule) was missing.
Change   End the pointer block with two bare `@` imports (`@~/.agents/standards/STANDARD.md`,
         `@~/.agents/standards/dimensions/a.md`) and add `opencode.json`, whose `instructions` array
         names the same two files. Add `§5 conflict rule` to the section list. `test_conventions.py`'s
         `ROOT_FILES` gains `opencode.json`.
Evidence 2026-10-16: CodeBuddy CLI (`-p --tools ''`, every tool disabled) in a directory whose only
         memory file was an `AGENTS.md` carrying the two imports printed the tokens they carried — the
         text is injected, not read by a tool. opencode `run` printed the token its `instructions`
         named, with no tool call. `python3 -m pytest scripts/tests/test_conventions.py -q`
         9 passed.
Cost     Both hooks name `~/.agents`, absent in another clone, so they are skipped there — the
         pointer stays the portable part. Nothing is copied, so nothing can drift.
Commit   3d8cdd6

### D-020 · 2026-10-19 · chore

Symptom  AGENTS.md cited the shared standard by bare STANDARD.md, a name that only resolves via
         the pointer block above it.
Change   Both references now carry the full ~/.agents/standards/STANDARD.md path.
Evidence 2026-10-19: python3 -m unittest discover -s scripts/tests — 323 tests, OK.
Cost     None; wording only.
Commit   c83ea2d

### D-021 · 2026-10-19 · fix

Symptom  The `test_tui.py` row of the suite table carried two orphan lines beneath it, each
         starting with `|`; GitHub rendered them as two extra one-cell rows.
Change   The two orphan lines are gone. They were the tail of the sentence this row replaced in
         c26710a, left behind when the row was rewritten to "which screen owns the rows of which
         table"; the row is one line again, as its pipe count says.
Evidence 2026-10-19: every table block in README.md now has a uniform pipe count;
         `python3 -m unittest discover -s scripts/tests` — 323 tests, OK.
Cost     None; no test or CI file was touched.

Commit   bc5f4cc

### D-022 · 2026-10-22 · fix
Symptom  The footer never listed the key that opens a row: Textual binds `enter` on
         `DataTable` with `show=False`, so the focused table's own binding won and hid every
         other `enter`; an app-level binding could not fix it.
Change   A two-line `Table(DataTable)` subclass re-declares the same `select_cursor` action
         under a visible name, used only by the tables whose rows open something (the
         entity tabs and the drill-down screens). `dash-projects` and `usage-table` carry
         no row key and stay plain, so the footer never advertises a dead key.
Evidence 2026-10-22: the footer test reads `active_bindings` per tab and finds
         `("enter", "Open")` absent where no row opens, present where one does. Suite green.
Cost     A table is now `Table` or `DataTable` by hand and the wrong choice is silent both
         ways; the registration guard counts both classes but does not check the choice.
Commit   20af79e

### D-023 · 2026-10-22 · taste
Symptom  Two unlabelled token totals on one screen. The Tokens and Usage tables, the
         project board, the agent history, the headless `models` table and the model
         summary line each carried an "API Total" beside the Usage Total.
Change   One token total everywhere: the API Total column is gone from all of them; the
         stored per-row total survives on the single-call screen renamed "reported total".
         `SUMMARY_KEYS["model"]` drops the second total, and the CLI footnote explains
         Usage Total and the derived cache columns instead of two competing totals.
Evidence 2026-10-22: `test_the_models_table_explains_its_two_derived_columns` renamed and
         now asserts `api total` is absent from the CLI output; the TUI suites were green
         the same day (387 tests).
Cost     A reader who wants the provider's own per-row figure opens the single-call
         screen, where it is labelled by provenance. The dashboard's Usage panel and the
         CLI now share one number, so they can no longer disagree quietly.
Commit   5b7f2f3

### D-024 · 2026-10-22 · taste
Symptom  The dashboard's second row repeated figures other tabs compute from the same
         rows (Runtime: completed/failed/pending, average tool time, peak context,
         compaction tax) and a calls-per-day sparkline that duplicated the Usage page's
         window; the project board sat in a fixed-height panel where a tenth project was
         silently invisible.
Change   The Runtime row and the sparkline are removed; the dashboard re-flows into three
         rows (headline counts · leaderboards · project board). Peak context and
         compactions stay readable one Enter away on the project screen, which is where a
         per-project fact belongs. The board's panel is `height: auto` inside the
         scrolling `#dash-box`, so it takes the height its rows need.
Evidence 2026-10-22: the tab-set test pins the three `.dash-row` ids and the absence of
         `#dash-rt-*`; `test_the_project_screen_and_tokens_note_surface_the_new_ledgers`
         reads compactions/peak off the project screen and asserts the dashboard ids are
         gone. Suite green (387 tests).
Cost     Failure and pending counts are one keystroke further (the Tools tab), and the
         per-day shape of usage is visible only through window changes.
Commit   5b7f2f3

### D-025 · 2026-10-22 · fix
Symptom  Both dashboard lists were fixed-width lines sized for the three-up row at 140
         columns (models 41 cells, tools 38). One more digit — a credit total past 999, a
         call count past 99,999 — pushed the tail past the panel, and a wrapped tail reads
         as another entry in the list.
Change   Each line takes its label width from the panel's measured width less one reserved
         column (the pane's scrollbar) and the widths of the fields the data actually needs;
         a longer number narrows the label instead of wrapping the row.
Evidence L0 2026-10-22: the dashboard rendered with the picker on All time at 120x45,
         130x45, 140x45, 150x45 and 160x45, every Static measured against its own width —
         0 overflows. Suite green (387 tests).
Cost     At 140 columns the model label is 33 cells (it was 18) and the tool label 34 (it
         was 16); the numbers keep their exact form, so a wide panel is spent on the name.
Commit   f8b3078

### D-026 · 2026-10-22 · fix
Symptom  The dashboard legend was one 168-cell sentence rendered at `height: auto`, so on a
         120- or 160-column terminal it wrapped onto a second row and pushed the project
         board — and the table under it — down by one line.
Change   The sentence is now 95 cells and carries `height: 1` with `nowrap` + `ellipsis`,
         so a narrower terminal clips it on one row instead of growing. A resize case
         renders the line at 120 and 80 columns and asserts one row, no overflow, and no
         ellipsis at 120.
Evidence L0 2026-10-22: `#dash-note` measured at 70/80/100/120/140/160/200 columns on the
         real database — height 1 everywhere, 0 overflows, ellipsis only below 97 columns.
         Suite green (388 tests).
Cost     The legend lost its prose: "Built-in = Qoder, else BYOK · `—` = no token · `-` =
         no data · Enter = open a project". The drill-down hint is a gesture, not a
         sentence, and the two dashes keep their meaning.
Commit   e89ac3a

### D-027 · 2026-10-22 · taste
Symptom  Every dashboard row was content-sized, so on a 120x45 terminal the box ended nine
         rows above the status line — a dashboard that read as half-loaded.
Change   `_fit_dashboard` measures what the rows above the ledger leave (the note, rows 1
         and 2, and the three rows of row margin) and hands it to the ledger as a
         `min-height`: the table grows into the spare rows and the note lands on the box's
         last line. The spare is measured from the rows above and the table's row count,
         never from the table's current height, so a second call returns the same answer;
         a short terminal computes a non-positive spare and keeps the natural height, which
         is what keeps the box scrolling instead of clipping a project.
Evidence L0 2026-10-22: 120x45 box 40 / rows 12+8+16 / `max_scroll_y` 0 and the note on
         the box's last line; 120x60 the ledger takes 28 rows; 120x30 and 80x40 keep the
         natural heights and scroll as before. Suite green (389 tests).
Cost     On a tall terminal the ledger is mostly empty rows. The alternative — the same
         space outside the card — looked like a broken layout, and the ledger is the only
         panel that can show one more row per unit of height.
Commit   4162f0e

### D-028 · 2026-10-22 · fix
Symptom  Growing the terminal left the ledger at the old height, and 120x45 -> 120x20 -> 120x45
         came back five rows short. `_fit_dashboard` was scheduled from the app's `on_resize`,
         where the box is still laid out at the old height: the spare was the previous
         window's, and nothing re-ran once the new layout landed.
Change   The box is a `DashBox(Vertical)` that re-fits the ledger on its *own* resize — that
         event fires after it has been laid out at the new height. `on_resize` keeps the
         responsive layout and the column plan only.
Evidence L0 2026-10-22: 120x45 -> 160x60 -> 120x20 -> 120x45 on the real index: gap 0 / 0 /
         scroll 17 / 0, ledger 13 / 28 / 4 / 13 rows. The ledger case now resizes the window
         instead of only mounting at two sizes. Suite green (389 tests).
Cost     One subclass and a private call (`getattr(app, "_fit_dashboard")`) instead of the
         app-level hook; the app no longer knows when its own dashboard was resized.
Commit   e9cf14d

### D-029 · 2026-10-25 · fix
Symptom  The agent drill-down disagreed with the tab above it. `q_history(agent)` summed
         `input_tokens` and `output_tokens` across a run's responses and then applied the
         "both halves reported" test to the sums, so a response that reports output but not input
         disappeared into the total instead of contributing nothing: a run of (100/10) and (NULL/30)
         reported 140 where its rows justify 110. The rest of the family decides Usage Total per
         row, and the cross-repo golden fixture pins that — only this one query was off.
Change   The `model_calls` subquery now carries `_AGG_USAGE_TOTAL_EXPR` (SUM of the per-row
         CASE) and the outer query selects `r.usage_total_tokens` instead of rebuilding the CASE
         over the column sums. `input_tokens`/`output_tokens` keep their SUMs — those columns show
         what was reported, and only the total needs the per-row rule.
Evidence L0 2026-10-25: 406 tests, OK. The new case
         (`test_a_partly_reported_response_does_not_inflate_a_runs_usage_total`) asserts 110 and
         2 requests; reverted against the previous query it reports 140. `test_readme_counts`
         caught the stale 405/72 rows and the README pair plus `docs/maintenance/baseline.md` now
         carry 406/73.
Cost     One aggregate expression moves into the subquery; the outer SELECT loses five lines and
         the drill-down gains one column alias. A run whose responses are all partly reported now
         shows `—` instead of a number the rows do not support — which is the point, but it does
         mean fewer runs carry a total than before.

Commit   0098d39
