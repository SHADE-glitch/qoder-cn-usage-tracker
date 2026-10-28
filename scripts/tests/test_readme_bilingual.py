"""The two READMEs are one document in two languages, so their shape must not drift.

``AGENTS.md`` states the convention — "Edit both together and keep section order
aligned; a section added on one side alone is drift" — and until this guard existed
nothing but a human reviewer enforced it.

The check is structural, never textual. Section *titles* legitimately differ per
language (``✨ Features`` beside ``✨ 特性``), so comparing titles would fail on every
honest translation. What cannot legitimately differ is the **emoji marker that opens
each heading**: both pages put the same marker on the same section in the same order.
Pinning that sequence pins "same set of sections, same order", which is strictly
stronger than comparing counts — a section added, dropped *or moved* on one side alone
goes red here.

Two cases from the CodeBuddy original are deliberately not ported, because qcut's
files make them hollow or hostile:

* *status-code parity* — no HTTP status code appears in either qcut page, so the case
  could only ever assert ``set() == set()``: an instrument that cannot fire.
* *number parity* — a measurement is legitimately restated per language ("past the
  model's 400k window" beside "超出模型 40 万上下文"), so comparing digits would fail on
  honest translation. The numbers that must *not* move are test counts, and
  ``test_readme_counts.py`` owns exactly those.

stdlib ``unittest`` only, so it runs on a bare interpreter without ``textual`` and
under both ``python3 -m unittest discover -s scripts/tests`` and pytest. The pair cases
read the two READMEs; the link sweep at the end reads every tracked ``.md``. Nothing
here opens a database, ``~/.qoder-cn``, or a temp tree.
"""

from __future__ import annotations

import re
import subprocess
import tempfile
import unittest
from pathlib import Path
import sys
import _hermetic  # noqa: E402,F401 -- tripwire: the suite must never open the real database

# Same convention as the other suites: the start directory of discovery, so the
# module imports identically whether it is loaded by name or by path.
sys.path.insert(0, str(Path(__file__).resolve().parent))

REPO = Path(__file__).resolve().parents[2]
EN = REPO / "README.md"
ZH = REPO / "README.zh-CN.md"

#: A bilingual pair is `<name>.md` + `<name>.zh-CN.md` in the repository root.
PAIRS = tuple(
    (en, REPO / (en.stem + ".zh-CN.md"))
    for en in sorted(REPO.glob("*.md"))
    if (REPO / (en.stem + ".zh-CN.md")).is_file()
)

HEADING = re.compile(r"^(#{2,3}) (.+)$", re.M)
H2_TEXT = re.compile(r"^## (.+)$", re.M)
MARKER = re.compile(r"^\s*(\S+)")
TOC_ITEM = re.compile(r"^\s*-\s*\[([^\]]*)\]\(#([^)]+)\)", re.M)
INLINE_ANCHOR = re.compile(r"\]\(#([^)]+)\)")
FENCE = re.compile(r"^```[^\n]*\n.*?^```[ \t]*$\n?", re.M | re.S)

# GitHub's anchor: lowercase, drop everything that is not a letter, digit, space or
# hyphen, then spaces to hyphens. The emoji goes with it — but the variation selector
# (U+FE0F) and the zero-width joiner (U+200D) *survive*, hiding inside the anchor.
# Stripping them too (the obvious regex) reports every emoji heading as a dead link:
# a false positive on all 17 sections of each page.
_ANCHOR_KEEP = re.compile(r"[^\w \-️‍]+", re.U)
# A heading is not the only way to mint an anchor: `<a name="x"></a>` above a heading
# pins a fragment that survives a retranslation, which is how ocut keeps one
# `#known-limitations` working across both of its languages. A guard that read only
# headings would call such a link dead.
EXPLICIT_ANCHOR = re.compile(r"<[a-z][^>]*?\b(?:name|id)\s*=\s*[\"']([^\"']+)[\"']", re.I)


def read(path):
    return Path(path).read_text(encoding="utf-8")


def prose(text):
    """The markdown with fenced code blocks removed, so a `#` in a snippet is not a
    heading. qcut's pages carry eight fenced blocks between them."""
    return FENCE.sub("", text)


def marker_of(title):
    match = MARKER.match(title)
    return match.group(1) if match else ""


def anchor_of(title):
    return _ANCHOR_KEEP.sub("", title.strip().lower()).replace(" ", "-")


def sections(text):
    """`[(level, marker, title, body)]` in document order, fences ignored."""
    body = prose(text)
    found = list(HEADING.finditer(body))
    out = []
    for index, match in enumerate(found):
        stop = found[index + 1].start() if index + 1 < len(found) else len(body)
        out.append((len(match.group(1)), marker_of(match.group(2)),
                    match.group(2), body[match.end():stop]))
    return out


def markers(text, level=2):
    """The sequence of section markers at `level`, in document order."""
    return tuple(marker for lv, marker, _title, _body in sections(text) if lv == level)


def heading_anchors(text):
    """Every anchor the page mints: one per heading, plus any explicit ``<a name>``."""
    body = prose(text)
    return ({anchor_of(title) for _lv, _m, title, _b in sections(text)}
            | set(EXPLICIT_ANCHOR.findall(body)))


EXTERNAL = ("http://", "https://", "mailto:", "ftp://")
LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")


def documents():
    """Every tracked Markdown file, as absolute paths."""
    out = subprocess.run(["git", "ls-files", "*.md"], cwd=REPO,
                         capture_output=True, text=True, check=True)
    return [REPO / name for name in out.stdout.split() if (REPO / name).is_file()]


def links_of(text):
    """The target of every inline link in ``text``, fenced blocks removed."""
    return LINK.findall(prose(text))


def broken_links(docs, root=REPO):
    """Every relative link that points at nothing, and every fragment that misses.

    ``root`` is the boundary a link may not leave: a target outside it belongs to
    another repository. The control case passes its own temporary directory.
    """
    anchors = {}
    problems = []
    for src in docs:
        for target in links_of(read(src)):
            if target.startswith(EXTERNAL) or target.startswith("<"):
                continue
            path, _, fragment = target.partition("#")
            dest = (src.parent / path).resolve() if path else src.resolve()
            try:
                dest.relative_to(root)
            except ValueError:
                continue
            if not dest.exists():
                problems.append("%s: %s -> no such file"
                                % (src.relative_to(root), target))
                continue
            if not fragment or dest.is_dir() or dest.suffix != ".md":
                continue  # a directory or a source file: no headings to land on
            if dest not in anchors:
                anchors[dest] = heading_anchors(read(dest))
            if fragment not in anchors[dest]:
                problems.append("%s: %s -> %s has no heading `%s`"
                                % (src.relative_to(root), target,
                                   dest.relative_to(root), fragment))
    return problems


def dead_anchors(text):
    """In-page links (`](#anchor)`) with no heading to land on — TOC entries included.

    Deleting a section is the mistake this catches: the pair comparison above only
    checks the two files against *each other*, so removing one section from both
    READMEs passes it while leaving both tables of contents pointing at nothing.
    """
    anchors = heading_anchors(text)
    return [link for link in INLINE_ANCHOR.findall(prose(text)) if link not in anchors]


def toc_drift(text):
    """(sections the TOC should list, sections it does list) as marker sequences.

    The TOC section is found mechanically — the one whose body holds the list items —
    so nothing here depends on the words "Table of Contents" or "目录".
    """
    secs = sections(text)
    listed = [marker for _label, _anchor, marker in _toc_items(text)]
    index = max(range(len(secs)), key=lambda i: _count_in(secs[i][3])) if secs else None
    expected = [marker for i, (lv, marker, _t, _b) in enumerate(secs)
                if lv == 2 and i != index]
    return expected, listed


def _toc_items(text):
    return [(label, anchor, marker_of(label))
            for label, anchor in TOC_ITEM.findall(prose(text))]


def _count_in(body):
    return len(TOC_ITEM.findall(body))


class ReadmeBilingualTest(unittest.TestCase):
    """The pair, and each page against itself."""

    def test_both_readmes_exist_and_neither_is_one_sided(self):
        self.assertTrue(EN.is_file(), "missing README.md")
        self.assertTrue(ZH.is_file(), "missing README.zh-CN.md")
        self.assertIn("README.zh-CN.md", read(EN),
                      "the English entry must link the Chinese one")
        self.assertIn("README.md", read(ZH),
                      "the Chinese entry must point back at the English one")

    def test_the_section_marker_sequence_is_identical(self):
        """Same `##` sections, same order — the AGENTS.md rule, made mechanical."""
        en, zh = markers(read(EN)), markers(read(ZH))
        self.assertEqual(
            en, zh,
            "the two READMEs have drifted: sections in README.md but not "
            "README.zh-CN.md: %s; in README.zh-CN.md but not README.md: %s; "
            "present on both but in a different order (%s vs %s) — add or move the "
            "section on the other side"
            % (sorted(set(en) - set(zh)) or "none",
               sorted(set(zh) - set(en)) or "none", " ".join(en), " ".join(zh)))
        self.assertGreater(len(en), 1, "the marker extractor found no sections")

    def test_the_subsection_marker_sequence_is_identical(self):
        """`###` headings belong to the pair too — Install's optional-systemd one."""
        self.assertEqual(markers(read(EN), level=3), markers(read(ZH), level=3))

    def test_every_bilingual_pair_keeps_its_section_count(self):
        """Not just the READMEs: MAINTENANCE.md is a pair with the same rule."""
        self.assertTrue(PAIRS, "no bilingual pair found in the repository root")
        for en_path, zh_path in PAIRS:
            en, zh = len(H2_TEXT.findall(prose(read(en_path)))), \
                len(H2_TEXT.findall(prose(read(zh_path))))
            self.assertEqual(
                en, zh,
                "%s has %d '##' sections, %s has %d — a section added on one side "
                "alone is drift" % (en_path.name, en, zh_path.name, zh))

    def test_both_open_with_the_language_switcher(self):
        """The switcher is the one thing a reader needs before the first section."""
        for path, other in ((EN, "README.zh-CN.md"), (ZH, "README.md")):
            text = read(path)
            head = text[:text.find("\n## ")] if "\n## " in text else text
            self.assertIn(
                other, head,
                "%s must offer the language switcher (%s) before its first '##' "
                "section" % (path.name, other))

    def test_every_in_page_link_resolves_to_a_heading(self):
        """A link whose heading was deleted renders as a dead link on GitHub."""
        for path in (EN, ZH):
            dead = dead_anchors(read(path))
            self.assertEqual(dead, [],
                             "%s links these anchors with no heading: %s"
                             % (path.name, dead))

        # Control: an instrument that cannot report a known dead link is not a guard.
        broken = "## 🧹 Uninstall\n\n- [🤝 Contributing](#-contributing)\n"
        self.assertEqual(dead_anchors(broken), ["-contributing"],
                         "the checker cannot see a dead link, so it proves nothing")
        emoji = "## 🧹 Uninstall\n\n- [🧹 Uninstall](#-uninstall)\n"
        self.assertEqual(dead_anchors(emoji), [],
                         "the checker reports a dead link that is not dead")

    def test_the_toc_lists_every_section_once_in_order(self):
        """A section added to the body and not to the TOC is invisible on GitHub."""
        for path in (EN, ZH):
            expected, listed = toc_drift(read(path))
            self.assertEqual(
                listed, expected,
                "%s's table of contents does not walk its own sections in order: "
                "missing %s, extra %s" % (path.name,
                                          sorted(set(expected) - set(listed)) or "none",
                                          sorted(set(listed) - set(expected)) or "none"))

        # Control: a page with a section the TOC never mentions must be reported.
        drifted = ("## 📖 Table of Contents\n\n- [✨ Features](#-features)\n\n"
                   "## ✨ Features\n\n## 🧹 Uninstall\n")
        expected, listed = toc_drift(drifted)
        self.assertEqual((expected, listed), (["✨", "🧹"], ["✨"]),
                         "the checker cannot see a TOC that lost a section")

    def test_every_relative_link_in_every_document_resolves(self):
        """The pair's own links are checked above; this is every link in every page.

        A cross-file anchor is the case the pair check cannot see: a README points
        into ``docs/maintenance/compatibility.md#codebuddy``, and renaming that
        heading leaves every *in-page* link in the repository resolving. Nothing
        noticed until the sweep below existed.
        """
        problems = broken_links(documents())
        self.assertEqual([], problems,
                         "a document points at something that is not there:\n  "
                         + "\n  ".join(problems))

    def test_the_link_checker_can_report_a_break(self):
        """Control: a missing file, a missed fragment, and links that are fine."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "target.md").write_text("## Real\n", encoding="utf-8")
            page = root / "page.md"
            page.write_text(
                "## Real\n\n"
                "[ok](#real)\n"
                "[across](target.md#real)\n"
                "[dead](#nowhere)\n"
                "[nofile](missing.md)\n"
                "[nofrag](target.md#nowhere)\n"
                "\n```\n[fenced](#nowhere)\n```\n",
                encoding="utf-8")
            problems = broken_links([page], root=root)

        self.assertEqual(
            ["page.md: #nowhere -> page.md has no heading `nowhere`",
             "page.md: missing.md -> no such file",
             "page.md: target.md#nowhere -> target.md has no heading `nowhere`"],
            problems,
            "the checker missed a break, invented one, or read a fenced example as a link")


if __name__ == "__main__":
    unittest.main()
