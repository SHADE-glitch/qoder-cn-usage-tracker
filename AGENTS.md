# AGENTS.md

Guidance for AI coding agents working in this repository.

> **Dimension.** This repository is dimension **A — open source, strict** in the machine-wide
> `CONVENTIONS.md` (outside this repository); the shared rules live in `STANDARD.md` and the
> dimension-A detail in `~/.agents/standards/dimensions/a.md`.
>
> **Shared standard.** Root file names, the process-draft location (`docs/reports/`), the
> `CHANGELOG` entry format and the runtime ignore list are defined once in the machine-wide
> `STANDARD.md` (outside this repository); the dimension-A rules — the language policy, CI
> version pinning and entry commands, and the test entry command — live in
> `~/.agents/standards/dimensions/a.md`. Neither is restated here.
>
> **Push over SSH, never HTTPS.** Verify `git remote get-url --push origin` starts with `git@`
> before pushing; if it starts with `https://`, fix it first — never push over HTTPS.

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

**The eight tab slots are shared with the sibling trackers, not owned by this repository.**
`Dashboard · Tools · Skills · Agents · Plugins · MCP · Tokens · Usage` is the same list, in the
same order, as `cbut`'s and `tcut`'s — only the data behind a slot differs. That is the point:
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
  is a keyed field and needs no text at all; it is the only one measured on real
  transcripts here.
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
  `Tokens` reports **credits** alongside the tokens a route actually carried (a BYOK
  route carries no `credits` field at all, so it is stored NULL, not 0), and `Usage` is
  per-response only where a response was recorded. The caveat is part of the contract;
  delete it and the panel starts lying.
  The *set* is machine-checked too, and against literals rather than against
  `TABS`: `scripts/qcut-tui.py` builds its panes in a loop over that tuple, so
  comparing the built panes back to it holds for any tab set at all — including
  one missing a slot, which is the drift the rule exists to catch.
  `test_tui.py::test_the_eight_tabs_are_present_and_in_order` spells out the
  eight ids and the eight visible labels instead. A sibling checkout is not
  importable from here, so this is the only place in the suite where the family
  contract is read; label text comes from the tab bar (`tc.get_tab(id).label_text`),
  because `TabPane` carries no label in Textual 8.2.8.
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
- **Never commit runtime state or working notes**: `usage.db`, its `-wal`/`-shm`
  side files, `.venv/`, `__pycache__/`, `backups/`, and the session reports under
  `reports/`. Long-lived maintenance material goes in `docs/maintenance/` and
  *is* committed.

## Conventions

- **This UI is a look-alike, and the chrome is part of the claim.** Tab titles are
  plain words (`Dashboard`, `MCP`) with no emoji, and the app defines no canvas or
  status-bar `background` of its own — both are Textual defaults, as in the tool
  this one mirrors. The emoji that *do* appear in the READMEs sit in the feature
  bullets and the first column of the tab table; they document the tabs, they are
  not the tab labels. `test_tui.py::ChromeTest` pins the two shape facts.
- **A flag is registered only where a command reads it.** `--limit` belongs to the
  six reports that pass it to a query, and `--range` only to `show`, which is the
  one command with a window argument. A flag the parser accepts and the report
  ignores reads as a filter that worked.
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
- Commit messages are **English** and follow Conventional Commits
  (`feat:` / `fix:` / `docs:` / `chore:`), code before docs.

## Recording conventions

- **`feat` commits are out of scope, by class.** This is an original project with no upstream, so a
  feature is the product, not a droppable deviation — features are documented in the READMEs. The
  exclusion is one regex over the commit subject inside the check; it must not decay into a
  per-commit skip flag.
- `kind` ∈ `fix` | `perf` | `taste` | `guard` | `revert` | `chore`, cut by **who may demand a
  revert**: bug → `fix`; measurable degradation only → `perf`; only my taste → `taste` (zero
  obligation, discardable on an upgrade); no behaviour change, detects drift → `guard`; withdraws
  earlier work → `revert`; cleanup owed nothing either way → `chore`.
- **An empty covered window is legal only when the header says so.** If the window contains covered
  commits but the record has no entries, the check fails: a pass over an empty set proves nothing.
- `Symptom` names the mechanism, never the session: no prompt text, no command lines, no tool
  argument values, no real paths from `~/.qoder-cn`. The record is held to the same
  metadata-not-content rule as the code.
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

## Listed on the GitHub profile

**This repository is not on the owner's profile front page, and that is not an oversight.** It is a
**private** repository, and `SHADE-glitch/SHADE-glitch` lists public repositories only — the two
whitelisted private repos are the sole exception, and this is not one of them. The profile's own
`AGENTS.md` is the authoritative rulebook, and its §1 is explicit on both counts: naming another
private repo needs the owner's spoken approval first, and **a repo must never be flipped to public
just so it can be listed** — that is a privacy decision, not a profile edit.

So there is no listing obligation today, and this section exists to say so rather than to be
silently absent. If the repo is ever made public **and** added to the profile, this section becomes
the reciprocal of the profile's §6 index, and these facts must then be kept true on the front page:

- **Rename / transfer / delete** — the profile link dies; fix the row.
- **Purpose drift** — the profile's one-line description must still be true; the claims it copies —
  *"no network"*, "dev tooling" — are **promises**, not slogans.
- **LICENSE change** — the profile's license badge must equal this repo's `LICENSE`.
- **Version / runtime declaration change** — the matching badge must equal this repo's declaration.
- **Freeze / archive** — say so on the profile; never leave a stale "maintained".
- **Public ↔ private** — a repo that goes private again must be **delisted**.

**The GitHub About description is under the same rule.** It is a promise with 100 characters, and it
must describe *this* repository, not the family. Copying a sibling's About verbatim gets one thing
wrong here, and misses one:

- **The product name is part of the promise.** "CodeBuddy Code" in a Qoder repository's About is a
  copy-paste slip a reader cannot resolve.
- **"credits vs tokens" is not decoration.** 29,557 responses on this machine carry a real credit and
  a literal `0` in all four token fields; 71 carry real tokens and no credits field at all. An About
  that says only "token analytics" describes the 71 rows and misrepresents the other 29,557.
- **What *may* be copied** is the strongest true claim here: this repository really does store
  metadata only — `test_privacy.py::MetadataOnlyTest` scans sentinels across every text column of
  every table — so "no message bodies" is true, and worth saying.

<dcp-system-reminder>
You've been iterating for a while after the last user message.

If there is a closed portion that is unlikely to be referenced immediately (for example, finished research before implementation), use the compress tool on it now.

Compressed block context:
- Active compressed blocks in this session: 1 (b5)
</dcp-system-reminder>
