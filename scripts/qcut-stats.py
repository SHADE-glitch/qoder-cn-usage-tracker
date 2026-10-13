#!/usr/bin/env python3
"""qcut-stats — headless reports over the Qoder CN usage index.

Read-only: every subcommand below only reads the database. The two maintenance
subcommands (`backup`, `restore`) and `format --write` are the only writers, and
each says so in its own help.

    python3 scripts/qcut-stats.py stats            # overview of every category
    python3 scripts/qcut-stats.py tools|skills|agents|plugins|mcp
    python3 scripts/qcut-stats.py models           # per-model tokens + credits
    python3 scripts/qcut-stats.py show tool Bash   # recent calls for one entity
    python3 scripts/qcut-stats.py recent
    python3 scripts/qcut-stats.py inventory        # installed, used vs unused
    python3 scripts/qcut-stats.py export           # every table as JSON
    python3 scripts/qcut-stats.py health           # database + data-source check
    python3 scripts/qcut-stats.py format           # the Qoder format surface

Standard library only.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import qcut_db as db  # noqa: E402


# --- formatting ------------------------------------------------------------


def ts(v):
    if not v:
        return "-"
    return datetime.fromtimestamp(v / 1000).strftime("%Y-%m-%d %H:%M")


def ms(v):
    if v is None:
        return "-"
    if v < 1000:
        return f"{v:.0f}ms"
    return f"{v / 1000:.2f}s"


def fmt_n(v):
    if v is None:
        return "-"
    return f"{v:,}"


def fmt_credits(v):
    if v is None:
        return "-"
    if v == 0:
        return "0"
    return f"{v:,.2f}"


def fmt_tok(value, reported):
    """A token measure, or ``—`` when the route reports credits instead of tokens.

    Same three states as the TUI's renderer of the same name, and the same
    guard: a non-zero is shown whatever the flag claims, so the headless report
    and the TUI cannot tell two stories about one window — and neither can hide
    a measurement (``test_tui.py`` locks the two definitions together).
    """
    if value is None:
        return "-"
    if not reported and not value:
        return "—"
    return fmt_n(value)


def fmt_credit_total(value, carried):
    """An aggregate of credits: `-` when no row inside it carried the field.

    The mirror of ``fmt_tok``, and the same guard: a non-zero is always shown.
    A BYOK route has no `credits` field, so summing it with COALESCE yields a `0`
    that reads "billed nothing" — a claim about money that was never made in that
    unit. Per-row cells already render an absent field as `-` via `fmt_credits`.
    """
    if value is None or (not carried and not value):
        return "-"
    return fmt_credits(value)


def _display_width(text: str) -> int:
    """Terminal cells a string takes: East-Asian wide characters count as 2."""
    import unicodedata
    width = 0
    for ch in str(text):
        width += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return width


def table(headers, rows, aligns=None):
    """Render a padded table, preserving the given row order and text as-is."""
    rows = [[("" if c is None else str(c)) for c in row] for row in rows]
    widths = [_display_width(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], _display_width(cell))
    aligns = aligns or ["<"] * len(headers)
    out = ["  ".join(h.ljust(widths[i]) if aligns[i] == "<" else h.rjust(widths[i])
                     for i, h in enumerate(headers))]
    out.append("  ".join("-" * w for w in widths))
    for row in rows:
        out.append("  ".join(
            cell.ljust(widths[i]) if aligns[i] == "<" else cell.rjust(widths[i])
            for i, cell in enumerate(row)))
    return "\n".join(out)


def _rows(cur, keys):
    return [[row[k] for k in keys] for row in cur]


def _limit(args, default=50):
    return args.limit or default


# --- subcommands -----------------------------------------------------------


def cmd_stats(conn, args):
    k = db.q_usage_kpi(conn)
    # One flag decides every token line below, exactly as the TUI's single
    # `tokens_reported` decides its panels and its `Tok reported` column. An
    # empty window is not a route that withheld its tokens — `q_usage_kpi`
    # flattens NULL to 0, so the "no responses here" case has to be named here
    # or it would be drawn as the `—` that means "credits instead of tokens".
    k_tok = not k["requests"] or bool(k["tokens_reported"])
    o = db.q_overview(conn)
    # `all time` means the whole index, so the index says how far it reaches:
    # a stopped cursor and an idle week both print as a quiet week otherwise.
    span = "-" if not (o["first_ts"] or o["last_ts"]) \
        else f"{ts(o['first_ts'])} .. {ts(o['last_ts'])}"
    lines = [
        "Qoder CN usage — all time",
        "",
        f"  sessions indexed     {fmt_n(k['model_sessions'] or k['tool_sessions'])}",
        f"  projects             {fmt_n(k['tool_projects'] or k['model_projects'])}",
        f"  range                {span}",
        "",
        f"  model requests       {fmt_n(k['requests'])}",
        f"  input tokens         {fmt_tok(k['input_tokens'], k_tok)}",
        f"  output tokens        {fmt_tok(k['output_tokens'], k_tok)}",
        f"  cache read tokens    {fmt_tok(k['cache_read_tokens'], k_tok)}",
        f"  credits              {fmt_credit_total(k['credits'], k['credits_carried'])}",
        "",
        f"  tool calls           {fmt_n(k['tool_calls'])}"
        f"  ({fmt_n(k['tool_failed'])} failed, {fmt_n(k['tool_pending'])} pending)",
        f"  distinct tools       {fmt_n(len(db.q_tools(conn)))}",
        f"  skill invocations    {fmt_n(k['skill_calls'])}"
        f"  ({fmt_n(k['skill_names'])} distinct)",
        f"  subagent calls       {fmt_n(k['agent_calls'])}"
        f"  ({fmt_n(k['agent_names'])} types)",
        f"  mcp calls            {fmt_n(k['mcp_calls'])}"
        f"  ({fmt_n(k['mcp_servers'])} servers)",
    ]
    if not k_tok and k["credits"]:
        lines += [
            "",
            "  note: no response reported token counts, so the three token lines",
            "        above read `—` instead of 0. Qoder's own routes bill in credits",
            "        and answer every token field with a literal 0; a BYOK route is",
            "        the reverse. Both are stored as reported, never converted.",
        ]
    print("\n".join(lines))
    return 0


def cmd_tools(conn, args):
    rows = db.q_tools(conn, limit=_limit(args))
    cur = [[r["tool_name"], fmt_n(r["calls"]), fmt_n(r["completed"]), fmt_n(r["failed"]),
            fmt_n(r["pending"]), ms(r["avg_ms"]), ts(r["last_used"])] for r in rows]
    print(table(["tool", "calls", "ok", "failed", "pending", "avg", "last used"], cur,
                aligns=["<", ">", ">", ">", ">", ">", "<"]))
    return 0


def cmd_skills(conn, args):
    rows = db.q_skills(conn, limit=_limit(args))
    cur = [[r["skill"], fmt_n(r["calls"]), r["plugin"] or "-", ts(r["last_used"])]
           for r in rows]
    print(table(["skill", "calls", "plugin", "last used"], cur,
                aligns=["<", ">", "<", "<"]))
    return 0


def cmd_agents(conn, args):
    rows = db.q_agents(conn, limit=_limit(args))
    cur = [[r["agent_type"], fmt_n(r["calls"]), fmt_n(r["completed"]), ts(r["last_used"])]
           for r in rows]
    print(table(["agent type", "calls", "ok", "last used"], cur,
                aligns=["<", ">", ">", "<"]))
    return 0


def cmd_mcp(conn, args):
    rows = db.q_mcp(conn, limit=_limit(args))
    if not rows:
        print("no MCP servers configured or used yet")
        return 0
    cur = [[(r["server"] or "—"), (r["tool"] or "—"), fmt_n(r["calls"]),
            ms(r["avg_ms"]), ts(r["last_used"])] for r in rows]
    print(table(["server", "tool", "calls", "avg", "last used"], cur,
                aligns=["<", "<", ">", ">", "<"]))
    return 0


def cmd_models(conn, args):
    rows = db.q_models(conn)
    if not rows:
        print("no model responses indexed yet — run `qcut sync` first")
        return 0
    cur = []
    for r in rows:
        rep = bool(r["tokens_reported"])
        tok = r["input_tokens"] or 0
        api_total = tok + (r["output_tokens"] or 0)
        cur.append([
            r["model"], r["provider"] or "-", fmt_n(r["requests"]),
            fmt_tok(r["input_tokens"], rep), fmt_tok(r["output_tokens"], rep),
            fmt_tok(api_total, rep),
            fmt_tok(r["cache_read_tokens"], rep),
            fmt_credit_total(r["credits"], r["credits_carried"]),
            "yes" if rep else "no",
        ])
    print(table(["model", "provider", "requests", "input", "output", "api total",
                 "cache read", "credits", "tokens?"], cur,
                aligns=["<", "<", ">", ">", ">", ">", ">", ">", "<"]))
    print()
    print("  api total = input + output (input already contains cache read on this shape)")
    print("  tokens? = the route reported a non-zero token count for at least one response")
    return 0


def cmd_plugins(conn, args):
    rows = db.q_plugins(conn)
    cur = [[r["plugin"], r["version"] or "-", r["source"] or "-", fmt_n(r["uses"]),
            r["kinds"] or "-", ts(r["last_used"])] for r in rows]
    print(table(["plugin", "version", "source", "uses", "kinds", "last used"], cur,
                aligns=["<", "<", "<", ">", "<", "<"]))
    return 0


# Column names are SQL identifiers; these are the words a reader expects.
# `avg_ms` rendered as "583.18s" (a `_ms` suffix next to a seconds value) is the
# kind of mismatch that makes a number look wrong when it is merely mislabelled.
_SHOW_LABELS = {
    "avg_ms": "avg", "duration_ms": "duration", "first_used": "first used",
    "last_used": "last used", "is_sidechain": "sidechain", "admin_op": "op",
}


def _fmt_field(key, value, row=None):
    """Render one summary/history value the way the tab would.

    A raw epoch-millisecond integer is not a reading anyone can use, so the two
    timestamp columns and the two duration columns are formatted here rather
    than printed verbatim. A token measure whose row or window reports no token
    at all reads `—`, the same mark the TUI uses.
    """
    if value is None:
        return "-"
    if key in ("ts", "first_used", "last_used", "started_at", "ended_at"):
        return ts(value)
    if key in ("ms", "avg_ms", "duration_ms"):
        return ms(value)
    if key == "credits":
        if row is not None and "credits_carried" in row.keys():
            return fmt_credit_total(value, row["credits_carried"])
        return fmt_credits(value)
    if row is not None and key in db.TOKEN_COLUMNS:
        keys = row.keys()
        # A summary carries the `tokens_reported` count; a history row does not,
        # so it falls back to the same field-by-field test the SQL applies.
        reported = (bool(row["tokens_reported"]) if "tokens_reported" in keys
                    else db.row_reports_tokens(row))
        return fmt_tok(value, reported)
    return fmt_n(value) if isinstance(value, int) else value


def cmd_show(conn, args):
    kind, name = args.kind, args.name
    start, end = _window_from_args(args)
    if not db.q_entity_exists(conn, kind, name):
        # The aggregate below answers `COUNT(*) = 0` for a name the index has never
        # seen, which prints as a confident report about an entity that does not
        # exist. Asking the index without a window is what tells "no such thing"
        # apart from "nothing in this window" — the second one is a real zero.
        print(f"no such {kind}: {name}", file=sys.stderr)
        return 1
    summary = db.q_entity_summary(conn, kind, name, start, end)
    print(f"{kind}: {name}")
    for key in summary.keys():
        print(f"  {_SHOW_LABELS.get(key, key):<18} "
              f"{_fmt_field(key, summary[key], summary)}")
    print()
    rows = db.q_history(conn, kind, name, limit=_limit(args, 20), start_ts=start, end_ts=end)
    if not rows:
        print("  (no calls in this window)")
    for row in rows:
        print("  " + "  ".join(
            f"{_SHOW_LABELS.get(k, k)}={_fmt_field(k, row[k], row)}"
            for k in row.keys()))
    return 0


def cmd_recent(conn, args):
    rows = db.q_recent(conn, limit=_limit(args, 30))
    cur = [[ts(r["ts"]), r["tool_name"], r["category"], r["status"] or "pending",
            ms(r["duration_ms"]), r["project"] or "-"] for r in rows]
    print(table(["time", "tool", "category", "status", "duration", "project"], cur))
    return 0


def cmd_inventory(conn, args):
    rows = db.q_inventory(conn, args.kind)
    used = {}
    for kind, query in (("tool", db.q_tools), ("skill", db.q_skills),
                        ("agent", db.q_agents), ("plugin", db.q_plugins)):
        for r in query(conn):
            key = r[0]
            used[(kind, key)] = r["calls"] if "calls" in r.keys() else r["uses"]
    mcp_used = {}
    for r in db.q_mcp(conn):
        mcp_used[(r["server"], r["tool"])] = r["calls"]
    cur = []
    for r in rows:
        kind, name = r["kind"], r["name"]
        if kind == "mcp":
            count = mcp_used.get((name, None), 0)
        else:
            count = used.get((kind, name), 0)
        cur.append([kind, name, r["owner_plugin"] or "-", r["version"] or "-",
                    "used" if count else "unused", fmt_n(count)])
    if not cur:
        # An empty *filter* is not an empty inventory: `--kind mcp` returns
        # nothing on a machine whose mcp.json has no servers, and saying "the
        # inventory is empty" there sends the reader after the wrong thing.
        if args.kind:
            print(f"no {args.kind} entries in the inventory")
            if args.kind == "mcp":
                print(f"  {db.QODER_DIR / 'mcp.json'} defines no servers")
        else:
            print("inventory is empty — run `qcut sync`")
        return 0
    print(table(["kind", "name", "owner", "version", "state", "uses"], cur,
                aligns=["<", "<", "<", "<", "<", ">"]))
    return 0


def cmd_export(conn, args):
    out = {}
    for table_name in ("sessions", "model_calls", "tool_calls", "skill_usage",
                       "agent_usage", "mcp_usage", "plugin_usage", "commands",
                       "inventory", "unparsed"):
        # Empty rather than an error on a database sync has not built yet, so a
        # backup script can dump a fresh install without special-casing it.
        rows = db.q_usage_safe(conn, f"SELECT * FROM {table_name}")
        out[table_name] = [dict(r) for r in rows]
    text = json.dumps(out, indent=2, ensure_ascii=False, default=str)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"wrote {args.out}")
    else:
        print(text)
    return 0


def cmd_health(conn, args):
    path = db.db_file(conn)
    missing = path is None or not path.exists()
    print(f"database: {path or getattr(args, 'db', '(in memory)')}"
          + (" (not created yet — run `qcut sync`)" if missing else ""))
    print(f"schema:   v{db.stored_version(conn)} (tool expects v{db.SCHEMA_VERSION})")
    if missing:
        print()
        print("the index does not exist yet; `qcut sync` builds it from")
        print(f"{db.QODER_DIR} — nothing else here is meaningful until then.")
        return 0
    print(f"source:   {db.QODER_DIR}"
          f" {'(exists)' if db.QODER_DIR.exists() else '(MISSING)'}")
    print(f"config:   {db.CONFIG_PATH}"
          f" {'(read)' if db.CONFIG_PATH.is_file() else '(not present)'}")
    try:
        cfg = db.load_config()
    except db.ConfigError as exc:
        # A file the owner just edited that is not being read is the worst
        # failure this surface can have, and a traceback names no way to fix it.
        print(f"config is not usable: {exc}", file=sys.stderr)
        return 1
    for key in sorted(cfg):
        print(f"  setting {key} = {cfg[key]}")
    print()
    counts = db.overview(conn)
    width = max(len(k) for k in counts)
    for key, value in counts.items():
        print(f"  {key:<{width}} {fmt_n(value) if value is not None else 'n/a'}")
    print()
    unparsed = db.q_unparsed(conn)
    if unparsed:
        print("unclaimed records (a jump here means Qoder's format moved):")
        for row in unparsed:
            print(f"  {row['reason']:<28} {fmt_n(row['count'])}")
    else:
        print("unclaimed records: none")
    return 0


def cmd_backup(args) -> int:
    path = Path(args.db)
    if not path.is_file():
        # Opening it would create an empty index and then faithfully snapshot
        # the emptiness — a safety copy of nothing, printed as a success.
        print(f"no database at {path} — run `qcut sync` first", file=sys.stderr)
        return 1
    conn = db.open_db(path)
    try:
        db.ensure_schema(conn)
        # Retention runs inside `snapshot`, so it also runs for `sync --full` and
        # `restore`. The names are captured here only to say which files went.
        before = {p.name for p in db.list_backups(path)}
        dest = db.snapshot(conn, "manual", path)
    finally:
        conn.close()
    print(f"wrote {dest}")
    for name in sorted(before - {p.name for p in db.list_backups(path)}):
        print(f"pruned {dest.parent / name}")
    return 0


def cmd_restore(args) -> int:
    if not args.name:
        backups = db.list_backups(args.db)
        if not backups:
            print("no snapshots — nothing to restore")
            return 0
        for path in backups:
            print(path)
        print("\nrestore one with: qcut restore <name>")
        return 0
    target = Path(args.name)
    if not target.is_file():
        candidates = [p for p in db.list_backups(args.db) if p.name == args.name]
        if not candidates:
            print(f"no such snapshot: {args.name}", file=sys.stderr)
            return 1
        target = candidates[0]
    db.restore(target, args.db)
    print(f"restored {target}")
    return 0


def cmd_format(args) -> int:
    sync = db.load_sync()
    body = sync.format_doc()
    if not args.write:
        print(body)
        return 0
    doc = Path(args.out) if args.out else (
        Path(__file__).resolve().parent.parent
        / "docs" / "maintenance" / "qoder-format.md")
    start, end = "<!-- BEGIN GENERATED -->", "<!-- END GENERATED -->"
    block = f"{start}\n{body}{end}\n"
    if doc.is_file():
        text = doc.read_text(encoding="utf-8")
        absent = [marker for marker in (start, end) if marker not in text]
        if absent:
            # Appending would leave the document holding two copies of the same
            # surface, one of them outside any marker and never replaced again —
            # and a reader cannot tell a stale block apart from a current one.
            print(f"{doc} is missing {', '.join(absent)}, so the generated block "
                  f"has nowhere to go. Refusing to append a second copy: put the "
                  f"marker back, or write a new file with --out.", file=sys.stderr)
            return 1
        head, _, rest = text.partition(start)
        _, _, tail = rest.partition(end)
        text = head + block + tail.lstrip("\n")
    else:
        text = block
    doc.parent.mkdir(parents=True, exist_ok=True)
    doc.write_text(text, encoding="utf-8")
    print(f"wrote {doc}")
    return 0


def _window_from_args(args):
    if getattr(args, "range", None):
        return db.window_bounds(args.range, int(datetime.now().timestamp() * 1000))
    return None, None


COMMANDS = {
    "stats": cmd_stats,
    "tools": cmd_tools,
    "skills": cmd_skills,
    "agents": cmd_agents,
    "plugins": cmd_plugins,
    "mcp": cmd_mcp,
    "models": cmd_models,
    "show": cmd_show,
    "recent": cmd_recent,
    "inventory": cmd_inventory,
    "export": cmd_export,
    "health": cmd_health,
}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="qcut", description="Qoder CN usage reports.")
    parser.add_argument("--db", default=str(db.DB_PATH))
    sub = parser.add_subparsers(dest="cmd")

    # A flag is registered only on the subcommand that reads it. `--limit` on a
    # report that ignores it is not a convenience, it is a filter that looks like
    # it worked: the table comes back whole and nothing says otherwise.
    for name in ("stats", "plugins", "models", "health"):
        sub.add_parser(name)
    for name in ("tools", "skills", "agents", "mcp", "recent"):
        sub.add_parser(name).add_argument("--limit", type=int, default=None)

    p = sub.add_parser("inventory")
    p.add_argument("--kind", default=None)

    p = sub.add_parser("export")
    p.add_argument("--out", default=None)

    p = sub.add_parser("show")
    p.add_argument("kind", choices=["tool", "skill", "agent", "model"])
    p.add_argument("name")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--range", default=None, choices=list(db.USAGE_RANGES))

    p = sub.add_parser("backup")
    p = sub.add_parser("restore")
    p.add_argument("name", nargs="?")
    p = sub.add_parser("format")
    p.add_argument("--write", action="store_true")
    p.add_argument("--out", default=None,
                   help="write the generated block into this file instead of "
                        "docs/maintenance/qoder-format.md")

    args = parser.parse_args(argv)
    if not args.cmd:
        args.cmd = "stats"

    if args.cmd in ("backup", "restore", "format"):
        return {"backup": cmd_backup, "restore": cmd_restore, "format": cmd_format}[args.cmd](args)

    conn = db.open_db(args.db, readonly=True)
    try:
        if db.stored_version(conn) < db.SCHEMA_VERSION and args.cmd != "health":
            print("index is empty or older than this tool — run `qcut sync` first",
                  file=sys.stderr)
            return 1
        return COMMANDS[args.cmd](conn, args)
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
