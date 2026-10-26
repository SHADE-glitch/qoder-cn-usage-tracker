"""Settings-layer tests: defaults, file, environment precedence and bounds.

Anything asserting the file or the defaults is asserting them *under* the
environment, which outranks both — so these cases run with every ``QCUT_*``
name hidden, or a configured shell turns them into false failures.
"""

from __future__ import annotations

import ast
import contextlib
import importlib.util
import os
import sys
import unittest
from pathlib import Path

# `_helpers` puts scripts/ on sys.path, so it must be imported before qcut_db.
from _helpers import SCRIPTS, no_settings_env

import qcut_db as db
import _hermetic  # noqa: E402,F401 -- tripwire: the suite must never open the real database

def load_tui(name="qcut_tui_cfg"):
    spec = importlib.util.spec_from_file_location(name, str(SCRIPTS / "qcut-tui.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


class SettingsHaveConsumersTest(unittest.TestCase):
    """A key the loader returns and no renderer consults is a knob you can edit
    for no effect -- which is the exact failure this project's settings rule
    forbids, and `top_n` lived with it: declared, validated, bounded, printed by
    `qcut health`, and read by nothing while the Dashboard sliced a class constant.

    Scanned off the two entry scripts' syntax trees rather than from a list, so a
    seventh knob cannot be added without either giving it a consumer or failing
    here. Reading a value is not enough to count: `qcut health` prints every key,
    so the keys it merely *reports* are subtracted back out.
    """

    #: The settings layer and the two surfaces that render behaviour.
    CONSUMERS = ("qcut-tui.py", "qcut-stats.py")

    def consumed_keys(self):
        found = set()
        for name in self.CONSUMERS:
            tree = ast.parse((SCRIPTS / name).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if (isinstance(node, ast.Subscript)
                        and isinstance(node.slice, ast.Constant)
                        and isinstance(node.slice.value, str)
                        and self._is_cfg(node.value)):
                    found.add(node.slice.value)
        return found

    @staticmethod
    def _is_cfg(node):
        """True for `cfg["k"]`, `self.cfg["k"]`, `self.app.cfg["k]`-shaped hosts."""
        if isinstance(node, ast.Attribute):
            return node.attr == "cfg"
        return isinstance(node, ast.Name) and node.id == "cfg"

    def test_every_declared_setting_is_read_by_a_renderer(self):
        unused = set(db.DEFAULTS) - self.consumed_keys()
        self.assertEqual(
            unused, set(),
            f"setting(s) no renderer consults: {sorted(unused)} -- give each one "
            "a consumer where the value is used, or drop the knob")

    def test_no_renderer_reads_a_setting_that_was_never_declared(self):
        """The other direction: a typo'd key reads as a default that works."""
        undeclared = self.consumed_keys() - set(db.DEFAULTS)
        self.assertEqual(
            undeclared, set(),
            f"key(s) read but not in DEFAULTS: {sorted(undeclared)}")

    def test_the_scanner_finds_a_consumer_it_cannot_miss(self):
        """Positive control: the scan sees the six keys that really are read."""
        self.assertEqual(self.consumed_keys(), set(db.DEFAULTS),
                         "the scanner's result is not the shipped knob set -- "
                         "this case is the one that keeps the two above honest")


class DefaultsTest(unittest.TestCase):
    def test_without_a_file_the_defaults_are_shipped(self):
        with no_settings_env():
            cfg = db.load_config("/nonexistent/config.toml")
        self.assertEqual(cfg, db.DEFAULTS)

    def test_every_default_has_an_environment_name_and_bounds(self):
        self.assertEqual(set(db.DEFAULTS), set(db.ENV_NAMES))
        self.assertEqual(set(db.DEFAULTS), set(db._BOUNDS))

    def test_a_missing_file_is_not_an_error(self):
        with no_settings_env():
            cfg = db.load_config("/definitely/not/here.toml")
        self.assertEqual(cfg["name_cap"], db.DEFAULTS["name_cap"])


class FileTest(unittest.TestCase):
    def setUp(self):
        self.t = TempTreeForConfig(self)

    def test_a_file_overrides_a_default(self):
        with no_settings_env():
            cfg = db.load_config(self.t.write("top_n = 9\nname_cap = 40\n"))
        self.assertEqual(cfg["top_n"], 9)
        self.assertEqual(cfg["name_cap"], 40)
        self.assertEqual(cfg["log_limit"], db.DEFAULTS["log_limit"])

    def test_an_unknown_key_is_refused_not_ignored(self):
        with no_settings_env():
            with self.assertRaises(db.ConfigError) as ctx:
                db.load_config(self.t.write("nope = 3\n"))
        self.assertIn("nope", str(ctx.exception))

    def test_a_wrong_type_is_refused(self):
        with no_settings_env():
            with self.assertRaises(db.ConfigError):
                db.load_config(self.t.write('top_n = "many"\n'))

    def test_a_bool_is_not_an_integer(self):
        with no_settings_env():
            with self.assertRaises(db.ConfigError):
                db.load_config(self.t.write("top_n = true\n"))

    def test_an_out_of_range_value_is_refused(self):
        with no_settings_env():
            with self.assertRaises(db.ConfigError):
                db.load_config(self.t.write("top_n = 0\n"))

    def test_broken_toml_names_the_file(self):
        with no_settings_env():
            with self.assertRaises(db.ConfigError) as ctx:
                db.load_config(self.t.write("top_n = = 3\n"))
        self.assertIn(str(self.t.path), str(ctx.exception))


class PrecedenceTest(unittest.TestCase):
    def setUp(self):
        self.t = TempTreeForConfig(self)

    def test_the_environment_outranks_the_file(self):
        path = self.t.write("top_n = 9\n")
        with no_settings_env():
            os.environ["QCUT_TOP_N"] = "3"
            try:
                cfg = db.load_config(path)
            finally:
                os.environ.pop("QCUT_TOP_N")
        self.assertEqual(cfg["top_n"], 3)

    def test_a_non_numeric_environment_value_is_refused(self):
        with no_settings_env():
            os.environ["QCUT_TOP_N"] = "lots"
            try:
                with self.assertRaises(db.ConfigError):
                    db.load_config(self.t.write(""))
            finally:
                os.environ.pop("QCUT_TOP_N")

    def test_a_fractional_integer_setting_is_refused(self):
        """No setting is a duration any more, so the env parse has one rule:
        a fractional value for an integer knob is a typo, not a rounding
        opportunity."""
        with no_settings_env():
            os.environ["QCUT_LOG_LIMIT"] = "2.5"
            try:
                with self.assertRaises(db.ConfigError):
                    db.load_config(self.t.write(""))
            finally:
                os.environ.pop("QCUT_LOG_LIMIT")


class WiringTest(unittest.TestCase):
    def test_the_tui_reads_the_cap_through_the_loader(self):
        tui = load_tui()
        app = tui.TrackerApp(db_path="/tmp/qcut-cfg-test.db",
                             cfg=dict(db.DEFAULTS, name_cap=17))
        try:
            self.assertEqual(app.name_cap, 17)
        finally:
            app.conn.close()

    def test_the_shipped_cap_matches_the_constant_it_replaces(self):
        tui = load_tui()
        self.assertEqual(tui.TrackerApp.NAME_CAP, db.DEFAULTS["name_cap"])

    def test_every_default_is_a_real_attribute_the_tui_consults(self):
        """A key the loader returns and no renderer reads is a setting the user
        can edit with no effect.

        Read from the TUI's own source rather than from the app's dict: asking
        the dict whether it holds a key it was just given proves nothing, which
        is what this case used to do (`assertEqual(cfg[k], cfg[k])`). That the
        value reaches a *table* is checked where the fixture can afford rows —
        `test_tui.TabWiringTest.test_the_usage_page_shows_only_log_limit_rows`.
        """
        source = (SCRIPTS / "qcut-tui.py").read_text(encoding="utf-8")
        unread = [key for key in db.DEFAULTS
                  if f'cfg["{key}"]' not in source]
        self.assertEqual(unread, [],
                         "settings no renderer reads: " + ", ".join(sorted(unread)))


class HealthSurfaceTest(unittest.TestCase):
    def test_health_reports_each_default(self):
        """`qcut health` prints whatever `load_config()` returned, keyed by the
        names the file uses, so a knob cannot be added and go unreported."""
        import importlib.util
        spec = importlib.util.spec_from_file_location("qcut_stats", str(SCRIPTS / "qcut-stats.py"))
        stats = importlib.util.module_from_spec(spec)
        sys.modules["qcut_stats"] = stats
        spec.loader.exec_module(stats)
        t = TempTreeForConfig(self)
        conn = db.open_db(t.db_path)
        db.ensure_schema(conn)
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with no_settings_env(), redirect_stdout(buf):
            args = type("A", (), {})()
            stats.cmd_health(conn, args)
        conn.close()
        out = buf.getvalue()
        for key in db.DEFAULTS:
            self.assertIn(f"{key} =", out)


class TempTreeForConfig:
    """A minimal temp dir + config path, without importing the sync helpers."""

    def __init__(self, testcase):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.path = self.dir / "config.toml"
        self.db_path = self.dir / "usage.db"
        testcase.addCleanup(self._tmp.cleanup)

    def write(self, text):
        self.path.write_text(text, encoding="utf-8")
        return self.path


if __name__ == "__main__":
    unittest.main()
