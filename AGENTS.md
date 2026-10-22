# AGENTS.md

Guidance for AI coding agents working in this repository.

> **Dimension.** A — open source (strict), per `CONVENTIONS.md` §0; the shared rules live once in
> `~/.agents/standards/STANDARD.md` (§1 root files, §2 process drafts, §3 CHANGELOG entries,
> §4 runtime ignore list, §5 conflict rule, §6 remotes/pushing, §8 profile listing) and the
> dimension-A detail in `~/.agents/standards/dimensions/a.md`. Neither is restated here.
>
> **Read these before the first edit** (bare `@` imports — CodeBuddy expands them; opencode reads
> the same two from `opencode.json`):
> @~/.agents/standards/STANDARD.md
> @~/.agents/standards/dimensions/a.md

## What this is

A **passive** usage tracker for the **Qoder CN CLI**: it parses the session transcripts
the CLI already writes and builds a local SQLite index, then shows it in an eight-tab
Textual TUI (plus a headless `qcut stats` CLI). The IDE/desktop Qoder app is out of
scope: it does not write this transcript tree, so there is nothing here to index for it.
Three layers, in dependency order:

1. `scripts/qcut-sync.py` — the parser and indexer. Reads **only**
   `~/.qoder-cn/projects/**/*.jsonl` (plus the small `skills/`, `plugins/`,
   `mcp.json` and `settings.json` inventory files), read-only, and classifies
   tool / skill / agent / MCP calls into
   `~/.local/share/qoder-cn-usage-tracker/usage.db`.
2. `scripts/qcut_db.py` — the shared data layer (schema, queries, calendar-day
   windows, snapshots). Imported by the TUI, the stats CLI and the tests.
3. `scripts/qcut-tui.py` (Textual) and `scripts/qcut-stats.py` (headless CLI).

**Positioning: this is an imitation of `cbut`, not a port of it.** `cbut`
(`codebuddy-usage-tracker`) is the sibling tracker for CodeBuddy Code and the model this
project deliberately copies; `tcut` is the third sibling. What is copied is the family
shape — a passive reader over files the host already writes, the same eight tab slots in
the same order, per-tab calendar-day windows, name-keyed unions, two-level drill-downs.
What is **not** copied is any category Qoder CN CLI does not record: per-request duration,
HTTP status and money have no Qoder field, so they stay absent and the note says why.
The Qoder-specific additions — credits beside tokens, the `—`/`-` distinction for a
route that reports no token, one **Usage Total** as the token total (the panels print no
other), cache read / miss / write — are this project's own and are first-class, not
deviations to be sanded off
for parity. When the two disagree, the
source of truth is what Qoder's transcripts contain, never cbut's screen. The READMEs
carry the user-facing version under "Relationship to cbut".

**There are two writers, not one.** `qcut sync` is the only thing that indexes
transcripts, but the TUI also writes: `on_mount` runs `db.ensure_schema()` and
the sync worker it starts on launch (or on `r`) calls the same indexer the CLI
does. Anything that assumes "the TUI is read-only" is wrong — reason about locks
accordingly. The headless
CLI is read-only except for its explicit maintenance subcommands (`backup`,
`restore`, `vacuum`, `format --write`).

`bin/qcut` is a bash dispatcher. **It is bash on purpose**: it must still run and
print a useful error when the TUI venv is missing or broken, so it cannot depend
on any Python. `--help` is answered before the venv is resolved, for the same
reason.

**The eight tab slots are shared with the sibling trackers, not owned by this repository.**
`Dashboard · Tools · Skills · Agents · Plugins · MCP · Tokens · Usage` is the same list, in the
same order, as `cbut`'s (the model this project imitates) and `tcut`'s — only the data behind
a slot differs. That is the point:
a reader switching between the three tools finds the same shape and learns which product writes
what. It also means a slot must be filled with **what this product's files actually contain** and
must **name what it does not contain** — never a placeholder that reads as `0`, and never a deleted
tab. See "Hard rules".

This repository is private.

## Commands

```bash
python3 -m unittest discover -s scripts/tests     # stdlib unittest; pytest discovers them too
./install.sh                                      # create .venv + link bin/qcut; optional systemd timer
bash -n bin/qcut                                  # syntax-check the dispatcher
```

Single file: `python3 -m unittest discover -s scripts/tests -p 'test_sync.py'`.

**Run the suite with the project venv** (`.venv/bin/python -m unittest …`). Two
suites need `textual`: `test_tui.py`, and `test_config.py` — its wiring cases
exec `qcut-tui.py` to prove every setting is read by a renderer. The other thirteen
are stdlib-only and run anywhere. That count is checked against `scripts/tests/`
rather than typed (`test_maintenance_docs.py`), because a suite added later would
otherwise make this sentence a lie by existing. On a bare interpreter without `textual`, `test_config.py` reports
three *errors* (`SystemExit: 2` from the TUI's own dependency guard): that is a
missing package, not a Python-version failure — do not read it as one.

## CI

GitHub Actions runs on every `push` and `pull_request` (`.github/workflows/ci.yml`, Python matrix of
**3.11 and 3.14** — the floor and the declared upper bound):

```bash
python3 -m pip install -r requirements.txt
python3 -m unittest discover -s scripts/tests
```

The checkout must fetch full history (`fetch-depth: 0`): the record-coverage test walks
`git log <anchor>..HEAD` back to the coverage anchor, which a shallow clone cannot resolve.

**It must stay green.** A red CI is a broken contract, not a warning: this is the same command as
the L0 verification tier below, so a change that fails here fails everywhere. Run it locally before
pushing — do not leave the first run to CI.

**Green CI does not certify the interpreter you are using.** CI runs Python 3.11 and 3.14; the local
venv is created at 3.13 (`install.sh`) and the system `python3` may be newer — the version matrix and
what each number means live in
[`docs/maintenance/compatibility.md`](docs/maintenance/compatibility.md). If a change depends on
interpreter behaviour, say which version you actually ran, and consider widening the CI matrix
rather than assuming those two speak for all of them.

- **Keep CI in step with the code.** Update `.github/workflows/ci.yml` in the *same change* that
  makes it stale — never as a later cleanup.
- **New or renamed tests need no CI edit** as long as CI runs the discovery command
  (`python3 -m unittest discover -s scripts/tests`); it does, so it picks them up automatically.
  Only touch CI if the *command itself* changes.
- **Environment changes** — a new dependency, a Python version bump, or a new system tool — mean
  updating the workflow's setup/install steps.
- **Renamed or moved code**: the record-coverage test watches a declared list of code paths
  (`CODE_PATHS` in `test_record_coverage.py`). If a watched path moves, update that list; the test
  goes red until you do.
- **After a refactor**, confirm CI still exercises the real code and the declared paths still cover
  it. A green CI that no longer touches the changed code is worse than a red one.
- **A new verification tier** (e.g. a live/TUI layer) — decide explicitly whether CI runs it; do not
  add it silently.
- If what CI runs changes, update this section too. CI is a signal, not a gate, until branch
  protection is enabled — read the result after every push.

## Release / version

The project's version lives in `pyproject.toml` (`[project].version`) and nowhere else; it is
currently `0.1.0`. Bump it in that one file when a release is cut — the READMEs and `CHANGELOG.md`
describe behaviour and do not carry a version to keep in sync. There is no PyPI publish step: the
tool runs from a checkout, so the version only needs to move when a documented change ships.

## Hard rules

- **Qoder's own files are never written.** No hook is installed, no
  `~/.qoder-cn/**` file is opened for writing, no runtime overhead is added to
  the host. `qcut` reads and stops there. Enforced by
  `test_privacy.py::HostDirNotWrittenTest`, which fingerprints a fixture tree
  around a real index run.
- **Metadata only, never content.** Only structured metadata fields are read:
  counts, timings, statuses, tokens, credits, identifiers. Prompt/response text
  and tool argument *values* are never stored — the only argument names ever read
  are the catalog ones registered in `TOOL_ARG_FIELDS` (`skill` and its `command`
  alias, `subagent_type` and `agent_type`, `toolName` and its `tool_name` alias).
  The two aliases are each half of a real branch: a Skill call that names itself
  only in `command`, or an MCP target only in `tool_name`, must still be recorded —
  and `test_sync.py` has a case that goes through each branch rather than only
  through the preferred spelling.
  One field is read *out of* text rather than out of a keyed slot: a slash
  command's name inside a `<command-name>` marker. The marker is the boundary —
  `CMD_RE` keeps the name, the prose around it is dropped, and the scan runs over
  **user** records only, because a marker in an assistant block is the model
  quoting it, not running it (D-009). A third source, `toolUseResult.commandName`,
  is a keyed field and needs no text at all; it is read only on a non-Skill result,
  because Qoder writes the skill's own name there on a Skill call.
  Enforced by `test_privacy.py::MetadataOnlyTest`, a sentinel fed into every slot
  the parser reads and scanned across **every text column of every table**, with a
  positive control so the scan cannot pass on a database that indexed nothing.
  Do not re-add a column that holds free text.
- **Nothing leaves the machine.** **Scope it honestly:**
  `test_privacy.py::ImportSurfaceTest` inspects *this repository's* four production
  scripts — it is not a claim about third-party packages. Each script declares its
  **whole** import surface in `MODULE_ALLOWLIST`, and anything outside it fails: an
  allowlist refuses a module nobody ever thought to forbid, where a denylist can
  only refuse the ones someone named in advance. A declared module the file no
  longer uses fails too, so the list cannot quietly grow wider than the code. Two
  things a list of imports cannot see are checked apart: a call reached through a
  module that is already declared (`os.system` — `os` is on three allowlists) in
  `FORBIDDEN_CALLS`, and a module named inside a string in `DYNAMIC_LOADERS`, whose
  only entry is `load_sync` loading its sibling by path. Nothing imports
  `threading`: the TUI's sync worker runs on Textual's own pool.
- **Tests never touch real state.** Each suite builds its own throwaway database
  **and** its own throwaway Qoder tree in a temp directory; the real `usage.db`
  and the real `~/.qoder-cn` are never opened or read. A test that reads a live store is
  an instrument error, not a finding. `_hermetic.py` refuses file reads, directory walks,
  database opens and unpinned child launches. Any test that shells out to `bin/qcut` must
  pin `QCUT_SCRIPTS`, `QCUT_DB` **and** `QCUT_QODER_DIR` — all three, because the
  launcher resolves them independently.
- **Sync must stay incremental *and* idempotent.** Repeated syncs and schema
  migrations must not duplicate rows; `qcut sync --full` is the documented
  rebuild path. Pinned by `test_repeated_syncs_do_not_duplicate_rows` and
  `test_only_the_appended_tail_is_indexed`. An offset must always land on a record
  boundary (`test_an_offset_always_lands_on_a_line_boundary`), so a trailing
  *partial* line is left for the next run — while a **complete** record that has not
  been terminated yet is consumed, because a writer flushes the record and its
  newline as two writes and a crash sits between them. A file that was
  rewritten rather than appended to is re-read from zero: same length with a moved
  mtime, a shortened file, or an offset that no longer follows a record boundary
  (`_needs_reset`, `test_sync.RewrittenFileTest`).
  **One table is deliberately not idempotent:** `unparsed(reason, count)` carries no
  file attribution, so a re-read adds a file's earlier bad lines a second time.
  `qcut sync --full` clears and re-derives it, which is the only way to correct it —
  read `unparsed` as "at least this many", never as an exact count.
  **Schema v2 backfills only on `--full`.** Its agent links (`parent_call_id`,
  `agent_id`) and `compactions` rows come from records an incremental run has no
  reason to revisit; existing rows stay NULL/empty until a full pass. That is why
  the upgrade note in both READMEs asks for one `qcut sync --full`.
  **Schema v3 is an in-place data repair**, applied by `ensure_schema()` when it
  opens a v1/v2 index: drop the false commands copied from Skill results, split
  `<plugin>:<skill>` names, and move direct `mcp__server__tool` rows from builtin
  tools into MCP. It is idempotent and needs no full pass.
- **Route origin is read, never guessed.** `provider` comes from the model string
  (`qoder-custom-<id>/<model>`) plus the configured `baseUrl` host. It is never
  inferred from the model *name*: a BYOK alias can look built-in (`qcfmodel`
  beside `qfmodel`). `test_a_model_alias_that_looks_builtin_is_still_tagged_from_its_route`
  is the case that holds this.
- **Tokens and credits are stored as reported and never converted.** A Qoder-hosted
  route with `QODERCN_EXPOSE_TOKEN_USAGE` unset stores real credits and a literal 0
  in every token field; with the flag set it stores real tokens and **may also
  carry credits in the same row**; a BYOK route carries tokens and **no `credits`
  field at all**, so it is stored NULL, not 0. Store what was written. Do not
  derive tokens from credits or `context_usage_ratio`, and do not derive credits
  from tokens. Zeros already in the transcripts are history and cannot be repaired
  by qcut — the only lever is the CLI's own environment at call time.
- **A run's cost is joined, never inferred.** A sidechain response belongs to a run
  only when its own record carries `parent_tool_use_id`; `agentId` is stored as the
  companion identifier. Never match a run to responses by timestamp, agent type or
  file adjacency. A run with no linked response keeps `requests = 0` and `-` token
  cells; `test_a_main_chain_response_has_no_invented_run_link` and
  `test_a_sidechain_response_keeps_its_launching_run_link` hold both directions.
- **Compaction is metadata only.** Read `compact_boundary.compactMetadata` (trigger,
  pre/post tokens, summarized count, duration) and never the summarized messages,
  which are prose. `compacted_tokens = max(pre - post, 0)` is a display measure of
  context dropped; it is shown **beside** token totals and never added to them. A
  duplicate boundary is `INSERT OR IGNORE`d, and missing metadata is not fabricated.
- **Coverage tracking was removed on purpose.** There is no capture boundary, no
  coverage percentage, and no `credits-only` / `unmeasured` partition. The owner
  runs every future CLI call with the token flag, so the old ledger described
  history rather than the tool. Do not reintroduce it without a request;
  `token_covered` survives only as the predicate that decides a `—`.
- **A route-level zero is drawn `—`, an absent value `-`, and a non-zero is always
  shown.** Storage keeps the 0 (the rule above); the *display* must not present it
  as a measurement — a window of only legacy hosted responses sums to `0` input
  tokens while billing real credits, and printing that `0` claims "used nothing".
  The `—` decision is `token_covered > 0` at aggregate level and
  `row_reports_tokens` per row. The credits axis has the mirror predicate
  (`credits_carried`): summing a route that never carried the field gives `0`,
  which reads "billed nothing" and should read `-`. Both renderers guard on the
  value too (`not reported and not value`), so a wrong flag can suppress a zero
  but can never swallow a measurement —
  `test_usage.py::TokenReportingTest` and `test_tui.py::TokenDashTest` hold this,
  and `test_fmt_tok_is_the_same_function_in_both_renderers` pins the TUI and the
  headless CLI against each other.
  **Known asymmetry, deliberate:** an *empty* window reads `-` on the Usage page
  and the Dashboard (their `q_usage_summary` keeps NULL) but `0` in `qcut stats`
  (its `q_usage_kpi` flattens NULL to 0, and there is no route there to describe).
  Do not "unify" one side without moving the other, or the same window answers
  differently per surface.
- **qcut never edits a shell profile or the CLI's environment.** The capture flag
  is read by the Qoder CN CLI process, not by qcut, so setting it from inside qcut
  would be too late and out of bounds. `qcut env` is a read-only advisor: it
  names `db.TOKEN_ENV_NAME`, reports the current process state, and prints the
  exact line to add. It works with an absent database and must not create one.
  `install.sh` does not touch profiles either.
- **"No such thing" and "nothing in this window" are different answers.** An
  aggregate over `WHERE name = ?` cannot tell them apart — both return
  `COUNT(*) = 0`, so an unknown entity printed as a confident report about something
  that does not exist. `qcut show` now asks existence separately
  (`db.q_entity_exists`, deliberately **window-free**) and refuses an unknown name
  with rc 1; a known-but-idle name keeps its honest zeros and
  `(no calls in this window)`. Anything adding a per-entity view copies that split
  instead of reusing a summary as an existence test
  (`test_usage.py::QueryTest`, `test_stats.py::CliGuardTest`).
- **A tab slot is filled with what the data is, and names what it is not.** All eight
  slots are shared with `cbut` and `tcut`, so this repository never trims the tab set and
  never ships an empty panel: an empty token panel reads as "0 tokens" or as a broken
  widget, and neither is true. Where a slot's meaning is narrower here than in the
  sibling, the note line above the table says so in the same round —
  `Tokens` reports **credits** and the tokens a route actually carried (a BYOK
  route carries no `credits` field at all, so it is stored NULL, not 0), and
  `Usage` is per-response only where a response was recorded; its `—` means that
  call reported no token, and its note says credits and tokens can coexist. Both
  tables carry **one** `Provider` column: `Built-in` for Qoder's own route,
  otherwise the stored BYOK channel name. A second one-word `Route` column is gone
  — it repeated the family and cost the width that made the table drop its tail on
  a normal terminal. Usage Total leads the measure family, then credits; the
  caveat that a legacy sum only describes the rows that reported a token is part of
  the contract; delete it and the panel starts lying.
  The *set* is machine-checked too, and against literals rather than against
  `TABS`: `scripts/qcut-tui.py` builds its panes in a loop over that tuple, so
  comparing the built panes back to it holds for any tab set at all — including
  one missing a slot, which is the drift the rule exists to catch.
  `test_tui.py::test_the_eight_tabs_are_present_and_in_order` spells out the
  eight ids and the eight visible labels instead. A sibling checkout is not
  importable from here, so this is the only place in the suite where the family
  contract is read; label text comes from the tab bar (`tc.get_tab(id).label_text`),
  because `TabPane` carries no label in Textual 8.2.8.
- **Name is the primary key of every panel.** An entity tab defaults to what its
  window actually used, matching Tokens; `a` switches it to the union of what is
  installed (the static `inventory`) and what has *ever been used* (the usage
  tables). Both views are keyed by name alone. Add a new panel by name, never by a
  per-run surrogate id; the headless reports expose the same switch as `--all`.
- **Windows are whole local calendar days, not "now minus N hours".**
  `window_bounds` starts at local 00:00 and ends at the current time; each tab
  keeps its own window (`DEFAULT_RANGES`). Do not switch to rolling hours or a
  shared range.
- **A table never shows a half column.** Every `DataTable` in the app is filled
  through `_fill_wide`, which keeps the longest **prefix** of its columns and
  names the dropped ones on the note line above it. The plan **re-runs on
  `on_resize`**: widening gives the columns back and narrowing takes them away
  without losing rows or row keys (`test_tui.py::ResizeTest` — before those cases
  the mount-time layout was all that had ever been measured, so a re-plan that
  lost rows or never returned a column read as intended behaviour). Each host declares
  `WIDE_TABLES` = table id -> (columns, note id, cells lost to its surroundings);
  `test_every_datatable_in_the_source_is_registered` walks `qcut-tui.py` for
  `DataTable(id=…)` and fails on any id no host registers.
- **A row key comes from the uncapped value.** `_fill_wide` builds the key from
  the **uncapped** row, so two entities that render identically still route to
  their own detail screen. Do not cap by truncating the key — that was a real bug
  (`Grep` and `Glob` collided once the cap engaged).
- **Never commit runtime state or working notes** — `~/.agents/standards/STANDARD.md` §4 and §2 hold the
  ignore list and the process-draft location; this repo adds `usage.db`, its `-wal`/`-shm`
  side files and `backups/`. Long-lived maintenance material goes in `docs/maintenance/`
  and *is* committed.

## Conventions

- **This UI is a look-alike of cbut, and the chrome is part of the claim.** Tab titles
  are plain words (`Dashboard`, `MCP`) with no emoji, and the app defines no canvas or
  status-bar `background` of its own — both are Textual defaults, as in the tool this
  one mirrors. The emoji that *do* appear in the READMEs sit in the feature bullets and
  the first column of the tab table; they document the tabs, they are not the tab
  labels. `test_tui.py::ChromeTest` pins the two shape facts.
- **The custom-range picker is cbut's picker, geometry included.** Height-3 shortcut
  chips, a blank row between the preset row and the start card, another between the two
  cards, and the default bordered "follows now" switch. It carries **cbut's six chips**
  — `Today`, `1d`, `7d`, `14d`, `30d`, `All time` — not one chip per Select option:
  the Select's 2 days / 3 days are shortcuts the calendar can still express, while a
  seventh height-3 chip needs 60 cells in a 53-cell column and clips `All time`.
  The box measures 79x30 and is shared by every windowed tab; Plugins hides the control
  because its window is all-time. At 80x30 it fits exactly, and on a shorter terminal
  the modal screen scrolls by the overflow instead of crushing the chrome. Both facts
  are measured in `test_tui.py::DateRangeTest::test_the_picker_keeps_cbuts_roomy_layout`
  and `test_a_short_terminal_scrolls_the_picker_instead_of_crushing_it`.
- **A flag is registered only where a command reads it.** `--limit` belongs to the
  eight reports that pass it to a query (`tools`, `skills`, `agents`, `mcp`,
  `projects`, `commands`, `recent`, `show`), `--all` only to the four entity
  reports that can widen to installed ∪ ever-used (`tools`, `skills`, `agents`,
  `mcp`), and `--range` only to `show` and `projects`, the two commands with a
  window argument. A flag the parser accepts and the report ignores reads as a
  filter that worked.
- **Settings are read once, in one place.** `db.load_config()` resolves
  environment > file > `DEFAULTS`. Adding a knob means: one entry in `DEFAULTS`,
  one in `ENV_NAMES`, one in `_BOUNDS`, and one assignment where the value is
  *used*. `test_config.py` pins the default against the constant it replaces, and
  `qcut health` prints whatever `load_config()` returned, keyed by the names the
  file uses — so a knob cannot be added and go unreported.
- **A behaviour change ships with a case that fails without it.** If no test goes
  red when you undo the change, the change is not verified yet — and provoke it:
  remove the fix on purpose and read the failure.
- **A case may not depend on the runner's clock or scheduler.** Two shapes that
  pass locally and fail on the runner, both seen here: `datetime.now()` fed to an
  assertion about a *day* boundary (`window_bounds("7d", now)` versus
  `window_bounds("7d", now - 3h)` — three hours back crosses into yesterday at
  01:35 UTC and passes at 09:35 +08:00, same code), and two `pilot.click` lines
  with no `await pilot.pause()` between them, where whether the second click lands
  before the first has taken effect is scheduling. Anchor cases to fixed hours, and
  let `test_tui.py::ClickCadenceTest` enumerate the click pairs. **CI's matrix runs
  in UTC** — a local green says nothing about a date-anchored assertion here.
- **A settings case must pass in someone else's shell.** Anything asserting the
  file or the defaults is asserting them *under* the environment, which outranks
  both. `test_config.py` wraps those cases in `no_settings_env()`, which hides
  every name derived from `db.ENV_NAMES`.

## Docs & commits

- **Docs are bilingual**: `README.md` (English, landing page) and
  `README.zh-CN.md` (Chinese). Edit both together and keep section order aligned;
  a section added on one side alone is drift.
- Commit messages are **English** and follow Conventional Commits
  (`feat:` / `fix:` / `docs:` / `chore:`), code before docs.

## Recording conventions

Common CHANGELOG rules: `~/.agents/standards/STANDARD.md` §3.

- The `feat` exclusion is one regex over the commit subject inside the check; it must not decay
  into a per-commit skip flag. Features are documented in the READMEs.
- The record is held to the same metadata-not-content rule as the code.
- Run the coverage check before committing production code:
  `python3 -m unittest scripts.tests.test_record_coverage`. It is part of the suite.

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

`qcut format --write` regenerates that document, and **refuses** a target that lost
either marker rather than appending a second copy of the surface below text nobody
will regenerate again — a stale block inside the markers is replaced, a stale block
outside them is invisible. `--out` points the writer at another path, which is what
makes the splice and the refusal testable without rewriting a tracked file.
When upstream changes: re-run
`qcut format --write`, change the registry and the handler **in the same commit**,
and re-index into a throwaway database (`QCUT_DB=/tmp/…`) before touching a real
one. `AGENTS.md` does not list the fields — the registry does.

Three checks keep that claim from going rhetorical, and all three read the source
rather than trusting prose (`test_format_registry.py`, `test_maintenance_docs.py`):

- the registry and the parser are locked **both ways** — a field read without being
  registered fails, and a registered field the parser no longer reads fails too;
- `docs/maintenance/qoder-format.md` must equal what `qcut format` prints right now,
  so a registry change that skipped `--write` is caught by the suite, not by luck;
- layout values, separators and tool names may not be re-typed at a call site, and
  every registry constant must actually be used — a declared-but-unused constant is
  the same false claim as a listed-but-unread field.

The sets a handler *branches on* are checked too: `CLAIMED_RECORD_TYPES` must equal
the record types the dispatch chain names, or a type counts as parsed while nothing
reads it (which is how `system` sat in the wrong set).

## Verification tiers

Named by what the claim needs, not by the tool:

- **L0** = `python3 -m unittest discover -s scripts/tests` — temp database, no host.
- **L1** = a throwaway store outside the repository, fed by the real transcripts:
  `QCUT_DB=/tmp/qcut-check.db python3 scripts/qcut-sync.py`. This is where parser
  changes are proved, and a sync report covering every table's row count is how a
  regression in a table you did not touch stays visible.
- **L2** = the real `~/.local/share/qoder-cn-usage-tracker/usage.db`. Needs the
  owner's go-ahead; a destructive step takes a snapshot first (`qcut sync --full`
  does this automatically). Retention lives in `db.snapshot()` — every snapshot
  path prunes to the newest five, names itself with a `-1`/`-2` suffix rather than
  overwriting the copy it was meant to keep, and skips the snapshot entirely when
  the index holds nothing.

## Invariants and how to check them

Three promises are load-bearing enough that prose is not proof.

```bash
python3 -m unittest scripts.tests.test_privacy
```

1. **Nothing leaves the machine** — an AST import **allowlist**: each production
   script declares its entire module surface, so an import nobody has forbidden is
   still a test failure, and so is a declaration the code no longer uses. A call
   reached through an already-declared module (`os.system`) and a module named in a
   string are checked separately, each with a control that proves the check fires.
2. **Qoder's own files are never written** — a fixture tree is fingerprinted
   before and after the real indexer runs over it; one changed byte fails it.
3. **Metadata only, never content** — sentinels in every argument slot, scanned
   across every text column of every table, with a positive control.

## Known-good facts (measured, not assumed)

Re-measure before restating any of these; the commands are in
[`docs/maintenance/`](docs/maintenance/).

- 489 transcript files / 522 MB → 6.79 s cold, 0.10 s warm, 133,928 KB peak RSS,
  26,189,824-byte database. Measured 2026-10-21 on Qoder CN CLI `1.1.75`; the tree
  is the owner's live transcripts, so the file count moves between two runs in the
  same minute (a scan seconds after this index already saw more usage rows).
- A tool result with no `toolUseResult.kind` is 19,816 of 46,011 results on this
  machine. Treating those as "pending" reported finished work as unfinished.
- `input_tokens` is the whole prompt and `cache_read_tokens` a subset of it: the
  largest single request here reads 357,240 in / 358,703 on the provider's own
  total, but re-adding the cache read makes it 715,055 — past the model's 400k
  window. Usage Total re-adds the hit by the family's definition; the provider's
  total is stored but is not printed as a second panel total beside it.
- 444 of 489 transcript files are `agent-a<Type>-<hash>.jsonl` subagent
  transcripts carrying the parent session id.
- On the legacy index most hosted calls predate the token flag, so their token
  cells read `—` while their credits are real; only a small BYOK slice measured
  tokens. The flag makes coexistence the normal shape for a new hosted call.
  Re-measure with `qcut stats` / `qcut models` rather than quoting a count; the
  coverage ledger that used to report this was removed by request.

## Listed on the GitHub profile

**This repository is listed on the owner's profile front page**, and this section is the reciprocal
of `SHADE-glitch/SHADE-glitch`'s §6 index — that file is the authoritative rulebook. It became
**public on 2026-10-22** by the owner's explicit decision (the visibility call came first; the row
followed in the same round, not the other way round). These facts must stay true on the front page:

- **Rename / transfer / delete** — the profile link dies; fix the row.
- **Purpose drift** — the profile's one-line description must still be true; the claims it copies —
  *"no network"*, "dev tooling" — are **promises**, not slogans.
- **LICENSE change** — the profile's license badge must equal this repo's `LICENSE`.
- **Version / runtime declaration change** — the matching badge must equal this repo's declaration.
- **Freeze / archive** — say so on the profile; never leave a stale "maintained".
- **Public ↔ private** — a repo that goes private again must be **delisted**.

**The GitHub About description is under the same rule.** It is a promise with at most 350
characters, and it must describe *this* repository, not the family. Copying a sibling's About verbatim gets one thing
wrong here, and misses one:

- **The product name is part of the promise.** "CodeBuddy Code" in a Qoder repository's About is a
  copy-paste slip a reader cannot resolve.
- **"credits vs tokens" is not decoration.** Most legacy hosted responses on this machine carry a
  real credit and a literal `0` in all four token fields. An About that says only "token analytics"
  describes a fraction of the index, so it must name credits too (and the `—` that marks an
  unreported token) rather than imply every call is measured.
- **What *may* be copied** is the strongest true claim here: this repository really does store
  metadata only — `test_privacy.py::MetadataOnlyTest` scans sentinels across every text column of
  every table — so "no message bodies" is true, and worth saying.
