"""
test_record_coverage.py — the recording-coverage guard.

Ported from cbut's file of the same name, re-aimed at this repository's record
format. The guarantee it holds, in qcut's own terms:

    a change to a watched production script must leave a trace in CHANGELOG.md,
    so that "the code moved and nobody recorded why" cannot pass silently.

**What had to change in the port.** cbut's record numbers its entries `D-001`,
`D-002`, … and closes each one with a `Commit <sha>` field, so its check is a
*citation*: every covered commit hash must appear in the record. qcut's record is
`## [<version>] — <date>` sections carrying `` - `kind` prose `` bullets — no ids
and no hashes (see CHANGELOG.md). Matching therefore runs on the **kind** axis, as
a floor: for each commit class the window contains, the record must hold at least
that many bullets of the matching kind. That is weaker than a hash citation, and
the weakness is stated rather than hidden — the guard proves a trace of the right
kind exists, not that a given commit is cited. Closing that gap means adding
hashes to CHANGELOG.md, which is a record-format change and not this file's call.
The `D-###` mechanics (id uniqueness, monotonicity, gaplessness, cross-document
`D-###` references, the five required fields per entry) are **not** ported at all:
they police an addressing scheme this project does not use, and importing them
would only teach this suite to fail on a CHANGELOG written the way this repo
writes it.

**Scope rule.** Commits whose subject starts with `feat` are **not** covered —
original project, no upstream, so a feature is the product and not a droppable
deviation. `Merge` commits are out for a structural reason: they record nothing of
their own, the commits they join are enumerated individually. Both exclusions are
categorical (by commit class), declared in source, and never a per-commit skip
flag.

**Anti-vacuity.** A check over an empty set proves nothing. A window with covered
commits and a record with no bullets fails outright; a window with no covered
commits *skips*, with the endpoints and the counts in its reason, and never reports
a pass; a repository with no readable history skips at class level for the same
reason. `test_vacuous_window_is_never_reported_as_a_pass` asserts that the third
branch answers "vacuous" and not "pass", so the distinction cannot be collapsed
later without this file going red.

**The enumerator.** The window is walked out of `git log`, never typed: nothing
here counts commits by hand, so a new commit cannot be forgotten by a stale
number. The floor is the repository's root commit, found with
`git rev-list --max-parents=0 HEAD` and deliberately not hardcoded — a literal sha
is an artifact of one clone and points at nothing in a fresh one, while the root
commit is a property of the history itself. The window is **closed at that end**
(the root is counted, not excluded): `root..HEAD` would drop the one commit that
carries every watched script into the repository for the first time, and in a
newly initialized repository — one commit, HEAD == root — it would leave the guard
nothing to read.

**Hygiene.** stdlib `unittest` only: no `textual`, no import of the product, no
database, no `~/.qoder-cn`. It runs `git` read-only (`rev-parse`, `rev-list`,
`log`, `ls-tree`) and reads CHANGELOG.md; the only writes are inside a /tmp
directory it creates and deletes. `subprocess` is not a violation here:
`test_privacy.py::ImportSurfaceTest` allowlists the four `scripts/qcut*.py`
production files by name — test files are outside its scope.

**How to provoke it.** Set `QCUT_RECORD_COVERAGE_REPO=/tmp/some-repo` to aim the
window/anchor logic at a fabricated checkout, or let `GuardProvocationTest` build
one in /tmp and destroy it (`test_a_watched_change_with_no_record_goes_red`,
`test_b_feat_class_commit_is_out_of_scope_even_on_a_watched_path`,
`test_c_empty_window_is_vacuous_and_never_silent`).
"""

import os
import re
import shutil
import subprocess
import tempfile
import unittest
from collections import Counter, namedtuple
from pathlib import Path

# ---------------------------------------------------------------- the surface

#: The checkout under review. Defaults to this repo (`scripts/tests/../..`);
#: overridable so the guard can be aimed at a fabricated history and *provoked*
#: rather than merely read.
REPO = Path(os.environ.get("QCUT_RECORD_COVERAGE_REPO")
            or Path(__file__).resolve().parents[2]).expanduser().resolve()

#: Production code a commit must touch to be worth a record. Same four scripts
#: `test_privacy.py` names, plus the bash dispatcher, which is load-bearing.
WATCHED_PATHS = ("bin/qcut", "scripts/qcut_db.py", "scripts/qcut-sync.py",
                 "scripts/qcut-tui.py", "scripts/qcut-stats.py")

RECORD_PATH = "CHANGELOG.md"

#: Kinds the record format admits, as the leading backtick token of a
#: `` - `kind` prose `` bullet. cbut's list plus `docs`, which this record uses.
RECORD_KINDS = frozenset({"chore", "docs", "fix", "guard", "perf", "revert", "taste"})

#: Commit classes carrying no recording obligation, by subject class only.
EXCLUDED_CLASSES = ((re.compile(r"^feat(\([^)]*\))?!?:"), "feat"),
                    (re.compile(r"^Merge\s"), "merge"))

#: Commit subject type -> record kind. AGENTS.md declares Conventional Commits
#: (`feat:` / `fix:` / `docs:` / `chore:`); the extra keys are spellings that
#: arrive anyway, mapped rather than dropped, so a type cannot escape by being
#: absent from this table.
COMMIT_TYPE_TO_KIND = {
    "fix": "fix", "hotfix": "fix", "bugfix": "fix",
    "perf": "perf",
    "taste": "taste", "style": "taste",
    "guard": "guard", "test": "guard",
    "revert": "revert",
    "chore": "chore", "ci": "chore", "build": "chore", "refactor": "chore",
    "docs": "docs",
}

SUBJECT_TYPE = re.compile(r"^([a-z][a-z0-9-]*)(\([^)]*\))?(!)?:\s*")
BARE_REVERT = re.compile(r"^revert\b", re.I)
# A bullet whose first token is a single lowercase word in backticks is a record.
# `- `qcut sync` parses …` is *not* one: the word-boundary here is what keeps the
# release-prose bullets from being counted as if they recorded something.
RECORD_BULLET = re.compile(r"^- `([a-z][a-z0-9_-]*)`[ \t]", re.M)
RECORD_SECTION = re.compile(r"^## \[([^\]]+)\][ \t]+[—-][ \t]+(\d{4}-\d{2}-\d{2})\s*$", re.M)
ANY_SECTION = re.compile(r"^## (.*)$", re.M)

Commit = namedtuple("Commit", "sha subject paths")
Bullet = namedtuple("Bullet", "kind line section")
Section = namedtuple("Section", "title date start end")
Coverage = namedtuple("Coverage", "verdict problems reason in_scope excluded "
                                  "unrecordable bullets sections")


class GitError(Exception):
    pass


# -------------------------------------------------------------------- git side

def git(root, *args):
    proc = subprocess.run(["git", "-C", str(root), *args],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        raise GitError("git %s: %s" % (" ".join(args), proc.stderr.strip()))
    return proc.stdout


def readable_history(root):
    """Narrow skip condition: only a checkout with no commits at all (or no
    checkout at all) is unreadable — everything else must produce a verdict."""
    try:
        git(root, "rev-parse", "--verify", "HEAD^{commit}")
        return True
    except (GitError, OSError):
        return False


def head_sha(root):
    return git(root, "rev-parse", "--verify", "HEAD^{commit}").strip()


def root_shas(root):
    """The window floor, computed from the history instead of copied from a file.

    A hardcoded sha belongs to one clone: it is meaningless in a fresh checkout,
    in a fork, and after a rewrite. `--max-parents=0 HEAD` re-derives the same
    commit from whatever history this checkout actually has.
    """
    return [line.strip() for line in git(root, "rev-list", "--max-parents=0", "HEAD").splitlines()
            if line.strip()]


def parse_log(raw):
    """`git log --format=%H\\x1f%s --name-only` output -> [Commit]."""
    commits, sha, subject, paths = [], None, "", []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        left, sep, right = line.partition("\x1f")
        if sep and re.fullmatch(r"[0-9a-f]{40}", left):
            if sha:
                commits.append(Commit(sha, subject, tuple(paths)))
            sha, subject, paths = left, right, []
        elif sha:
            paths.append(line)
    if sha:
        commits.append(Commit(sha, subject, tuple(paths)))
    return commits


def select_watched(commits, watched=WATCHED_PATHS):
    return [c for c in commits if set(c.paths) & set(watched)]


def window_commits(root, anchor):
    """Every commit reachable from HEAD at or after `anchor` that touches watched
    code — the window closed at both ends, see the module docstring."""
    listing = ("log", "--format=%H\x1f%s", "--name-only")
    tail = parse_log(git(root, *listing, "%s..HEAD" % anchor, "--", *WATCHED_PATHS))
    base = parse_log(git(root, *listing, "-1", anchor, "--", *WATCHED_PATHS))
    seen, out = set(), []
    for commit in base + tail:
        if commit.sha not in seen:
            seen.add(commit.sha)
            out.append(commit)
    return select_watched(out)


def tracked_at_head(root):
    return set(git(root, "ls-tree", "-r", "--name-only", "HEAD").splitlines())


# ------------------------------------------------------------------ record side

def changelog_text(root):
    path = root / RECORD_PATH
    assert path.exists(), "%s missing from %s" % (RECORD_PATH, root)
    return path.read_text(encoding="utf-8")


def record_sections(text):
    found = list(RECORD_SECTION.finditer(text))
    return [Section(m.group(1), m.group(2), m.start(),
                    found[i + 1].start() if i + 1 < len(found) else len(text))
            for i, m in enumerate(found)]


def record_bullets(text, sections=None):
    sections = record_sections(text) if sections is None else sections
    bullets = []
    for match in RECORD_BULLET.finditer(text):
        line = text.count("\n", 0, match.start()) + 1
        owner = next((s for s in sections if s.start <= match.start() < s.end), None)
        bullets.append(Bullet(match.group(1), line, owner.title if owner else None))
    return bullets


def classify(commit):
    """-> (bucket, detail); bucket is kind | excluded | untyped | unmapped."""
    subject = commit.subject
    for pattern, name in EXCLUDED_CLASSES:
        if pattern.match(subject):
            return "excluded", name
    if BARE_REVERT.match(subject):
        return "kind", "revert"
    match = SUBJECT_TYPE.match(subject)
    if not match:
        return "untyped", subject
    kind = COMMIT_TYPE_TO_KIND.get(match.group(1).lower())
    return ("kind", kind) if kind else ("unmapped", match.group(1).lower())


def evaluate(commits, record_text):
    """The whole guarantee, as a pure function of (commits, record text).

    Callable with an injected commit list, which is what makes the guard
    provokable without adding commits to a live repository.
    """
    sections = record_sections(record_text)
    bullets = record_bullets(record_text, sections)
    in_scope, excluded, unrecordable = [], [], []
    for commit in commits:
        bucket, detail = classify(commit)
        if bucket == "excluded":
            excluded.append((commit, detail))
        elif bucket == "kind":
            in_scope.append((commit, detail))
        else:
            unrecordable.append((commit, bucket, detail))

    if not in_scope and not unrecordable:
        return Coverage("vacuous", [], _vacuous_reason(commits, excluded),
                        in_scope, excluded, unrecordable, bullets, sections)

    have = Counter(b.kind for b in bullets)
    problems = []
    if not bullets:
        problems.append(
            "the record holds no `` - `kind` prose `` bullet at all while the window "
            "carries %d covered commit(s): a check over an empty set proves nothing. "
            "Write the entries." % (len(in_scope) + len(unrecordable)))
    need = Counter(kind for _, kind in in_scope)
    for kind in sorted(need):
        if have.get(kind, 0) < need[kind]:
            subjects = "\n".join("      %s %s" % (c.sha[:7], c.subject)
                                 for c, k in in_scope if k == kind)
            problems.append(
                "kind %r: %d watched commit(s) in the window, %d matching bullet(s) in "
                "CHANGELOG.md — at least %d more:\n%s"
                % (kind, need[kind], have.get(kind, 0), need[kind] - have.get(kind, 0),
                   subjects))
    for commit, bucket, detail in unrecordable:
        problems.append(
            "%s %s: a watched production script changed under a subject this guard "
            "cannot map to a record kind (%s: %r). Name the commit type or use one "
            "of %s." % (commit.sha[:7], commit.subject, bucket, detail,
                         sorted(set(COMMIT_TYPE_TO_KIND))))
    return Coverage("pass" if not problems else "fail", problems, "", in_scope,
                    excluded, unrecordable, bullets, sections)


def _vacuous_reason(commits, excluded):
    """The reason a window with nothing to judge reports itself as *vacuous*.

    It names the watched list and the counts so a reader can tell "no history was
    offered" from "history was offered and matched nothing".
    """
    return ("no commit in the window touches a watched production script "
            "(watched: %s; commits offered: %d, excluded by class: %d %s) — skipping, "
            "because a vacuous window must never be reported as a pass"
            % (", ".join(WATCHED_PATHS), len(commits), len(excluded),
               sorted({name for _, name in excluded}) or "[]"))


def verdict_for(root):
    """Real-repo entry point: anchor from history, enumerate, judge."""
    anchor = root_shas(root)
    if len(anchor) != 1:
        return None, None, Coverage("fail", ["history has %d root commits reachable "
                                             "from HEAD, so the window floor is "
                                             "ambiguous" % len(anchor)],
                                    "", [], [], [], [], [])
    commits = window_commits(root, anchor[0])
    return anchor[0], head_sha(root), evaluate(commits, changelog_text(root))


NO_HISTORY = not readable_history(REPO)
NO_HISTORY_REASON = ("%s exposes no readable git history (no checkout, or no commit "
                     "on HEAD): the guard has no window it could judge, and "
                     "reporting a pass for a window it could not see is the exact "
                     "failure mode this file exists to prevent" % REPO)


# ---------------------------------------------------------------- real repository

@unittest.skipUnless(not NO_HISTORY, NO_HISTORY_REASON)
class RecordCoverageAgainstRepositoryTest(unittest.TestCase):
    def setUp(self):
        self.anchor, self.head, self.cov = verdict_for(REPO)

    def test_window_anchor_is_derived_and_reachable(self):
        """If the floor stops being one unambiguous, reachable, parentless commit,
        every count taken from it is a number the guard made up."""
        roots = root_shas(REPO)
        self.assertEqual(len(roots), 1,
                         "expected exactly one root commit reachable from HEAD, found %d: %s"
                         % (len(roots), roots))
        self.assertEqual(self.anchor, roots[0])
        # It really is a root: `rev-list --parents` prints the sha alone.
        self.assertEqual(git(REPO, "rev-list", "--parents", "-n", "1", self.anchor).split(),
                         [self.anchor], "the anchor has a parent, so it is not the root")
        # And it is the floor of this HEAD, not an orphan someone grafted in.
        try:
            git(REPO, "merge-base", "--is-ancestor", self.anchor, "HEAD")
        except GitError as exc:
            self.fail("the computed root is not an ancestor of HEAD: %s" % exc)

    def test_watched_path_list_is_not_stale(self):
        """A watched path that no longer exists silently shrinks the window to
        nothing — the guard would then certify a repository it cannot see."""
        tracked = tracked_at_head(REPO)
        missing = [p for p in WATCHED_PATHS if p not in tracked]
        self.assertFalse(missing,
                         "watched path(s) %s are not tracked at HEAD; fix the list "
                         "rather than let the enumeration go quiet" % ", ".join(missing))

    def test_window_enumerates_from_git_not_from_a_typed_count(self):
        """Each offered commit must really touch a watched path and really exist."""
        if not self.cov.in_scope and not self.cov.unrecordable:
            self.skipTest(self.cov.reason)
        tracked = tracked_at_head(REPO)
        for commit, _kind in self.cov.in_scope:
            with self.subTest(commit=commit.sha[:7]):
                self.assertTrue(set(commit.paths) & set(WATCHED_PATHS),
                                "enumerated a commit that touched nothing watched")
                self.assertTrue(set(commit.paths) <= tracked, "named a path git does not track")
                self.assertEqual(len(commit.sha), 40)

    def test_record_sections_and_bullet_kinds_are_well_formed(self):
        """The record has a shape; drift in it is a failure, not a re-read."""
        text = changelog_text(REPO)
        headers = ANY_SECTION.findall(text)
        self.assertTrue(headers, "CHANGELOG.md has no '## ' section header at all")
        self.assertEqual(len(self.cov.sections), len(headers),
                         "a '## ' header is not of the form '## [<version>] — YYYY-MM-DD'")
        self.assertTrue(self.cov.bullets,
                        "not one `` - `kind` prose `` bullet in the record: the "
                        "coverage floor below would be checked against nothing")
        for bullet in self.cov.bullets:
            with self.subTest(line=bullet.line):
                self.assertIn(bullet.kind, sorted(RECORD_KINDS),
                              "kind %r on line %d is outside the declared vocabulary"
                              % (bullet.kind, bullet.line))
                self.assertIsNotNone(bullet.section,
                                     "bullet on line %d sits outside any versioned "
                                     "section, so no release claims it" % bullet.line)

    def test_command_bullets_are_not_counted_as_records(self):
        """Positive control for the bullet parser: `` - `qcut sync` parses … ``
        records nothing. If it were counted, a real gap could hide behind a prose
        bullet and the floor below would be met by text that owes no commit."""
        text = changelog_text(REPO)
        tokens = re.findall(r"^- `([^`]*)`[ \t]", text, re.M)
        word_like = [t for t in tokens if re.fullmatch(r"[a-z][a-z0-9_-]*", t)]
        multi_token = [t for t in tokens if not re.fullmatch(r"[a-z][a-z0-9_-]*", t)]
        self.assertTrue(multi_token,
                        "this record has no multi-token leading backtick bullet, so "
                        "the control measures nothing — say so by deleting it")
        self.assertFalse(set(multi_token) & set(RECORD_KINDS),
                         "a command-name bullet %r collides with a kind" % multi_token)
        self.assertEqual(Counter(b.kind for b in record_bullets(text)), Counter(word_like),
                         "the parser and the rule it documents disagree about what a "
                         "record bullet is")

    def test_every_watched_change_leaves_a_trace(self):
        """The ported guarantee itself."""
        if self.cov.verdict == "vacuous":
            self.skipTest(self.cov.reason)
        self.assertEqual(self.cov.verdict, "pass",
                         "the record does not cover the window:\n    "
                         + "\n    ".join(self.cov.problems))

    def test_excluded_classes_are_out_of_scope_by_class_not_by_convenience(self):
        """The exemption must stay visible: a `feat` or `Merge` commit that lands on
        a watched path belongs in `excluded`, and must never be judged against the
        floor. A commit that only *looks* excluded (no class prefix) still is."""
        for commit, kind in self.cov.in_scope:
            for pattern, name in EXCLUDED_CLASSES:
                with self.subTest(commit=commit.sha[:7], cls=name):
                    self.assertIsNone(pattern.match(commit.subject),
                                      "a %s-class commit was held to the floor" % name)
        declared = sorted({name for _, name in EXCLUDED_CLASSES})
        for commit, name in self.cov.excluded:
            with self.subTest(commit=commit.sha[:7]):
                self.assertIn(name, declared,
                              "excluded %r, which is not a class this file declares" % name)


# ------------------------------------------------------------------ provocation

def run_git(cwd, *args):
    return subprocess.run(["git", "-c", "user.name=coverage-probe",
                           "-c", "user.email=probe@invalid",
                           "-c", "commit.gpgsign=false", *args],
                          cwd=str(cwd), capture_output=True, text=True, check=True).stdout


#: A record that is *well formed and still says nothing*: one `docs` bullet, so
#: every format check passes, plus a release-prose bullet shaped like the ones in
#: the real CHANGELOG.md. A probe record that merely asserts "the record is not
#: empty" would let the guard pass on text that owes no commit — which is the lie
#: this file hunts.
PROBE_RECORD = ("# Changelog\n\n## [Unreleased] — 2026-10-13\n\n"
                "- `docs` the probe section is well formed and names no code.\n"
                "- `qcut sync` parses a probe tree; prose, not a record bullet.\n")

RECORDED_PROBE = ("# Changelog\n\n## [Unreleased] — 2026-10-13\n\n"
                  "- `docs` the probe section is well formed and names no code.\n"
                  "- `qcut sync` parses a probe tree; prose, not a record bullet.\n"
                  "- `fix` `window_bounds` started at UTC, so a local day read as two.\n")


class GuardProvocationTest(unittest.TestCase):
    """The guard aimed at fabricated history in a throwaway /tmp repository, so
    that red is *observed* rather than asserted in prose. Nothing here commits to
    the real repository, and every directory created here is removed here."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="qcut-record-coverage-", dir="/tmp"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def make_repo(self, changelog, watched=True):
        """A throwaway checkout laid out like this one, with every watched path
        tracked, so a red reading can only come from the coverage rule and never
        from a probe repo that merely lacks the files. `watched=False` is the
        opposite case: a checkout with no production script at all, which is how
        the empty window is provoked."""
        repo = self.tmp / "repo"
        repo.mkdir(parents=True)
        run_git(repo, "init", "-q", "--initial-branch=master")
        for rel in (WATCHED_PATHS if watched else ()):
            path = repo / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("VALUE = 1\n", encoding="utf-8")
        (repo / "README.md").write_text("# probe\n", encoding="utf-8")
        (repo / RECORD_PATH).write_text(changelog, encoding="utf-8")
        return repo

    def commit(self, repo, subject, *rel_paths):
        for rel in rel_paths:
            path = repo / rel
            path.write_text(path.read_text(encoding="utf-8") + "# touched\n", encoding="utf-8")
        run_git(repo, "add", "-A")
        run_git(repo, "commit", "-q", "-m", subject)

    def assert_red(self, repo, needle):
        _anchor, _head, cov = verdict_for(repo)
        self.assertEqual(cov.verdict, "fail",
                         "expected red for %s, got %s (%s)" % (repo, cov.verdict, cov.reason))
        self.assertTrue(any(needle in p for p in cov.problems),
                        "red, but for the wrong reason: %s" % cov.problems)
        return cov

    def test_a_watched_change_with_no_record_goes_red(self):
        repo = self.make_repo(PROBE_RECORD)
        self.commit(repo, "fix: qcut_db window bounds were off by a day", "scripts/qcut_db.py")
        cov = self.assert_red(repo, "kind 'fix'")
        self.assertIn("1 watched commit(s) in the window, 0 matching bullet(s)",
                      " ".join(cov.problems))
        self.assertEqual([b.kind for b in cov.bullets], ["docs"],
                         "the fabricated record stopped being well-formed-but-short")
        self.assertEqual(cov.in_scope[0][1], "fix")

        # the trace clears it: the obligation is the record, not the commit count
        self.commit(repo, "chore: record the window fix", RECORD_PATH)
        (repo / RECORD_PATH).write_text(RECORDED_PROBE, encoding="utf-8")
        _anchor, _head, fixed = verdict_for(repo)
        self.assertEqual(fixed.verdict, "pass", fixed.problems)

    def test_b_feat_class_commit_is_out_of_scope_even_on_a_watched_path(self):
        repo = self.make_repo(PROBE_RECORD)
        self.commit(repo, "feat: qcut inventory subcommand", "scripts/qcut_db.py")
        _anchor, _head, cov = verdict_for(repo)
        self.assertEqual(cov.in_scope, [], "a feat commit was held to the floor")
        self.assertEqual([name for _, name in cov.excluded], ["feat"])
        self.assertEqual(cov.verdict, "vacuous", "an excluded-only window must not pass")
        # feat + fix in one window: only the fix is owed a trace.
        self.commit(repo, "fix: credits aggregate printed 0 for an absent field",
                    "scripts/qcut_db.py")
        _anchor, _head, both = verdict_for(repo)
        self.assertEqual([kind for _, kind in both.in_scope], ["fix"])
        self.assertEqual([name for _, name in both.excluded], ["feat"])
        self.assertEqual(both.verdict, "fail", both.problems)

    def test_c_empty_window_is_vacuous_and_never_silent(self):
        """A repository whose whole history stays off the watched paths: the guard
        has nothing to judge, and says so instead of printing a clean result."""
        repo = self.make_repo(PROBE_RECORD, watched=False)
        self.commit(repo, "docs: add the probe readme", "README.md")
        self.assertEqual(window_commits(repo, root_shas(repo)[0]), [],
                         "the fabricated window is not empty, so this proves nothing")
        _anchor, _head, cov = verdict_for(repo)
        self.assertEqual(cov.verdict, "vacuous")
        self.assertNotEqual(cov.verdict, "pass")
        self.assertIn("never be reported as a pass", cov.reason)
        self.assertIn("no commit in the window touches a watched production script",
                      cov.reason)

    def test_vacuous_window_is_never_reported_as_a_pass(self):
        """Pure-logic version: the three verdicts are distinct, so no refactor can
        quietly collapse 'nothing to judge' into 'judged and clean'."""
        record = ("- `fix` one\n- `fix` two\n")
        self.assertEqual(evaluate([], record).verdict, "vacuous")
        self.assertEqual(evaluate([], "").verdict, "vacuous")
        self.assertEqual(evaluate([], "").reason,
                         _vacuous_reason([], []))
        self.assertEqual(evaluate([Commit("a" * 40, "feat: x", ("bin/qcut",))], "").verdict,
                         "vacuous")
        self.assertEqual(evaluate([Commit("b" * 40, "fix: y", ("bin/qcut",))], record).verdict,
                         "pass")
        self.assertEqual(evaluate([Commit("c" * 40, "fix: z", ("bin/qcut",))], "").verdict,
                         "fail")

    def test_untyped_and_unmapped_subjects_are_a_failure_not_an_oversight(self):
        record = "- `fix` anything\n- `chore` anything\n"
        for subject in ("tidy the parser", "wip: whatever", "refct: mispelled type"):
            with self.subTest(subject=subject):
                cov = evaluate([Commit("d" * 40, subject, ("bin/qcut",))], record)
                self.assertEqual(cov.verdict, "fail")
                self.assertIn("cannot map to a record kind", " ".join(cov.problems))
        # mapped spellings are honoured, not silently dropped
        for subject, kind in (("test: add a case", "guard"), ("ci: pin the floor", "chore"),
                              ("Revert \"fix: broke it\"", "revert")):
            with self.subTest(subject=subject):
                cov = evaluate([Commit("e" * 40, subject, ("bin/qcut",))], record)
                self.assertEqual([k for _, k in cov.in_scope], [kind], subject)

    def test_floor_is_a_count_not_a_presence_check(self):
        """Three `fix` commits against one `fix` bullet is under-recording, and the
        guard says so instead of noting that at least one bullet exists."""
        commits = [Commit("f" * 40, "fix: number %d" % i, ("scripts/qcut_db.py",))
                   for i in range(3)]
        cov = evaluate(commits, "- `fix` the one that got recorded\n")
        self.assertEqual(cov.verdict, "fail")
        self.assertIn("3 watched commit(s) in the window, 1 matching bullet(s)",
                      " ".join(cov.problems))


if __name__ == "__main__":
    unittest.main()
