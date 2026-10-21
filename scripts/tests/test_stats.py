"""test_stats.py — the headless report CLI (``qcut stats …``), end to end.

``qcut-stats.py`` is what CI and a shell reach for when there is no terminal to
render the TUI into, so its output *is* its contract. The other suites already
hold the parts of it that live *inside* a command:

* ``test_usage.py::TokenReportingTest`` — the ``—`` / ``-`` / numeral rules in
  ``fmt_tok`` and ``fmt_credit_total``, and how ``stats`` / ``models`` / ``show``
  draw them. Not re-asserted here; the seed data uses one BYOK route so those
  lines render as plain numerals and cannot collide with what is checked below.
* ``test_usage.py::CliReportTest`` — the two inventory wordings and ``show``'s
  timestamp and empty-window notices.
* ``test_config.py::HealthSurfaceTest`` — that ``health`` prints every setting
  knob. Deliberately not repeated here.

What was unchecked was everything above the formatter: the argument parser, the
``--db`` flag, the read-only connection, the dispatch table, the stale-index
refusal, ``--limit``, ``export``/``inventory``/``recent``/``show`` output for the
kinds no other suite drives, and the ``backup``/``restore`` pair. Those run as
shipped here — every case calls the real ``stats.main([...])`` with both streams
captured — so a renamed subcommand, a dropped column or a message that stops
naming the fix fails in this file instead of in someone's terminal.

Every database is built in a temp directory by :func:`seed_db` and addressed with
``--db``; ``TempTree`` additionally pins ``DB_PATH``, ``QODER_DIR`` and
``CONFIG_PATH``, so even a forgotten ``--db`` stays inside the temp tree. The one
case that shells out to ``bin/qcut`` pins ``QCUT_SCRIPTS``, ``QCUT_DB``,
``QCUT_QODER_DIR`` and ``QCUT_CONFIG``, and asserts every path the launcher
printed is inside the temp directory. Standard library only — no ``textual``.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

# `_helpers` puts scripts/ on sys.path, so it must be imported before qcut_db.
from _helpers import SCRIPTS, TempTree, no_settings_env

import qcut_db as db
import _hermetic  # noqa: E402,F401 -- tripwire: the suite must never open the real database

_spec = importlib.util.spec_from_file_location(
    "qcut_stats_under_test", str(SCRIPTS / "qcut-stats.py"))
stats = importlib.util.module_from_spec(_spec)
sys.modules["qcut_stats_under_test"] = stats
_spec.loader.exec_module(stats)

REPO = SCRIPTS.parent
QCUT = REPO / "bin" / "qcut"

# One fixed instant so `ts()` renders a known string, and so `recent`'s order is
# decided by the data rather than by whatever "now" happens to be. The seed
# spreads its rows a minute apart (`m()`) because `ts()` only renders to the
# minute: timestamps closer together than that print identically, which would let
# a row-order assertion pass on any order at all.
TS = 1_790_000_000_000


def m(minutes):
    return TS + minutes * 60_000


# The tables `qcut export` dumps — the names a backup script reads.
EXPORTED = ("sessions", "model_calls", "tool_calls", "skill_usage", "agent_usage",
            "mcp_usage", "plugin_usage", "commands", "inventory", "unparsed")

# Every subcommand that only reads, as the argv tail it is typed with, so one
# list drives both the refusal and the "a report never writes" cases.
READ_ONLY_COMMANDS = (("stats",), ("tools",), ("skills",), ("agents",), ("plugins",),
                      ("mcp",), ("models",), ("commands",), ("recent",),
                      ("inventory",), ("export",), ("env",),
                      ("show", "tool", "Bash"))


def run(*argv):
    """Invoke the CLI exactly as a shell would, capturing both streams."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = stats.main([str(a) for a in argv])
    return rc, out.getvalue(), err.getvalue()


def body(out):
    """The data rows of a rendered table, without its header and separator.

    Anything printed after the table's first blank line -- `models` puts two
    explanatory footnotes there -- is not a row, so it is dropped first.
    """
    table = out.split("\n\n")[0]
    lines = table.splitlines()
    return [line for line in lines[2:] if line.strip()]


def cells(line, upto=None):
    """The leading cells of a table row.

    Padded cells never contain a space of their own, so a plain split recovers
    them; `upto` stops before a column whose value does (a rendered timestamp, a
    two-word provider).
    """
    parts = line.split()
    return parts[:upto] if upto else parts


def stamp(ms):
    return datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d %H:%M")


def seed_db(path):
    """A database with one distinctive row in every table the reports read.

    Deliberately not minimal: each report below is asserted on a value that only
    exists if *its own* table was queried and its own columns survived, so a
    command that silently reads the wrong table fails here instead of printing an
    empty-but-plausible header. The durations are chosen so the ms/s boundary in
    `ms()` is visible in one table (500 ms, 1.20 s and 2.10 s averages).
    """
    conn = db.open_db(path)
    db.ensure_schema(conn)
    conn.execute(
        "INSERT INTO sessions(session_id, project, cwd, git_branch, version,"
        " entrypoint, started_at, ended_at, model)"
        " VALUES('s1','/work/demo','/work/demo','main','1.1.57','cli',?,?, 'qfmodel')",
        (m(0), m(3)))
    # One BYOK response: real tokens, no `credits` field. Token *rendering* is
    # test_usage's ground; this row exists so the counts have something to count.
    conn.execute(
        "INSERT INTO model_calls(message_id, session_id, project, ts, model,"
        " provider, input_tokens, output_tokens, cache_read_tokens,"
        " cache_creation_tokens, credits, has_usage)"
        " VALUES('m1','s1','/work/demo',?,'ark-code-latest','Volcano ARK',"
        " 100, 10, 80, 0, NULL, 1)", (m(0),))
    # Bash twice (one completed, one error), Read once, and Glob still pending
    # with no duration at all — the four states a status column can be in. The
    # timestamps are minutes apart, and Glob is the newest of them.
    conn.executemany(
        "INSERT INTO tool_calls(call_id, session_id, project, tool_name, category,"
        " ts, duration_ms, status) VALUES(?,?,?,?,?,?,?,?)",
        [("c1", "s1", "/work/demo", "Bash", "builtin", m(2), 1_200, "completed"),
         ("c2", "s1", "/work/demo", "Bash", "builtin", m(0), 3_000, "error"),
         ("c3", "s1", "/work/demo", "Read", "builtin", m(1), 500, "completed"),
         ("c4", "s1", "/work/demo", "Glob", "builtin", m(3), None, None)])
    conn.executemany(
        "INSERT INTO skill_usage(call_id, skill, plugin, session_id, project, ts,"
        " status, duration_ms) VALUES(?,?,?,?,?,?,?,?)",
        [("k1", "playwright-cli", None, "s1", "/work/demo", m(2), "completed", 100),
         ("k2", "demo-skill", "demo-plugin", "s1", "/work/demo", m(1), "completed", 60)])
    conn.execute(
        "INSERT INTO agent_usage(call_id, agent_type, session_id, project, ts,"
        " status, duration_ms) VALUES('a1','Explore','s1','/work/demo',?,"
        " 'completed', 5000)", (m(3),))
    conn.execute(
        "INSERT INTO mcp_usage(call_id, server, tool, admin_op, session_id,"
        " project, ts, status, duration_ms)"
        " VALUES('mc1','node-repl','node_repl','call','s1','/work/demo',?,"
        " 'completed',40)", (m(4),))
    conn.execute(
        "INSERT INTO plugin_usage(plugin, marketplace, kind, target, session_id,"
        " project, ts) VALUES('demo-plugin','mp','skill','demo-skill','s1',"
        " '/work/demo',?)", (m(1),))
    conn.executemany(
        "INSERT INTO commands(command, session_id, project, ts) VALUES(?,?,?,?)",
        [("compact", "s1", "/work/demo", m(0)),
         ("review", "s1", "/work/demo", m(1))])
    conn.executemany(
        "INSERT INTO inventory(kind, name, owner_plugin, version, path, source)"
        " VALUES(?,?,?,?,?,?)",
        [("tool", "Bash", None, None, None, "builtin"),
         ("tool", "Grep", None, None, None, "builtin"),
         ("tool", "never-used", None, None, None, "builtin"),
         ("skill", "demo-skill", "demo-plugin", "1.2.0", "/p/skills/demo", "plugin"),
         ("plugin", "demo-plugin", None, "1.2.0", "/p", "plugin"),
         ("agent", "Explore", None, None, None, "builtin"),
         ("mcp", "node-repl", None, None, None, "user"),
         ("mcp", "idle-server", None, None, None, "user"),
         ("command", "compact", None, None, None, "builtin")])
    conn.execute("INSERT INTO unparsed(reason, count) VALUES('type:NewThing', 3)")
    conn.execute("INSERT INTO sync_state(file_path, size, mtime, offset)"
                 " VALUES('/work/demo/s1.jsonl', 1, 1.0, 1)")
    conn.commit()
    conn.close()


class StatsReportTest(unittest.TestCase):
    """The overview and the per-category tables, through the real entry point."""

    def setUp(self):
        self.t = TempTree(self)
        seed_db(self.t.db_path)

    def test_stats_prints_the_indexed_counts_through_the_real_entry_point(self):
        rc, out, err = run("--db", self.t.db_path, "stats")
        self.assertEqual(rc, 0, err)
        self.assertIn("Qoder CN usage — all time", out)
        self.assertIn("sessions indexed     1", out)
        self.assertIn("projects             1", out)
        # Four tool calls, one of them an error and one with no result at all.
        self.assertIn("tool calls           4  (1 failed, 1 pending)", out)
        # Five tools listed, two of which only the inventory knows about: the
        # panel is `used | installed`, not `used`.
        self.assertIn("distinct tools       5", out)
        self.assertIn("skill invocations    2  (2 distinct)", out)
        self.assertIn("subagent calls       1  (1 types)", out)
        self.assertIn("mcp calls            1  (1 servers)", out)
        self.assertIn("slash commands       2  (2 distinct)", out)
        # The seed's one response reports tokens, so the token lines are real
        # measurements and the coverage line says the sums describe all of it.
        self.assertIn("token coverage       100.0% (1/1)", out)
        self.assertIn("credits-only         0", out)
        self.assertIn("unmeasured           0", out)

    def test_the_tools_table_scales_a_duration_to_seconds(self):
        rc, out, err = run("--db", self.t.db_path, "tools")
        self.assertEqual(rc, 0, err)
        rows = {line.split()[0]: line for line in body(out)}
        self.assertEqual(set(rows), {"Bash", "Glob", "Read", "Grep", "never-used"},
                         "the tools table lost, gained or renamed a row")
        # 1200 ms and 3000 ms average out to 2.10 s; 500 ms stays in ms.
        self.assertEqual(cells(rows["Bash"], 6),
                         ["Bash", "2", "1", "1", "0", "2.10s"])
        self.assertEqual(cells(rows["Read"], 6),
                         ["Read", "1", "1", "0", "0", "500ms"])
        # A call with no result is counted as pending and has no duration to
        # average: `-`, never a `0ms` that reads as a measured zero.
        self.assertEqual(cells(rows["Glob"], 6), ["Glob", "1", "0", "0", "1", "-"])
        # An installed tool that was never called still lists, with nothing yet
        # to average or date.
        self.assertEqual(cells(rows["Grep"], 7),
                         ["Grep", "0", "0", "0", "0", "-", "-"])
        self.assertIn(stamp(m(2)), rows["Bash"])       # its newest call, c1
        self.assertIn(stamp(m(1)), rows["Read"])

    def test_the_skills_table_names_the_owning_plugin_or_marks_it_absent(self):
        rc, out, err = run("--db", self.t.db_path, "skills")
        self.assertEqual(rc, 0, err)
        rows = {line.split()[0]: line for line in body(out)}
        self.assertEqual(set(rows), {"demo-skill", "playwright-cli"})
        self.assertEqual(cells(rows["demo-skill"], 3),
                         ["demo-skill", "1", "demo-plugin"])
        # A bare skill has no owner to invent: the cell reads `-`.
        self.assertEqual(cells(rows["playwright-cli"], 3),
                         ["playwright-cli", "1", "-"])
        self.assertIn(stamp(m(1)), rows["demo-skill"])

    def test_the_agents_table_lists_a_type_with_its_calls(self):
        rc, out, err = run("--db", self.t.db_path, "agents")
        self.assertEqual(rc, 0, err)
        rows = {line.split()[0]: line for line in body(out)}
        self.assertEqual(set(rows), {"Explore"},
                         "agents must list used types plus the inventory's")
        self.assertEqual(cells(rows["Explore"], 3), ["Explore", "1", "1"])
        self.assertIn(stamp(m(3)), rows["Explore"])

    def test_the_mcp_table_splits_the_server_from_the_tool(self):
        rc, out, err = run("--db", self.t.db_path, "mcp")
        self.assertEqual(rc, 0, err)
        rows = {line.split()[0]: line for line in body(out)}
        self.assertEqual(set(rows), {"node-repl", "idle-server"})
        self.assertEqual(cells(rows["node-repl"], 4),
                         ["node-repl", "node_repl", "1", "40ms"])
        # A configured server with no calls yet is still a row — with no tool to
        # name, so the cell reads the em dash rather than a blank that would make
        # the row look like the same server again.
        self.assertEqual(cells(rows["idle-server"], 5),
                         ["idle-server", "—", "0", "-", "-"])
        self.assertIn(stamp(m(4)), rows["node-repl"])

    def test_the_commands_table_ranks_and_limits_slash_commands(self):
        rc, out, err = run("--db", self.t.db_path, "commands")
        self.assertEqual(rc, 0, err)
        rows = {line.split()[0]: line for line in body(out)}
        self.assertEqual(set(rows), {"compact", "review"})
        self.assertEqual(cells(rows["review"], 3),
                         ["review", "1", "1"])
        self.assertIn(stamp(m(1)), rows["review"])
        rc, out, err = run("--db", self.t.db_path, "commands", "--limit", "1")
        self.assertEqual(rc, 0, err)
        self.assertEqual([line.split()[0] for line in body(out)], ["compact"])

    def test_the_plugins_table_carries_the_version_and_the_kind(self):
        rc, out, err = run("--db", self.t.db_path, "plugins")
        self.assertEqual(rc, 0, err)
        rows = body(out)
        self.assertEqual(len(rows), 1, out)
        self.assertEqual(cells(rows[0], 5),
                         ["demo-plugin", "1.2.0", "plugin", "1", "skill"])
        self.assertIn(stamp(m(1)), rows[0])

    def test_the_models_table_explains_its_two_derived_columns(self):
        rc, out, err = run("--db", self.t.db_path, "models")
        self.assertEqual(rc, 0, err)
        rows = body(out)
        self.assertEqual(len(rows), 1, out)
        self.assertIn("ark-code-latest", rows[0])
        self.assertIn("Volcano ARK", rows[0])
        self.assertIn("100.0% (1/1)", rows[0])
        # `api total` is input + output, and the footnote is what tells a reader
        # why the cache read is beside it and not added into it.
        self.assertRegex(out, r"api total = input \+ output")
        self.assertRegex(out, r"coverage = requests that reported any non-zero token")

    def test_a_limit_caps_a_report_at_the_largest_rows(self):
        # `ORDER BY calls DESC, tool_name` — so capping keeps Bash and Glob (a
        # one-call tie broken by name) and cuts Read and the two unused tools.
        rc, out, err = run("--db", self.t.db_path, "tools", "--limit", "2")
        self.assertEqual(rc, 0, err)
        names = [line.split()[0] for line in body(out)]
        self.assertEqual(names, ["Bash", "Glob"], out)
        rc, all_out, err = run("--db", self.t.db_path, "tools")
        self.assertEqual(rc, 0, err)
        self.assertGreater(len(body(all_out)), len(names), "--limit did nothing at all")

    def test_show_prints_one_entity_and_the_history_under_it(self):
        rc, out, err = run("--db", self.t.db_path, "show", "tool", "Bash")
        self.assertEqual(rc, 0, err)
        self.assertIn("tool: Bash", out)
        # The summary half and the history half both come from this tool's rows:
        # two calls, one of them a 3000 ms error, spanning two separate minutes
        # so `first used` and `last used` cannot be the same value by accident.
        self.assertRegex(out, r"calls\s+2")
        self.assertRegex(out, r"avg\s+2\.10s")
        self.assertIn(stamp(m(0)), out)
        self.assertIn(stamp(m(2)), out)
        self.assertEqual(out.count("name=Bash"), 2, out)
        self.assertIn("status=error", out)
        self.assertIn("duration=3.00s", out)
        self.assertIn("project=/work/demo", out)
        self.assertNotIn("name=Read", out, "the history showed another tool")

    def test_recent_lists_the_newest_call_first(self):
        rc, out, err = run("--db", self.t.db_path, "recent")
        self.assertEqual(rc, 0, err)
        rows = body(out)
        self.assertEqual(len(rows), 4, out)
        # c4 (Glob, m(3)) is the newest timestamp in the seed and c2 (the Bash
        # error, m(0)) the oldest; each stamp is a distinct minute. The time cell
        # is "YYYY-MM-DD HH:MM", so the tool name is the third whitespace token.
        self.assertEqual([line.split()[2] for line in rows],
                         ["Glob", "Bash", "Read", "Bash"], out)
        self.assertTrue(rows[0].lstrip().startswith(stamp(m(3))), rows[0])
        self.assertTrue(rows[-1].lstrip().startswith(stamp(m(0))), rows[-1])
        # A call with no result renders its status as the word, not a blank.
        self.assertIn("pending", rows[0])

    def test_inventory_separates_what_is_used_from_what_is_installed(self):
        rc, out, err = run("--db", self.t.db_path, "inventory")
        self.assertEqual(rc, 0, err)
        rows = {line.split()[1]: line for line in body(out)}
        self.assertEqual(
            set(rows), {"Bash", "Grep", "never-used", "demo-skill", "demo-plugin",
                        "Explore", "node-repl", "idle-server", "compact"},
            "the inventory listing is the whole inventory, keyed by name")
        self.assertEqual(cells(rows["Bash"], 6),
                         ["tool", "Bash", "-", "-", "used", "2"])
        self.assertEqual(cells(rows["never-used"], 6),
                         ["tool", "never-used", "-", "-", "unused", "0"])
        self.assertEqual(cells(rows["demo-skill"], 3),
                         ["skill", "demo-skill", "demo-plugin"])
        # Inventory names MCP *servers*; usage rows are (server, tool). The use
        # count must sum the server's tools rather than look for (name, None).
        self.assertEqual(cells(rows["node-repl"], 6),
                         ["mcp", "node-repl", "-", "-", "used", "1"])
        self.assertEqual(cells(rows["idle-server"], 6),
                         ["mcp", "idle-server", "-", "-", "unused", "0"])
        # Commands are matched to the slash-command table, not left unused.
        self.assertEqual(cells(rows["compact"], 6),
                         ["command", "compact", "-", "-", "used", "1"])

        rc, out, err = run("--db", self.t.db_path, "inventory", "--kind", "plugin")
        self.assertEqual(rc, 0, err)
        self.assertEqual([line.split()[1] for line in body(out)], ["demo-plugin"])
        self.assertNotIn("Bash", out, "--kind plugin must filter the tools out")

    def test_export_dumps_every_indexed_table_as_json(self):
        rc, out, err = run("--db", self.t.db_path, "export")
        self.assertEqual(rc, 0, err)
        data = json.loads(out)          # a traceback would not parse
        self.assertEqual(set(data), set(EXPORTED),
                         "export gained or dropped a table")
        self.assertEqual(len(data["tool_calls"]), 4)
        self.assertEqual([r["tool_name"] for r in data["tool_calls"]].count("Bash"), 2)
        self.assertEqual(data["unparsed"], [{"reason": "type:NewThing", "count": 3}])
        self.assertEqual(len(data["inventory"]), 9)
        self.assertEqual(data["sessions"][0]["session_id"], "s1")

    def test_export_can_write_the_dump_to_a_named_file(self):
        dest = self.t.root / "dump.json"
        rc, out, err = run("--db", self.t.db_path, "export", "--out", dest)
        self.assertEqual(rc, 0, err)
        self.assertIn(f"wrote {dest}", out)
        self.assertEqual(set(json.loads(dest.read_text(encoding="utf-8"))),
                         set(EXPORTED))

    def test_no_subcommand_is_the_overview(self):
        """A bare `qcut --db …` must answer with the report, not with usage."""
        rc, out, err = run("--db", self.t.db_path)
        self.assertEqual(rc, 0, err)
        self.assertIn("Qoder CN usage — all time", out)


class EmptyReportTest(unittest.TestCase):
    """A report with nothing behind it has to say what to do about it."""

    def setUp(self):
        self.t = TempTree(self)
        conn = db.open_db(self.t.db_path)     # schema, zero rows
        db.ensure_schema(conn)
        conn.close()

    def test_a_report_with_nothing_in_it_names_the_fix(self):
        for cmd, notice in (("mcp", "no MCP servers configured or used yet"),
                            ("models", "no model responses indexed yet")):
            rc, out, err = run("--db", self.t.db_path, cmd)
            self.assertEqual(rc, 0, err)
            self.assertIn(notice, out, f"`qcut {cmd}` answered with a bare header")

    def test_the_span_line_has_an_absent_state_of_its_own(self):
        """No index means no span, which is not the same as a span of one instant.

        `X .. X` would read as "everything happened at one moment"; the absence of
        both endpoints is `-`, the state this tool already uses for "nothing here".
        """
        rc, out, err = run("--db", self.t.db_path, "stats")
        self.assertEqual(rc, 0, err)
        line = [l for l in out.splitlines() if l.strip().startswith("range")][0]
        self.assertEqual(" ".join(line.split()), "range -", out)


class CliGuardTest(unittest.TestCase):
    """The refusal paths: the parser, the stale index, and staying read-only."""

    def setUp(self):
        self.t = TempTree(self)
        seed_db(self.t.db_path)

    def test_an_unknown_subcommand_is_refused_by_the_parser(self):
        with self.assertRaises(SystemExit) as caught:
            run("--db", self.t.db_path, "definitely-not-a-command")
        self.assertEqual(caught.exception.code, 2)

    def test_an_unknown_window_is_refused_by_the_parser(self):
        """`--range` is a closed set: a typo must not quietly mean "all time"."""
        for argv in (("show", "tool", "Bash", "--range", "5hours"),
                     ("show", "tool", "Bash", "--range", "yesterday")):
            with self.subTest(argv=argv), self.assertRaises(SystemExit) as caught:
                run("--db", self.t.db_path, *argv)
            self.assertEqual(caught.exception.code, 2)

    def test_a_flag_a_report_would_ignore_is_not_accepted(self):
        """Only the commands that read a flag may be given one.

        `--limit` used to be registered on all eleven subcommands and read by six,
        and `--range` on all eleven and read by one — so `qcut stats --limit 5`
        printed every row and `qcut models --range 7d` printed all time. Refusing
        both is the honest surface, and it is what `cbut`'s parser already does.
        """
        for cmd in ("stats", "plugins", "models", "health", "env", "inventory",
                    "export"):
            with self.subTest(cmd=cmd):
                with self.assertRaises(SystemExit) as caught:
                    run("--db", self.t.db_path, cmd, "--limit", "5")
                self.assertEqual(caught.exception.code, 2)
        # `30d` is a *valid* window here on purpose: if the parser were still
        # accepting `--range` the call would run, so the only way this can fail is
        # the flag being absent — not a typo the closed set would have caught anyway.
        for cmd in ("stats", "tools", "models", "recent"):
            with self.subTest(range=cmd):
                with self.assertRaises(SystemExit) as caught:
                    run("--db", self.t.db_path, cmd, "--range", "30d")
                self.assertEqual(caught.exception.code, 2)
        # Positive control: the flags that do work still do, or this case would be
        # satisfied by a parser that rejects everything.
        rc, out, err = run("--db", self.t.db_path, "tools", "--limit", "1")
        self.assertEqual(rc, 0, err)
        self.assertEqual(len(body(out)), 1)
        rc, out, err = run("--db", self.t.db_path, "show", "tool", "Bash",
                           "--range", "30d")
        self.assertEqual(rc, 0, err)

    def test_stats_prints_the_span_the_index_actually_covers(self):
        """`all time` is only honest beside how far the index reaches.

        The seed's newest row is an MCP call four minutes after its newest model
        response, so a span read off the response table alone stops short and the
        report quietly disagrees with `qcut recent`.
        """
        rc, out, err = run("--db", self.t.db_path, "stats")
        self.assertEqual(rc, 0, err)
        line = [l for l in out.splitlines() if l.strip().startswith("range")][0]
        self.assertIn(stamp(m(0)), line, out)
        self.assertIn(stamp(m(4)), line,
                      f"the span is not the whole index: {line}")
        self.assertEqual(len(line.split("..")), 2,
                         f"the span is not a pair of endpoints: {line}")

    def test_show_refuses_a_name_the_index_has_never_seen(self):
        """A report about nothing must not be printed as a report.

        The summary is an aggregate, so an unknown name used to come back as
        `COUNT(*) = 0` and print a confident header plus a column of zeros —
        indistinguishable from an entity that exists and has been idle.
        """
        rc, out, err = run("--db", self.t.db_path, "show", "tool", "NoSuchTool")
        self.assertEqual(rc, 1, out)
        self.assertIn("no such tool: NoSuchTool", err)
        self.assertNotIn("tool:", out, f"the refusal still printed a report: {out}")
        self.assertNotIn("calls", out)

    def test_show_draws_a_real_zero_for_an_entity_idle_in_this_window(self):
        """The other half: known name, quiet window — a measurement, not a refusal."""
        rc, out, err = run("--db", self.t.db_path, "show", "tool", "Bash",
                           "--range", "1d")
        self.assertEqual(rc, 0, err)
        self.assertIn("tool: Bash", out)
        self.assertRegex(out, r"calls\s+0")
        self.assertIn("(no calls in this window)", out)

    def test_an_index_this_tool_has_not_built_is_refused_with_a_point(self):
        absent = self.t.root / "never-synced.db"
        rc, out, err = run("--db", absent, "stats")
        self.assertEqual(rc, 1)
        self.assertIn("index is empty or older than this tool", err)
        self.assertIn("qcut sync", err)
        self.assertNotIn("Traceback", err)
        # health is the documented exception — it explains an absent index.
        rc, out, err = run("--db", absent, "health")
        self.assertEqual(rc, 0, err)
        self.assertIn("not created yet", out)

    def test_reading_a_report_never_creates_or_changes_the_index(self):
        """Every read subcommand opens the file ``mode=ro``.

        The one writer this CLI is allowed to be is its own ``backup``/``restore``
        /``format --write``. A report that opened read-write would create a
        database where none exists (and a ``-wal`` beside a live one), which is
        how a status command ends up owning the state it claims to describe.
        """
        absent = self.t.root / "never-synced.db"
        for argv in READ_ONLY_COMMANDS:
            rc, out, err = run("--db", absent, *argv)
            with self.subTest(cmd=argv[0]):
                # `env` is the one report whose job is to explain an absent
                # index (it is the first command a new install needs), so it
                # answers rather than refusing — and still writes nothing.
                if argv[0] == "env":
                    self.assertEqual(rc, 0, err)
                    self.assertIn("no index yet", out)
                else:
                    self.assertEqual(rc, 1, err)
        self.assertFalse(absent.exists(), "a read report created the index")
        self.assertFalse((self.t.root / "backups").exists())
        self.assertFalse(Path(str(absent) + "-wal").exists())

        before = self.t.db_path.read_bytes()
        for argv in READ_ONLY_COMMANDS:
            run("--db", self.t.db_path, *argv)
        self.assertEqual(self.t.db_path.read_bytes(), before,
                         "a read report rewrote the index")


class HealthReportTest(unittest.TestCase):
    """``qcut health`` — the database, the source tree and the drift signal."""

    def setUp(self):
        self.t = TempTree(self)
        seed_db(self.t.db_path)

    def test_health_reports_the_index_and_the_paths_it_is_reading(self):
        rc, out, err = run("--db", self.t.db_path, "health")
        self.assertEqual(rc, 0, err)
        self.assertIn(f"database: {self.t.db_path}", out)
        self.assertIn(f"schema:   v{db.SCHEMA_VERSION}", out)
        # The two paths a reader needs to check are this suite's, never the
        # machine's: an assertion that describes the real home is an instrument
        # error, not a finding.
        self.assertIn(f"source:   {self.t.qoder} (exists)", out)
        self.assertIn(f"config:   {self.t.db_path.parent / 'config.toml'} (not present)", out)
        self.assertTrue(str(self.t.root) in out)
        # Row counts per table, from `overview()`.
        for key, value in (("tool_calls", 4), ("model_calls", 1), ("inventory", 9),
                           ("sync_state", 1), ("sessions", 1)):
            self.assertRegex(out, rf"{key}\s+{value}\b")

    def test_health_reports_the_token_flag_and_the_all_time_coverage(self):
        """The flag is read by the CLI, not by qcut — but health can report it.

        A reader whose coverage is low needs the reason on the same screen as the
        counts; sending them to `qcut env` from a report that already knows the
        answer would be a detour.
        """
        with mock.patch.dict(os.environ, {}):
            os.environ.pop(db.TOKEN_ENV_NAME, None)
            rc, out, err = run("--db", self.t.db_path, "health")
        self.assertEqual(rc, 0, err)
        self.assertIn(f"tokens:   {db.TOKEN_ENV_NAME} not set", out)
        self.assertRegex(
            out, r"coverage\s+100\.0% \(1/1\) token-covered · "
                 r"0 credits-only · 0 unmeasured")

        with mock.patch.dict(os.environ, {db.TOKEN_ENV_NAME: "1"}):
            rc, out, err = run("--db", self.t.db_path, "health")
        self.assertEqual(rc, 0, err)
        self.assertIn(f"tokens:   {db.TOKEN_ENV_NAME} set to 1", out)

    def test_health_surfaces_the_flag_even_when_the_index_does_not_exist(self):
        absent = self.t.root / "never-synced.db"
        with mock.patch.dict(os.environ, {db.TOKEN_ENV_NAME: "1"}):
            rc, out, err = run("--db", absent, "health")
        self.assertEqual(rc, 0, err)
        self.assertIn(f"tokens:   {db.TOKEN_ENV_NAME} set to 1", out)
        self.assertIn("not created yet", out)
        self.assertFalse(absent.exists())

    def test_a_broken_settings_file_is_reported_and_not_raised(self):
        """`qcut health` is the command you reach for when something is wrong.

        A malformed file raised `ConfigError` straight out of `load_config()` and
        the command died with a traceback -- which names the file, but only to
        someone who can read a stack. It answers with the same message, a non-zero
        rc, and no traceback now. The environment is hidden first: it outranks the
        file, so a case that did not would be asserting someone else's shell.
        """
        (self.t.root / "config.toml").write_text('name_cap = "abc"\n',
                                                 encoding="utf-8")
        with no_settings_env():
            rc, out, err = run("--db", self.t.db_path, "health")
        self.assertEqual(rc, 1, out)
        self.assertIn("config is not usable", err)
        self.assertIn("name_cap", err)
        self.assertIn("config.toml", err)
        self.assertNotIn("Traceback", err)

    def test_health_surfaces_an_unclaimed_record_type(self):
        """The whole point of the `unparsed` table is that health shows it."""
        rc, out, err = run("--db", self.t.db_path, "health")
        self.assertEqual(rc, 0, err)
        self.assertIn("unclaimed records", out)
        self.assertIn("type:NewThing", out)
        self.assertRegex(out, r"type:NewThing\s+3")

    def test_health_says_everything_was_claimed_when_nothing_was_not(self):
        conn = db.open_db(self.t.db_path)
        conn.execute("DELETE FROM unparsed")
        conn.commit()
        conn.close()
        rc, out, err = run("--db", self.t.db_path, "health")
        self.assertEqual(rc, 0, err)
        self.assertIn("unclaimed records: none", out)
        self.assertNotIn("type:NewThing", out)


class EnvReportTest(unittest.TestCase):
    """``qcut env`` — advice about the switch the *CLI* must see at startup."""

    def setUp(self):
        self.t = TempTree(self)
        seed_db(self.t.db_path)

    def test_unset_flag_names_the_export_and_the_index_coverage(self):
        with mock.patch.dict(os.environ, {}):
            os.environ.pop(db.TOKEN_ENV_NAME, None)
            rc, out, err = run("--db", self.t.db_path, "env")
        self.assertEqual(rc, 0, err)
        self.assertIn(f"{db.TOKEN_ENV_NAME}: not set", out)
        self.assertIn("100.0% (1/1)", out)
        self.assertIn(f"export {db.TOKEN_ENV_NAME}=1", out)
        self.assertIn("does not edit shell profiles", out)
        self.assertIn("cannot be recovered", out)

    def test_a_present_flag_is_reported_as_present(self):
        with mock.patch.dict(os.environ, {db.TOKEN_ENV_NAME: "1"}):
            rc, out, err = run("--db", self.t.db_path, "env")
        self.assertEqual(rc, 0, err)
        self.assertIn("set to 1", out)
        self.assertIn("The flag is present in this shell", out)
        self.assertNotIn(f"export {db.TOKEN_ENV_NAME}=1", out)

    def test_a_missing_index_is_explained_and_never_created(self):
        absent = self.t.root / "never-synced.db"
        with mock.patch.dict(os.environ, {}):
            os.environ.pop(db.TOKEN_ENV_NAME, None)
            rc, out, err = run("--db", absent, "env")
        self.assertEqual(rc, 0, err)
        self.assertIn("no index yet", out)
        self.assertIn("qcut sync", out)
        self.assertIn(f"export {db.TOKEN_ENV_NAME}=1", out)
        self.assertFalse(absent.exists())
        self.assertFalse((self.t.root / "backups").exists())


class MaintenanceCliTest(unittest.TestCase):
    """``qcut backup`` / ``qcut restore`` — the two subcommands that write."""

    def setUp(self):
        self.t = TempTree(self)
        seed_db(self.t.db_path)

    def test_backup_then_restore_round_trips_through_the_cli(self):
        rc, out, err = run("--db", self.t.db_path, "backup")
        self.assertEqual(rc, 0, err)
        self.assertIn("wrote ", out)
        made = db.list_backups(self.t.db_path)
        self.assertEqual(len(made), 1, f"backup did not leave one snapshot: {made}")
        self.assertTrue(str(made[0]).startswith(str(self.t.root)))

        rc, out, err = run("--db", self.t.db_path, "restore")
        self.assertEqual(rc, 0, err)
        self.assertIn("restore one with: qcut restore", out)
        name = Path(out.splitlines()[0]).name

        # Lose the index the way a bad restore or a stray `reset` would, then
        # come back through the CLI rather than the data layer.
        conn = db.open_db(self.t.db_path)
        conn.execute("DELETE FROM tool_calls")
        conn.execute("DELETE FROM model_calls")
        conn.commit()
        conn.close()

        rc, out, err = run("--db", self.t.db_path, "restore", name)
        self.assertEqual(rc, 0, err)
        self.assertIn("restored", out)
        conn = db.open_db(self.t.db_path, readonly=True)
        try:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM tool_calls").fetchone()[0], 4)
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM model_calls").fetchone()[0], 1)
        finally:
            conn.close()
        # The state the restore overwrote (the emptied tables) was kept too.
        self.assertTrue(any("pre-restore" in p.name for p in db.list_backups(
            self.t.db_path)), db.list_backups(self.t.db_path))

    def test_restore_accepts_a_snapshot_by_its_full_path_as_well_as_its_name(self):
        """``restore NAME`` resolves inside ``backups/``; a path is taken as-is.

        The path form is what recovers a snapshot someone moved, or one the tool
        did not make, so it must not be gated on the managed directory.
        """
        rc, out, err = run("--db", self.t.db_path, "backup")
        self.assertEqual(rc, 0, err)
        moved = self.t.root / "elsewhere.db"
        shutil.copy(db.list_backups(self.t.db_path)[0], moved)

        conn = db.open_db(self.t.db_path)
        conn.execute("DELETE FROM tool_calls")
        conn.commit()
        conn.close()

        rc, out, err = run("--db", self.t.db_path, "restore", str(moved))
        self.assertEqual(rc, 0, err)
        self.assertIn("restored", out)
        conn = db.open_db(self.t.db_path, readonly=True)
        try:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM tool_calls").fetchone()[0], 4)
        finally:
            conn.close()

    def test_restore_with_no_snapshot_says_there_is_nothing(self):
        rc, out, err = run("--db", self.t.db_path, "restore")
        self.assertEqual(rc, 0, err)
        self.assertIn("no snapshots", out)
        self.assertFalse(db.list_backups(self.t.db_path))
        self.assertTrue(self.t.db_path.exists(), "listing snapshots wrote to the index")

    def test_restore_refuses_a_name_it_cannot_find(self):
        rc, out, err = run("--db", self.t.db_path, "backup")
        self.assertEqual(rc, 0, err)
        rc, out, err = run("--db", self.t.db_path, "restore",
                           "usage-19990101-000000-manual.db")
        self.assertEqual(rc, 1)
        self.assertIn("no such snapshot", err)
        self.assertNotIn("Traceback", err)
        self.assertFalse(any("pre-restore" in p.name for p in db.list_backups(
            self.t.db_path)), "a refused restore still replaced the index")

    def test_backup_refuses_a_database_that_does_not_exist_yet(self):
        """A first run of `qcut backup` on a fresh machine has nothing to save.

        Opening the path creates the index, so the old flow wrote an empty schema
        into `backups/` and printed `wrote …` — a success line for a safety copy of
        nothing, and one fewer reason to run `qcut sync`.
        """
        missing = self.t.root / "fresh" / "usage.db"
        rc, out, err = run("--db", missing, "backup")
        self.assertEqual(rc, 1, out)
        self.assertIn("no database at", err)
        self.assertIn("qcut sync", err)
        self.assertFalse(missing.exists(),
                         "backup created the index it was refusing to back up")
        self.assertEqual(db.list_backups(missing), [],
                         "a refused backup left a snapshot behind")

    def test_a_backup_beyond_the_kept_five_prunes_the_oldest(self):
        """Retention is the CLI's job too: `backup` prunes after it writes."""
        for i in range(1, 8):                      # seven old snapshots, by name
            stale = db.backup_dir(self.t.db_path) / f"usage-202301{i:02d}-120000-manual.db"
            stale.parent.mkdir(parents=True, exist_ok=True)
            conn = db.open_db(stale)
            db.ensure_schema(conn)
            conn.close()
            moment = 1_700_000_000 + i * 10        # mtime agrees with the name
            os.utime(stale, (moment, moment))

        rc, out, err = run("--db", self.t.db_path, "backup")
        self.assertEqual(rc, 0, err)
        left = db.list_backups(self.t.db_path)
        self.assertEqual(len(left), db.BACKUP_KEEP, f"kept {len(left)}: {[p.name for p in left]}")
        pruned = [line.split()[-1] for line in out.splitlines() if line.startswith("pruned")]
        self.assertTrue(pruned, f"nothing was reported as pruned: {out}")
        for path in pruned:
            self.assertFalse(Path(path).exists(), f"reported {path} pruned but left it")
        self.assertTrue(all(p.name not in pruned for p in left))


class VacuumCliTest(unittest.TestCase):
    """``qcut vacuum`` — explicit-only compaction, snapshotted and verified."""

    def setUp(self):
        self.t = TempTree(self)
        seed_db(self.t.db_path)

    def test_vacuum_snapshots_first_and_keeps_every_row(self):
        conn = db.open_db(self.t.db_path)
        before = db.overview(conn)
        conn.close()

        rc, out, err = run("--db", self.t.db_path, "vacuum")
        self.assertEqual(rc, 0, err)
        self.assertIn("compacted", out)
        self.assertIn("verified", out)
        self.assertIn("quick_check ok", out)
        snaps = [p for p in db.list_backups(self.t.db_path)
                 if "pre-vacuum" in p.name]
        self.assertEqual(len(snaps), 1, f"no pre-vacuum snapshot: {snaps}")

        conn = db.open_db(self.t.db_path, readonly=True)
        try:
            self.assertEqual(db.overview(conn), before)
            self.assertEqual(
                conn.execute("PRAGMA quick_check").fetchone()[0], "ok")
        finally:
            conn.close()

    def test_vacuum_refuses_a_database_that_does_not_exist(self):
        missing = self.t.root / "fresh" / "usage.db"
        rc, out, err = run("--db", missing, "vacuum")
        self.assertEqual(rc, 1, out)
        self.assertIn("no database at", err)
        self.assertIn("qcut sync", err)
        self.assertFalse(missing.exists())
        self.assertEqual(db.list_backups(missing), [])


class LauncherMaintenanceTest(unittest.TestCase):
    """The launcher's route to the two subcommands that write on disk."""

    def setUp(self):
        self.t = TempTree(self)
        seed_db(self.t.db_path)
        # All four, because the launcher resolves each of them independently and
        # a leak in any one of them runs a real command against live state.
        self.env = dict(
            os.environ,
            QCUT_SCRIPTS=str(SCRIPTS),
            QCUT_DB=str(self.t.db_path),
            QCUT_QODER_DIR=str(self.t.qoder),
            QCUT_CONFIG=str(self.t.db_path.parent / "config.toml"),
            QCUT_VENV=sys.executable,
        )

    def qcut(self, *argv):
        return subprocess.run(["bash", str(QCUT), *argv], capture_output=True,
                              text=True, env=self.env, timeout=60)

    def test_the_maintenance_pair_reaches_the_cli_through_the_launcher(self):
        out = self.qcut("backup")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertNotIn("Traceback", out.stderr)
        written = out.stdout.strip().splitlines()[0].split()[-1]
        self.assertTrue(written.startswith(str(self.t.root)),
                        f"the launcher wrote outside the temp tree: {written}")
        made = Path(written)
        self.assertTrue(made.is_file())

        listed = self.qcut("restore")
        self.assertEqual(listed.returncode, 0, listed.stderr)
        self.assertIn(made.name, listed.stdout)

        restored = self.qcut("restore", made.name)
        self.assertEqual(restored.returncode, 0, restored.stderr)
        self.assertIn("restored", restored.stdout)
        conn = db.open_db(self.t.db_path, readonly=True)
        try:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM tool_calls").fetchone()[0], 4)
        finally:
            conn.close()

    def test_the_launcher_never_touches_a_path_it_was_not_given(self):
        """A read report through the launcher must not create the index either.

        The launcher passes ``--db`` through to argparse, whose default is the
        environment's ``DB_PATH``: forget one pin and this is where a real
        database gets opened — or built.
        """
        absent = self.t.root / "launcher-never-synced.db"
        out = self.qcut("--db", absent, "stats")
        self.assertEqual(out.returncode, 1, out.stdout)
        self.assertIn("index is empty or older than this tool", out.stderr)
        self.assertFalse(Path(absent).exists())
        self.assertFalse((self.t.root / "backups").exists())


class FormatCliTest(unittest.TestCase):
    """``qcut format`` — the generated format surface, and its one writer flag.

    ``--write`` rewrites a document *between markers*. It is exercised through
    ``--out`` into a temp file: the tracked document in this repository is the
    writer's production target, and a test that rewrote it would be a change to
    the product, not a check of it.
    """

    def setUp(self):
        self.t = TempTree(self)

    def doc(self, text):
        path = self.t.root / "format-target.md"
        path.write_text(text, encoding="utf-8")
        return path

    def test_format_prints_the_registry_and_needs_no_database(self):
        rc, out, err = run("format")           # no --db at all
        self.assertEqual(rc, 0, err)
        self.assertTrue(out.startswith("# Qoder CN format surface"), out[:80])
        for section in ("## Layout (relative to ~/.qoder-cn)", "## Record types",
                        "## Fields read", "## Classification"):
            self.assertIn(section, out, f"format lost its {section} section")
        # The paths the parser actually globs, so a registry edit is visible here.
        self.assertIn("projects", out)
        self.assertIn("mcp.json", out)
        self.assertIn("unparsed", out)
        self.assertFalse(db.list_backups(self.t.db_path),
                         "a read-only report left a snapshot behind")

    def test_write_splices_between_the_markers_and_keeps_the_prose(self):
        path = self.doc("<!-- BEGIN GENERATED -->\nold\n<!-- END GENERATED -->\n"
                        "## Hand-written section\n\nNot generated.\n")
        rc, out, err = run("format", "--write", "--out", path)
        self.assertEqual(rc, 0, err)
        text = path.read_text(encoding="utf-8")
        self.assertIn("# Qoder CN format surface", text)
        self.assertNotIn("old", text, "the generated block was not replaced")
        self.assertIn("## Hand-written section", text,
                      "the writer dropped what it did not generate")
        self.assertEqual(text.count("<!-- BEGIN GENERATED -->"), 1,
                         "a second copy of the generated block appeared")

    def test_write_refuses_a_document_that_lost_its_markers(self):
        """The failure the refusal exists for: appending buries a stale block.

        A document with no markers used to get the block tacked on the end, so it
        held two copies of the same surface and only one of them was ever
        regenerated again. Nothing may be written at all.
        """
        path = self.doc("# Somebody's notes\n\nNo markers here.\n")
        before = path.read_bytes()
        rc, out, err = run("format", "--write", "--out", path)
        self.assertEqual(rc, 1, out)
        self.assertIn(str(path), err)
        self.assertEqual(path.read_bytes(), before,
                         "the refusal still wrote to the document")

    def test_write_names_the_marker_that_is_missing(self):
        for half, expected in (
            ("<!-- BEGIN GENERATED -->\nbody\n", "<!-- END GENERATED -->"),
            ("prose\n<!-- END GENERATED -->\n", "<!-- BEGIN GENERATED -->"),
        ):
            with self.subTest(markers=half.strip()):
                path = self.doc(half)
                before = path.read_bytes()
                rc, out, err = run("format", "--write", "--out", path)
                self.assertEqual(rc, 1, out)
                self.assertIn(expected, err,
                              "the message did not name the missing marker")
                self.assertEqual(path.read_bytes(), before)

    def test_write_creates_a_document_that_does_not_exist_yet(self):
        """The other half of the rule: no file is the one case where there is
        nothing to stale out, so the writer may start one."""
        path = self.t.root / "nested" / "format-target.md"
        self.assertFalse(path.exists())
        rc, out, err = run("format", "--write", "--out", path)
        self.assertEqual(rc, 0, err)
        text = path.read_text(encoding="utf-8")
        self.assertIn("<!-- BEGIN GENERATED -->", text)
        self.assertIn("<!-- END GENERATED -->", text)

    def test_a_second_write_changes_nothing(self):
        """The splice must be a fixed point, or every maintenance run drifts."""
        path = self.doc("<!-- BEGIN GENERATED -->\n\n<!-- END GENERATED -->\n"
                        "tail\n")
        self.assertEqual(run("format", "--write", "--out", path)[0], 0)
        once = path.read_bytes()
        self.assertEqual(run("format", "--write", "--out", path)[0], 0)
        self.assertEqual(path.read_bytes(), once,
                         "a second write moved the document")
        self.assertTrue(once.endswith(b"tail\n"), "the tail moved or vanished")


if __name__ == "__main__":
    unittest.main()
