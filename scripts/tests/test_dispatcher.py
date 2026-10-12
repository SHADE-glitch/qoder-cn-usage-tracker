"""`bin/qcut` routing: the bash entry point must still behave when the install
is broken, which is the whole reason it is bash and not Python.

Every case pins ``QCUT_SCRIPTS``, ``QCUT_DB`` and ``QCUT_QODER_DIR`` into a temp
directory — all three, because the launcher resolves them independently, and a
test that leaks one of them would run against live state.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
QCUT = REPO / "bin" / "qcut"


def make_stub_scripts(dirpath: Path) -> Path:
    """A scripts/ dir whose four entry points print which one ran."""
    scripts = dirpath / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    for name in ("qcut-tui.py", "qcut-sync.py", "qcut-stats.py"):
        (scripts / name).write_text(
            "import sys\n"
            f"print('RAN {name} ' + ' '.join(sys.argv[1:]))\n",
            encoding="utf-8")
    return scripts


class DispatcherTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.scripts = make_stub_scripts(self.dir)
        self.env = dict(
            os.environ,
            QCUT_SCRIPTS=str(self.scripts),
            QCUT_DB=str(self.dir / "usage.db"),
            QCUT_QODER_DIR=str(self.dir / "qoder-cn"),
            # An absolute path on purpose: the launcher's ``-x`` test does no
            # PATH lookup, so a bare ``python3`` is correctly refused.
            QCUT_VENV=shutil.which("python3"),
        )
        (self.dir / "qoder-cn").mkdir(exist_ok=True)

    def run_qcut(self, *args, env=None):
        return subprocess.run(
            ["bash", str(QCUT), *args], capture_output=True, text=True,
            env=env or self.env, timeout=30)

    def test_no_argument_launches_the_tui(self):
        out = self.run_qcut()
        self.assertIn("RAN qcut-tui.py", out.stdout)

    def test_tui_is_spelled_out_too(self):
        out = self.run_qcut("tui")
        self.assertIn("RAN qcut-tui.py", out.stdout)

    def test_sync_routes_to_the_indexer_and_passes_its_flags(self):
        out = self.run_qcut("sync", "--full")
        self.assertIn("RAN qcut-sync.py --full", out.stdout)

    def test_a_report_subcommand_routes_to_the_stats_cli(self):
        out = self.run_qcut("tools")
        self.assertIn("RAN qcut-stats.py tools", out.stdout)

    def test_an_unknown_subcommand_still_reaches_the_stats_cli(self):
        """The CLI owns its own argument errors; the dispatcher must not second
        guess the command list."""
        out = self.run_qcut("stats")
        self.assertIn("RAN qcut-stats.py stats", out.stdout)

    def test_help_prints_without_resolving_any_script(self):
        broken = dict(self.env)
        broken.pop("QCUT_SCRIPTS", None)
        broken["QCUT_VENV"] = str(self.dir / "nope" / "python")
        out = self.run_qcut("--help", env=broken)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("qcut sync", out.stdout)

    def test_a_missing_scripts_dir_is_an_error_not_a_traceback(self):
        """A copy of the launcher with no scripts/ beside it has nowhere to
        look, and must say so rather than exec a path that cannot exist."""
        lone = self.dir / "lone" / "bin"
        lone.mkdir(parents=True)
        shutil.copy(QCUT, lone / "qcut")
        broken = dict(self.env)
        broken.pop("QCUT_SCRIPTS", None)
        out = subprocess.run(["bash", str(lone / "qcut"), "stats"],
                             capture_output=True, text=True, env=broken, timeout=30)
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("no scripts directory found", out.stderr)
        self.assertNotIn("Traceback", out.stderr)

    def test_an_unusable_venv_override_is_reported(self):
        broken = dict(self.env)
        broken["QCUT_VENV"] = "/definitely/not/a/python"
        out = self.run_qcut("stats", env=broken)
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("neither a venv dir nor an executable", out.stderr)

    def test_a_venv_binary_is_honoured(self):
        env = dict(self.env, QCUT_VENV=shutil.which("python3"))
        out = self.run_qcut("stats", env=env)
        self.assertIn("RAN qcut-stats.py stats", out.stdout)


if __name__ == "__main__":
    unittest.main()
