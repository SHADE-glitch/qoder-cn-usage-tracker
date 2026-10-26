"""Storage-layer tests: the calendar-day windows, the query helpers, the schema.

A hand-built database in a temp directory — no transcript parsing, so a failure
here points at the storage layer and nowhere else.
"""

from __future__ import annotations

import ast
import unittest
from datetime import datetime, timedelta

# `_helpers` puts scripts/ on sys.path, so it must be imported before qcut_db.
from _helpers import SCRIPTS, TempTree

import qcut_db as db
import _hermetic  # noqa: E402,F401 -- tripwire: the suite must never open the real database


def ms_on(days_ago=0, hour=12):
    day = datetime.now().replace(hour=hour, minute=0, second=0, microsecond=0)
    return int((day - timedelta(days=days_ago)).timestamp() * 1000)


class ProviderLabelTest(unittest.TestCase):
    """One Provider column carries the family and the channel name."""

    def test_builtin_third_party_and_unknown(self):
        self.assertEqual(db.provider_label("builtin"), "Built-in")
        self.assertEqual(db.provider_label("Volcano ARK"), "Volcano ARK")
        self.assertEqual(db.provider_label(None), "-")


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
        # The summary no longer carries per-field missing counters; the dash
        # in the panels is driven by `token_covered` alone.
        self.assertNotIn("missing_input", summary)
        self.assertNotIn("missing_output", summary)

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

    A token field of ``0`` is not a measurement: it is what a hosted call writes
    when the CLI ran without ``QODERCN_EXPOSE_TOKEN_USAGE=1``, and neither the
    field nor a sum of them becomes a token count. The decision is
    ``_TOKEN_COVERED_EXPR`` in SQL and ``row_reports_tokens`` in Python — these
    cases hold the two together, because a dash that disagrees with the coverage
    count would be a second way to lie. Credits and tokens are independent axes:
    a hosted call may report both, only credits, or only tokens.
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
        self.insert_providers(*[
            (mid, model, "builtin", i, o, cr, cw, credits)
            for mid, model, i, o, cr, cw, credits in rows])

    def insert_providers(self, *rows):
        """rows: (message_id, model, provider, input, output, cache_read,
        cache_write, credits)"""
        conn = self.t.conn()
        conn.executemany(
            "INSERT INTO model_calls(message_id, session_id, project, ts, model,"
            " provider, input_tokens, output_tokens, cache_read_tokens,"
            " cache_creation_tokens, credits, has_usage) VALUES"
            " (?,?, '/a', ?, ?, ?, ?, ?, ?, ?, ?, 1)",
            [(mid, "s1", ms_on(0), model, provider, i, o, cr, cw, credits)
             for mid, model, provider, i, o, cr, cw, credits in rows])
        conn.commit()
        conn.close()

    def _all_covered_counts(self):
        """The same window through every query that shows a token number."""
        conn = self.t.conn()
        try:
            got = {
                "kpi": db.q_usage_kpi(conn)["token_covered"],
                "summary": db.q_usage_summary(conn, None, None)["token_covered"],
                "models": {r["model"]: r["token_covered"]
                           for r in db.q_models(conn)},
                "entity": db.q_entity_summary(conn, "model", "qfmodel",
                                              None, None)["token_covered"],
            }
        finally:
            conn.close()
        return got

    def test_a_credits_only_row_does_not_count_as_reporting_tokens(self):
        self.insert(("m1", "qfmodel", 0, 0, 0, 0, 5.0))
        counts = self._all_covered_counts()
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
        counts = self._all_covered_counts()
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
                "SELECT message_id, CASE WHEN " + db._TOKEN_COVERED_EXPR +
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
        counts = self._all_covered_counts()
        self.assertEqual(counts["kpi"], 1)
        self.assertEqual(counts["models"], {"qfmodel": 0, "ark": 1})

    def test_the_token_flag_still_separates_measured_from_legacy_rows(self):
        """Legacy hosted, hosted-with-flag, and BYOK in one window.

        Coverage percentages are gone, but ``token_covered`` still decides
        whether a structural zero is drawn as ``—``; the one legacy row must not
        be counted as a route that reported a token.
        """
        self.insert_providers(
            ("legacy", "qfmodel", "builtin", 0, 0, 0, 0, 5.0),
            ("hosted", "qfmodel", "builtin", 100, 10, 80, 0, 1.0),
            ("byok", "ark", "Volcano ARK", 120, 24, 60, 0, None))
        conn = self.t.conn()
        try:
            kpi = db.q_usage_kpi(conn)
            summary = db.q_usage_summary(conn, None, None)
            rows = {(r["model"], r["provider"]): r for r in db.q_models(conn)}
        finally:
            conn.close()
        for data in (kpi, summary):
            self.assertEqual(data["requests"], 3)
            self.assertEqual(data["token_covered"], 2)
        # The token sums cover only the measured requests; the credit sum
        # covers only the rows that carried credits. Neither is a total.
        self.assertEqual(kpi["input_tokens"], 220)
        self.assertEqual(kpi["output_tokens"], 34)
        self.assertEqual(kpi["credits"], 6.0)
        self.assertEqual(rows[("qfmodel", "builtin")]["token_covered"], 1)
        self.assertEqual(rows[("ark", "Volcano ARK")]["token_covered"], 1)

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
        # Coverage statistics and the capture advice are gone from the report.
        self.assertNotIn("coverage", out.lower())
        self.assertNotIn("credits-only", out.lower())
        self.assertNotIn(db.TOKEN_ENV_NAME, out)

    def test_stats_keeps_numbers_where_a_route_reported_tokens(self):
        self.insert(("m1", "ark", 100, 10, 80, 0, None))
        _, out = self._run(self.stats.cmd_stats, type("A", (), {})())
        self.assertRegex(out, r"usage total\s+190")
        self.assertRegex(out, r"input tokens\s+100")
        self.assertRegex(out, r"output tokens\s+10")
        self.assertRegex(out, r"cache read tokens\s+80")
        self.assertNotRegex(out, r"tokens\s+—")
        self.assertNotIn("coverage", out.lower())

    def test_an_empty_window_reports_no_measurement(self):
        # With no responses there is no route to describe: a NULL sum reads `-`,
        # not the `—` that says "this call answered in credits instead of
        # tokens".
        _, out = self._run(self.stats.cmd_stats, type("A", (), {})())
        self.assertRegex(out, r"input tokens\s+-\n")
        self.assertNotRegex(out, r"tokens\s+—")
        self.assertNotIn("coverage", out.lower())

    def test_models_keep_measured_cells_beside_legacy_dashes(self):
        self.insert(("m1", "qfmodel", 0, 0, 0, 0, 5.0),
                    ("m2", "qfmodel", 100, 10, 80, 0, 1.0),
                    ("m3", "ark", 0, 0, 0, 0, None))
        _, out = self._run(self.stats.cmd_models,
                           type("A", (), {"limit": None})())
        lines = {l.split()[0]: l for l in out.splitlines()
                 if l.strip().startswith(("qfmodel", "ark"))}
        self.assertEqual(len(lines), 2, out)
        # A mixed model keeps the token it measured; it no longer carries a
        # percentage saying how many requests that sum describes.
        self.assertNotIn("coverage", out.lower())
        self.assertIn("100", lines["qfmodel"])
        self.assertNotIn("—", lines["qfmodel"])
        # A model with no measurement keeps its dashed tokens and credits.
        self.assertIn("—", lines["ark"])
        self.assertIn(" - ", lines["ark"])

    def test_show_dashes_one_model_and_its_own_history_rows(self):
        self.insert(("m1", "qfmodel", 0, 0, 0, 0, 5.0),
                    ("m2", "qfmodel", 0, 0, 0, 0, 2.0))
        args = type("A", (), {"kind": "model", "name": "qfmodel", "limit": 5,
                             "range": None})()
        code, out = self._run(self.stats.cmd_show, args)
        self.assertEqual(code, 0)
        # The window summary dashes the token rows...
        self.assertRegex(out, r"input_tokens\s+—")
        self.assertNotIn("coverage", out.lower())
        # ...and the per-row history falls back to each row's own fields rather
        # than printing a structural zero.
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
        # `-` (never billed in this unit) and not `0` (billed nothing). The
        # row ends with Last used now; read the credits cell by its neighbours.
        self.assertRegex(row, r"Built-in\s+190\s+-\s+1\s", row)
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
        self.assertEqual(rows[("qfmodel", "builtin")]["token_covered"], 0)
        self.assertNotIn("credits_only", rows[("qfmodel", "builtin")])
        self.assertEqual(rows[("ark-code-latest", "Volcano ARK")]["token_covered"], 1)

    def test_the_builtin_call_count_excludes_skills_and_counts_cancelled(self):
        """The KPI counts builtin calls, not every row in `tool_calls`.

        A Skill/Agent/MCP row lives in `tool_calls` too, so counting the table
        made the Dashboard's "Tool calls" larger than the tools it summarises.
        A cancelled call still counts: it happened.
        """
        conn = self.t.conn()
        conn.executemany(
            "INSERT INTO tool_calls(call_id, session_id, project, ts, tool_name,"
            " category, duration_ms, status) VALUES(?,?,?,?,?,?,?,?)",
            [("c9", "s1", "/a", ms_on(0), "Skill", "skill", 50, "completed"),
             ("c10", "s1", "/a", ms_on(0), "Bash", "builtin", 10, "cancelled")])
        conn.commit()
        try:
            kpi = db.q_usage_kpi(conn)
        finally:
            conn.close()
        self.assertEqual(kpi["builtin_tool_calls"], 4,
                         "the skill row must not count; the cancelled call must")

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

    def test_model_history_and_summary_narrow_to_the_selected_provider(self):
        conn = self.t.conn()
        try:
            conn.execute(
                "INSERT INTO model_calls(message_id, session_id, project, ts, model,"
                " provider, input_tokens, output_tokens, cache_read_tokens,"
                " cache_creation_tokens, credits, has_usage) VALUES"
                " ('m4','s1','/a',?, 'qfmodel','Volcano ARK', 50,5,40,0,NULL,1)",
                (ms_on(1),))
            conn.commit()
            wide = db.q_history(conn, "model", "qfmodel")
            narrowed = db.q_history(conn, "model", "qfmodel",
                                    provider="Volcano ARK")
            summary = db.q_entity_summary(conn, "model", "qfmodel",
                                          provider="Volcano ARK")
        finally:
            conn.close()
        self.assertEqual(len(wide), 3)
        self.assertEqual([r["call_id"] for r in narrowed], ["m4"])
        self.assertEqual(summary["calls"], 1)
        self.assertEqual(summary["token_covered"], 1)
        self.assertEqual(summary["input_tokens"], 50)

    def test_mcp_history_narrows_to_the_selected_tool(self):
        conn = self.t.conn()
        try:
            conn.execute(
                "INSERT INTO mcp_usage(call_id, server, tool, admin_op, session_id,"
                " project, ts, status) VALUES"
                " ('c5','node-repl','js','call','s1','/a',?, 'completed')",
                (ms_on(1),))
            conn.commit()
            server = db.q_history(conn, "mcp", "node-repl")
            tool = db.q_history(conn, "mcp", "node-repl", tool="node_repl")
            summary = db.q_entity_summary(conn, "mcp", "node-repl",
                                          tool="node_repl")
        finally:
            conn.close()
        self.assertEqual({r["call_id"] for r in server}, {"c4", "c5"})
        self.assertEqual([r["call_id"] for r in tool], ["c4"])
        self.assertEqual(summary["calls"], 1)

    def test_commands_rank_by_calls_and_honour_limit(self):
        conn = self.t.conn()
        try:
            conn.executemany(
                "INSERT INTO commands(command, session_id, project, ts)"
                " VALUES (?,?,?,?)",
                [("deploy", "s1", "/a", ms_on(0)),
                 ("deploy", "s2", "/b", ms_on(1)),
                 ("review", "s1", "/a", ms_on(2))])
            conn.commit()
            rows = db.q_commands(conn)
            top = db.q_commands(conn, limit=1)
        finally:
            conn.close()
        self.assertEqual([(r["command"], r["calls"], r["sessions"])
                          for r in rows],
                         [("deploy", 2, 2), ("review", 1, 1)])
        self.assertEqual([r["command"] for r in top], ["deploy"])

    def test_duplicate_inventory_rows_do_not_multiply_plugin_uses(self):
        conn = self.t.conn()
        try:
            conn.executemany(
                "INSERT INTO inventory(kind,name,owner_plugin,version,path,source)"
                " VALUES ('plugin','demo-plugin',?,?,'/p', ?)",
                [(None, "1.2.3", "marketplace"),
                 ("other", "1.2.3", "marketplace")])
            conn.execute(
                "INSERT INTO plugin_usage(plugin, kind, target, session_id,"
                " project, ts) VALUES"
                " ('demo-plugin','skill','demo-skill','s1','/a',?)",
                (ms_on(0),))
            conn.commit()
            row = {r["plugin"]: r for r in db.q_plugins(conn)}["demo-plugin"]
        finally:
            conn.close()
        self.assertEqual(row["uses"], 1)
        self.assertEqual(row["version"], "1.2.3")

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


class DeadQueryTest(unittest.TestCase):
    """A query nothing outside the tests calls is code the suite is feeding.

    `q_usage_daily` and `q_usage_activity` outlived the Dashboard panels they
    were written for — the per-day sparkline and the Runtime row — and the only
    thing keeping them alive was their own test. Removing them is a one-off;
    this guard is what stops the next withdrawn panel from leaving its query
    behind, which is the failure mode a passing test hides.
    """

    PRODUCTION = ("qcut_db.py", "qcut-tui.py", "qcut-sync.py", "qcut-stats.py")

    def test_every_public_query_has_a_caller_outside_the_tests(self):
        body = ast.parse((SCRIPTS / "qcut_db.py").read_text(encoding="utf-8"))
        declared = {node.name for node in body.body
                    if isinstance(node, ast.FunctionDef)
                    and node.name.startswith("q_")}
        self.assertTrue(declared, "no q_* queries found — the scan is looking at the wrong file")
        used = set()
        for name in self.PRODUCTION:
            tree = ast.parse((SCRIPTS / name).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                # `db.q_x(...)` is an Attribute, a call inside qcut_db.py a Name;
                # a def's own name is neither, so it cannot count as its own caller.
                if isinstance(node, ast.Name):
                    used.add(node.id)
                elif isinstance(node, ast.Attribute):
                    used.add(node.attr)
        orphans = sorted(declared - used)
        self.assertEqual(orphans, [],
                         "queries with no caller outside the tests: " + ", ".join(orphans))


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


class LedgerQueryTest(unittest.TestCase):
    """Run cost, project/session ledgers and compactions.

    The links here are explicit `parent_call_id` values, because that is the only
    thing the queries are allowed to use; a seed that expected inference would
    prove the wrong contract.
    """

    def setUp(self):
        self.t = TempTree(self)
        t0, t1 = ms_on(1), ms_on(0)
        self.t0 = t0
        conn = self.t.conn()
        conn.execute(
            "INSERT INTO model_calls(message_id, session_id, project, ts, model,"
            " provider, input_tokens, output_tokens, cache_read_tokens, credits,"
            " context_usage_ratio, is_sidechain, has_usage, parent_call_id,"
            " agent_id) VALUES"
            " ('m1','s1','/a',?, 'qfmodel','builtin', 100,10,80, 0, 0.40, 1,1,"
            "  'a1','ag1'),"
            " ('m2','s1','/a',?, 'qfmodel','builtin', 0,0,0, 2.0, 0.55, 1,1,"
            "  'a1','ag1'),"
            " ('m3','s2','/b',?, 'ark','Volcano ARK', 50,5,0, NULL, 0.10, 0,1,"
            "  NULL,NULL),"
            " ('m4','s2','/b',?, 'qfmodel','builtin', 0,0,0, 1.0, 0.20, 1,1,"
            "  'a2',NULL)",
            (t0, t0, t1, t1))
        conn.execute(
            "INSERT INTO agent_usage(call_id, agent_type, session_id, project, ts,"
            " status) VALUES"
            " ('a1','Explore','s1','/a',?,'completed'),"
            " ('a2','Explore','s2','/b',?,'error')", (t0, t1))
        conn.execute(
            "INSERT INTO compactions(session_id, ts, project, trigger, pre_tokens,"
            " post_tokens, messages_summarized, duration_ms) VALUES"
            " ('s1',?,'/a','auto',1000,200,10,5000),"
            " ('s2',?,'/b','manual',900,100,8,7000)", (t0, t1))
        conn.commit()
        conn.close()

    def q(self, fn, *args, **kw):
        conn = self.t.conn()
        try:
            return fn(conn, *args, **kw)
        finally:
            conn.close()

    def test_agent_runs_aggregate_only_their_linked_responses(self):
        rows = {r["agent_type"]: r for r in self.q(db.q_agents)}
        explore = rows["Explore"]
        self.assertEqual(explore["runs"], 2)
        self.assertEqual(explore["completed"], 1)
        self.assertEqual(explore["requests"], 3)
        self.assertEqual(explore["credits"], 3.0)
        self.assertEqual(explore["token_covered"], 1)
        self.assertEqual(explore["peak_context"], 0.55)
        self.assertIsNotNone(explore["input_tokens"])

    def test_an_unlinked_response_is_not_attributed_to_a_run(self):
        # m3 has no parent and must not leak into Explore's cost.
        self.assertAlmostEqual(
            self.q(db.q_agents)[0]["credits"], 3.0,
            msg="an unlinked BYOK response changed a run's cost")

    def test_a_run_drill_down_lists_its_own_responses_newest_first(self):
        rows = self.q(db.q_agent_run_calls, "a1")
        self.assertEqual([r["call_id"] for r in rows], ["m2", "m1"])

    def test_a_partly_reported_response_does_not_inflate_a_runs_usage_total(self):
        """Usage Total is decided per response, then summed.

        A response that reports output but not input has no total of its own, so
        it contributes nothing. Summing the columns first makes the missing half
        invisible — the run then reports 140 where its rows only justify 110.
        """
        conn = self.t.conn()
        try:
            conn.execute(
                "INSERT INTO model_calls(message_id, session_id, project, ts, model,"
                " provider, input_tokens, output_tokens, cache_read_tokens, credits,"
                " context_usage_ratio, is_sidechain, has_usage, parent_call_id)"
                " VALUES"
                " ('p1','s3','/c',?, 'qfmodel','builtin', 100,10,0, 0, 0.10, 0,1,'a3'),"
                " ('p2','s3','/c',?, 'qfmodel','builtin', NULL,30,0, 0, 0.10, 0,1,'a3')",
                (self.t0, self.t0))
            conn.execute(
                "INSERT INTO agent_usage(call_id, agent_type, session_id, project, ts,"
                " status) VALUES ('a3','Explore','s3','/c',?,'completed')", (self.t0,))
            conn.commit()
            row = [r for r in db.q_history(conn, "agent", "Explore")
                   if r["call_id"] == "a3"][0]
            self.assertEqual(row["usage_total_tokens"], 110,
                             "sum-then-CASE counted the response with no input")
            self.assertEqual(row["requests"], 2)
        finally:
            conn.close()

    def test_a_project_board_carries_cost_and_compaction_tax(self):
        rows = {r["project"]: r for r in self.q(db.q_project_board)}
        self.assertEqual(rows["/a"]["requests"], 2)
        self.assertEqual(rows["/a"]["sessions"], 1)
        self.assertEqual(rows["/a"]["credits"], 2.0)
        self.assertEqual(rows["/a"]["compactions"], 1)
        self.assertEqual(rows["/a"]["compacted_tokens"], 800)
        self.assertEqual(rows["/b"]["requests"], 2)
        self.assertEqual(rows["/b"]["token_covered"], 1)
        self.assertEqual(rows["/b"]["compacted_tokens"], 800)

    def test_a_project_summary_is_windowed_but_existence_is_not(self):
        conn = self.t.conn()
        try:
            self.assertTrue(db.q_project_exists(conn, "/a"))
            self.assertFalse(db.q_project_exists(conn, "/nope"))
            empty = db.q_project_summary(conn, "/a", 0, 1)
            self.assertEqual(empty["requests"], 0)
            self.assertEqual(empty["compactions"], 0)
            self.assertTrue(db.q_project_exists(conn, "/a"),
                            "existence moved with the window")
        finally:
            conn.close()

    def test_a_project_session_ledger_keeps_its_context_pressure(self):
        rows = self.q(db.q_project_sessions, "/a")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["session_id"], "s1")
        self.assertEqual(rows[0]["requests"], 2)
        self.assertEqual(rows[0]["peak_context"], 0.55)
        self.assertEqual(rows[0]["compactions"], 1)

    def test_session_requests_name_the_launching_agent_newest_first(self):
        rows = self.q(db.q_session_calls, "s1")
        self.assertEqual([r["call_id"] for r in rows], ["m2", "m1"])
        self.assertEqual({r["agent_type"] for r in rows}, {"Explore"})

    def test_compaction_summary_splits_trigger_and_counts_context_dropped(self):
        summary = self.q(db.q_compaction_summary)
        self.assertEqual(summary["compactions"], 2)
        self.assertEqual(summary["sessions"], 2)
        self.assertEqual(summary["auto"], 1)
        self.assertEqual(summary["manual"], 1)
        self.assertEqual(summary["compacted_tokens"], 1600)
        self.assertEqual(summary["peak_pre_tokens"], 1000)
        self.assertEqual(summary["avg_ms"], 6000)

    def test_kpi_surfaces_the_new_ledgers(self):
        kpi = self.q(db.q_usage_kpi)
        self.assertEqual(kpi["agent_requests"], 3)
        self.assertEqual(kpi["agent_credits"], 3.0)
        self.assertEqual(kpi["agent_credits_carried"], 3)
        self.assertEqual(kpi["compactions"], 2)
        self.assertEqual(kpi["compacted_tokens"], 1600)
        self.assertEqual(kpi["peak_context"], 0.55)
        self.assertNotIn("first_token_ts", kpi)
        self.assertNotIn("token_coverage", kpi)


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

    def test_an_upgraded_database_widens_the_plugin_key_in_place(self):
        """The widening has to reach an index that already exists.

        A v3 install has ux_plugin_usage over five columns, and
        CREATE UNIQUE INDEX IF NOT EXISTS matches on the name: without the
        rebuild the old shape would survive every upgrade and keep folding
        same-millisecond calls.
        """
        db.migrate(self.t.db_path)
        conn = db.open_db(self.t.db_path)
        try:
            conn.execute("DROP INDEX IF EXISTS ux_plugin_usage")
            conn.execute("ALTER TABLE plugin_usage DROP COLUMN call_id")
            conn.execute(
                "CREATE UNIQUE INDEX ux_plugin_usage"
                " ON plugin_usage(plugin, kind, target, session_id, ts)")
            conn.commit()
        finally:
            conn.close()
        db.migrate(self.t.db_path)
        conn = db.open_db(self.t.db_path)
        try:
            columns = [r[2] for r in conn.execute(
                "PRAGMA index_info(ux_plugin_usage)")]
            self.assertEqual(columns, ["plugin", "kind", "target", "session_id",
                                       "ts", "call_id"])
            for call in ("c1", "c2"):
                conn.execute(
                    "INSERT INTO plugin_usage(call_id, plugin, kind, target,"
                    " session_id, ts) VALUES(?,?,?,?,?,?)",
                    (call, "p", "skill", "s", "s1", 1))
            conn.commit()
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM plugin_usage").fetchone()[0], 2)
        finally:
            conn.close()

    def test_a_v1_database_gains_the_v2_ledger_additively(self):
        """v2 adds two columns and one table; existing rows are left alone.
        Their new columns stay NULL until `qcut sync --full` backfills them,
        which is why the release notes ask for that one full pass."""
        db.migrate(self.t.db_path)
        conn = self.t.conn()
        try:
            conn.execute("DROP INDEX IF EXISTS idx_model_calls_parent")
            conn.execute("ALTER TABLE model_calls DROP COLUMN parent_call_id")
            conn.execute("ALTER TABLE model_calls DROP COLUMN agent_id")
            conn.execute("DROP TABLE compactions")
            conn.execute("UPDATE meta SET value='1' WHERE key='schema_version'")
            conn.commit()
        finally:
            conn.close()
        self.assertEqual(db.migrate(self.t.db_path), "migrated")
        conn = self.t.conn()
        try:
            cols = {r["name"] for r in conn.execute(
                "PRAGMA table_info(model_calls)")}
            self.assertIn("parent_call_id", cols)
            self.assertIn("agent_id", cols)
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM compactions").fetchone()[0], 0)
            conn.execute(
                "INSERT INTO model_calls(message_id, parent_call_id)"
                " VALUES('m1','a1')")
            conn.commit()
            self.assertEqual(conn.execute(
                "SELECT parent_call_id FROM model_calls").fetchone()[0], "a1")
        finally:
            conn.close()

    def test_v3_repairs_commands_skill_names_and_direct_mcp(self):
        """The three blind spots v2 wrote into real indexes.

        A Skill result's `commandName` became a slash command, a
        `<plugin>:<skill>` invocation stayed qualified, and a direct
        `mcp__server__tool` call was counted as a builtin tool. The migration
        repairs all three in place, without touching data v2 got right.
        """
        db.migrate(self.t.db_path)
        conn = self.t.conn()
        try:
            conn.execute(
                "INSERT INTO skill_usage(call_id, skill, plugin, session_id,"
                " project, ts, status) VALUES('k1','qoder-context:knowledge-plan',"
                " NULL,'s1','/a',1,'completed')")
            conn.executemany(
                "INSERT INTO commands(command, session_id, project, ts)"
                " VALUES(?,?,?,?)",
                [("knowledge-plan", "s1", "/a", 1),
                 ("review", "s1", "/a", 2)])
            conn.execute(
                "INSERT INTO plugin_usage(plugin, kind, target, session_id,"
                " project, ts) VALUES('qoder-context','skill',"
                " 'qoder-context:knowledge-plan','s1','/a',1)")
            conn.execute(
                "INSERT INTO tool_calls(call_id, session_id, project, tool_name,"
                " category, ts, duration_ms, status) VALUES('c1','s1','/a',"
                " 'mcp__srv__work','builtin',1,10,'completed')")
            conn.execute("UPDATE meta SET value='2' WHERE key='schema_version'")
            conn.commit()
        finally:
            conn.close()

        self.assertEqual(db.migrate(self.t.db_path), "migrated")
        conn = self.t.conn()
        try:
            self.assertEqual(
                [r["command"] for r in conn.execute(
                    "SELECT command FROM commands ORDER BY ts")],
                ["review"], "the Skill result's commandName survived v3")
            skill = conn.execute("SELECT skill, plugin FROM skill_usage").fetchone()
            self.assertEqual((skill["skill"], skill["plugin"]),
                             ("knowledge-plan", "qoder-context"))
            target = conn.execute(
                "SELECT target FROM plugin_usage").fetchone()["target"]
            self.assertEqual(target, "knowledge-plan")
            self.assertEqual(conn.execute(
                "SELECT category FROM tool_calls").fetchone()["category"],
                "mcp_tool")
            mcp = conn.execute("SELECT * FROM mcp_usage").fetchone()
            self.assertEqual((mcp["server"], mcp["tool"], mcp["admin_op"]),
                             ("srv", "work", "call"))
        finally:
            conn.close()

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
                      "agent_usage", "mcp_usage", "plugin_usage", "compactions",
                      "inventory"):
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
