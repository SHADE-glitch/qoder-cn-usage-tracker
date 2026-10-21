"""qcut_db — storage layer for the Qoder CN CLI usage tracker.

Defines the SQLite schema (tables + views) and the small helpers shared by the
indexer (``qcut-sync.py``), the headless CLI (``qcut-stats.py``) and the TUI
(``qcut-tui.py``).

The tracker is a *passive reader*: it never writes into Qoder's own
directories. It only reads ``~/.qoder-cn`` and writes this one database.

Standard library only.
"""

from __future__ import annotations

import importlib.util
import os
import re
import sqlite3
import sys
import tomllib
from datetime import datetime, timedelta
from pathlib import Path
from typing import NamedTuple

SCHEMA_VERSION = 3

# The Qoder CN CLI writes real token fields only when this variable is set in
# the CLI process's environment. qcut never sets it: it only reads what the CLI
# already wrote, and `qcut env` tells the user how to enable it for future calls.
TOKEN_ENV_NAME = "QODERCN_EXPOSE_TOKEN_USAGE"

# --- locations -------------------------------------------------------------

QODER_DIR = Path(os.environ.get("QCUT_QODER_DIR", str(Path.home() / ".qoder-cn")))

DB_PATH = Path(
    os.environ.get(
        "QCUT_DB",
        str(Path.home() / ".local" / "share" / "qoder-cn-usage-tracker" / "usage.db"),
    )
)


# --- settings --------------------------------------------------------------
#
# One file, outside the repository, read with the standard library's own TOML
# parser (``tomllib``, Python 3.11+ — the floor this project promises), so the
# settings layer costs no dependency. Precedence is environment > file > DEFAULTS.
#
# A file that cannot be read is an error, not a fallback: someone editing
# ``~/.config/qcut/config.toml`` and being silently ignored is the worst outcome
# this layer can produce, so every failure names the path or variable and the key.

CONFIG_PATH = Path(
    os.environ.get("QCUT_CONFIG")
    or (
        Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
        / "qcut"
        / "config.toml"
    )
)

# Defaults are the values the interface ships with. ``test_config.py`` pins each
# against the constant it replaces, so a settings layer cannot quietly retune
# shipped behaviour.
DEFAULTS = {
    "top_n": 5,           # rows in the Dashboard's Top models / Top tools
    "log_limit": 100,     # Usage page request rows
    "detail_limit": 200,  # rows on a history / model-response screen
    "name_cap": 28,       # widest cell a table renders before it cuts the value
    "refresh_secs": 5,    # TUI redraw timer
    "sync_secs": 30,      # TUI auto-sync timer
}

ENV_NAMES = {
    "top_n": "QCUT_TOP_N",
    "log_limit": "QCUT_LOG_LIMIT",
    "detail_limit": "QCUT_DETAIL_LIMIT",
    "name_cap": "QCUT_NAME_CAP",
    "refresh_secs": "QCUT_REFRESH_SECS",
    "sync_secs": "QCUT_SYNC_SECS",
}

# (low, high, accepted types). Ranges are wide enough to be useful and tight
# enough that a typo fails loudly: 0 rows of anything is a blank panel, not a
# preference.
_BOUNDS = {
    "top_n": (1, 100, int),
    "log_limit": (1, 1000, int),
    "detail_limit": (1, 1000, int),
    "name_cap": (8, 200, int),
    "refresh_secs": (0.5, 3600, (int, float)),
    "sync_secs": (1, 86400, (int, float)),
}


class ConfigError(ValueError):
    """The settings file, or an environment value, is not usable."""


def _setting_value(key, value, source):
    low, high, kind = _BOUNDS[key]
    # bool is an int subclass: ``top_n = true`` must not quietly become top_n = 1.
    if isinstance(value, bool) or not isinstance(value, kind):
        raise ConfigError(
            f"{source}: {key} = {value!r} must be "
            f"{'a whole number' if kind is int else 'a number'}"
        )
    if not low <= value <= high:
        raise ConfigError(f"{source}: {key} = {value!r} is outside {low}..{high}")
    return value


def load_config(path=None):
    """Resolve settings from environment, then file, then ``DEFAULTS``.

    Never raises for a *missing* file — that is the normal case — but raises
    ``ConfigError`` for a malformed one, an unknown key, or an unusable value in
    either source.
    """
    cfg = dict(DEFAULTS)
    source = Path(path if path is not None else CONFIG_PATH)
    if source.is_file():
        try:
            parsed = tomllib.loads(source.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"{source}: not valid TOML ({exc})") from None
        for key, value in parsed.items():
            if key not in DEFAULTS:
                raise ConfigError(
                    f"{source}: unknown setting {key!r} — known keys are "
                    f"{', '.join(sorted(DEFAULTS))}"
                )
            cfg[key] = _setting_value(key, value, str(source))
    for key, env in ENV_NAMES.items():
        raw = os.environ.get(env)
        if raw is None:
            continue
        try:
            value = int(raw)
        except ValueError:
            try:
                value = float(raw)
            except ValueError:
                raise ConfigError(f"{env}={raw!r} is not a number") from None
        cfg[key] = _setting_value(key, value, f"{env} environment variable")
    return cfg


def load_sync():
    """Import ``qcut-sync.py`` — a hyphenated name cannot be imported normally.

    The TUI's sync worker, ``qcut format`` and the tests all need the parser as
    a module, so the loader lives here instead of being copied a third time.
    """
    name = "qcut_sync_embedded"
    mod = sys.modules.get(name)
    if mod is None:
        path = Path(__file__).with_name("qcut-sync.py")
        spec = importlib.util.spec_from_file_location(name, str(path))
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
    return mod


# --- schema ----------------------------------------------------------------

TABLES_SQL = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- A session is identified by its id and located by its cwd. There is
-- deliberately no `title` column: Qoder's custom-title is prose written by the
-- user or model, so storing it would break the metadata-only promise.
CREATE TABLE IF NOT EXISTS sessions (
    session_id  TEXT PRIMARY KEY,
    project     TEXT,             -- decoded cwd the session ran in
    cwd         TEXT,
    git_branch  TEXT,
    version     TEXT,             -- Qoder app/agent version, read not assumed
    entrypoint  TEXT,
    started_at  INTEGER,          -- epoch ms
    ended_at    INTEGER,
    model       TEXT              -- last model seen in the session
);

-- One row per model response, keyed by the message id (globally unique among
-- usage-bearing assistant records: verified against the transcripts on this
-- machine, 28098 distinct ids / 28098 rows).
--
-- Token columns come *only* from the response's own `message.usage`. They are
-- never derived from credits, context_usage_ratio or a model multiplier. A
-- field absent from the usage object is stored as NULL (never a fabricated 0);
-- a field the provider reported as 0 stays 0.
--
-- Qoder's own routes (qfmodel / qmodel* / auto) write credits and *zero for
-- every token field* unless the CLI process has QODERCN_EXPOSE_TOKEN_USAGE=1;
-- with that flag they write real tokens and may still carry credits. A BYOK
-- route (openai-compatible, e.g. ark) reports tokens and carries no credits
-- field at all -- NULL, not a 0, per the rule above. Tokens and credits can
-- therefore coexist, and a token sum only describes the rows that reported one.
-- `row_reports_tokens` (and its SQL mirror) turns a route-level zero into `—`
-- at render time; no coverage percentage is shown or stored.
CREATE TABLE IF NOT EXISTS model_calls (
    message_id           TEXT PRIMARY KEY,
    session_id           TEXT,
    project              TEXT,
    ts                   INTEGER,
    model                TEXT,     -- display name (custom route: the part after '/')
    provider             TEXT,     -- 'builtin' or a resolved third-party label
    provider_id          TEXT,     -- the BYOK provider id, when the route is custom
    input_tokens         INTEGER,
    output_tokens        INTEGER,
    cache_read_tokens    INTEGER,
    cache_creation_tokens INTEGER,
    credits              REAL,
    original_credits     REAL,
    billable             INTEGER,
    request_id           TEXT,
    context_usage_ratio  REAL,
    stop_reason          TEXT,
    is_sidechain         INTEGER DEFAULT 0,
    has_usage            INTEGER DEFAULT 0,
    -- A sidechain response names the Agent tool call that launched it. The id is
    -- Qoder's own `parent_tool_use_id`; `agent_id` is the transcript-level run id
    -- (`agentId`) and is kept only as corroboration. Neither is ever derived from
    -- session or time proximity: an unmatched response stays NULL.
    parent_call_id       TEXT,
    agent_id             TEXT
);
CREATE INDEX IF NOT EXISTS idx_model_calls_model    ON model_calls(model);
CREATE INDEX IF NOT EXISTS idx_model_calls_ts       ON model_calls(ts);
CREATE INDEX IF NOT EXISTS idx_model_calls_model_ts ON model_calls(model, ts);
CREATE INDEX IF NOT EXISTS idx_model_calls_provider ON model_calls(provider);

-- One row per tool invocation, keyed by the tool_use block id (also unique).
-- `category` separates the five tracked kinds so the entity tabs can share one
-- table: builtin | skill | agent | mcp_tool | mcp_admin.
CREATE TABLE IF NOT EXISTS tool_calls (
    call_id     TEXT PRIMARY KEY,
    session_id  TEXT,
    project     TEXT,
    tool_name   TEXT,
    category    TEXT,
    ts          INTEGER,
    duration_ms INTEGER,
    status      TEXT,             -- completed | error | backgrounded | cancelled | pending
    is_sidechain INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_tool_name ON tool_calls(tool_name);
CREATE INDEX IF NOT EXISTS idx_tool_ts   ON tool_calls(ts);
CREATE INDEX IF NOT EXISTS idx_tool_cat  ON tool_calls(category);

-- Qoder invokes a skill through its own `Skill` tool; the argument names the
-- skill. A name of the form `<plugin>:<skill>` is owned by that plugin, which is
-- the only plugin attribution the transcripts carry.
CREATE TABLE IF NOT EXISTS skill_usage (
    call_id     TEXT PRIMARY KEY,
    skill       TEXT,
    plugin      TEXT,
    session_id  TEXT,
    project     TEXT,
    ts          INTEGER,
    status      TEXT,
    duration_ms INTEGER
);
CREATE INDEX IF NOT EXISTS idx_skill_name ON skill_usage(skill);

-- Subagents are launched through the `Agent` tool; `agent_type` is that call's
-- own `subagent_type` (Explore / Plan / general-purpose). The free-text
-- `description`/`prompt` arguments are never stored.
CREATE TABLE IF NOT EXISTS agent_usage (
    call_id     TEXT PRIMARY KEY,
    agent_type  TEXT,
    session_id  TEXT,
    project     TEXT,
    ts          INTEGER,
    status      TEXT,
    duration_ms INTEGER
);
CREATE INDEX IF NOT EXISTS idx_agent_type ON agent_usage(agent_type);

-- Qoder's MCP shape is *not* `mcp__server__tool` as a tool name: the model calls
-- the `mcp_call` / `mcp_get` / `mcp_list` tools and names the target inside the
-- arguments (`toolName: "mcp__<server>__<tool>"`). The server and tool are
-- split out of that argument here.
CREATE TABLE IF NOT EXISTS mcp_usage (
    call_id     TEXT PRIMARY KEY,
    server      TEXT,
    tool        TEXT,
    admin_op    TEXT,             -- call | get | list (NULL for an unparsed target)
    session_id  TEXT,
    project     TEXT,
    ts          INTEGER,
    status      TEXT,
    duration_ms INTEGER
);
CREATE INDEX IF NOT EXISTS idx_mcp_server ON mcp_usage(server);
CREATE INDEX IF NOT EXISTS idx_mcp_tool   ON mcp_usage(tool);

CREATE TABLE IF NOT EXISTS plugin_usage (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    plugin      TEXT,
    marketplace TEXT,
    kind        TEXT,             -- skill | command | agent | tool
    target      TEXT,
    session_id  TEXT,
    project     TEXT,
    ts          INTEGER
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_plugin_usage
    ON plugin_usage(plugin, kind, target, session_id, ts);

CREATE TABLE IF NOT EXISTS commands (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    command     TEXT,
    session_id  TEXT,
    project     TEXT,
    ts          INTEGER
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_commands ON commands(command, session_id, ts);

-- One row per context compaction Qoder writes (`system` /
-- `compact_boundary`). Only `compactMetadata` is stored: the summarized
-- messages themselves are prose and are never read, let alone copied.
-- `pre_tokens - post_tokens` is the context the compaction discarded; it is a
-- context count, not a billing measure, so it is never added to token sums.
CREATE TABLE IF NOT EXISTS compactions (
    session_id            TEXT NOT NULL,
    ts                    INTEGER NOT NULL,
    project               TEXT,
    trigger               TEXT,     -- auto | manual
    pre_tokens            INTEGER,
    post_tokens           INTEGER,
    messages_summarized   INTEGER,
    duration_ms           INTEGER,
    PRIMARY KEY (session_id, ts)
);
CREATE INDEX IF NOT EXISTS idx_compactions_ts ON compactions(ts);

CREATE TABLE IF NOT EXISTS inventory (
    kind         TEXT,            -- tool | skill | agent | plugin | mcp
    name         TEXT,
    owner_plugin TEXT,
    version      TEXT,
    path         TEXT,
    source       TEXT,            -- builtin | user | plugin
    PRIMARY KEY (kind, name, owner_plugin)
);

CREATE TABLE IF NOT EXISTS sync_state (
    file_path TEXT PRIMARY KEY,
    size      INTEGER,
    mtime     REAL,
    offset    INTEGER
);

-- Records the indexer saw but could not use: an unknown ``type`` discriminator
-- (the usual sign Qoder's log format changed) or a complete line that is not
-- JSON. Accumulated, because a run that silently claims nothing still prints
-- "done" — the count is what turns a format change into something visible.
CREATE TABLE IF NOT EXISTS unparsed (
    reason TEXT PRIMARY KEY,       -- "type:<name>" | "type:<missing>" | "unparseable_line"
    count  INTEGER NOT NULL DEFAULT 0
);
"""

# --- name sets for the "used ∪ installed" union ---------------------------
#
# Every entity tab lists the union of what is *installed/available* (the static
# ``inventory``) and what has *ever been used* (the usage tables), keyed by NAME
# alone. Name is the primary key: a renamed, updated or removed entity keeps its
# history, a name that appears under several owners merges into one row, and an
# installed-but-unused entity still shows with a zero count. Under a time window
# only the counts shrink — the list itself stays full.
#
# These CTEs are shared by the v_* views (all-time, for ``qcut export``) and by
# the q_* functions (which add the optional window in the LEFT JOIN's ON clause
# so an out-of-window entity still yields a zero-count row).
# Tools means *builtin* tools. Skill / Agent / MCP calls have their own tabs,
# so including them here (as v2 did) double-listed one invocation and made the
# Dashboard's "tool calls" overlap every specialised count beneath it. The
# inventory guard is defensive: a v2 database may still hold those two rows
# until its next sync rewrites the inventory snapshot.
_TOOL_NAMES_CTE = (
    "WITH names AS ("
    " SELECT name FROM inventory WHERE kind='tool'"
    "   AND name NOT IN ('Skill', 'Agent')"
    " UNION SELECT DISTINCT tool_name FROM tool_calls"
    "   WHERE tool_name IS NOT NULL AND category='builtin')"
)
_SKILL_NAMES_CTE = (
    "WITH names AS ("
    " SELECT name FROM inventory WHERE kind='skill'"
    " UNION SELECT DISTINCT skill FROM skill_usage WHERE skill IS NOT NULL)"
)
_AGENT_NAMES_CTE = (
    "WITH names AS ("
    " SELECT name FROM inventory WHERE kind='agent'"
    " UNION SELECT DISTINCT agent_type FROM agent_usage WHERE agent_type IS NOT NULL)"
)
_PLUGIN_NAMES_CTE = (
    "WITH names AS ("
    " SELECT name FROM inventory WHERE kind='plugin'"
    " UNION SELECT DISTINCT plugin FROM plugin_usage WHERE plugin IS NOT NULL)"
)
# MCP is keyed by (server, tool). A configured server with no usage yet is
# listed once as (server, NULL) so the tab shows configured-but-unused servers
# without forking a server that already has recorded calls.
_MCP_NAMES_CTE = (
    "WITH pairs AS ("
    " SELECT server, tool FROM mcp_usage"
    " UNION SELECT name, NULL FROM inventory i WHERE kind='mcp'"
    "   AND NOT EXISTS (SELECT 1 FROM mcp_usage m WHERE m.server = i.name))"
)

VIEWS_SQL = f"""
DROP VIEW IF EXISTS v_tools;
CREATE VIEW v_tools AS
{_TOOL_NAMES_CTE}
SELECT n.name AS tool_name,
       COUNT(c.call_id)                                        AS calls,
       SUM(CASE WHEN c.status = 'completed' THEN 1 ELSE 0 END)  AS completed,
       SUM(CASE WHEN c.status = 'error' THEN 1 ELSE 0 END)      AS failed,
       SUM(CASE WHEN c.call_id IS NOT NULL AND c.status IS NULL THEN 1 ELSE 0 END) AS pending,
       CAST(AVG(c.duration_ms) AS INTEGER)                      AS avg_ms,
       MAX(c.ts)                                                AS last_used,
       MIN(c.ts)                                                AS first_used,
       COUNT(DISTINCT c.project)                                AS projects,
       COUNT(DISTINCT c.session_id)                             AS sessions
FROM names n
LEFT JOIN tool_calls c ON c.tool_name = n.name AND c.category = 'builtin'
GROUP BY n.name;

DROP VIEW IF EXISTS v_skills;
CREATE VIEW v_skills AS
{_SKILL_NAMES_CTE}
-- A skill's owner is evidence-based: the plugin the *usage* names, else the
-- plugin that *installs* a skill of this name. Never guessed from the name.
SELECT n.name AS skill,
       COUNT(s.call_id)                                        AS calls,
       SUM(CASE WHEN s.status = 'completed' THEN 1 ELSE 0 END)  AS completed,
       SUM(CASE WHEN s.status = 'error' THEN 1 ELSE 0 END)      AS failed,
       MAX(s.ts)                                                AS last_used,
       MIN(s.ts)                                                AS first_used,
       COUNT(DISTINCT s.project)                                AS projects,
       COALESCE(MAX(s.plugin), (SELECT i.owner_plugin FROM inventory i
                                 WHERE i.kind='skill' AND i.name = n.name
                                   AND i.owner_plugin IS NOT NULL
                                 ORDER BY i.owner_plugin LIMIT 1)) AS plugin
FROM names n
LEFT JOIN skill_usage s ON s.skill = n.name
GROUP BY n.name;

DROP VIEW IF EXISTS v_agents;
CREATE VIEW v_agents AS
{_AGENT_NAMES_CTE}
SELECT n.name AS agent_type,
       COUNT(a.call_id)                                        AS calls,
       SUM(CASE WHEN a.status = 'completed' THEN 1 ELSE 0 END)  AS completed,
       SUM(CASE WHEN a.status = 'error' THEN 1 ELSE 0 END)      AS failed,
       MAX(a.ts)                                                AS last_used,
       MIN(a.ts)                                                AS first_used,
       COUNT(DISTINCT a.project)                                AS projects
FROM names n
LEFT JOIN agent_usage a ON a.agent_type = n.name
GROUP BY n.name;

DROP VIEW IF EXISTS v_mcp;
CREATE VIEW v_mcp AS
{_MCP_NAMES_CTE}
SELECT p.server AS server,
       p.tool   AS tool,
       COUNT(m.call_id)                                        AS calls,
       SUM(CASE WHEN m.status = 'completed' THEN 1 ELSE 0 END)  AS completed,
       SUM(CASE WHEN m.status = 'error' THEN 1 ELSE 0 END)      AS failed,
       SUM(CASE WHEN m.call_id IS NOT NULL AND m.status IS NULL THEN 1 ELSE 0 END) AS pending,
       CAST(AVG(m.duration_ms) AS INTEGER)                      AS avg_ms,
       MAX(m.ts)                                                AS last_used
-- ``IS`` rather than ``=``: SQLite's ``=`` is never true when a side is NULL,
-- which silently dropped every MCP row whose target could not be split (a
-- server-less `mcp_list`, for one). ``IS`` compares NULLs as equal.
FROM pairs p
LEFT JOIN mcp_usage m ON m.server IS p.server AND m.tool IS p.tool
GROUP BY p.server, p.tool;

DROP VIEW IF EXISTS v_plugins;
CREATE VIEW v_plugins AS
{_PLUGIN_NAMES_CTE}
SELECT n.name AS plugin,
       COUNT(u.id)                          AS uses,
       COUNT(DISTINCT u.kind)               AS kinds,
       COUNT(DISTINCT u.session_id)         AS sessions,
       MAX(u.ts)                            AS last_used,
       MIN(u.ts)                            AS first_used
FROM names n
LEFT JOIN plugin_usage u ON u.plugin = n.name
GROUP BY n.name;
"""


# --- connection helpers ----------------------------------------------------


def ensure_dir(path: Path) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)


def open_db(path: Path | str = DB_PATH, readonly: bool = False) -> sqlite3.Connection:
    """Open the tracker database, optionally read-only.

    A read-only open of a file that does not exist yields an *empty in-memory*
    database rather than an error: SQLite cannot open a missing file ``mode=ro``,
    and a freshly cloned install has to be able to answer ``qcut health`` (which
    ``install.sh`` runs) before the first ``qcut sync``. Every read helper below
    tolerates the empty schema, so the worst a caller sees is "no rows yet".
    """
    path = Path(path)
    if readonly and not path.exists():
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        return conn
    if readonly:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
    else:
        ensure_dir(path)
        conn = sqlite3.connect(str(path), timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    return conn


def db_file(conn) -> Path | None:
    """The database file a connection is attached to, or ``None`` in memory."""
    rows = conn.execute("PRAGMA database_list").fetchall()
    for row in rows:
        if row[1] == "main":
            return Path(row[2]) if row[2] else None
    return None


def backup_dir(db_path=None) -> Path:
    return Path(db_path or DB_PATH).parent / "backups"


BACKUP_KEEP = 5

#: The shape :func:`snapshot` makes. Prune and list both speak it, so a snapshot
#: nobody named is never deleted by retention and never missed by a restore.
BACKUP_PATTERN = "usage-*.db"


def snapshot(conn, reason: str = "manual", db_path=None) -> Path:
    """Copy the database to ``backups/`` before a destructive step.

    Two details are the whole point of this function and neither is obvious:

    * the name is only unique per *second*, so a second snapshot taken in the
      same second used to overwrite the first — and the overwrite that mattered
      was :func:`restore`'s ``pre-restore`` copy, which deleted the very safety
      copy it was making. Hence the suffix loop;
    * retention is enforced here, not by the caller. ``sync --full`` and
      ``restore`` both take snapshots, and a caller that forgot to prune is a
      directory that grows forever while the promise says it keeps five.
    """
    target_dir = backup_dir(db_path)
    target_dir.mkdir(parents=True, exist_ok=True)
    base = f"usage-{datetime.now():%Y%m%d-%H%M%S}"
    dest = target_dir / f"{base}-{reason}.db"
    n = 1
    while dest.exists():
        dest = target_dir / f"{base}-{reason}-{n}.db"
        n += 1
    dest_conn = sqlite3.connect(str(dest))
    try:
        conn.backup(dest_conn)
    finally:
        dest_conn.close()
    prune_backups(db_path)
    return dest


def list_backups(db_path=None) -> list[Path]:
    d = backup_dir(db_path)
    if not d.is_dir():
        return []
    return sorted(d.glob(BACKUP_PATTERN))


def prune_backups(db_path=None, keep: int = BACKUP_KEEP) -> list[Path]:
    """Delete managed snapshots beyond the newest ``keep``, and say which.

    Ordered by mtime, not by name: the collision suffixes make ``-10`` sort
    before ``-2``, so a name-ordered prune would delete the *newer* file —
    retention backwards, and silent.
    """
    files = list_backups(db_path)
    if keep > 0:
        files = sorted(files, key=lambda p: (p.stat().st_mtime, p.name),
                       reverse=True)[keep:]
    removed = []
    for path in files:
        path.unlink(missing_ok=True)
        removed.append(path)
    # The selection above is mtime-ordered; the report goes back in name order,
    # so a caller printing it twice prints the same thing in the same order.
    return sorted(removed, key=lambda p: p.name)


def restore(backup_path, db_path=None, reason: str = "pre-restore") -> Path:
    """Roll the database back to a snapshot, snapshotting the current file first.

    The current file is snapshotted (best effort) so a restore is itself
    undoable — restoring the wrong name used to be the one unrecoverable step.
    """
    backup_path = Path(backup_path)
    if not backup_path.is_file():
        raise FileNotFoundError(f"no such snapshot: {backup_path}")
    target = Path(db_path or DB_PATH)
    if target.exists():
        conn = sqlite3.connect(str(target))
        try:
            snapshot(conn, reason, db_path)
        except sqlite3.Error:
            pass
        finally:
            conn.close()
    ensure_dir(target)
    dest = sqlite3.connect(str(target))
    src = sqlite3.connect(f"file:{backup_path}?mode=ro", uri=True)
    try:
        src.backup(dest)
    finally:
        src.close()
        dest.close()
    # The WAL side files belong to the replaced database.
    for suffix in ("-wal", "-shm"):
        Path(str(target) + suffix).unlink(missing_ok=True)
    return target


# --- schema lifecycle ------------------------------------------------------


def stored_version(conn: sqlite3.Connection) -> int:
    try:
        row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    except sqlite3.OperationalError:
        return 0
    return int(row[0]) if row else 0


def _has_indexed_state(conn: sqlite3.Connection) -> bool:
    """Whether a snapshot would preserve anything at all.

    Checked against the tables `reset` clears, not against `sync_state` alone: a
    database that holds usage rows whose offsets were lost is precisely the state
    worth a way back, and one table's count would call it empty.
    """
    for table in ("sync_state", "model_calls", "tool_calls"):
        if conn.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone():
            return True
    return False


def _add_missing_columns(conn: sqlite3.Connection, table: str, columns) -> bool:
    """Add columns a v1 table lacks, in place. Returns True when one was added.

    ``CREATE TABLE IF NOT EXISTS`` cannot widen a table that already exists, and
    a v1 index is the normal upgrade path — not an error. The new columns are
    all nullable, so the ALTER is cheap and every existing row stays readable;
    their values arrive on the next full sync (incremental sync only appends,
    and INSERT OR IGNORE never revisits an indexed response).
    """
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    added = False
    for name, decl in columns:
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
            added = True
    return added


def _migration_mcp_parts(tool_name: str):
    """Split a direct ``mcp__<server>__<tool>`` name during the v3 repair.

    Kept here rather than imported from ``qcut-sync.py``: a migration must run
    before the parser is loaded, and the parser owns the same two separators as
    active format constants. This is the one-place repair of data written by the
    parser *before* it recognised the direct shape.
    """
    prefix, sep = "mcp__", "__"
    if not isinstance(tool_name, str) or not tool_name.startswith(prefix):
        return None, None
    body = tool_name[len(prefix):]
    if sep in body:
        server, tool = body.split(sep, 1)
        return server or None, tool or None
    return body or None, None


def _repair_v3(conn: sqlite3.Connection) -> None:
    """Repair data written by v2's three blind spots. Additive, idempotent.

    * a Skill result's ``commandName`` is the skill's own name, not a slash
      command — v2 recorded those as commands;
    * a skill invoked as ``<plugin>:<skill>`` was stored under the qualified
      name, while the inventory knows the bare one — the Skills tab and a
      plugin's resource count listed both;
    * a direct ``mcp__<server>__<tool>`` tool name was classified as builtin —
      it disappeared from MCP and appeared on Tools.
    """
    # Match the false command before normalising the skill name it mirrors.
    # A plugin-qualified skill is still qualified here, while its Skill result's
    # `commandName` is the bare name — match both halves or that pair survives.
    conn.execute("""
        DELETE FROM commands
        WHERE EXISTS (
            SELECT 1 FROM skill_usage s
            WHERE s.session_id = commands.session_id
              AND (s.skill = commands.command
                   OR (instr(s.skill, ':') > 0
                       AND substr(s.skill, instr(s.skill, ':') + 1)
                           = commands.command)))
    """)
    conn.execute("""
        UPDATE skill_usage
        SET plugin = COALESCE(plugin, substr(skill, 1, instr(skill, ':') - 1)),
            skill  = substr(skill, instr(skill, ':') + 1)
        WHERE instr(skill, ':') > 0
          AND substr(skill, 1, instr(skill, ':') - 1) <> ''
          AND substr(skill, instr(skill, ':') + 1) <> ''
    """)
    conn.execute("""
        UPDATE plugin_usage
        SET target = substr(target, instr(target, ':') + 1)
        WHERE kind = 'skill' AND instr(target, ':') > 0
          AND substr(target, 1, instr(target, ':') - 1) <> ''
          AND substr(target, instr(target, ':') + 1) <> ''
    """)
    # Normalising two names into one can collide; the unique index cannot have
    # caught a collision that only exists after the update.
    conn.execute("""
        DELETE FROM plugin_usage
        WHERE id NOT IN (
            SELECT MIN(id) FROM plugin_usage
            GROUP BY plugin, kind, target, session_id, ts)
    """)
    direct = conn.execute(
        "SELECT call_id, tool_name, session_id, project, ts, status, duration_ms"
        " FROM tool_calls"
        " WHERE category = 'builtin' AND tool_name LIKE 'mcp\\_\\_%' ESCAPE '\\'"
    ).fetchall()
    for row in direct:
        server, tool = _migration_mcp_parts(row["tool_name"])
        conn.execute(
            "UPDATE tool_calls SET category = 'mcp_tool' WHERE call_id = ?",
            (row["call_id"],),
        )
        conn.execute(
            "INSERT OR IGNORE INTO mcp_usage"
            "(call_id, server, tool, admin_op, session_id, project, ts,"
            " status, duration_ms) VALUES(?,?,?,?,?,?,?,?,?)",
            (row["call_id"], server, tool, "call", row["session_id"],
             row["project"], row["ts"], row["status"], row["duration_ms"]),
        )


def ensure_schema(conn: sqlite3.Connection) -> str:
    """Create or widen the schema. Idempotent, and safe on a live install."""
    current = stored_version(conn)
    conn.executescript(TABLES_SQL)
    # Kept out of TABLES_SQL: CREATE INDEX on a v1 file would run before the
    # columns exist and fail the whole upgrade.
    _add_missing_columns(conn, "model_calls", (
        ("parent_call_id", "TEXT"),
        ("agent_id", "TEXT"),
    ))
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_model_calls_parent"
        " ON model_calls(parent_call_id)"
    )
    if 0 < current < SCHEMA_VERSION:
        _repair_v3(conn)
    conn.executescript(VIEWS_SQL)
    conn.execute(
        "INSERT INTO meta(key, value) VALUES('schema_version', ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (str(SCHEMA_VERSION),),
    )
    conn.commit()
    if current == 0:
        return "created"
    return "current" if current >= SCHEMA_VERSION else "migrated"


def migrate(path: Path | str = DB_PATH) -> str:
    """Bring the database up to ``SCHEMA_VERSION``.

    v2 widens ``model_calls`` with the Agent-call link and adds the compaction
    table; both are additive. A database from a *newer* schema than this code
    must be refused rather than half-read.
    """
    conn = open_db(path)
    try:
        current = stored_version(conn)
        if current > SCHEMA_VERSION:
            raise RuntimeError(
                f"{path}: schema v{current} is newer than this tool understands "
                f"(v{SCHEMA_VERSION}) — update qcut or point QCUT_DB elsewhere"
            )
        return ensure_schema(conn)
    finally:
        conn.close()


def reset(conn: sqlite3.Connection) -> None:
    """Empty every indexed table so ``sync --full`` can rebuild from scratch."""
    for table in ("model_calls", "tool_calls", "skill_usage", "agent_usage",
                  "mcp_usage", "plugin_usage", "commands", "compactions",
                  "sessions", "sync_state", "unparsed"):
        conn.execute(f"DELETE FROM {table}")
    conn.commit()


def note_unparsed(conn: sqlite3.Connection, reason: str, n: int) -> None:
    conn.execute(
        "INSERT INTO unparsed(reason, count) VALUES(?, ?) "
        "ON CONFLICT(reason) DO UPDATE SET count = count + excluded.count",
        (reason, n),
    )


def q_unparsed(conn: sqlite3.Connection):
    """Unclaimed-record counts. Empty (not an error) on an unbuilt schema."""
    try:
        return conn.execute(
            "SELECT reason, count FROM unparsed ORDER BY count DESC"
        ).fetchall()
    except sqlite3.OperationalError:
        return []


# --- windows ---------------------------------------------------------------

# Whole local calendar days. ``None`` means "all time" (no lower bound).
USAGE_RANGES = {
    "1d": 1,
    "2d": 2,
    "3d": 3,
    "7d": 7,
    "14d": 14,
    "30d": 30,
    "all": None,
}
RANGE_ORDER = ("1d", "2d", "3d", "7d", "14d", "30d", "all")
RANGE_LABELS = {
    "1d": "Today",
    "2d": "2 days",
    "3d": "3 days",
    "7d": "7 days",
    "14d": "14 days",
    "30d": "30 days",
    "all": "All time",
}
# The TUI (and the picker's chips) name this mapping USAGE_RANGE_LABELS; the
# headless CLI says RANGE_LABELS. One mapping, two spellings, no third copy.
USAGE_RANGE_LABELS = RANGE_LABELS


class CustomWindow(NamedTuple):
    """An absolute window chosen in the date picker (as opposed to a preset).

    ``start_ms`` of ``None`` means no lower bound (all time). ``follow_now``
    keeps the end pinned to the current time on every refresh; otherwise the
    window is the fixed ``[start_ms, end_ms]`` span. Times are epoch
    milliseconds, the same unit as every other bound in this module.
    """

    start_ms: int | None
    end_ms: int | None
    follow_now: bool = False


def _short_time(ms) -> str:
    return datetime.fromtimestamp(ms / 1000).strftime("%m-%d %H:%M")


def window_label(value) -> str:
    """A short label for a window value: a preset's name, or a custom span.

    A ``follow_now`` end reads as ``now`` rather than a timestamp, so the label
    does not go stale between refreshes. Kept free of a clock argument on
    purpose: the label depends only on what was chosen, never on the time it is
    read.
    """
    if not isinstance(value, CustomWindow):
        return RANGE_LABELS.get(value, str(value))
    if value.start_ms is None:
        return "all → now" if value.follow_now else f"all → {_short_time(value.end_ms)}"
    start = _short_time(value.start_ms)
    if value.follow_now:
        return f"{start} → now"
    return f"{start} → {_short_time(value.end_ms)}"


# --- typed date / time entry (the date picker) ------------------------------
# The picker lets a date and a time be *typed* as well as clicked, so the two
# strings a person types are normalised before anything compares them: "10-01"
# and "10-1" are the same day, "9:5 AM" and "09:05 AM" the same minute. That is
# the whole point — the display format is a suggestion, not a syntax to match.
#
# Month-first is assumed for the ambiguous two-field date form (the picker shows
# MM/DD/YYYY), and a leading four-digit field is read as the year. Both the date
# and the time are displayed in the same form the rest of the app prints them:
# a 24-hour clock, so midnight reads "00:00" rather than "12:00 AM". An am/pm
# marker is still *accepted* on input ("12:00 AM" is midnight) — the display
# format is a suggestion, not a syntax to match.
DATE_DISPLAY_FMT = "%m/%d/%Y"
TIME_DISPLAY_FMT = "%H:%M"
# The date is split by hand rather than through a table of strptime formats:
# a yearless "%m-%d" parse is deprecated in 3.14 (the default year is ambiguous
# and leap day cannot resolve), so the missing year is filled in explicitly.
_DATE_SPLIT = re.compile(r"[-/.]")
_TIME_FORMATS = ("%I:%M %p", "%I:%M%p", "%I %p", "%I%p", "%H:%M", "%H:%M:%S")


def parse_date(text: str, default_year: int) -> datetime:
    """A typed date -> ``datetime`` at local midnight. Raises ``ValueError``.

    ``10-01``, ``10/1`` and ``10.01`` all mean the same day once ``default_year``
    fills in the missing year; a four-digit leading field is the year
    (``2026-10-01``). The month comes first in the ambiguous two-field form, and
    a two-digit trailing year uses the same 69/70 pivot as ``%y``.
    """
    s = (text or "").strip()
    fields = _DATE_SPLIT.split(s)
    if len(fields) == 2:
        year, (month, day) = str(default_year), fields
    elif len(fields) == 3:
        first, second, third = fields
        if len(first) == 4:                       # YYYY-MM-DD
            year, month, day = first, second, third
        else:                                     # MM-DD-YYYY / MM-DD-YY
            month, day, year = first, second, third
            if len(year) == 2:
                year = ("19" if int(year) >= 69 else "20") + year
    else:
        raise ValueError(f"unrecognised date: {text!r}")
    try:
        return datetime(int(year), int(month), int(day))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"unrecognised date: {text!r}") from exc


def parse_time(text: str):
    """A typed time -> ``(hour, minute)`` in 24-hour form. Raises ``ValueError``.

    An am/pm marker makes it 12-hour ("9:5 PM" == "09:05 PM"); without one it is
    24-hour ("21:05"). ``12:00 AM`` is midnight, ``12:00 PM`` noon.
    """
    s = (text or "").strip()
    if not s:
        raise ValueError("empty time")
    for fmt in _TIME_FORMATS:
        try:
            dt = datetime.strptime(s, fmt)
        except ValueError:
            continue
        return dt.hour, dt.minute
    raise ValueError(f"unrecognised time: {text!r}")


def format_date(value: datetime) -> str:
    """A datetime's date as the picker displays it (``MM/DD/YYYY``)."""
    return value.strftime(DATE_DISPLAY_FMT)


def format_time(hour: int, minute: int) -> str:
    """``(hour, minute)`` as the picker shows it (24-hour, e.g. ``00:00``)."""
    return datetime(2000, 1, 1, hour, minute).strftime(TIME_DISPLAY_FMT)


def parse_datetime(date_text: str, time_text: str, default_year: int):
    """A typed ``(date, time)`` pair -> epoch ms, or ``None`` for a blank date.

    A blank time means midnight. Raises ``ValueError`` on a bad date or time, so
    the caller can tell "all time" (``None``) apart from "I mistyped" (an error).
    """
    if not (date_text or "").strip():
        return None
    day = parse_date(date_text, default_year)
    hour, minute = parse_time(time_text) if (time_text or "").strip() else (0, 0)
    return int(day.replace(hour=hour, minute=minute).timestamp() * 1000)


def bounds_of(value, now_ms: int):
    """``(start_ms, end_ms)`` for any window value, preset or custom.

    One place that knows how a window becomes a pair of bounds, so the TUI's
    tabs and the picker's prefill cannot drift apart. ``follow_now`` resolves
    its end to ``now_ms`` here, which is why the caller must pass a fresh clock.
    """
    if isinstance(value, CustomWindow):
        return value.start_ms, (int(now_ms) if value.follow_now else value.end_ms)
    if value is None:
        return None, int(now_ms)
    return window_bounds(value, now_ms)


def window_bounds(range_key: str, now_ms: int):
    """``(start_ms, end_ms)`` for a whole-calendar-day window ending at ``now_ms``.

    The window is a whole number of **local** calendar days: ``"1d"`` runs from
    today's local 00:00 to ``now_ms``, ``"2d"`` from yesterday's 00:00, and so
    on. It never uses "now minus N hours", so the bounds are stable through the
    day and match what a person means by "today" / "the last 2 days".

    ``start_ms`` is ``None`` for ``"all"`` (no lower bound).
    """
    days = USAGE_RANGES[range_key]
    end = int(now_ms)
    if days is None:
        return None, end
    midnight = datetime.fromtimestamp(end / 1000).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    start = midnight - timedelta(days=days - 1)
    return int(start.timestamp() * 1000), end


def cache_hit_rate(hit, miss, write=None):
    """``hit / (hit + miss + write)`` as a fraction, or ``None`` when undefined.

    The denominator is the *cacheable input*, so the three parts partition the
    prompt. Qoder reports only the cached read (``cache_read_tokens``) and
    ``input_tokens`` is the whole prompt, so ``miss`` is derived at the call
    site as ``input - read``; the split is then the same three-way one the
    panels and the hit-rate column show.

    A NULL ``hit`` or an empty denominator yields ``None`` — never a fabricated
    rate (``0.0`` would claim "nothing was cached", which is a different fact).
    """
    if hit is None:
        return None
    total = (hit or 0) + (miss or 0) + (write or 0)
    if total <= 0:
        return None
    return hit / total


def provider_label(value) -> str:
    """The display label for a ``model_calls.provider`` value.

    One column carries both the family and the channel: ``None`` (unknown) reads
    as ``-``, the built-in sentinel reads as ``Built-in``, and every other value
    — a vendor name or an endpoint tag — is shown as stored. There is no second
    one-word route column; it repeated the family and pushed columns off narrow
    terminals.
    """
    if value is None:
        return "-"
    return "Built-in" if value == "builtin" else value


# --- time predicates -------------------------------------------------------


def _time_conds(start_ts, end_ts, col="ts"):
    conds = []
    if start_ts is not None:
        conds.append((f"{col} >= ?", start_ts))
    if end_ts is not None:
        conds.append((f"{col} < ?", end_ts))
    return conds


def _window_join(cond_list, col="ts"):
    """``LEFT JOIN ... ON <alias>.<col> >= ? AND <col> < ?`` fragment."""
    return " AND ".join(sql for sql, _ in cond_list), [v for _, v in cond_list]


def q_usage_safe(conn, sql, params=()):
    """Run a query, returning no rows when the schema is not built yet.

    The read-only surface (health, export, the health check `install.sh` runs)
    must answer on a database that `qcut sync` has never touched.
    """
    try:
        return conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError:
        return []


# --- entity queries --------------------------------------------------------


def _on_time(conds) -> tuple[str, list]:
    return _window_join(conds)


def q_tools(conn, limit=None, start_ts=None, end_ts=None):
    on, params = _on_time(_time_conds(start_ts, end_ts))
    sql = f"""
    {_TOOL_NAMES_CTE}
    SELECT n.name AS tool_name,
           COUNT(c.call_id) AS calls,
           SUM(CASE WHEN c.status='completed' THEN 1 ELSE 0 END) AS completed,
           SUM(CASE WHEN c.status='error' THEN 1 ELSE 0 END) AS failed,
           SUM(CASE WHEN c.call_id IS NOT NULL AND c.status IS NULL THEN 1 ELSE 0 END) AS pending,
           CAST(AVG(CASE WHEN c.status IS NOT NULL THEN c.duration_ms END) AS INTEGER) AS avg_ms,
           MAX(c.ts) AS last_used,
           COUNT(DISTINCT c.session_id) AS sessions,
           COUNT(DISTINCT c.project) AS projects
    FROM names n
    LEFT JOIN tool_calls c ON c.tool_name = n.name AND c.category = 'builtin'
         {('AND ' + on) if on else ''}
    GROUP BY n.name
    ORDER BY calls DESC, tool_name
    """
    if limit:
        sql += f" LIMIT {int(limit)}"
    return conn.execute(sql, params).fetchall()


def q_skills(conn, limit=None, start_ts=None, end_ts=None):
    on, params = _on_time(_time_conds(start_ts, end_ts))
    sql = f"""
    {_SKILL_NAMES_CTE}
    SELECT n.name AS skill,
           COUNT(s.call_id) AS calls,
           SUM(CASE WHEN s.status='completed' THEN 1 ELSE 0 END) AS completed,
           SUM(CASE WHEN s.status='error' THEN 1 ELSE 0 END) AS failed,
           MAX(s.ts) AS last_used,
           COUNT(DISTINCT s.session_id) AS sessions,
           COALESCE(MAX(s.plugin), (SELECT i.owner_plugin FROM inventory i
                                     WHERE i.kind='skill' AND i.name = n.name
                                       AND i.owner_plugin IS NOT NULL
                                     ORDER BY i.owner_plugin LIMIT 1)) AS plugin
    FROM names n
    LEFT JOIN skill_usage s ON s.skill = n.name {('AND ' + on) if on else ''}
    GROUP BY n.name
    ORDER BY calls DESC, skill
    """
    if limit:
        sql += f" LIMIT {int(limit)}"
    return conn.execute(sql, params).fetchall()


def q_agents(conn, limit=None, start_ts=None, end_ts=None):
    """Per-agent-type runs and the cost of the responses they launched.

    A run is one `Agent` tool call. Its requests are matched by the transcript's
    own `parent_tool_use_id`, never by session or time proximity: a run whose
    sidechain transcript was not indexed shows ``requests = 0`` and NULL token
    sums, which is the honest answer. The run window applies to `agent_usage.ts`;
    once a run is inside it, *all* of that run's responses are counted.
    """
    conds = _time_conds(start_ts, end_ts)
    where = ("WHERE " + " AND ".join(f"a.{sql}" for sql, _ in conds)) if conds else ""
    params = [v for _, v in conds]
    sql = f"""
    {_AGENT_NAMES_CTE},
    req AS (
        SELECT parent_call_id,
               COUNT(*) AS requests,
               SUM(input_tokens)  AS input_tokens,
               SUM(output_tokens) AS output_tokens,
               SUM(cache_read_tokens) AS cache_read_tokens,
               SUM(cache_creation_tokens) AS cache_creation_tokens,
               SUM(credits) AS credits,
               {_AGG_CREDITS_CARRIED_EXPR} AS credits_carried,
               {_AGG_TOKEN_COVERED_EXPR} AS token_covered,
               MAX(context_usage_ratio) AS peak_context
        FROM model_calls
        WHERE parent_call_id IS NOT NULL
        GROUP BY parent_call_id
    ),
    agg AS (
        SELECT a.agent_type,
               COUNT(a.call_id) AS runs,
               SUM(CASE WHEN a.status='completed' THEN 1 ELSE 0 END) AS completed,
               SUM(CASE WHEN a.status='error' THEN 1 ELSE 0 END) AS failed,
               COUNT(DISTINCT a.session_id) AS sessions,
               MAX(a.ts) AS last_used,
               SUM(COALESCE(r.requests, 0)) AS requests,
               SUM(r.input_tokens)  AS input_tokens,
               SUM(r.output_tokens) AS output_tokens,
               SUM(r.cache_read_tokens) AS cache_read_tokens,
               SUM(r.cache_creation_tokens) AS cache_creation_tokens,
               SUM(r.credits) AS credits,
               SUM(COALESCE(r.credits_carried, 0)) AS credits_carried,
               SUM(COALESCE(r.token_covered, 0)) AS token_covered,
               MAX(r.peak_context) AS peak_context
        FROM agent_usage a
        LEFT JOIN req r ON r.parent_call_id = a.call_id
        {where}
        GROUP BY a.agent_type
    )
    SELECT n.name AS agent_type,
           COALESCE(g.runs, 0) AS runs,
           COALESCE(g.completed, 0) AS completed,
           COALESCE(g.failed, 0) AS failed,
           COALESCE(g.sessions, 0) AS sessions,
           g.last_used,
           COALESCE(g.requests, 0) AS requests,
           g.input_tokens, g.output_tokens,
           g.cache_read_tokens, g.cache_creation_tokens,
           CASE WHEN g.input_tokens IS NOT NULL AND g.output_tokens IS NOT NULL
                THEN g.input_tokens + g.output_tokens
                     + COALESCE(g.cache_read_tokens, 0) END
               AS usage_total_tokens,
           g.credits, COALESCE(g.credits_carried, 0) AS credits_carried,
           COALESCE(g.token_covered, 0) AS token_covered,
           g.peak_context
    FROM names n
    LEFT JOIN agg g ON g.agent_type = n.name
    ORDER BY runs DESC, agent_type
    """
    if limit:
        sql += f" LIMIT {int(limit)}"
    return conn.execute(sql, params).fetchall()


def _request_summary(conn, where, params):
    return conn.execute(f"""
    SELECT COUNT(*) AS requests,
           SUM(credits) AS credits,
           {_AGG_CREDITS_CARRIED_EXPR} AS credits_carried,
           {_AGG_TOKEN_COVERED_EXPR} AS token_covered,
           MAX(context_usage_ratio) AS peak_context
    FROM model_calls m {where}
    """, params).fetchone()


def q_agent_run_calls(conn, call_id, limit=200):
    """The newest responses one Agent run launched, with an honest total.

    Newest first because a long run's latest work is what a reader opens the
    screen for; the header carries the true count so the limit cannot read as
    the whole run.
    """
    return conn.execute(f"""
    SELECT m.ts, m.model, m.provider, m.credits,
           m.input_tokens, m.output_tokens, m.cache_read_tokens,
           m.cache_creation_tokens, m.context_usage_ratio, m.is_sidechain,
           {_USAGE_TOTAL_EXPR} AS usage_total_tokens,
           {_TOTAL_EXPR} AS total_tokens,
           {_CACHE_MISS_EXPR} AS cache_miss_tokens,
           m.message_id AS call_id
    FROM model_calls m
    WHERE m.parent_call_id = ?
    ORDER BY m.ts DESC, m.message_id DESC
    LIMIT ?
    """, (call_id, limit)).fetchall()


def q_agent_run_summary(conn, call_id):
    """Totals for every response of one Agent run, not only the shown tail."""
    return _request_summary(conn, "WHERE parent_call_id = ?", (call_id,))


def q_mcp(conn, limit=None, start_ts=None, end_ts=None):
    on, params = _on_time(_time_conds(start_ts, end_ts))
    sql = f"""
    {_MCP_NAMES_CTE}
    SELECT p.server AS server,
           p.tool AS tool,
           COUNT(m.call_id) AS calls,
           SUM(CASE WHEN m.status='completed' THEN 1 ELSE 0 END) AS completed,
           SUM(CASE WHEN m.status='error' THEN 1 ELSE 0 END) AS failed,
           SUM(CASE WHEN m.call_id IS NOT NULL AND m.status IS NULL THEN 1 ELSE 0 END) AS pending,
           CAST(AVG(m.duration_ms) AS INTEGER) AS avg_ms,
           MAX(m.ts) AS last_used
    FROM pairs p
    LEFT JOIN mcp_usage m ON m.server IS p.server AND m.tool IS p.tool
         {('AND ' + on) if on else ''}
    GROUP BY p.server, p.tool
    ORDER BY calls DESC, p.server, p.tool
    """
    if limit:
        sql += f" LIMIT {int(limit)}"
    return conn.execute(sql, params).fetchall()


def q_plugins(conn):
    """Plugins are always all-time: a plugin row is an install fact.

    ``skills``/``agents``/``commands`` count the plugin's own resources, from
    whichever evidence exists: an attributed call (``plugin_usage``, where the
    ``<plugin>:<skill>`` name is proof) or an installed file under the plugin's
    own directory (``inventory.owner_plugin``). A resource that is both counted
    once — the two sets are unioned by name, not added, so a plugin that ships
    a skill it also calls does not read as two.
    """
    return conn.execute(f"""
    {_PLUGIN_NAMES_CTE}
    SELECT n.name AS plugin,
           COUNT(u.id) AS uses,
           COUNT(DISTINCT u.session_id) AS sessions,
           MAX(u.ts) AS last_used,
           MAX(i.version) AS version,
           MAX(i.source) AS source,
           (SELECT group_concat(DISTINCT kind) FROM plugin_usage pu
              WHERE pu.plugin = n.name) AS kinds,
           (SELECT COUNT(*) FROM (
                SELECT target AS name FROM plugin_usage pu
                 WHERE pu.plugin = n.name AND pu.kind = 'skill'
                UNION SELECT name FROM inventory iv
                 WHERE iv.owner_plugin = n.name AND iv.kind = 'skill'
            )) AS skills,
           (SELECT COUNT(*) FROM (
                SELECT target AS name FROM plugin_usage pu
                 WHERE pu.plugin = n.name AND pu.kind = 'agent'
                UNION SELECT name FROM inventory iv
                 WHERE iv.owner_plugin = n.name AND iv.kind = 'agent'
            )) AS agents,
           (SELECT COUNT(*) FROM (
                SELECT target AS name FROM plugin_usage pu
                 WHERE pu.plugin = n.name AND pu.kind = 'command'
                UNION SELECT name FROM inventory iv
                 WHERE iv.owner_plugin = n.name AND iv.kind = 'command'
            )) AS commands
    FROM names n
    LEFT JOIN plugin_usage u ON u.plugin = n.name
    LEFT JOIN (
        SELECT name, MAX(version) AS version, MAX(source) AS source
        FROM inventory WHERE kind = 'plugin' GROUP BY name
    ) i ON i.name = n.name
    GROUP BY n.name
    ORDER BY uses DESC, plugin
    """).fetchall()


def q_commands(conn, limit=None, start_ts=None, end_ts=None):
    """Slash-command usage, ranked by call count, optionally windowed."""
    where, params = _where(start_ts, end_ts, "commands")
    sql = f"""
    SELECT command,
           COUNT(*) AS calls,
           COUNT(DISTINCT session_id) AS sessions,
           MAX(ts) AS last_used
    FROM commands {where}
    GROUP BY command
    ORDER BY calls DESC, command
    """
    if limit is not None:
        sql += " LIMIT ?"
        params.append(int(limit))
    return conn.execute(sql, params).fetchall()


def q_models(conn, start_ts=None, end_ts=None):
    """Per-model totals: Usage Total first, then credits, then API tokens.

    ``token_covered`` is retained only so the renderer can draw a route-level
    zero as ``—``; it is no longer a coverage statistic. Token ``SUM``s stay
    NULL when no row contributed a field (never a fabricated 0).
    """
    conds = _time_conds(start_ts, end_ts)
    where = ("WHERE " + " AND ".join(sql for sql, _ in conds)) if conds else ""
    params = [v for _, v in conds]
    return conn.execute(f"""
    SELECT model,
           provider,
           COUNT(*) AS requests,
           SUM(input_tokens)  AS input_tokens,
           SUM(output_tokens) AS output_tokens,
           SUM(cache_read_tokens) AS cache_read_tokens,
           SUM(cache_creation_tokens) AS cache_creation_tokens,
           SUM(COALESCE(credits,0)) AS credits,
           SUM(COALESCE(original_credits,0)) AS original_credits,
           {_AGG_TOKEN_COVERED_EXPR} AS token_covered,
           {_AGG_CREDITS_CARRIED_EXPR} AS credits_carried,
           {_AGG_TOTAL_EXPR} AS total_tokens,
           {_AGG_USAGE_TOTAL_EXPR} AS usage_total_tokens,
           {_AGG_CACHE_MISS_EXPR} AS cache_miss_tokens,
           MAX(is_sidechain) AS sidechain,
           MAX(ts) AS last_used,
           COUNT(DISTINCT session_id) AS sessions
    FROM model_calls {where}
    GROUP BY model, provider
    ORDER BY usage_total_tokens DESC, credits DESC, total_tokens DESC, model
    """, params).fetchall()


def q_usage_model_stats(conn, start_ts=None, end_ts=None,
                        order_by="usage_total_tokens"):
    """Per-model rollup for one window (the Dashboard's Top models panel).

    The default order is the one the panel displays: Usage Total, then credits,
    then API total. Ranking by API tokens while printing credits put a
    1.8M-token / 2,039-credit model below a 68M-token / 266-credit one.
    """
    where, params = _where(start_ts, end_ts, "model_calls")
    order = _Q_MODEL_STATS_ORDER.get(
        order_by, _Q_MODEL_STATS_ORDER["usage_total_tokens"])
    return conn.execute(f"""
    SELECT model, provider,
           COUNT(*) AS requests,
           SUM(input_tokens)  AS input_tokens,
           SUM(output_tokens) AS output_tokens,
           {_AGG_TOTAL_EXPR} AS total_tokens,
           {_AGG_USAGE_TOTAL_EXPR} AS usage_total_tokens,
           SUM(cache_read_tokens) AS cache_hit,
           {_AGG_CACHE_MISS_EXPR} AS cache_miss,
           SUM(cache_creation_tokens) AS cache_write,
           SUM(COALESCE(credits,0)) AS credits,
           {_AGG_CREDITS_CARRIED_EXPR} AS credits_carried,
           {_AGG_TOKEN_COVERED_EXPR} AS token_covered
    FROM model_calls {where}
    GROUP BY model, provider ORDER BY {order}, model
    """, params).fetchall()


def q_recent(conn, limit=30):
    return conn.execute("""
    SELECT ts, tool_name, category, status, duration_ms, project, session_id, call_id
    FROM tool_calls
    ORDER BY ts DESC
    LIMIT ?
    """, (limit,)).fetchall()


def _history_table(kind):
    return {
        "tool": ("tool_calls", "tool_name"),
        "skill": ("skill_usage", "skill"),
        "agent": ("agent_usage", "agent_type"),
        "model": ("model_calls", "model"),
        "mcp": ("mcp_usage", "server"),
    }[kind]


def q_history(conn, kind, name, limit=50, start_ts=None, end_ts=None,
              provider=None, tool=None):
    """One entity's calls, optionally narrowed to a provider or an MCP tool.

    ``provider`` and ``tool`` are the second half of the key a Tokens or MCP
    row carries: a model can appear under more than one provider, and an MCP
    server owns many tools. Passing them keeps the drill-down on the row the
    user selected instead of widening back to the whole server/model.
    """
    table, col = _history_table(kind)
    if kind == "agent":
        conds = [("agent_type = ?", name)]
        conds += _time_conds(start_ts, end_ts)
        where = " AND ".join(f"a.{sql}" if sql != "agent_type = ?" else sql
                             for sql, _ in conds)
        return conn.execute(f"""
        SELECT a.ts, a.agent_type AS name, a.status, a.duration_ms,
               a.project, a.session_id, a.call_id,
               COALESCE(r.requests, 0) AS requests,
               r.input_tokens, r.output_tokens,
               r.cache_read_tokens, r.cache_creation_tokens,
               CASE WHEN r.input_tokens IS NOT NULL
                          AND r.output_tokens IS NOT NULL
                    THEN r.input_tokens + r.output_tokens
                         + COALESCE(r.cache_read_tokens, 0)
                    ELSE NULL END AS usage_total_tokens,
               r.credits, COALESCE(r.credits_carried, 0) AS credits_carried,
               COALESCE(r.token_covered, 0) AS token_covered,
               r.peak_context
        FROM agent_usage a
        LEFT JOIN (
            SELECT parent_call_id, COUNT(*) AS requests,
                   SUM(input_tokens) AS input_tokens,
                   SUM(output_tokens) AS output_tokens,
                   SUM(cache_read_tokens) AS cache_read_tokens,
                   SUM(cache_creation_tokens) AS cache_creation_tokens,
                   SUM(credits) AS credits,
                   {_AGG_CREDITS_CARRIED_EXPR} AS credits_carried,
                   {_AGG_TOKEN_COVERED_EXPR} AS token_covered,
                   MAX(context_usage_ratio) AS peak_context
            FROM model_calls WHERE parent_call_id IS NOT NULL
            GROUP BY parent_call_id
        ) r ON r.parent_call_id = a.call_id
        WHERE {where}
        ORDER BY a.ts DESC LIMIT ?
        """, [v for _, v in conds] + [limit]).fetchall()
    conds = [(f"{col} = ?", name)]
    if kind == "model" and provider is not None:
        conds.append(("provider IS ?", provider))
    if kind == "mcp" and tool is not None:
        conds.append(("tool = ?", tool))
    conds += _time_conds(start_ts, end_ts)
    where = " AND ".join(sql for sql, _ in conds)
    params = [v for _, v in conds]
    # Each kind returns exactly the columns its history screen renders, so the
    # mapper in the TUI cannot drift out of step with the table it fills.
    if kind == "model":
        cols = ("ts, model, provider, session_id, message_id AS call_id, "
                "input_tokens, output_tokens, cache_read_tokens, "
                "cache_creation_tokens, credits, stop_reason, "
                f"{_TOTAL_EXPR} AS total_tokens, "
                f"{_USAGE_TOTAL_EXPR} AS usage_total_tokens, "
                f"{_CACHE_MISS_EXPR} AS cache_miss_tokens")
    elif kind == "tool":
        cols = ("ts, tool_name AS name, category, status, duration_ms, project, "
                "session_id, call_id")
    elif kind == "mcp":
        cols = "ts, server AS name, tool, status, duration_ms, session_id, call_id"
    else:
        cols = f"ts, {col} AS name, status, duration_ms, project, session_id, call_id"
    return conn.execute(
        f"SELECT {cols} FROM {table} WHERE {where} ORDER BY ts DESC LIMIT ?",
        params + [limit],
    ).fetchall()


def q_entity_exists(conn, kind, name):
    """Is *name* in the index at all — **ignoring** any window the caller holds?

    `show` needs two states kept apart, and a windowed aggregate cannot tell them:
    a name that was never used, and a name used only outside this window. The first
    is an answer the tool does not have; the second is an honest zero.
    """
    table, col = _history_table(kind)
    return conn.execute(
        f"SELECT 1 FROM {table} WHERE {col} = ? LIMIT 1", (name,)).fetchone() is not None


def q_entity_summary(conn, kind, name, start_ts=None, end_ts=None,
                     provider=None, tool=None):
    """Counts and totals for one entity, inside the caller's window."""
    table, col = _history_table(kind)
    if kind == "agent":
        conds = [("agent_type = ?", name)] + _time_conds(start_ts, end_ts)
        where = " AND ".join(f"a.{sql}" if sql != "agent_type = ?" else sql
                             for sql, _ in conds)
        return conn.execute(f"""
        WITH runs AS (
            SELECT call_id, status, duration_ms, ts, session_id, project
            FROM agent_usage a WHERE {where}
        ),
        req AS (
            SELECT m.* FROM model_calls m
            JOIN runs r ON m.parent_call_id = r.call_id
        )
        SELECT (SELECT COUNT(*) FROM runs) AS calls,
               (SELECT COUNT(*) FROM runs) AS runs,
               (SELECT SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END)
                  FROM runs) AS completed,
               (SELECT SUM(CASE WHEN status='error' THEN 1 ELSE 0 END)
                  FROM runs) AS failed,
               (SELECT CAST(AVG(CASE WHEN status IS NOT NULL THEN duration_ms END)
                            AS INTEGER) FROM runs) AS avg_ms,
               (SELECT COUNT(*) FROM req) AS requests,
               (SELECT SUM(input_tokens) FROM req) AS input_tokens,
               (SELECT SUM(output_tokens) FROM req) AS output_tokens,
               (SELECT SUM(cache_read_tokens) FROM req) AS cache_read_tokens,
               (SELECT SUM(cache_creation_tokens) FROM req) AS cache_creation_tokens,
               (SELECT {_AGG_USAGE_TOTAL_EXPR} FROM req) AS usage_total_tokens,
               (SELECT SUM(credits) FROM req) AS credits,
               (SELECT {_AGG_CREDITS_CARRIED_EXPR} FROM req) AS credits_carried,
               (SELECT {_AGG_TOKEN_COVERED_EXPR} FROM req) AS token_covered,
               (SELECT MAX(context_usage_ratio) FROM req) AS peak_context,
               (SELECT COUNT(DISTINCT session_id) FROM runs) AS sessions,
               (SELECT COUNT(DISTINCT project) FROM runs) AS projects,
               (SELECT MIN(ts) FROM runs) AS first_used,
               (SELECT MAX(ts) FROM runs) AS last_used
        """, [v for _, v in conds]).fetchone()
    conds = [(f"{col} = ?", name)]
    if kind == "model" and provider is not None:
        conds.append(("provider IS ?", provider))
    if kind == "mcp" and tool is not None:
        conds.append(("tool = ?", tool))
    conds += _time_conds(start_ts, end_ts)
    where = " AND ".join(sql for sql, _ in conds)
    params = [v for _, v in conds]
    if kind == "model":
        row = conn.execute(f"""
        SELECT COUNT(*) AS calls,
               SUM(input_tokens) AS input_tokens,
               SUM(output_tokens) AS output_tokens,
               SUM(cache_read_tokens) AS cache_read_tokens,
               SUM(cache_creation_tokens) AS cache_creation_tokens,
               SUM(credits) AS credits,
               {_AGG_CREDITS_CARRIED_EXPR} AS credits_carried,
               {_AGG_TOTAL_EXPR} AS total_tokens,
               {_AGG_USAGE_TOTAL_EXPR} AS usage_total_tokens,
               {_AGG_CACHE_MISS_EXPR} AS cache_miss_tokens,
               {_AGG_TOKEN_COVERED_EXPR} AS token_covered,
               MIN(ts) AS first_used, MAX(ts) AS last_used,
               COUNT(DISTINCT session_id) AS sessions
        FROM {table} WHERE {where}
        """, params).fetchone()
    else:
        row = conn.execute(f"""
        SELECT COUNT(*) AS calls,
               SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) AS completed,
               SUM(CASE WHEN status='error' THEN 1 ELSE 0 END) AS failed,
               CAST(AVG(CASE WHEN status IS NOT NULL THEN duration_ms END) AS INTEGER) AS avg_ms,
               MIN(ts) AS first_used, MAX(ts) AS last_used,
               COUNT(DISTINCT session_id) AS sessions,
               COUNT(DISTINCT project) AS projects
        FROM {table} WHERE {where}
        """, params).fetchone()
    return row


def q_compaction_summary(conn, start_ts=None, end_ts=None) -> dict:
    """Compactions in one window: count, trigger split and the context dropped.

    ``compacted_tokens`` is ``pre_tokens - post_tokens`` summed — the *context*
    the summarizer discarded, not a billing measure. It is never added to any
    token total; it is shown beside them as the cost of staying under the
    window. Rows with a missing or inverted pair contribute 0.
    """
    where, params = _where(start_ts, end_ts, None)
    row = conn.execute(f"""
    SELECT COUNT(*) AS compactions,
           COUNT(DISTINCT session_id) AS sessions,
           SUM(CASE WHEN trigger='auto' THEN 1 ELSE 0 END) AS auto,
           SUM(CASE WHEN trigger='manual' THEN 1 ELSE 0 END) AS manual,
           SUM(CASE WHEN pre_tokens > post_tokens
                    THEN pre_tokens - post_tokens ELSE 0 END) AS compacted_tokens,
           CAST(AVG(CASE WHEN duration_ms IS NOT NULL THEN duration_ms END)
                AS INTEGER) AS avg_ms,
           MAX(pre_tokens) AS peak_pre_tokens
    FROM compactions {where}
    """, params).fetchone()
    data = dict(row)
    for key in ("compactions", "sessions", "auto", "manual", "compacted_tokens"):
        data[key] = data[key] or 0
    return data


def q_project_board(conn, start_ts=None, end_ts=None, limit=None):
    """Per-project cost and usage for the Dashboard's project ledger."""
    where, params = _where(start_ts, end_ts, "m")
    cmp_where, cmp_params = _where(start_ts, end_ts, "c")
    sql = f"""
    WITH req AS (
        SELECT m.project AS project,
               COUNT(*) AS requests,
               COUNT(DISTINCT m.session_id) AS sessions,
               SUM(m.input_tokens)  AS input_tokens,
               SUM(m.output_tokens) AS output_tokens,
               {_AGG_USAGE_TOTAL_EXPR} AS usage_total_tokens,
               SUM(m.credits) AS credits,
               {_AGG_CREDITS_CARRIED_EXPR} AS credits_carried,
               {_AGG_TOKEN_COVERED_EXPR} AS token_covered,
               MAX(m.context_usage_ratio) AS peak_context,
               MIN(m.ts) AS first_used,
               MAX(m.ts) AS last_used
        FROM model_calls m {where}
        GROUP BY m.project
    ),
    cmp AS (
        SELECT c.project AS project,
               COUNT(*) AS compactions,
               SUM(CASE WHEN c.pre_tokens > c.post_tokens
                        THEN c.pre_tokens - c.post_tokens ELSE 0 END)
                   AS compacted_tokens
        FROM compactions c {cmp_where}
        GROUP BY c.project
    )
    SELECT r.project, r.requests, r.sessions,
           r.input_tokens, r.output_tokens, r.usage_total_tokens, r.credits,
           r.credits_carried, r.token_covered, r.peak_context,
           r.first_used, r.last_used,
           COALESCE(cm.compactions, 0) AS compactions,
           COALESCE(cm.compacted_tokens, 0) AS compacted_tokens
    FROM req r LEFT JOIN cmp cm ON cm.project IS r.project
    ORDER BY r.usage_total_tokens DESC, r.credits DESC, r.requests DESC
    """
    if limit:
        sql += f" LIMIT {int(limit)}"
    return conn.execute(sql, params + cmp_params).fetchall()


def q_project_exists(conn, project) -> bool:
    """Whether the index has ever seen this project, in any window.

    The same distinction ``q_entity_exists`` draws for tools: a windowed
    aggregate answers 0 for a quiet window *and* for a name that was never
    indexed, and those are different sentences. ``None`` asks about the
    ``(no project)`` group.
    """
    row = conn.execute("""
    SELECT EXISTS(SELECT 1 FROM model_calls WHERE project IS ?)
        OR EXISTS(SELECT 1 FROM compactions WHERE project IS ?)
    """, (project, project)).fetchone()
    return bool(row[0])


def q_project_summary(conn, project, start_ts=None, end_ts=None) -> dict:
    """One project's totals inside the caller's window."""
    mc_where, mc_params = _where(start_ts, end_ts, "m")
    mc_where = (mc_where + " AND " if mc_where else "WHERE ") + "m.project IS ?"
    cp_where, cp_params = _where(start_ts, end_ts, "c")
    cp_where = (cp_where + " AND " if cp_where else "WHERE ") + "c.project IS ?"
    row = conn.execute(f"""
    WITH cmp AS (
        SELECT COUNT(*) AS compactions,
               SUM(CASE WHEN c.pre_tokens > c.post_tokens
                        THEN c.pre_tokens - c.post_tokens ELSE 0 END)
                   AS compacted_tokens
        FROM compactions c {cp_where}
    )
    SELECT COUNT(*) AS requests,
           COUNT(DISTINCT m.session_id) AS sessions,
           SUM(m.input_tokens)  AS input_tokens,
           SUM(m.output_tokens) AS output_tokens,
           {_AGG_USAGE_TOTAL_EXPR} AS usage_total_tokens,
           SUM(m.credits) AS credits,
           {_AGG_CREDITS_CARRIED_EXPR} AS credits_carried,
           {_AGG_TOKEN_COVERED_EXPR} AS token_covered,
           MAX(m.context_usage_ratio) AS peak_context,
           MIN(m.ts) AS first_used, MAX(m.ts) AS last_used,
           (SELECT compactions FROM cmp) AS compactions,
           (SELECT compacted_tokens FROM cmp) AS compacted_tokens
    FROM model_calls m {mc_where}
    """, cp_params + [project] + mc_params + [project]).fetchall()[0]
    data = dict(row)
    data["compacted_tokens"] = data["compacted_tokens"] or 0
    data["compactions"] = data["compactions"] or 0
    return data


def q_project_sessions(conn, project, start_ts=None, end_ts=None,
                       limit=None):
    """One project's sessions, newest first, with their own cost and pressure."""
    where, params = _where(start_ts, end_ts, "m")
    where = (where + " AND " if where else "WHERE ") + "m.project IS ?"
    cmp_where, cmp_params = _where(start_ts, end_ts, "c")
    cmp_where = (cmp_where + " AND " if cmp_where else "WHERE ") + "c.project IS ?"
    sql = f"""
    WITH req AS (
        SELECT m.session_id,
               COUNT(*) AS requests,
               SUM(m.input_tokens)  AS input_tokens,
               SUM(m.output_tokens) AS output_tokens,
               {_AGG_USAGE_TOTAL_EXPR} AS usage_total_tokens,
               SUM(m.credits) AS credits,
               {_AGG_CREDITS_CARRIED_EXPR} AS credits_carried,
               {_AGG_TOKEN_COVERED_EXPR} AS token_covered,
               MAX(m.context_usage_ratio) AS peak_context,
               MIN(m.ts) AS first_used, MAX(m.ts) AS last_used
        FROM model_calls m {where}
        GROUP BY m.session_id
    ),
    cmp AS (
        SELECT c.session_id,
               COUNT(*) AS compactions,
               SUM(CASE WHEN c.pre_tokens > c.post_tokens
                        THEN c.pre_tokens - c.post_tokens ELSE 0 END)
                   AS compacted_tokens
        FROM compactions c {cmp_where}
        GROUP BY c.session_id
    )
    SELECT r.session_id, r.requests, r.input_tokens, r.output_tokens,
           r.usage_total_tokens, r.credits, r.credits_carried, r.token_covered,
           r.peak_context, r.first_used, r.last_used,
           COALESCE(cm.compactions, 0) AS compactions,
           COALESCE(cm.compacted_tokens, 0) AS compacted_tokens,
           (SELECT s.model FROM sessions s WHERE s.session_id = r.session_id)
               AS model
    FROM req r LEFT JOIN cmp cm ON cm.session_id = r.session_id
    ORDER BY r.last_used DESC
    """
    if limit:
        sql += f" LIMIT {int(limit)}"
    return conn.execute(sql, params + [project] + cmp_params + [project]).fetchall()


def q_session_calls(conn, session_id, start_ts=None, end_ts=None, limit=200):
    """The newest responses in one session, with the agent that launched each."""
    where, params = _where(start_ts, end_ts, "m")
    where = (where + " AND " if where else "WHERE ") + "m.session_id = ?"
    return conn.execute(f"""
    SELECT m.ts, m.model, m.provider, m.credits,
           m.input_tokens, m.output_tokens, m.cache_read_tokens,
           m.cache_creation_tokens, m.context_usage_ratio, m.is_sidechain,
           {_USAGE_TOTAL_EXPR} AS usage_total_tokens,
           {_TOTAL_EXPR} AS total_tokens,
           {_CACHE_MISS_EXPR} AS cache_miss_tokens,
           a.agent_type,
           m.message_id AS call_id
    FROM model_calls m
    LEFT JOIN agent_usage a ON a.call_id = m.parent_call_id
    {where}
    ORDER BY m.ts DESC, m.message_id DESC
    LIMIT ?
    """, params + [session_id, limit]).fetchall()


def q_session_summary(conn, session_id, start_ts=None, end_ts=None):
    """Totals for every response in one session, not only the shown tail."""
    where, params = _where(start_ts, end_ts, "m")
    where = (where + " AND " if where else "WHERE ") + "m.session_id = ?"
    return _request_summary(conn, where, params + [session_id])


def q_call_detail(conn, kind, key):
    """Every stored field of a single call, for the second drill-down level."""
    if kind == "model":
        return conn.execute(
            "SELECT * FROM model_calls WHERE message_id = ?", (key,)
        ).fetchone()
    if kind == "tool":
        return conn.execute(
            "SELECT * FROM tool_calls WHERE call_id = ?", (key,)
        ).fetchone()
    if kind == "skill":
        return conn.execute(
            "SELECT * FROM skill_usage WHERE call_id = ?", (key,)
        ).fetchone()
    if kind == "agent":
        return conn.execute(
            "SELECT * FROM agent_usage WHERE call_id = ?", (key,)
        ).fetchone()
    if kind == "mcp":
        return conn.execute(
            "SELECT * FROM mcp_usage WHERE call_id = ?", (key,)
        ).fetchone()
    if kind == "plugin":
        return conn.execute(
            "SELECT * FROM plugin_usage WHERE id = ?", (key,)
        ).fetchone()
    return None


def q_inventory(conn, kind=None):
    if kind is None:
        return conn.execute(
            "SELECT kind, name, owner_plugin, version, path, source "
            "FROM inventory ORDER BY kind, name"
        ).fetchall()
    return conn.execute(
        "SELECT kind, name, owner_plugin, version, path, source "
        "FROM inventory WHERE kind = ? ORDER BY name",
        (kind,),
    ).fetchall()


# --- usage / dashboard queries --------------------------------------------


# --- display-only "Usage Total" -------------------------------------------
# input + output + cache read. Qoder's ``input_tokens`` already *contains* the
# cached read (verified on this machine: adding cache read on top pushes a
# single request past the model's 400k window), so this deliberately re-adds it
# and lands at roughly twice the API total. It is shown *next to* the API total,
# never instead of it. NULL when the API parts are missing; a row without cache
# data contributes 0 (COALESCE) so it is neither inflated nor silently dropped
# from the sum.
_USAGE_TOTAL_EXPR = (
    "CASE WHEN input_tokens IS NOT NULL AND output_tokens IS NOT NULL"
    " THEN input_tokens + output_tokens + COALESCE(cache_read_tokens, 0)"
    " ELSE NULL END"
)
_AGG_USAGE_TOTAL_EXPR = f"SUM({_USAGE_TOTAL_EXPR})"
# Per-row API total: input + output. Qoder reports no separate provider total,
# so this is the whole of it. NULL only when a part is missing (never a 0).
_TOTAL_EXPR = (
    "CASE WHEN input_tokens IS NOT NULL AND output_tokens IS NOT NULL"
    " THEN input_tokens + output_tokens ELSE NULL END"
)
_AGG_TOTAL_EXPR = f"SUM({_TOTAL_EXPR})"
# The part of the prompt that was *not* cached. Derived, because Qoder reports
# only the cached read; clamped at 0 so a provider that ever reports a read
# larger than the prompt cannot produce a negative count.
_CACHE_MISS_EXPR = (
    "CASE WHEN input_tokens IS NULL THEN NULL"
    " ELSE MAX(0, input_tokens - COALESCE(cache_read_tokens, 0)) END"
)
_AGG_CACHE_MISS_EXPR = f"SUM({_CACHE_MISS_EXPR})"
# Does a row report *any* token measure? A literal `0` in every field is the
# shape the CLI writes for a hosted call without the token flag, so it means
# "no token was reported", not "used nothing". One expression for every query
# that shows a token number, and the Python half below, so a rendered `—` and
# the reported-row count can never disagree about the same row.
_TOKEN_COVERED_EXPR = (
    "COALESCE(input_tokens,0) > 0 OR COALESCE(output_tokens,0) > 0"
    " OR COALESCE(cache_read_tokens,0) > 0"
    " OR COALESCE(cache_creation_tokens,0) > 0"
)
_AGG_TOKEN_COVERED_EXPR = (
    f"SUM(CASE WHEN {_TOKEN_COVERED_EXPR} THEN 1 ELSE 0 END)")

# The credits-side mirror of the same problem. A BYOK route carries no `credits`
# field at all, so `SUM(COALESCE(credits,0))` over such a route is `0` — which
# reads "billed nothing" when the truth is "never billed in this unit". Counting
# the rows that *did* carry the field lets the renderer answer `-` (no
# measurement) instead of `0` (a measurement of zero). Per-row cells need
# nothing: a NULL credits is already absent, and `fmt_credits` renders it `-`.
_CREDITS_CARRIED_EXPR = "credits IS NOT NULL"
_AGG_CREDITS_CARRIED_EXPR = (
    f"SUM(CASE WHEN {_CREDITS_CARRIED_EXPR} THEN 1 ELSE 0 END)")

_TOKEN_FIELDS = ("input_tokens", "output_tokens", "cache_read_tokens",
                 "cache_creation_tokens")

# Every column that holds a token *measure* — the four reported fields plus the
# three derived in SQL. The TUI's detail renderer and the headless report both
# read this one list, so a new token column cannot be shown by one and missed by
# the other.
TOKEN_COLUMNS = _TOKEN_FIELDS + ("total_tokens", "usage_total_tokens",
                                 "cache_miss_tokens")


def row_reports_tokens(row) -> bool:
    """The Python half of :data:`_TOKEN_COVERED_EXPR`, field for field.

    Works on a ``sqlite3.Row`` and on a plain dict alike; a field the query did
    not select is treated as not reported rather than raising.
    """
    keys = row.keys()
    return any((row[f] or 0) > 0 for f in _TOKEN_FIELDS if f in keys)


_Q_MODEL_STATS_ORDER = {
    "usage_total_tokens": "usage_total_tokens DESC, credits DESC, total_tokens DESC",
    "total_tokens": "total_tokens DESC, credits DESC",
    "requests": "requests DESC, usage_total_tokens DESC",
    "credits": "credits DESC, usage_total_tokens DESC",
    "cache_hit": "cache_hit DESC, usage_total_tokens DESC",
}


def _where(start_ts, end_ts, table="model_calls", col="ts"):
    conds = _time_conds(start_ts, end_ts, col)
    prefix = f"{table}." if table else ""
    if not conds:
        return "", []
    sql = " AND ".join(f"{prefix}{s}" for s, _ in conds)
    return "WHERE " + sql, [v for _, v in conds]


def q_usage_kpi(conn, start_ts=None, end_ts=None) -> dict:
    """The Dashboard's headline numbers, all inside one window."""
    mc_where, mc_params = _where(start_ts, end_ts, "model_calls")
    tok = conn.execute(f"""
    SELECT COUNT(*) AS requests,
           SUM(COALESCE(input_tokens,0))  AS input_tokens,
           SUM(COALESCE(output_tokens,0)) AS output_tokens,
           SUM(COALESCE(cache_read_tokens,0)) AS cache_read_tokens,
           SUM(COALESCE(cache_creation_tokens,0)) AS cache_creation_tokens,
           SUM(COALESCE(credits,0)) AS credits,
           SUM(COALESCE(original_credits,0)) AS original_credits,
           {_AGG_TOKEN_COVERED_EXPR} AS token_covered,
           {_AGG_CREDITS_CARRIED_EXPR} AS credits_carried,
           {_AGG_TOTAL_EXPR} AS total_tokens,
           {_AGG_USAGE_TOTAL_EXPR} AS usage_total_tokens,
           SUM(CASE WHEN parent_call_id IS NOT NULL THEN 1 ELSE 0 END)
               AS agent_requests,
           SUM(CASE WHEN parent_call_id IS NOT NULL
                    THEN COALESCE(credits, 0) END) AS agent_credits,
           SUM(CASE WHEN parent_call_id IS NOT NULL
                    AND credits IS NOT NULL THEN 1 ELSE 0 END)
               AS agent_credits_carried,
           MAX(context_usage_ratio) AS peak_context,
           COUNT(DISTINCT session_id) AS sessions,
           COUNT(DISTINCT project) AS projects
    FROM model_calls {mc_where}
    """, mc_params).fetchone()
    tc_where, tc_params = _where(start_ts, end_ts, "tool_calls")
    calls = conn.execute(f"""
    SELECT COUNT(*) AS tool_calls,
           SUM(CASE WHEN category='builtin' THEN 1 ELSE 0 END) AS builtin_tool_calls,
           SUM(CASE WHEN category='builtin' AND status='error'
                    THEN 1 ELSE 0 END) AS builtin_failed,
           SUM(CASE WHEN category='builtin' AND status IS NULL
                    THEN 1 ELSE 0 END) AS builtin_pending,
           SUM(CASE WHEN status='error' THEN 1 ELSE 0 END) AS failed,
           SUM(CASE WHEN status IS NULL THEN 1 ELSE 0 END) AS pending,
           CAST(AVG(CASE WHEN status IS NOT NULL THEN duration_ms END) AS INTEGER) AS avg_ms,
           COUNT(DISTINCT session_id) AS sessions,
           COUNT(DISTINCT project) AS projects
    FROM tool_calls {tc_where}
    """, tc_params).fetchone()
    sc_where, sc_params = _where(start_ts, end_ts, "skill_usage")
    skills = conn.execute(
        f"SELECT COUNT(*) AS n, COUNT(DISTINCT skill) AS distinct_n FROM skill_usage {sc_where}",
        sc_params,
    ).fetchone()
    ag_where, ag_params = _where(start_ts, end_ts, "agent_usage")
    agents = conn.execute(
        f"SELECT COUNT(*) AS n, COUNT(DISTINCT agent_type) AS distinct_n FROM agent_usage {ag_where}",
        ag_params,
    ).fetchone()
    mc2_where, mc2_params = _where(start_ts, end_ts, "mcp_usage")
    mcp = conn.execute(
        f"SELECT COUNT(*) AS n, COUNT(DISTINCT server) AS distinct_n FROM mcp_usage {mc2_where}",
        mc2_params,
    ).fetchone()
    pl_where, pl_params = _where(start_ts, end_ts, "plugin_usage")
    plugins = conn.execute(
        f"SELECT COUNT(DISTINCT plugin) AS n FROM plugin_usage {pl_where}",
        pl_params,
    ).fetchone()
    cmd_where, cmd_params = _where(start_ts, end_ts, "commands")
    commands = conn.execute(
        f"SELECT COUNT(*) AS n FROM commands {cmd_where}", cmd_params
    ).fetchone()
    comp = q_compaction_summary(conn, start_ts, end_ts)
    return {
        # The Dashboard's call counts, partitioned: builtin tools, skills,
        # agent runs, MCP calls and commands are disjoint sets of tool_use rows.
        "skills": skills["n"] or 0,
        "agents": agents["n"] or 0,
        "mcp": mcp["n"] or 0,
        "plugins": plugins["n"] or 0,
        "commands": commands["n"] or 0,
        "requests": tok["requests"] or 0,
        # Token sums stay NULL when nothing contributed; a hosted-only window's
        # literal zeros read `—` through the row-reports-token predicate.
        "input_tokens": tok["input_tokens"],
        "output_tokens": tok["output_tokens"],
        "cache_read_tokens": tok["cache_read_tokens"],
        "cache_creation_tokens": tok["cache_creation_tokens"],
        "total_tokens": tok["total_tokens"],
        "usage_total_tokens": tok["usage_total_tokens"],
        "credits": tok["credits"] or 0.0,
        "original_credits": tok["original_credits"] or 0.0,
        "token_covered": tok["token_covered"] or 0,
        "credits_carried": tok["credits_carried"] or 0,
        "peak_context": tok["peak_context"],
        "agent_requests": tok["agent_requests"] or 0,
        "agent_credits": tok["agent_credits"] or 0.0,
        "agent_credits_carried": tok["agent_credits_carried"] or 0,
        "model_sessions": tok["sessions"] or 0,
        "model_projects": tok["projects"] or 0,
        "compactions": comp["compactions"],
        "compaction_sessions": comp["sessions"],
        "compacted_tokens": comp["compacted_tokens"],
        "compaction_avg_ms": comp["avg_ms"],
        "tool_calls": calls["tool_calls"] or 0,
        "builtin_tool_calls": calls["builtin_tool_calls"] or 0,
        "builtin_failed": calls["builtin_failed"] or 0,
        "builtin_pending": calls["builtin_pending"] or 0,
        "tool_failed": calls["failed"] or 0,
        "tool_pending": calls["pending"] or 0,
        "tool_avg_ms": calls["avg_ms"] or 0,
        "tool_sessions": calls["sessions"] or 0,
        "tool_projects": calls["projects"] or 0,
        "skill_calls": skills["n"] or 0,
        "skill_names": skills["distinct_n"] or 0,
        "agent_calls": agents["n"] or 0,
        "agent_names": agents["distinct_n"] or 0,
        "mcp_calls": mcp["n"] or 0,
        "mcp_servers": mcp["distinct_n"] or 0,
    }


def q_usage_summary(conn, start_ts, end_ts) -> dict:
    """Totals for one window, for the Usage page's summary panels.

    ``total_tokens`` is the **API Total** (input + output), ``usage_total_tokens``
    the **display-only** re-add of the cache read (see
    :data:`_USAGE_TOTAL_EXPR`). The cache parts are split three ways
    (hit / miss / write) so the hit-rate denominator is the cacheable input.
    Every ``SUM`` keeps NULL when nothing contributed — a window with no rows
    reads as ``-``, never a fabricated 0.
    """
    where, params = _where(start_ts, end_ts, "model_calls")
    row = conn.execute(f"""
    SELECT COUNT(*) AS requests,
           COUNT(DISTINCT session_id) AS sessions,
           COUNT(DISTINCT project) AS projects,
           SUM(input_tokens)  AS input_tokens,
           SUM(output_tokens) AS output_tokens,
           {_AGG_TOTAL_EXPR} AS total_tokens,
           {_AGG_USAGE_TOTAL_EXPR} AS usage_total_tokens,
           SUM(cache_read_tokens) AS cache_hit,
           {_AGG_CACHE_MISS_EXPR} AS cache_miss,
           SUM(cache_creation_tokens) AS cache_write,
           SUM(credits) AS credits,
           SUM(original_credits) AS original_credits,
           {_AGG_CREDITS_CARRIED_EXPR} AS credits_carried,
           SUM(CASE WHEN parent_call_id IS NOT NULL THEN 1 ELSE 0 END)
               AS agent_requests,
           SUM(CASE WHEN parent_call_id IS NOT NULL
                    THEN COALESCE(credits, 0) END) AS agent_credits,
           SUM(CASE WHEN parent_call_id IS NOT NULL
                    AND credits IS NOT NULL THEN 1 ELSE 0 END)
               AS agent_credits_carried,
           MAX(context_usage_ratio) AS peak_context,
           {_AGG_TOKEN_COVERED_EXPR} AS token_covered
    FROM model_calls {where}
    """, params).fetchone()
    data = dict(row)
    data["token_covered"] = data["token_covered"] or 0
    return data


def q_usage_request_logs(conn, start_ts, end_ts, limit=100, offset=0):
    """Per-response rows in the window, newest first (never re-parses logs).

    Carries both ``total_tokens`` (API total) and ``usage_total_tokens`` (the
    display-only re-add of the cache read), plus the three cache parts and the
    derived miss.
    """
    where, params = _where(start_ts, end_ts, "model_calls")
    return conn.execute(f"""
    SELECT ts, model, provider, session_id, project, message_id,
           input_tokens, output_tokens, cache_read_tokens,
           cache_creation_tokens, credits, original_credits,
           context_usage_ratio, is_sidechain, stop_reason,
           {_TOTAL_EXPR} AS total_tokens,
           {_USAGE_TOTAL_EXPR} AS usage_total_tokens,
           {_CACHE_MISS_EXPR} AS cache_miss_tokens
    FROM model_calls {where}
    ORDER BY ts DESC, message_id
    LIMIT ? OFFSET ?
    """, params + [limit, offset]).fetchall()


def q_usage_daily(conn, start_ts=None, end_ts=None):
    """Per-local-day call and credit totals, oldest first (sparkline input)."""
    where, params = _where(start_ts, end_ts, "model_calls")
    rows = conn.execute(f"""
    SELECT ts, COALESCE(credits,0) AS credits FROM model_calls {where}
    """, params).fetchall()
    buckets = {}
    for row in rows:
        day = datetime.fromtimestamp(row["ts"] / 1000).strftime("%Y-%m-%d")
        b = buckets.setdefault(day, {"day": day, "calls": 0, "credits": 0.0})
        b["calls"] += 1
        b["credits"] += row["credits"]
    return [buckets[k] for k in sorted(buckets)]


def q_usage_activity(conn, start_ts=None, end_ts=None) -> dict:
    """How the builtin tool calls in the window behaved (the Runtime panel).

    Restricted to ``category='builtin'`` so Completed + Failed + Pending is the
    same population as the Dashboard's "Tool calls" KPI; skills, agent runs and
    MCP calls carry their own status columns on their tabs. ``completed`` and
    ``incomplete`` split the ``calls`` total exactly: Qoder leaves a call without
    a status when its paired ``tool_result`` never arrived, which is a fact about
    the transcript, not about the tool. ``avg_ms`` averages only the calls that
    reported a duration (NULL when nothing ran, never a fabricated 0);
    ``sessions``/``projects`` are DISTINCT ids.
    """
    where, params = _where(start_ts, end_ts, "tool_calls")
    where = (where + " AND " if where else "WHERE ") + "category = 'builtin'"
    row = conn.execute(f"""
    SELECT COUNT(*) AS calls,
           SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS completed,
           SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) AS failed,
           SUM(CASE WHEN status IS NULL THEN 1 ELSE 0 END) AS pending,
           SUM(CASE WHEN status = 'backgrounded' THEN 1 ELSE 0 END) AS backgrounded,
           SUM(CASE WHEN status = 'cancelled' THEN 1 ELSE 0 END) AS cancelled,
           SUM(CASE WHEN status IS NULL OR status <> 'completed' THEN 1 ELSE 0 END)
               AS incomplete,
           CAST(AVG(CASE WHEN status IS NOT NULL THEN duration_ms END) AS INTEGER)
               AS avg_ms,
           COUNT(DISTINCT session_id) AS sessions,
           COUNT(DISTINCT project) AS projects
    FROM tool_calls {where}
    """, params).fetchone()
    return dict(row)


def overview(conn) -> dict:
    """Row counts for every indexed table — what `qcut health` prints."""
    out = {}
    for table in ("sessions", "model_calls", "tool_calls", "skill_usage",
                  "agent_usage", "mcp_usage", "plugin_usage", "commands",
                  "compactions", "inventory", "sync_state", "unparsed"):
        try:
            out[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        except sqlite3.OperationalError:
            out[table] = None
    return out


def q_overview(conn) -> dict:
    """All-time counts for the status bar: what is indexed right now.

    Every key is a ``COUNT`` (never a ``SUM``), so 0 means "nothing indexed",
    not "the provider reported nothing". ``unparsed_records`` is the drift
    signal: records the indexer saw and could not use, which is what turns a
    changed log format into something visible instead of a panel full of 0.
    """
    def one(sql, default=0):
        try:
            row = conn.execute(sql).fetchone()
        except sqlite3.OperationalError:
            return default
        if row is None or row[0] is None:
            return default
        return row[0]

    # The reach of the index, over every usage table that carries a `ts` — not just
    # the first one that has rows. `qcut stats` prints both ends, because "all
    # time" is only honest next to how far the index actually goes: a cursor that
    # stopped a week ago reads as a quiet week otherwise, and a span taken from one
    # table alone hides work recorded in another. `sessions` is left out on purpose
    # — its own span lives in `started_at`/`ended_at`, not in a `ts` column.
    lo = hi = None
    for table in ("model_calls", "tool_calls", "skill_usage", "agent_usage",
                  "mcp_usage", "plugin_usage", "commands", "compactions"):
        try:
            row = conn.execute(f"SELECT MIN(ts), MAX(ts) FROM {table}").fetchone()
        except sqlite3.OperationalError:
            continue
        first, last = row or (None, None)
        if first is not None:
            lo = first if lo is None else min(lo, first)
        if last is not None:
            hi = last if hi is None else max(hi, last)

    return {
        "tool_calls": one("SELECT COUNT(*) FROM tool_calls"),
        "builtin_tool_calls": one(
            "SELECT COUNT(*) FROM tool_calls WHERE category = 'builtin'"),
        "skills": one("SELECT COUNT(*) FROM skill_usage"),
        "agents": one("SELECT COUNT(*) FROM agent_usage"),
        "plugins_used": one("SELECT COUNT(DISTINCT plugin) FROM plugin_usage"),
        "mcp": one("SELECT COUNT(*) FROM mcp_usage"),
        "commands": one("SELECT COUNT(*) FROM commands"),
        "model_responses": one("SELECT COUNT(*) FROM model_calls"),
        "compactions": one("SELECT COUNT(*) FROM compactions"),
        "unparsed_records": one("SELECT SUM(count) FROM unparsed"),
        "last_ts": hi,
        "first_ts": lo,
    }
