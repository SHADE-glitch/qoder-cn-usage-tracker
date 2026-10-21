"""Shared helpers for the qcut test suites.

Every suite builds its own throwaway database and its own throwaway Qoder tree in
a temp directory. Nothing here opens the real ``usage.db`` or ``~/.qoder-cn``:
a test that reads live state is an instrument error, not a finding.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
import tempfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import qcut_db as db  # noqa: E402


def load_sync():
    return db.load_sync()


def write_records(path: Path, recs) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        for rec in recs:
            fh.write(json.dumps(rec) + "\n")


def assistant(sid, mid, model="qfmodel", usage=None, content=None,
              ts="2026-10-01T10:00:00.000Z", cwd="/work/demo", **kw):
    rec = {
        "type": "assistant",
        "uuid": f"uuid-{mid}",
        "timestamp": ts,
        "sessionId": sid,
        "cwd": cwd,
        "version": "1.1.57",
        "entrypoint": "cli",
        "gitBranch": "main",
        "isSidechain": False,
        "message": {
            "id": mid,
            "type": "message",
            "role": "assistant",
            "model": model,
            "content": content if content is not None else [],
            "usage": usage,
            "stop_reason": "tool_use",
        },
    }
    rec.update(kw)
    return rec


def tool_use(call_id, name, **args):
    return {"type": "tool_use", "id": call_id, "name": name, "input": args}


def compact(sid, ts="2026-10-01T10:05:00.000Z", trigger="auto",
            pre_tokens=1200, post_tokens=300, messages=None, duration_ms=9000,
            cwd="/work/demo"):
    """A `system` / `compact_boundary` record, metadata only.

    Mirrors the shape the CLI writes; the summarized messages themselves are
    absent on purpose — the parser must never need them.
    """
    return {
        "type": "system",
        "subtype": "compact_boundary",
        "timestamp": ts,
        "sessionId": sid,
        "cwd": cwd,
        "isSidechain": False,
        "entrypoint": "cli",
        "compactMetadata": {
            "trigger": trigger,
            "preTokens": pre_tokens,
            "postTokens": post_tokens,
            "messagesSummarized": messages if messages is not None else 12,
            "durationMs": duration_ms,
        },
    }


def now_iso(offset_seconds=0):
    """A transcript timestamp relative to now, so a windowed query sees it."""
    from datetime import datetime, timedelta, timezone
    moment = datetime.now(timezone.utc) + timedelta(seconds=offset_seconds)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def tool_result(rec_id, call_id, is_error=False, kind=None,
                ts="2026-10-01T10:00:05.000Z", **meta):
    rec = {
        "type": "user",
        "uuid": f"uuid-{rec_id}",
        "timestamp": ts,
        "sessionId": "s1",
        "cwd": "/work/demo",
        "isSidechain": False,
        "message": {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": call_id,
                         "content": "ok", "is_error": is_error}],
        },
    }
    if kind is not None or meta:
        rec["toolUseResult"] = dict({"kind": kind}, **meta)
    return rec


def usage(input_tokens=None, output_tokens=None, cache_read=None, cache_creation=None,
          credits=None, original_credits=None, billable=None, request_id="req-1",
          context_usage_ratio=None):
    out = {}
    for key, value in (
        ("input_tokens", input_tokens), ("output_tokens", output_tokens),
        ("cache_read_input_tokens", cache_read),
        ("cache_creation_input_tokens", cache_creation),
        ("credits", credits), ("original_credits", original_credits),
        ("billable", billable), ("request_id", request_id),
        ("context_usage_ratio", context_usage_ratio),
    ):
        if value is not None:
            out[key] = value
    return out


#: Every environment name that outranks the settings file, derived rather than
#: typed: a hand-copied list is how the next knob gets forgotten in the isolation
#: helper too. `QCUT_CONFIG` is not in `ENV_NAMES` (it names the file itself) and
#: the two launcher paths outrank the temp tree, so a case that asserts the file or
#: the defaults must hide all of them.
SETTINGS_ENV = tuple(db.ENV_NAMES.values()) + ("QCUT_CONFIG", "QCUT_DB",
                                               "QCUT_QODER_DIR")


@contextlib.contextmanager
def no_settings_env():
    """Run with no qcut settings in the environment, whatever the shell has."""
    saved = {name: os.environ.pop(name) for name in SETTINGS_ENV
             if name in os.environ}
    try:
        yield
    finally:
        os.environ.update(saved)


class TempTree:
    """A throwaway ``~/.qoder-cn`` plus its database, wired into ``db``."""

    def __init__(self, testcase):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.qoder = self.root / "qoder-cn"
        self.qoder.mkdir(parents=True, exist_ok=True)
        self.db_path = self.root / "usage.db"
        self._saved = (db.QODER_DIR, db.DB_PATH, db.CONFIG_PATH)
        db.QODER_DIR = self.qoder
        db.DB_PATH = self.db_path
        db.CONFIG_PATH = self.root / "config.toml"
        testcase.addCleanup(self.close)

    def close(self):
        db.QODER_DIR, db.DB_PATH, db.CONFIG_PATH = self._saved
        self._tmp.cleanup()

    # --- fixture builders ---

    def transcript(self, rel="projects/-work-demo/s1.jsonl"):
        return self.qoder / rel

    def write(self, rel, recs):
        path = self.qoder / rel
        write_records(path, recs)
        return path

    def json_file(self, rel, payload):
        path = self.qoder / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def skill(self, name, body="# skill\n"):
        path = self.qoder / "skills" / name / "SKILL.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        return path

    def sync(self, **kw):
        return load_sync().run(db_path=self.db_path, quiet=True, **kw)

    def conn(self):
        conn = db.open_db(self.db_path)
        db.ensure_schema(conn)
        return conn

    def rows(self, sql, params=()):
        conn = self.conn()
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()

    def one(self, sql, params=()):
        rows = self.rows(sql, params)
        return rows[0] if rows else None
