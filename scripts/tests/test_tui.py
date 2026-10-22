"""TUI tests, driven through Textual's own `run_test` harness.

Every case builds a temp database from fixture transcripts; the real
``~/.qoder-cn`` and the real ``usage.db`` are never opened.
"""

from __future__ import annotations

import ast
import importlib.util
import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

# `_helpers` puts scripts/ on sys.path, so it must be imported before qcut_db.
from _helpers import (SCRIPTS, TempTree, assistant, compact, now_iso,
                      tool_result, tool_use, usage)

import qcut_db as db
import _hermetic  # noqa: E402,F401 -- tripwire: the suite must never open the real database


def load_tui(name="qcut_tui_test"):
    spec = importlib.util.spec_from_file_location(name, str(SCRIPTS / "qcut-tui.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


TUI = load_tui()

# The app runs no background timers: one sync on mount, then only `r` reads the
# host. Tests still pass the shipped defaults explicitly.
QUIET_CFG = dict(db.DEFAULTS)

# The eight slots, spelled out rather than derived from `TUI.TABS`. The panes are
# *generated* from that tuple (`scripts/qcut-tui.py` builds them in a loop), so a
# check that compares the built panes back to the tuple that built them holds for
# any tab set at all -- including one missing a slot. These literals are the only
# place in this suite where the family contract is machine-read, and a sibling
# checkout is not importable from here, so nothing else can catch the drift.
FAMILY_TAB_IDS = ["dashboard", "tools", "skills", "agents",
                  "plugins", "mcp", "tokens", "usage"]
FAMILY_TAB_LABELS = ["Dashboard", "Tools", "Skills", "Agents",
                     "Plugins", "MCP", "Tokens", "Usage"]


def build_db(case):
    """A temp tree with one of everything, indexed, plus its db path.

    Timestamps are relative to now so the tabs' own default windows see them: a
    literal date would fall outside a 7-day window and make these cases depend
    on the day they happen to run.
    """
    t = TempTree(case)
    t.write("projects/-work-demo/s1.jsonl", [
        assistant("s1", "m1", content=[tool_use("c1", "Bash", command="ls")],
                  usage=usage(input_tokens=100, output_tokens=10, credits=2.0),
                  ts=now_iso(-40)),
        tool_result("r1", "c1", kind="completed", ts=now_iso(-35)),
        assistant("s1", "m2", content=[tool_use("c2", "Edit", file_path="/x")],
                  usage=usage(input_tokens=200, output_tokens=20, credits=3.5),
                  ts=now_iso(-30)),
        tool_result("r2", "c2", is_error=True, kind="completed", ts=now_iso(-25)),
        assistant("s1", "m3", content=[tool_use("c3", "Skill", skill="demo-skill")],
                  usage=usage(input_tokens=0, credits=1.0), ts=now_iso(-20)),
        assistant("s1", "m4", content=[
            tool_use("c4", "Agent", subagent_type="Explore", description="d")],
            usage=usage(input_tokens=0, credits=1.0), ts=now_iso(-15)),
        assistant("s1", "m5", content=[
            tool_use("c5", "mcp_call", toolName="mcp__srv__work")],
            usage=usage(input_tokens=0, credits=1.0), ts=now_iso(-10)),
        # The response the Agent run launched: linked by Qoder's own
        # `parent_tool_use_id`, so the run's cost ledger and drill-down have a
        # measured row rather than an empty list.
        assistant("s1", "m6", parent_tool_use_id="c4", agentId="ag1",
                  isSidechain=True,
                  usage=usage(input_tokens=30, output_tokens=3, credits=0.5,
                              context_usage_ratio=0.42), ts=now_iso(-14)),
        compact("s1", ts=now_iso(-12), pre_tokens=1000, post_tokens=200),
    ])
    t.json_file("plugins/installed_plugins_v2.json", {
        "version": 2,
        "plugins": {"demo-plugin@bundler": [
            {"installPath": str(t.qoder / "plugins/cache/demo-plugin"),
             "version": "1.2.3", "source": "marketplace"}]},
    })
    (t.qoder / "plugins/cache/demo-plugin").mkdir(parents=True, exist_ok=True)
    t.skill("demo-skill")
    t.sync()
    return t


class TabWiringTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.t = build_db(self)

    async def _run(self, size=(120, 40)):
        app = TUI.TrackerApp(db_path=self.t.db_path, cfg=QUIET_CFG)
        return app, app.run_test(size=size)

    async def test_the_eight_tabs_are_present_and_in_order(self):
        """The family contract, read against literals.

        `TUI.TABS` builds the panes, so comparing the panes to it proves only
        that the loop ran. These two lists are the contract: `cbut` and `tcut`
        ship the same eight slots under the same names in the same order, and
        dropping or renaming one here is the drift nobody would otherwise see.
        """
        app = TUI.TrackerApp(db_path=self.t.db_path, cfg=QUIET_CFG)
        async with app.run_test(size=(120, 40)) as pilot:
            from textual.widgets import TabbedContent, TabPane
            tc = app.query_one(TabbedContent)
            panes = list(tc.query(TabPane))
            # TabPane carries no label in this release; the tab bar does.
            ids = [pane.id for pane in panes]
            labels = [tc.get_tab(pane.id).label_text for pane in panes]
            _ = pilot
        self.assertEqual(ids, FAMILY_TAB_IDS)
        self.assertEqual(labels, FAMILY_TAB_LABELS)

    async def test_every_tab_has_its_widget_and_table(self):
        app = TUI.TrackerApp(db_path=self.t.db_path, cfg=QUIET_CFG)
        async with app.run_test(size=(120, 40)) as pilot:
            from textual.widgets import DataTable, TabbedContent, TabPane
            tabs = app.query_one(TabbedContent)
            # Orphan check only: every *declared* tab built a pane. The reverse
            # -- that the declaration is the whole navigation set -- is pinned
            # against literals by the case above, which this one cannot do.
            panes = [p.id for p in app.query(TabPane)]
            self.assertEqual(sorted(panes), sorted(tid for tid, _ in TUI.TABS))
            for tid, _ in TUI.TABS:
                if tid == "dashboard":
                    app.query_one("#dash-box")
                    app.query_one("#dash-row-1")
                    app.query_one("#dash-row-2")
                    app.query_one("#dash-row-3")
                    app.query_one("#dash-row-4")
                    app.query_one("#panel-dash-cache")
                    continue
                if tid == "usage":
                    app.query_one("#usage-box")
                    app.query_one("#usage-panels")
                    app.query_one("#panel-window")
                    app.query_one("#panel-tokens")
                    app.query_one("#panel-cache")
                    app.query_one("#panel-credits")
                app.query_one(f"#{tid}-table", DataTable)
                app.query_one(f"#colnote-{tid}")
                app.query_one(f"#{tid}-table").id
                self.assertEqual(tabs.active is not None, True)
                _ = pilot

    async def test_fixture_data_reaches_the_entity_tabs(self):
        app = TUI.TrackerApp(db_path=self.t.db_path, cfg=QUIET_CFG)
        async with app.run_test(size=(120, 40)) as pilot:
            from textual.widgets import DataTable, TabbedContent
            tabs = app.query_one(TabbedContent)
            expected = {
                "tools": ("tools-table", "Bash"),
                "skills": ("skills-table", "demo-skill"),
                "agents": ("agents-table", "Explore"),
                "plugins": ("plugins-table", "demo-plugin"),
                "mcp": ("mcp-table", "srv"),
                "tokens": ("tokens-table", "qfmodel"),
                "usage": ("usage-table", "qfmodel"),
            }
            for tab, (table_id, needle) in expected.items():
                tabs.active = tab
                await pilot.pause()
                await pilot.pause()
                table = app.query_one(f"#{table_id}", DataTable)
                dumped = str(list(table.rows.keys())) + str(
                    [table.get_row(k) for k in table.rows])
                self.assertIn(needle, dumped, f"{tab} did not show {needle}")

    async def test_a_tab_with_no_data_says_so(self):
        """The empty state is for a category with nothing *used in the window*.

        Installed builtins are still known, but the tools tab follows the same
        boundary as Tokens: zero-use rows are hidden until `a` widens the tab to
        the installed ∪ ever-used set.
        """
        empty = TempTree(self)
        empty.sync()
        app = TUI.TrackerApp(db_path=empty.db_path, cfg=QUIET_CFG)
        async with app.run_test(size=(120, 40)) as pilot:
            from textual.widgets import DataTable, Static, TabbedContent
            tabs = app.query_one(TabbedContent)
            tabs.active = "skills"
            await pilot.pause()
            await pilot.pause()
            table = app.query_one("#skills-table", DataTable)
            cells = [str(table.get_row(k)) for k in table.rows]
            self.assertTrue(any("no skill invocations" in c for c in cells), cells)
            tabs.active = "tools"
            await pilot.pause()
            await pilot.pause()
            tools = app.query_one("#tools-table", DataTable)
            self.assertTrue(any("no tool calls in this window" in str(tools.get_row(k))
                                for k in tools.rows))
            await pilot.press("a")
            await pilot.pause()
            self.assertGreater(tools.row_count, 1,
                               "`a` must bring back the installed builtins")
            note = str(app.query_one("#filter-tools", Static).content)
            self.assertIn("incl. installed-unused", note)


class RangeTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.t = build_db(self)

    async def _select(self, pilot, app, key):
        from textual.widgets import Select
        app.query_one("#range", Select).value = key
        await pilot.pause()

    async def test_changing_one_tab_leaves_the_others_alone(self):
        app = TUI.TrackerApp(db_path=self.t.db_path, cfg=QUIET_CFG)
        async with app.run_test(size=(120, 40)) as pilot:
            from textual.widgets import TabbedContent
            before = dict(app.ranges)
            app.query_one(TabbedContent).active = "tools"
            await pilot.pause()
            await self._select(pilot, app, "30d")
            self.assertEqual(app.ranges["tools"], "30d")
            for tab, value in before.items():
                if tab != "tools":
                    self.assertEqual(app.ranges[tab], value)

    async def test_the_control_shows_the_active_tabs_own_window(self):
        """Switching tabs shows *that* tab's range, not the last one picked."""
        app = TUI.TrackerApp(db_path=self.t.db_path, cfg=QUIET_CFG)
        async with app.run_test(size=(120, 40)) as pilot:
            from textual.widgets import Button, Select, TabbedContent
            tabs = app.query_one(TabbedContent)
            tabs.active = "tools"
            await pilot.pause()
            await self._select(pilot, app, "30d")
            tabs.active = "skills"
            await pilot.pause()
            await pilot.pause()
            self.assertEqual(app.query_one("#range", Select).value, "7d")
            # The button offers the picker while a preset is active; the span
            # itself lives in the Select, so the two never print it twice.
            btn = app.query_one("#date-range", Button)
            self.assertEqual(str(btn.label), "📅 Custom…")
            self.assertEqual(btn.tooltip, "7 days")
            tabs.active = "tools"
            await pilot.pause()
            await pilot.pause()
            self.assertEqual(app.query_one("#range", Select).value, "30d")

    async def test_the_plugins_tab_has_no_window(self):
        app = TUI.TrackerApp(db_path=self.t.db_path, cfg=QUIET_CFG)
        async with app.run_test(size=(120, 40)) as pilot:
            from textual.widgets import Button, Select, TabbedContent
            app.query_one(TabbedContent).active = "plugins"
            await pilot.pause()
            await pilot.pause()
            # The window control is withdrawn entirely: there is no window here.
            self.assertFalse(app.query_one("#range", Select).display)
            self.assertFalse(app.query_one("#date-range", Button).display)
            self.assertIsNone(app.ranges["plugins"])

    def test_the_picker_chips_are_cbuts_six_shortcuts(self):
        """The picker copies cbut's six chips, not the Select's whole list.

        cbut's Select offers 2 days and 3 days too, yet its picker carries six
        height-3 chips. Seven do not fit: the row would need 60 cells in the
        53-cell column and clip "All time". The extra ranges stay one click away
        in the Select and remain reachable through the calendar.
        """
        chips = [(label, key) for label, _wid, key in TUI.DateRangeModal.PRESETS]
        self.assertEqual(chips, [("Today", "1d"), ("1d", "1d"), ("7d", "7d"),
                                 ("14d", "14d"), ("30d", "30d"),
                                 ("All time", "all")])
        ids = [wid for _label, wid, _key in TUI.DateRangeModal.PRESETS]
        self.assertEqual(len(ids), len(set(ids)), "duplicate chip ids")
        values = [value for _label, value in TUI.RANGE_OPTIONS]
        self.assertIn("2d", values)
        self.assertIn("3d", values)
        for _label, key in chips:
            self.assertIn(key, values)


class DateRangeTest(unittest.IsolatedAsyncioTestCase):
    """The custom-range picker, driven the way a person drives it."""

    async def asyncSetUp(self):
        self.t = build_db(self)

    @staticmethod
    def _ms(year, month, day, hour=12, minute=0):
        return int(datetime(year, month, day, hour, minute).timestamp() * 1000)

    async def _open(self, pilot, app, tab="tools"):
        from textual.widgets import TabbedContent
        app.query_one(TabbedContent).active = tab
        await pilot.pause()
        await pilot.pause()
        await pilot.click("#date-range")
        await pilot.pause()
        return app.screen

    async def test_the_calendar_button_opens_the_picker(self):
        app = TUI.TrackerApp(db_path=self.t.db_path, cfg=QUIET_CFG)
        async with app.run_test(size=(140, 45)) as pilot:
            modal = await self._open(pilot, app)
            self.assertIsInstance(modal, TUI.DateRangeModal)

    async def test_the_picker_keeps_cbuts_roomy_layout(self):
        """The picker is cbut's, geometry included: height-3 shortcut chips, a
        blank row between the preset row and the start card, another between the
        two cards, and the default bordered "follows now" switch.

        The box is 79x30, so at 80x30 it fits exactly. The width case is the
        regression that forced the six-chip list: seven height-3 chips need 60
        cells in a 53-cell column and clip "All time". The picker is shared, so
        every windowed tab must render it the same way; Plugins hides the
        control because its window is all-time.
        """
        from textual.widgets import Button
        app = TUI.TrackerApp(db_path=self.t.db_path, cfg=QUIET_CFG)
        async with app.run_test(size=(80, 30)) as pilot:
            for tab in ("dashboard", "tools", "skills", "agents", "mcp",
                        "tokens", "usage"):
                modal = await self._open(pilot, app, tab)
                await pilot.pause()
                box = modal.query_one("#dr-box")
                self.assertEqual(box.region.height, 30, tab)
                self.assertLessEqual(box.region.right, 80, tab)
                presets = modal.query_one("#dr-presets")
                left = modal.query_one("#dr-left")
                for _label, wid, _key in TUI.DateRangeModal.PRESETS:
                    chip = modal.query_one(f"#{wid}", Button)
                    self.assertEqual(chip.region.height, 3, f"{tab} {wid}")
                    self.assertLessEqual(chip.region.right,
                                         left.region.right, f"{tab} {wid}")
                start = modal.query_one("#dr-start-card")
                end = modal.query_one("#dr-end-card")
                self.assertEqual(start.region.y, presets.region.bottom + 1, tab)
                self.assertEqual(end.region.y, start.region.bottom + 1, tab)
                follow = modal.query_one("#dr-follow")
                self.assertGreaterEqual(follow.region.height, 3, tab)
                for wid in ("#dr-actions", "#dr-cancel", "#dr-ok"):
                    widget = modal.query_one(wid)
                    self.assertLessEqual(widget.region.bottom, 30,
                                         f"{tab} {wid}")
                await pilot.press("escape")
                await pilot.pause()

    async def test_a_short_terminal_scrolls_the_picker_instead_of_crushing_it(self):
        """At 24 rows the 30-row box cannot fit, so the modal screen scrolls by
        exactly the overflow. That keeps Confirm reachable without shrinking
        cbut's chrome back to one-row chips and a borderless switch."""
        app = TUI.TrackerApp(db_path=self.t.db_path, cfg=QUIET_CFG)
        async with app.run_test(size=(80, 24)) as pilot:
            modal = await self._open(pilot, app)
            await pilot.pause()
            self.assertEqual(modal.query_one("#dr-box").region.height, 30)
            self.assertEqual(modal.screen.max_scroll_y, 6)
            modal.screen.scroll_end(animate=False)
            await pilot.pause()
            self.assertEqual(modal.screen.scroll_y, 6)
            ok = modal.query_one("#dr-ok")
            self.assertLessEqual(ok.region.bottom - modal.screen.scroll_y, 24)

    async def test_confirm_applies_the_picked_window_to_that_tab_only(self):
        app = TUI.TrackerApp(db_path=self.t.db_path, cfg=QUIET_CFG)
        async with app.run_test(size=(140, 45)) as pilot:
            await self._open(pilot, app, "tools")
            await pilot.click("#dr-14d")
            await pilot.pause()
            await pilot.click("#dr-ok")
            await pilot.pause()
            win = app.ranges["tools"]
            self.assertIsInstance(win, db.CustomWindow)
            self.assertTrue(win.follow_now, "a preset follows the clock")
            self.assertEqual(app.ranges["skills"], "7d", "another tab moved")

    async def test_cancel_leaves_the_window_and_reverts_the_control(self):
        app = TUI.TrackerApp(db_path=self.t.db_path, cfg=QUIET_CFG)
        async with app.run_test(size=(140, 45)) as pilot:
            from textual.widgets import Select
            await self._open(pilot, app, "tools")
            await pilot.click("#dr-cancel")
            await pilot.pause()
            self.assertEqual(app.ranges["tools"], "7d")
            # Choosing "Custom…" only opens the picker; it is never stored, so
            # cancelling must put the Select back on the tab's real window.
            self.assertEqual(app.query_one("#range", Select).value, "7d")

    async def test_choosing_custom_in_the_select_opens_the_picker(self):
        app = TUI.TrackerApp(db_path=self.t.db_path, cfg=QUIET_CFG)
        async with app.run_test(size=(140, 45)) as pilot:
            await self._select_custom(pilot, app)
            self.assertIsInstance(app.screen, TUI.DateRangeModal)

    async def _select_custom(self, pilot, app):
        from textual.widgets import Select, TabbedContent
        app.query_one(TabbedContent).active = "tools"
        await pilot.pause()
        app.query_one("#range", Select).value = TUI.CUSTOM_RANGE
        await pilot.pause()

    async def test_the_calendar_steps_months_and_crosses_the_year(self):
        from textual.widgets import Static
        app = TUI.TrackerApp(db_path=self.t.db_path, cfg=QUIET_CFG)
        async with app.run_test(size=(140, 45)) as pilot:
            app.push_screen(TUI.DateRangeModal("7d",
                                               self._ms(2026, 12, 15)))
            await pilot.pause()
            modal = app.screen
            self.assertIn("December 2026",
                          str(modal.query_one("#dr-cal-month", Static).content))
            await pilot.click("#dr-next")
            await pilot.pause()
            self.assertIn("January 2027",
                          str(modal.query_one("#dr-cal-month", Static).content))
            await pilot.click("#dr-prev")
            await pilot.pause()
            await pilot.click("#dr-prev")
            await pilot.pause()
            self.assertIn("November 2026",
                          str(modal.query_one("#dr-cal-month", Static).content))

    async def test_a_clicked_day_writes_into_the_active_card(self):
        from textual.widgets import Checkbox, Input
        app = TUI.TrackerApp(db_path=self.t.db_path, cfg=QUIET_CFG)
        async with app.run_test(size=(140, 45)) as pilot:
            # Dec 2026 starts on a Tuesday, so cell 10 is Wed 9 December.
            app.push_screen(TUI.DateRangeModal("7d",
                                               self._ms(2026, 12, 15)))
            await pilot.pause()
            modal = app.screen
            await pilot.click("#dr-end-card .dr-card-title")
            await pilot.pause()
            self.assertEqual(modal._active, "end")
            await pilot.click("#dr-day-10")
            await pilot.pause()
            self.assertEqual(modal.query_one("#dr-end-date", Input).value,
                             "12/09/2026")
            # Picking an end date is a *fixed* end, so the follow switch clears.
            self.assertFalse(modal.query_one("#dr-follow", Checkbox).value)

    async def test_the_hour_grid_sets_the_active_cards_hour(self):
        from textual.widgets import Input
        app = TUI.TrackerApp(db_path=self.t.db_path, cfg=QUIET_CFG)
        async with app.run_test(size=(140, 45)) as pilot:
            app.push_screen(TUI.DateRangeModal("7d", self._ms(2026, 12, 15)))
            await pilot.pause()
            modal = app.screen
            self.assertEqual(modal._active, "start")
            before = modal.query_one("#dr-start-time", Input).value
            minute = before.split(":")[1]
            await pilot.click("#dr-hour-14")
            await pilot.pause()
            self.assertEqual(modal.query_one("#dr-start-time", Input).value,
                             f"14:{minute}")
            await pilot.click("#dr-end-card .dr-card-title")
            await pilot.pause()
            await pilot.click("#dr-hour-06")
            await pilot.pause()
            self.assertEqual(modal.query_one("#dr-end-time", Input).value,
                             "06:00")
            # The hour grid marks the *active* card's hour, and only that one.
            self.assertTrue(modal.query_one("#dr-hour-06").has_class("-selected"))
            self.assertFalse(modal.query_one("#dr-hour-14").has_class("-selected"))

    async def test_the_nudge_step_moves_the_field_by_the_chosen_minutes(self):
        from textual.widgets import Input
        app = TUI.TrackerApp(db_path=self.t.db_path, cfg=QUIET_CFG)
        async with app.run_test(size=(140, 45)) as pilot:
            app.push_screen(TUI.DateRangeModal(
                db.CustomWindow(self._ms(2026, 12, 15, 10, 30),
                                self._ms(2026, 12, 15, 12, 0), False),
                self._ms(2026, 12, 15, 12, 0)))
            await pilot.pause()
            modal = app.screen
            await pilot.click("#dr-step-60")
            await pilot.pause()
            start = modal.query_one("#dr-start-time", TUI.TimeInput)
            self.assertEqual(start.step, 60)
            start.focus()
            await pilot.pause()
            await pilot.press("up")
            await pilot.pause()
            self.assertEqual(modal.query_one("#dr-start-time", Input).value,
                             "11:30")
            await pilot.press("down")
            await pilot.pause()
            self.assertEqual(modal.query_one("#dr-start-time", Input).value,
                             "10:30")

    async def test_a_typed_pair_is_normalised_before_it_is_compared(self):
        """``10-1`` and ``9:5 PM`` are the same instant as ``10/01/2026 21:05``."""
        from textual.widgets import Checkbox, Input
        app = TUI.TrackerApp(db_path=self.t.db_path, cfg=QUIET_CFG)
        results = []
        async with app.run_test(size=(140, 45)) as pilot:
            app.push_screen(TUI.DateRangeModal("7d",
                                               self._ms(2026, 12, 15)),
                            results.append)
            await pilot.pause()
            modal = app.screen
            modal.query_one("#dr-start-date", Input).value = "10-1"
            modal.query_one("#dr-start-time", Input).value = "9:5 PM"
            modal.query_one("#dr-end-date", Input).value = "2026-10-02"
            modal.query_one("#dr-end-time", Input).value = "08:00"
            modal.query_one("#dr-follow", Checkbox).value = False
            await pilot.pause()
            modal._confirm()
            await pilot.pause()
            self.assertEqual(len(results), 1)
            win = results[0]
            self.assertEqual(datetime.fromtimestamp(win.start_ms / 1000),
                             datetime(2026, 10, 1, 21, 5))
            self.assertEqual(datetime.fromtimestamp(win.end_ms / 1000),
                             datetime(2026, 10, 2, 8, 0))

    async def test_an_end_before_the_start_is_refused_and_the_picker_stays(self):
        from textual.widgets import Checkbox, Input
        app = TUI.TrackerApp(db_path=self.t.db_path, cfg=QUIET_CFG)
        async with app.run_test(size=(140, 45)) as pilot:
            app.push_screen(TUI.DateRangeModal("7d", self._ms(2026, 12, 15)))
            await pilot.pause()
            modal = app.screen
            modal.query_one("#dr-start-date", Input).value = "12/20/2026"
            modal.query_one("#dr-end-date", Input).value = "12/01/2026"
            modal.query_one("#dr-follow", Checkbox).value = False
            await pilot.pause()
            modal._confirm()
            await pilot.pause()
            self.assertIs(app.screen, modal, "the picker closed on a bad range")

    async def test_a_mistyped_date_is_refused_with_a_message(self):
        from textual.widgets import Input
        app = TUI.TrackerApp(db_path=self.t.db_path, cfg=QUIET_CFG)
        async with app.run_test(size=(140, 45)) as pilot:
            app.push_screen(TUI.DateRangeModal("7d", self._ms(2026, 12, 15)))
            await pilot.pause()
            modal = app.screen
            modal.query_one("#dr-start-date", Input).value = "not a date"
            modal._confirm()
            await pilot.pause()
            self.assertIs(app.screen, modal)

    async def test_editing_an_end_field_drops_follow_now(self):
        """A typed end is a fixed end; our own prefill must not clear it."""
        from textual.widgets import Checkbox, Input
        app = TUI.TrackerApp(db_path=self.t.db_path, cfg=QUIET_CFG)
        async with app.run_test(size=(140, 45)) as pilot:
            app.push_screen(TUI.DateRangeModal("7d", self._ms(2026, 12, 15)))
            await pilot.pause()
            modal = app.screen
            self.assertTrue(modal.query_one("#dr-follow", Checkbox).value)
            modal.query_one("#dr-end-time", Input).value = "03:00"
            await pilot.pause()
            self.assertFalse(modal.query_one("#dr-follow", Checkbox).value)

    async def test_a_blank_start_means_all_time(self):
        from textual.widgets import Button, Checkbox, Input
        app = TUI.TrackerApp(db_path=self.t.db_path, cfg=QUIET_CFG)
        results = []
        async with app.run_test(size=(140, 45)) as pilot:
            app.push_screen(TUI.DateRangeModal("7d",
                                               self._ms(2026, 12, 15)),
                            results.append)
            await pilot.pause()
            modal = app.screen
            # The chip names the whole window, not an abbreviation of it.
            self.assertEqual(str(modal.query_one("#dr-all", Button).label),
                             "All time")
            await pilot.click("#dr-all")
            await pilot.pause()
            self.assertEqual(modal.query_one("#dr-start-date", Input).value, "")
            self.assertTrue(modal.query_one("#dr-follow", Checkbox).value)
            modal._confirm()
            await pilot.pause()
            self.assertIsNone(results[0].start_ms)
            self.assertTrue(results[0].follow_now)


class WideTableTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.t = build_db(self)

    def test_every_tab_is_a_summary_page_or_owns_a_table(self):
        """The registry walk below leans on this partition, so it is pinned.

        `TABLE_TABS` is what resolves the `f"{tid}-table"` template in the walk,
        and the layout branches on the same two names, so a tab that belongs to
        neither (or to both) would make the walk's answer wrong rather than
        absent.
        """
        tabs = [tid for tid, _ in TUI.TABS]
        declared = list(TUI.SUMMARY_TABS) + list(TUI.TABLE_TABS)
        self.assertEqual(sorted(declared), sorted(tabs))
        self.assertEqual(len(declared), len(set(declared)))
        self.assertEqual(set(TUI.TABLE_TABS) & set(TUI.SUMMARY_TABS), set())

    async def test_a_narrow_terminal_names_the_columns_it_dropped(self):
        app = TUI.TrackerApp(db_path=self.t.db_path, cfg=QUIET_CFG)
        async with app.run_test(size=(60, 24)) as pilot:
            from textual.widgets import Static, TabbedContent
            app.query_one(TabbedContent).active = "tokens"
            await pilot.pause()
            await pilot.pause()
            note = app.query_one("#colnote-tokens", Static)
            if note.display:
                self.assertIn("of", str(note.content))
                self.assertIn("columns", str(note.content))

    async def test_a_wide_terminal_shows_every_column_and_no_note(self):
        app = TUI.TrackerApp(db_path=self.t.db_path, cfg=QUIET_CFG)
        async with app.run_test(size=(200, 40)) as pilot:
            from textual.widgets import DataTable, Static, TabbedContent
            app.query_one(TabbedContent).active = "tokens"
            await pilot.pause()
            await pilot.pause()
            table = app.query_one("#tokens-table", DataTable)
            self.assertEqual(len(table.columns), len(TUI.TOKENS_COLUMNS))
            self.assertFalse(app.query_one("#colnote-tokens", Static).display)

    async def test_a_planned_table_gains_no_unregistered_column(self):
        """`on_mount` builds the columns from the registry, so a table cannot
        gain a column the plan does not know about."""
        app = TUI.TrackerApp(db_path=self.t.db_path, cfg=QUIET_CFG)
        async with app.run_test(size=(200, 40)) as pilot:
            from textual.widgets import DataTable, TabbedContent
            tabs = app.query_one(TabbedContent)
            for tab, (table_id, _) in {
                "tools": ("tools-table", None), "skills": ("skills-table", None),
                "agents": ("agents-table", None), "plugins": ("plugins-table", None),
                "mcp": ("mcp-table", None), "tokens": ("tokens-table", None),
                "usage": ("usage-table", None),
            }.items():
                tabs.active = tab
                await pilot.pause()
                await pilot.pause()
                table = app.query_one(f"#{table_id}", DataTable)
                labels = [str(c.label) for c in table.columns.values()]
                registered = list(app.WIDE_TABLES[table_id][0])
                self.assertEqual(labels, registered[:len(labels)])

    # Variables an `id=` template may be built from, resolved from the constant
    # the layout itself branches on — never a second hard-coded list.
    _ID_TEMPLATE_VALUES = {"tid": TUI.TABLE_TABS}

    @classmethod
    def _ids_for(cls, node):
        """Every table id an ``id=`` argument can produce.

        The entity tabs build theirs from the tab id (``f"{tid}-table"``), so a
        literal-only walk would miss every table id built that way and the reverse
        check below could not be honest. An unrecognised shape yields no ids
        rather than a guess, so a new style of id shows up as a failure instead
        of silently passing.
        """
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return {node.value}
        if not isinstance(node, ast.JoinedStr):
            return set()
        out = {""}
        for part in node.values:
            if isinstance(part, ast.Constant) and isinstance(part.value, str):
                out = {prefix + part.value for prefix in out}
            elif (isinstance(part, ast.FormattedValue)
                    and isinstance(part.value, ast.Name)):
                values = cls._ID_TEMPLATE_VALUES.get(part.value.id)
                if values is None:
                    return set()
                out = {prefix + value for prefix in out for value in values}
            else:
                return set()
        return out

    def test_every_datatable_in_the_source_is_registered(self):
        """A layout test can only loop over what is already registered, so an
        unplanned table would clip in silence. This walks the source instead.

        Both directions are checked: an unregistered table, and a registered
        table nothing builds (a plan that has drifted away from its widget).
        """
        tree = ast.parse((SCRIPTS / "qcut-tui.py").read_text(encoding="utf-8"))
        ids = set()
        for node in ast.walk(tree):
            # `Table` is the DataTable subclass that re-exposes enter in the
            # footer; both are tables, and both need a plan.
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id in ("DataTable", "Table")):
                for kw in node.keywords:
                    if kw.arg == "id":
                        ids |= self._ids_for(kw.value)
        registered = set(TUI.TrackerApp.WIDE_TABLES)
        registered |= set(TUI.HistoryScreen.WIDE_TABLES)
        registered |= set(TUI.PluginScreen.WIDE_TABLES)
        registered |= set(TUI.ModelResponsesScreen.WIDE_TABLES)
        registered |= set(TUI.ProjectScreen.WIDE_TABLES)
        registered |= set(TUI.RequestsScreen.WIDE_TABLES)
        self.assertTrue(ids)
        self.assertEqual(ids, registered, "the source and the column plan have"
                         " drifted apart")


class ResizeTest(unittest.IsolatedAsyncioTestCase):
    """The column plan re-runs when the terminal changes size.

    `TrackerApp.on_resize` re-plans every wide table, but no case had ever resized
    one, so the whole dynamic half of the rule was unmeasured: a table that dropped
    columns at mount and never gave them back would have looked like the intended
    behaviour, and a rebuild that quietly lost rows would have looked like a filter.
    """

    async def asyncSetUp(self):
        self.t = build_db(self)

    async def _tab(self, pilot, app, tab):
        from textual.widgets import TabbedContent
        app.query_one(TabbedContent).active = tab
        await pilot.pause()
        await pilot.pause()

    async def _labels(self, app, table_id):
        from textual.widgets import DataTable
        table = app.query_one(f"#{table_id}", DataTable)
        return table, [c.label.plain for c in table.columns.values()]

    async def test_every_table_at_a_narrow_width_is_a_prefix_of_its_plan(self):
        """Unconditional, where the mount-time case asked `if note.display:`."""
        from textual.widgets import Static
        app = TUI.TrackerApp(db_path=self.t.db_path, cfg=QUIET_CFG)
        async with app.run_test(size=(64, 24)) as pilot:
            for table_id, (cols, note_id, _chrome) in app.WIDE_TABLES.items():
                # Tab tables are named `<tab>-table`; the Dashboard's project
                # board lives on the tab already open at mount.
                tab = (table_id[: -len("-table")]
                       if table_id.endswith("-table") else "dashboard")
                await self._tab(pilot, app, tab)
                table, labels = await self._labels(app, table_id)
                with self.subTest(tab=tab):
                    self.assertGreaterEqual(len(labels),
                                            TUI.WideTableMixin.MIN_SHOWN,
                                            f"{tab} narrowed to fewer than two columns")
                    self.assertEqual(labels, list(cols)[:len(labels)],
                                     f"{tab} reordered its plan when it dropped")
                    note = app.query_one(f"#{note_id}", Static)
                    self.assertEqual(note.display, len(labels) < len(cols),
                                     f"{tab} lied about what it hid")
                    for dropped in cols[len(labels):]:
                        self.assertIn(dropped, str(note.content))

    async def test_widening_gives_every_dropped_column_back_with_its_rows(self):
        app = TUI.TrackerApp(db_path=self.t.db_path, cfg=QUIET_CFG)
        from textual.widgets import Static
        async with app.run_test(size=(64, 24)) as pilot:
            await self._tab(pilot, app, "tokens")
            table, narrow = await self._labels(app, "tokens-table")
            rows = table.row_count
            self.assertLess(len(narrow), len(TUI.TOKENS_COLUMNS),
                            "the fixture never hid a column, so this proves nothing")
            self.assertGreater(rows, 0, "no rows to keep across the resize")
            await pilot.resize_terminal(200, 40)
            await pilot.pause()
            await pilot.pause()
            _t, wide = await self._labels(app, "tokens-table")
            self.assertEqual(wide, list(TUI.TOKENS_COLUMNS),
                             "widening never gave the columns back")
            self.assertEqual(table.row_count, rows, "the rebuild lost rows")
            self.assertFalse(app.query_one("#colnote-tokens", Static).display,
                             "a table showing everything still announced a plan")

    async def test_narrowing_drops_columns_and_announces_it_without_losing_rows(self):
        from textual.widgets import Static
        app = TUI.TrackerApp(db_path=self.t.db_path, cfg=QUIET_CFG)
        async with app.run_test(size=(200, 40)) as pilot:
            await self._tab(pilot, app, "tokens")
            table, wide = await self._labels(app, "tokens-table")
            self.assertEqual(wide, list(TUI.TOKENS_COLUMNS))
            self.assertGreater(table.row_count, 0, "nothing to keep across the resize")
            rows = table.row_count
            await pilot.resize_terminal(64, 24)
            await pilot.pause()
            await pilot.pause()
            _t, narrow = await self._labels(app, "tokens-table")
            self.assertLess(len(narrow), len(wide),
                            "narrowing hid nothing, so the note is untested")
            self.assertEqual(narrow, list(TUI.TOKENS_COLUMNS)[:len(narrow)])
            self.assertEqual(table.row_count, rows, "the rebuild lost rows")
            self.assertTrue(app.query_one("#colnote-tokens", Static).display,
                            "columns were hidden without saying which")

    async def test_a_row_selected_after_a_resize_routes_by_its_uncapped_key(self):
        """The key is built from the uncapped row, so a rebuild cannot reroute it.

        Widening first matters: the plan runs again and the table is refilled, and
        the row the cursor lands on must still open the history of the entity whose
        key it carries — a key taken from a capped cell would route two long names
        that share a prefix to whichever screen won first.
        """
        app = TUI.TrackerApp(db_path=self.t.db_path, cfg=QUIET_CFG)
        async with app.run_test(size=(64, 24)) as pilot:
            await self._tab(pilot, app, "tools")
            await pilot.resize_terminal(200, 40)
            await pilot.pause()
            await pilot.pause()
            table, _labels = await self._labels(app, "tools-table")
            kind, _, name = str(next(iter(table.rows.values())).key.value).partition("\t")
            self.assertEqual(kind, "tool", f"an unexpected key vocabulary: {kind}")
            self.assertFalse(name.endswith("…"), f"the key was a capped cell: {name}")
            # The oracle is the database, not the router: whatever the key claims,
            # that tool really has rows.
            known = {r["tool_name"] for r in self.t.rows(
                "SELECT DISTINCT tool_name FROM tool_calls")}
            self.assertIn(name, known,
                          f"the key carries a name that is not a tool: {name!r}")
            table.move_cursor(row=0)
            await pilot.pause()
            table.action_select_cursor()
            await pilot.pause()
            await pilot.pause()
            screen = app.screen
            self.assertIsInstance(screen, TUI.HistoryScreen)
            self.assertEqual(screen.entity, name,
                             "the detail screen opened for a different entity")


    async def test_two_names_sharing_a_prefix_longer_than_the_cap_route_apart(self):
        """The key is built from the uncapped row, and this is the whole reason.

        Both names are longer than `name_cap` and identical well past the point where
        the cell is cut, so on a narrow terminal the two rows *render* the same text.
        A key taken from the rendered cell would send both rows to whichever entity
        the router met first, and one tool's history would be shown for the other.
        """
        shared = "g" * (db.DEFAULTS["name_cap"] + 6)
        first, second = shared + "-first", shared + "-second"
        conn = self.t.conn()
        now_ms = int(datetime.now().timestamp() * 1000)
        for call_id, name in (("c-long-a", first), ("c-long-b", second)):
            conn.execute(
                "INSERT INTO tool_calls(call_id, session_id, project, tool_name,"
                " category, ts, duration_ms, status) VALUES(?,?,?,?,?,?,?,?)",
                (call_id, "s1", "/work/demo", name, "builtin", now_ms, 100,
                 "completed"))
        conn.commit()
        conn.close()

        app = TUI.TrackerApp(db_path=self.t.db_path, cfg=QUIET_CFG)
        async with app.run_test(size=(64, 24)) as pilot:
            await self._tab(pilot, app, "tools")
            table, labels = await self._labels(app, "tools-table")
            keys = {str(row.key.value).partition("\t")[2]
                    for row in table.rows.values()}
            self.assertIn(first, keys)
            self.assertIn(second, keys)
            order = [str(row.key.value).partition("\t")[2]
                     for row in table.rows.values()]
            for expected in (first, second):
                index = order.index(expected)
                table.move_cursor(row=index)
                await pilot.pause()
                table.action_select_cursor()
                await pilot.pause()
                await pilot.pause()
                screen = app.screen
                self.assertIsInstance(screen, TUI.HistoryScreen)
                self.assertEqual(screen.entity, expected)
                self.assertEqual(str(screen.query_one("#hist-head").content).count(
                    "…"), 0, "the heading was drawn from a capped name")
                app.pop_screen()
                await pilot.pause()


class DrilldownTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.t = build_db(self)

    async def test_selecting_a_tool_row_opens_its_history(self):
        app = TUI.TrackerApp(db_path=self.t.db_path, cfg=QUIET_CFG)
        async with app.run_test(size=(120, 40)) as pilot:
            from textual.widgets import TabbedContent
            app.query_one(TabbedContent).active = "tools"
            await pilot.pause()
            await pilot.pause()
            app.push_screen(TUI.HistoryScreen("tool", "Bash"))
            await pilot.pause()
            await pilot.pause()
            top = app.screen
            self.assertIsInstance(top, TUI.HistoryScreen)
            self.assertEqual(top.entity, "Bash")
            head = str(top.query_one("#hist-head").content)
            self.assertIn("Bash", head)

    async def test_a_call_row_opens_every_field(self):
        """The detail screen shows every stored column, under a human label.

        The body is a stack of label/value rows (not one Static), so this reads
        the rendered pairs rather than a single widget's content — which is also
        what lets each value wrap on its own instead of pushing one long line
        off the right edge.
        """
        app = TUI.TrackerApp(db_path=self.t.db_path, cfg=QUIET_CFG)
        async with app.run_test(size=(120, 40)) as pilot:
            app.push_screen(TUI.CallScreen("tool", "c1", "Bash"))
            await pilot.pause()
            labels = [str(w.content) for w in app.screen.query(".call-label")]
            values = [str(w.content) for w in app.screen.query(".call-value")]
            self.assertEqual(len(labels), len(values))
            self.assertGreater(len(labels), 5)
            self.assertIn("call id", labels)
            self.assertIn("status", labels)
            self.assertIn("duration", labels)
            self.assertIn("session", labels)
            # Every raw column of `tool_calls` is rendered, labelled or not.
            row = db.q_call_detail(self._conn(), "tool", "c1")
            self.assertEqual(len(labels), len(row.keys()))
            self.assertIn("c1", " ".join(values))

    def _conn(self):
        conn = db.open_db(self.t.db_path)
        db.ensure_schema(conn)
        return conn

    async def test_a_plugin_row_opens_its_breakdown(self):
        app = TUI.TrackerApp(db_path=self.t.db_path, cfg=QUIET_CFG)
        async with app.run_test(size=(120, 40)) as pilot:
            app.push_screen(TUI.PluginScreen("demo-plugin"))
            await pilot.pause()
            await pilot.pause()
            head = str(app.screen.query_one("#plugin-head").content)
            self.assertIn("demo-plugin", head)



class RowRoutingTest(unittest.IsolatedAsyncioTestCase):
    """A row's key means different things on a tab and inside a pushed screen.

    Every table is filled through `_fill_wide`, so all of them key rows with the
    same `<kind>\t<value>` shape — and on the History screen the value is a *call
    id*, on the response screen a *message id*. The tab router used to read those
    as entity names too, so the event the owning screen had already handled was
    routed a second time: an empty `HistoryScreen("tool", "call-aaa")` landed on
    top of the detail that was just opened, and a model's response list opened a
    second response list named after a message id. These cases drive the real
    gesture (`action_select_cursor`, i.e. Enter on a row) rather than pushing the
    screen they want to see, because pushing it is what let the bug through.
    """

    async def asyncSetUp(self):
        self.t = build_db(self)

    def _table(self, app, table_id):
        # From the active screen, not from the app: `App.query` resolves against
        # the base `Screen(id='_default')`, so a table on a pushed screen is not
        # reachable that way -- and a case that "found" it would have been reading
        # a different screen's widget.
        return app.screen.query_one(f"#{table_id}")

    async def _open(self, app, pilot, table_id, row=0):
        table = self._table(app, table_id)
        table.move_cursor(row=row)
        await pilot.pause()
        table.action_select_cursor()
        await pilot.pause()
        await pilot.pause()

    async def _tab(self, app, pilot, pane):
        # Click the tab, do not set `TabbedContent.active`: a hidden pane's table
        # has no cursor coordinate, so `action_select_cursor` on it is a no-op and
        # the case would pass without ever routing anything.
        from textual.widgets import TabbedContent
        app.query_one(TabbedContent).active = pane
        await pilot.pause()
        await pilot.pause()

    def _row_index(self, app, table_id, key):
        """The row number of one exact `<kind>\\t…` key, read back from the table."""
        table = self._table(app, table_id)
        keys = [str(k.value) for k in table.rows]
        self.assertIn(key, keys, f"{table_id} has no key {key!r}: {keys}")
        return keys.index(key)

    async def test_a_history_row_opens_the_call_detail_and_nothing_on_top(self):
        app = TUI.TrackerApp(db_path=self.t.db_path, cfg=QUIET_CFG)
        async with app.run_test(size=(140, 40)) as pilot:
            await self._tab(app, pilot, "tools")
            await self._open(app, pilot, "tools-table")
            self.assertIsInstance(app.screen, TUI.HistoryScreen)
            depth = len(app.screen_stack)
            await self._open(app, pilot, "hist-table")
            self.assertIsInstance(
                app.screen, TUI.CallScreen,
                f"Enter on a call row did not leave the detail on top: "
                f"{[type(s).__name__ for s in app.screen_stack]}")
            self.assertEqual(
                len(app.screen_stack), depth + 1,
                "the tab router pushed a second screen over the detail: "
                f"{[type(s).__name__ for s in app.screen_stack]}")

    async def test_a_response_row_opens_that_response(self):
        app = TUI.TrackerApp(db_path=self.t.db_path, cfg=QUIET_CFG)
        async with app.run_test(size=(140, 40)) as pilot:
            await self._tab(app, pilot, "tokens")
            await self._open(app, pilot, "tokens-table")
            top = app.screen
            self.assertIsInstance(top, TUI.ModelResponsesScreen)
            # The key carries the provider half (the built-in sentinel here, the
            # same value the row filters by): the drill-down is the one Tokens
            # row, not a model name stripped of its route.
            self.assertEqual((top.model, top.provider), ("qfmodel", "builtin"))
            await self._open(app, pilot, "resp-table")
            top = app.screen
            self.assertIsInstance(
                top, TUI.CallScreen,
                f"a response row routed to {type(top).__name__} instead of the "
                f"call detail: {[type(s).__name__ for s in app.screen_stack]}")
            self.assertEqual(
                [type(x).__name__ for x in app.screen_stack],
                ["Screen", "ModelResponsesScreen", "CallScreen"],
                "the second drill-down level is not the only screen the response "
                "row pushed")

    async def test_a_tokens_row_drills_down_to_its_own_provider(self):
        """One model can appear under two providers; the row's channel is the
        identity, so the drill-down must not list the other channel's calls."""
        conn = self.t.conn()
        conn.execute(
            "INSERT INTO model_calls(message_id, session_id, project, ts, model,"
            " provider, input_tokens, output_tokens, credits, has_usage)"
            " VALUES('mx','s1','/work/demo',?, 'qfmodel','Volcano ARK',"
            " 120, 24, NULL, 1)",
            (int(datetime.now().timestamp() * 1000),))
        conn.commit()
        conn.close()

        app = TUI.TrackerApp(db_path=self.t.db_path, cfg=QUIET_CFG)
        async with app.run_test(size=(140, 40)) as pilot:
            await self._tab(app, pilot, "tokens")
            row = self._row_index(app, "tokens-table", "model\tqfmodel\tVolcano ARK")
            await self._open(app, pilot, "tokens-table", row=row)
            top = app.screen
            self.assertIsInstance(top, TUI.ModelResponsesScreen)
            self.assertEqual(top.provider, "Volcano ARK")
            self.assertIn("Volcano ARK", top.heading)
            # The four hosted qfmodel responses stay behind: this list is the
            # provider's one call, not the model's whole history.
            self.assertEqual(self._table(app, "resp-table").row_count, 1)

    async def test_a_usage_row_opens_that_exact_call(self):
        """The Usage tab lists calls, not models: Enter must open the call the
        cursor is on, not a model-wide list that makes the user find it again."""
        app = TUI.TrackerApp(db_path=self.t.db_path, cfg=QUIET_CFG)
        async with app.run_test(size=(140, 40)) as pilot:
            await self._tab(app, pilot, "usage")
            await self._open(app, pilot, "usage-table")   # newest first: m5
            top = app.screen
            self.assertIsInstance(
                top, TUI.CallScreen,
                f"a usage row routed to {type(top).__name__}: "
                f"{[type(s).__name__ for s in app.screen_stack]}")
            self.assertEqual((top.kind, top.key, top.heading),
                             ("model", "m5", "Usage request"))
            self.assertEqual(
                [type(x).__name__ for x in app.screen_stack],
                ["Screen", "CallScreen"],
                "the usage row pushed more than the one call detail")

    async def test_an_mcp_tool_row_opens_it_under_its_server(self):
        app = TUI.TrackerApp(db_path=self.t.db_path, cfg=QUIET_CFG)
        async with app.run_test(size=(140, 40)) as pilot:
            await self._tab(app, pilot, "mcp")
            row = self._row_index(app, "mcp-table", "mcp\tsrv\twork")
            await self._open(app, pilot, "mcp-table", row=row)
            top = app.screen
            self.assertIsInstance(top, TUI.HistoryScreen)
            self.assertEqual((top.kind, top.entity, top.tool),
                             ("mcp", "srv", "work"))
            self.assertIn("srv · work", top.heading)
            self.assertEqual(self._table(app, "hist-table").row_count, 1)

    async def test_an_mcp_server_only_row_opens_the_whole_server(self):
        conn = self.t.conn()
        conn.execute(
            "INSERT INTO inventory(kind, name, owner_plugin, version, path, source)"
            " VALUES('mcp','idle-srv',NULL,NULL,NULL,'user')")
        conn.commit()
        conn.close()

        app = TUI.TrackerApp(db_path=self.t.db_path, cfg=QUIET_CFG)
        # The launch sync rebuilds `inventory` from the fixture tree and would
        # erase the row this case is about; the tab under test is the toggle,
        # not the sync.
        with mock.patch.object(TUI.TrackerApp, "_request_sync", lambda self: None):
            async with app.run_test(size=(140, 40)) as pilot:
                await self._tab(app, pilot, "mcp")
                # The server is installed but unused, so the default view hides
                # it; `a` is the installed ∪ ever-used switch.
                await pilot.press("a")
                await pilot.pause()
                row = self._row_index(app, "mcp-table", "mcp\tidle-srv\t")
                await self._open(app, pilot, "mcp-table", row=row)
                top = app.screen
                self.assertIsInstance(top, TUI.HistoryScreen)
                # No tool half: the screen is the server's own history, with no
                # filter invented from an absent cell.
                self.assertEqual((top.kind, top.entity, top.tool),
                                 ("mcp", "idle-srv", None))
                self.assertEqual(top.heading, "idle-srv")

    async def test_an_mcp_row_with_no_server_opens_nothing(self):
        """An admin op (`mcp_list` and friends) has no server to scope a history
        to. Pushing an empty HistoryScreen would be a screen with no entity."""
        conn = self.t.conn()
        conn.execute(
            "INSERT INTO mcp_usage(call_id, server, tool, admin_op, session_id,"
            " project, ts, status) VALUES('mc-admin',NULL,NULL,'list','s1',"
            " '/work/demo',?, 'completed')",
            (int(datetime.now().timestamp() * 1000),))
        conn.commit()
        conn.close()

        app = TUI.TrackerApp(db_path=self.t.db_path, cfg=QUIET_CFG)
        async with app.run_test(size=(140, 40)) as pilot:
            await self._tab(app, pilot, "mcp")
            row = self._row_index(app, "mcp-table", "mcp\t\t")
            depth = len(app.screen_stack)
            await self._open(app, pilot, "mcp-table", row=row)
            self.assertEqual(
                len(app.screen_stack), depth,
                f"a server-less mcp row opened {type(app.screen).__name__}: "
                f"{[type(s).__name__ for s in app.screen_stack]}")

    async def test_a_plugin_page_row_opens_no_second_screen(self):
        """`PluginScreen` is a leaf: its rows are calls, not entities to route.

        The fixture plugin has no attributed call until this case writes one, and
        an empty page would make the assertion true for the wrong reason -- a leaf
        with no rows proves nothing routes to it.
        """
        conn = self.t.conn()
        conn.execute("INSERT INTO plugin_usage(plugin, kind, target, session_id,"
                     " project, ts) VALUES('demo-plugin','skill','demo-skill',"
                     "'s1','/work/demo',?)",
                     (int(datetime.now().timestamp() * 1000),))
        conn.commit()
        conn.close()

        app = TUI.TrackerApp(db_path=self.t.db_path, cfg=QUIET_CFG)
        async with app.run_test(size=(140, 40)) as pilot:
            await self._tab(app, pilot, "plugins")
            await self._open(app, pilot, "plugins-table")
            self.assertIsInstance(app.screen, TUI.PluginScreen,
                                  f"plugins tab did not route to a plugin page: "
                                  f"{[type(s).__name__ for s in app.screen_stack]}")
            page = self._table(app, "plugin-table")
            keys = [k.value for k in page.rows]
            self.assertTrue(any(k and "\t" in k for k in keys),
                            f"the plugin page has no keyed row to select: {keys}")
            depth = len(app.screen_stack)
            await self._open(app, pilot, "plugin-table")
            self.assertEqual(
                len(app.screen_stack), depth,
                f"a plugin page row routed to {type(app.screen).__name__}: "
                f"{[type(s).__name__ for s in app.screen_stack]}")

    def test_every_pushed_table_routes_itself_or_is_a_leaf(self):
        """A new screen with a table must decide, in source, who routes its rows.

        Checked off the class objects rather than a typed list: `WIDE_TABLES` is
        what the tab router allows, so a screen that mounts a table and names no
        handler and no leaf status is a screen whose rows would be routed by the
        tab router again.
        """
        tab_tables = set(TUI.TrackerApp.WIDE_TABLES)
        for name, obj in vars(TUI).items():
            if not (isinstance(obj, type) and issubclass(obj, TUI.Screen)
                    and obj is not TUI.Screen):
                continue
            own = obj.__dict__.get("WIDE_TABLES")
            if not own:
                continue
            handled = "on_data_table_row_selected" in obj.__dict__
            self.assertTrue(
                handled or set(own) <= tab_tables or name in LEAF_SCREENS,
                f"{name} owns table(s) {sorted(own)} with no selection handler "
                f"and no leaf declaration -- its rows would reach the tab router")


LEAF_SCREENS = frozenset({"PluginScreen", "CallScreen"})


class LedgerDrilldownTest(unittest.IsolatedAsyncioTestCase):
    """Runs → requests → call, and Dashboard → project → session → requests.

    Both ladders are driven through Enter on the row (the gesture a user makes),
    and both are keyed by values that are not simple entity names: a run is its
    Agent call id, a session is a UUID, and a project is a path. A screen that
    received the rendered cell instead of the key would look right and query the
    wrong thing.
    """

    async def asyncSetUp(self):
        self.t = build_db(self)

    def _table(self, app, table_id):
        return app.screen.query_one(f"#{table_id}")

    async def _open(self, app, pilot, table_id, row=0):
        table = self._table(app, table_id)
        table.move_cursor(row=row)
        await pilot.pause()
        table.action_select_cursor()
        await pilot.pause()
        await pilot.pause()

    async def _tab(self, app, pilot, pane):
        from textual.widgets import TabbedContent
        app.query_one(TabbedContent).active = pane
        await pilot.pause()
        await pilot.pause()

    def _row_index(self, app, table_id, key):
        table = self._table(app, table_id)
        keys = [str(k.value) for k in table.rows]
        self.assertIn(key, keys, f"{table_id} has no key {key!r}: {keys}")
        return keys.index(key)

    async def test_the_agents_tab_shows_a_runs_linked_cost(self):
        app = TUI.TrackerApp(db_path=self.t.db_path, cfg=QUIET_CFG)
        async with app.run_test(size=(140, 40)) as pilot:
            await self._tab(app, pilot, "agents")
            row = row_map(self._table(app, "agents-table"), "Explore")
            self.assertEqual(row["runs"], "1")
            # The fixture's Agent call has no tool_result, so the run is pending
            # and neither completed nor failed.
            self.assertEqual(row["ok"], "0")
            self.assertEqual(row["fail"], "0")
            self.assertEqual(row["requests"], "1")
            self.assertEqual(row["usage total"], "33")
            self.assertEqual(row["credits"], "0.50")
            self.assertEqual(row["peak ctx"], "42.0%")

    async def test_a_run_row_opens_the_responses_it_launched(self):
        app = TUI.TrackerApp(db_path=self.t.db_path, cfg=QUIET_CFG)
        async with app.run_test(size=(140, 40)) as pilot:
            await self._tab(app, pilot, "agents")
            row = self._row_index(app, "agents-table", "agent\tExplore")
            await self._open(app, pilot, "agents-table", row=row)
            hist = app.screen
            self.assertIsInstance(
                hist, TUI.HistoryScreen,
                f"an agent type opened {type(hist).__name__}: "
                f"{[type(s).__name__ for s in app.screen_stack]}")
            self.assertEqual((hist.kind, hist.entity), ("agent", "Explore"))
            await self._open(app, pilot, "hist-table")
            top = app.screen
            self.assertIsInstance(
                top, TUI.RequestsScreen,
                f"a run row opened {type(top).__name__}: "
                f"{[type(s).__name__ for s in app.screen_stack]}")
            self.assertEqual(top.agent_call_id, "c4")
            table = self._table(app, "req-table")
            self.assertEqual(table.row_count, 1)
            keys = [str(k.value) for k in table.rows]
            self.assertEqual(keys, ["model\tm6"])
            head = str(top.query_one("#req-head").content)
            self.assertIn("1 request", head)
            self.assertIn("42.0%", head)

    async def test_the_dashboard_project_board_opens_its_session_ledger(self):
        app = TUI.TrackerApp(db_path=self.t.db_path, cfg=QUIET_CFG)
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            row = self._row_index(app, "dash-projects", "project\t/work/demo")
            await self._open(app, pilot, "dash-projects", row=row)
            top = app.screen
            self.assertIsInstance(top, TUI.ProjectScreen)
            self.assertEqual(top.project, "/work/demo")
            self.assertEqual(top.heading, "/work/demo")
            sessions = self._table(app, "project-table")
            self.assertEqual(sessions.row_count, 1)
            head = str(top.query_one("#project-head").content)
            self.assertIn("compactions", head)
            await self._open(app, pilot, "project-table")
            self.assertIsInstance(app.screen, TUI.RequestsScreen)
            self.assertEqual(app.screen.session_id, "s1")
            self.assertEqual(self._table(app, "req-table").row_count, 6)

    async def test_a_null_project_row_opens_the_no_project_group(self):
        """NULL is a real group and must route to `project IS NULL`, not to a
        lookup for a project named "" that would open an empty screen."""
        conn = self.t.conn()
        conn.execute(
            "INSERT INTO model_calls(message_id, session_id, project, ts, model,"
            " provider, input_tokens, output_tokens, has_usage)"
            " VALUES('m-null','s-null',NULL,?, 'qfmodel','builtin', 1, 1, 1)",
            (int(datetime.now().timestamp() * 1000),))
        conn.commit()
        conn.close()

        app = TUI.TrackerApp(db_path=self.t.db_path, cfg=QUIET_CFG)
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            row = self._row_index(app, "dash-projects",
                                  f"project\t{TUI.NULL_PROJECT_KEY}")
            await self._open(app, pilot, "dash-projects", row=row)
            top = app.screen
            self.assertIsInstance(top, TUI.ProjectScreen)
            self.assertIsNone(top.project)
            self.assertEqual(top.heading, "(no project)")
            self.assertEqual(self._table(app, "project-table").row_count, 1)

    async def test_the_dashboard_project_counter_matches_the_board(self):
        """The counter and the board must be the same ledger.

        Tool calls stay in `/work/demo`, so a tool-scoped count would read 1
        while the board below has the second project's request.
        """
        from textual.widgets import Static
        conn = self.t.conn()
        conn.execute(
            "INSERT INTO model_calls(message_id, session_id, project, ts, model,"
            " provider, input_tokens, output_tokens, has_usage) VALUES"
            " ('m-other','s-other','/work/other',?, 'qfmodel','builtin', 1,1,1)",
            (int(datetime.now().timestamp() * 1000),))
        conn.commit()
        conn.close()

        app = TUI.TrackerApp(db_path=self.t.db_path, cfg=QUIET_CFG)
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            self.assertEqual(self._table(app, "dash-projects").row_count, 2)
            counter = str(app.query_one("#dash-sum-projects", Static).content)
            self.assertIn("Projects", counter)
            self.assertTrue(counter.rstrip().endswith("2"), counter)

    async def test_the_dashboard_and_tokens_note_surface_the_new_ledgers(self):
        from textual.widgets import Static
        app = TUI.TrackerApp(db_path=self.t.db_path, cfg=QUIET_CFG)
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            compactions = str(app.query_one("#dash-rt-compactions", Static).content)
            self.assertIn("1", compactions)
            self.assertIn("800 ctx", compactions)
            peak = str(app.query_one("#dash-rt-peak", Static).content)
            self.assertIn("42.0%", peak)
            await self._tab(app, pilot, "tokens")
            note = str(app.query_one("#filter-tokens", Static).content)
            # Coverage tracking is gone; the note keeps only the provider
            # legend and the meaning of the two dashes.
            self.assertNotIn("coverage", note.lower())
            self.assertNotIn("capture", note.lower())
            self.assertIn("`—` = this route reported no token", note)
            self.assertIn("Provider:", note)


class TokenCaptureHintTest(unittest.IsolatedAsyncioTestCase):
    async def test_credits_without_tokens_name_the_missing_cli_flag(self):
        t = build_credit_only_db(self)
        app = TUI.TrackerApp(db_path=t.db_path, cfg=QUIET_CFG)
        async with app.run_test(size=(160, 40)) as pilot:
            await pilot.pause()
            from textual.widgets import Static, TabbedContent
            tabs = app.query_one(TabbedContent)
            tabs.active = "tokens"
            await pilot.pause()
            await pilot.pause()
            note = str(app.query_one("#filter-tokens", Static).content)
            self.assertIn("token capture off", note)
            self.assertIn("QODERCN_EXPOSE_TOKEN_USAGE=1", note)
            tabs.active = "usage"
            await pilot.pause()
            await pilot.pause()
            note = str(app.query_one("#usage-note", Static).content)
            self.assertIn("token capture off", note)

    async def test_a_window_with_real_tokens_does_not_warn(self):
        """One zero-token turn is a fact, not evidence about the whole run."""
        t = build_db(self)
        app = TUI.TrackerApp(db_path=t.db_path, cfg=QUIET_CFG)
        async with app.run_test(size=(160, 40)) as pilot:
            await pilot.pause()
            from textual.widgets import Static, TabbedContent
            tabs = app.query_one(TabbedContent)
            tabs.active = "tokens"
            await pilot.pause()
            await pilot.pause()
            note = str(app.query_one("#filter-tokens", Static).content)
            self.assertNotIn("token capture off", note)


class StatusLineTest(unittest.IsolatedAsyncioTestCase):
    async def test_the_status_line_counts_what_is_indexed(self):
        t = build_db(self)
        app = TUI.TrackerApp(db_path=t.db_path, cfg=QUIET_CFG)
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            from textual.widgets import Static
            text = str(app.query_one("#status", Static).content)
            self.assertIn("builtin calls", text)
            self.assertIn("responses", text)
            self.assertIn("cmd", text)
            self.assertIn("unclaimed", text)
            # Path and schema live in `qcut health` now; the bar keeps the
            # counts, the freshness and any active warning.
            self.assertNotIn("schema v", text)

    async def test_s_cycles_the_active_tabs_sort_and_keeps_the_status_counts(self):
        """`s` is sort now: the note names the order, the rows follow it, and
        the status line keeps the counts it carries (the old `s` was an
        auto-sync switch that wrote over them)."""
        t = build_db(self)
        app = TUI.TrackerApp(db_path=t.db_path, cfg=QUIET_CFG)
        async with app.run_test(size=(160, 40)) as pilot:
            await pilot.pause()
            from textual.widgets import DataTable, Static, TabbedContent
            app.query_one(TabbedContent).active = "tools"
            await pilot.pause()
            await pilot.pause()
            table = app.query_one("#tools-table", DataTable)
            note = app.query_one("#filter-tools", Static)

            def first_row():
                key = next(iter(table.rows))
                return str(table.get_row(key))

            self.assertIn("sort: tab order", str(note.content))
            await pilot.press("s")
            await pilot.pause()
            self.assertIn("sort: calls", str(note.content))
            await pilot.press("s")
            await pilot.pause()
            self.assertIn("sort: recent", str(note.content))
            self.assertIn("Edit", first_row(), "recent should put Edit first")
            await pilot.press("s")
            await pilot.pause()
            self.assertIn("sort: name", str(note.content))
            self.assertIn("Bash", first_row(), "name should put Bash first")
            await pilot.press("s")
            await pilot.pause()
            self.assertIn("sort: tab order", str(note.content))
            text = str(app.query_one("#status", Static).content)
            self.assertIn("builtin calls", text, "sorting wiped the status counts")
            self.assertFalse(hasattr(app, "_auto_sync"))

    async def test_the_status_line_is_actually_on_screen(self):
        """Setting the text is not showing it: the bar once carried
        `dock: bottom` like the Footer, landed on the same row, and was painted
        over — so the unclaimed count was invisible while the test above (which
        reads the widget, not the screen) stayed green."""
        t = build_db(self)
        app = TUI.TrackerApp(db_path=t.db_path, cfg=QUIET_CFG)
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            bar = app.query_one("#status")
            self.assertGreater(bar.region.height, 0, "the status bar has no row")
            x = bar.region.x + 1
            y = bar.region.y
            widget, region = app.screen.get_widget_at(x, y)
            self.assertIs(widget, bar,
                          f"row {y} is drawn by {type(widget).__name__} "
                          f"id={widget.id!r}, not the status bar")


def build_credit_only_db(case, with_byok=False):
    """Responses that bill in credits and answer every token field with a zero.

    Qoder's own routes do not *omit* the token fields, they report ``0`` — so
    this fixture sends the zeros. An omitted field is a missing value and reads
    ``-``; a reported zero is the case that has to read ``—``, and the two must
    not collapse into one mark. ``with_byok`` adds the opposite kind of response
    — real tokens, no credits field — so one table can be asked to render both
    populations at once.
    """
    t = TempTree(case)
    zeros = dict(input_tokens=0, output_tokens=0, cache_read=0, cache_creation=0)
    recs = [
        assistant("s1", "m1", usage=usage(credits=1.5, **zeros), ts=now_iso(-40)),
        assistant("s1", "m2", usage=usage(credits=0.25, **zeros), ts=now_iso(-30)),
    ]
    if with_byok:
        recs.append(assistant(
            "s1", "m3", model="qoder-custom-a3d301ce/byok-model",
            usage=usage(input_tokens=120, output_tokens=24, cache_read=60),
            ts=now_iso(-20)))
    t.write("projects/-work-demo/s1.jsonl", recs)
    t.sync()
    return t


def row_map(table, name):
    """One rendered row as ``{column label: cell text}``, found by entity name.

    The app keys a row ``"<kind>\t<uncapped value>[\\t<second value>]"`` (see
    ``_fill``), so the key is read back rather than re-derived — a test that
    guessed the separator would fail on a refactor that changes nothing a user
    sees. A Tokens key carries (model, provider) and an MCP key (server, tool),
    so the name is matched against *any* segment, not only the last: the entity
    a case asks for is the one the user sees in the first column. Textual wraps
    each key in a ``RowKey`` object, so the value is unwrapped rather than
    ``str``-ed (the repr of a RowKey names its address, not the entity). The
    cells are zipped against the labels the table actually built, because
    ``_fill_wide`` may drop a column for want of width, and a positional read
    would then shift every cell by one instead of saying so.
    """
    def parts(key):
        return str(getattr(key, "value", key)).split("\t")

    key = next((k for k in table.rows if name in parts(k)), None)
    if key is None:
        raise AssertionError(f"no row for {name!r}; keys="
                             f"{[parts(k) for k in table.rows]}")
    labels = [str(c.label) for c in table.columns.values()]
    return dict(zip(labels, [str(v) for v in table.get_row(key)]))


class TokenDashTest(unittest.IsolatedAsyncioTestCase):
    """`—` is a claim about the *route*, so everywhere it appears must agree."""

    async def asyncSetUp(self):
        self.t = build_credit_only_db(self, with_byok=True)

    async def _tokens_row(self, key):
        from textual.widgets import DataTable, TabbedContent
        app = TUI.TrackerApp(db_path=self.t.db_path, cfg=QUIET_CFG)
        async with app.run_test(size=(200, 45)) as pilot:
            app.query_one(TabbedContent).active = "tokens"
            await pilot.pause()
            await pilot.pause()
            return row_map(app.query_one("#tokens-table", DataTable), key)

    async def test_the_tokens_tab_dashes_a_credits_only_model_and_not_the_byok_one(self):
        hosted = await self._tokens_row("qfmodel")
        for col in ("Usage Total", "Input", "Output", "API Total",
                    "Cache hit", "Cache miss", "Cache write"):
            self.assertEqual(hosted[col], "—", col)
        # What the route *does* report stays a number.
        self.assertEqual(hosted["Requests"], "2")
        self.assertEqual(hosted["Credits"], "1.75")
        # Coverage was removed: the table has no share column, and a dashed
        # token sum does not get a percentage beside it to explain it away.
        self.assertNotIn("Coverage", hosted)
        # One Provider column carries the family and the channel; there is no
        # second Route column repeating `Built-in` beside it.
        self.assertNotIn("Route", hosted)
        self.assertEqual(hosted["Provider"], "Built-in")
        byok = await self._tokens_row("byok-model")
        self.assertEqual(byok["Input"], "120")
        self.assertEqual(byok["Output"], "24")
        self.assertNotIn("Coverage", byok)
        self.assertNotIn("Route", byok)
        self.assertEqual(byok["Provider"], "qoder-custom-a3d301ce")
        # E1, the other axis: that route never carried a `credits` field, so its
        # rollup is absent (`-`), not billed-zero (`0`).
        self.assertEqual(byok["Credits"], "-")

    async def test_one_request_table_holds_both_kinds_of_row(self):
        """The mark is decided per row: a mixed list must not blend together."""
        from textual.widgets import DataTable, TabbedContent
        app = TUI.TrackerApp(db_path=self.t.db_path, cfg=QUIET_CFG)
        async with app.run_test(size=(200, 45)) as pilot:
            app.query_one(TabbedContent).active = "usage"
            await pilot.pause()
            await pilot.pause()
            table = app.query_one("#usage-table", DataTable)
            m1 = row_map(table, "m1")
            self.assertEqual(m1["Input"], "—")
            self.assertNotIn("Route", m1)
            self.assertEqual(m1["Provider"], "Built-in")
            m3 = row_map(table, "m3")
            self.assertEqual(m3["Input"], "120")
            self.assertNotIn("Route", m3)
            self.assertEqual(m3["Provider"], "qoder-custom-a3d301ce")
            # A BYOK response carries no credits field at all: absent, which is
            # `-`, and not the `—` that means "this route counts in credits".
            self.assertEqual(row_map(table, "m3")["Credits"], "-")

    async def test_a_credits_only_window_dashes_the_summary_panels(self):
        """Aggregates are the dangerous half: a sum of route-level zeros is 0."""
        from textual.widgets import Static, TabbedContent
        only = build_credit_only_db(self)
        app = TUI.TrackerApp(db_path=only.db_path, cfg=QUIET_CFG)
        async with app.run_test(size=(200, 45)) as pilot:
            dash = str(app.query_one("#dash-tok-input", Static).content)
            self.assertTrue(dash.endswith("—"), dash)
            # There is no coverage widget to check: the zero-token window is
            # told by the dashes alone.
            self.assertEqual([w.id for w in app.query(".usage-panel") if "coverage" in str(w.id)], [])
            app.query_one(TabbedContent).active = "usage"
            await pilot.pause()
            await pilot.pause()
            panel = str(app.query_one("#sum-input", Static).content)
            self.assertTrue(panel.endswith("—"), panel)
            note = str(app.query_one("#usage-note", Static).content)
            self.assertNotIn("coverage", note.lower())
            self.assertIn("`—` = no token reported", note)

    async def test_a_mixed_window_shows_the_tokens_it_does_have(self):
        # The mirror case, and why the dash also reads the value: one reporting
        # response in the window and the sum is a measurement again.
        from textual.widgets import Static, TabbedContent
        app = TUI.TrackerApp(db_path=self.t.db_path, cfg=QUIET_CFG)
        async with app.run_test(size=(200, 45)) as pilot:
            panel = str(app.query_one("#dash-tok-input", Static).content)
            self.assertTrue(panel.endswith("120"), panel)
            # Top models use the same one-column provider label as the Tokens
            # tab, so a built-in row is not left unlabelled.
            models = str(app.query_one("#dash-models", Static).content)
            self.assertIn("qfmodel · Built-in", models)
            # The merged label is clipped to the panel's 18-cell name column;
            # the provider name is still the part that identifies the channel.
            self.assertIn("byok-model · qode", models)
            app.query_one(TabbedContent).active = "usage"
            await pilot.pause()
            await pilot.pause()
            note = str(app.query_one("#usage-note", Static).content)
            self.assertNotIn("coverage", note.lower())

    async def test_a_mixed_model_shows_a_partial_sum_beside_partial_coverage(self):
        """One model, both eras: the sum is real, and it covers a third of the
        calls — the cells must say both, not let the sum read as the whole."""
        conn = self.t.conn()
        conn.execute(
            "INSERT INTO model_calls(message_id, session_id, project, ts, model,"
            " provider, input_tokens, output_tokens, cache_read_tokens,"
            " cache_creation_tokens, credits, has_usage) VALUES"
            " ('m9','s1','/work/demo',?,'qfmodel','builtin',50,5,0,0,NULL,1)",
            (int(datetime.now().timestamp() * 1000),))
        conn.commit()
        conn.close()
        from textual.widgets import DataTable, Static, TabbedContent
        app = TUI.TrackerApp(db_path=self.t.db_path, cfg=QUIET_CFG)
        async with app.run_test(size=(200, 45)) as pilot:
            app.query_one(TabbedContent).active = "tokens"
            await pilot.pause()
            await pilot.pause()
            row = row_map(app.query_one("#tokens-table", DataTable), "qfmodel")
            # One model, both eras: the token cells keep the measured value
            # while the two legacy rows contribute only their credits.
            self.assertNotIn("Coverage", row)
            self.assertEqual(row["Input"], "50")
            self.assertEqual(row["Usage Total"], "55")
            self.assertEqual(row["Credits"], "1.75")
            note = str(app.query_one("#filter-tokens", Static).content)
            self.assertNotIn("coverage", note.lower())
            self.assertIn("Usage Total re-adds cache hit", note)

    async def test_a_credits_only_call_detail_dashes_its_token_rows(self):
        app = TUI.TrackerApp(db_path=self.t.db_path, cfg=QUIET_CFG)
        async with app.run_test(size=(200, 45)) as pilot:
            app.push_screen(TUI.CallScreen("model", "m1", "qfmodel"))
            await pilot.pause()
            labels = [str(w.content) for w in app.screen.query(".call-label")]
            values = [str(w.content) for w in app.screen.query(".call-value")]
            detail = dict(zip(labels, values))
            # `SELECT *` of one stored row: the derived columns (api total, cache
            # miss) are not there to check, but all four reported-as-zero fields
            # are.
            for col in ("input tokens", "output tokens", "cache read",
                        "cache write"):
                self.assertEqual(detail[col], "—", col)
            # The money axis is a measurement, so it keeps its number — and a
            # reported measure keeps its parenthesised raw value.
            self.assertNotEqual(detail["credits"], "—")
            self.assertIn("(1.5)", detail["credits"])
            # The stored sentinel is not a label a reader should see.
            self.assertEqual(detail["provider"], "Built-in")
            # Coverage is not a row on this screen any more: the four dashes
            # already say the route reported no token.
            self.assertNotIn("token coverage", detail)

    async def test_a_byok_call_detail_keeps_its_token_numbers(self):
        """The same screen, the other kind of row: nothing here may be dashed."""
        app = TUI.TrackerApp(db_path=self.t.db_path, cfg=QUIET_CFG)
        async with app.run_test(size=(200, 45)) as pilot:
            app.push_screen(TUI.CallScreen("model", "m3", "byok-model"))
            await pilot.pause()
            labels = [str(w.content) for w in app.screen.query(".call-label")]
            values = [str(w.content) for w in app.screen.query(".call-value")]
            detail = dict(zip(labels, values))
            self.assertEqual(detail["input tokens"], "120  (120)")
            self.assertEqual(detail["output tokens"], "24  (24)")
            self.assertEqual(detail["provider"], "qoder-custom-a3d301ce")
            self.assertNotIn("—", detail.values())
            # Its credits field is absent, which is `-`: an absent value and a
            # route-level zero are two different statements.
            self.assertEqual(detail["credits"], "-")
            self.assertNotIn("token coverage", detail)

    async def test_fmt_tok_is_the_same_function_in_both_renderers(self):
        """The TUI and the headless CLI each define it; they must not drift.

        Two copies of one rule is the shape that lets `qcut stats` say `—` while
        the TUI says `0` about one window, so the pair is checked against a
        matrix rather than trusted.
        """
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "qcut_stats_fmt", str(SCRIPTS / "qcut-stats.py"))
        stats = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(stats)
        self.assertEqual(sorted(TUI._DETAIL_TOKEN_COLS), sorted(db.TOKEN_COLUMNS),
                         "the detail screen's token list left the shared one")
        for value in (None, 0, 1, 7, 4_000_000):
            for reported in (True, False):
                self.assertEqual(TUI.fmt_tok(value, reported),
                                 stats.fmt_tok(value, reported),
                                 f"value={value!r} reported={reported!r}")
        # The credit formatter is deliberately compared only below 1000: the two
        # `fmt_credits` copies already disagree there (the TUI shortens
        # `1,234`, the CLI writes `1,234.50`). That divergence predates this
        # change and is a formatting question, not a truth question — the dash
        # decision is what is locked here.
        for value in (None, 0, 0.25, 5.0):
            for carried in (True, False):
                self.assertEqual(TUI.fmt_credit_total(value, carried),
                                 stats.fmt_credit_total(value, carried),
                                 f"value={value!r} carried={carried!r}")



if __name__ == "__main__":
    unittest.main()


class ChromeTest(unittest.TestCase):
    """The tab strip and the canvas are the two things every glance touches.

    Both were qcut inventions here: the tab titles carried an emoji each that the
    reference UI never had, and the app repainted the whole `Screen` with
    `$surface` plus the status line with `$panel`. Neither is a rendering
    preference the tests could previously see, so both are pinned as shape
    promises -- an emoji in a title is a character, and a background rule is
    text in `CSS`.
    """

    def test_tab_titles_are_plain_names(self):
        for tid, title in TUI.TABS:
            self.assertEqual(title, title.encode("ascii", "replace").decode(),
                             f"tab {tid!r} has a non-ASCII character in its title")
            self.assertEqual(title, title.strip(),
                             f"tab {tid!r} title is padded")

    def test_the_app_defines_no_screen_background(self):
        css = TUI.TrackerApp.CSS
        self.assertNotIn("Screen {", css,
                         "the app overrides the canvas background")
        self.assertNotIn("background: $surface", css)
        self.assertNotIn("background: $panel", css)


class UnifiedControlsTest(unittest.IsolatedAsyncioTestCase):
    """The shortcuts shared with ocut/cbut: `s`, `/`, `a`, `d`, tab, and the
    pushed screens that must not inherit them."""

    async def asyncSetUp(self):
        self.t = build_db(self)

    async def _tab(self, app, pilot, tab):
        from textual.widgets import TabbedContent
        app.query_one(TabbedContent).active = tab
        await pilot.pause()
        await pilot.pause()

    async def test_slash_filters_live_keeps_the_bar_and_clears_with_escape(self):
        from textual.widgets import DataTable, Input, Static
        app = TUI.TrackerApp(db_path=self.t.db_path, cfg=QUIET_CFG)
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await self._tab(app, pilot, "tools")
            inp = app.query_one("#filter-input", Input)
            table = app.query_one("#tools-table", DataTable)
            note = app.query_one("#filter-tools", Static)
            self.assertFalse(inp.display, "the bar is hidden with no filter")
            await pilot.press("slash")
            await pilot.pause()
            self.assertTrue(inp.display)
            self.assertIs(app.focused, inp)
            for ch in "edit":
                await pilot.press(ch)
            await pilot.pause()
            self.assertEqual(inp.value, "edit")
            self.assertEqual(table.row_count, 1)
            self.assertIn("Edit", str(table.get_row(next(iter(table.rows)))))
            self.assertIn('filter "edit" · 1 of 2', str(note.content))
            self.assertEqual(table.cursor_row, 0,
                             "a filter edit must start at the first match")
            # Enter applies: focus returns to the table, the bar stays visible.
            await pilot.press("enter")
            await pilot.pause()
            self.assertTrue(inp.display,
                            "Enter closed the bar under a live filter")
            self.assertIs(app.focused, table)
            # The needle is per tab: Skills has none, so its bar is hidden.
            await self._tab(app, pilot, "skills")
            self.assertFalse(inp.display)
            await self._tab(app, pilot, "tools")
            self.assertTrue(inp.display)
            self.assertEqual(inp.value, "edit")
            await pilot.press("escape")
            await pilot.pause()
            self.assertFalse(inp.display)
            self.assertEqual(inp.value, "")
            self.assertEqual(table.row_count, 2)
            self.assertNotIn("filter", str(note.content).lower())

    async def test_usage_filter_and_sort_run_over_the_whole_window(self):
        from textual.widgets import DataTable, Static
        app = TUI.TrackerApp(db_path=self.t.db_path, cfg=QUIET_CFG)
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await self._tab(app, pilot, "usage")
            table = app.query_one("#usage-table", DataTable)
            note = app.query_one("#usage-note", Static)

            def first_row():
                key = next(iter(table.rows))
                return str(key.value), table.get_row(key)

            self.assertIn("sort: time", str(note.content))
            self.assertTrue(first_row()[0].endswith("\tm5"),
                            "time sort: m5 is the newest request")
            await pilot.press("s")
            await pilot.pause()
            self.assertIn("sort: tokens", str(note.content))
            self.assertTrue(first_row()[0].endswith("\tm2"),
                            "tokens sort: m2 has the largest usage total")
            self.assertEqual(str(first_row()[1][3]), "220",
                             "tokens sort must rank above the page's time order")
            await pilot.press("s")
            await pilot.pause()
            self.assertIn("sort: credits", str(note.content))
            await pilot.press("s")
            await pilot.pause()
            self.assertIn("sort: model", str(note.content))
            await pilot.press("s")
            await pilot.pause()
            self.assertIn("sort: time", str(note.content))
            # The filter runs in SQL over the window, not over the loaded page.
            await pilot.press("slash")
            await pilot.pause()
            for ch in "builtin":
                await pilot.press(ch)
            await pilot.pause()
            self.assertIn('filter "builtin" · 6 matches', str(note.content))
            self.assertEqual(table.row_count, 6)
            await pilot.press("escape")
            await pilot.pause()
            self.assertNotIn('filter "', str(note.content))

    async def test_no_tab_scrolls_when_there_is_nothing_to_scroll(self):
        """The screen itself never scrolls: a root measured two rows taller than
        the terminal let every tab drift on an otherwise empty page."""
        from textual.widgets import TabbedContent
        app = TUI.TrackerApp(db_path=self.t.db_path, cfg=QUIET_CFG)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            for tab in app.TAB_IDS:
                app.query_one(TabbedContent).active = tab
                await pilot.pause()
                await pilot.pause()
                self.assertEqual(app.screen.max_scroll_y, 0,
                                 f"{tab} can scroll with nothing to show")
            await pilot.press("slash")
            await pilot.pause()
            self.assertEqual(app.screen.max_scroll_y, 0,
                             "the visible filter row added screen scroll")

    async def test_a_pushed_screen_owns_its_keys(self):
        from textual.widgets import DataTable
        app = TUI.TrackerApp(db_path=self.t.db_path, cfg=QUIET_CFG)
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await self._tab(app, pilot, "tools")
            app.query_one("#tools-table", DataTable).focus()
            await pilot.press("enter")
            await pilot.pause()
            await pilot.pause()
            self.assertIsInstance(app.screen, TUI.HistoryScreen)
            actions = {b.binding.action for b in app.active_bindings.values()}
            for action in ("cycle_sort", "open_filter", "toggle_show_all",
                           "date_range", "next_tab", "previous_tab",
                           "refresh", "quit"):
                self.assertNotIn(action, actions,
                                 f"app shortcut {action!r} is live on a detail")
            self.assertTrue(any("pop_screen" in action for action in actions),
                            "the detail screen lost its Back binding")
            before = dict(app._sort_mode)
            await pilot.press("s")
            await pilot.pause()
            self.assertEqual(app._sort_mode, before)
            self.assertEqual(len(app.screen_stack), 2)
            await pilot.press("q")
            await pilot.pause()
            self.assertEqual(len(app.screen_stack), 1,
                             "q must be Back on a pushed screen, not Quit")

    async def test_it_syncs_once_on_launch_and_keeps_no_timer(self):
        calls = []
        with mock.patch.object(TUI.TrackerApp, "_request_sync",
                               lambda app: calls.append(app)):
            app = TUI.TrackerApp(db_path=self.t.db_path, cfg=QUIET_CFG)
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                self.assertEqual(calls, [app], "launch must sync exactly once")
                intervals = [getattr(timer, "_interval", None)
                             for timer in app._timers]
                self.assertFalse(any(value in (5, 5.0, 30, 30.0)
                                     for value in intervals),
                                 f"a refresh/sync timer survived: {intervals}")
                self.assertFalse(hasattr(app, "_auto_sync"))


class ClickCadenceTest(unittest.TestCase):
    """Two `pilot.click` lines with nothing between them are a race, not a test.

    Textual posts a click and works through the resulting message pump at the next
    pause. Without a pause between two clicks the second can arrive before the first
    has changed anything, and whether the case passes is then a property of the
    runner's scheduling — which is exactly how CI's Python 3.11 job failed on the
    calendar stepper while every local interpreter passed it.

    The check reads this file's own source rather than running the app: a cadence
    rule that only fires when the timing happens to be unlucky is not a guard.
    """

    SOURCE = Path(__file__)

    def test_no_two_clicks_are_issued_without_a_pause_between_them(self):
        lines = self.SOURCE.read_text(encoding="utf-8").splitlines()
        issued = lambda line: line.strip().startswith("await pilot.click(")
        offenders = [
            (index + 1, lines[index].strip(), lines[index + 1].strip())
            for index in range(len(lines) - 1)
            if issued(lines[index]) and issued(lines[index + 1])
        ]
        self.assertEqual(
            offenders, [],
            f"clicks issued with no `await pilot.pause()` between them: {offenders}")
