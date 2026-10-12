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
