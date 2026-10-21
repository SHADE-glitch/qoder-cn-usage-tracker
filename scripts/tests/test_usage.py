"""Storage-layer tests: the calendar-day windows, the query helpers, the schema.

A hand-built database in a temp directory — no transcript parsing, so a failure
here points at the storage layer and nowhere else.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta

# `_helpers` puts scripts/ on sys.path, so it must be imported before qcut_db.
from _helpers import SCRIPTS, TempTree

import qcut_db as db
import _hermetic  # noqa: E402,F401 -- tripwire: the suite must never open the real database


def ms_on(days_ago=0, hour=12):
    day = datetime.now().replace(hour=hour, minute=0, second=0, microsecond=0)
    return int((day - timedelta(days=days_ago)).timestamp() * 1000)


class RouteLabelTest(unittest.TestCase):
    """`Route` is derived once, so the Tokens and Usage tabs cannot disagree."""

    def test_builtin_third_party_and_unknown(self):
        self.assertEqual(db.route_label("builtin"), "Built-in")
        self.assertEqual(db.route_label("Volcano ARK"), "3P")
        self.assertEqual(db.route_label(None), "-")


class WindowTest(unittest.TestCase):
    def setUp(self):
        self.t = TempTree(self)

    def test_today_starts_at_local_midnight(self):
        now = int(datetime.now().replace(microsecond=0).timestamp() * 1000)
        start, end = db.window_bounds("1d", now)
        self.assertEqual(end, now)
        self.assertEqual(datetime.fromtimestamp(start / 1000).hour, 0)
        self.assertEqual(datetime.fromtimestamp(start / 1000).minute, 0)

    def test_a_two_day_window_starts_yesterday(self):
        now = int(datetime.now().timestamp() * 1000)
        start, _ = db.window_bounds("2d", now)
        today_midnight = datetime.fromtimestamp(now / 1000).replace(
            hour=0, minute=0, second=0, microsecond=0)
        self.assertEqual(datetime.fromtimestamp(start / 1000),
                         today_midnight - timedelta(days=1))

    def test_all_time_has_no_lower_bound(self):
        start, end = db.window_bounds("all", 1234567890)
        self.assertIsNone(start)
        self.assertEqual(end, 1234567890)

    def test_the_window_is_whole_calendar_days_not_rolling_hours(self):
        """A window is stable through the day, and it moves only at midnight.

        Anchored to fixed hours on purpose. With `datetime.now()` this case was
        only true while "now minus three hours" stayed inside the same local day:
        CI ran it at 01:35 UTC, where three hours back is the previous day, and it
        failed; the same suite passed at 09:35 local on the same commit. The rule
        did not change, the runner's clock did.
        """
        morning, afternoon = db.window_bounds("7d", ms_on(0, 9))[0], \
            db.window_bounds("7d", ms_on(0, 15))[0]
        self.assertEqual(morning, afternoon,
                         "the window crept with the hour inside one day")
        previous_noon = ms_on(1, 12)
        across_midnight = db.window_bounds("7d", previous_noon)[0]
        first, second = (datetime.fromtimestamp(value / 1000).date()
                         for value in (morning, across_midnight))
        self.assertEqual(first - second, timedelta(days=1),
                         "one step of the window is not one calendar day "
                         f"({first} vs {second})")

    def test_every_range_has_a_label(self):
        for key in db.RANGE_ORDER:
            self.assertIn(key, db.USAGE_RANGES)
            self.assertTrue(db.window_label(key))

    def test_a_custom_window_labels_both_of_its_ends(self):
        label = db.window_label(db.CustomWindow(ms_on(1, 9), ms_on(0, 17)))
        self.assertIn("→", label)


class CacheHitRateTest(unittest.TestCase):
    """The rate is over the *cacheable input*: hit / (hit + miss + write).

    Qoder reports only the cached read and stores the whole prompt in
    ``input_tokens``, so ``miss`` is derived at the call site as
    ``input - read``. The three parts then partition the prompt, which is what
    makes the percentage comparable to what the provider's own dashboard shows.
    """

    def test_the_denominator_is_the_three_cache_parts(self):
        self.assertAlmostEqual(db.cache_hit_rate(500, 500), 0.5)
        self.assertAlmostEqual(db.cache_hit_rate(500, 0), 1.0)
        self.assertAlmostEqual(db.cache_hit_rate(0, 1000), 0.0)

    def test_a_cache_write_counts_in_the_denominator(self):
        # 100 hit of (100 + 100 miss + 800 write) — a first cold request.
        self.assertAlmostEqual(db.cache_hit_rate(100, 100, 800), 0.1)

    def test_an_empty_prompt_is_undefined_not_zero(self):
        """0.0 would claim "nothing was cached", which is a different fact."""
        self.assertIsNone(db.cache_hit_rate(0, 0))

    def test_a_missing_read_count_is_undefined(self):
        self.assertIsNone(db.cache_hit_rate(None, 100))

    def test_a_missing_miss_count_still_resolves(self):
        """A route that reports the read but no prompt still has a rate."""
        self.assertAlmostEqual(db.cache_hit_rate(500, None), 1.0)


class CacheMissDerivationTest(unittest.TestCase):
    """The miss column: the part of the prompt that was not read from cache."""

    def setUp(self):
        self.t = TempTree(self)
        conn = self.t.conn()
        conn.execute(
            "INSERT INTO model_calls(message_id, ts, model, provider,"
            " input_tokens, output_tokens, cache_read_tokens) VALUES"
            " ('m1',?, 'ark-code-latest','Volcano ARK', 1000, 10, 800),"
            " ('m2',?, 'ark-code-latest','Volcano ARK', 500,  10, 500),"
            " ('m3',?, 'qfmodel','builtin', NULL, NULL, NULL)",
            (ms_on(0), ms_on(0), ms_on(0)))
        conn.commit()
        conn.close()

    def test_miss_is_input_minus_read(self):
        rows = {r["message_id"]: r for r in self.t.rows(
            "SELECT message_id, input_tokens - COALESCE(cache_read_tokens,0)"
            " AS miss FROM model_calls")}
        self.assertEqual(rows["m1"]["miss"], 200)
        self.assertEqual(rows["m2"]["miss"], 0)

    def test_the_summary_reports_the_derived_miss_and_the_missing_rows(self):
        conn = self.t.conn()
        try:
            summary = db.q_usage_summary(conn, None, ms_on(0) + 1)
        finally:
            conn.close()
        self.assertEqual(summary["cache_hit"], 1300)
        self.assertEqual(summary["cache_miss"], 200)
        self.assertEqual(summary["requests"], 3)
        # A route that reported no tokens at all is counted, not invented.
        self.assertEqual(summary["missing_input"], 1)
        self.assertEqual(summary["missing_output"], 1)

    def test_the_request_log_carries_the_derived_miss_per_row(self):
        conn = self.t.conn()
        try:
            rows = {r["message_id"]: r for r in db.q_usage_request_logs(
                conn, None, ms_on(0) + 1)}
        finally:
            conn.close()
        self.assertEqual(rows["m1"]["cache_miss_tokens"], 200)
        self.assertIsNone(rows["m3"]["cache_miss_tokens"])
        self.assertIsNone(rows["m3"]["total_tokens"])
        # Usage Total is the display-only re-add; it is NULL with no API parts.
        self.assertIsNone(rows["m3"]["usage_total_tokens"])
        self.assertEqual(rows["m1"]["usage_total_tokens"], 1000 + 10 + 800)


class TokenReportingTest(unittest.TestCase):
    """`—` versus `0`, and the one predicate that decides which is drawn.

    Qoder's own routes bill in credits and answer every token field with a
    literal ``0``; a BYOK route is the reverse. So a ``0`` in a token column is
    not a measurement, and neither is a sum of them. The decision is
    ``_TOKENS_REPORTED_EXPR`` in SQL and ``row_reports_tokens`` in Python —
    these cases hold the two together, because a dash that disagrees with the
    ``Tok reported`` column would be a second way to lie.
    """

    COLUMNS = ("message_id", "session_id", "project", "ts", "model", "provider",
               "input_tokens", "output_tokens", "cache_read_tokens",
               "cache_creation_tokens", "credits")

    def setUp(self):
        self.t = TempTree(self)
        import importlib.util
        import sys as _sys
        spec = importlib.util.spec_from_file_location(
            "qcut_stats_tokens", str(SCRIPTS / "qcut-stats.py"))
        self.stats = importlib.util.module_from_spec(spec)
        _sys.modules["qcut_stats_tokens"] = self.stats
        spec.loader.exec_module(self.stats)

    def insert(self, *rows):
        """rows: (message_id, model, input, output, cache_read, cache_write, credits)"""
        conn = self.t.conn()
        conn.executemany(
            "INSERT INTO model_calls(message_id, session_id, project, ts, model,"
            " provider, input_tokens, output_tokens, cache_read_tokens,"
            " cache_creation_tokens, credits, has_usage) VALUES"
            " (?,?, '/a', ?, ?, 'builtin', ?, ?, ?, ?, ?, 1)",
            [(mid, "s1", ms_on(0), model, i, o, cr, cw, credits)
             for mid, model, i, o, cr, cw, credits in rows])
        conn.commit()
        conn.close()

    def _all_reported_counts(self):
        """The same window through every query that shows a token number."""
        conn = self.t.conn()
        try:
            got = {
                "kpi": db.q_usage_kpi(conn)["tokens_reported"],
                "summary": db.q_usage_summary(conn, None, None)["tokens_reported"],
                "models": {r["model"]: r["tokens_reported"]
                           for r in db.q_models(conn)},
                "entity": db.q_entity_summary(conn, "model", "qfmodel",
                                              None, None)["tokens_reported"],
            }
        finally:
            conn.close()
        return got

    def test_a_credits_only_row_does_not_count_as_reporting_tokens(self):
        self.insert(("m1", "qfmodel", 0, 0, 0, 0, 5.0))
        counts = self._all_reported_counts()
        self.assertEqual(counts["kpi"], 0)
        self.assertEqual(counts["summary"], 0)
        self.assertEqual(counts["models"]["qfmodel"], 0)
        self.assertEqual(counts["entity"], 0)

    def test_a_row_that_reports_only_a_cache_write_counts_in_every_query(self):
        # The three window queries once each spelled the predicate out by hand,
        # and two of them left `cache_creation_tokens` out — so a row that
        # reported only a cache write read as "no tokens" in the Dashboard and
        # Usage panels while the Tokens tab called it reported. One expression
        # now, and this is the shape that separates them.
        self.insert(("m1", "qfmodel", 0, 0, 0, 7, 1.0))
        counts = self._all_reported_counts()
        self.assertEqual(counts["kpi"], 1)
        self.assertEqual(counts["summary"], 1)
        self.assertEqual(counts["models"]["qfmodel"], 1)
        self.assertEqual(counts["entity"], 1)

    def test_the_sql_and_python_halves_agree_row_for_row(self):
        shapes = [
            ("m1", "qfmodel", 0, 0, 0, 0, 5.0),        # credits-only
            ("m2", "ark", 100, 10, 80, 0, None),      # tokens-only
            ("m3", "ark", 0, 0, 0, 7, None),          # cache write only
            ("m4", "ark", 0, 3, 0, 0, None),          # output only
            ("m5", "qfmodel", None, None, None, None, 1.0),  # fields absent
        ]
        self.insert(*shapes)
        conn = self.t.conn()
        try:
            sql = conn.execute(
                "SELECT message_id, CASE WHEN " + db._TOKENS_REPORTED_EXPR +
                " THEN 1 ELSE 0 END AS rep FROM model_calls").fetchall()
            rows = {r["message_id"]: r for r in conn.execute(
                "SELECT * FROM model_calls").fetchall()}
        finally:
            conn.close()
        for r in sql:
            self.assertEqual(bool(r["rep"]), db.row_reports_tokens(rows[r["message_id"]]),
                             r["message_id"])

    def test_a_mixed_window_counts_only_the_rows_that_report(self):
        self.insert(("m1", "qfmodel", 0, 0, 0, 0, 5.0),
                    ("m2", "qfmodel", 0, 0, 0, 0, 2.0),
                    ("m3", "ark", 100, 10, 0, 0, None))
        counts = self._all_reported_counts()
        self.assertEqual(counts["kpi"], 1)
        self.assertEqual(counts["models"], {"qfmodel": 0, "ark": 1})

    # --- the headless report renders the same decision ---------------------

    def _run(self, fn, args=None):
        import io
        from contextlib import redirect_stdout
        conn = self.t.conn()
        buf = io.StringIO()
        try:
            with redirect_stdout(buf):
                code = fn(conn, args)
        finally:
            conn.close()
        return code, buf.getvalue()

    def test_stats_draws_a_dash_for_a_credits_only_window(self):
        self.insert(("m1", "qfmodel", 0, 0, 0, 0, 5.0))
        _, out = self._run(self.stats.cmd_stats, type("A", (), {})())
        self.assertRegex(out, r"input tokens\s+—")
        self.assertRegex(out, r"output tokens\s+—")
        self.assertRegex(out, r"cache read tokens\s+—")
        # The money axis is a real measurement, so it stays a number.
        self.assertRegex(out, r"credits\s+5\.00")
        self.assertIn("instead of 0", out)

    def test_stats_keeps_numbers_where_a_route_reported_tokens(self):
        self.insert(("m1", "ark", 100, 10, 80, 0, None))
        _, out = self._run(self.stats.cmd_stats, type("A", (), {})())
        self.assertRegex(out, r"input tokens\s+100")
        self.assertRegex(out, r"output tokens\s+10")
        self.assertRegex(out, r"cache read tokens\s+80")
        self.assertNotRegex(out, r"tokens\s+—")
        self.assertNotIn("instead of 0", out)

    def test_an_empty_window_keeps_its_zeros(self):
        # `—` describes a route that answers in credits instead of tokens. With
        # no responses at all there is no route to describe, so the zero is the
        # truthful reading and the note must not fire.
        _, out = self._run(self.stats.cmd_stats, type("A", (), {})())
        self.assertRegex(out, r"input tokens\s+0\n")
        self.assertNotRegex(out, r"tokens\s+—")
        self.assertNotIn("instead of 0", out)

    def test_models_dash_only_the_row_that_reports_no_token(self):
        self.insert(("m1", "qfmodel", 0, 0, 0, 0, 5.0),
                    ("m2", "ark", 100, 10, 80, 0, None))
        _, out = self._run(self.stats.cmd_models,
                           type("A", (), {"limit": None})())
        lines = {l.split()[0]: l for l in out.splitlines()
                 if l.strip().startswith(("qfmodel", "ark"))}
        self.assertEqual(len(lines), 2, out)
        self.assertIn("—", lines["qfmodel"])
        self.assertNotIn("—", lines["ark"])
        # ...and the yes/no column is the same fact, so it cannot disagree.
        self.assertTrue(lines["qfmodel"].rstrip().endswith("no"), lines["qfmodel"])
        self.assertTrue(lines["ark"].rstrip().endswith("yes"), lines["ark"])

    def test_show_dashes_one_model_and_its_own_history_rows(self):
        self.insert(("m1", "qfmodel", 0, 0, 0, 0, 5.0),
                    ("m2", "qfmodel", 0, 0, 0, 0, 2.0))
        args = type("A", (), {"kind": "model", "name": "qfmodel", "limit": 5,
                             "range": None})()
        code, out = self._run(self.stats.cmd_show, args)
        self.assertEqual(code, 0)
        # Both halves of the screen: the window summary, which carries the
        # `tokens_reported` count...
        self.assertRegex(out, r"input_tokens\s+—")
        # ...and the per-row history, which does not, so it must fall back to
        # the row's own fields rather than print a structural zero.
        self.assertIn("input_tokens=—", out)
        self.assertNotRegex(out, r"input_tokens\s+0\b")
        # The money axis is a real measurement, so it stays a number.
        self.assertRegex(out, r"credits\s+7\.00")

    def test_a_reported_number_is_never_replaced_by_the_dash(self):
        # The dash is allowed to suppress a zero and nothing else: a renderer
        # that could swallow a real measurement would be worse than the lie it
        # replaced.
        for reported in (True, False):
            self.assertEqual(self.stats.fmt_tok(1, reported), "1")
            self.assertEqual(self.stats.fmt_tok(4_000_000, reported), "4,000,000")
        self.assertEqual(self.stats.fmt_tok(0, True), "0")
        self.assertEqual(self.stats.fmt_tok(0, False), "—")
        self.assertEqual(self.stats.fmt_tok(None, False), "-")

    # --- E1: the same trap on the credits axis -----------------------------

    def _all_credits_carried(self):
        conn = self.t.conn()
        try:
            got = {
                "kpi": db.q_usage_kpi(conn)["credits_carried"],
                "summary": db.q_usage_summary(conn, None, None)["credits_carried"],
                "models": {r["model"]: r["credits_carried"]
                           for r in db.q_models(conn)},
                "stats": {r["model"]: r["credits_carried"] for r in
                          db.q_usage_model_stats(conn)},
                "entity": db.q_entity_summary(conn, "model", "ark",
                                              None, None)["credits_carried"],
            }
        finally:
            conn.close()
        return got

    def test_a_byok_window_carries_no_credits_to_add_up(self):
        # Every BYOK response omits the `credits` field, so `SUM(COALESCE(...))`
        # is a hard 0 that reads "billed nothing". Five aggregates all now say
        # how many rows actually carried the field.
        self.insert(("m1", "ark", 100, 10, 0, 0, None),
                    ("m2", "ark", 200, 20, 0, 0, None))
        counts = self._all_credits_carried()
        for key in ("kpi", "summary", "entity"):
            self.assertEqual(counts[key], 0, key)
        self.assertEqual(counts["models"]["ark"], 0)
        self.assertEqual(counts["stats"]["ark"], 0)

    def test_a_mixed_window_keeps_the_credits_it_does_have(self):
        self.insert(("m1", "qfmodel", 0, 0, 0, 0, 5.0),
                    ("m2", "ark", 100, 10, 0, 0, None))
        counts = self._all_credits_carried()
        self.assertEqual(counts["kpi"], 1)
        self.assertEqual(counts["summary"], 1)
        self.assertEqual(counts["models"], {"qfmodel": 1, "ark": 0})

    def test_models_report_a_byok_row_as_absent_credits_not_zero(self):
        self.insert(("m1", "ark", 100, 10, 80, 0, None))
        _, out = self._run(self.stats.cmd_models, type("A", (), {"limit": None})())
        row = [l for l in out.splitlines() if l.strip().startswith("ark")][0]
        # `-` (never billed in this unit) and not `0` (billed nothing).
        self.assertIn(" - ", row)
        self.assertNotRegex(row, r"\b0\.00\b")

    def test_stats_reports_an_unbilled_window_as_absent_credits(self):
        self.insert(("m1", "ark", 100, 10, 80, 0, None))
        _, out = self._run(self.stats.cmd_stats, type("A", (), {})())
        self.assertRegex(out, r"credits\s+-\n")

    def test_an_absent_credit_total_is_dash_shaped_not_a_swallowed_number(self):
        for carried in (True, False):
            self.assertEqual(self.stats.fmt_credit_total(5.0, carried), "5.00")
        self.assertEqual(self.stats.fmt_credit_total(0, 1), "0")
        self.assertEqual(self.stats.fmt_credit_total(0, 0), "-")
        self.assertEqual(self.stats.fmt_credit_total(None, 1), "-")


class QueryTest(unittest.TestCase):
    def setUp(self):
        self.t = TempTree(self)
        conn = self.t.conn()
        conn.execute(
            "INSERT INTO model_calls(message_id, session_id, project, ts, model,"
            " provider, input_tokens, output_tokens, cache_read_tokens, credits,"
            " is_sidechain, has_usage) VALUES"
            " ('m1','s1','/a',?, 'qfmodel','builtin', 0,0,0, 5.0, 0,1),"
            " ('m2','s1','/a',?, 'ark-code-latest','Volcano ARK', 100, 10, 80, 0, 0,1),"
            " ('m3','s2','/b',?, 'qfmodel','builtin', 0,0,0, 2.0, 1,1)",
            (ms_on(0), ms_on(0), ms_on(2)))
        conn.execute(
            "INSERT INTO tool_calls(call_id, session_id, project, ts, tool_name,"
            " category, duration_ms, status) VALUES"
            " ('c1','s1','/a',?, 'Bash','builtin',1000,'completed'),"
            " ('c2','s1','/a',?, 'Bash','builtin',3000,'error'),"
            " ('c3','s2','/b',?, 'Read','builtin',500,'completed')",
            (ms_on(0), ms_on(0), ms_on(3)))
        conn.execute(
            "INSERT INTO skill_usage(call_id, skill, plugin, session_id, project, ts,"
            " status) VALUES ('c1','playwright-cli',NULL,'s1','/a',?, 'completed')",
            (ms_on(0),))
        conn.execute(
            "INSERT INTO agent_usage(call_id, agent_type, session_id, project, ts,"
            " status) VALUES ('c9','Explore','s1','/a',?, 'completed')", (ms_on(0),))
        conn.execute(
            "INSERT INTO mcp_usage(call_id, server, tool, admin_op, session_id,"
            " project, ts, status) VALUES"
            " ('c4','node-repl','node_repl','call','s1','/a',?, 'completed')", (ms_on(0),))
        conn.execute(
            "INSERT INTO inventory(kind,name,owner_plugin,version,path,source) VALUES"
            " ('tool','Bash',NULL,NULL,NULL,'builtin'),"
            " ('tool','never-used',NULL,NULL,NULL,'builtin'),"
            " ('mcp','unused-srv',NULL,NULL,NULL,'user')")
        conn.commit()
        conn.close()

    def test_tools_aggregate_status_and_duration(self):
        conn = self.t.conn()
        try:
            rows = {r["tool_name"]: r for r in db.q_tools(conn)}
        finally:
            conn.close()
        self.assertEqual(rows["Bash"]["calls"], 2)
        self.assertEqual(rows["Bash"]["completed"], 1)
        self.assertEqual(rows["Bash"]["failed"], 1)
        self.assertEqual(rows["Bash"]["avg_ms"], 2000)

    def test_a_tool_with_no_calls_still_lists_with_zero(self):
        conn = self.t.conn()
        try:
            rows = {r["tool_name"]: r for r in db.q_tools(conn)}
        finally:
            conn.close()
        self.assertIn("never-used", rows)
        self.assertEqual(rows["never-used"]["calls"], 0)

    def test_a_window_shrinks_counts_but_never_drops_a_row(self):
        conn = self.t.conn()
        try:
            start, end = db.window_bounds("1d", ms_on(0, 23))
            rows = {r["tool_name"]: r for r in db.q_tools(conn, start_ts=start, end_ts=end)}
        finally:
            conn.close()
        self.assertEqual(rows["Bash"]["calls"], 2)      # both were today
        self.assertEqual(rows["Read"]["calls"], 0)      # three days ago
        self.assertIn("Read", rows)                     # but the row stays

    def test_models_group_by_model_and_provider(self):
        conn = self.t.conn()
        try:
            rows = {(r["model"], r["provider"]): r for r in db.q_models(conn)}
        finally:
            conn.close()
        self.assertEqual(rows[("qfmodel", "builtin")]["requests"], 2)
        self.assertEqual(rows[("qfmodel", "builtin")]["credits"], 7.0)
        self.assertEqual(rows[("qfmodel", "builtin")]["tokens_reported"], 0)
        self.assertEqual(rows[("ark-code-latest", "Volcano ARK")]["tokens_reported"], 1)

    def test_a_server_less_mcp_row_is_not_dropped(self):
        """`m.server = p.server` is never true when both are NULL, which once
        silently discarded every MCP row whose target could not be split."""
        conn = self.t.conn()
        try:
            conn.execute(
                "INSERT INTO mcp_usage(call_id, server, tool, admin_op, session_id,"
                " project, ts, status) VALUES ('c5',NULL,NULL,'list','s1','/a',?, 'completed')",
                (ms_on(0),))
            conn.commit()
            rows = {(r["server"], r["tool"]): r for r in db.q_mcp(conn)}
        finally:
            conn.close()
        self.assertIn((None, None), rows)
        self.assertEqual(rows[(None, None)]["calls"], 1)

    def test_an_installed_but_unused_mcp_server_lists(self):
        conn = self.t.conn()
        try:
            rows = {(r["server"], r["tool"]): r for r in db.q_mcp(conn)}
        finally:
            conn.close()
        self.assertIn(("unused-srv", None), rows)
        self.assertEqual(rows[("unused-srv", None)]["calls"], 0)

    def test_kpi_sums_inside_one_window(self):
        conn = self.t.conn()
        try:
            start, end = db.window_bounds("1d", ms_on(0, 23))
            k = db.q_usage_kpi(conn, start, end)
        finally:
            conn.close()
        self.assertEqual(k["requests"], 2)
        self.assertEqual(k["tool_calls"], 2)   # the third call is three days old
        self.assertEqual(k["credits"], 5.0)
        self.assertEqual(k["skill_calls"], 1)
        self.assertEqual(k["agent_calls"], 1)
        self.assertEqual(k["mcp_calls"], 1)

    def test_kpi_all_time_spans_every_row(self):
        conn = self.t.conn()
        try:
            k = db.q_usage_kpi(conn, None, None)
        finally:
            conn.close()
        self.assertEqual(k["requests"], 3)
        self.assertEqual(k["tool_calls"], 3)
        self.assertEqual(k["credits"], 7.0)

    def test_request_logs_come_back_newest_first(self):
        conn = self.t.conn()
        try:
            rows = db.q_usage_request_logs(conn, None, ms_on(0, 23))
        finally:
            conn.close()
        self.assertEqual(len(rows), 3)
        self.assertGreaterEqual(rows[0]["ts"], rows[-1]["ts"])

    def test_entity_summary_is_windowed(self):
        conn = self.t.conn()
        try:
            start, end = db.window_bounds("1d", ms_on(0, 23))
            summary = db.q_entity_summary(conn, "tool", "Bash", start, end)
        finally:
            conn.close()
        self.assertEqual(summary["calls"], 2)
        self.assertEqual(summary["completed"], 1)

    def test_entity_existence_ignores_the_window_the_summary_uses(self):
        """The two answers `show` must not conflate.

        A windowed aggregate says `calls = 0` for a quiet window **and** for a name
        the index has never seen, and those are different sentences: one is a
        measurement, the other is the tool admitting it has no idea.
        """
        conn = self.t.conn()
        try:
            self.assertTrue(db.q_entity_exists(conn, "tool", "Bash"))
            self.assertEqual(
                db.q_entity_summary(conn, "tool", "Bash", 0, 1)["calls"], 0,
                "a window with no rows should read as zero calls")
            self.assertTrue(db.q_entity_exists(conn, "tool", "Bash"),
                            "existence moved with the window")
            self.assertFalse(db.q_entity_exists(conn, "tool", "NoSuchTool"))
            self.assertEqual(
                db.q_entity_summary(conn, "tool", "NoSuchTool", None, None)["calls"], 0,
                "an unknown name is what a zero report used to look like")
        finally:
            conn.close()

    def test_call_detail_returns_every_column(self):
        conn = self.t.conn()
        try:
            row = db.q_call_detail(conn, "tool", "c1")
        finally:
            conn.close()
        self.assertEqual(row["tool_name"], "Bash")
        for column in ("call_id", "session_id", "project", "ts", "duration_ms",
                       "status", "category", "is_sidechain"):
            self.assertIn(column, row.keys())

    def test_daily_buckets_are_local_days(self):
        conn = self.t.conn()
        try:
            days = db.q_usage_daily(conn, None, ms_on(0, 23))
        finally:
            conn.close()
        self.assertEqual(len(days), 2)
        self.assertEqual(sum(d["calls"] for d in days), 3)

    def test_skills_show_their_plugin_owner(self):
        conn = self.t.conn()
        try:
            conn.execute("INSERT INTO inventory(kind,name,owner_plugin,source)"
                         " VALUES('skill','playwright-cli',NULL,'user')")
            conn.execute("INSERT INTO inventory(kind,name,owner_plugin,source)"
                         " VALUES('skill','owned-thing','demo-plugin','plugin')")
            conn.commit()
            rows = {r["skill"]: r for r in db.q_skills(conn)}
        finally:
            conn.close()
        self.assertIsNone(rows["playwright-cli"]["plugin"])
        self.assertEqual(rows["owned-thing"]["plugin"], "demo-plugin")


class CliReportTest(unittest.TestCase):
    """The headless reports' wording, where a wrong message sends the reader
    after the wrong thing."""

    def setUp(self):
        self.t = TempTree(self)
        import importlib.util
        import sys as _sys
        spec = importlib.util.spec_from_file_location(
            "qcut_stats_cli", str(SCRIPTS / "qcut-stats.py"))
        self.stats = importlib.util.module_from_spec(spec)
        _sys.modules["qcut_stats_cli"] = self.stats
        spec.loader.exec_module(self.stats)

    def _run(self, fn, args):
        import io
        from contextlib import redirect_stdout
        conn = self.t.conn()
        buf = io.StringIO()
        try:
            with redirect_stdout(buf):
                code = fn(conn, args)
        finally:
            conn.close()
        return code, buf.getvalue()

    def test_an_empty_kind_is_not_reported_as_an_empty_inventory(self):
        self.t.write("projects/-work-demo/s1.jsonl", [])
        conn = self.t.conn()
        conn.execute("INSERT INTO inventory(kind,name,source) VALUES('tool','Bash','builtin')")
        conn.commit()
        conn.close()
        args = type("A", (), {"kind": "mcp"})()
        _, out = self._run(self.stats.cmd_inventory, args)
        self.assertIn("no mcp entries", out)
        self.assertNotIn("inventory is empty", out)

    def test_a_truly_empty_inventory_says_to_sync(self):
        self.t.write("projects/-work-demo/s1.jsonl", [])
        args = type("A", (), {"kind": None})()
        _, out = self._run(self.stats.cmd_inventory, args)
        self.assertIn("inventory is empty", out)

    def test_show_renders_timestamps_not_raw_epochs(self):
        self.t.write("projects/-work-demo/s1.jsonl", [])
        conn = self.t.conn()
        conn.execute("INSERT INTO agent_usage(call_id, agent_type, ts, status,"
                     " duration_ms) VALUES('c1','Explore',?, 'completed', 5000)",
                     (ms_on(0),))
        conn.commit()
        conn.close()
        args = type("A", (), {"kind": "agent", "name": "Explore", "limit": 1,
                              "range": None})()
        code, out = self._run(self.stats.cmd_show, args)
        self.assertEqual(code, 0)
        # A 13-digit epoch would mean the value was printed verbatim.
        self.assertNotRegex(out, r"\b1\d{12}\b")
        self.assertIn("last used", out)
        self.assertIn("avg", out)

    def test_show_on_an_entity_with_no_calls_in_the_window_says_so(self):
        self.t.write("projects/-work-demo/s1.jsonl", [])
        conn = self.t.conn()
        conn.execute("INSERT INTO agent_usage(call_id, agent_type, ts, status)"
                     " VALUES('c1','Explore',?, 'completed')", (ms_on(30),))
        conn.commit()
        conn.close()
        args = type("A", (), {"kind": "agent", "name": "Explore", "limit": 5,
                              "range": "1d"})()
        _, out = self._run(self.stats.cmd_show, args)
        self.assertIn("no calls in this window", out)


class SchemaTest(unittest.TestCase):
    def setUp(self):
        self.t = TempTree(self)

    def test_ensure_schema_is_idempotent(self):
        db.migrate(self.t.db_path)
        db.migrate(self.t.db_path)
        conn = self.t.conn()
        try:
            self.assertEqual(db.stored_version(conn), db.SCHEMA_VERSION)
        finally:
            conn.close()

    def test_migrate_refuses_a_newer_schema(self):
        db.migrate(self.t.db_path)
        conn = self.t.conn()
        conn.execute("UPDATE meta SET value='999' WHERE key='schema_version'")
        conn.commit()
        conn.close()
        with self.assertRaises(RuntimeError):
            db.migrate(self.t.db_path)

    def test_reset_empties_the_index_but_keeps_the_schema(self):
        self.t.write("projects/-work-demo/s1.jsonl", [])
        conn = self.t.conn()
        conn.execute("INSERT INTO tool_calls(call_id, tool_name) VALUES('c1','Bash')")
        conn.commit()
        db.reset(conn)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM tool_calls").fetchone()[0], 0)
        self.assertEqual(db.stored_version(conn), db.SCHEMA_VERSION)
        conn.close()

    def test_overview_counts_every_indexed_table(self):
        conn = self.t.conn()
        try:
            counts = db.overview(conn)
        finally:
            conn.close()
        for table in ("sessions", "model_calls", "tool_calls", "skill_usage",
                      "agent_usage", "mcp_usage", "plugin_usage", "inventory"):
            self.assertIn(table, counts)

    def test_a_read_only_open_of_a_missing_file_is_empty_not_an_error(self):
        """`install.sh` runs `qcut health` on a freshly cloned tree, before the
        first sync. SQLite cannot open a missing file `mode=ro`, so this must not
        raise."""
        missing = self.t.root / "never-created.db"
        conn = db.open_db(missing, readonly=True)
        try:
            self.assertEqual(db.stored_version(conn), 0)
            # None, not 0: "the schema is not built" is a different statement
            # from "the table is empty", and `qcut health` prints them apart.
            self.assertIsNone(db.overview(conn)["tool_calls"])
            self.assertEqual(db.q_unparsed(conn), [])
            self.assertEqual(db.q_usage_safe(conn, "SELECT * FROM nope"), [])
        finally:
            conn.close()

    def test_health_on_a_fresh_install_explains_itself(self):
        import importlib.util
        import io
        import sys as _sys
        from contextlib import redirect_stdout
        spec = importlib.util.spec_from_file_location(
            "qcut_stats_fresh", str(SCRIPTS / "qcut-stats.py"))
        stats = importlib.util.module_from_spec(spec)
        _sys.modules["qcut_stats_fresh"] = stats
        spec.loader.exec_module(stats)
        args = type("A", (), {"db": str(self.t.root / "absent.db")})()
        conn = db.open_db(self.t.root / "absent.db", readonly=True)
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = stats.cmd_health(conn, args)
        conn.close()
        out = buf.getvalue()
        self.assertEqual(code, 0)
        self.assertIn("not created yet", out)
        self.assertIn("qcut sync", out)

    def test_a_snapshot_can_be_listed_and_restored(self):
        conn = self.t.conn()
        conn.execute("INSERT INTO tool_calls(call_id, tool_name) VALUES('c1','Bash')")
        conn.commit()
        path = db.snapshot(conn, "test", self.t.db_path)
        conn.execute("DELETE FROM tool_calls")
        conn.commit()
        conn.close()
        self.assertTrue(path.is_file())
        self.assertIn(path, db.list_backups(self.t.db_path))
        db.restore(path, self.t.db_path)
        conn = self.t.conn()
        try:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM tool_calls").fetchone()[0], 1)
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
