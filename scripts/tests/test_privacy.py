"""Privacy invariants, each with a check rather than a promise.

1. Nothing leaves the machine — an import *allowlist*: each production script
   declares its whole module surface and anything outside it fails, so an escape
   does not have to be thought of in advance to be refused. Two things an import
   list cannot see are checked separately: a call reached through a module that is
   already declared (`os.system`), and a module named in a string
   (`importlib.import_module`). Scope it honestly: this inspects *our* code. It is
   not a claim about third-party packages.
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

#: The entire import surface of each production script, read off the file itself.
#: This is an allowlist, not a denylist, and the difference is the point: a
#: denylist can only name the escapes somebody had already thought of, so adding
#: `poplib` to a script was a convention change that nothing reported. Here an
#: import the guard has never heard of fails, which is the only way to say
#: "nothing leaves the machine" about a module nobody has forbidden.
MODULE_ALLOWLIST = {
    "qcut_db.py": frozenset({
        "__future__", "datetime", "importlib.util", "os", "pathlib", "re",
        "sqlite3", "sys", "tomllib", "typing"}),
    "qcut-sync.py": frozenset({
        "__future__", "argparse", "datetime", "json", "pathlib", "qcut_db", "re",
        "sqlite3", "sys", "time"}),
    "qcut-stats.py": frozenset({
        "__future__", "argparse", "datetime", "json", "os", "pathlib", "qcut_db",
        "sqlite3", "sys", "unicodedata"}),
    "qcut-tui.py": frozenset({
        "__future__", "argparse", "datetime", "pathlib", "qcut_db", "sqlite3",
        "sys", "textual.app", "textual.binding", "textual.containers",
        "textual.screen", "textual.widgets", "time", "unicodedata"}),
}

# Roots that would give this tool a way off the machine, or a way to grow a
# second process. Kept as a statement of intent, and checked *against the
# allowlist above*: the list is the guard, and this is the guard on the guard.
FORBIDDEN_ROOTS = {
    "socket", "ssl", "http", "urllib3", "requests", "httpx", "aiohttp",
    "ftplib", "smtplib", "telnetlib", "xmlrpc", "asyncio", "subprocess",
    "multiprocessing", "threading", "webbrowser", "ctypes", "pickle",
    "shelve", "pty", "telnet", "uuid", "secrets",
    # The ones a denylist only grows to cover once somebody is asked.
    "poplib", "imaplib", "nntplib", "socketserver", "websocket", "grpc",
}
# `urllib.parse` is string work and would be harmless to declare; `urllib.request`
# is the one that opens a connection. Neither is on any allowlist, so the import
# check already refuses it — this pair is the rule for the day the list is widened,
# when the only thing standing between a bare `urllib.request` and a report is the
# check below it.
FORBIDDEN_PREFIXES = ("urllib.request", "urllib.error")

#: Call sites that need no import the allowlist cannot already permit. `os` is
#: imported by three of the four scripts, so `os.system` is reachable without any
#: new dependency at all — the import surface alone cannot say "no shell". The
#: last two are already refused as imports; they are named here too so that
#: widening the allowlist for one of them still has to pass this check. Matched by
#: dotted name, because a bare-attribute match would report `sqlite3.connect`.
FORBIDDEN_CALLS = frozenset({
    "os.system", "os.popen", "os.fork", "os.forkpty", "os.posix_spawn",
    "os.execv", "os.execve", "os.execvp", "os.execl", "os.execlp",
    "os.spawnv", "os.spawnl", "os.startfile",
    "urllib.request.urlopen", "socket.socket",
})

#: The only dynamic loader in production, named rather than exempted by pattern:
#: `load_sync` reads the sibling parser by path so `qcut` needs no installed
#: package, and it is the one place the file's own name is not enough.
DYNAMIC_LOADERS = {
    "qcut_db.py": frozenset({"spec_from_file_location", "module_from_spec",
                             "exec_module"}),
    "qcut-sync.py": frozenset(),
    "qcut-stats.py": frozenset(),
    "qcut-tui.py": frozenset(),
}
DYNAMIC_LOAD_NAMES = ("__import__", "import_module", "eval", "exec",
                      "spec_from_file_location", "module_from_spec",
                      "exec_module")


def imported_modules(source: str):
    """Every module name an `import` statement in *source* asks for."""
    tree = ast.parse(source)
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module)
    return out


def outside_allowlist(modules, allowed):
    """Modules asked for by an `import` that are not on the declared surface."""
    return sorted(m for m in modules if m not in allowed)


def called_names(source: str):
    """Every callee written as a chain of names — `os.system(...)`, `run(...)`.

    Dotted where the receiver is a plain name, so the forbidden list can name the
    module a call belongs to rather than any method that shares its word: a bare
    `connect` match would report `sqlite3.connect`, which is how every query in
    this product reaches its database.
    """
    tree = ast.parse(source)
    found = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        parts = []
        func = node.func
        while isinstance(func, ast.Attribute):
            parts.append(func.attr)
            func = func.value
        if isinstance(func, ast.Name):
            parts.append(func.id)
            found.add(".".join(reversed(parts)))
    return found


def dynamic_import_calls(source: str):
    """Calls that can load a module no `import` statement mentions."""
    tree = ast.parse(source)
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
            if name in DYNAMIC_LOAD_NAMES:
                found.add(name)
    return found


def tree_fingerprint(root: Path):
    out = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            out[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


class ImportSurfaceTest(unittest.TestCase):
    def test_each_script_imports_exactly_what_is_declared(self):
        for name in PRODUCTION_SCRIPTS:
            with self.subTest(script=name):
                modules = imported_modules(
                    (SCRIPTS / name).read_text(encoding="utf-8"))
                offenders = outside_allowlist(modules, MODULE_ALLOWLIST[name])
                self.assertEqual(
                    offenders, [],
                    f"{name} imports {offenders}, which is not on its declared "
                    f"surface; nothing may reach a new dependency quietly")

    def test_the_allowlist_itself_holds_no_escape(self):
        """The list is the guard; this is the guard on the list."""
        declared = set().union(*MODULE_ALLOWLIST.values())
        for module in sorted(declared):
            with self.subTest(module=module):
                self.assertNotIn(module.split(".")[0], FORBIDDEN_ROOTS,
                                 f"{module!r} is both allowed and forbidden")
                self.assertFalse(module.startswith(FORBIDDEN_PREFIXES))

    def test_no_allowlist_entry_is_stale(self):
        """A list wider than the code is a promise nobody is keeping.

        Dropping the last use of `threading` has to shrink the list, or the next
        reader believes the script still needs it.
        """
        for name in PRODUCTION_SCRIPTS:
            with self.subTest(script=name):
                modules = imported_modules(
                    (SCRIPTS / name).read_text(encoding="utf-8"))
                stale = sorted(MODULE_ALLOWLIST[name] - modules)
                self.assertEqual(
                    stale, [], f"{name} declares unused imports {stale}")

    def test_the_import_check_reports_an_escape_it_has_never_seen(self):
        """Proof the allowlist bites on a module no rule names.

        The denylist era's blind spot, run through the same function: `poplib`
        was never in `FORBIDDEN_ROOTS`, and it is not on any allowlist either.
        """
        source = "import poplib\nimport socket\nimport os\n"
        offenders = outside_allowlist(imported_modules(source), frozenset({"os"}))
        self.assertEqual(offenders, ["poplib", "socket"])
        # The control: the same function on a declared import says nothing.
        self.assertEqual(
            outside_allowlist(imported_modules("import os\n"), frozenset({"os"})), [])

    def test_no_production_script_calls_a_shell_or_opens_a_connection(self):
        for name in PRODUCTION_SCRIPTS:
            with self.subTest(script=name):
                hits = sorted(called_names(
                    (SCRIPTS / name).read_text(encoding="utf-8")) & FORBIDDEN_CALLS)
                self.assertEqual(
                    hits, [],
                    f"{name} calls {hits}; the import allowlist cannot see this "
                    f"because `os` is already declared")

    def test_the_call_site_scan_reports_a_shell_reached_through_an_allowed_import(self):
        """`os` is on three allowlists, so the import check cannot refuse a shell.

        The control is the other half: `os.makedirs` is the same module, the same
        kind of call, and legal — a scan that failed on both would be noise.
        """
        source = "import os\nos.system('ls')\nos.makedirs('x')\n"
        self.assertEqual(
            sorted(called_names(source) & FORBIDDEN_CALLS), ["os.system"])
        self.assertEqual(
            sorted(called_names("import os\nos.makedirs('x')\n")
                   & FORBIDDEN_CALLS), [])
        # And the one call the product really does make on a forbidden-sounding word.
        self.assertEqual(
            sorted(called_names("import sqlite3\nsqlite3.connect('x.db')\n")
                   & FORBIDDEN_CALLS), [])

    def test_the_dynamic_loaders_are_exactly_the_ones_named(self):
        for name in PRODUCTION_SCRIPTS:
            with self.subTest(script=name):
                found = dynamic_import_calls(
                    (SCRIPTS / name).read_text(encoding="utf-8"))
                self.assertEqual(
                    found, set(DYNAMIC_LOADERS[name]),
                    f"{name} loads modules dynamically in ways the guard does not "
                    f"know about: {sorted(found - DYNAMIC_LOADERS[name])}")

    def test_the_dynamic_loader_scan_fires_on_a_name_in_a_string(self):
        """An `import` statement is not the only way to reach a module.

        Without this control the case above could pass on a detector that found
        nothing at all: `importlib.import_module("http.client")` names a
        forbidden root in a *string*, so the import surface never sees it.
        """
        source = ("import importlib\n"
                  "importlib.import_module('http.client')\n"
                  "exec('pass')\n")
        self.assertEqual(dynamic_import_calls(source), {"import_module", "exec"})
        self.assertEqual(dynamic_import_calls("import os\nos.getcwd()\n"), set())

    def test_no_production_script_starts_a_thread_of_its_own(self):
        """The TUI's sync runs on Textual's worker, not on `threading`.

        This used to allow exactly one file (`qcut-tui.py`) to import
        `threading` for its background sync worker. Moving that worker to
        `App.run_worker(thread=True)` removed the last use, so the rule is now
        the plain one: any production script that can spawn a thread fails.
        """
        users = [name for name in PRODUCTION_SCRIPTS
                 if "threading" in imported_modules(
                     (SCRIPTS / name).read_text(encoding="utf-8"))]
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
