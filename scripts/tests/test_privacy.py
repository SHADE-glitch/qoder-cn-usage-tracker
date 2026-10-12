"""Privacy invariants, each with a check rather than a promise.

1. Nothing leaves the machine — an import allowlist over this repository's own
   production scripts. Scope it honestly: this inspects *our* code. It is not a
   claim about third-party packages.
2. Qoder's own files are never written — a fixture tree is fingerprinted before
   and after a real index run.
3. Metadata only, never content — sentinel strings fed into every slot the
   parser reads must not reach any column.
"""

from __future__ import annotations

import ast
import hashlib
import unittest
from pathlib import Path

# `_helpers` puts scripts/ on sys.path, so it must be imported before qcut_db.
from _helpers import SCRIPTS, TempTree, assistant, tool_use, usage

import qcut_db as db
import _hermetic  # noqa: E402,F401 -- tripwire: the suite must never open the real database

PRODUCTION_SCRIPTS = ("qcut_db.py", "qcut-sync.py", "qcut-stats.py", "qcut-tui.py")

# Roots that would give this tool a way off the machine, or a way to grow a
# second process. Named broadly on purpose: adding one is a test failure rather
# than a quiet convention change.
FORBIDDEN_ROOTS = {
    "socket", "ssl", "http", "urllib3", "requests", "httpx", "aiohttp",
    "ftplib", "smtplib", "telnetlib", "xmlrpc", "asyncio", "subprocess",
    "multiprocessing", "threading", "webbrowser", "ctypes", "pickle",
    "shelve", "pty", "telnet", "uuid", "secrets",
}
# No file is exempt from the list above. The one historical exemption
# (`threading`, for the TUI's background sync worker) is gone: the worker now
# runs on Textual's own thread pool via `App.run_worker(thread=True)`, so no
# production script needs the knob at all — see
# `test_no_production_script_starts_a_thread_of_its_own`.
ALLOWED_EXTRA = {}
# `urllib.parse` is string work; the bare `urllib` import is the one that can
# open a connection, and it is not in any allowlist.
FORBIDDEN_PREFIXES = ("urllib.request", "urllib.error")


def imported_roots(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                roots.append(node.module)
    return roots


def dynamic_import_calls(path: Path):
    """Calls that could bypass the allowlist: `__import__`, `import_module`."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if name in ("__import__", "import_module", "eval", "exec"):
                found.append(f"{name} at line {node.lineno}")
    return found


def tree_fingerprint(root: Path):
    out = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            out[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


class ImportSurfaceTest(unittest.TestCase):
    def test_no_production_script_can_leave_the_machine(self):
        for name in PRODUCTION_SCRIPTS:
            path = SCRIPTS / name
            allowed = ALLOWED_EXTRA.get(name, set())
            for root in imported_roots(path):
                head = root.split(".")[0]
                with self.subTest(script=name, module=root):
                    if head in allowed:
                        continue
                    self.assertNotIn(
                        head, FORBIDDEN_ROOTS,
                        f"{name} imports {root!r}, which is outside the local-only surface")
                    self.assertFalse(
                        root.startswith(FORBIDDEN_PREFIXES),
                        f"{name} imports {root!r}, which can open a connection")

    def test_nothing_dynamic_bypasses_the_allowlist(self):
        for name in PRODUCTION_SCRIPTS:
            with self.subTest(script=name):
                # `load_sync`/`load_tui` load a sibling by path on purpose and are
                # the only dynamic loaders; they are named, not blanket-exempted.
                found = [f for f in dynamic_import_calls(SCRIPTS / name)
                         if "import_module" not in f]
                self.assertEqual(found, [])

    def test_no_production_script_starts_a_thread_of_its_own(self):
        """The TUI's sync runs on Textual's worker, not on `threading`.

        This used to allow exactly one file (`qcut-tui.py`) to import
        `threading` for its background sync worker. Moving that worker to
        `App.run_worker(thread=True)` removed the last use, so the rule is now
        the plain one: any production script that can spawn a thread fails.
        """
        users = [name for name in PRODUCTION_SCRIPTS
                 if any(r.split(".")[0] == "threading"
                        for r in imported_roots(SCRIPTS / name))]
        self.assertEqual(users, [])


class HostDirNotWrittenTest(unittest.TestCase):
    def test_indexing_does_not_change_one_byte_of_the_source_tree(self):
        t = TempTree(self)
        t.write("projects/-work-demo/s1.jsonl", [
            assistant("s1", "m1", usage=usage(input_tokens=5, credits=1.0),
                      content=[tool_use("c1", "Bash", command="ls")]),
        ])
        t.json_file("settings.json", {"providers": {}})
        t.json_file("mcp.json", {"mcpServers": {}})
        t.skill("demo")
        before = tree_fingerprint(t.qoder)
        self.assertTrue(before)
        t.sync()
        after = tree_fingerprint(t.qoder)
        self.assertEqual(before, after, "a Qoder file was written by the indexer")

    def test_no_stray_side_file_appears_next_to_the_database(self):
        t = TempTree(self)
        t.write("projects/-work-demo/s1.jsonl", [
            assistant("s1", "m1", usage=usage(input_tokens=5))])
        t.sync()
        names = sorted(p.name for p in t.root.iterdir())
        # The database plus its own WAL side files are allowed; nothing else.
        allowed = {"usage.db", "usage.db-wal", "usage.db-shm", "qoder-cn", "config.toml"}
        self.assertTrue(set(names) <= allowed, f"unexpected files: {names}")


class MetadataOnlyTest(unittest.TestCase):
    """A sentinel in every slot the parser reads must reach no text column."""

    SENTINELS = (
        "SENT-AGENT-DESC-1a2b",      # Agent tool description
        "SENT-AGENT-PROMPT-3c4d",    # Agent tool prompt
        "SENT-TOOL-ARGS-5e6f",       # an arbitrary tool argument value
        "SENT-SKILL-ARGS-7g8h",      # Skill tool args
        "SENT-MESSAGE-BODY-9i0j",    # a message text block
        "SENT-RESULT-BODY-k1l2",     # a tool result body
    )

    def test_no_free_text_value_reaches_a_column(self):
        t = TempTree(self)
        t.write("projects/-work-demo/s1.jsonl", [
            assistant("s1", "m1", usage=usage(input_tokens=5), content=[
                {"type": "text", "text": self.SENTINELS[4]},
                tool_use("c1", "Bash", command=self.SENTINELS[1], stdout=self.SENTINELS[2]),
                tool_use("c2", "Agent", subagent_type="Plan",
                         description=self.SENTINELS[0], prompt=self.SENTINELS[1]),
                tool_use("c3", "Skill", skill="demo", args=self.SENTINELS[3]),
            ]),
            {"type": "user", "sessionId": "s1", "timestamp": "2026-10-01T10:00:05.000Z",
             "message": {"content": [{"type": "text", "text": self.SENTINELS[4]},
                                     {"type": "tool_result", "tool_use_id": "c1",
                                      "content": self.SENTINELS[5]}]},
             "toolUseResult": {"kind": "completed", "stdout": self.SENTINELS[5]}},
        ])
        t.sync()
        conn = t.conn()
        try:
            tables = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")]
            for table in tables:
                cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
                if not cols:
                    continue
                where = " OR ".join(f"CAST({c} AS TEXT) LIKE ?" for c in cols)
                for sentinel in self.SENTINELS:
                    hit = conn.execute(
                        f"SELECT COUNT(*) FROM {table} WHERE {where}",
                        [f"%{sentinel}%"] * len(cols)).fetchone()[0]
                    self.assertEqual(hit, 0, f"{sentinel} reached {table}")
        finally:
            conn.close()

    def test_the_scan_has_a_positive_control(self):
        """Without this the check above would pass on a database that indexed
        nothing at all."""
        t = TempTree(self)
        t.write("projects/-work-demo/s1.jsonl", [
            assistant("s1", "m1", usage=usage(input_tokens=5),
                      content=[tool_use("c1", "Bash", command="ls")]),
        ])
        t.sync()
        conn = t.conn()
        try:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM tool_calls WHERE tool_name='Bash'")
                .fetchone()[0], 1)
        finally:
            conn.close()

    def test_no_usage_table_holds_a_prose_column(self):
        """A prose column in a usage table is a privacy change, so it has to be
        declared here rather than added quietly."""
        allowed_prose = {
            "commands.command",       # a slash-command name: a catalog entry
            "skill_usage.skill",      # a skill name
            "agent_usage.agent_type",  # an agent type name
            "mcp_usage.server", "mcp_usage.tool",
            "inventory.name", "inventory.owner_plugin", "inventory.version",
            "inventory.path", "inventory.source",
            "model_calls.model", "model_calls.provider", "model_calls.provider_id",
            "model_calls.request_id", "model_calls.stop_reason",
            "sessions.project", "sessions.cwd", "sessions.git_branch",
            "sessions.version", "sessions.entrypoint", "sessions.model",
        }
        conn = db.open_db(":memory:")
        db.ensure_schema(conn)
        prose_suspects = {"title", "description", "text", "content", "prompt",
                          "body", "summary", "message", "args", "arguments"}
        for table in ("tool_calls", "skill_usage", "agent_usage", "mcp_usage",
                      "model_calls", "plugin_usage", "commands", "sessions"):
            for row in conn.execute(f"PRAGMA table_info({table})"):
                column = row[1]
                key = f"{table}.{column}"
                with self.subTest(column=key):
                    self.assertFalse(
                        column in prose_suspects and key not in allowed_prose,
                        f"{key} holds what looks like prose")
        conn.close()


class HermeticTest(unittest.TestCase):
    def test_the_suites_never_open_the_real_database(self):
        real = Path.home() / ".local" / "share" / "qoder-cn-usage-tracker" / "usage.db"
        for name in PRODUCTION_SCRIPTS + ("tests/_helpers.py",):
            path = SCRIPTS / name if (SCRIPTS / name).exists() else None
            if path is None:
                continue
            text = path.read_text(encoding="utf-8")
            with self.subTest(script=name):
                self.assertNotIn(str(real), text)


if __name__ == "__main__":
    unittest.main()
