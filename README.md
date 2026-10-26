<p align="right"><a href="README.md"><b>English</b></a> | <a href="README.zh-CN.md">简体中文</a></p>

# 🐾 Qoder CN CLI Usage Tracker (`qcut`)

![Qoder CN CLI](https://img.shields.io/badge/Qoder%20CN%20CLI-reads%20local%20transcripts-blue?logo=robotframework&logoColor=white)
![Python](https://img.shields.io/badge/python-3.11%2B-blue?logo=python&logoColor=white)
![SQLite](https://img.shields.io/badge/storage-SQLite-003B57?logo=sqlite&logoColor=white)
![Textual](https://img.shields.io/badge/TUI-Textual-ff69b4?logo=terminal&logoColor=white)
![License: MIT](https://img.shields.io/badge/license-MIT-green)
![Platform: Linux](https://img.shields.io/badge/platform-Linux-lightgrey?logo=linux&logoColor=black)

**What it is** — A passive tracker for the [Qoder CN CLI](https://qoder.com.cn), **modeled on `cbut`** (the sibling [codebuddy-usage-tracker](https://github.com/SHADE-glitch/codebuddy-usage-tracker)): an eight-tab TUI and headless reports over every tool, skill, subagent, plugin, MCP server and model you use, plus the tokens and credits each one cost. It reads Qoder's files only and writes only its own local index.

**What it solves** — the Qoder CN CLI writes one transcript per session and per subagent run; `qcut` folds them into one local SQLite file so usage, tokens and credits are visible in one place — and tells a measured zero apart from a route that never reported a token.

**How to run** — `./install.sh`, then `qcut sync`.

**Where to look** — See [AGENTS.md](AGENTS.md) and [MAINTENANCE.md](MAINTENANCE.md).

> 🔭 See how you **actually** use the [Qoder CN CLI](https://qoder.com.cn) —
> every 🧰 tool, 🎯 skill, 🤖 subagent, 🧩 plugin, 🔌 MCP server and 🔢 model,
> plus the 💳 credits and 🔑 tokens each one cost.

---

## 📖 Table of Contents

- [✨ Features](#-features)
- [🤔 Why this exists](#-why-this-exists)
- [🧬 Relationship to cbut](#-relationship-to-cbut)
- [🏗️ How it works](#️-how-it-works)
- [📊 The eight tabs](#-the-eight-tabs)
- [⌨️ Keyboard](#️-keyboard)
- [🔑 Token capture](#-token-capture)
- [💳 Credits vs tokens](#-credits-vs-tokens)
- [📋 Requirements](#-requirements)
- [🚀 Install](#-install)
- [🧭 Usage](#-usage)
- [⚙️ Configuration](#️-configuration)
- [🔒 Privacy](#-privacy)
- [🧪 Testing](#-testing)
- [📁 Repository layout](#-repository-layout)
- [⚠️ Limitations & roadmap](#️-limitations--roadmap)
- [🧹 Uninstall](#-uninstall)
- [🤝 Contributing](#-contributing)
- [🙏 Attribution](#-attribution)
- [📄 License](#-license)

## ✨ Features

- 🧰 **Eight views, one command** — a dashboard, tools, skills, agents, plugins, MCP, tokens, usage.
- 📜 **Full history, instantly** — backfills every session already on disk: **489 files / 522 MB in 6.8 s**, 25 MB of index, and only new bytes after that (**0.10 s** warm re-run).
- 🔑 **Keyed by name — history never disappears** — a tab defaults to the entities **used in its window**, the same boundary Tokens has always used; <kbd>a</kbd> widens it to installed ∪ ever-used. Either way the key is the name alone, so a renamed, updated or removed entity keeps its history.
- 🪶 **Passive toward Qoder** — it reads Qoder's transcripts and writes only its own local index: no hook, no plugin, no Qoder source changes.
- 🗃️ **One local SQLite file** — no network, no upload, no telemetry.
- 💳 **Real credits, real tokens — as reported** — every value is stored exactly as Qoder wrote it. A hosted row can carry tokens *and* credits once the [token flag](#-token-capture) is on; a legacy hosted row carries credits and a literal `0` in every token field; a BYOK row carries tokens and no `credits` field. Nothing is estimated, converted or backfilled: `—` marks a route-level zero, `-` an absent value.
- 🤖 **Agent runs are a cost ledger** — Qoder writes one transcript per subagent run (`agent-aExplore-<hash>.jsonl`); qcut folds them back into the parent session and links each response to the run that launched it through Qoder's own `parent_tool_use_id`, never by guessing from time or type. The Agents tab shows runs, ok/failed, linked requests, **usage total**, credits and peak context per type; <kbd>Enter</kbd> opens that type's runs, and <kbd>Enter</kbd> a run opens the responses it launched. An unlinked run reads `0` requests and `-` tokens instead of an invented cost.
- 📁 **Project and context ledgers** — the Dashboard's project board goes project → session → request → call, carrying requests, usage total, credits, peak context and last use; the project screen adds sessions and compaction detail. `qcut projects` prints the same board, and `qcut projects <path>` drills into one project. A compaction's dropped tokens are shown **beside** the token totals and never added to them.
- 🔌 **MCP in Qoder's own shape** — the model calls `mcp_call` / `mcp_get` / `mcp_list` and names the target in an argument, so the server and tool are split out of `mcp__<server>__<tool>`. Older transcripts that put `mcp__<server>__<tool>` directly in the tool name are recognised too, and a server-less admin op is shown as `(admin)`.
- 📊 **Calendar-day windows, per tab** — the top-bar range (Today / 2 / 3 / 7 / 14 / 30 days / All time) is kept **per tab**, so changing one never moves another. Entity tabs default to **7 days**, Dashboard / Tokens / Usage to **Today**, and Plugins is always all-time. Windows are whole local calendar days (00:00 → now), never "now minus N hours". A **Custom…** entry and the 📅 control open cbut's date-range picker — six roomy shortcut chips, editable start/end date and time, a month calendar and an hour grid — and the picked range belongs to the active tab. The picker is 79×30, so it fits an 80×30 terminal exactly; shorter terminals scroll it.
- 🔎 **Drill-down all the way** — <kbd>Enter</kbd> a row on Tools, Skills, Agents, MCP or Tokens for that entity's summary *in the tab's own window* above its recent calls; the Agents tab continues into its runs and their responses, and the Dashboard's project board continues into sessions and requests. A Tokens row keeps its **provider**, and a Usage row opens **that exact request** instead of a model-wide list. <kbd>Enter</kbd> a call to see **every stored field** of that single call, including its token state.
- 🔐 **Metadata only** — names, timings, statuses, tokens, credits, projects. Never message bodies, prompts or tool-argument values.

## 🤔 Why this exists

Qoder CN's CLI already **writes** the raw data you need — it just never **shows** it. It keeps:

- 📜 **session transcripts** in `~/.qoder-cn/projects/<project>/<session>.jsonl`
  (every model response carries `message.usage` with tokens and **credits**;
  every tool call is a `tool_use` block with a name and a matching `tool_result`);

…yet the CLI only exposes a per-session context meter. There is **no**
cross-session, per-tool / per-skill / per-plugin breakdown, no way to see
which route your credits are going to, and no way to tell a zero some route never
measured from a real one. That gap is what `qcut` fills. The target is the
**CLI's own transcript tree**; the Qoder IDE/desktop app is out of scope and is
not indexed.

## 🧬 Relationship to cbut

`qcut` is a **deliberate imitation of `cbut`**
([`codebuddy-usage-tracker`](https://github.com/SHADE-glitch/codebuddy-usage-tracker)),
the sibling tracker for CodeBuddy Code. `tcut` is the third sibling. The family
shape came from `cbut`: a passive reader over files the host already writes, the
same eight tab slots in the same order, per-tab calendar-day windows, a name-keyed
union of installed and ever-used entities, and two-level drill-downs.

It is an imitation, **not a port and not a parity target**. The target object is the
**Qoder CN CLI**, so Qoder's transcripts decide what a tab can show. Where a `cbut`
category has a Qoder field behind it, qcut has it too. Where it does not, qcut leaves
the category out and says why rather than inventing a placeholder — per-request
duration, HTTP status and money have no Qoder field. Conversely, Qoder facts that
`cbut` does not have are kept and made first-class: credits beside tokens, the
`—`/`-` distinction for a route that reports no token, one **Usage Total** as the
token total, and cache read / miss / write. "What does Qoder actually record?" is
the question; matching cbut's screen is not.

## 🏗️ How it works

```text
   ~/.qoder-cn/                          qcut                          ~/.local/share/
 ┌──────────────────┐          ┌─────────────────────┐          ┌────────────────────────┐
 │ projects/        │          │  qcut-sync.py       │          │ qoder-cn-usage-        │
 │   **/*.jsonl  ───┼── read ──▶  parse + classify   │          │   tracker/usage.db     │
 │                  │  (only)  │  incremental,       │  write   │                        │
 │ skills/          │          │  idempotent         ├─────────▶│  SQLite  (tools ·      │
 │ plugins/         │          └─────────────────────┘          │  skills · agents ·     │
 │ mcp.json         │                    ▲                       │  plugins · mcp ·       │
 │ settings.json    │                    │                       │  models)               │
 └──────────────────┘          ┌─────────┴───────────┐           └───────────┬────────────┘
      untouched                │  qcut-tui.py (TUI)  │◀────── query ─────────┘
                               │  qcut-stats.py (CLI)│
                               └─────────────────────┘
```

`qcut` is a **passive reader**: it parses the files Qoder already writes and
builds an index. It never touches Qoder's files, installs no hook, and adds no
runtime overhead to the host.

## 📊 The eight tabs

The **Dashboard** opens first: an at-a-glance summary of the important numbers.
A shared top bar above the tabs carries the range selector and the 📅 control,
and **each tab keeps its own range**. The **Plugins** tab has no window at all —
an install fact is always all-time — so both controls are hidden there. A window
changes the **counts** and which entity rows are shown; it never deletes history.
Choosing **Custom…** or pressing 📅 opens the date-range picker, whose Confirm
button applies the picked start/end to the active tab only.

| | Tab | What it shows |
|---|-----|---------------|
| 🧭 | **Dashboard** | a Usage panel (requests, credits, sessions, projects, builtin calls, skills, agent runs, MCP, plugins), a Tokens panel (usage total · input · output · credits · cache hit rate), a Cache panel, Top models, Top tools, and the **project board** (requests · usage total · credits · peak ctx · last used) — all for the Dashboard's own window. The runtime row and the per-day calls sparkline were removed: the failure split lives on the Tools tab and peak context / compactions are one Enter away on the project screen. The board grows with its rows and takes the height the rows above leave, so the dashboard ends at the status line; the pane scrolls once a long project list no longer fits |
| 🧰 | **Tools** | builtin tool calls by name: calls · ok · fail · pending · average duration · last used |
| 🎯 | **Skills** | skill invocations by name: calls · ok · fail · owning plugin · last used |
| 🤖 | **Agents** | subagent types: **runs · ok · fail · linked requests · usage total · credits · peak context · last used**; a run with no linked response reads `0` requests and `-` tokens |
| 🧩 | **Plugins** | every plugin — installed **and** ever used — with source, version, attributed uses and installed resources; all-time |
| 🔌 | **MCP** | MCP targets: server · tool · calls · ok · fail · average duration · last used; an admin op shows `(admin)` |
| 🔢 | **Tokens** | per-model **provider** (`Built-in` for Qoder's own route, otherwise the BYOK channel name), **usage total · credits · requests**, input / output, cache hit / miss / write, cache hit rate, last used |
| 📊 | **Usage** | the request log, newest first: time · model · **provider** · **usage total · credits** · input · output · cache hit / miss / write · cache hit rate. <kbd>Enter</kbd> opens that exact call |

`Provider` is one column, not a family plus a name: Qoder's own route reads
`Built-in`, and a BYOK/third-party call shows the channel that served it.

**Entity tabs default to the rows used in their window — the same boundary the
Tokens tab has always had.** Press <kbd>a</kbd> to switch to the installed ∪
ever-used set (the filter note says how many rows are hidden); Plugins is always
the full install ledger. Tokens and Usage are ranked by **usage total, then
credits**; Top tools by call count.

A table never shows a half column: each table keeps the longest **prefix** of its
columns that fits and names the dropped ones on the note line above it, so a
narrow terminal costs columns, not legibility.

## ⌨️ Keyboard

The tab bar and footer are the whole key surface, and the same keys work in the
sibling `ocut` and `cbut` trackers.

| Key | Action |
|---|---|
| `q` | quit |
| `r` | sync now — one incremental sync, then reload the screen |
| `s` | cycle the active tab's sort order; the note above the table names it |
| `a` | on Tools / Skills / Agents / MCP, show the installed ∪ ever-used rows |
| `/` | filter the active tab; the bar stays visible while a filter is applied, `enter` returns to the table, `escape` clears it |
| `d` | pick the active tab's time window |
| `tab` / `shift+tab` | next / previous tab |
| `enter` / double-click | open the selected row's detail screen |
| `escape` / `q` | clear the active filter, or go back from a pushed detail screen |

The TUI runs **one** incremental sync when it opens and then stops: no timer
redraws the page, and no background sync writes to the index. New rows arrive
through `r` or by reopening the TUI. A filter edit starts the list at its first
match, and the note line above each table names the active sort and the filter
count. Usage filters and ranks in SQL over the whole window — not only the
visible page — so `log_limit` caps the output without hiding matches.

Pushed detail screens own their keys: the app-level shortcuts go quiet there and
the screen's own `escape`/`q` Back remains.

## 🔑 Token capture

Qoder CN's CLI reads one environment variable when **it** starts:

```bash
export QODERCN_EXPOSE_TOKEN_USAGE=1
```

- **Set it before the CLI starts.** The value is read by the Qoder CN CLI process,
  not by qcut: put the line in your shell profile and open a new terminal. Setting it
  in the shell that runs `qcut` changes nothing.
- **Unset** — a hosted response is written with real credits and a literal `0` in all
  four token fields. That zero is not a measurement.
- **Set** — a hosted response is written with real input / output / cache-read /
  cache-creation tokens; credits may be present in the same row. BYOK routes report
  tokens either way and never a `credits` field.
- **Past calls cannot be repaired.** The transcripts already hold zeros and are never
  rewritten. Those rows keep their credits, and their token cells read `—` — never a
  measured `0`. There is no coverage percentage or capture-boundary ledger: the index
  simply shows what each row reported. When a window's credit-bearing rows carry no
  token at all, the Usage and Tokens notes say so where the dashes are read:
  `token capture off — set QODERCN_EXPOSE_TOKEN_USAGE=1 before starting Qoder`.

qcut never edits `~/.qoder-cn`, your shell profile, or the CLI's environment.
`qcut env` is a read-only advisor: it names the variable, reports whether it is set,
and prints the exact line to add. It works with an absent database and does not
create one. No token number is ever derived from credits, a context ratio, or
anything else.

## 💳 Credits vs tokens

This is the one Qoder-specific fact worth understanding before reading the
numbers, and it was measured on this machine rather than assumed:

| Route | Tokens | Credits |
|---|---|---|
| Hosted, capture off (legacy) | a literal `0` in every token field | real credits (+ `original_credits`, `billable`) |
| Hosted, capture on | real input / output / cache read / cache creation | may also be present in the same row |
| A BYOK route (`qoder-custom-<id>/<model>`) | real input / output / cache read | the `credits` field is **absent**, stored NULL |

Tokens and credits are **not complementary**. They coexist on a row whenever Qoder
reports both; the three states above describe what each route writes, not two halves of
one measure. qcut stores exactly what was reported and draws the difference:

- a numeral is a measured value;
- `—` means the row (or the aggregate of such rows) reported no token at all — a
  route-level zero, never evidence that nothing was used;
- `-` means the value is absent, or there is nothing in this window.

A mixed table can therefore hold both kinds of row at once: the measured cells stay
numerals and the route-level zeros become `—` beside them. **One token total: Usage
Total** is a display re-add of the cache hit (already part of Input), and it is the only
total any panel prints — the earlier `API Total` column is gone, because two unlabelled
totals on one screen invited the reader to pick the wrong one.

**No estimation, no conversion:** tokens are never derived from credits or from a
context ratio, and credits are never derived from tokens. History can be reviewed but
not repaired.

Two further notes on the arithmetic:

- **On Qoder's shape `input_tokens` is the *whole* prompt** and
  `cache_read_tokens` is a **subset** of it — adding cache read on top would push a
  single request past the model's 400k window, which is how the reading was checked.
  Usage Total is `input + output + cache hit` by the family's definition; the
  provider's own total is not a rival number shown beside it (the earlier
  `API total = input + output` column was removed with it).
- **Cache hit rate = cache read ÷ (read + miss + write)**, the cacheable input that
  the three parts partition. `-` when there is no cache data, never a fabricated `0%`.

## 📋 Requirements

| | |
|---|---|
| 🐧 OS | Linux — developed and verified on Ubuntu; other distributions **unverified** |
| 🐾 Qoder CN | the **CLI**, verified against version `1.1.75` (2026-10-21) and the transcripts on this machine (which still carry `1.1.57` sessions); the schema is read from the files, not from a version number |
| 🐍 Python | 3.11 — 3.14, both ends measured green (CI runs them). Headless commands need only the stdlib; the TUI needs `textual` |
| 🖥️ `textual` | only for the interactive TUI (installed by `install.sh`) |

## 🚀 Install

```bash
cd qoder-cn-usage-tracker
./install.sh          # creates .venv with textual, links ~/.local/bin/qcut
qcut sync             # build the index from your existing Qoder CN transcripts
qcut                  # launch the TUI
```

> **Upgrading an existing index?** Schema v3 repairs v2's command, skill-name and direct-MCP rows in place the first time the index is opened. The v2 agent-run links (`parent_call_id`, `agent_id`) still backfill only from a full read, so run **`qcut sync --full` once** after upgrading from an older index; it snapshots the old one first. Fresh installs need no extra step.

Prefer no venv? The headless reports work with plain `python3`:

```bash
python3 scripts/qcut-sync.py
python3 scripts/qcut-stats.py stats
```

### 🔄 Optional: keep the index fresh without opening the TUI

Two user units ship in [`systemd/`](systemd). Installing them runs
`qcut sync --quiet` once a day:

```bash
mkdir -p ~/.config/systemd/user
cp systemd/qcut-sync.* ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now qcut-sync.timer
```

`Persistent=true` catches up a run missed while the machine was off, the wake-up
is jittered by up to 15 minutes, and the service runs at `Nice=10`.

## 🧭 Usage

```bash
qcut                     # 🖥️  interactive TUI (eight tabs)
qcut sync                # 🔄  incremental index of new transcript data
qcut sync --full         # ♻️  rebuild the database from scratch (also backfills a v2 upgrade)
qcut stats               # 📊  overview of all categories
qcut tools|skills|plugins|mcp [--all]   # used rows; --all = installed ∪ ever used
qcut agents [--all]      # 🤖  per-type run cost ledger
qcut projects [PATH]     # 📁  project board; PATH drills into its sessions
qcut models              # 🔢  per-model usage total, tokens and credits
qcut commands            # ⌨️   slash-command usage, ranked by calls
qcut env                 # 🔑  the token-capture flag, read-only advice
qcut show tool Bash      # 🔍  calls for one entity; rc 1 if the index never saw it
qcut recent              # 🕒  latest tool calls
qcut inventory           # 📦  what is installed, used vs unused
qcut export              # 💾  dump every table as JSON
qcut health              # 🩺  database + data-source check
qcut backup              # 💾  snapshot before a risky step
qcut restore             # ⏪  list snapshots; `qcut restore NAME` rolls one back
qcut vacuum              # 🧹  compact the index; snapshots first, verifies after
qcut format              # 🧾  the Qoder fields and paths this tool depends on
```

| Variable | Default | Purpose |
|---|---|---|
| `QCUT_DB` | `~/.local/share/qoder-cn-usage-tracker/usage.db` | database location |
| `QCUT_QODER_DIR` | `~/.qoder-cn` | source transcript directory |
| `QODERCN_EXPOSE_TOKEN_USAGE` | unset | read by the **Qoder CN CLI**, not qcut — set to `1` so hosted calls record real tokens |

## ⚙️ Configuration

Every knob has the value the app already shipped with, and the file is optional:
`qcut` behaves exactly as documented until you add one.

`~/.config/qcut/config.toml` — or point `QCUT_CONFIG` somewhere else:

```toml
top_n = 5            # rows in the Dashboard's Top models / Top tools
log_limit = 100      # request rows on the Usage page
detail_limit = 200   # rows on a history screen
name_cap = 28        # widest cell a table renders before it cuts the value
```

- **Precedence is environment > file > default.** Each key also reads from
  `QCUT_TOP_N`, `QCUT_LOG_LIMIT`, `QCUT_DETAIL_LIMIT` and `QCUT_NAME_CAP`.
  There is no refresh or sync interval: the TUI is event-driven.
- **A broken file stops the TUI** with the path and the reason rather than
  quietly starting on defaults. `qcut health` prints the same verdict.
- **Unknown keys are refused**, and values are type- and range-checked.
- Parsed by the standard library's `tomllib`, so this adds **no dependency**.
  Nothing ever writes this file, and no setting is stored in the database.

## 🔒 Privacy

`qcut` reads only the **structured metadata** fields of Qoder's own transcripts
and stores counts, timings, statuses, tokens, credits and identifiers. It does
**not** read or store prompt/response text, tool argument *values*, or file
contents. The one thing ever read *out of* free text is a slash command's **name**,
pulled from the `<command-name>` marker of a user record and stored as an
identifier — the prose around the marker is discarded, and a marker the model
quotes in its own answer is not treated as an invocation. 🔒 Nothing leaves the
machine.

Three of those promises are enforced by checks rather than by good intentions —
see [`AGENTS.md`](AGENTS.md#invariants-and-how-to-check-them).

## 🧪 Testing

```bash
python3 -m unittest discover -s scripts/tests     # Ran 423 tests ... OK
```

The suites are plain `unittest` (`pytest` discovers them too). Two need `textual`
installed — `test_tui.py`, and `test_config.py`, whose wiring cases load the TUI
module to prove every setting reaches a renderer. Each suite builds its own
throwaway database **and** its own throwaway Qoder tree in a temp directory — your
real `usage.db` and your real `~/.qoder-cn` are never opened, and no code path in
this repository reaches the network.

| Suite | Tests | Covers |
|---|---:|---|
| `test_sync.py` | 74 | transcript parsing, classification (tool / skill / agent / MCP, including a direct `mcp__server__tool` tool name), route origin, inventory, incremental and full re-sync, rewritten and unterminated tails, timestamped-status pairing, unparsed accounting, `model_calls` counted once per inserted row, a Skill result's own `commandName` refused as a slash command, the generated format document's token-capture section, idempotency across **every** table in the schema, a sidechain response linked to its launching run through Qoder's own `parent_tool_use_id`, `compact_boundary` metadata indexed without its prose, an unreadable transcript reported and retried, a transcript rewritten with the same records counted once, a runtime-config that opens a session leaving its model in place, two skills in one record counted as two uses, a newer schema refused at every entry point, crash safety |
| `test_cross_repo_golden.py` | 16 | the cross-repo golden fixture: one fixed input set (NULL / partial / cache-missing token rows, model spellings, local-midnight and cross-hour boundaries, both 2026 US DST days) run through this repo's own Usage Total expression, window aggregates, model rollup, typed date entry and calendar-day windows, pinned to the canonical file shared with cbut and ocut |
| `test_usage.py` | 74 | the calendar-day windows, the provider label, cache hit rate, every query helper, the "installed ∪ used" union, the token flag that still separates measured rows from legacy zeros, provider- and tool-filtered history, slash-command ranking, entity existence kept apart from a quiet window, run cost aggregated only through explicit links, a run's Usage Total decided per response before it is summed, the project/session/request ladder, the compaction summary and its context tax, schemas (including the additive v1→v2 migration and the v3 command/skill/MCP repair), the minimal snapshot→list→restore round trip, a fresh install with no database yet, a query with no caller outside the tests, and the headless reports' wording |
| `test_stats.py` | 59 | the headless CLI end to end: real `main([...])` calls for every subcommand, `--db`, `--limit` and `--all`, the read-only connection, the stale-index refusal, `export` / `inventory` / `recent` / `show` / `health`, the `agents` and `projects` ledgers (including a project that has never existed), the `commands` report, `env` with the flag set and unset, `vacuum` on a temp database (snapshot, `quick_check`, unchanged row totals), a malformed settings file reported as a message rather than a traceback, the `backup`/`restore` pair, `format --write` splicing between the markers and **refusing** to append into a document that lost them, a name `show` has never seen refused instead of drawn as zeros, the span the index actually covers, a `--limit` that says how many rows it left out, and the launcher refusing to touch a path it was not given |
| `test_snapshots.py` | 14 | the destructive step's way back: the snapshot is taken *before* the change, it is an online backup rather than a byte copy of a file whose committed rows are still in the `-wal`, a restore keeps the state it overwrote, retention prunes only what it made, and an index whose offsets were lost is still worth a snapshot |
| `test_config.py` | 19 | the settings layer: defaults, file, environment precedence and bounds, a broken file that names its path, the keys `qcut health` prints, and that **every declared knob has a renderer that reads it** — a setting the user can edit for no effect is the failure this pins |
| `test_privacy.py` | 15 | an **allowlist** of each production script's whole import surface — plus the two things a list of imports cannot see: a call reached through a module that is already declared (`os.system`) and a module named in a string — a fingerprint proving the source tree is untouched, a sentinel scan across every text column, and a positive control for each |
| `test_tui.py` | 80 | tab wiring through Textual's own `run_test` harness, the resize axis (`on_resize` re-plans every table; a narrow plan is a prefix of its registry and names what it hid; rows and row *keys* survive the rebuild), click cadence (two `pilot.click` lines with no pause between them are a race the runner wins or loses), per-tab ranges, the Plugins tab's missing window, the column plan, empty states, the used-only default and the `a` switch to installed ∪ ever-used, drill-down including provider-scoped Tokens and server+tool MCP routing, the agent-type → run-history → responses ladder, the Dashboard project → session → requests ladder (and the NULL project as its own group), the per-row `—`/`-`/number distinction on both measure axes, the compaction notes, the plain tab titles and the default canvas colour, which screen owns the rows of which table, the cbut-shaped date picker (six shortcut chips, roomy 79×30 geometry, scroll on a short terminal), the shared `s` / `/` / `a` / `d` keys (per-tab sort cycles, a live filter that stays visible and restarts at its first match, a screen that cannot scroll with nothing to show), one launch sync and no background timer, pushed detail screens owning their keys, a session whose id is NULL opening as a session rather than as a run, an MCP admin row answering Enter, a run's row carrying the per-response total, `log_limit` reaching the query, and the status line and the dashboard legend as they are **actually rendered** (one row each, clipped rather than wrapped) |
| `test_dispatcher.py` | 9 | `bin/qcut` routing, flag pass-through, and the failure modes that are the reason it is bash |
| `test_format_registry.py` | 10 | an AST mirror of the format registry against the parser beside it: every field read is registered, every registered field is still read, the dispatch chain and the registry claim the same record types, and no separator, path segment or tool name is typed at a call site |
| `test_maintenance_docs.py` | 11 | the generated format document equals what `qcut format` prints today, every maintenance asset is reachable from the router, every link resolves, and the CI matrix pins the floor `pyproject.toml` actually declares |
| `test_hermetic.py` | 14 | the tripwire that keeps the suite off the real database and Qoder host: protected paths are refused, ordinary temp paths are allowed, the hook is live on child spawns, children must pin `QCUT_DB` and `QCUT_QODER_DIR`, and the settings file the loader opens by default is protected too |
| `test_readme_counts.py` | 6 | every count in either README — each row and each total — equals the number read off the suites' syntax trees, and a suite on disk with no row fails as hard as a row pointing at a deleted file |
| `test_readme_bilingual.py` | 7 | the two READMEs are one document in two languages: the same heading-marker sequence in the same order, so a section added, dropped *or moved* on one side alone goes red, and every in-page link resolves to a real heading |
| `test_record_coverage.py` | 6 | a commit that touches a production script must leave a `D-###` entry in `CHANGELOG.md`; the coverage window is enumerated from `git log` rather than typed, the ids are gapless, and every cited hash must resolve |
| `test_conventions.py` | 9 | the dimension-A root layer is present at the root, AGENTS.md names its dimension in one line instead of quoting the standard's body, and no runtime state the ignore list covers is tracked |

GitHub Actions runs that same discovery command on every push and pull request
against **Python 3.11 and 3.14** — the floor `pyproject.toml` declares and the
highest version measured green here. Both ends are covered because the local venv
is 3.13, a middle version that speaks for neither.

## 📁 Repository layout

| Path | What it is |
|---|---|
| `bin/qcut` | bash entry point. Bash on purpose: it must still print a useful error when the TUI venv is missing or broken, so it cannot depend on Python |
| `install.sh` | creates the venv (`uv` when available) and symlinks `~/.local/bin/qcut` |
| `scripts/qcut_db.py` | SQLite schema and the query helpers every entry point shares |
| `scripts/qcut-sync.py` | transcript parser and incremental indexer (`--full`, `--quiet`) |
| `scripts/qcut-stats.py` | headless reports |
| `scripts/qcut-tui.py` | the eight-tab Textual UI |
| `scripts/tests/` | the 423 tests above |
| `docs/maintenance/` | the generated format surface and the size & performance baseline |
| `systemd/` | optional daily sync service + timer |
| `requirements.txt` | `textual>=8.2,<9` — TUI only; everything else is stdlib |

Both paths are overridable for a sandboxed install: `QCUT_SCRIPTS` and
`QCUT_VENV` for the launcher, `QCUT_DB` and `QCUT_QODER_DIR` for the data.

## ⚠️ Limitations & roadmap

- 🔌 **MCP usage is sparse** until you actually invoke an MCP tool; on this
  machine it is 19 calls across 3 servers. A sparse MCP tab is honest, not broken.
- 🧩 **Plugin attribution is evidence-based.** A skill named `plugin:skill` (or
  one a plugin installs) is credited to that plugin; a plugin's own agents and
  commands are listed from its install directory but are **not** attributed from
  the transcripts, because Qoder does not record that attribution there.
- 🔑 **Legacy hosted calls stay dashed.** Calls written without
  `QODERCN_EXPOSE_TOKEN_USAGE=1` stored zeros that cannot be repaired; their token
  cells read `—` and are never estimated, while their credits remain real. Only
  calls made after the flag is set carry hosted tokens — see
  [Token capture](#-token-capture).
- ⏱️ **Three columns `cbut` has cannot exist here, and no placeholder is invented
  for them.** A model response carries one timestamp and no start/end pair,
  so **per-request duration** cannot be computed — `model_calls` has no duration
  column at all (tool, skill, agent and MCP rows do, paired from their results).
  Qoder writes no **HTTP status** per request either. And credits are never shown as
  **money**: the transcripts carry a credit amount and no price, so a currency
  figure would be arithmetic on a constant this tool made up. These are absences in
  what Qoder records, not gaps in the reader; a column of dashes would read as data
  that merely happened to be missing.
- 📅 **The date-range picker copies cbut's, six shortcuts included.** It is 79×30,
  so it fits an 80×30 terminal exactly; a narrower terminal scrolls the modal
  horizontally and a shorter one scrolls it vertically. The chips are cbut's six
  (`Today`, `1d`, `7d`, `14d`, `30d`, `All time`); the Select's extra 2- and 3-day
  ranges stay in the Select and are still expressible through the calendar. A picked
  range belongs to the active tab, and Plugins stays all-time.
- 🔄 Sync is manual (or via the optional systemd timer); there is no live hook.

## 🧹 Uninstall

`qcut` touches exactly three places outside the repository, and removing them
leaves nothing behind:

```bash
systemctl --user disable --now qcut-sync.timer 2>/dev/null          # only if installed
rm -f ~/.config/systemd/user/qcut-sync.service ~/.config/systemd/user/qcut-sync.timer
systemctl --user daemon-reload
rm -f ~/.local/bin/qcut                                            # the symlink install.sh made
rm -rf ~/.local/share/qoder-cn-usage-tracker                       # the SQLite index
```

Deleting the repository directory (with its `.venv`) finishes it. Qoder's own
files were only ever read, so there is nothing to restore there. 🗑️

## 🤝 Contributing

This is a personal tool, not a project with a roadmap and a review queue. Bug reports and patches
are still welcome: open an issue with the output of `qcut health` and the version you run. Read
[AGENTS.md](AGENTS.md) before sending code — the invariants there (read-only access, one parser,
credits kept apart from tokens, the eight tabs and their keys) are what a patch has to keep.

## 🙏 Attribution

`qcut` **imitates** [`cbut` / `codebuddy-usage-tracker`](https://github.com/SHADE-glitch/codebuddy-usage-tracker),
which tracks the same categories for CodeBuddy Code; see
[Relationship to cbut](#-relationship-to-cbut). The architecture is the same — read
what the host already writes — but the schema is Qoder's own: credits instead of a
provider-reported total, `Skill`/`Agent` tool calls instead of marker parsing, and
MCP named in an argument instead of in the tool name. It is an imitation, not a
port: a category is only kept when a Qoder CN CLI field can fill it.

## 📄 License

MIT — see [LICENSE](LICENSE). © 2026 SHADE-glitch
