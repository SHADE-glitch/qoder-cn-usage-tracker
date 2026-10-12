"""test_readme_counts.py — every test count printed in the READMEs is the real count.

Both pages document the suite table (``| test_sync.py | 39 | …``) and restate the total
in two places — ``# Ran 165 tests … OK`` next to the discovery command, and the
``scripts/tests/`` row of the repository-layout table. All of it is typed by hand, which
is exactly how a page goes stale: a suite gains a case, nobody counts again.

The guard counts the real thing instead of trusting the page: it parses
``scripts/tests/test_*.py`` and reads the ``def test*`` methods off the syntax tree, then
asserts that every documented number — each row and each total, in each language — equals
what it found. Set equality makes the reverse direction fail too: a suite on disk with no
row is as much drift as a row pointing at a deleted file.

Why parsing and not ``unittest``'s own loader (the obvious instrument): two of the suites
import ``textual``, which a bare interpreter does not have, and this guard is documented
as stdlib-only so it runs on the same interpreter the CI matrix checks. The two readings
were cross-checked before this was written — parsed counts equal loader counts for every
suite on disk, this file's included — and the last case keeps that equivalence under
control, so the counter cannot quietly drift away from what the runner reports.

The suite reads repository files only: the two README pages and the test sources under
``scripts/tests/``. It opens no database, no ``~/.qoder-cn``, and needs no temp tree.
"""

from __future__ import annotations

import ast
import re
import sys
import types
import unittest
from pathlib import Path

# Same convention as the other suites: the start directory of discovery on sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parent))

SCRIPTS = Path(__file__).resolve().parents[1]
REPO = SCRIPTS.parent
TESTS_DIR = SCRIPTS / "tests"
PAGES = ("README.md", "README.zh-CN.md")

#: One row of the suite table: `| `test_sync.py` | 39 | <what it covers> |`.
ROW = re.compile(r"^\| `(test_\w+)\.py` \| (\d+) \|", re.M)

#: Every phrasing in which either page states a total. A total printed in a form not
#: listed here is a gap in the check, so the labels are kept in the failure message.
TOTALS = (
    ("Ran N tests", re.compile(r"Ran (\d+) tests")),
    ("the N tests above", re.compile(r"the (\d+) tests above")),
    ("上述 N 个用例", re.compile(r"上述 (\d+) 个用例")),
)


def read_source(path):
    return Path(path).read_text(encoding="utf-8")


def counted_tests(path):
    """How many cases `unittest` will report for one suite file.

    The rules are the loader's own, read off the syntax tree: a ``def test*`` that is a
    direct child of a class statement reachable from the module body counts. A helper
    method, a function nested inside a test, and a class nested inside a class are all
    invisible to the loader, so they are invisible here too. Prefix is ``test`` (the
    loader's ``testMethodPrefix``), not ``test_``, so a method named ``testFoo`` cannot
    hide from the count.
    """
    return _count_methods(ast.parse(read_source(path), filename=str(path)).body)


def _count_methods(body):
    total = 0
    for node in body:
        if isinstance(node, ast.ClassDef):
            total += sum(1 for statement in node.body
                         if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef))
                         and statement.name.startswith("test"))
        elif isinstance(node, (ast.If, ast.Try, ast.With)):
            for part in (node.body, getattr(node, "orelse", None) or [],
                         getattr(node, "finalbody", None) or []):
                total += _count_methods(part)
            for handler in getattr(node, "handlers", None) or []:
                total += _count_methods(handler.body)
    return total


def real_counts(tests_dir=None):
    """{suite stem: real case count} for every suite on disk."""
    directory = Path(tests_dir) if tests_dir else TESTS_DIR
    return {path.stem: counted_tests(path) for path in sorted(directory.glob("test_*.py"))}


def table(text):
    """{suite stem: count claimed by the page}."""
    return {stem: int(count) for stem, count in ROW.findall(text)}


def printed_totals(text):
    """[(phrasing, number)] for every total the page states."""
    return [(label, int(found))
            for label, pattern in TOTALS for found in pattern.findall(text)]


def disagreements(claimed, real):
    """{stem: (claimed, real)} — the pure comparison, so it can be controlled."""
    return {stem: (number, real[stem]) for stem, number in claimed.items()
            if stem in real and real[stem] != number}


def row_markdown(stem, count):
    """A paste-ready row, so fixing the page is not a formatting exercise."""
    return "| `%s.py` | %d | <what it covers> |" % (stem, count)


class ReadmeCountsTest(unittest.TestCase):
    def page(self, name):
        return read_source(REPO / name)

    def test_every_suite_on_disk_has_a_row_in_the_english_readme(self):
        rows, real = table(self.page("README.md")), real_counts()
        self.assertEqual(
            set(rows), set(real),
            "the README table and scripts/tests/ are not the same set — "
            "rows to add: %s; rows to drop (no such suite): %s"
            % ([row_markdown(stem, real[stem]) for stem in sorted(set(real) - set(rows))]
               or "none",
               sorted(set(rows) - set(real)) or "none"))

    def test_each_documented_row_matches_the_real_count(self):
        wrong = disagreements(table(self.page("README.md")), real_counts())
        self.assertEqual(
            wrong, {},
            "README.md's table disagrees with scripts/tests/ (claimed, real): %s — "
            "update the page, or the suite it describes" % (wrong,))

    def test_the_printed_totals_match_the_real_total(self):
        real = real_counts()
        total = sum(real.values())
        for name in PAGES:
            found = printed_totals(self.page(name))
            self.assertTrue(found,
                            "%s no longer states a total any check can see (%s)"
                            % (name, [label for label, _ in TOTALS]))
            for label, claimed in found:
                self.assertEqual(
                    claimed, total,
                    "%s says '%s' = %d, but the suites on disk hold %d cases "
                    "(%d suites) — every documented number must be the real one"
                    % (name, label, claimed, total, len(real)))

    def test_both_pages_carry_the_same_table_and_the_same_totals(self):
        """The two READMEs are one document: the same suites, the same numbers.

        Compared as numbers in document order, never as phrasings — the English page
        states its second total as "the 261 tests above" and the Chinese one as
        "上述 261 个用例", and both are correct. An instrument that compared the wording
        would fail on every honest translation; only the value is load-bearing here.
        """
        en, zh = self.page("README.md"), self.page("README.zh-CN.md")
        self.assertEqual(
            table(en), table(zh),
            "the suite tables disagree: only README.md -> %s; only "
            "README.zh-CN.md -> %s" % ({k: v for k, v in table(en).items()
                                        if table(zh).get(k) != v} or "none",
                                        {k: v for k, v in table(zh).items()
                                         if table(en).get(k) != v} or "none"))
        en_totals, zh_totals = printed_totals(en), printed_totals(zh)
        self.assertEqual(
            [number for _label, number in en_totals],
            [number for _label, number in zh_totals],
            "the two pages state different totals in the same order: README.md %s vs "
            "README.zh-CN.md %s" % (en_totals, zh_totals))
        self.assertEqual(
            len(en_totals), len(zh_totals),
            "one page dropped a place where the total is printed: README.md has %s, "
            "README.zh-CN.md has %s" % (en_totals, zh_totals))

    def test_the_baseline_table_carries_the_same_counts_as_the_readmes(self):
        """`docs/maintenance/baseline.md` restates the suite table, so it drifts too.

        The page's own instruction is "re-measure before restating"; a number under
        that heading that no check reads is exactly the stale guarantee it warns
        about. Compared on values, never on prose, because the prose is the point of
        the page.
        """
        real = real_counts()
        base = read_source(REPO / "docs" / "maintenance" / "baseline.md")
        rows = table(base)
        self.assertEqual(
            set(rows), set(real),
            "baseline.md's suite table and scripts/tests/ are not the same set — "
            "rows to add: %s; rows to drop: %s"
            % ([row_markdown(stem, real[stem]) for stem in sorted(set(real) - set(rows))]
               or "none", sorted(set(rows) - set(real)) or "none"))
        wrong = disagreements(rows, real)
        self.assertEqual(wrong, {},
                         "baseline.md's table disagrees with scripts/tests/ "
                         "(claimed, real): %s" % (wrong,))
        found = re.search(r"\| \*\*total\*\* \| \*\*(\d+)\*\* \|", base)
        self.assertTrue(found, "baseline.md no longer states a total this check can see")
        self.assertEqual(
            int(found.group(1)), sum(real.values()),
            "baseline.md total = %s but the suites hold %d"
            % (found.group(1), sum(real.values())))

    def test_the_counter_is_the_same_instrument_as_the_loader(self):
        """Prove the parser counts what the runner runs, on a synthetic module.

        The shape is the one every qcut suite uses, with the three things the loader
        cannot see — a helper method, a function nested inside a test, a class nested
        inside a class — plus a module-level function, which unittest does not collect
        either. The control also proves the comparison reports a wrong number rather
        than shrugging at one.
        """
        source = (
            "import unittest\n"
            "class AlphaTest(unittest.TestCase):\n"
            "    def test_one(self): pass\n"
            "    def test_two(self): pass\n"
            "    def helper(self):\n"
            "        def test_nested_not_a_case(self): pass\n"
            "class BetaTest(unittest.TestCase):\n"
            "    def test_three(self): pass\n"
            "    class Inner(unittest.TestCase):\n"
            "        def test_hidden(self): pass\n"
            "if True:\n"
            "    class GammaTest(unittest.TestCase):\n"
            "        def test_four(self): pass\n"
            "def test_module_level_is_not_collected(): pass\n"
        )
        module = types.ModuleType("synthetic_suite")
        module.__file__ = "synthetic_suite.py"
        exec(compile(source, "synthetic_suite.py", "exec"), module.__dict__)
        loaded = unittest.TestLoader().loadTestsFromModule(module).countTestCases()
        self.assertEqual(
            _count_methods(ast.parse(source).body), loaded,
            "the syntax-tree counter and unittest's loader disagree on a module "
            "neither of them should count twice")
        self.assertEqual(loaded, 4, "the synthetic control itself changed shape")

        # The comparison bites: a page one case off reality must be reported, not
        # smoothed over, and a row naming no suite on disk must not pass silently.
        wrong = {"AlphaTest": 3, "BetaTest": 4}
        self.assertEqual(disagreements(wrong, {"AlphaTest": 4, "BetaTest": 4}),
                         {"AlphaTest": (3, 4)},
                         "disagreements() cannot see a wrong count")
        self.assertEqual(disagreements({"AlphaTest": 4}, {"AlphaTest": 4}), {},
                         "disagreements() reports a number that matches")
        self.assertEqual(
            set(table("| `test_sync.py` | 39 |\n| `test_gone.py` | 7 |\n")),
            {"test_sync", "test_gone"}, "the row parser lost a row")


if __name__ == "__main__":
    unittest.main()
