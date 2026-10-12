# AGENTS.md

Guidance for AI coding agents working in this repository.

## What this is

A **passive** usage tracker for Qoder CN: it parses the session transcripts Qoder
already writes and builds a local SQLite index, then shows it in an eight-tab
Textual TUI (plus a headless `qcut stats` CLI). Three layers, in dependency order:

1. `scripts/qcut-sync.py` — the parser and indexer. Reads **only**
   `~/.qoder-cn/projects/**/*.jsonl` (plus the small `skills/`, `plugins/`,
   `mcp.json` and `settings.json` inventory files), read-only, and classifies
   tool / skill / agent / MCP calls into
   `~/.local/share/qoder-cn-usage-tracker/usage.db`.
2. `scripts/qcut_db.py` — the shared data layer (schema, queries, calendar-day
   windows, snapshots). Imported by the TUI, the stats CLI and the tests.
3. `scripts/qcut-tui.py` (Textual) and `scripts/qcut-stats.py` (headless CLI).

**There are two writers, not one.** `qcut sync` is the only thing that indexes
transcripts, but the TUI also writes: `on_mount` runs `db.ensure_schema()` and
its auto-sync worker calls the same indexer the CLI does. Anything that assumes
"the TUI is read-only" is wrong — reason about locks accordingly. The headless
CLI is read-only except for its explicit maintenance subcommands (`backup`,
`restore`, `format --write`).

`bin/qcut` is a bash dispatcher. **It is bash on purpose**: it must still run and
print a useful error when the TUI venv is missing or broken, so it cannot depend
on any Python. `--help` is answered before the venv is resolved, for the same
reason.

This repository is public.

## Commands

```bash
python3 -m unittest discover -s scripts/tests     # stdlib unittest; pytest discovers them too
./install.sh                                      # create .venv + link bin/qcut; optional systemd timer
bash -n bin/qcut                                  # syntax-check the dispatcher
```

Single file: `python3 -m unittest discover -s scripts/tests -p 'test_sync.py'`.

**Run the suite with the project venv** (`.venv/bin/python -m unittest …`). Two
suites need `textual`: `test_tui.py`, and `test_config.py` — its wiring cases
exec `qcut-tui.py` to prove every setting is read by a renderer. The other four
(`test_sync`, `test_usage`, `test_privacy`, `test_dispatcher`) are stdlib-only and
run anywhere. On a bare interpreter without `textual`, `test_config.py` reports
three *errors* (`SystemExit: 2` from the TUI's own dependency guard): that is a
missing package, not a Python-version failure — do not read it as one.

## Hard rules

- **Qoder's own files are never written.** No hook is installed, no
  `~/.qoder-cn/**` file is opened for writing, no runtime overhead is added to
  the host. `qcut` reads and stops there. Enforced by
  `test_privacy.py::HostDirNotWrittenTest`, which fingerprints a fixture tree
  around a real index run.
- **Metadata only, never content.** Only structured metadata fields are read:
  counts, timings, statuses, tokens, credits, identifiers. Prompt/response text
  and tool argument *values* are never stored — the only argument names ever read
  are the catalog ones (`skill`, `subagent_type`, `agent_type`, `toolName`).
  Enforced by `test_privacy.py::MetadataOnlyTest`, a sentinel fed into every slot
  the parser reads and scanned across **every text column of every table**, with a
  positive control so the scan cannot pass on a database that indexed nothing.
  Do not re-add a column that holds free text.
- **Nothing leaves the machine.** No production script imports a socket, HTTP
  client, mail or process-spawning module. **Scope it honestly:**
  `test_privacy.py::ImportSurfaceTest` inspects *this repository's* four
  production scripts — it is not a claim about third-party packages. The one
  exemption is `threading` in `qcut-tui.py`, for the background sync worker, and
  it is listed by name in `ALLOWED_EXTRA` so it stays visible.
- **Tests never touch real state.** Each suite builds its own throwaway database
  **and** its own throwaway Qoder tree in a temp directory; the real `usage.db`
  and the real `~/.qoder-cn` are never opened. A test that reads a live store is
  an instrument error, not a finding. Any test that shells out to `bin/qcut` must
  pin `QCUT_SCRIPTS`, `QCUT_DB` **and** `QCUT_QODER_DIR` — all three, because the
  launcher resolves them independently.
- **Sync must stay incremental *and* idempotent.** Repeated syncs and schema
  migrations must not duplicate rows; `qcut sync --full` is the documented
  rebuild path. Pinned by `test_repeated_syncs_do_not_duplicate_rows` and
  `test_only_the_appended_tail_is_indexed`. An offset must always land on a line
  boundary (`test_an_offset_always_lands_on_a_line_boundary`), so a trailing
  partial line is left for the next run.
- **Route origin is read, never guessed.** `provider` comes from the model string
  (`qoder-custom-<id>/<model>`) plus the configured `baseUrl` host. It is never
  inferred from the model *name*: a BYOK alias can look built-in (`qcfmodel`
  beside `qfmodel`). `test_a_model_alias_that_looks_builtin_is_still_tagged_from_its_route`
  is the case that holds this.
- **Tokens and credits are stored as reported and never converted.** Qoder's own
  routes report credits and a literal 0 for every token field; a BYOK route is the
  reverse — and it carries **no `credits` field at all**, so it is stored NULL, not
  0. Store the 0, store the credits, and let the UI name which one is in play. Do
  not derive tokens from credits, or credits from tokens.
- **A route-level zero is drawn `—`, an absent value `-`, and a non-zero is always
  shown.** Storage keeps the 0 (the rule above); the *display* must not present it
  as a measurement — a window of only Qoder-hosted responses sums to `0` input
  tokens while billing real credits, and printing that `0` claims "used nothing".
  One predicate decides it: `db._TOKENS_REPORTED_EXPR` in SQL and
  `db.row_reports_tokens` in Python, which is what feeds both the `—` and the
  `Tok reported` column, so they cannot tell two stories. The credits axis has the
  mirror predicate (`credits_carried`): summing a route that never carried the
  field gives `0`, which reads "billed nothing" and should read `-`. Both renderers
  guard on the value too (`not reported and not value`), so a wrong flag can
  suppress a zero but can never swallow a measurement —
  `test_usage.py::TokenReportingTest` and `test_tui.py::TokenDashTest` hold this,
  and `test_fmt_tok_is_the_same_function_in_both_renderers` pins the TUI and the
  headless CLI against each other.
  **Known asymmetry, deliberate:** an *empty* window reads `-` on the Usage page
  and the Dashboard (their `q_usage_summary` keeps NULL) but `0` in `qcut stats`
  (its `q_usage_kpi` flattens NULL to 0, and there is no route there to describe).
  Do not "unify" one side without moving the other, or the same window answers
  differently per surface.
- **Name is the primary key of every panel.** Each tab lists the union of what is
  installed (the static `inventory`) and what has *ever been used* (the usage
  tables), keyed by name alone, sorted by call count. Under a time window only the
  counts shrink — the list stays full. Add a new panel by name, never by a
  per-run surrogate id.
- **Windows are whole local calendar days, not "now minus N hours".**
  `window_bounds` starts at local 00:00 and ends at the current time; each tab
  keeps its own window (`DEFAULT_RANGES`). Do not switch to rolling hours or a
  shared range.
- **A table never shows a half column.** Every `DataTable` in the app is filled
  through `_fill_wide`, which keeps the longest **prefix** of its columns and
  names the dropped ones on the note line above it. Each host declares
  `WIDE_TABLES` = table id -> (columns, note id, cells lost to its surroundings);
  `test_every_datatable_in_the_source_is_registered` walks `qcut-tui.py` for
  `DataTable(id=…)` and fails on any id no host registers.
- **A row key comes from the uncapped value.** `_fill_wide` builds the key from
  the **uncapped** row, so two entities that render identically still route to
  their own detail screen. Do not cap by truncating the key — that was a real bug
  (`Grep` and `Glob` collided once the cap engaged).
- **Never commit runtime state or working notes**: `usage.db`, its `-wal`/`-shm`
  side files, `.venv/`, `__pycache__/`, `backups/`, and the session reports under
  `reports/`. Long-lived maintenance material goes in `docs/maintenance/` and
  *is* committed.

## Conventions

- **Docs are bilingual**: `README.md` (English, landing page) and
  `README.zh-CN.md` (Chinese). Edit both together and keep section order aligned;
  a section added on one side alone is drift.
- **Settings are read once, in one place.** `db.load_config()` resolves
  environment > file > `DEFAULTS`. Adding a knob means: one entry in `DEFAULTS`,
  one in `ENV_NAMES`, one in `_BOUNDS`, and one assignment where the value is
  *used*. `test_config.py` pins the default against the constant it replaces, and
  `qcut health` prints whatever `load_config()` returned, keyed by the names the
  file uses — so a knob cannot be added and go unreported.
- **A behaviour change ships with a case that fails without it.** If no test goes
  red when you undo the change, the change is not verified yet — and provoke it:
  remove the fix on purpose and read the failure.
- **A settings case must pass in someone else's shell.** Anything asserting the
  file or the defaults is asserting them *under* the environment, which outranks
  both. `test_config.py` wraps those cases in `no_settings_env()`, which hides
  every name derived from `db.ENV_NAMES`.
- Commit messages are **English** and follow Conventional Commits
  (`feat:` / `fix:` / `docs:` / `chore:`), code before docs.

## Version

The project's version lives in `pyproject.toml` (`[project].version`) and nowhere
else; it is currently `0.1.0`. There is no PyPI publish step — the tool runs from
a checkout, so the version only needs to move when a documented change ships.

## Format coupling

Qoder owns the data; this tool only reads it. **The main maintenance risk is
upstream changing the format**, and the failure mode is silent: a renamed field
reads as `NULL`, a renamed record type is simply unclaimed, the panels empty out,
`qcut sync` prints "done", CI stays green.

Two defenses, and both are mechanical:

- **Unclaimed records are counted.** `qcut-sync.py` ends its dispatch chain with a
  check that records *why* a record was not claimed (`type:<name>`,
  `type:<missing>`, `unparseable_line`) into the `unparsed` table. `qcut health`,
  the sync summary and the TUI status line all surface it. Types the code
  understands but does not need (`active-leaf`, `file-history-snapshot`, …) are in
  `NOOP_RECORD_TYPES` and claimed explicitly — `active-leaf` alone runs to tens of
  thousands here, and counting those as unparsed would make the signal useless.
- **The format surface is registered in one place.** The block at the top of
  `scripts/qcut-sync.py` (`# --- Qoder CN format registry`) declares every field
  name, path, glob, separator, tool name and regex the parser depends on.
  `qcut format` prints it, and `docs/maintenance/qoder-format.md` carries that
  output between `<!-- BEGIN GENERATED -->` markers.

`qcut format --write` regenerates that document. When upstream changes: re-run
`qcut format --write`, change the registry and the handler **in the same commit**,
and re-index into a throwaway database (`QCUT_DB=/tmp/…`) before touching a real
one. `AGENTS.md` does not list the fields — the registry does.

## Verification tiers

Named by what the claim needs, not by the tool:

- **L0** = `python3 -m unittest discover -s scripts/tests` — temp database, no host.
- **L1** = a throwaway store outside the repository, fed by the real transcripts:
  `QCUT_DB=/tmp/qcut-check.db python3 scripts/qcut-sync.py`. This is where parser
  changes are proved, and a sync report covering every table's row count is how a
  regression in a table you did not touch stays visible.
- **L2** = the real `~/.local/share/qoder-cn-usage-tracker/usage.db`. Needs the
  owner's go-ahead; a destructive step takes a snapshot first (`qcut sync --full`
  does this automatically, keeping the last five under `backups/`).

## Invariants and how to check them

Three promises are load-bearing enough that prose is not proof.

```bash
python3 -m unittest scripts.tests.test_privacy
```

1. **Nothing leaves the machine** — an AST import allowlist over the four
   production scripts, with forbidden roots named broadly so adding one is a test
   failure rather than a quiet convention change.
2. **Qoder's own files are never written** — a fixture tree is fingerprinted
   before and after the real indexer runs over it; one changed byte fails it.
3. **Metadata only, never content** — sentinels in every argument slot, scanned
   across every text column of every table, with a positive control.

## Known-good facts (measured, not assumed)

Re-measure before restating any of these; the commands are in
[`docs/maintenance/`](docs/maintenance/).

- 173 transcript files / 362 MB → 5.17 s cold, 0.10 s warm, 133 MB peak RSS,
  19 MB database. Measured 2026-10-12; the tree is the owner's live transcripts,
  so the file count moves between two runs in the same minute.
- A tool result with no `toolUseResult.kind` is 14,770 of 33,531 results on this
  machine. Treating those as "pending" reported finished work as unfinished.
- `input_tokens` is the whole prompt and `cache_read_tokens` a subset of it: the
  largest single request here is 357,240 in / 358,703 API total, but re-adding the
  cache read makes it 715,055 — past the model's 400k window. That is why
  `Usage Total` is shown *beside* `API Total` and never instead of it.
- 133 of 173 transcript files are `agent-a<Type>-<hash>.jsonl` subagent
  transcripts carrying the parent session id.
- The route split is complementary, not partially missing: 29,557 responses carry
  a real credit and a literal `0` in all four token fields, 71 carry real tokens
  and **no `credits` field**. Asking "did this row report a token?" by
  `credits IS NULL` and by "some token field is non-zero" disagrees on **0 rows**
  — which is what lets one predicate drive both the `—` and `Tok reported`.
