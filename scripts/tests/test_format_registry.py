"""test_format_registry.py — Qoder's format surface is registered, not scattered.

`qcut` reads JSON that upstream owns. A renamed field or record type raises
nothing: the panels quietly empty out, `qcut sync` prints "done", CI stays green.
The registry block at the top of `qcut-sync.py` is the one place that states what
this tool depends on, and these tests read that file's AST to prove the statement
is still true — **in both directions**, so the block cannot decay into a
description of a parser that stopped matching it.

Two enforcement styles, chosen to match how each entry is used:

* layout, separators and tool names are **source** — the code reads them through
  the constant, and a bare literal at a call site fails;
* record field names stay **inline** in the handlers (a parser is easier to trust
  when the field it grabs is visible) and are *mirrored* by the ``*_FIELDS`` sets:
  an unregistered read fails, and a registered read nobody performs fails.

Every failure names the offending line, because the point of the check is to point
at the call sites a rename still owes.

Standard library only; nothing is executed and no host file is opened beyond one
in-memory schema.
"""

import ast
import importlib.util
import sqlite3
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
import qcut_db as db  # noqa: E402
import _hermetic  # noqa: E402,F401 -- tripwire: the suite must never open the real database

SYNC_PATH = SCRIPTS / "qcut-sync.py"
_spec = importlib.util.spec_from_file_location("qcut_sync_registry_test", str(SYNC_PATH))
sync = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sync)

SRC = SYNC_PATH.read_text(encoding="utf-8")
TREE = ast.parse(SRC)

# Methods whose string argument is a claim about Qoder's naming or layout. Output
# formatting (", ".join of a display line) is deliberately out of scope — it
# states nothing about the input format.
FORMAT_METHODS = {"startswith", "endswith", "split", "partition", "rfind", "glob"}

# Constants that must be *read by the code*, not merely declared. The mirror sets
# (``*_FIELDS``) are deliberately absent: their consumer is this file, and
# asserting otherwise would forbid the mirror design.
SOURCE_CONSTANTS = (
    "DIR_PROJECTS", "DIR_SKILLS", "TRANSCRIPT_GLOB",
    "SKILL_MANIFEST_GLOB", "MCP_FILE", "SETTINGS_FILE", "PLUGIN_SUBDIRS",
    "PLUGIN_INDEX_FILES", "PLUGIN_MARKETPLACE_SEP", "PLUGIN_INDEX_PLUGIN_KEY",
    "HIDDEN_NAME_PREFIX", "SKILL_PLUGIN_SEP",
    "TRANSCRIPT_LINE_SEP", "TRANSCRIPT_TS_UTC_SUFFIX",
    "SKILL_TOOL", "AGENT_TOOL", "MCP_TOOLS",
    "MCP_TARGET_PREFIX", "MCP_TARGET_SEP", "PROVIDER_MODEL_SEP",
    "CUSTOM_PROVIDER_PREFIX", "CMD_RE", "CLAIMED_RECORD_TYPES",
    "NOOP_RECORD_TYPES", "BUILTIN_TOOLS", "DEFAULT_AGENT_TYPE",
    "BUILTIN_AGENTS", "PROVIDER_BUILTIN", "VENDOR_HOSTS",
    "UNPARSEABLE_LINE", "MISSING_TYPE",
)

# Fields reached through a registry constant instead of a literal — a layout value
# is *source* by design (`plugins` is read as `PLUGIN_INDEX_PLUGIN_KEY`, never
# re-typed), so the mirror has to credit the constant's value as a read or it
# would report a live dependency as dead.
def registry_constant_values(module):
    return {v for name, v in vars(module).items()
            if name.isupper() and isinstance(v, str)}


# Fields the parser reads through a computed key rather than a literal one, and
# which therefore need a behavioural proof elsewhere. None today: the point of
# making this set explicit is that adding to it is a decision, not an oversight.
DYNAMIC_READS = frozenset()


def get_literal_keys(tree):
    """`{key: [lineno, ...]}` for every `x.get("<literal>", ...)` call."""
    out = {}
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get" and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)):
            out.setdefault(node.args[0].value, []).append(node.lineno)
    return out


def subscript_keys(tree):
    """`{key: [lineno, ...]}` for every `x["<literal>"]` read.

    A field can be grabbed with `[]` instead of `.get()`; the registry has to
    cover both, or the check is only as wide as the friendlier spelling.
    """
    out = {}
    for node in ast.walk(tree):
        if (isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant)
                and isinstance(node.slice.value, str)
                and isinstance(node.ctx, ast.Load)):
            out.setdefault(node.slice.value, []).append(node.lineno)
    return out


def format_method_literals(tree):
    """Literal string arguments to naming/layout methods, with locations."""
    out = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in FORMAT_METHODS and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)):
            out.append((node.lineno, node.func.attr, node.args[0].value))
    return out


def path_division_literals(tree):
    """Literal right-hand operands of `/` — i.e. inlined path segments."""
    return [(n.lineno, n.right.value) for n in ast.walk(tree)
            if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Div)
            and isinstance(n.right, ast.Constant)
            and isinstance(n.right.value, str)]


def bytes_literals(tree):
    """Every bytes literal, so the JSONL separator cannot be re-typed inline."""
    return [(n.lineno, n.value) for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, bytes)]


def dispatch_types(tree):
    """The `type` values `_handle_record` branches on.

    The local holding the record type is found by its own assignment rather than
    by name, so renaming it does not blind the check.
    """
    fn = next((n for n in tree.body
               if isinstance(n, ast.FunctionDef) and n.name == "_handle_record"),
              None)
    assert fn is not None, "_handle_record is gone from qcut-sync.py"
    holders = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if (isinstance(t, ast.Name) and isinstance(node.value, ast.Call)
                        and isinstance(node.value.func, ast.Attribute)
                        and node.value.func.attr == "get" and node.value.args
                        and getattr(node.value.args[0], "value", None) == "type"):
                    holders.add(t.id)
    assert holders, "_handle_record no longer reads the record type"
    found = set()
    for node in ast.walk(fn):
        if (isinstance(node, ast.Compare) and isinstance(node.left, ast.Name)
                and node.left.id in holders):
            right = node.comparators[0]
            if isinstance(right, ast.Constant) and isinstance(right.value, str):
                found.add(right.value)
            elif isinstance(right, (ast.Tuple, ast.Set, ast.List)):
                found.update(e.value for e in right.elts
                             if isinstance(e, ast.Constant))
    return found, holders


def referenced_names(tree):
    """Names *read* somewhere. Load context only, so a constant's own definition
    cannot count as its use."""
    return {n.id for n in ast.walk(tree)
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}


def where(pairs):
    return ", ".join(f"L{p[0]}:{p[1]}" if len(p) > 1 else f"L{p}" for p in pairs)


class FieldsAreRegisteredTest(unittest.TestCase):
    """Record field reads cannot go off the books."""

    def test_every_field_read_is_registered(self):
        reads = get_literal_keys(TREE) | subscript_keys(TREE)
        known = sync.QODER_FIELDS | sync.QCUT_INTERNAL_KEYS
        unregistered = {k: v for k, v in reads.items() if k not in known}
        self.assertEqual(
            unregistered, {},
            "field(s) read without being registered in the format registry: "
            f"{unregistered} — a key of Qoder's data belongs in the *_FIELDS "
            "sets above, and a key of qcut's own dicts in QCUT_INTERNAL_KEYS "
            "(and neither may be an argument *value*)")

    def test_no_registered_field_is_dead(self):
        """A registered field nobody reads is a declaration that stopped being
        true — the same failure as a schema version that no longer matches."""
        reads = set(get_literal_keys(TREE)) | set(subscript_keys(TREE))
        dead = (sync.QODER_FIELDS - reads
                - registry_constant_values(sync) - DYNAMIC_READS)
        self.assertEqual(dead, frozenset(),
                         f"registered but never read: {sorted(dead)}")

    def test_the_registry_covers_every_read(self):
        # Positive control: a scan over an empty result would pass vacuously, so
        # pin that the parser really is reading a large registered surface.
        self.assertGreaterEqual(
            len(set(get_literal_keys(TREE)) | set(subscript_keys(TREE))), 30)

    def test_each_usage_field_lands_in_a_column(self):
        """A field can be registered, read, and still indexed into nothing: the
        INSERT lists its columns by hand. Two cache fields are also *renamed* on
        the way in, so the map is explicit rather than an identity."""
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        db.ensure_schema(conn)
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(model_calls)")}
        conn.close()
        stored = {f: f for f in sync.USAGE_FIELDS}
        stored["cache_read_input_tokens"] = "cache_read_tokens"
        stored["cache_creation_input_tokens"] = "cache_creation_tokens"
        missing = {f: c for f, c in stored.items() if c not in cols}
        self.assertEqual(missing, {},
                         f"usage fields with no column to persist them: {missing}")


class DispatchTypesAreRegisteredTest(unittest.TestCase):
    def test_dispatch_chain_matches_the_registry(self):
        found, holders = dispatch_types(TREE)
        self.assertEqual(
            found, set(sync.CLAIMED_RECORD_TYPES),
            "the registry and the if/elif chain disagree about which record "
            f"types are claimed (branching on {sorted(holders)}): "
            f"only in code={sorted(found - sync.CLAIMED_RECORD_TYPES)}, "
            f"only in registry="
            f"{sorted(sync.CLAIMED_RECORD_TYPES - found)}")

    def test_noop_types_are_not_claimed_twice(self):
        """A type cannot be both "deliberately ignored" and "handled": the first
        means it is counted as understood, the second means it is parsed."""
        overlap = sync.NOOP_RECORD_TYPES & sync.CLAIMED_RECORD_TYPES
        self.assertEqual(overlap, frozenset(),
                         f"record types in both sets: {sorted(overlap)}")


class CouplingIsCentralizedTest(unittest.TestCase):
    """Layout, separators and tool names must be reached through the registry."""

    def test_no_bare_literal_passed_to_a_format_method(self):
        offenders = format_method_literals(TREE)
        self.assertEqual(
            offenders, [],
            "inlined format coupling — use the registry constant instead: "
            + ", ".join(f"L{ln}.{meth}({val!r})" for ln, meth, val in offenders))

    def test_no_bare_literal_path_segment(self):
        offenders = path_division_literals(TREE)
        self.assertEqual(
            offenders, [],
            "path segments inlined at the division site; register them: "
            + where(offenders))

    def test_transcript_line_separator_is_not_retyped(self):
        # b"" is excluded on purpose: an empty literal is how a buffer is
        # initialised, and it carries no format claim to centralize.
        offenders = [p for p in bytes_literals(TREE)
                     if p[1] and p[1] != sync.TRANSCRIPT_LINE_SEP]
        self.assertEqual(offenders, [],
                         "a bytes literal other than the registered JSONL "
                         "separator: " + where(offenders))

    def test_source_constants_are_actually_used(self):
        """Guards against the registry becoming a second schema version: a
        constant declared, plausible-looking, and read by nothing."""
        missing = [n for n in SOURCE_CONSTANTS if not hasattr(sync, n)]
        self.assertEqual(missing, [], f"registry constant removed: {missing}")
        used = referenced_names(TREE)
        unused = [n for n in SOURCE_CONSTANTS if n not in used]
        self.assertEqual(unused, [], f"declared and never used: {unused}")


if __name__ == "__main__":
    unittest.main()
