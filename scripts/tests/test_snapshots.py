"""test_snapshots.py — a destructive step must always have a way back.

The owner's rule (AGENTS.md, verification tier L2) is that a destructive step
takes a snapshot first, and `qcut sync --full` is the destructive step: it clears
every indexed table and re-reads the transcripts from scratch. These tests assert
both halves of that promise at the data layer — `scripts/qcut_db.py` — plus the
retention rule that keeps five of them:

* the snapshot is taken **before** the change, so it still holds the state that
  is about to be lost (one taken afterwards is worthless);
* it is a *working database*, not a byte copy of a file whose newest committed
  frames are still sitting in the `-wal` beside it;
* restoring it brings the rows back, and the state it overwrites is kept too, so
  restoring the wrong name is itself undoable;
* pruning deletes nothing this module did not make.

Standard library only — no `textual`. Every database here is built inside a
`TempTree` temp directory, and the cases that exercise a rebuild drive the real
indexer over a throwaway transcript, never over `~/.qoder-cn`.

What is **not** here, and why: `test_usage.py::SchemaTest` already holds the
plain `snapshot` → `list_backups` → `restore` round trip on a hand-inserted row,
and `ensure_schema`/`migrate`/`reset` behaviour, so none of that is re-asserted.
There is no pre-*migration* snapshot case either: `SCHEMA_VERSION` is 1 and
`migrate` has no `ALTER` path to guard yet — it needs its own case the day a v2
lands.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import unittest
from pathlib import Path

# `_helpers` puts scripts/ on sys.path, so it must be imported before qcut_db.
from _helpers import TempTree, assistant, tool_result, tool_use, usage

import qcut_db as db
import _hermetic  # noqa: E402,F401 -- tripwire: the suite must never open the real database

TRANSCRIPT = "projects/-work-demo/s1.jsonl"

# Any fixed timestamp: these rows are read back by name, never by time.
TS = 1_790_000_000_000


def tool_names(path):
    """The `tool_calls` rows of a database file.

    ``None`` when the file has no such table, which is what lets the WAL case use
    it as a control: a byte copy of a database whose newest frames are still in
    the `-wal` beside it is not the same database.
    """
    conn = sqlite3.connect(str(path))
    try:
        return sorted(row[0] for row in conn.execute("SELECT tool_name FROM tool_calls"))
    except sqlite3.OperationalError:
        return None
    finally:
        conn.close()


def managed(db_path):
    """The names of the snapshots this module manages for one database."""
    return sorted(p.name for p in db.list_backups(db_path))


def make_snapshot_file(path):
    """A plausible managed snapshot, as an older release would have left it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE t(x)")
    conn.commit()
    conn.close()
    return path


class RebuildSnapshotTest(unittest.TestCase):
    """`sync --full` clears the index, so it must copy the index first."""

    def setUp(self):
        self.t = TempTree(self)
        self.t.write(TRANSCRIPT, [
            assistant("s1", "m1", usage=usage(input_tokens=5),
                      content=[tool_use("c1", "Bash", command="ls")]),
            tool_result("r1", "c1", kind="completed"),
        ])
        self.t.sync()

    def add_a_row_no_transcript_can_produce(self, name="Vanished"):
        """Row 'Bash' comes back from the transcripts; this one cannot.

        The only way to tell a snapshot of the *before* state from a snapshot of
        the after state is a row that exists in exactly one of them.
        """
        conn = self.t.conn()
        conn.execute("INSERT INTO tool_calls(call_id, tool_name, ts) VALUES(?,?,?)",
                     ("gone", name, TS))
        conn.commit()
        conn.close()

    def rebuilt(self):
        self.t.sync(full=True)
        return [p for p in db.list_backups(self.t.db_path) if "before-full-sync" in p.name]

    def test_a_full_rebuild_snapshots_the_state_it_is_about_to_erase(self):
        self.add_a_row_no_transcript_can_produce()
        self.assertEqual(tool_names(self.t.db_path), ["Bash", "Vanished"])

        snaps = self.rebuilt()
        self.assertEqual(len(snaps), 1, f"expected one rebuild snapshot: {managed(self.t.db_path)}")
        self.assertTrue(all(str(p).startswith(str(self.t.root)) for p in snaps),
                        f"a snapshot landed outside the temp tree: {snaps}")
        # The rebuild did erase the row...
        self.assertEqual(tool_names(self.t.db_path), ["Bash"])
        # ...and the copy still holds it. Read it out of the file rather than
        # trusting the name: a snapshot taken after `reset()` would be called
        # `before-full-sync` too, and would be worthless.
        self.assertEqual(tool_names(snaps[0]), ["Bash", "Vanished"],
                         "the snapshot holds the post-rebuild state, so it was "
                         "taken after the tables were cleared and cannot roll a "
                         "rebuild back")

    def test_restoring_that_snapshot_gives_back_the_rows_the_rebuild_erased(self):
        self.add_a_row_no_transcript_can_produce()
        snaps = self.rebuilt()
        self.assertEqual(tool_names(self.t.db_path), ["Bash"])

        db.restore(snaps[0], self.t.db_path)
        self.assertEqual(tool_names(self.t.db_path), ["Bash", "Vanished"],
                         "restore did not bring the rows back")
        # Restored through the data layer, so the queries still read it. Counted
        # through `q_tools`, which is what the panels use: the name set is the
        # union of what was used and what the inventory says is installed, so it
        # is the *counts* that prove the rows came back, not the rows' presence.
        conn = db.open_db(self.t.db_path, readonly=True)
        try:
            self.assertEqual(db.stored_version(conn), db.SCHEMA_VERSION)
            counts = {r["tool_name"]: r["calls"] for r in db.q_tools(conn)}
        finally:
            conn.close()
        self.assertEqual(counts["Vanished"], 1, "restore did not bring the row back")
        self.assertEqual(counts["Bash"], 1)
        self.assertEqual(counts["Read"], 0,
                         "an installed tool that was never used stopped listing")

    def test_a_snapshot_is_an_online_backup_not_a_copy_of_the_main_file(self):
        """The index runs in WAL mode, so the file is not the whole database.

        A committed row can still be sitting in `usage.db-wal` when a destructive
        step asks for its safety copy. Copying the main file then hands back a
        database that predates the very rows the caller wanted protected — and
        restoring it silently loses them. `snapshot` goes through SQLite's backup
        API for exactly this reason.
        """
        conn = self.t.conn()
        conn.execute("INSERT INTO tool_calls(call_id, tool_name, ts)"
                     " VALUES('wal','HeldInWal',?)", (TS,))
        conn.commit()                          # the writer stays open

        wal = Path(str(self.t.db_path) + "-wal")
        self.assertTrue(wal.exists() and wal.stat().st_size > 0,
                        "no WAL in flight: this case cannot tell a backup from a copy")

        raw = self.t.root / "byte-copy.db"
        shutil.copy(self.t.db_path, raw)
        copied = tool_names(raw)
        if copied is not None and "HeldInWal" in copied:
            self.skipTest("the WAL had already been checkpointed into the main "
                          "file, so this run cannot distinguish a file copy from "
                          "an online backup")
        self.assertEqual(copied, ["Bash"],
                          "the control has gone: a byte copy of the main file "
                          "should not carry a row that is only in the WAL")

        snap = db.snapshot(conn, "wal-check", self.t.db_path)
        conn.close()
        self.assertEqual(tool_names(snap), ["Bash", "HeldInWal"],
                         "the snapshot did not capture what the live connection "
                         "had already committed")
        # And the snapshot is a database in its own right, not a partial file:
        # it carries the schema the queries need.
        conn = db.open_db(snap, readonly=True)
        try:
            self.assertEqual(db.stored_version(conn), db.SCHEMA_VERSION)
        finally:
            conn.close()


class EmptyIndexTest(unittest.TestCase):
    """A rebuild of an index that holds nothing has nothing to protect."""

    def test_a_full_rebuild_of_an_untouched_index_writes_no_snapshot(self):
        """`sync --full` on a fresh database is the ordinary first run.

        Snapshotting it anyway fills `backups/` with copies of an empty schema,
        one per first launch, and none of them can be restored to anything. The
        database is still rebuilt — the promise is only that a way back is taken
        when there is a way to go back to.
        """
        t = TempTree(self)
        t.sync(full=True)
        self.assertEqual(managed(t.db_path), [],
                         "an empty index was snapshotted before being cleared")
        conn = db.open_db(t.db_path, readonly=True)
        try:
            self.assertEqual(db.stored_version(conn), db.SCHEMA_VERSION)
        finally:
            conn.close()


class RestoreSafetyTest(unittest.TestCase):
    """A restore is itself a destructive step."""

    def setUp(self):
        self.t = TempTree(self)
        conn = self.t.conn()
        conn.executemany("INSERT INTO tool_calls(call_id, tool_name, ts)"
                         " VALUES(?,?,?)",
                         [("c1", "First", TS), ("c2", "Second", TS + 1)])
        conn.commit()
        self.keeper = db.snapshot(conn, "keeper", self.t.db_path)
        conn.execute("DELETE FROM tool_calls WHERE tool_name = 'First'")
        conn.commit()
        conn.close()
        self.assertEqual(tool_names(self.t.db_path), ["Second"])

    def test_a_restore_keeps_the_index_it_replaced_so_the_step_is_undoable(self):
        db.restore(self.keeper, self.t.db_path)
        self.assertEqual(tool_names(self.t.db_path), ["First", "Second"])

        made = [p for p in db.list_backups(self.t.db_path) if "pre-restore" in p.name]
        self.assertEqual(len(made), 1,
                         f"the index the restore overwrote was not kept: "
                         f"{managed(self.t.db_path)}")
        self.assertEqual(tool_names(made[0]), ["Second"],
                         "the pre-restore copy is not the state that was replaced")

        # And it is reachable: restoring *it* puts the overwritten state back.
        # This second restore deliberately keeps the default `pre-restore` reason
        # and runs in the same second as the snapshot above, so it is the case
        # the collision suffix exists for: without it, the second restore writes
        # its own safety copy over the snapshot it is about to read, and silently
        # restores the state it was supposed to be undoing.
        db.restore(made[0], self.t.db_path)
        self.assertEqual(tool_names(self.t.db_path), ["Second"],
                         "a restore is not undoable, so picking the wrong "
                         "snapshot is the one unrecoverable step")

    def test_a_restore_backs_up_the_live_file_where_a_restore_is_looked_for(self):
        """The safety copy belongs beside the database being replaced.

        Not beside the snapshot being restored *from*: a moved or foreign file is
        exactly the case the path form of `restore` exists for, and a copy that
        lands next to it is in a directory nothing lists.
        """
        elsewhere = self.t.root / "moved" / "keeper-copy.db"
        elsewhere.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(self.keeper, elsewhere)

        db.restore(elsewhere, self.t.db_path)
        self.assertEqual(tool_names(self.t.db_path), ["First", "Second"])
        self.assertTrue(any("pre-restore" in p.name for p in db.list_backups(
            self.t.db_path)),
                        f"no safety copy under {db.backup_dir(self.t.db_path)}: "
                        f"{managed(self.t.db_path)}")
        # Nothing of the restore's own making beside the source: SQLite may add
        # its `-wal`/`-shm` to any file it opens, but the safety *snapshot* has to
        # land in the directory `list_backups` reads.
        strays = [p.name for p in elsewhere.parent.iterdir()
                  if p.name.startswith("usage-")]
        self.assertEqual(strays, [],
                         f"the restore left its safety copy beside the source: {strays}")

    def test_two_snapshots_that_ask_for_the_same_name_are_both_kept(self):
        """A snapshot name has one second of resolution, and that is not a rate.

        Two calls can therefore ask for the same file. The pair that matters is
        not two `qcut backup`s in a script — it is the undo chain above: a restore
        writes a `pre-restore` copy, and a second restore in the same second would
        otherwise write *its* copy over the snapshot it is about to read, which
        undoes the undo instead of the original step.
        """
        conn = self.t.conn()
        first = db.snapshot(conn, "pre-restore", self.t.db_path)
        conn.execute("INSERT INTO tool_calls(call_id, tool_name, ts)"
                     " VALUES('c3','Third',?)", (TS,))
        conn.commit()
        second = db.snapshot(conn, "pre-restore", self.t.db_path)
        conn.close()

        self.assertNotEqual(first.name, second.name,
                            f"both snapshots claim {first.name}: "
                            f"{managed(self.t.db_path)}")
        self.assertTrue(first.is_file() and second.is_file(),
                        f"a snapshot vanished: {managed(self.t.db_path)}")
        # Each holds the state of its own moment, read out of the file rather than
        # inferred from a count: an overwritten first file is still there, with the
        # second one's rows in it. (`First` left the live index in setUp, which is
        # what makes the two moments differ at all.)
        self.assertEqual(tool_names(first), ["Second"])
        self.assertEqual(tool_names(second), ["Second", "Third"])

    def test_a_missing_snapshot_is_refused_before_the_live_index_is_touched(self):
        with self.assertRaises(FileNotFoundError):
            db.restore(self.t.root / "no-such-snapshot.db", self.t.db_path)
        # Nothing was written, so nothing was copied aside either.
        self.assertEqual(managed(self.t.db_path), [self.keeper.name])
        self.assertEqual(tool_names(self.t.db_path), ["Second"])


class RetentionTest(unittest.TestCase):
    """Pruning may only ever delete snapshots this module made."""

    def setUp(self):
        self.t = TempTree(self)
        conn = self.t.conn()
        conn.execute("INSERT INTO tool_calls(call_id, tool_name, ts)"
                     " VALUES('c1','Bash',?)", (TS,))
        conn.commit()
        conn.close()

    def aged(self, index):
        """A managed snapshot with a plausible name, `index` days old.

        The name carries the timestamp and the mtime is set to match it, so this
        fixture cannot be what lets a wrong ordering through: pruning the newest
        instead of the oldest fails whether the code reads names or mtimes.
        """
        moment = 1_700_000_000 + index * 86_400
        path = make_snapshot_file(
            db.backup_dir(self.t.db_path) / f"usage-202301{index + 1:02d}-120000-manual.db")
        os.utime(path, (moment, moment))
        return path

    def test_pruning_keeps_the_newest_five_and_returns_what_it_deleted(self):
        made = [self.aged(i) for i in range(7)]
        gone = db.prune_backups(self.t.db_path)          # the default keep
        self.assertEqual([p.name for p in gone], [made[0].name, made[1].name],
                         f"pruning must remove exactly the two oldest, removed {gone}")
        for path in gone:
            self.assertFalse(path.exists(), f"{path.name} was reported gone but left")
        left = sorted(p.name for p in db.backup_dir(self.t.db_path).iterdir())
        self.assertEqual(left, sorted(p.name for p in made[2:]),
                         f"kept {left}, expected the newest {db.BACKUP_KEEP}")

    def test_pruning_touches_nothing_outside_the_managed_snapshot_set(self):
        made = [self.aged(i) for i in range(3)]
        hand = self.t.db_path.parent / "usage.db.bak-20261007-093557"
        make_snapshot_file(hand)
        # Two look-alikes: one in the managed directory with the wrong shape, one
        # with the right shape in the wrong place.
        impostor = db.backup_dir(self.t.db_path) / "not-a-snapshot.db"
        make_snapshot_file(impostor)
        near_miss = db.backup_dir(self.t.db_path) / "usage-20230201-120000-manual.txt"
        near_miss.write_text("notes", encoding="utf-8")

        gone = db.prune_backups(self.t.db_path, keep=1)
        self.assertEqual([p.name for p in gone], [made[0].name, made[1].name],
                         f"pruning deleted or missed something: {gone}")
        for untouched in (hand, impostor, near_miss):
            self.assertTrue(untouched.exists(), f"pruning deleted {untouched.name}")
        self.assertTrue(hand.exists(),
                        "a hand-made backup is the only copy someone else kept")

    def test_pruning_with_keep_zero_clears_the_whole_managed_set(self):
        """``keep=0`` means "drop them all", not "keep nothing but one".

        The obvious slice form, ``list_backups()[:-keep]``, is ``[:0]`` for
        ``keep == 0`` and silently prunes nothing — which is how a backups
        directory grows forever under a tool asked to clear it.
        """
        made = [self.aged(i) for i in range(3)]
        gone = db.prune_backups(self.t.db_path, keep=0)
        self.assertEqual(sorted(p.name for p in gone), sorted(p.name for p in made))
        self.assertEqual(managed(self.t.db_path), [],
                         "keep=0 left snapshots behind")
        self.assertEqual(list(db.backup_dir(self.t.db_path).iterdir()), [],
                         "the managed files were reported deleted but are on disk")

    def test_retention_bounds_a_snapshot_the_backup_cli_never_took(self):
        """"Keep five" is a property of the directory, not of one subcommand.

        `sync --full` and `restore` both take snapshots and neither goes through
        `qcut backup`, so a prune that lived in the CLI left those two paths
        writing forever: the promise was about retention, the check was about a
        command.
        """
        aged = [self.aged(i) for i in range(db.BACKUP_KEEP)]
        conn = self.t.conn()
        dest = db.snapshot(conn, "before-full-sync", self.t.db_path)
        conn.close()

        self.assertEqual(managed(self.t.db_path),
                         sorted([p.name for p in aged[1:]] + [dest.name]),
                         f"retention did not run for a data-layer snapshot: "
                         f"{managed(self.t.db_path)}")
        self.assertFalse(aged[0].exists(), "the oldest file was kept")

    def test_a_snapshot_is_kept_beside_the_database_it_was_taken_from(self):
        """Each database owns its own ``backups/`` directory.

        ``--db`` and ``QCUT_DB`` can point the tool anywhere, and the snapshot
        must land where the same flag will find it again — never where the
        default path happens to point.
        """
        nested = self.t.root / "nested" / "other.db"
        conn = db.open_db(nested)
        db.ensure_schema(conn)
        conn.execute("INSERT INTO tool_calls(call_id, tool_name, ts)"
                     " VALUES('n1','Nested',?)", (TS,))
        conn.commit()
        snap = db.snapshot(conn, "manual", nested)
        conn.close()

        self.assertEqual(snap.parent, nested.parent / "backups")
        self.assertTrue(snap.is_file())
        self.assertEqual(tool_names(snap), ["Nested"])
        # The default database's own managed set is untouched, and it is empty:
        # the live index is never offered as a snapshot of itself.
        self.assertEqual(managed(self.t.db_path), [])
        self.assertEqual([p.name for p in db.list_backups(nested)], [snap.name])


if __name__ == "__main__":
    unittest.main()
