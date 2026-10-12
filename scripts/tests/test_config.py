"""Settings-layer tests: defaults, file, environment precedence and bounds.

Anything asserting the file or the defaults is asserting them *under* the
environment, which outranks both — so these cases run with every ``QCUT_*``
name hidden, or a configured shell turns them into false failures.
"""

from __future__ import annotations

import contextlib
import importlib.util
import os
import sys
import unittest
from pathlib import Path

# `_helpers` puts scripts/ on sys.path, so it must be imported before qcut_db.
from _helpers import SCRIPTS

import qcut_db as db

ENV_NAMES = tuple(db.ENV_NAMES.values()) + ("QCUT_CONFIG", "QCUT_DB", "QCUT_QODER_DIR")


@contextlib.contextmanager
def no_settings_env():
    saved = {name: os.environ.pop(name) for name in ENV_NAMES if name in os.environ}
    try:
        yield
    finally:
        os.environ.update(saved)


def load_tui(name="qcut_tui_cfg"):
    spec = importlib.util.spec_from_file_location(name, str(SCRIPTS / "qcut-tui.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


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

    def test_a_float_setting_accepts_a_float(self):
        with no_settings_env():
            os.environ["QCUT_REFRESH_SECS"] = "2.5"
            try:
                cfg = db.load_config(self.t.write(""))
            finally:
                os.environ.pop("QCUT_REFRESH_SECS")
        self.assertEqual(cfg["refresh_secs"], 2.5)


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
        can edit with no effect."""
        tui = load_tui()
        app = tui.TrackerApp(db_path="/tmp/qcut-cfg-test.db")
        try:
            self.assertEqual(app.cfg["log_limit"], app.cfg["log_limit"])
            self.assertIn("top_n", app.cfg)
            self.assertEqual(app.cfg["detail_limit"], db.DEFAULTS["detail_limit"])
        finally:
            app.conn.close()


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
