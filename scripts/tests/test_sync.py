"""Indexer tests: transcript parsing, classification, incremental re-runs.

Each case builds a throwaway Qoder tree in a temp directory; the real
``~/.qoder-cn`` and the real ``usage.db`` are never opened.
"""

from __future__ import annotations

import json
import os
import types
import unittest

from _helpers import TempTree, assistant, load_sync, tool_result, tool_use, usage
import qcut_db as db
import _hermetic  # noqa: E402,F401 -- tripwire: the suite must never open the real database


class ModelCallTest(unittest.TestCase):
    def setUp(self):
        self.t = TempTree(self)

    def test_a_usage_bearing_response_lands_one_row(self):
        self.t.write("projects/-work-demo/s1.jsonl", [
            assistant("s1", "m1",
                      usage=usage(input_tokens=100, output_tokens=20, credits=1.5)),
        ])
        self.t.sync()
        row = self.t.one("SELECT * FROM model_calls")
        self.assertEqual(row["message_id"], "m1")
        self.assertEqual(row["input_tokens"], 100)
        self.assertEqual(row["output_tokens"], 20)
        self.assertEqual(row["credits"], 1.5)
        self.assertEqual(row["provider"], "builtin")

    def test_a_new_model_call_is_counted_and_not_reported_as_unparsed(self):
        """`model_calls` is a count the run reports, not a parse failure.

        The filter in `_run` decides which bumped names reach `unparsed`; a
        missing name there would make every successful response look like a
        record qcut could not understand.
        """
        self.t.write("projects/-work-demo/s1.jsonl", [
            assistant("s1", "m1", usage=usage(input_tokens=100))])
        summary = self.t.sync()
        self.assertEqual(summary["counts"].get("model_calls"), 1, summary["counts"])
        self.assertEqual(self.t.rows("SELECT * FROM unparsed"), [])

    def test_a_re_read_message_is_not_counted_twice(self):
        """A duplicate line is re-read, `INSERT OR IGNORE` keeps one row, and the
        run must count only what it actually inserted."""
        path = self.t.write("projects/-work-demo/s1.jsonl", [
            assistant("s1", "m1", usage=usage(input_tokens=100))])
        self.t.sync()
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(
                assistant("s1", "m1", usage=usage(input_tokens=100))) + "\n")
        second = self.t.sync()
        self.assertNotIn("model_calls", second["counts"], second["counts"])
        self.assertEqual(
            self.t.one("SELECT COUNT(*) c FROM model_calls")["c"], 1)

    def test_a_response_without_usage_is_not_a_model_call(self):
        self.t.write("projects/-work-demo/s1.jsonl", [
            assistant("s1", "m1", usage=None),
        ])
        self.t.sync()
        self.assertEqual(self.t.one("SELECT COUNT(*) c FROM model_calls")["c"], 0)

    def test_a_reported_zero_stays_zero_and_is_not_turned_into_null(self):
        """Qoder's own routes report credits and a literal 0 for every token."""
        self.t.write("projects/-work-demo/s1.jsonl", [
            assistant("s1", "m1",
                      usage=usage(input_tokens=0, output_tokens=0, credits=4.65)),
        ])
        self.t.sync()
        row = self.t.one("SELECT * FROM model_calls")
        self.assertEqual(row["input_tokens"], 0)
        self.assertIsNotNone(row["input_tokens"])
        self.assertEqual(row["credits"], 4.65)

    def test_an_absent_field_is_null_never_a_fabricated_zero(self):
        self.t.write("projects/-work-demo/s1.jsonl", [
            assistant("s1", "m1", usage={"input_tokens": 7}),
        ])
        self.t.sync()
        row = self.t.one("SELECT * FROM model_calls")
        self.assertEqual(row["input_tokens"], 7)
        self.assertIsNone(row["output_tokens"])
        self.assertIsNone(row["credits"])

    def test_the_same_response_twice_is_stored_once(self):
        rec = assistant("s1", "m1", usage=usage(input_tokens=5))
        self.t.write("projects/-work-demo/s1.jsonl", [rec, rec])
        self.t.sync()
        self.assertEqual(self.t.one("SELECT COUNT(*) c FROM model_calls")["c"], 1)

    def test_sidechain_flag_is_recorded(self):
        self.t.write("projects/-work-demo/s1.jsonl", [
            assistant("s1", "m1", usage=usage(input_tokens=5), isSidechain=True),
        ])
        self.t.sync()
        self.assertEqual(self.t.one("SELECT is_sidechain FROM model_calls")["is_sidechain"], 1)


class RouteOriginTest(unittest.TestCase):
    """Provider is read from the configured endpoint, never from the model name."""

    def setUp(self):
        self.t = TempTree(self)

    def test_a_builtin_model_stays_builtin(self):
        self.t.write("projects/-work-demo/s1.jsonl", [
            assistant("s1", "m1", model="qmodel_38max", usage=usage(input_tokens=1)),
        ])
        self.t.sync()
        row = self.t.one("SELECT model, provider, provider_id FROM model_calls")
        self.assertEqual(row["model"], "qmodel_38max")
        self.assertEqual(row["provider"], "builtin")
        self.assertIsNone(row["provider_id"])

    def test_a_custom_route_splits_the_model_and_resolves_the_vendor(self):
        self.t.json_file("settings.json", {
            "providers": {
                "qoder-custom-abc": {"baseUrl": "https://ark.cn-beijing.volces.com/api/v3"},
            }
        })
        self.t.write("projects/-work-demo/s1.jsonl", [
            assistant("s1", "m1", model="qoder-custom-abc/ark-code-latest",
                      usage=usage(input_tokens=1)),
        ])
        self.t.sync()
        row = self.t.one("SELECT model, provider, provider_id FROM model_calls")
        self.assertEqual(row["model"], "ark-code-latest")
        self.assertEqual(row["provider"], "Volcano ARK")
        self.assertEqual(row["provider_id"], "qoder-custom-abc")

    def test_an_unknown_endpoint_falls_back_to_its_host(self):
        self.t.json_file("settings.json", {
            "providers": {"qoder-custom-x": {"baseUrl": "https://gateway.example.net/v1"}},
        })
        self.t.write("projects/-work-demo/s1.jsonl", [
            assistant("s1", "m1", model="qoder-custom-x/some-model",
                      usage=usage(input_tokens=1)),
        ])
        self.t.sync()
        self.assertEqual(self.t.one("SELECT provider FROM model_calls")["provider"],
                         "gateway.example.net")

    def test_a_model_alias_that_looks_builtin_is_still_tagged_from_its_route(self):
        """The whole point: an alias must not be inferred from its spelling."""
        self.t.json_file("settings.json", {
            "providers": {"qoder-custom-z": {"baseUrl": "https://api.deepseek.com/v1"}},
        })
        self.t.write("projects/-work-demo/s1.jsonl", [
            assistant("s1", "m1", model="qoder-custom-z/qfmodel",
                      usage=usage(input_tokens=1)),
        ])
        self.t.sync()
        row = self.t.one("SELECT model, provider FROM model_calls")
        self.assertEqual(row["model"], "qfmodel")
        self.assertEqual(row["provider"], "DeepSeek")


class ToolCallTest(unittest.TestCase):
    def setUp(self):
        self.t = TempTree(self)

    def test_a_tool_use_and_its_result_pair_up_with_a_duration(self):
        self.t.write("projects/-work-demo/s1.jsonl", [
            assistant("s1", "m1", content=[tool_use("c1", "Bash", command="ls")]),
            tool_result("r1", "c1", kind="completed"),
        ])
        self.t.sync()
        row = self.t.one("SELECT * FROM tool_calls")
        self.assertEqual(row["tool_name"], "Bash")
        self.assertEqual(row["category"], "builtin")
        self.assertEqual(row["status"], "completed")
        self.assertEqual(row["duration_ms"], 5000)

    def test_a_result_with_no_kind_still_means_completed(self):
        """14k of 31.7k results on this machine carry no `kind`; calling those
        pending reported finished work as unfinished."""
        self.t.write("projects/-work-demo/s1.jsonl", [
            assistant("s1", "m1", content=[tool_use("c1", "Read", file_path="/x")]),
            tool_result("r1", "c1"),
        ])
        self.t.sync()
        self.assertEqual(self.t.one("SELECT status FROM tool_calls")["status"], "completed")

    def test_an_error_result_is_an_error(self):
        self.t.write("projects/-work-demo/s1.jsonl", [
            assistant("s1", "m1", content=[tool_use("c1", "Bash", command="false")]),
            tool_result("r1", "c1", is_error=True, kind="completed"),
        ])
        self.t.sync()
        self.assertEqual(self.t.one("SELECT status FROM tool_calls")["status"], "error")

    def test_a_call_with_no_result_stays_pending(self):
        self.t.write("projects/-work-demo/s1.jsonl", [
            assistant("s1", "m1", content=[tool_use("c1", "Bash", command="sleep 99")]),
        ])
        self.t.sync()
        self.assertIsNone(self.t.one("SELECT status FROM tool_calls")["status"])

    def test_a_backgrounded_result_keeps_that_status(self):
        self.t.write("projects/-work-demo/s1.jsonl", [
            assistant("s1", "m1", content=[tool_use("c1", "Bash", command="serve")]),
            tool_result("r1", "c1", kind="backgrounded"),
        ])
        self.t.sync()
        self.assertEqual(self.t.one("SELECT status FROM tool_calls")["status"], "backgrounded")


class EntityClassificationTest(unittest.TestCase):
    def setUp(self):
        self.t = TempTree(self)

    def test_the_skill_tool_records_a_skill_invocation(self):
        self.t.write("projects/-work-demo/s1.jsonl", [
            assistant("s1", "m1", content=[tool_use("c1", "Skill", skill="playwright-cli")]),
            tool_result("r1", "c1", kind="completed"),
        ])
        self.t.sync()
        row = self.t.one("SELECT * FROM skill_usage")
        self.assertEqual(row["skill"], "playwright-cli")
        self.assertEqual(row["status"], "completed")

    def test_a_skill_named_in_the_command_argument_is_still_recorded(self):
        """`_classify` reads `skill` *or* `command` — the alias branch, which is a
        claimed part of the format and so needs a case that goes through it.

        Without this, a Qoder that stops sending `skill` and sends only `command`
        would empty the Skills tab while every test stayed green.
        """
        self.t.write("projects/-work-demo/s1.jsonl", [
            assistant("s1", "m1", content=[
                tool_use("c1", "Skill", command="playwright-cli")]),
            tool_result("r1", "c1", kind="completed"),
        ])
        self.t.sync()
        row = self.t.one("SELECT * FROM skill_usage")
        self.assertEqual(row["skill"], "playwright-cli")
        self.assertEqual(row["status"], "completed")

    def test_a_skill_argument_that_is_not_the_catalog_name_is_ignored(self):
        # The other half of the alias: an unrelated `command` on a non-Skill call
        # must not be read at all, so a Bash command line never reaches a column.
        self.t.write("projects/-work-demo/s1.jsonl", [
            assistant("s1", "m1", content=[
                tool_use("c1", "Bash", command="rm -rf /SENTINEL-NOT-STORED")]),
        ])
        self.t.sync()
        self.assertEqual(self.t.rows("SELECT * FROM skill_usage"), [])
        row = self.t.one("SELECT * FROM tool_calls")
        self.assertEqual(row["tool_name"], "Bash")
        self.assertEqual(row["category"], "builtin")

    def test_a_plugin_qualified_skill_is_credited_to_its_plugin(self):
        self.t.write("projects/-work-demo/s1.jsonl", [
            assistant("s1", "m1", content=[
                tool_use("c1", "Skill", skill="qoder-context:knowledge-plan")]),
        ])
        self.t.sync()
        self.assertEqual(self.t.one("SELECT plugin FROM skill_usage")["plugin"],
                         "qoder-context")
        usage_row = self.t.one("SELECT * FROM plugin_usage")
        self.assertEqual(usage_row["plugin"], "qoder-context")
        self.assertEqual(usage_row["kind"], "skill")

    def test_the_agent_tool_records_a_subagent_type(self):
        self.t.write("projects/-work-demo/s1.jsonl", [
            assistant("s1", "m1", content=[
                tool_use("c1", "Agent", subagent_type="Explore", description="x")]),
        ])
        self.t.sync()
        self.assertEqual(self.t.one("SELECT agent_type FROM agent_usage")["agent_type"],
                         "Explore")

    def test_an_agent_call_without_a_type_defaults_to_general_purpose(self):
        self.t.write("projects/-work-demo/s1.jsonl", [
            assistant("s1", "m1", content=[tool_use("c1", "Agent", description="x")]),
        ])
        self.t.sync()
        self.assertEqual(self.t.one("SELECT agent_type FROM agent_usage")["agent_type"],
                         "general-purpose")

    def test_mcp_is_recognised_by_its_own_shape_not_by_a_tool_prefix(self):
        """Qoder calls `mcp_call` and names the target in an argument — there is
        no `mcp__server__tool` tool name to match on."""
        self.t.write("projects/-work-demo/s1.jsonl", [
            assistant("s1", "m1", content=[
                tool_use("c1", "mcp_call", toolName="mcp__node-repl__node_repl",
                         arguments={"code": "1"})]),
        ])
        self.t.sync()
        row = self.t.one("SELECT * FROM mcp_usage")
        self.assertEqual(row["server"], "node-repl")
        self.assertEqual(row["tool"], "node_repl")
        self.assertEqual(row["admin_op"], "call")

    def test_an_mcp_get_target_is_also_split(self):
        self.t.write("projects/-work-demo/s1.jsonl", [
            assistant("s1", "m1", content=[
                tool_use("c1", "mcp_get", toolName="mcp__srv__do_thing")]),
        ])
        self.t.sync()
        row = self.t.one("SELECT * FROM mcp_usage")
        self.assertEqual((row["server"], row["tool"], row["admin_op"]),
                         ("srv", "do_thing", "get"))

    def test_an_mcp_target_in_the_snake_case_argument_is_also_split(self):
        """`toolName` *or* `tool_name` — the second is read, so it must be proven."""
        self.t.write("projects/-work-demo/s1.jsonl", [
            assistant("s1", "m1", content=[
                tool_use("c1", "mcp_call", tool_name="mcp__builtin__get_table")]),
        ])
        self.t.sync()
        row = self.t.one("SELECT * FROM mcp_usage")
        self.assertEqual((row["server"], row["tool"], row["admin_op"]),
                         ("builtin", "get_table", "call"))

    def test_a_free_text_argument_is_never_stored(self):
        """The agent `description`/`prompt` is prose; only the catalog name is kept."""
        sentinel = "SENTINEL-PROSE-9f2a"
        self.t.write("projects/-work-demo/s1.jsonl", [
            assistant("s1", "m1", content=[
                tool_use("c1", "Agent", subagent_type="Plan",
                         description=sentinel, prompt=sentinel)]),
        ])
        self.t.sync()
        conn = self.t.conn()
        try:
            for (table,) in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"):
                cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
                text_cols = [c for c in cols if c not in ("key", "value")]
                if not text_cols:
                    continue
                where = " OR ".join(f"CAST({c} AS TEXT) LIKE ?" for c in text_cols)
                hit = conn.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE {where}",
                    [f"%{sentinel}%"] * len(text_cols)).fetchone()[0]
                self.assertEqual(hit, 0, f"{sentinel} leaked into {table}")
        finally:
            conn.close()


class CommandTest(unittest.TestCase):
    """Where a slash-command name comes from, and what must never become one.

    Two sources write the `commands` table: a `<command-name>` marker in the text
    of a *user* record, and a `commandName` field on a tool result. The marker is
    the only place in the whole parser that reads the text of a message, so these
    cases also hold the line around it.
    """

    def setUp(self):
        self.t = TempTree(self)

    def test_a_command_quoted_by_the_model_is_not_recorded_as_an_invocation(self):
        """An assistant block containing a well-formed marker is prose.

        The real transcripts on this machine hold seven such markers and every one
        is an agent quoting the tag in an explanation, none of them a command the
        user ran. Storing them would invent usage. The user record in the same
        file is the control: the scan itself works.
        """
        self.t.write("projects/-work-demo/s1.jsonl", [
            assistant("s1", "m1", content=[
                {"type": "text",
                 "text": ("Here is the shape of the marker:\n"
                          "```\n<command-name>/never-ran</command-name>\n```\n"
                          "it echoes the invocation back.")},
            ]),
            {"type": "user", "sessionId": "s1", "uuid": "uuid-u1",
             "timestamp": "2026-10-01T10:00:05.000Z", "cwd": "/work/demo",
             "isSidechain": False,
             "message": {"role": "user", "content": [
                 {"type": "text",
                  "text": "<command-name>/deploy-staging</command-name>"}]}},
        ])
        self.t.sync()
        self.assertEqual(
            [r["command"] for r in self.t.rows("SELECT command FROM commands")],
            ["deploy-staging"])

    def test_a_command_name_on_a_tool_result_is_recorded(self):
        """The other source, and the only one observed in real transcripts."""
        self.t.write("projects/-work-demo/s1.jsonl", [
            assistant("s1", "m1", content=[tool_use("c1", "Bash", command="ls")]),
            tool_result("r1", "c1", commandName="playwright-cli"),
        ])
        self.t.sync()
        row = self.t.one("SELECT * FROM commands")
        self.assertEqual(row["command"], "playwright-cli")
        self.assertEqual(row["session_id"], "s1")

    def test_nothing_outside_the_command_marker_reaches_a_column(self):
        """The marker's contents are stored; the prose around it is not."""
        sentinel = "SENTINEL-AROUND-MARKER-4c1f"
        self.t.write("projects/-work-demo/s1.jsonl", [
            {"type": "user", "sessionId": "s1", "uuid": "uuid-u1",
             "timestamp": "2026-10-01T10:00:05.000Z", "cwd": "/work/demo",
             "isSidechain": False,
             "message": {"role": "user", "content": [
                 {"type": "text",
                  "text": f"{sentinel}\n<command-name>/show-usage</command-name>\n"
                          f"{sentinel}"}]}},
        ])
        self.t.sync()
        self.assertEqual(
            [r["command"] for r in self.t.rows("SELECT command FROM commands")],
            ["show-usage"])
        conn = self.t.conn()
        try:
            for (table,) in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"):
                cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
                text_cols = [c for c in cols if c not in ("key", "value")]
                if not text_cols:
                    continue
                where = " OR ".join(f"CAST({c} AS TEXT) LIKE ?" for c in text_cols)
                hit = conn.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE {where}",
                    [f"%{sentinel}%"] * len(text_cols)).fetchone()[0]
                self.assertEqual(hit, 0, f"{sentinel} leaked into {table}")
        finally:
            conn.close()


class InventoryTest(unittest.TestCase):
    def setUp(self):
        self.t = TempTree(self)

    def test_user_skills_are_inventoried_from_their_manifest(self):
        self.t.skill("gh")
        self.t.skill("playwright-cli")
        self.t.sync()
        names = {r["name"] for r in self.t.rows(
            "SELECT name FROM inventory WHERE kind='skill' AND source='user'")}
        self.assertEqual(names, {"gh", "playwright-cli"})

    def test_plugins_are_inventoried_with_their_version(self):
        self.t.json_file("plugins/installed_plugins_v2.json", {
            "version": 2,
            "plugins": {
                "demo-plugin@qoder-bundler": [
                    {"scope": "user", "installPath": str(self.t.qoder / "plugins/cache/demo-plugin"),
                     "version": "0.4.2", "source": "marketplace"},
                ],
            },
        })
        (self.t.qoder / "plugins/cache/demo-plugin").mkdir(parents=True)
        self.t.sync()
        row = self.t.one("SELECT * FROM inventory WHERE kind='plugin'")
        self.assertEqual(row["name"], "demo-plugin")
        self.assertEqual(row["owner_plugin"], "qoder-bundler")
        self.assertEqual(row["version"], "0.4.2")

    def test_configured_mcp_servers_show_even_with_no_usage(self):
        self.t.json_file("mcp.json", {"mcpServers": {"unused-srv": {"command": "x"}}})
        self.t.sync()
        self.assertEqual(self.t.one("SELECT name FROM inventory WHERE kind='mcp'")["name"],
                         "unused-srv")

    def test_an_installed_but_unused_entity_still_lists_with_zero(self):
        self.t.skill("never-used")
        self.t.sync()
        conn = self.t.conn()
        try:
            from qcut_db import q_skills
            rows = {r["skill"]: r["calls"] for r in q_skills(conn)}
        finally:
            conn.close()
        self.assertIn("never-used", rows)
        self.assertEqual(rows["never-used"], 0)


class IncrementalTest(unittest.TestCase):
    def setUp(self):
        self.t = TempTree(self)

    def test_a_second_run_reads_nothing_new(self):
        self.t.write("projects/-work-demo/s1.jsonl", [
            assistant("s1", "m1", usage=usage(input_tokens=1))])
        first = self.t.sync()
        second = self.t.sync()
        self.assertEqual(first["scanned"], 1)
        self.assertEqual(second["scanned"], 0)

    def test_only_the_appended_tail_is_indexed(self):
        path = self.t.write("projects/-work-demo/s1.jsonl", [
            assistant("s1", "m1", usage=usage(input_tokens=1))])
        self.t.sync()
        from _helpers import write_records
        write_records(path, [assistant("s1", "m2", usage=usage(input_tokens=2))])
        self.t.sync()
        self.assertEqual(self.t.one("SELECT COUNT(*) c FROM model_calls")["c"], 2)

    def test_a_partial_trailing_line_is_left_for_the_next_run(self):
        path = self.t.transcript()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
        self.t.sync()
        # A half-written JSON object, as a file being appended to right now.
        with open(path, "a", encoding="utf-8") as fh:
            fh.write('{"type": "assistant", "message": {"id": "m9"')
        self.t.sync()
        self.assertEqual(self.t.one("SELECT COUNT(*) c FROM model_calls")["c"], 0)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(', "usage": {"input_tokens": 3}}}\n')
        self.t.sync()
        self.assertEqual(self.t.one("SELECT COUNT(*) c FROM model_calls")["c"], 1)

    def test_repeated_syncs_do_not_duplicate_rows(self):
        self.t.write("projects/-work-demo/s1.jsonl", [
            assistant("s1", "m1", content=[tool_use("c1", "Bash", command="x")],
                      usage=usage(input_tokens=1)),
            tool_result("r1", "c1", kind="completed"),
        ])
        for _ in range(3):
            self.t.sync()
        self.assertEqual(self.t.one("SELECT COUNT(*) c FROM model_calls")["c"], 1)
        self.assertEqual(self.t.one("SELECT COUNT(*) c FROM tool_calls")["c"], 1)

    def test_repeated_syncs_stabilise_every_table_in_the_schema(self):
        """The promise is "sync is idempotent", and the case above only looked at
        two of the nine tables.

        `inventory` was the one that broke: it has no unique key, so
        `INSERT OR REPLACE` never conflicted and each sync appended another copy
        of every entry — 35 installed things read as 89 rows after three syncs,
        and `qcut inventory` printed each one three times. The table list here is
        enumerated from `sqlite_master` rather than typed, so a table added next
        month is covered by this case without somebody remembering to say so.
        """
        self.t.write("projects/-work-demo/s1.jsonl", [
            assistant("s1", "m1", content=[tool_use("c1", "Skill",
                                                    skill="demo")],
                      usage=usage(input_tokens=1)),
            tool_result("r1", "c1", kind="completed"),
        ])
        self.t.skill("demo")
        self.t.json_file("mcp.json", {"mcpServers": {"srv": {"url": "http://x"}}})

        def counts():
            import re
            conn = self.t.conn()
            try:
                names = [r["name"] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")]
                keep = [n for n in names if re.fullmatch(r"[a-z_]+", n)]
                self.assertEqual(len(keep), len(names),
                                 f"a table name escaped the allowlist: {names}")
                return {n: conn.execute(f"SELECT COUNT(*) FROM {n}").fetchone()[0]
                        for n in keep}
            finally:
                conn.close()

        self.t.sync()
        first = counts()
        self.t.sync()
        self.t.sync()
        third = counts()

        # Positive control: a scan that indexed nothing would compare empty to
        # empty and pass, which is how a stability check stops meaning anything.
        self.assertGreaterEqual(first["inventory"], 25,
                                "the fixture must populate inventory")
        self.assertGreaterEqual(first["tool_calls"], 1)
        grew = {t: (first[t], third[t]) for t in first if first[t] != third[t]}
        self.assertEqual(
            grew, {},
            f"three syncs of unchanged input changed row counts: {grew}")

    def test_full_sync_rebuilds_from_scratch(self):
        self.t.write("projects/-work-demo/s1.jsonl", [
            assistant("s1", "m1", usage=usage(input_tokens=1))])
        self.t.sync()
        self.t.sync(full=True)
        self.assertEqual(self.t.one("SELECT COUNT(*) c FROM model_calls")["c"], 1)

    def test_a_rewritten_shorter_file_is_re_read(self):
        path = self.t.write("projects/-work-demo/s1.jsonl", [
            assistant("s1", "m1", usage=usage(input_tokens=1)),
            assistant("s1", "m2", usage=usage(input_tokens=2)),
        ])
        self.t.sync()
        self.assertEqual(self.t.one("SELECT COUNT(*) c FROM model_calls")["c"], 2)
        path.write_text(json.dumps(
            assistant("s1", "m3", usage=usage(input_tokens=3))) + "\n", encoding="utf-8")
        self.t.sync()
        self.assertEqual(self.t.one("SELECT COUNT(*) c FROM model_calls")["c"], 3)


class RewrittenFileTest(unittest.TestCase):
    """A transcript that is *rewritten*, not appended to.

    All of the indexer's speed comes from an append-only assumption, so every case
    here is the other shape: bytes replaced at the same length, a file that
    shortened, a terminator that arrives after the record. Each of these fails
    silently under the old predicate — a stale offset reads zero bytes, the run
    reports success, and the new records are simply not in the database.
    """

    def setUp(self):
        self.t = TempTree(self)

    def _spool(self, path, recs, terminator="\n"):
        """Write the file from scratch. `terminator=""` leaves the **last** record
        unterminated, which is the crash shape; it does not put the records on one
        line, because two records on one line are not JSON at all."""
        path.parent.mkdir(parents=True, exist_ok=True)
        body = "\n".join(json.dumps(r) for r in recs)
        path.write_text(body + terminator if body else "", encoding="utf-8")
        return path

    def test_a_rewritten_file_of_the_same_length_is_reread(self):
        """The shape stat alone cannot name: same bytes, different content.

        An append always grows the file, so a changed mtime that did *not* grow it
        is a rewrite. Reading on from the old offset would return nothing at all.
        """
        path = self._spool(self.t.transcript(), [
            assistant("s1", "m1", usage=usage(input_tokens=1))])
        self.t.sync()
        self._spool(path, [assistant("s1", "m2", usage=usage(input_tokens=2))])
        now = path.stat().st_mtime
        os.utime(path, (now + 5, now + 5))       # the rewrite moved mtime
        self.t.sync()
        ids = [r["message_id"] for r in
               self.t.rows("SELECT message_id FROM model_calls")]
        self.assertIn("m2", ids, f"the rewrite was read from a stale offset: {ids}")

    def test_a_shrunk_file_is_reread(self):
        path = self._spool(self.t.transcript(), [
            assistant("s1", "m1", usage=usage(input_tokens=1)),
            assistant("s1", "m2", usage=usage(input_tokens=2))])
        self.t.sync()
        self._spool(path, [assistant("s1", "m3", usage=usage(input_tokens=3))])
        self.t.sync()
        ids = [r["message_id"] for r in
               self.t.rows("SELECT message_id FROM model_calls")]
        self.assertIn("m3", ids, f"the shortened file was never re-read: {ids}")

    def test_a_growing_file_is_still_read_from_its_offset(self):
        """The control: the fix must not turn every append into a rebuild."""
        path = self._spool(self.t.transcript(), [
            assistant("s1", "m1", usage=usage(input_tokens=1))])
        self.t.sync()
        from _helpers import write_records
        write_records(path, [assistant("s1", "m2", usage=usage(input_tokens=2))])
        second = self.t.sync()
        self.assertEqual(second["scanned"], 1)
        row = self.t.one("SELECT * FROM sync_state")
        self.assertGreater(row["offset"], 0)
        self.assertEqual(self.t.one("SELECT COUNT(*) c FROM model_calls")["c"], 2)

    def test_a_final_record_with_no_newline_is_indexed(self):
        """A record and its terminator are two flushes, and a crash sits between."""
        path = self._spool(self.t.transcript(), [
            assistant("s1", "m1", usage=usage(input_tokens=1)),
            assistant("s1", "m2", usage=usage(input_tokens=2))], terminator="")
        self.t.sync()
        self.assertEqual(
            self.t.one("SELECT COUNT(*) c FROM model_calls")["c"], 2,
            "the unterminated last record was never read")

    def test_a_terminator_arriving_later_does_not_re_read_the_whole_file(self):
        """The newline shows up after the record was already consumed.

        A bad-line marker is planted in the file because a re-read from zero would
        count it a second time — the row count is the observable that says whether
        the offset was trusted.
        """
        path = self._spool(self.t.transcript(), [
            assistant("s1", "m1", usage=usage(input_tokens=1))], terminator="")
        self.t.sync()
        # The precondition: the unterminated record was consumed, so the stored
        # offset sits after it, on the byte that has not arrived yet. Without this
        # the case passes on a build that never read the tail at all.
        row = self.t.one("SELECT * FROM sync_state")
        self.assertEqual(row["offset"], path.stat().st_size,
                         "the tail was not consumed, so this case proves nothing")
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("\nnot-json\n")
        self.t.sync()
        self.assertEqual(
            self.t.one("SELECT COUNT(*) c FROM model_calls")["c"], 1,
            "the record before the late terminator was not kept")
        reasons = dict(self.t.rows("SELECT reason, count FROM unparsed"))
        self.assertEqual(reasons.get("unparseable_line"), 1, reasons)

    def test_a_half_written_tail_is_not_counted_as_unparseable(self):
        """Retried, not reported: a partial line would otherwise grow the drift
        signal once per poll."""
        path = self._spool(self.t.transcript(), [])
        self.t.sync()
        with open(path, "a", encoding="utf-8") as fh:
            fh.write('{"type": "assistant", "message": {"id": "m9"')
        self.t.sync()
        reasons = dict(self.t.rows("SELECT reason, count FROM unparsed"))
        self.assertNotIn("unparseable_line", reasons, reasons)
        self.assertEqual(
            self.t.one("SELECT COUNT(*) c FROM model_calls")["c"], 0)

    def test_the_reset_predicate_leaves_an_untouched_file_alone(self):
        """Unit level, with the negative control the churn needs.

        A file that is untouched and fully consumed must not be reset even though
        its stored offset is not preceded by a newline — without that early-out a
        transcript ending without a terminator would be re-read on every run.
        """
        sync = load_sync()
        path = self._spool(self.t.transcript(), [
            assistant("s1", "m1", usage=usage(input_tokens=1))], terminator="")
        self.t.sync()
        row = dict(self.t.one("SELECT * FROM sync_state"))
        st = path.stat()
        self.assertFalse(sync._needs_reset(path, st, row),
                         "an untouched, fully consumed file was called stale")
        rewritten = types.SimpleNamespace(
            st_size=st.st_size, st_mtime=st.st_mtime + 5)
        self.assertTrue(sync._needs_reset(path, rewritten, row),
                        "a same-length rewrite was not called stale")
        shorter = types.SimpleNamespace(st_size=st.st_size - 1,
                                        st_mtime=st.st_mtime + 5)
        self.assertTrue(sync._needs_reset(path, shorter, row),
                        "a shortened file was not called stale")
        # A plain append, on a file that ends the way a flushed transcript does.
        second = self._spool(self.t.transcript("projects/-work-demo/s2.jsonl"), [
            assistant("s2", "m1", usage=usage(input_tokens=1))])
        self.t.sync()
        row2 = self.t.one("SELECT * FROM sync_state WHERE file_path = ?",
                          (str(second),))
        st2 = second.stat()
        appended = types.SimpleNamespace(st_size=st2.st_size + 40,
                                         st_mtime=st2.st_mtime + 5)
        self.assertFalse(sync._needs_reset(second, appended, dict(row2)),
                         "a plain append was called stale")


class UnparsedAccountingTest(unittest.TestCase):
    def setUp(self):
        self.t = TempTree(self)

    def test_an_unparseable_line_is_counted(self):
        path = self.t.transcript()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("not json at all\n", encoding="utf-8")
        self.t.sync()
        self.assertEqual(
            self.t.one("SELECT count FROM unparsed WHERE reason='unparseable_line'")["count"], 1)

    def test_an_unknown_record_type_is_counted_by_name(self):
        self.t.write("projects/-work-demo/s1.jsonl", [
            {"type": "brand-new-thing", "sessionId": "s1"},
        ])
        self.t.sync()
        row = self.t.one("SELECT * FROM unparsed")
        self.assertEqual(row["reason"], "type:brand-new-thing")

    def test_a_known_but_unused_record_type_is_claimed_not_counted(self):
        """`active-leaf` runs to tens of thousands here; counting it as
        unparsed would make the signal useless. `ai-title` is the CLI 1.1.75
        shape of the same thing: a generated title qcut must never read."""
        self.t.write("projects/-work-demo/s1.jsonl", [
            {"type": "active-leaf", "sessionId": "s1", "uuid": "u1"},
            {"type": "file-history-snapshot", "sessionId": "s1"},
            {"type": "ai-title", "sessionId": "s1",
             "title": "a sentinel that must not be stored"},
        ])
        self.t.sync()
        self.assertEqual(self.t.one("SELECT COUNT(*) c FROM unparsed")["c"], 0)
        self.assertEqual(self.t.one(
            "SELECT COUNT(*) c FROM sync_state")["c"], 1,
            "the file carrying only no-op records was not consumed")

    def test_a_record_with_no_type_is_counted(self):
        self.t.write("projects/-work-demo/s1.jsonl", [{"sessionId": "s1"}])
        self.t.sync()
        self.assertEqual(self.t.one("SELECT reason FROM unparsed")["reason"],
                         "type:<missing>")


class SessionTest(unittest.TestCase):
    def setUp(self):
        self.t = TempTree(self)

    def test_a_session_records_its_project_and_span(self):
        self.t.write("projects/-work-demo/s1.jsonl", [
            assistant("s1", "m1", usage=usage(input_tokens=1),
                      ts="2026-10-01T10:00:00.000Z", cwd="/work/demo"),
            assistant("s1", "m2", usage=usage(input_tokens=1),
                      ts="2026-10-01T11:00:00.000Z", cwd="/work/demo"),
        ])
        self.t.sync()
        row = self.t.one("SELECT * FROM sessions")
        self.assertEqual(row["project"], "/work/demo")
        self.assertEqual(row["version"], "1.1.57")
        self.assertLess(row["started_at"], row["ended_at"])

    def test_a_subagent_transcript_shares_its_parent_session(self):
        """120 of 157 files here are `agent-a<Type>-<hash>.jsonl` carrying the
        parent's sessionId — they must not create a session per file."""
        self.t.write("projects/-work-demo/s1.jsonl", [
            assistant("s1", "m1", usage=usage(input_tokens=1))])
        self.t.write("projects/-work-demo/agent-aExplore-abc.jsonl", [
            assistant("s1", "m2", usage=usage(input_tokens=1), isSidechain=True)])
        self.t.sync()
        self.assertEqual(self.t.one("SELECT COUNT(*) c FROM sessions")["c"], 1)
        self.assertEqual(self.t.one("SELECT COUNT(*) c FROM model_calls")["c"], 2)


class CrashSafetyTest(unittest.TestCase):
    def setUp(self):
        self.t = TempTree(self)

    def test_an_offset_always_lands_on_a_line_boundary(self):
        self.t.write("projects/-work-demo/s1.jsonl", [
            assistant("s1", "m1", usage=usage(input_tokens=1)),
            assistant("s1", "m2", usage=usage(input_tokens=2)),
        ])
        self.t.sync()
        row = self.t.one("SELECT * FROM sync_state")
        path = row["file_path"]
        with open(path, "rb") as fh:
            fh.seek(row["offset"] - 1)
            self.assertEqual(fh.read(1), b"\n")


    def test_an_offset_after_an_unterminated_record_sits_on_a_record_boundary(self):
        """The same rule, on the shape the line-boundary case cannot see.

        A consumed final record with no terminator leaves the offset at the end of
        the file with a `}` behind it. That is a record boundary — and asserting
        only the newline form would let the code and the doc drift apart.
        """
        path = self.t.transcript()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(assistant("s1", "m1", usage=usage(input_tokens=1))),
                        encoding="utf-8")
        self.t.sync()
        row = self.t.one("SELECT * FROM sync_state")
        self.assertEqual(row["offset"], path.stat().st_size)
        with open(path, "rb") as fh:
            fh.seek(row["offset"] - 1)
            self.assertEqual(fh.read(1), b"}")

    def test_an_unreadable_file_is_reported_and_the_run_survives(self):
        """One unreadable transcript must not take the index down with it.

        The name is planted in `unparsed` because the reason is the point: an
        operator reading `qcut health` has to see *which* shape failed, not a run
        that returned nothing.
        """
        good = self.t.write("projects/-work-demo/good.jsonl", [
            assistant("s1", "m1", usage=usage(input_tokens=1))])
        bad = self.t.write("projects/-work-demo/bad.jsonl", [
            assistant("s2", "m2", usage=usage(input_tokens=2))])
        os.chmod(bad, 0)
        self.addCleanup(os.chmod, bad, 0o644)
        summary = self.t.sync()
        self.assertGreaterEqual(summary["files"], 2,
                                "the run did not reach the file list")
        self.assertEqual(self.t.one("SELECT COUNT(*) c FROM model_calls")["c"], 1,
                         "the readable transcript was not indexed either")
        reasons = dict(self.t.rows("SELECT reason, count FROM unparsed"))
        self.assertIn("unreadable_file", reasons, reasons)

    def test_a_retry_picks_the_unreadable_file_back_up(self):
        """Nothing was remembered for it, so the next run reads it.

        Storing an offset for a file that was never read would retire it forever,
        and the row it holds would be missing with no reason attached to it.
        """
        bad = self.t.write("projects/-work-demo/bad.jsonl", [
            assistant("s2", "m2", usage=usage(input_tokens=2))])
        os.chmod(bad, 0)
        self.t.sync()
        self.assertIsNone(self.t.one(
            "SELECT 1 x FROM sync_state WHERE file_path = ?", (str(bad),)))
        self.assertEqual(
            self.t.one("SELECT COUNT(*) c FROM model_calls")["c"], 0)
        os.chmod(bad, 0o644)
        self.t.sync()
        self.assertEqual(
            self.t.one("SELECT COUNT(*) c FROM model_calls")["c"], 1,
            "the file stayed skipped after it became readable")


class FormatDocTest(unittest.TestCase):
    """The generated format surface must carry the capture rule."""

    def test_the_env_section_names_the_variable_and_the_scope(self):
        doc = load_sync().format_doc()
        self.assertIn("## Token capture (environment)", doc)
        self.assertIn(db.TOKEN_ENV_NAME, doc)
        # The one thing a reader gets wrong on their own: qcut's environment is
        # irrelevant; the variable has to be there when the CLI starts.
        self.assertIn("process that runs the Qoder CN CLI", doc)
        self.assertIn("cannot be recovered", doc)

    def test_the_env_section_is_generated_from_the_constant(self):
        """A hard-coded name in the document would drift from the one `qcut env`
        prints. The section is built from `db.TOKEN_ENV_NAME`, so replacing the
        constant must replace both."""
        from unittest import mock
        sync = load_sync()
        with mock.patch.object(db, "TOKEN_ENV_NAME", "QCUT_TEST_FLAG"):
            doc = sync.format_doc()
        self.assertIn("QCUT_TEST_FLAG", doc)
        self.assertNotIn("QODERCN_EXPOSE_TOKEN_USAGE", doc)


if __name__ == "__main__":
    unittest.main()
