#!/usr/bin/env python3
"""qcut-sync — index Qoder CN's own session transcripts into the tracker database.

Passive reader: walks ``~/.qoder-cn/projects/**/*.jsonl`` (session transcripts),
classifies every tool call into one of the five tracked categories, and upserts
it into SQLite. Incremental: each file remembers how many bytes were already
parsed, so re-runs only read the tail.

    python3 scripts/qcut-sync.py            # incremental
    python3 scripts/qcut-sync.py --full     # wipe tracker tables, re-index all
    python3 scripts/qcut-sync.py --quiet

Standard library only.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import qcut_db as db  # noqa: E402

# --- Qoder CN format registry ----------------------------------------------
#
# Everything qcut assumes about Qoder CN's data: on-disk layout, record shape,
# field names, and the tool names that drive classification. This is the block
# to re-verify when Qoder ships a new version; AGENTS.md §Format coupling points
# here and docs/maintenance/qoder-format.md carries `qcut format`'s output.
#
# Why it matters: a renamed field or record type raises nothing. The panels go
# empty, `qcut sync` prints "done", CI stays green. The ``unparsed`` counter in
# ``_handle_record`` is the runtime signal; this registry is the surface it is
# measured against.

# On-disk layout, all relative to ~/.qoder-cn (``db.QODER_DIR``).
DIR_PROJECTS = "projects"
DIR_SKILLS = "skills"
TRANSCRIPT_GLOB = "**/*.jsonl"
# A skill is the *parent of a manifest*: skills/<skill>/SKILL.md.
SKILL_MANIFEST_GLOB = "**/SKILL.md"
MCP_FILE = "mcp.json"
SETTINGS_FILE = "settings.json"
PLUGIN_SUBDIRS = (("skills", "skill"), ("agents", "agent"), ("commands", "command"))
# Plugin index keys are "<name>@<marketplace>".
PLUGIN_INDEX_FILES = ("plugins/installed_plugins_v2.json", "plugins/installed_plugins.json")
PLUGIN_MARKETPLACE_SEP = "@"
PLUGIN_INDEX_PLUGIN_KEY = "plugins"
# A directory entry starting with this is not an inventory item. This is a POSIX
# convention, not a Qoder one, and it is registered because the guard treats every
# literal passed to a naming method as a format claim it must be able to name.
HIDDEN_NAME_PREFIX = "."
# `Skill` may be invoked as "<plugin>:<skill>"; the part before the separator
# names the owning plugin.
SKILL_PLUGIN_SEP = ":"

# A transcript is newline-delimited JSON. The same separator decides where an
# incremental offset may resume: one byte mid-line is a rewritten file.
TRANSCRIPT_LINE_SEP = b"\n"
# Timestamps are ISO-8601 with a trailing `Z` for UTC; `_parse_ts` turns that into
# an explicit offset. Registered because a change here silently nulls every `ts`.
TRANSCRIPT_TS_UTC_SUFFIX = "Z"

# Tool names that carry meaning for classification (see ``_classify``).
SKILL_TOOL = "Skill"
AGENT_TOOL = "Agent"
# Qoder's MCP shape is *not* ``mcp__server__tool`` as a tool name. The model
# calls one of these three, and names the real target inside its arguments.
MCP_TOOLS = {"mcp_call": "call", "mcp_get": "get", "mcp_list": "list"}
MCP_TARGET_PREFIX = "mcp__"       # value of the `toolName` argument
MCP_TARGET_SEP = "__"
# A custom (BYOK) route is named "<providerId>/<model>".
PROVIDER_MODEL_SEP = "/"
CUSTOM_PROVIDER_PREFIX = "qoder-custom-"

CMD_RE = re.compile(r"<command-name>\s*/?([^<\s]+)\s*</command-name>")

# ``type`` values a handler claims in ``_handle_record``, including the ones it
# deliberately does nothing with. A record type nobody claims is counted in
# ``unparsed`` — that count is how a format change becomes visible.
#
# The no-op set is real and load-bearing: on this machine `active-leaf` alone
# runs to tens of thousands of records, and quietly counting them as "unparsed"
# would make the signal useless.
CLAIMED_RECORD_TYPES = frozenset({
    "assistant", "user", "runtime-config", "workspace-directories",
})
NOOP_RECORD_TYPES = frozenset({
    "active-leaf", "attachment", "file-history-snapshot", "last-prompt",
    "custom-title", "worktree-state", "system",
})
# `last-prompt` and `custom-title` carry free prose (the user's own prompt and
# the AI-written title). They are claimed and dropped, never read. `system` is
# dropped too: it has no branch, and a type in CLAIMED with no handler is the
# one mistake this split exists to prevent — it would read as "parsed".

# --- classification tables -------------------------------------------------

# Tools observed in the transcripts on this machine (2026-10-12). Anything not
# Skill/Agent/mcp/meta is a builtin tool even if absent here — the set only
# powers the "available" view and the inventory rows.
BUILTIN_TOOLS = {
    "Bash", "Edit", "Read", "Write", "Grep", "Glob", "NotebookEdit", "MultiEdit",
    "WebFetch", "WebSearch", "SearchKnowledge", "TaskCreate", "TaskGet",
    "TaskUpdate", "TaskList", "TaskStop", "TaskOutput", "AskUserQuestion",
    "EnterPlanMode", "ExitPlanMode", "Skill", "Agent",
}

# The Agent tool's own contract: when the caller omits ``subagent_type`` the
# call runs as ``general-purpose``. A call carrying neither that nor
# ``agent_type`` is recorded under this name, never a "?".
DEFAULT_AGENT_TYPE = "general-purpose"

# Subagent types observed here; the set only feeds the "available" inventory.
BUILTIN_AGENTS = {"Explore", "Plan", "general-purpose"}

# Qoder's own model routes report credits and no token counts; a BYOK route
# reports tokens and no credits. Recorded verbatim — see the model_calls DDL.
PROVIDER_BUILTIN = "builtin"

# Vendor names for a BYOK base URL host. The label is read from the configured
# endpoint, never inferred from the model *name* — an alias can look built-in.
VENDOR_HOSTS = {
    "volces.com": "Volcano ARK",
    "aliyuncs.com": "Alibaba Cloud",
    "deepseek.com": "DeepSeek",
    "moonshot.cn": "Moonshot",
    "bigmodel.cn": "Zhipu",
    "siliconflow.cn": "SiliconFlow",
    "openai.com": "OpenAI",
    "anthropic.com": "Anthropic",
    "minimaxi.com": "MiniMax",
    "tencentcloudapi.com": "Tencent Cloud",
}

# --- field mirrors: the record shape we read, by object --------------------
#
# These sets are a *claim about the parser*, and `test_format_registry.py` reads
# the AST both ways to keep them honest: a field read without being listed fails,
# and a field listed but never read fails too. Sixteen of the names that used to
# sit here (`uuid`, `parentUuid`, `exitCode`, `durationMs`, …) were fields Qoder
# does send that this parser never looks at — plausible, harmless, and a registry
# that overstates its own surface is one that stops being re-checked on a rename.
# An observed-but-unused field belongs in a comment, not in the dependency list.

RECORD_FIELDS = frozenset({
    "type", "timestamp", "message", "isSidechain", "cwd", "sessionId",
    "entrypoint", "version", "gitBranch", "customProviderId", "toolUseResult",
    "directories",
    # `runtime-config` names the session's model at the *top* level, not inside
    # `message`, so the same key is read off two different objects.
    "model",
})
MESSAGE_FIELDS = frozenset({
    "id", "type", "model", "stop_reason", "content", "usage",
})
# The text block of a message — the only place a slash-command name can appear.
# Reading the block's *text* is not metadata: only the string inside the
# `<command-name>` marker is kept, which is what `CMD_RE` matches.
TEXT_BLOCK_FIELDS = frozenset({"type", "text"})
USAGE_FIELDS = frozenset({
    "input_tokens", "output_tokens",
    "cache_read_input_tokens", "cache_creation_input_tokens",
    "credits", "original_credits", "billable", "request_id", "context_usage_ratio",
})
TOOL_USE_FIELDS = frozenset({"type", "id", "name", "input"})
TOOL_RESULT_FIELDS = frozenset({"type", "tool_use_id", "content", "is_error"})
# `stop_reason` and `isError` are read; the rest of what Qoder puts in
# `toolUseResult` (exitCode, signal, interrupted, timedOut, isImage, agentType,
# status, durationMs) is ignored — duration in particular is computed from the
# row's own start timestamp, not from `durationMs`, so a result that lands after
# its call was indexed still pairs up.
TOOL_RESULT_META_FIELDS = frozenset({"kind", "isError", "commandName"})
# Tool *argument names*. Reading a name is metadata; reading its value is the
# privacy line, and only these are ever stored — the skill name, the agent type
# and the MCP target, all of which are catalog entries rather than prose.
# `command` and `tool_name` are the snake_case aliases the same two tools answer
# to, so they are read as often as `skill` / `toolName`.
TOOL_ARG_FIELDS = frozenset({"skill", "command", "subagent_type", "agent_type",
                             "toolName", "tool_name"})
# JSON keys of the non-transcript files we read: the inventory files plus
# settings.json (whose providers[].baseUrl gives a custom route its vendor).
INVENTORY_JSON_FIELDS = frozenset({
    "mcpServers", "plugins", "installPath", "version", "source", "providers", "baseUrl",
})

QODER_FIELDS = (RECORD_FIELDS | MESSAGE_FIELDS | TEXT_BLOCK_FIELDS
                | USAGE_FIELDS | TOOL_USE_FIELDS
                | TOOL_RESULT_FIELDS | TOOL_RESULT_META_FIELDS | TOOL_ARG_FIELDS
                | INVENTORY_JSON_FIELDS)

# qcut's own vocabulary in the ``unparsed`` table — NOT Qoder fields.
UNPARSEABLE_LINE = "unparseable_line"
MISSING_TYPE = "type:<missing>"
# Keys of dicts *qcut* builds and reads: its `sync_state` columns, its
# inventory-side plugin→skill map, and `run()`'s result. They are listed because
# the format mirror cannot tell an upstream dependency from an internal key by
# itself, and a literal read that matches neither set must fail the guard — the
# day a handler starts doing `rec.get("file_path")` on a transcript, that is a
# new dependency on upstream data and belongs in the *_FIELDS sets above.
# Like the mirror sets, this one's consumer is `test_format_registry.py`.
QCUT_INTERNAL_KEYS = frozenset({
    "skill_owner",
    "file_path", "offset", "size", "mtime",
    "counts", "seconds", "db",
})


# --- small helpers ---------------------------------------------------------


def _bump(counts: dict, key: str, n: int = 1) -> None:
    counts[key] = counts.get(key, 0) + n


def _load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _parse_ts(value):
    """ISO-8601 (with a trailing ``Z``) -> epoch milliseconds, or ``None``."""
    if not isinstance(value, str) or not value:
        return None
    text = value.strip()
    if text.endswith(TRANSCRIPT_TS_UTC_SUFFIX):
        text = text[:-len(TRANSCRIPT_TS_UTC_SUFFIX)] + "+00:00"
    try:
        from datetime import datetime
        return int(datetime.fromisoformat(text).timestamp() * 1000)
    except ValueError:
        return None


def _decode_project(dirname: str) -> str:
    """Fallback project path from a transcript directory name.

    Qoder names a project directory after its cwd with the separators replaced
    by '-', which is lossy for a path containing a hyphen. Records carry the
    real ``cwd``, so this is only used when a record has none.
    """
    if not dirname:
        return ""
    return "/" + dirname.lstrip("-").replace("-", "/")


def url_host(url) -> str:
    if not isinstance(url, str):
        return ""
    m = re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://([^/?#]+)", url.strip())
    return m.group(1).lower() if m else ""


def _vendor_for_host(host: str):
    for suffix, vendor in VENDOR_HOSTS.items():
        if host == suffix or host.endswith("." + suffix):
            return vendor
    return None


def load_providers(qoder_dir) -> dict:
    """``{provider_id: label}`` from settings.json's custom provider block."""
    data = _load_json(Path(qoder_dir) / SETTINGS_FILE) or {}
    out = {}
    for pid, spec in (data.get("providers") or {}).items():
        if not isinstance(spec, dict):
            continue
        host = url_host(spec.get("baseUrl"))
        if not host:
            continue
        out[pid] = _vendor_for_host(host) or host
    return out


def resolve_route(model_raw, custom_provider_id, providers):
    """``(model_display, provider_label, provider_id)`` for a response.

    A route is custom when the model string is ``<providerId>/<model>``; the
    vendor label then comes from the configured endpoint host, falling back to
    the host itself, falling back to the provider id. It is never inferred from
    the model name.
    """
    if not isinstance(model_raw, str) or not model_raw:
        return model_raw, None, custom_provider_id
    if PROVIDER_MODEL_SEP in model_raw:
        pid, model = model_raw.split(PROVIDER_MODEL_SEP, 1)
        if pid.startswith(CUSTOM_PROVIDER_PREFIX):
            label = (providers or {}).get(pid) or url_host(
                ((providers or {}).get(pid) or "")
            ) or pid
            return model, label, pid
        return model, pid, pid
    if custom_provider_id:
        label = (providers or {}).get(custom_provider_id) or custom_provider_id
        return model_raw, label, custom_provider_id
    return model_raw, PROVIDER_BUILTIN, None


def _classify(name: str, args: dict):
    """``(category, payload)`` for one tool_use block."""
    if name == SKILL_TOOL:
        skill = args.get("skill") or args.get("command") or ""
        return "skill", skill
    if name == AGENT_TOOL:
        return "agent", args.get("subagent_type") or args.get("agent_type") or DEFAULT_AGENT_TYPE
    if name in MCP_TOOLS:
        target = args.get("toolName") or args.get("tool_name") or ""
        return "mcp_tool", (MCP_TOOLS[name], target)
    return "builtin", None


def _split_mcp_target(target: str):
    """``mcp__<server>__<tool>`` -> ``(server, tool)``; ``(None, None)`` otherwise."""
    if not isinstance(target, str) or not target.startswith(MCP_TARGET_PREFIX):
        return None, None
    body = target[len(MCP_TARGET_PREFIX):]
    if MCP_TARGET_SEP in body:
        server, tool = body.split(MCP_TARGET_SEP, 1)
        return server or None, tool or None
    return body or None, None


def _status_from(kind, is_error):
    """Status for a call whose result *is* in hand.

    A record that carries a ``tool_result`` block has returned, whatever else it
    says — so a missing ``toolUseResult.kind`` (the write shape of older
    records: about 14k of 31.7k results here) means "completed", not "pending".
    Only a call with no result block at all stays ``NULL`` (pending); conflating
    the two reported 13k finished calls as unfinished.
    """
    if is_error:
        return "error"
    if kind == "cancelled":
        return "cancelled"
    if kind == "backgrounded":
        return "backgrounded"
    return "completed"


# --- inventory -------------------------------------------------------------


def scan_inventory(conn: sqlite3.Connection, qoder_dir) -> dict:
    """Record what is installed/available, so a tab can show unused rows.

    Read-only over small config and manifest files; a missing file is not an
    error (a fresh install has no plugins yet).
    """
    root = Path(qoder_dir)
    counts = {}

    # Tools and agents that ship with Qoder.
    for name in sorted(BUILTIN_TOOLS):
        conn.execute(
            "INSERT OR REPLACE INTO inventory(kind,name,owner_plugin,version,path,source)"
            " VALUES('tool',?,NULL,NULL,NULL,'builtin')", (name,))
    for name in sorted(BUILTIN_AGENTS):
        conn.execute(
            "INSERT OR REPLACE INTO inventory(kind,name,owner_plugin,version,path,source)"
            " VALUES('agent',?,NULL,NULL,NULL,'builtin')", (name,))

    # User skills: skills/<skill>/SKILL.md — the *parent* of the manifest.
    n = 0
    skills_dir = root / DIR_SKILLS
    if skills_dir.is_dir():
        for manifest in skills_dir.glob(SKILL_MANIFEST_GLOB):
            name = manifest.parent.name
            if not name or name.startswith(HIDDEN_NAME_PREFIX):
                continue
            conn.execute(
                "INSERT OR REPLACE INTO inventory(kind,name,owner_plugin,version,path,source)"
                " VALUES('skill',?,NULL,NULL,?,'user')", (name, str(manifest.parent)))
            n += 1
    counts["skills"] = n

    # Plugins. The v2 index is the richer one; both shapes are accepted.
    n = 0
    seen = set()
    for rel in PLUGIN_INDEX_FILES:
        data = _load_json(root / rel)
        if not isinstance(data, dict):
            continue
        for key, spec in (data.get(PLUGIN_INDEX_PLUGIN_KEY) or {}).items():
            entries = spec if isinstance(spec, list) else [spec]
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                name, _, marketplace = str(key).partition(PLUGIN_MARKETPLACE_SEP)
                if (name, marketplace) in seen:
                    continue
                seen.add((name, marketplace))
                path = entry.get("installPath")
                conn.execute(
                    "INSERT OR REPLACE INTO inventory"
                    "(kind,name,owner_plugin,version,path,source)"
                    " VALUES('plugin',?,?,?,?,?)",
                    (name, marketplace or "", entry.get("version"), path,
                     entry.get("source") or "marketplace"))
                n += 1
                # A plugin's own skills/agents/commands, keyed by name.
                if path and Path(path).is_dir():
                    for sub, kind in PLUGIN_SUBDIRS:
                        if kind == "skill":
                            for m in Path(path).glob(f"{sub}/**/SKILL.md"):
                                conn.execute(
                                    "INSERT OR REPLACE INTO inventory"
                                    "(kind,name,owner_plugin,version,path,source)"
                                    " VALUES('skill',?,?,?,?,'plugin')",
                                    (m.parent.name, name, entry.get("version"), str(m.parent)))
                        else:
                            for m in Path(path).glob(f"{sub}/*.md"):
                                conn.execute(
                                    "INSERT OR REPLACE INTO inventory"
                                    "(kind,name,owner_plugin,version,path,source)"
                                    " VALUES(?,?,?,?,?,'plugin')",
                                    (kind, m.stem, name, entry.get("version"), str(m)))
        if n:
            break
    counts["plugins"] = n

    # Configured MCP servers.
    n = 0
    data = _load_json(root / MCP_FILE) or {}
    for name in (data.get("mcpServers") or {}):
        conn.execute(
            "INSERT OR REPLACE INTO inventory(kind,name,owner_plugin,version,path,source)"
            " VALUES('mcp',?,NULL,NULL,NULL,'user')", (name,))
        n += 1
    counts["mcp"] = n

    # Skill-name -> owning plugin, so a used skill can be credited to a plugin.
    owners = {}
    for row in conn.execute(
            "SELECT name, owner_plugin FROM inventory WHERE kind='skill' AND owner_plugin IS NOT NULL"):
        owners[row[0]] = row[1]
    return {"counts": counts, "skill_owner": owners}


# --- record handlers -------------------------------------------------------


def _session_row(rec):
    return (
        rec.get("sessionId"),
        rec.get("cwd"),
        rec.get("gitBranch"),
        rec.get("version"),
        rec.get("entrypoint"),
    )


def _upsert_session(conn, rec, ts, project):
    sid, cwd, branch, version, entrypoint = _session_row(rec)
    if not sid:
        return
    conn.execute(
        """
        INSERT INTO sessions(session_id, project, cwd, git_branch, version,
                             entrypoint, started_at, ended_at, model)
        VALUES(?,?,?,?,?,?,?,?,NULL)
        ON CONFLICT(session_id) DO UPDATE SET
            project    = COALESCE(excluded.project, sessions.project),
            cwd        = COALESCE(excluded.cwd, sessions.cwd),
            git_branch = COALESCE(excluded.git_branch, sessions.git_branch),
            version    = COALESCE(excluded.version, sessions.version),
            entrypoint = COALESCE(excluded.entrypoint, sessions.entrypoint),
            started_at = MIN(COALESCE(sessions.started_at, excluded.started_at),
                             COALESCE(excluded.started_at, sessions.started_at)),
            ended_at   = MAX(COALESCE(sessions.ended_at, excluded.ended_at),
                             COALESCE(excluded.ended_at, sessions.ended_at))
        """,
        (sid, project, cwd, branch, version, entrypoint, ts, ts),
    )


def _record_model_call(conn, rec, msg, ts, project, providers) -> int:
    message_id = msg.get("id")
    usage = msg.get("usage")
    if not message_id or not isinstance(usage, dict):
        return 0
    model, provider, provider_id = resolve_route(
        msg.get("model"), rec.get("customProviderId"), providers)
    cur = conn.execute(
        """
        INSERT OR IGNORE INTO model_calls(
            message_id, session_id, project, ts, model, provider, provider_id,
            input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens,
            credits, original_credits, billable, request_id, context_usage_ratio,
            stop_reason, is_sidechain, has_usage)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)
        """,
        (
            message_id, rec.get("sessionId"), project, ts, model, provider, provider_id,
            usage.get("input_tokens"), usage.get("output_tokens"),
            usage.get("cache_read_input_tokens"), usage.get("cache_creation_input_tokens"),
            usage.get("credits"), usage.get("original_credits"),
            None if usage.get("billable") is None else int(bool(usage.get("billable"))),
            usage.get("request_id"), usage.get("context_usage_ratio"),
            msg.get("stop_reason"),
            1 if rec.get("isSidechain") else 0,
        ),
    )
    return cur.rowcount or 0


def _record_tool_use(conn, rec, block, ts, project, skill_owner, counts) -> None:
    call_id = block.get("id")
    name = block.get("name")
    if not call_id or not name:
        return
    args = block.get("input")
    if not isinstance(args, dict):
        args = {}
    category, payload = _classify(name, args)
    sid = rec.get("sessionId")
    side = 1 if rec.get("isSidechain") else 0

    conn.execute(
        """
        INSERT OR IGNORE INTO tool_calls(
            call_id, session_id, project, tool_name, category, ts, duration_ms,
            status, is_sidechain)
        VALUES(?,?,?,?,?,?,NULL,NULL,?)
        """,
        (call_id, sid, project, name, category, ts, side),
    )
    _bump(counts, "tool_calls")

    if category == "skill":
        skill = payload or ""
        plugin = None
        if ":" in skill:
            plugin = skill.split(SKILL_PLUGIN_SEP, 1)[0]
        plugin = plugin or skill_owner.get(skill)
        conn.execute(
            "INSERT OR IGNORE INTO skill_usage"
            "(call_id, skill, plugin, session_id, project, ts, status, duration_ms)"
            " VALUES(?,?,?,?,?,?,NULL,NULL)",
            (call_id, skill, plugin, sid, project, ts),
        )
        _bump(counts, "skill_calls")
        if plugin:
            conn.execute(
                "INSERT OR IGNORE INTO plugin_usage(plugin, marketplace, kind, target,"
                " session_id, project, ts) VALUES(?,?,?,?,?,?,?)",
                (plugin, None, "skill", skill, sid, project, ts),
            )
    elif category == "agent":
        conn.execute(
            "INSERT OR IGNORE INTO agent_usage"
            "(call_id, agent_type, session_id, project, ts, status, duration_ms)"
            " VALUES(?,?,?,?,?,NULL,NULL)",
            (call_id, payload, sid, project, ts),
        )
        _bump(counts, "agent_calls")
    elif category == "mcp_tool":
        op, target = payload
        server, tool = _split_mcp_target(target)
        conn.execute(
            "INSERT OR IGNORE INTO mcp_usage"
            "(call_id, server, tool, admin_op, session_id, project, ts, status, duration_ms)"
            " VALUES(?,?,?,?,?,?,?,NULL,NULL)",
            (call_id, server, tool, op, sid, project, ts),
        )
        _bump(counts, "mcp_calls")


def _record_tool_result(conn, rec, block, ts, counts) -> None:
    call_id = block.get("tool_use_id")
    if not call_id:
        return
    meta = rec.get("toolUseResult")
    if not isinstance(meta, dict):
        meta = {}
    kind = meta.get("kind")
    is_error = bool(block.get("is_error")) or bool(meta.get("isError"))
    status = _status_from(kind, is_error)
    # duration is computed from the row's own start ts, so a result that lands
    # after the tool_use was indexed in an earlier run still pairs up.
    conn.execute(
        "UPDATE tool_calls SET status = ?, duration_ms = CASE "
        "  WHEN ts IS NULL OR ? IS NULL OR ? < ts THEN NULL ELSE ? - ts END "
        "WHERE call_id = ? AND status IS NULL",
        (status, ts, ts, ts, call_id),
    )
    conn.execute(
        "UPDATE skill_usage SET status = ?, duration_ms = CASE "
        "  WHEN ts IS NULL OR ? IS NULL OR ? < ts THEN NULL ELSE ? - ts END "
        "WHERE call_id = ? AND status IS NULL",
        (status, ts, ts, ts, call_id),
    )
    conn.execute(
        "UPDATE agent_usage SET status = ?, duration_ms = CASE "
        "  WHEN ts IS NULL OR ? IS NULL OR ? < ts THEN NULL ELSE ? - ts END "
        "WHERE call_id = ? AND status IS NULL",
        (status, ts, ts, ts, call_id),
    )
    conn.execute(
        "UPDATE mcp_usage SET status = ?, duration_ms = CASE "
        "  WHEN ts IS NULL OR ? IS NULL OR ? < ts THEN NULL ELSE ? - ts END "
        "WHERE call_id = ? AND status IS NULL",
        (status, ts, ts, ts, call_id),
    )
    _bump(counts, "tool_results")
    command = meta.get("commandName")
    if isinstance(command, str) and command:
        conn.execute(
            "INSERT OR IGNORE INTO commands(command, session_id, project, ts)"
            " VALUES(?,?,?,?)", (command, rec.get("sessionId"), None, ts))


def _record_commands(conn, rec, msg, ts, project, counts) -> None:
    content = msg.get("content")
    if not isinstance(content, list):
        return
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "text":
            continue
        text = block.get("text")
        if not isinstance(text, str):
            continue
        m = CMD_RE.search(text)
        if m:
            conn.execute(
                "INSERT OR IGNORE INTO commands(command, session_id, project, ts)"
                " VALUES(?,?,?,?)",
                (m.group(1), rec.get("sessionId"), project, ts),
            )
            _bump(counts, "commands")


def _handle_record(conn, rec, providers, skill_owner, fallback_project, counts) -> None:
    rtype = rec.get("type")
    if rtype is None:
        _bump(counts, MISSING_TYPE)
        return
    if rtype in NOOP_RECORD_TYPES:
        return
    if rtype not in CLAIMED_RECORD_TYPES:
        _bump(counts, f"type:{rtype}")
        return

    ts = _parse_ts(rec.get("timestamp"))
    project = rec.get("cwd") or fallback_project
    if rtype == "assistant":
        msg = rec.get("message")
        if not isinstance(msg, dict):
            return
        _upsert_session(conn, rec, ts, project)
        _record_model_call(conn, rec, msg, ts, project, providers)
        content = msg.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    _record_tool_use(conn, rec, block, ts, project, skill_owner, counts)
        _record_commands(conn, rec, msg, ts, project, counts)
    elif rtype == "user":
        msg = rec.get("message")
        if not isinstance(msg, dict):
            return
        content = msg.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    _record_tool_result(conn, rec, block, ts, counts)
    elif rtype == "runtime-config":
        sid = rec.get("sessionId")
        model = rec.get("model")
        if sid and model:
            conn.execute(
                "UPDATE sessions SET model = COALESCE(?, model) WHERE session_id = ?",
                (model, sid))
    elif rtype == "workspace-directories":
        sid = rec.get("sessionId")
        dirs = rec.get("directories")
        if sid and isinstance(dirs, list) and dirs:
            conn.execute(
                "UPDATE sessions SET project = COALESCE(project, ?) WHERE session_id = ?",
                (dirs[0] if isinstance(dirs[0], str) else None, sid))


# --- file indexing ---------------------------------------------------------


def index_file(conn, path: Path, offset: int, providers, skill_owner,
               fallback_project: str, counts: dict) -> int:
    """Parse one transcript from ``offset``; return the new offset.

    Only whole lines are consumed: a trailing partial line (a file being
    appended to right now) is left for the next run, so an offset always sits on
    a line boundary.
    """
    size = path.stat().st_size
    if offset > size:
        offset = 0
    with open(path, "rb") as fh:
        fh.seek(offset)
        data = fh.read()
    if not data:
        return offset
    consumed = data.rfind(TRANSCRIPT_LINE_SEP)
    if consumed == -1:
        return offset
    consumed += 1
    for raw in data[:consumed].split(TRANSCRIPT_LINE_SEP):
        if not raw.strip():
            continue
        try:
            rec = json.loads(raw.decode("utf-8", "replace"))
        except ValueError:
            _bump(counts, UNPARSEABLE_LINE)
            continue
        if not isinstance(rec, dict):
            _bump(counts, UNPARSEABLE_LINE)
            continue
        try:
            _handle_record(conn, rec, providers, skill_owner, fallback_project, counts)
        except sqlite3.Error:
            raise
    return offset + consumed


def _starts_on_line_boundary(path: Path, offset: int) -> bool:
    if offset <= 0:
        return True
    with open(path, "rb") as fh:
        fh.seek(offset - 1)
        return fh.read(1) == TRANSCRIPT_LINE_SEP


def _needs_reset(path: Path, st, row) -> bool:
    """Whether a file must be re-read from 0 (rewritten or replaced)."""
    if row is None:
        return False
    return st.st_size < (row["offset"] or 0)


def format_doc() -> str:
    """The format surface `qcut format` prints and docs/maintenance carries."""
    lines = [
        "# Qoder CN format surface",
        "",
        "Generated by `qcut format`. Everything this tool assumes about Qoder CN's",
        "own files. Re-verify after a Qoder update; the procedure is in "
        "[`MAINTENANCE.md`](../../MAINTENANCE.md).",
        "",
        "## Layout (relative to ~/.qoder-cn)",
        "",
        f"- transcripts: `{DIR_PROJECTS}/{TRANSCRIPT_GLOB}`",
        f"- skill manifests: `{DIR_SKILLS}/{SKILL_MANIFEST_GLOB}`",
        f"- plugin index: `{', '.join(PLUGIN_INDEX_FILES)}`",
        f"- MCP servers: `{MCP_FILE}`",
        f"- settings (BYOK routes): `{SETTINGS_FILE}`",
        f"- line separator: `{TRANSCRIPT_LINE_SEP!r}`",
        "",
        "## Record types",
        "",
        f"- claimed: {', '.join(sorted(CLAIMED_RECORD_TYPES))}",
        f"- claimed and dropped: {', '.join(sorted(NOOP_RECORD_TYPES))}",
        "- anything else is counted in `unparsed` as `type:<name>`",
        "",
        "## Fields read",
        "",
    ]
    for label, fields in (
        ("record", RECORD_FIELDS), ("message", MESSAGE_FIELDS),
        ("usage", USAGE_FIELDS), ("tool_use", TOOL_USE_FIELDS),
        ("tool_result", TOOL_RESULT_FIELDS), ("toolUseResult", TOOL_RESULT_META_FIELDS),
        ("tool arguments", TOOL_ARG_FIELDS), ("inventory JSON", INVENTORY_JSON_FIELDS),
    ):
        lines.append(f"- {label}: {', '.join(sorted(fields))}")
    lines += [
        "",
        "## Classification",
        "",
        f"- skill tool: `{SKILL_TOOL}` (arguments "
        + ", ".join(f"`{a}`" for a in sorted(
            {"skill", "command"} & TOOL_ARG_FIELDS)) + ")",
        f"- agent tool: `{AGENT_TOOL}` (arguments `subagent_type`, `agent_type`"
        f"; default `{DEFAULT_AGENT_TYPE}`)",
        "- mcp tools: " + ", ".join(f"`{k}` -> {v}" for k, v in sorted(MCP_TOOLS.items())),
        f"- mcp target: `{MCP_TARGET_PREFIX}<server>{MCP_TARGET_SEP}<tool>` in the `toolName` argument",
        f"- custom route: `<providerId>{PROVIDER_MODEL_SEP}<model>`, prefix `{CUSTOM_PROVIDER_PREFIX}`",
        "",
    ]
    return "\n".join(lines)


# --- top level -------------------------------------------------------------


def run(full: bool = False, quiet: bool = False, db_path=db.DB_PATH) -> dict:
    conn = db.open_db(db_path)
    try:
        db.ensure_schema(conn)
        return _run(conn, full=full, quiet=quiet)
    finally:
        conn.close()


def _run(conn, full: bool = False, quiet: bool = False) -> dict:
    started = time.time()
    qoder_dir = db.QODER_DIR
    root = Path(qoder_dir) / DIR_PROJECTS
    if full:
        db.snapshot(conn, "before-full-sync", db.db_file(conn) or db.DB_PATH)
        db.reset(conn)

    inv = scan_inventory(conn, qoder_dir)
    skill_owner = inv["skill_owner"]
    providers = load_providers(qoder_dir)

    files = sorted(root.glob(TRANSCRIPT_GLOB)) if root.is_dir() else []
    state = {
        row["file_path"]: row
        for row in conn.execute("SELECT file_path, size, mtime, offset FROM sync_state")
    }

    counts: dict = {}
    scanned = 0
    for path in files:
        key = str(path)
        try:
            st = path.stat()
        except OSError:
            continue
        row = state.get(key)
        offset = 0
        if row is not None and not _needs_reset(path, st, row):
            if row["size"] == st.st_size and row["mtime"] == st.st_mtime:
                continue
            offset = row["offset"] or 0
            if not _starts_on_line_boundary(path, offset):
                offset = 0
        fallback = _decode_project(path.parent.name)
        try:
            new_offset = index_file(conn, path, offset, providers, skill_owner,
                                    fallback, counts)
        except OSError:
            _bump(counts, "unreadable_file")
            continue
        conn.execute(
            "INSERT OR REPLACE INTO sync_state(file_path, size, mtime, offset)"
            " VALUES(?,?,?,?)", (key, st.st_size, st.st_mtime, new_offset))
        scanned += 1

    for reason, n in counts.items():
        if reason in ("tool_calls", "tool_results", "skill_calls", "agent_calls",
                      "mcp_calls", "commands"):
            continue
        db.note_unparsed(conn, reason, n)
    conn.commit()

    summary = {
        "files": len(files),
        "scanned": scanned,
        "seconds": round(time.time() - started, 2),
        "counts": counts,
        "inventory": inv["counts"],
        "db": str(db.db_file(conn) or db.DB_PATH),
    }
    if not quiet:
        print(f"indexed {scanned}/{len(files)} transcript files in "
              f"{summary['seconds']}s -> {summary['db']}")
        for key in sorted(counts):
            print(f"  {key}: {counts[key]}")
    return summary


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Index Qoder CN transcripts.")
    parser.add_argument("--full", action="store_true",
                        help="wipe the tracker tables and re-index every file")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--db", default=str(db.DB_PATH))
    args = parser.parse_args(argv)
    summary = run(full=args.full, quiet=args.quiet, db_path=Path(args.db))
    return 0 if summary else 1


if __name__ == "__main__":
    raise SystemExit(main())
