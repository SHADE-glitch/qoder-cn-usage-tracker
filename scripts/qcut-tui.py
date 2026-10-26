#!/usr/bin/env python3
"""qcut-tui — the eight-tab Textual UI for the Qoder CN CLI usage tracker.

    python3 scripts/qcut-tui.py

The app is a *reader* of the index built by ``qcut sync`` — except that it also
writes: ``on_mount`` runs ``db.migrate()`` and the sync worker it starts on
launch (or on ``r``) runs the same indexer the CLI does. Anything that assumes
the TUI is read-only is wrong.

Tabs: Dashboard · Tools · Skills · Agents · Plugins · MCP · Tokens · Usage.
Each tab keeps its own window; the Plugins tab has none (an install fact is
always all-time). The window comes from the shared top bar: a range ``Select``
with a ``Custom…`` entry, beside a 📅 control that opens the date-range picker.
Every number is an integer, a formatted value, ``-`` (no measurement contributed
to this window) or ``—`` (the call reported credits instead of a token) — never a
guess.

Two deliberate absences, both because Qoder's transcripts carry no such field:
cc-switch's per-request *duration* and *HTTP status* columns. The cost column is
present, because Qoder reports real credits.

Requires ``textual`` (the only third-party dependency; see requirements.txt).
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import time
import unicodedata
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import qcut_db as db  # noqa: E402

try:
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.containers import Grid, Horizontal, Vertical
    from textual.screen import ModalScreen, Screen
    from textual.widgets import (
        Button,
        Checkbox,
        DataTable,
        Footer,
        Input,
        Select,
        Static,
        TabbedContent,
        TabPane,
    )

    class Table(DataTable):
        """A DataTable whose enter key is in the footer.

        Textual binds `enter` on DataTable with `show=False`, so the one key
        that opens a row's detail was the one key the footer hid — and an
        app-level `enter` binding cannot fix it, because the focused table's
        own binding wins. Same action, visible name. Only the tables whose rows
        actually open something use it; a table with no detail stays plain, so
        the footer never advertises a dead key.
        """

        BINDINGS = [Binding("enter", "select_cursor", "Open")]
except ImportError:  # pragma: no cover - the CLI prints this, not a traceback
    print("qcut: the TUI needs the 'textual' package.\n"
          "  ./install.sh        (creates .venv with textual)\n"
          "  pip install -r requirements.txt", file=sys.stderr)
    raise SystemExit(2)


# --- formatting ------------------------------------------------------------


def ts(v):
    if not v:
        return "-"
    return datetime.fromtimestamp(v / 1000).strftime("%m-%d %H:%M")


def ms(v):
    if v is None:
        return "-"
    if v < 1000:
        return f"{v:.0f}ms"
    if v < 60000:
        return f"{v / 1000:.2f}s"
    return f"{v / 60000:.1f}m"


def fmt_n(v):
    """Render a count, showing ``-`` for NULL (never a fabricated 0)."""
    if v is None:
        return "-"
    return f"{v:,}"


def fmt_tok(value, reported):
    """Render one token measure, given whether *any* row reported a token.

    Three states, and they are not the same statement:

    ``-``   nothing in this window contributed (a NULL sum, or a NULL field)
    ``—``   there *is* a number, and no row behind it carried a token — a
            legacy hosted call (written without the token flag) answers every
            token field with a literal ``0``, so a sum of those zeros is not a
            total. Naming it ``0`` would claim "this model used no tokens",
            which is a different fact.
    ``1,234`` a measured value, possibly a partial total when some rows in the
            same aggregate reported no token.

    Credits and tokens can coexist: a hosted call made with
    ``QODERCN_EXPOSE_TOKEN_USAGE=1`` carries real tokens *and* credits, so a
    non-zero token cell says nothing about credits and vice versa.

    The guard reads the value too, not only the flag: a non-zero is shown
    whatever `reported` claims. The flag is computed elsewhere from the same
    fields it then hides, so a bug there must not be able to swallow a
    measurement — `test_usage.py` locks the two definitions together.
    """
    if value is None:
        return "-"
    if not reported and not value:
        return "—"
    return fmt_n(value)


def fmt_compact_n(v):
    """A count shortened for a panel line (``68.0M``), or ``-`` for NULL."""
    if v is None:
        return "-"
    n = float(v)
    for size, suffix in ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "K")):
        if abs(n) >= size:
            return f"{n / size:.1f}{suffix}"
    return f"{int(n):,}"


def count_n(v, singular, plural=None):
    """``1 tool call`` / ``12 tool calls``, grouped like every count cell.

    Counts that read as prose are one class, so they agree here rather than
    being re-remembered per site — "1 skills" on the status bar and "Synced 1
    files" were the visible ones.
    """
    word = singular if v == 1 else (plural or singular + "s")
    return f"{v:,} {word}"


def fmt_credits(v):
    if v is None:
        return "-"
    if not v:
        return "0"
    if v >= 1000:
        return f"{v:,.0f}"
    return f"{v:,.2f}"


def fmt_credit_total(value, carried):
    """An aggregate of credits: ``-`` when no row inside it carried the field.

    The mirror of ``fmt_tok``. A BYOK route has no ``credits`` field at all, so
    ``SUM(COALESCE(credits,0))`` over such a route is `0`, and `0` reads "billed
    nothing" — a claim about money that was never made in that unit. Per-row
    cells need no such help: an absent field is already NULL, and `fmt_credits`
    renders it `-`. Same guard as ``fmt_tok``: a non-zero is always shown.
    """
    if value is None or (not carried and not value):
        return "-"
    return fmt_credits(value)


def pct(v):
    if v is None:
        return "-"
    return f"{v * 100:.1f}%"


def clip(text, width):
    """Fit ``text`` into ``width`` cells, marking a cut with an ellipsis.

    The leaderboard lines are column-formatted (``{:<26}``), so a name longer
    than its field does not wrap — it pushes every column after it. Clipping
    keeps the columns aligned; the full name stays in the entity's own table.
    """
    s = str(text)
    return s if len(s) <= width else s[: width - 1] + "…"


def local_time(ms_value):
    """Local-time render of an epoch-ms value (query bounds stay in Unix ms)."""
    if not ms_value:
        return "-"
    return datetime.fromtimestamp(ms_value / 1000).strftime("%Y-%m-%d %H:%M:%S")


def display_width(text) -> int:
    width = 0
    for ch in str(text):
        width += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return width


# --- the shared window control ---------------------------------------------
#
# Windows are whole local calendar days ending now ("Today", "2 days", …).
# "Custom…" is the escape hatch to the date picker; it is never stored as a
# tab's range (a picked range is a db.CustomWindow instead).
RANGE_OPTIONS = [(db.USAGE_RANGE_LABELS[k], k) for k in db.RANGE_ORDER] + [
    ("Custom…", "custom")]
CUSTOM_RANGE = "custom"


# --- tabs ------------------------------------------------------------------

TABS = (
    ("dashboard", "Dashboard"),
    ("tools", "Tools"),
    ("skills", "Skills"),
    ("agents", "Agents"),
    ("plugins", "Plugins"),
    ("mcp", "MCP"),
    ("tokens", "Tokens"),
    ("usage", "Usage"),
)
# Every tab is one of two shapes: a *summary page* (a stack of bordered panels)
# or a *note line plus one table*. Declared as data because three things read
# it — the layout below, the table column registry, and the test that walks this
# source to check every table it builds is planned. A tab in neither group would
# be an unplanned pane, so the partition itself is asserted rather than assumed.
SUMMARY_TABS = ("dashboard", "usage")
TABLE_TABS = ("tools", "skills", "agents", "plugins", "mcp", "tokens")

# Each tab keeps its own window. Plugins has none: an install fact is all-time.
DEFAULT_RANGES = {
    "dashboard": "1d", "tools": "7d", "skills": "7d", "agents": "7d",
    "plugins": None, "mcp": "7d", "tokens": "1d", "usage": "1d",
}

# Per-tab sort orders: (label, row key, descending). Mode 0 is the query's own
# order; `s` then cycles these and wraps back to it. Named per tab because the
# same idea is a different column (`calls` / `runs` / `uses` / `requests`).
SORT_FIELDS = {
    "tools": (("calls", "calls", True), ("recent", "last_used", True),
              ("name", "tool_name", False)),
    "skills": (("calls", "calls", True), ("recent", "last_used", True),
               ("name", "skill", False)),
    "agents": (("runs", "runs", True), ("requests", "requests", True),
               ("tokens", "usage_total_tokens", True),
               ("credits", "credits", True),
               ("recent", "last_used", True), ("name", "agent_type", False)),
    "plugins": (("uses", "uses", True), ("recent", "last_used", True),
                ("name", "plugin", False)),
    "mcp": (("calls", "calls", True), ("recent", "last_used", True),
            ("server", "server", False)),
    "tokens": (("tokens", "usage_total_tokens", True),
               ("requests", "requests", True), ("credits", "credits", True),
               ("recent", "last_used", True), ("name", "model", False)),
}
# Usage is ordered in SQL — the list is capped at `log_limit` before Python
# sees it, so a client-side sort could only rank the page. Its first entry is
# therefore mode 0 rather than a separate "tab order".
USAGE_SORT_FIELDS = (("time", "ts", True),
                     ("tokens", "usage_total_tokens", True),
                     ("credits", "credits", True), ("model", "model", False))
# The one tab shape without a filterable table: its panels are aggregates.
FILTERABLE_TABS = ("tools", "skills", "agents", "plugins", "mcp", "tokens",
                   "usage")
# `a` widens the installed ∪ ever-used tabs; the others have no unused half.
SHOW_ALL_TABS = ("tools", "skills", "agents", "mcp")

# Hosted Qoder calls report credits either way; the token numbers only exist
# when the CLI process itself started with this flag. The note says it in the
# two places a reader looks for tokens instead of leaving `—` unexplained.
TOKEN_CAPTURE_HINT = ("token capture off — set QODERCN_EXPOSE_TOKEN_USAGE=1 "
                      "before starting Qoder")


# Column sets. The Usage page is cc-switch's request list with Qoder's fields:
# time · model · provider · usage total · input · output · API total · cache
# hit/miss/write · credits · hit rate. Cache *miss* is derived (the input that
# was not read from cache) and the page note says so; there is no duration or
# HTTP-status column because the transcripts carry neither. `Provider` is one
# column, not a family plus a name: built-in models read `Built-in`, and a BYOK
# call reads the channel that served it.
# Usage Total leads the measure family: it is the rank the Dashboard and the
# Tokens tab use (then credits), so the request log presents the same hierarchy.
USAGE_COLUMNS = ("Time", "Model", "Provider", "Usage Total", "Credits",
                 "Input", "Output", "Cache hit", "Cache miss",
                 "Cache write", "Cache hit rate")
TOKENS_COLUMNS = ("Model", "Provider", "Usage Total", "Credits", "Requests",
                  "Input", "Output", "Cache hit", "Cache miss",
                  "Cache write", "Cache hit rate", "Last used")

TOOLS_COLUMNS = ("tool", "calls", "ok", "fail", "pending", "avg", "last used")
SKILLS_COLUMNS = ("skill", "calls", "ok", "fail", "plugin", "last used")
# A run is one `Agent` tool call. `requests`/tokens/credits are the responses
# that run's own `parent_tool_use_id` links to it; a run whose sidechain file was
# never indexed keeps 0 requests and no token sum rather than a guess.
AGENTS_COLUMNS = ("agent", "runs", "ok", "fail", "requests",
                  "usage total", "credits", "peak ctx", "last used")
PLUGINS_COLUMNS = ("plugin", "source", "version", "uses", "skills", "agents",
                   "cmds", "last used")
MCP_COLUMNS = ("server", "tool", "calls", "ok", "fail", "avg", "last used")
# The Dashboard board is a cost summary, not a session log: sessions and
# compactions live one Enter away on the project screen, and dropping them here
# keeps the longest paths from pushing `last used` under the scrollbar.
PROJECT_COLUMNS = ("project", "requests", "usage total",
                   "credits", "peak ctx", "last used")
PROJECT_SESSION_COLUMNS = ("session", "last used", "requests", "usage total",
                           "credits", "peak ctx", "compactions", "compacted",
                           "model")
SESSION_CALL_COLUMNS = ("when", "model", "provider", "agent", "credits",
                        "usage total", "input", "output",
                        "cache hit", "cache miss", "cache write", "context",
                        "sidechain")

# A drill-down mirrors the table it came from. `model` keeps the whole measure
# family so the history screen agrees with the Tokens tab it was opened from.
HISTORY_COLUMNS = {
    "tool": ("when", "tool", "category", "status", "duration", "project",
             "session"),
    "skill": ("when", "skill", "status", "duration", "project", "session"),
    "agent": ("when", "agent", "status", "duration", "requests",
              "usage total", "input", "output", "cache hit",
              "cache write", "credits", "peak ctx", "project", "session"),
    "model": ("when", "model", "provider", "usage total", "credits", "input",
              "output", "cache hit", "cache miss", "cache write",
              "stop"),
    "mcp": ("when", "server", "tool", "status", "duration", "session"),
}
# Twelve columns need ~180 cells: on an 80-wide terminal most of this table used
# to sit past the right edge with no key that would bring it back.
RESPONSE_COLUMNS = ("when", "model", "provider", "usage total", "credits",
                    "input", "output", "cache hit", "cache miss",
                    "cache write", "hit rate", "message id")
# A plugin's own breakdown: attributed calls and installed resources. `state`
# keeps the evidence apart instead of spelling "(installed)" into `target`.
PLUGIN_COLUMNS = ("when", "kind", "target", "state", "marketplace", "session",
                  "project")

# The single-call detail screen renders every column of the row. Labels live
# here rather than inline so the DB column name and what the screen calls it
# stay one mapping; a column with no entry still shows, under its own name.
CALL_DETAIL_LABELS = {
    "call_id": "call id", "id": "id", "tool_name": "tool", "tool": "tool",
    "agent_type": "agent", "skill": "skill", "plugin": "plugin",
    "category": "category", "model": "model", "provider": "provider",
    "provider_id": "provider id", "request_id": "request id",
    "status": "status", "kind": "kind", "source": "source",
    "marketplace": "marketplace", "target": "target", "server": "server",
    "admin_op": "admin op", "ts": "when", "duration_ms": "duration",
    "session_id": "session", "project": "project", "cwd": "cwd",
    "git_branch": "git branch", "version": "version", "entrypoint": "entrypoint",
    "started_at": "started", "ended_at": "ended",
    "message_id": "message id", "stop_reason": "stop reason",
    "input_tokens": "input tokens", "output_tokens": "output tokens",
    "cache_read_tokens": "cache read", "cache_creation_tokens": "cache write",
    # The stored per-row total is kept on this screen (it is what the host wrote),
    # but it is named for where it came from — the panels' one token total is
    # `usage total`, and a second column called "api total" made two numbers look
    # like rivals for the same job.
    "total_tokens": "reported total", "usage_total_tokens": "usage total",
    "cache_miss_tokens": "cache miss",
    "credits": "credits", "original_credits": "list price",
    "billable": "billable", "is_sidechain": "sidechain",
    "has_usage": "has usage", "context_usage_ratio": "context ratio",
}
_DETAIL_TIME_COLS = {"ts", "started_at", "ended_at"}
_DETAIL_DURATION_COLS = {"duration_ms"}
_DETAIL_BOOL_COLS = {"billable", "is_sidechain", "has_usage"}
_DETAIL_CREDIT_COLS = {"credits", "original_credits"}
_DETAIL_PCT_COLS = {"context_usage_ratio"}
_DETAIL_TOKEN_COLS = frozenset(db.TOKEN_COLUMNS)

# Drill-down headers used to print every key of the summary row, including
# `credits_carried` and `token_covered` — internal flags, not readings. These
# lists choose the fields a person came to the screen for; everything else
# stays available in the single-call view.
SUMMARY_LABELS = {
    "calls": "calls", "runs": "runs", "completed": "completed", "failed": "failed",
    "avg_ms": "avg", "requests": "requests", "sessions": "sessions",
    "projects": "projects", "input_tokens": "input", "output_tokens": "output",
    "cache_read_tokens": "cache read", "cache_creation_tokens": "cache write",
    "usage_total_tokens": "usage total",
    "cache_miss_tokens": "cache miss", "credits": "credits",
    "original_credits": "list price", "peak_context": "peak ctx",
    "compactions": "compactions", "compacted_tokens": "context dropped",
    "first_used": "first used", "last_used": "last used",
}
SUMMARY_KEYS = {
    "tool": ("calls", "completed", "failed", "avg_ms", "sessions", "projects",
             "first_used", "last_used"),
    "skill": ("calls", "completed", "failed", "avg_ms", "sessions", "projects",
              "first_used", "last_used"),
    "mcp": ("calls", "completed", "failed", "avg_ms", "sessions", "projects",
            "first_used", "last_used"),
    "agent": ("runs", "completed", "failed", "requests", "usage_total_tokens",
              "credits", "peak_context", "sessions", "projects", "first_used",
              "last_used"),
    "model": ("calls", "sessions", "usage_total_tokens",
              "credits", "cache_read_tokens", "cache_creation_tokens",
              "peak_context", "first_used", "last_used"),
    "project": ("requests", "sessions", "usage_total_tokens", "credits",
                "peak_context", "compactions", "compacted_tokens",
                "first_used", "last_used"),
}


def _summary_value(key, value, summary):
    if value is None:
        return "-"
    if key in ("first_used", "last_used"):
        return ts(value)
    if key == "credits":
        if "credits_carried" in summary.keys():
            return fmt_credit_total(value, summary["credits_carried"])
        return fmt_credits(value)
    if key == "original_credits":
        carried = summary["credits_carried"] if "credits_carried" in summary.keys() else 0
        return fmt_credit_total(value, carried)
    if key == "avg_ms":
        return ms(value)
    if key == "peak_context":
        return pct(value)
    if key in db.TOKEN_COLUMNS:
        reported = (bool(summary["token_covered"])
                    if "token_covered" in summary.keys() else True)
        return fmt_tok(value, reported)
    if isinstance(value, int):
        return fmt_n(value)
    return value


def summary_lines(summary, kind):
    """Two readable lines of the summary fields that matter for *kind*."""
    keys = SUMMARY_KEYS.get(kind)
    if keys is None:
        keys = tuple(k for k in summary.keys() if k not in (
            "token_covered", "credits_carried"))
    parts = [f"{SUMMARY_LABELS.get(key, key.replace('_', ' '))} = "
             f"{_summary_value(key, summary[key], summary)}"
             for key in keys if key in summary.keys()]
    if not parts:
        return []
    mid = (len(parts) + 1) // 2
    return ["  " + "   ".join(parts[:mid]), "  " + "   ".join(parts[mid:])]


def _call_detail_rows(row):
    """``(label, text)`` for every field of one call row, in row order.

    NULL renders as ``-`` (never a fabricated 0); a token field of a row that
    reports no token at all renders as ``—``; a timestamp, a duration, the
    credit columns and the yes/no flags are formatted. An unknown column falls
    back to its name, so a column added to a query still appears.
    """
    out = []
    # ``sqlite3.Row`` iterates as values, not (key, value) pairs: the column
    # names come from ``keys()`` and the two are zipped, so this renders every
    # stored field of the row in the row's own column order.
    reports_tokens = db.row_reports_tokens(row)
    for col in row.keys():
        value = row[col]
        label = CALL_DETAIL_LABELS.get(col, col.replace("_", " "))
        if value is None:
            text = "-"
        elif col in _DETAIL_TIME_COLS:
            text = f"{local_time(value)}  ({value})"
        elif col in _DETAIL_DURATION_COLS:
            text = f"{ms(value)}  ({value})"
        elif col in _DETAIL_BOOL_COLS:
            text = "yes" if value else "no"
        elif col in _DETAIL_CREDIT_COLS:
            text = f"{fmt_credits(value)}  ({value})"
        elif col in _DETAIL_PCT_COLS:
            text = pct(value)
        elif col == "provider":
            text = db.provider_label(value)
        elif col in _DETAIL_TOKEN_COLS:
            # The parenthesised raw value stays visible for a reported measure,
            # but a structural zero has no number to show: `—` alone.
            text = f"{fmt_n(value)}  ({value})" if reports_tokens else "—"
        else:
            text = str(value)
        out.append((label, text))
    return out


# A NULL project is a real group on the Dashboard — `(no project)` — and it must
# route to `project IS NULL`, not to a search for a project literally named "".
# A NUL byte cannot occur in a cwd.
NULL_PROJECT_KEY = "\x00"

EMPTY_MSG = {
    "tools": "no tool calls in this window",
    "skills": "no skill invocations in this window",
    "agents": "no agent runs in this window",
    "plugins": "no plugins installed or used",
    "mcp": "no MCP servers configured or used",
    "tokens": "no model responses in this window",
    "usage": "No requests in this window — widen the range above.",
    "projects": "no project requests in this window",
    "project_sessions": "no sessions in this project and window",
    "agent_requests": "this run has no indexed requests (its sidechain transcript"
                      " may be absent)",
    "session_calls": "no requests in this session and window",
}


class TopBar(Horizontal):
    """The title bar plus the shared window control (replaces textual's Header).

    Two built-in behaviours are dropped: clicking no longer toggles the 'tall'
    height, and there is no top-left icon, so the command palette never opens.
    Only the main screen carries the control; detail screens build without one.
    """

    DEFAULT_CSS = """
    TopBar { dock: top; width: 100%; height: 1; background: $panel;
             color: $foreground; }
    TopBar > #topbar-title { width: 1fr; padding: 0 1; content-align: left middle;
                             text-wrap: nowrap; text-overflow: ellipsis; }
    TopBar > #date-range { width: auto; min-width: 0; height: 1; border: none;
                           padding: 0 1; background: $panel; color: $accent;
                           text-style: bold; }
    TopBar > #date-range:hover { background: $boost; }
    TopBar > #range { width: 30; }
    TopBar > #range SelectCurrent { border: none; padding: 0 1;
                                    background: $panel; color: $accent; }
    """

    def __init__(self, title: str, *, with_range: bool = False,
                 range_value: str = "7d", id: str | None = None):
        super().__init__(id=id)
        self._title = title
        self._with_range = with_range
        self._range_value = range_value

    def compose(self) -> ComposeResult:
        yield Static(self._title, id="topbar-title")
        if self._with_range:
            # Two controls, two jobs: the Select picks a fixed preset, and the
            # button opens the calendar for a custom span. The button offers
            # "Custom…" while a preset is active and names the span once a
            # custom window is in force.
            yield Button("📅 Custom…", id="date-range")
            yield Select(RANGE_OPTIONS, value=self._range_value,
                         allow_blank=False, id="range")

    def set_range_label(self, text: str, custom: bool = False) -> None:
        """Name the custom span, or hide the control on a tab with no window."""
        try:
            btn = self.query_one("#date-range", Button)
        except Exception:
            return
        btn.label = f"📅 {text} ▾" if custom and text else "📅 Custom…"
        btn.tooltip = text or None
        btn.display = bool(text)
        # A Button does not re-lay-out when its label grows, so the control
        # kept the width it was built with until the next full relayout.
        btn.refresh(layout=True)


class TimeInput(Input):
    """A clock field you can scrub as well as type.

    Typing is the fast path (``00:00`` and ``9:5 PM`` are both understood);
    ↑/↓ and the mouse wheel step the value, so it can be nudged without the
    keyboard. The step is a per-instance count of minutes, set by the picker's
    step selector, so one key press can move a minute, ten, or an hour. The
    value is re-rendered through ``db.format_time``, so what stays in the field
    is always the canonical 24-hour ``HH:MM``.
    """

    BINDINGS = [
        Binding("up", "nudge_up", "Later", show=False),
        Binding("down", "nudge_down", "Earlier", show=False),
    ]
    STEP = 1  # minutes per nudge, until the picker's step selector says otherwise

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.step = self.STEP

    def action_nudge_up(self) -> None:
        self._nudge(self.step)

    def action_nudge_down(self) -> None:
        self._nudge(-self.step)

    def on_mouse_scroll_up(self, event) -> None:
        self._nudge(self.step)
        event.stop()

    def on_mouse_scroll_down(self, event) -> None:
        self._nudge(-self.step)
        event.stop()

    def _nudge(self, minutes: int) -> None:
        try:
            hour, minute = db.parse_time(self.value)
        except ValueError:
            # An empty or half-typed field nudges from *now*, so the first ↑/↓
            # yields a valid time instead of an error.
            now = datetime.now()
            hour, minute = now.hour, now.minute
        total = (hour * 60 + minute + minutes) % (24 * 60)
        self.value = db.format_time(total // 60, total % 60)
        self.cursor_position = len(self.value)


class DateRangeModal(ModalScreen):
    """The custom date-range picker, laid out like cbut's: chips, cards, grids.

    Left column: cbut's six shortcut chips, an editable START TIME and END TIME (a
    date plus a 24-hour time each), a quick-time row under each card, a
    nudge-step selector and the "end follows now" switch. Right column: a month
    calendar whose ‹ › step the month (and, at the year's ends, the year), with
    a 24-cell hour grid below it. Clicking a card makes it active; a day or an
    hour then writes into the active card — start and end are picked the same
    way, and the calendar and the grid are the two halves of one clock.

    Opened from the top bar's 📅 control or the Select's "Custom…". Confirm
    dismisses with a ``db.CustomWindow``; Cancel or Esc dismisses with ``None``.
    A blank start date means all time.
    """

    BINDINGS = [Binding("escape", "cancel", "Cancel")]
    CELLS = 42                       # 6 rows × 7 columns covers any month
    DOW = ("S", "M", "T", "W", "T", "F", "S")
    # (chip label, widget id, preset key) — cbut's six shortcuts, unchanged.
    # "Today" and "1d" both name the single whole calendar day the model calls
    # "1d". The Select also offers 2 days and 3 days, but cbut's picker does not
    # repeat every Select option as a chip: six height-3 chips take 52 of the
    # 53-cell column, and a seventh would clip "All time". Those two ranges stay
    # one click away in the Select and reachable here through the calendar.
    PRESETS = (
        ("Today", "dr-today", "1d"),
        ("1d", "dr-1d", "1d"),
        ("7d", "dr-7d", "7d"),
        ("14d", "dr-14d", "14d"),
        ("30d", "dr-30d", "30d"),
        ("All time", "dr-all", "all"),
    )
    # The quick-time row under each card. The start card offers the day's edges
    # and the common halves; the end card also offers ``now``, which is the same
    # thing as ticking "follows current time" — the two ends of one choice, one
    # click each.
    QUICK_START = ("00:00", "06:00", "12:00", "18:00", "23:59")
    QUICK_END = ("now", "00:00", "12:00", "23:59")
    # The nudge selector: (label, minutes). ↑/↓ and the wheel move by the
    # selected step, so a day's edge is reachable without typing.
    STEPS = (("1m", 1), ("10m", 10), ("1h", 60))
    HOURS = 24

    DEFAULT_CSS = """
    DateRangeModal { align: center middle; overflow: auto; }
    /* The roomy cbut layout: height-3 preset chips, a blank row between the
       cards, and the default bordered checkbox. Width budget, so the picker
       fits an 80-column terminal: 1 border + 1 padding + 53 (left column, of
       which cbut's six preset chips use 52) + 1 margin + 21 (7 day cells of 3,
       and 6 hour cells of 3) + 1 padding + 1 border = 79. The day and hour
       cells are Statics, not Buttons: a Button renders a two-digit label as one
       digit at width 3. On a shorter terminal the screen scrolls rather than
       crushing the layout; the tests assert the roomy geometry at 80x30. */
    #dr-box { width: auto; height: auto; border: round $primary;
              background: $surface; padding: 1 1; }
    #dr-left { width: 53; height: auto; }
    #dr-presets { height: auto; }
    .dr-card { border: round $panel; height: auto; padding: 0 1; margin-top: 1; }
    .dr-card.-active { border: round $accent; background: $boost; }
    .dr-card-title { color: $text-muted; text-style: bold; }
    .dr-fields { height: auto; }
    #dr-start-date, #dr-end-date { width: 1fr; margin: 0 1 0 0; }
    /* An Input spends 6 cells on its border and its 0-2 padding, so 16 is what
       a 5-cell "23:59" needs with room to type over it. */
    #dr-start-time, #dr-end-time { width: 16; }
    /* The quick-time rows: one cell tall, so five chips + four chips cost two
       rows rather than six. 5 × (5 + 2 padding + 1 margin) = 40 ≤ 57. */
    #dr-start-quick, #dr-end-quick { height: auto; }
    #dr-follow { height: auto; }
    #dr-nudge { height: auto; }
    /* A bare Static in a Horizontal takes the whole row and pushes the buttons
       past the column (and off the modal), so the label is pinned to its own
       cells. */
    #dr-nudge .dr-card-title { width: 7; }
    #dr-actions { height: auto; align-horizontal: right; }
    #dr-cal { width: 21; height: auto; margin-left: 1; }
    #dr-cal-head { height: auto; align: center middle; }
    #dr-cal-month { width: 1fr; text-align: center; text-style: bold; }
    #dr-cal-grid { grid-size: 7; grid-gutter: 0; height: auto; }
    /* 6 columns of 3 = 18 of the 21 available, so the 24 hours land in 4 rows
       under the month. */
    #dr-hour-grid { grid-size: 6; grid-gutter: 0; height: auto; margin-top: 1; }
    /* Every rule that touches a Button is scoped by an id: Button's own
       `Button:ansi.-style-default` selector outranks a bare class, so
       `.dr-day { border: none }` would silently lose and the calendar would
       render with tall borders and no room for the number. */
    /* cbut's preset chips: three rows tall, a cell of padding each side, and a
       cell of air between them. The six chips use 52 of the 53-cell column, so
       "All time" keeps its full label. */
    #dr-presets Button { width: auto; min-width: 0; height: 3; border: none;
                         padding: 0 1; margin: 0 1 0 0; background: $boost;
                         color: $foreground; }
    #dr-start-quick Button, #dr-end-quick Button {
                         width: auto; min-width: 0; height: 1; border: none;
                         padding: 0 1; margin: 0 1 0 0; background: $boost;
                         color: $foreground; }
    #dr-nudge Button { width: auto; min-width: 0; height: 1; border: none;
                       padding: 0 1; margin: 0 1 0 0; background: $boost;
                       color: $foreground; }
    /* -selected, not -active: Button uses -active for its own 0.2s pressed
       effect and strips it on a timer, so the chosen step flickered off. */
    #dr-nudge Button.-selected { background: $accent; color: $text;
                                 text-style: bold; }
    #dr-actions Button { width: auto; min-width: 0; margin-left: 1; }
    #dr-cal-head Button { width: auto; min-width: 0; border: none;
                          background: $surface; color: $foreground; }
    #dr-cal-grid .dr-dow { text-align: center; color: $text-muted; }
    /* 3, not 4: a Static renders "30" at width 3, where a Button shows " 3 ".
       The clicks are read from the cell id in on_click. */
    #dr-cal-grid .dr-day { width: 3; height: 1; text-align: center;
                           background: $surface; color: $foreground; }
    #dr-cal-grid .dr-day.-other { color: $text-disabled; }
    #dr-cal-grid .dr-day.-today { color: $accent; text-style: bold; }
    #dr-cal-grid .dr-day.-selected { background: $accent; color: $text;
                                     text-style: bold; }
    #dr-hour-grid .dr-hour { width: 3; height: 1; text-align: center;
                             background: $surface; color: $foreground; }
    #dr-hour-grid .dr-hour.-selected { background: $accent; color: $text;
                                       text-style: bold; }
    """

    def __init__(self, current, now_ms: int, id: str | None = None):
        super().__init__(id=id)
        self._current = current
        self._now = now_ms
        self._active = "start"       # which card the calendar writes into
        self._step = self.STEPS[0][1]   # minutes per ↑/↓ nudge
        self._view = datetime.fromtimestamp(now_ms / 1000).date().replace(day=1)
        # What we last wrote into each field, so a *user* edit can be told from
        # our own prefill/preset write (both post Input.Changed).
        self._written = {}

    def compose(self) -> ComposeResult:
        with Horizontal(id="dr-box"):
            with Vertical(id="dr-left"):
                with Horizontal(id="dr-presets"):
                    for label, wid, _key in self.PRESETS:
                        yield Button(label, id=wid)
                with Vertical(id="dr-start-card", classes="dr-card"):
                    yield Static("START TIME", classes="dr-card-title")
                    with Horizontal(classes="dr-fields"):
                        yield Input(placeholder="MM/DD/YYYY", id="dr-start-date")
                        yield TimeInput(placeholder="00:00", id="dr-start-time")
                    with Horizontal(id="dr-start-quick"):
                        for i, label in enumerate(self.QUICK_START):
                            yield Button(label, id=f"dr-start-quick-{i}")
                with Vertical(id="dr-end-card", classes="dr-card"):
                    yield Static("END TIME", classes="dr-card-title")
                    with Horizontal(classes="dr-fields"):
                        yield Input(placeholder="MM/DD/YYYY", id="dr-end-date")
                        yield TimeInput(placeholder="00:00", id="dr-end-time")
                    with Horizontal(id="dr-end-quick"):
                        for i, label in enumerate(self.QUICK_END):
                            yield Button(label, id=f"dr-end-quick-{i}")
                yield Checkbox("End time follows current time", True,
                               id="dr-follow")
                with Horizontal(id="dr-nudge"):
                    yield Static("step", classes="dr-card-title")
                    for label, minutes in self.STEPS:
                        yield Button(label, id=f"dr-step-{minutes}")
                with Horizontal(id="dr-actions"):
                    yield Button("Cancel", id="dr-cancel")
                    yield Button("Confirm", variant="primary", id="dr-ok")
            with Vertical(id="dr-cal"):
                with Horizontal(id="dr-cal-head"):
                    yield Button("‹", id="dr-prev")
                    yield Static("", id="dr-cal-month")
                    yield Button("›", id="dr-next")
                with Grid(id="dr-cal-grid"):
                    for dow in self.DOW:
                        yield Static(dow, classes="dr-dow")
                    for i in range(self.CELLS):
                        # A Static, not a Button: a Button renders the two-digit
                        # label "30" as " 3 " at a 3-cell width, and widening
                        # every cell to 4 would not fit the calendar beside the
                        # cards. The day is read from the cell id in on_click.
                        yield Static("", id=f"dr-day-{i}", classes="dr-day")
                with Grid(id="dr-hour-grid"):
                    for h in range(self.HOURS):
                        # Zero-padded id and label: the id is parsed back with
                        # rsplit("-", 1), so an id the label does not match
                        # would be a second spelling to keep in step.
                        yield Static(f"{h:02d}", id=f"dr-hour-{h:02d}",
                                     classes="dr-hour")

    def on_mount(self) -> None:
        self._prefill()
        self.query_one("#dr-box").border_subtitle = "Supports both date and time"
        self._set_active("start")
        self._render_steps()

    # -- prefill from the tab's current window ------------------------------

    def _prefill(self) -> None:
        cur = self._current
        follow = True
        if isinstance(cur, db.CustomWindow):
            start, end, follow = cur.start_ms, cur.end_ms, cur.follow_now
        else:
            start, end = db.window_bounds(cur if cur is not None else "all",
                                          self._now)
        self._set_field("#dr-start", start)
        # A following end shows *now*; a fixed one shows the stored bound.
        self._set_field("#dr-end", self._now if follow else end)
        self.query_one("#dr-follow", Checkbox).value = follow
        anchor = start if start is not None else self._now
        self._view = datetime.fromtimestamp(anchor / 1000).date().replace(day=1)

    def _set_field(self, prefix: str, ms_value) -> None:
        date_in = self.query_one(f"{prefix}-date", Input)
        time_in = self.query_one(f"{prefix}-time", Input)
        if ms_value is None:
            date_in.value = time_in.value = ""
        else:
            dt = datetime.fromtimestamp(ms_value / 1000)
            date_in.value = db.format_date(dt)
            time_in.value = db.format_time(dt.hour, dt.minute)
        self._written[date_in.id] = date_in.value
        self._written[time_in.id] = time_in.value
        self._render_hours()

    def _follows_now(self) -> bool:
        return self.query_one("#dr-follow", Checkbox).value

    # -- the calendar --------------------------------------------------------

    def _grid_start(self) -> date:
        """The date in cell 0: the first day of the view, back to Sunday."""
        lead = (self._view.weekday() + 1) % 7      # Sunday-first column
        return self._view - timedelta(days=lead)

    def _day_at(self, index: int) -> date:
        return self._grid_start() + timedelta(days=index)

    def _active_date(self):
        """The active card's typed date, or ``None`` if it is blank/bad."""
        try:
            return db.parse_date(
                self.query_one(f"#dr-{self._active}-date", Input).value,
                self._view.year).date()
        except ValueError:
            return None

    def _render_calendar(self) -> None:
        today = datetime.fromtimestamp(self._now / 1000).date()
        chosen = self._active_date()
        self.query_one("#dr-cal-month", Static).update(
            self._view.strftime("%B %Y"))
        for i in range(self.CELLS):
            day = self._day_at(i)
            cell = self.query_one(f"#dr-day-{i}", Static)
            cell.update(str(day.day))
            cell.set_class(day.month != self._view.month, "-other")
            cell.set_class(day == today, "-today")
            cell.set_class(day == chosen, "-selected")

    def _step_month(self, delta: int) -> None:
        month = self._view.month + delta
        year = self._view.year + (month - 1) // 12
        self._view = date(year, (month - 1) % 12 + 1, 1)
        self._render_calendar()

    # -- the hour grid -------------------------------------------------------

    def _active_time(self):
        """The active card's typed time as ``(hour, minute)``, or ``None``."""
        try:
            return db.parse_time(
                self.query_one(f"#dr-{self._active}-time", Input).value)
        except ValueError:
            return None

    def _render_hours(self) -> None:
        """Mark the active card's hour in the 24-cell grid."""
        parsed = self._active_time()
        hour = parsed[0] if parsed else None
        for h in range(self.HOURS):
            # The same zero padding the cells are built with: an id written two
            # ways is a second spelling to keep in step, and this one broke the
            # whole modal the moment the cells gained it.
            self.query_one(f"#dr-hour-{h:02d}", Static).set_class(
                h == hour, "-selected")

    def _pick_hour(self, hour: int) -> None:
        """Write a clicked hour into the active card, keeping its minute.

        The grid is the calendar's other half: the day cells set the date, these
        set the hour, and both land in whichever card is active.
        """
        parsed = self._active_time()
        minute = parsed[1] if parsed else 0
        self._write_time(self._active, db.format_time(hour, minute))

    def _write_time(self, card: str, value: str) -> None:
        """Set one card's time field, and fix the end if that is the card."""
        if card == "end":
            # A chosen end time means a fixed end, not "now".
            self.query_one("#dr-follow", Checkbox).value = False
        self.query_one(f"#dr-{card}-time", Input).value = value
        self._written[f"dr-{card}-time"] = value
        self._render_hours()

    def _pick_day(self, index: int) -> None:
        """Write a clicked day into the active card."""
        day = self._day_at(index)
        if self._active == "end":
            # Choosing an end date means a fixed end, not "now".
            self.query_one("#dr-follow", Checkbox).value = False
        self.query_one(f"#dr-{self._active}-date", Input).value = db.format_date(day)
        self._written[f"dr-{self._active}-date"] = db.format_date(day)
        if (day.year, day.month) != (self._view.year, self._view.month):
            self._view = day.replace(day=1)
        self._render_calendar()

    def _set_active(self, which: str) -> None:
        self._active = which
        self.query_one("#dr-start-card").set_class(which == "start", "-active")
        self.query_one("#dr-end-card").set_class(which == "end", "-active")
        self._render_calendar()
        self._render_hours()

    # -- presets -------------------------------------------------------------

    def _apply_preset(self, key: str) -> None:
        start, _ = db.window_bounds(key, self._now)
        self._set_field("#dr-start", start)
        self.query_one("#dr-follow", Checkbox).value = True
        if start is not None:
            self._view = datetime.fromtimestamp(start / 1000).date().replace(day=1)
        self._render_calendar()

    def _apply_quick(self, card: str, label: str) -> None:
        """A quick-time chip: ``now`` re-follows the clock, a time fixes it."""
        if label == "now":
            self._set_field("#dr-end", self._now)
            self.query_one("#dr-follow", Checkbox).value = True
            self._render_hours()
            return
        hour, minute = db.parse_time(label)
        self._write_time(card, db.format_time(hour, minute))

    # -- nudge step ----------------------------------------------------------

    def _set_step(self, minutes: int) -> None:
        self._step = minutes
        for wid in ("#dr-start-time", "#dr-end-time"):
            self.query_one(wid, TimeInput).step = minutes
        self._render_steps()

    def _render_steps(self) -> None:
        for _label, minutes in self.STEPS:
            self.query_one(f"#dr-step-{minutes}", Button).set_class(
                minutes == self._step, "-selected")

    # -- confirm -------------------------------------------------------------

    def _confirm(self) -> None:
        follow = self._follows_now()
        year = self._view.year
        try:
            start = db.parse_datetime(
                self.query_one("#dr-start-date", Input).value,
                self.query_one("#dr-start-time", Input).value, year)
            end = None if follow else db.parse_datetime(
                self.query_one("#dr-end-date", Input).value,
                self.query_one("#dr-end-time", Input).value, year)
        except ValueError:
            self.notify("Dates are MM/DD/YYYY, times HH:MM (24-hour)",
                        severity="error")
            return
        if not follow and end is None:
            self.notify("Set an end date, or tick 'End follows current time'",
                        severity="error")
            return
        if start is not None and end is not None and end < start:
            self.notify("End is before start", severity="error")
            return
        self.dismiss(db.CustomWindow(start, end, follow))

    def action_cancel(self) -> None:
        self.dismiss(None)

    # -- events --------------------------------------------------------------

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        if event.checkbox.id == "dr-follow" and event.value:
            # Ticking "follows current time" puts the end back on now.
            self._set_field("#dr-end", self._now)

    def on_input_changed(self, event: Input.Changed) -> None:
        # A *user* edit of an end field means a fixed end, so drop the follow
        # switch. Our own prefill/preset writes are ignored by matching the
        # value we recorded.
        wid = event.input.id or ""
        if wid.endswith("-time"):
            self._render_hours()      # a typed hour re-marks the grid
        if not wid.startswith("dr-end") or not self._follows_now():
            return
        if self._written.get(wid) == event.value:
            return
        self.query_one("#dr-follow", Checkbox).value = False

    def on_click(self, event) -> None:
        # A calendar day and an hour cell are Statics, so their clicks arrive
        # here rather than as a Button.Pressed — read the index from the id.
        wid = getattr(event.widget, "id", "") or ""
        if wid.startswith("dr-day-"):
            self._pick_day(int(wid.rsplit("-", 1)[1]))
            return
        if wid.startswith("dr-hour-"):
            self._pick_hour(int(wid.rsplit("-", 1)[1]))
            return
        # Clicking anywhere on a card makes it the calendar's target.
        node = event.widget
        while node is not None:
            if getattr(node, "has_class", None) and node.has_class("dr-card"):
                self._set_active(
                    "start" if node.id == "dr-start-card" else "end")
                return
            node = getattr(node, "parent", None)

    def on_descendant_focus(self, event) -> None:
        wid = getattr(event.control, "id", "") or ""
        if wid.startswith("dr-start"):
            self._set_active("start")
        elif wid.startswith("dr-end"):
            self._set_active("end")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        preset = next((key for _l, wid, key in self.PRESETS if wid == bid), None)
        if preset is not None:
            self._apply_preset(preset)
        elif bid.startswith("dr-start-quick-"):
            self._apply_quick("start", self.QUICK_START[int(bid.rsplit("-", 1)[1])])
        elif bid.startswith("dr-end-quick-"):
            self._apply_quick("end", self.QUICK_END[int(bid.rsplit("-", 1)[1])])
        elif bid.startswith("dr-step-"):
            self._set_step(int(bid.rsplit("-", 1)[1]))
        elif bid == "dr-prev":
            self._step_month(-1)
        elif bid == "dr-next":
            self._step_month(1)
        elif bid == "dr-cancel":
            self.dismiss(None)
        elif bid == "dr-ok":
            self._confirm()


class WideTableMixin:
    """The narrow-terminal column plan, shared by every host that owns a table.

    A table never shows a half column: ``_fill_wide`` keeps the longest *prefix*
    of its columns that fits and names the dropped ones on the note line above
    it. A rule that covers two of the thirteen tables is not a rule, so the
    mechanism lives here and each host declares its own ``WIDE_TABLES``.
    ``_wide_rows`` is created by each host's ``__init__`` — it must not be a
    class attribute, or two open screens would share one row cache.
    """

    CELL_COST = 2          # the gutter DataTable puts between two columns
    MIN_SHOWN = 2          # never narrow a table to a single unnamed column
    # Measured on this machine's database, not guessed: every entity name fits
    # 28 cells; the only column that runs past it is the project path, where
    # 162 of 482 rows exceed 24 and the longest is 74. So a cap at 28 costs no
    # name and buys back the cells a path was spending.
    NAME_CAP = 28          # shipped default; the app shadows it from settings

    @property
    def name_cap(self) -> int:
        """The cap this host actually renders with.

        ``NAME_CAP`` is the shipped default, which the app shadows from the
        settings file. A screen pushed on top of the app has no config of its
        own, so it overrides this to read the app — otherwise a configured cap
        would silently apply to the tabs and not to the detail screens.
        """
        return self.NAME_CAP

    def _cap_cell(self, text: str) -> str:
        """Fit one cell to ``name_cap``, keeping the half that carries meaning.

        A path's head is the prefix every row shares and its tail is what tells
        the rows apart, so a path is capped from the left; everything else — a
        tool, a model, a skill — is read from the start and capped from the
        right.
        """
        cap = self.name_cap
        if display_width(text) <= cap:
            return text
        keep = cap - 1
        return ("…" + text[-keep:]) if "/" in text else (text[:keep] + "…")

    def _column_widths(self, labels, rows):
        return [max(display_width(label),
                    max((display_width(r[i]) for r in rows), default=0))
                for i, label in enumerate(labels)]

    def _set_columns(self, table, cols) -> None:
        if [str(c.label) for c in table.columns.values()] == list(cols):
            return
        table.clear(columns=True)
        table.add_columns(*cols)

    def _fill(self, table, rows, key_index=0, key_kind=None, key_rows=None):
        """Fill a table, restoring the highlight by row key (not by index).

        A re-sort between refreshes must not move the highlight onto a different
        entity, which index-based restore would.
        """
        cur_key = None
        cur = table.cursor_row
        if key_kind and table.row_count:
            try:
                cur_key = table.coordinate_to_cell_key(
                    table.cursor_coordinate).row_key
            except Exception:
                cur_key = None
        table.clear()
        n = 0
        for i, r in enumerate(rows):
            # NULL renders as "-", matching fmt_n (never a blank cell).
            cells = [("-" if c is None else str(c)) for c in r]
            key = None
            if key_kind:
                # key_index may be a single index or a tuple of indices for a
                # composite (unique) key, e.g. mcp -> (server, tool). ``key_rows``
                # carries the UNCAPPED values: two entities whose names share the
                # visible prefix render identically and must still route apart,
                # so the key cannot come from the cell.
                idx = (key_index if isinstance(key_index, (tuple, list))
                       else (key_index,))
                src = key_rows[i] if key_rows is not None else r
                key = "\t".join([key_kind] + [str(src[j]) for j in idx])
            table.add_row(*cells, key=key)
            n += 1
        if cur_key is not None and cur_key in table.rows:
            table.move_cursor(row=table.get_row_index(cur_key))
        elif not key_kind and 0 <= cur < n:
            # Unkeyed tables (the Usage request list) keep the index restore.
            table.move_cursor(row=cur)

    def _fill_or_empty(self, table, rows, empty_msg, key_index=0, key_kind=None,
                       key_rows=None):
        if rows:
            self._fill(table, rows, key_index, key_kind, key_rows)
        else:
            table.clear()
            table.add_row(empty_msg, *([""] * (len(table.columns) - 1)))

    def _fill_wide(self, table_id, rows, empty_msg, key_index=0, key_kind=None,
                   width=None, key_rows=None):
        table = self.query_one(f"#{table_id}", DataTable)
        labels, note_id, chrome = self.WIDE_TABLES[table_id]
        self._wide_rows[table_id] = (rows, empty_msg, key_index, key_kind,
                                    key_rows)
        note = self.query_one(f"#{note_id}", Static)
        width = width or self.size.width or 0
        if not width or not rows:
            self._set_columns(table, labels)
            self._fill_or_empty(table, rows, empty_msg, key_index, key_kind,
                                key_rows)
            note.update("")
            note.display = False
            if not width:
                self.call_after_refresh(self._plan_wide_tables)
            return
        raw = [[("-" if c is None else str(c)) for c in r] for r in rows]
        shown = [[self._cap_cell(c) for c in rr] for rr in raw]
        capped = any(a != b for rr, ss in zip(raw, shown) for a, b in zip(rr, ss))
        # The key is always built from the UNCAPPED row: two entities that render
        # identically must still route to their own detail screen, so a shortened
        # cell must never become a key.
        key_source = key_rows if key_rows is not None else raw
        full = self._column_widths(labels, raw)
        budget = width - chrome
        if sum(full) + self.CELL_COST * len(labels) <= budget:
            self._set_columns(table, labels)
            self._fill_or_empty(table, raw, empty_msg, key_index, key_kind,
                                key_source)
            note.update("")
            note.display = False
            return
        widths = self._column_widths(labels, shown)
        keep = len(labels)
        while keep > self.MIN_SHOWN and sum(widths[:keep]) + self.CELL_COST * keep > budget:
            keep -= 1
        self._set_columns(table, labels[:keep])
        self._fill_or_empty(table, [r[:keep] for r in shown], empty_msg,
                            key_index, key_kind, key_source)
        cap_note = f" · cells capped at {self.name_cap}" if capped else ""
        if keep >= len(labels):
            note.update(f"cells capped at {self.name_cap}" if capped else "")
            note.display = capped
            return
        note.update(f"{keep} of {len(labels)} columns · hidden: "
                    f"{', '.join(labels[keep:])} · widen to "
                    f"{sum(full) + self.CELL_COST * len(labels) + chrome}"
                    + (" · even these are cut" if
                       sum(widths[:keep]) + self.CELL_COST * keep > budget else "")
                    + cap_note)
        note.display = True

    def _plan_wide_tables(self, width=None) -> None:
        for table_id, (rows, empty_msg, key_index, key_kind,
                       key_rows) in list(self._wide_rows.items()):
            self._fill_wide(table_id, rows, empty_msg, key_index=key_index,
                            key_kind=key_kind, width=width, key_rows=key_rows)


# --- detail screens --------------------------------------------------------


class HistoryScreen(WideTableMixin, Screen):
    """One entity's recent calls, inside the window the tab was showing."""

    BINDINGS = [Binding("escape,q", "app.pop_screen", "Back")]
    WIDE_TABLES = {"hist-table": (HISTORY_COLUMNS["tool"], "colnote-hist", 1)}

    def __init__(self, kind, name, display=None, start_ts=None, end_ts=None,
                 window_label="all time", tool=None):
        super().__init__()
        self.kind = kind
        self.entity = name
        self.heading = display or name
        self.start_ts = start_ts
        self.end_ts = end_ts
        self.window_label = window_label
        self.tool = tool
        self._wide_rows = {}
        self._call_ids = []

    @property
    def name_cap(self):
        return self.app.name_cap

    def compose(self) -> ComposeResult:
        yield TopBar(f"{self.kind} · {self.heading}")
        yield Static(id="hist-head")
        yield Static("", id="colnote-hist", classes="colnote")
        yield Table(id="hist-table", zebra_stripes=True)
        yield Footer()

    def on_mount(self) -> None:
        self.WIDE_TABLES = {"hist-table": (HISTORY_COLUMNS[self.kind],
                                           "colnote-hist", 1)}
        self.title = f"{self.kind}: {self.heading}"
        for tid, (cols, _note, _chrome) in self.WIDE_TABLES.items():
            table = self.query_one(f"#{tid}", DataTable)
            table.cursor_type = "row"
            table.add_columns(*cols)
        self._reload()

    def _reload(self) -> None:
        conn = self.app.conn
        summary = db.q_entity_summary(conn, self.kind, self.entity,
                                      self.start_ts, self.end_ts, tool=self.tool)
        rows = db.q_history(conn, self.kind, self.entity,
                            limit=self.app.cfg["detail_limit"],
                            start_ts=self.start_ts, end_ts=self.end_ts,
                            tool=self.tool)
        lines = [f"[b]{self.kind}: {self.heading}[/b]  ·  window: {self.window_label}"]
        if summary is not None:
            lines.extend(summary_lines(summary, self.kind))
            total = summary["runs"] if self.kind == "agent" else summary["calls"]
            if total and len(rows) < total:
                noun = "run" if self.kind == "agent" else "call"
                lines.append(f"  showing latest {fmt_n(len(rows))} of "
                             f"{count_n(total, noun)}")
        self.query_one("#hist-head", Static).update("\n".join(lines))
        table_rows, call_ids = [], []
        for r in rows:
            status = r["status"] or "pending"
            if self.kind == "model":
                table_rows.append(self._model_row(r))
            elif self.kind == "tool":
                table_rows.append([ts(r["ts"]), r["name"], r["category"], status,
                                   ms(r["duration_ms"]), r["project"] or "-",
                                   r["session_id"] or "-"])
            elif self.kind == "mcp":
                table_rows.append([ts(r["ts"]), r["name"] or "-", r["tool"] or "-",
                                   status, ms(r["duration_ms"]),
                                   r["session_id"] or "-"])
            elif self.kind == "agent":
                rep = bool(r["token_covered"])
                table_rows.append([
                    ts(r["ts"]), r["name"], status, ms(r["duration_ms"]),
                    fmt_n(r["requests"]),
                    fmt_tok(self._usage_total(r), rep),
                    fmt_tok(r["input_tokens"], rep),
                    fmt_tok(r["output_tokens"], rep),
                    fmt_tok(r["cache_read_tokens"], rep),
                    fmt_tok(r["cache_creation_tokens"], rep),
                    fmt_credit_total(r["credits"], r["credits_carried"]),
                    pct(r["peak_context"]),
                    r["project"] or "-", r["session_id"] or "-"])
            else:
                table_rows.append([ts(r["ts"]), r["name"], status,
                                   ms(r["duration_ms"]), r["project"] or "-",
                                   r["session_id"] or "-"])
            call_ids.append(r["call_id"])
        self._call_ids = call_ids
        # The key is the call id, not a visible cell, so a row can open its own
        # detail without spending a column on it.
        self._fill_wide("hist-table", table_rows, "no calls in this window",
                        key_index=0, key_kind=self.kind,
                        key_rows=[[cid] for cid in call_ids])

    @staticmethod
    def _model_row(r):
        rep = db.row_reports_tokens(r)
        return [ts(r["ts"]), r["model"], db.provider_label(r["provider"]),
                fmt_tok(r["usage_total_tokens"], rep),
                fmt_credits(r["credits"]),
                fmt_tok(r["input_tokens"], rep), fmt_tok(r["output_tokens"], rep),
                fmt_tok(r["cache_read_tokens"], rep),
                fmt_tok(r["cache_miss_tokens"], rep),
                fmt_tok(r["cache_creation_tokens"], rep),
                r["stop_reason"] or "-"]

    @staticmethod
    def _usage_total(r):
        if r["input_tokens"] is None or r["output_tokens"] is None:
            return None
        return r["input_tokens"] + r["output_tokens"] + (r["cache_read_tokens"] or 0)

    def on_data_table_row_selected(self, event) -> None:
        if event.data_table.id != "hist-table":
            return
        try:
            index = self.query_one("#hist-table", DataTable).cursor_row
        except Exception:
            return
        if not 0 <= index < len(self._call_ids):
            return
        if self.kind == "agent":
            # A run is not a request: Enter opens the responses it launched, not
            # the Agent tool call's own two stored fields.
            self.app.push_screen(RequestsScreen(
                self.heading, agent_call_id=self._call_ids[index],
                agent_type=self.entity, start_ts=self.start_ts,
                end_ts=self.end_ts, window_label=self.window_label))
            return
        self.app.push_screen(
            CallScreen(self.kind, self._call_ids[index], self.heading))


class ProjectScreen(WideTableMixin, Screen):
    """One project's sessions, with cost and context pressure per session."""

    BINDINGS = [Binding("escape,q", "app.pop_screen", "Back")]
    WIDE_TABLES = {"project-table": (PROJECT_SESSION_COLUMNS,
                                     "colnote-project", 1)}

    def __init__(self, project, start_ts=None, end_ts=None,
                 window_label="all time"):
        super().__init__()
        self.project = project
        self.heading = project or "(no project)"
        self.start_ts = start_ts
        self.end_ts = end_ts
        self.window_label = window_label
        self._wide_rows = {}
        self._session_ids = []

    @property
    def name_cap(self):
        return self.app.name_cap

    def compose(self) -> ComposeResult:
        yield TopBar(f"project · {self.heading}")
        yield Static(id="project-head")
        yield Static("", id="colnote-project", classes="colnote")
        yield Table(id="project-table", zebra_stripes=True)
        yield Footer()

    def on_mount(self) -> None:
        self.title = f"project: {self.heading}"
        for tid, (cols, _note, _chrome) in self.WIDE_TABLES.items():
            table = self.query_one(f"#{tid}", DataTable)
            table.cursor_type = "row"
            table.add_columns(*cols)
        self._reload()

    def _reload(self) -> None:
        conn = self.app.conn
        summary = db.q_project_summary(conn, self.project,
                                       self.start_ts, self.end_ts)
        lines = [f"[b]project: {self.heading}[/b]  ·  window: {self.window_label}"]
        if summary is not None:
            lines.extend(summary_lines(summary, "project"))
        self.query_one("#project-head", Static).update("\n".join(lines))
        rows = db.q_project_sessions(conn, self.project, self.start_ts,
                                     self.end_ts)
        out = []
        self._session_ids = []
        for r in rows:
            rep = bool(r["token_covered"])
            out.append([
                r["session_id"] or "-", ts(r["last_used"]),
                fmt_n(r["requests"]),
                fmt_tok(r["usage_total_tokens"], rep),
                fmt_credit_total(r["credits"], r["credits_carried"]),
                pct(r["peak_context"]),
                fmt_n(r["compactions"]),
                fmt_n(r["compacted_tokens"]),
                r["model"] or "-"])
            self._session_ids.append(r["session_id"])
        self._fill_wide("project-table", out, EMPTY_MSG["project_sessions"],
                        key_index=0, key_kind="session",
                        key_rows=[[sid] for sid in self._session_ids])

    def on_data_table_row_selected(self, event) -> None:
        if event.data_table.id != "project-table":
            return
        try:
            index = self.query_one("#project-table", DataTable).cursor_row
        except Exception:
            return
        if 0 <= index < len(self._session_ids):
            self.app.push_screen(RequestsScreen(
                self._session_ids[index], session_id=self._session_ids[index],
                start_ts=self.start_ts, end_ts=self.end_ts,
                window_label=self.window_label))


class RequestsScreen(WideTableMixin, Screen):
    """The responses of one session or one Agent run, oldest first."""

    BINDINGS = [Binding("escape,q", "app.pop_screen", "Back")]
    WIDE_TABLES = {"req-table": (SESSION_CALL_COLUMNS, "colnote-req", 1)}

    def __init__(self, heading, session_id=None, agent_call_id=None,
                 agent_type=None, start_ts=None, end_ts=None,
                 window_label="all time"):
        super().__init__()
        self.heading = heading or "(unnamed)"
        self.session_id = session_id
        self.agent_call_id = agent_call_id
        self.agent_type = agent_type
        self.start_ts = start_ts
        self.end_ts = end_ts
        self.window_label = window_label
        self._wide_rows = {}
        self._message_ids = []

    @property
    def name_cap(self):
        return self.app.name_cap

    def compose(self) -> ComposeResult:
        yield TopBar(f"requests · {self.heading}")
        yield Static(id="req-head")
        yield Static("", id="colnote-req", classes="colnote")
        yield Table(id="req-table", zebra_stripes=True)
        yield Footer()

    def on_mount(self) -> None:
        self.title = f"requests: {self.heading}"
        for tid, (cols, _note, _chrome) in self.WIDE_TABLES.items():
            table = self.query_one(f"#{tid}", DataTable)
            table.cursor_type = "row"
            table.add_columns(*cols)
        conn = self.app.conn
        if self.session_id is not None:
            summary = db.q_session_summary(conn, self.session_id,
                                          self.start_ts, self.end_ts)
            rows = db.q_session_calls(conn, self.session_id, self.start_ts,
                                      self.end_ts,
                                      limit=self.app.cfg["detail_limit"])
        else:
            summary = db.q_agent_run_summary(conn, self.agent_call_id)
            rows = db.q_agent_run_calls(conn, self.agent_call_id,
                                        limit=self.app.cfg["detail_limit"])
        total = summary["requests"] or 0
        head = [f"[b]requests: {self.heading}[/b]  ·  window: {self.window_label}",
                f"  {count_n(total, 'request')}"
                f" · credits {fmt_credit_total(summary['credits'], summary['credits_carried'])}"
                f" · peak context {pct(summary['peak_context'])}"]
        if total and len(rows) < total:
            head.append(f"  showing latest {fmt_n(len(rows))} of "
                        f"{fmt_n(total)} requests")
        self.query_one("#req-head", Static).update("\n".join(head))
        out = []
        self._message_ids = []
        for r in rows:
            rep = db.row_reports_tokens(r)
            if self.agent_call_id:
                agent = self.agent_type or "agent"
            else:
                agent = r["agent_type"] or ("agent" if r["is_sidechain"] else "main")
            out.append([
                ts(r["ts"]), r["model"], db.provider_label(r["provider"]),
                agent, fmt_credits(r["credits"]),
                fmt_tok(r["usage_total_tokens"], rep),
                fmt_tok(r["input_tokens"], rep),
                fmt_tok(r["output_tokens"], rep),
                fmt_tok(r["cache_read_tokens"], rep),
                fmt_tok(r["cache_miss_tokens"], rep),
                fmt_tok(r["cache_creation_tokens"], rep),
                pct(r["context_usage_ratio"]),
                "yes" if r["is_sidechain"] else "no"])
            self._message_ids.append(r["call_id"])
        self._fill_wide("req-table", out, EMPTY_MSG[
                            "agent_requests" if self.agent_call_id
                            else "session_calls"],
                        key_index=0, key_kind="model",
                        key_rows=[[mid] for mid in self._message_ids])

    def on_data_table_row_selected(self, event) -> None:
        if event.data_table.id != "req-table":
            return
        try:
            index = self.query_one("#req-table", DataTable).cursor_row
        except Exception:
            return
        if 0 <= index < len(self._message_ids):
            self.app.push_screen(
                CallScreen("model", self._message_ids[index], self.heading))


class ModelResponsesScreen(WideTableMixin, Screen):
    """Every model response of one model, inside the tab's window."""

    BINDINGS = [Binding("escape,q", "app.pop_screen", "Back")]
    WIDE_TABLES = {"resp-table": (RESPONSE_COLUMNS, "colnote-resp", 1)}

    def __init__(self, model, start_ts=None, end_ts=None,
                 window_label="all time", provider=None):
        super().__init__()
        self.model = model
        self.provider = provider
        self.start_ts = start_ts
        self.end_ts = end_ts
        self.window_label = window_label
        self._wide_rows = {}

    @property
    def name_cap(self):
        return self.app.name_cap

    @property
    def heading(self) -> str:
        """One model can appear under several providers; the row's channel is
        part of its identity on the Tokens tab, so the drill-down keeps it."""
        if self.provider is None:
            return self.model
        return f"{self.model} · {db.provider_label(self.provider)}"

    def compose(self) -> ComposeResult:
        yield TopBar(f"model · {self.heading}")
        yield Static(id="resp-head")
        yield Static("", id="colnote-resp", classes="colnote")
        yield Table(id="resp-table", zebra_stripes=True)
        yield Footer()

    def on_mount(self) -> None:
        self.title = f"model: {self.heading}"
        for tid, (cols, _note, _chrome) in self.WIDE_TABLES.items():
            table = self.query_one(f"#{tid}", DataTable)
            table.cursor_type = "row"
            table.add_columns(*cols)
        conn = self.app.conn
        summary = db.q_entity_summary(conn, "model", self.model, self.start_ts,
                                      self.end_ts, provider=self.provider)
        rows = db.q_history(conn, "model", self.model,
                            limit=self.app.cfg["detail_limit"],
                            start_ts=self.start_ts, end_ts=self.end_ts,
                            provider=self.provider)
        lines = [f"[b]model: {self.heading}[/b]  ·  window: {self.window_label}"]
        if summary is not None:
            lines.extend(summary_lines(summary, "model"))
            total = summary["calls"]
            if total and len(rows) < total:
                lines.append(f"  showing latest {fmt_n(len(rows))} of "
                             f"{count_n(total, 'request')}")
        self.query_one("#resp-head", Static).update("\n".join(lines))
        out = []
        for r in rows:
            rep = db.row_reports_tokens(r)
            out.append([ts(r["ts"]), r["model"],
                        db.provider_label(r["provider"]),
                        fmt_tok(r["usage_total_tokens"], rep),
                        fmt_credits(r["credits"]),
                        fmt_tok(r["input_tokens"], rep),
                        fmt_tok(r["output_tokens"], rep),
                        fmt_tok(r["cache_read_tokens"], rep),
                        fmt_tok(r["cache_miss_tokens"], rep),
                        fmt_tok(r["cache_creation_tokens"], rep),
                        self._hit_rate(r["cache_read_tokens"],
                                       r["cache_miss_tokens"],
                                       r["cache_creation_tokens"]),
                        r["call_id"]])
        # The key is the call id, taken from `key_rows` and not from the rendered
        # cell: a message id is capped on screen like any other value, and two
        # responses whose ids share a prefix must still route apart. `key_index`
        # therefore indexes *key_rows* (one value per row), not the 12th column.
        self._fill_wide("resp-table", out, "no responses in this window",
                        key_index=0, key_kind="model",
                        key_rows=[[r["call_id"]] for r in rows])

    def on_data_table_row_selected(self, event) -> None:
        """A response row opens that response; the tab router no longer sees it.

        The key is `model\\t<message id>`, which the app's router would read as a
        *model named after a message id*. Owning the event here is what keeps the
        single-call detail reachable from this screen.
        """
        if event.data_table.id != "resp-table":
            return
        key = event.row_key.value
        if not key or "\t" not in key:
            return
        _kind, _, message_id = key.partition("\t")
        self.app.push_screen(CallScreen("model", message_id, self.model))

    @staticmethod
    def _hit_rate(hit, miss, write) -> str:
        frac = db.cache_hit_rate(hit, miss, write)
        return "-" if frac is None else f"{frac * 100:.1f}%"


class PluginScreen(WideTableMixin, Screen):
    """A plugin's own breakdown: its attributed calls and installed resources."""

    BINDINGS = [Binding("escape,q", "app.pop_screen", "Back")]
    WIDE_TABLES = {"plugin-table": (PLUGIN_COLUMNS, "colnote-plugin", 1)}

    def __init__(self, plugin):
        super().__init__()
        self.plugin = plugin
        self._wide_rows = {}

    @property
    def name_cap(self):
        return self.app.name_cap

    def compose(self) -> ComposeResult:
        yield TopBar(f"plugin · {self.plugin}")
        yield Static(id="plugin-head")
        yield Static("", id="colnote-plugin", classes="colnote")
        yield DataTable(id="plugin-table", zebra_stripes=True)
        yield Footer()

    def on_mount(self) -> None:
        for tid, (cols, _note, _chrome) in self.WIDE_TABLES.items():
            table = self.query_one(f"#{tid}", DataTable)
            table.cursor_type = "row"
            table.add_columns(*cols)
        conn = self.app.conn
        rows = conn.execute(
            "SELECT kind, target, marketplace, ts, session_id, project"
            " FROM plugin_usage WHERE plugin = ? ORDER BY ts DESC",
            (self.plugin,)).fetchall()
        inv = conn.execute(
            "SELECT kind, name, version FROM inventory"
            " WHERE kind IN ('skill','agent','command') AND owner_plugin = ?"
            " ORDER BY kind, name", (self.plugin,)).fetchall()
        head = [f"[b]plugin: {self.plugin}[/b]",
                f"  {count_n(len(rows), 'attributed call')} · "
                f"{count_n(len(inv), 'installed resource')}"]
        self.query_one("#plugin-head", Static).update("\n".join(head))
        data = [[ts(r["ts"]), r["kind"], r["target"], "used",
                 r["marketplace"] or "-", r["session_id"] or "-",
                 r["project"] or "-"] for r in rows]
        # An installed-but-never-called resource still belongs on the plugin's
        # page: the list is the union, keyed by name, exactly like the tabs.
        data += [["-", r["kind"], r["name"], "installed", "-", "-", "-"]
                 for r in inv]
        self._fill_wide("plugin-table", data, "no usage or install records",
                        key_index=2, key_kind="plugin")


class CallScreen(WideTableMixin, Screen):
    """Every stored field of one call, wrapped to the terminal."""

    BINDINGS = [Binding("escape,q", "app.pop_screen", "Back")]

    def __init__(self, kind, key, heading=""):
        super().__init__()
        self.kind = kind
        self.key = key
        self.heading = heading
        self._wide_rows = {}

    def compose(self) -> ComposeResult:
        yield TopBar(f"{self.kind} call")
        with Vertical(id="call-body"):
            yield Static(f"[b]{self.kind} call[/b]  {self.heading}",
                         id="call-title")
        yield Footer()

    def on_mount(self) -> None:
        self.title = f"{self.kind} call: {self.key}"
        row = db.q_call_detail(self.app.conn, self.kind, self.key)
        body = self.query_one("#call-body")
        if row is None:
            body.mount(Static("this call is no longer indexed",
                              classes="call-value"))
            return
        body.mount_all(
            Horizontal(Static(label, classes="call-label"),
                       Static(value, classes="call-value"))
            for label, value in _call_detail_rows(row))


class FilterInput(Input):
    """The one-line filter for the active tab.

    Typing filters live; Enter hands the keys back to the table but keeps the
    bar visible, and Esc clears the filter and hides the bar. Its own binding
    runs before the app's, so a printable key — `a` and `s` included — inserts
    into the field instead of reaching the tab's shortcut.
    """

    BINDINGS = [Binding("escape", "close_filter", "Clear filter", show=False)]

    def action_close_filter(self) -> None:
        # Widget-level bindings resolve on the widget, so forward to the app.
        self.app.action_close_filter()


# --- the app ---------------------------------------------------------------


class DashBox(Vertical):
    """The Dashboard's scrolling stack.

    It re-fits the project ledger when its own height changes. The app's
    resize event arrives before this box is laid out at the new height, so a
    fit measured there would use the previous window's spare.
    """

    def on_resize(self, event) -> None:  # textual.events.Resize
        try:
            app = self.app
        except Exception:  # not mounted yet
            return
        fit = getattr(app, "_fit_dashboard", None)
        if fit is not None:
            app.call_after_refresh(fit)


class TrackerApp(WideTableMixin, App):
    """The eight-tab Qoder CN CLI usage UI."""

    CSS = """
    #topbar { dock: top; }
    TabbedContent { height: 1fr; }
    .colnote { padding: 0 1; color: $text-muted; height: auto; display: none; }
    .filternote { padding: 0 1; color: $text-muted; height: auto; }
    DataTable { height: 1fr; }
    /* NOT docked: the Footer docks itself to the bottom, and a second bottom
       dock lands on the same row and is painted over — which hid the unclaimed
       count, the schema version and the database path behind the footer. In
       normal flow the bar takes the row directly above the footer. */
    #status { height: 1; color: $text-muted; padding: 0 1; }
    /* The active tab's filter: one row while it is in use, gone otherwise.
       Hiding is done in Python (widget.display), which outranks CSS. */
    #filter-input { display: none; height: 1; border: none; padding: 0 1; }
    /* Summary pages (Dashboard + Usage). Both share .usage-panel and
       .panel-title. The Usage page has one .summary-panels row (height capped,
       scrolling internally so its table below keeps its rows). The Dashboard is
       a stack of .dash-row rows; #dash-box scrolls. Each row is a Horizontal;
       .compact stacks it on narrow terminals. */
    #usage-box, #dash-box { height: 1fr; padding: 0 1; }
    #dash-box { overflow-y: auto; }
    .summary-panels { height: auto; max-height: 40%; overflow-y: auto; }
    .usage-panel { border: round $primary; padding: 0 1; margin: 0 1 0 0;
                   width: 1fr; height: auto; }
    #panel-dash-tools, #panel-dash-cache { margin: 0; }
    .panel-title { text-style: bold; color: $accent; }
    #usage-window { padding: 0; }
    #usage-note, #dash-note, #filter-tokens, #hist-head, #resp-head, #plugin-head {
        padding: 0 1; color: $text-muted; height: auto; }
    /* The dashboard legend is a footer, not prose: one row, clipped if the
       terminal is too narrow for it. */
    #dash-note { height: 1; text-wrap: nowrap; text-overflow: ellipsis; }
    #usage-table { height: 1fr; min-height: 6; }
    .summary-panels.compact, .dash-row.compact { layout: vertical; }
    /* Stacked one-per-row wastes the width a narrow terminal still has: at
       80x24 four stacked panels push the table's rows off the visible area.
       Two columns keep a 24-char panel line plus its border and padding
       (~28 cells) legible and leave the table room. */
    .summary-panels.compact { layout: grid; grid-size: 2; }
    /* Below two cells' minimum, stacking is the only layout that does not wrap
       a panel line mid-value. */
    .summary-panels.compact.stacked { layout: vertical; }
    .summary-panels.compact .usage-panel,
    .dash-row.compact .usage-panel { width: 100%; margin: 0 0 1 0; }
    .dash-row { height: auto; margin: 0 0 1 0; }
    /* The ledger grows with its rows and the box scrolls once the screen is
       full — a fixed 9 rows silently hid the rest of a long project list. */
    #panel-dash-projects { height: auto; }
    #dash-projects { height: auto; }
    /* The drill-down detail: fixed label column, wrapping value column. */
    #call-body { height: 1fr; overflow-y: auto; padding: 0 1; }
    .call-label { width: 18; color: $text-muted; }
    .call-value { width: 1fr; }
    #call-title { padding: 1 0 0 0; }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh", "Sync now"),
        Binding("s", "cycle_sort", "Sort"),
        Binding("a", "toggle_show_all", "All rows"),
        Binding("slash", "open_filter", "Filter"),
        Binding("d", "date_range", "Date"),
        Binding("escape", "close_filter", "Clear filter", show=False),
        Binding("tab", "next_tab", "Next tab", priority=True),
        Binding("shift+tab", "previous_tab", "Prev tab", priority=True),
    ]
    TITLE = "Qoder CN CLI usage tracker"
    # No built-in Header means no ctrl+p palette; disable it outright too.
    ENABLE_COMMAND_PALETTE = False
    # Every shortcut this app owns. A pushed detail screen disables these so
    # its own Back binding (q/escape) and row actions stay the only keys —
    # otherwise `s` would re-sort a page that has no sort.
    APP_SHORTCUT_ACTIONS = frozenset({
        "quit", "refresh", "cycle_sort", "toggle_show_all", "open_filter",
        "close_filter", "date_range", "next_tab", "previous_tab",
    })

    # Pane ids in display order, for wrap-around tab cycling.
    TAB_IDS = [tid for tid, _ in TABS]
    # The tab shown on launch. The TopBar Select must start on this tab's own
    # range, or its mount-time Changed would overwrite that tab's default
    # before the user touches anything.
    INITIAL_TAB = "dashboard"

    # Below this width the panels stack instead of sitting side by side: each
    # column needs ~28 cells for a 24-char panel line plus border/padding, so
    # 4 x 28 = 112.
    COMPACT_WIDTH = 112
    # A panel line is 24 columns of content plus 2 border and 2 padding, so a
    # two-up cell needs 28. Measured: the panels region is 2 cells narrower than
    # the terminal, so two columns stop fitting below 60.
    STACKED_WIDTH = 60

    # Every table: id -> (its columns in display order, the note that names what
    # the plan dropped, the cells it loses to its surroundings). Chrome is 2 for
    # a table sitting directly in a tab pane and 4 on the padded Usage page —
    # measured at 80/100/160 columns, not derived. A table that fits keeps every
    # column and shows no note, so registering one that does not need the plan
    # costs nothing.
    WIDE_TABLES = {
        "tools-table": (TOOLS_COLUMNS, "colnote-tools", 2),
        "skills-table": (SKILLS_COLUMNS, "colnote-skills", 2),
        "agents-table": (AGENTS_COLUMNS, "colnote-agents", 2),
        "plugins-table": (PLUGINS_COLUMNS, "colnote-plugins", 2),
        "mcp-table": (MCP_COLUMNS, "colnote-mcp", 2),
        "tokens-table": (TOKENS_COLUMNS, "colnote-tokens", 2),
        "usage-table": (USAGE_COLUMNS, "colnote-usage", 4),
        "dash-projects": (PROJECT_COLUMNS, "colnote-projects", 6),
    }

    def __init__(self, db_path=None, clock=None, cfg=None):
        super().__init__()
        self.db_path = Path(db_path) if db_path else db.DB_PATH
        self.cfg = cfg or db.load_config()
        self.conn = db.open_db(self.db_path)
        db.ensure_schema(self.conn)
        # Injectable so a test can pin "now" without freezing the process clock.
        self._clock = clock or time.time
        self.ranges = dict(DEFAULT_RANGES)
        self._wide_rows = {}
        self._schema_note = ""
        self._sync_note = ""
        self._query_note = ""
        self._usage_ready = False
        self._syncing = False
        self._sync_running = False
        self._sync_pending = False
        # Entity tabs show only what the window actually used; `a` widens them
        # to the installed ∪ ever-used set, matching how Tokens never lists an
        # unused model.
        self.show_all = False
        # Per tab sort mode: 0 is the query's own order, then the tab's
        # declared fields. Kept per tab so one page never reorders another.
        self._sort_mode = {}
        # Per tab live filter text; applied to the rendered cells so a category
        # or a state narrows the page as well as a name.
        self._tab_filters = {}
        # Set while the app writes the shared Input during a tab switch, so a
        # programmatic value does not count as a user edit.
        self._loading_filter = False
        self.sync_mod = None

    @property
    def name_cap(self):
        return self.cfg["name_cap"]

    # --- layout ---

    def compose(self) -> ComposeResult:
        yield TopBar(self.TITLE, with_range=True,
                     range_value=self.ranges[self.INITIAL_TAB], id="topbar")
        yield FilterInput(placeholder="filter rows — Enter keeps, Esc clears",
                          id="filter-input")
        with TabbedContent(initial=self.INITIAL_TAB):
            for tid, title in TABS:
                with TabPane(title, id=tid):
                    if tid == "dashboard":
                        yield DashBox(
                            Horizontal(
                                Vertical(
                                    Static("Usage", classes="panel-title"),
                                    Static(id="dash-sum-requests"),
                                    Static(id="dash-sum-credits"),
                                    Static(id="dash-sum-sessions"),
                                    Static(id="dash-sum-projects"),
                                    Static(id="dash-kpi-builtin"),
                                    Static(id="dash-kpi-skills"),
                                    Static(id="dash-kpi-agents"),
                                    Static(id="dash-kpi-mcp"),
                                    Static(id="dash-kpi-plugins"),
                                    classes="usage-panel", id="panel-dash-kpi",
                                ),
                                Vertical(
                                    Static("Tokens", classes="panel-title"),
                                    Static(id="dash-tok-usage"),
                                    Static(id="dash-tok-input"),
                                    Static(id="dash-tok-output"),
                                    Static(id="dash-tok-credits"),
                                    Static(id="dash-tok-hit-rate"),
                                    classes="usage-panel", id="panel-dash-tokens",
                                ),
                                Vertical(
                                    Static("Cache", classes="panel-title"),
                                    Static(id="dash-cache-hit"),
                                    Static(id="dash-cache-miss"),
                                    Static(id="dash-cache-write"),
                                    classes="usage-panel", id="panel-dash-cache",
                                ),
                                classes="dash-row", id="dash-row-1",
                            ),
                            Horizontal(
                                Vertical(
                                    Static("Top models", classes="panel-title"),
                                    Static(id="dash-models"),
                                    classes="usage-panel", id="panel-dash-models",
                                ),
                                Vertical(
                                    Static("Top tools", classes="panel-title"),
                                    Static(id="dash-tools"),
                                    classes="usage-panel", id="panel-dash-tools",
                                ),
                                classes="dash-row", id="dash-row-2",
                            ),
                            Horizontal(
                                Vertical(
                                    Static("Projects", classes="panel-title"),
                                    Static("", id="colnote-projects",
                                           classes="colnote"),
                                    DataTable(id="dash-projects",
                                              zebra_stripes=True),
                                    classes="usage-panel",
                                    id="panel-dash-projects",
                                ),
                                classes="dash-row", id="dash-row-3",
                            ),
                            Static(id="dash-note"),
                            id="dash-box",
                        )
                    elif tid == "usage":
                        yield Vertical(
                            Horizontal(
                                Vertical(
                                    Static("Window / Requests",
                                           classes="panel-title"),
                                    Static(id="usage-window"),
                                    Static(id="sum-requests"),
                                    Static(id="sum-sessions"),
                                    Static(id="sum-projects"),
                                    classes="usage-panel", id="panel-window",
                                ),
                                Vertical(
                                    Static("Tokens", classes="panel-title"),
                                    Static(id="sum-usage-total"),
                                    Static(id="sum-input"),
                                    Static(id="sum-output"),
                                    classes="usage-panel", id="panel-tokens",
                                ),
                                Vertical(
                                    Static("Cache breakdown",
                                           classes="panel-title"),
                                    Static(id="sum-cache-hit"),
                                    Static(id="sum-cache-miss"),
                                    Static(id="sum-cache-write"),
                                    Static(id="sum-hit-rate"),
                                    classes="usage-panel", id="panel-cache",
                                ),
                                Vertical(
                                    Static("Credits", classes="panel-title"),
                                    Static(id="sum-credits"),
                                    Static(id="sum-list-price"),
                                    Static(id="sum-agent-credits"),
                                    Static(id="sum-avg-credits"),
                                    classes="usage-panel", id="panel-credits",
                                ),
                                id="usage-panels", classes="summary-panels",
                            ),
                            Static(id="usage-note"),
                            Static("", id="colnote-usage", classes="colnote"),
                            Table(id="usage-table", zebra_stripes=True),
                            id="usage-box",
                        )
                    elif tid == "tokens":
                        yield Static("", id="filter-tokens", classes="filternote")
                        yield Static("", id="colnote-tokens", classes="colnote")
                        yield DataTable(id="tokens-table", zebra_stripes=True)
                    else:
                        # The filter line explains what `a` is hiding (or what
                        # Plugins' `uses` counts); it is separate from the
                        # column-plan note because the two can both be present.
                        yield Static("", id=f"filter-{tid}", classes="filternote")
                        yield Static("", id=f"colnote-{tid}", classes="colnote")
                        yield Table(id=f"{tid}-table", zebra_stripes=True)
        yield Static("", id="status")
        yield Footer()

    def on_mount(self) -> None:
        # One source of column truth: the registry the plan reads is the registry
        # that creates the columns, so a table cannot gain a column the plan does
        # not know how to drop.
        for tid, (cols, _note, _chrome) in self.WIDE_TABLES.items():
            table = self.query_one(f"#{tid}", DataTable)
            table.cursor_type = "row"
            table.add_columns(*cols)
        # Best-effort schema upgrade. migrate() returns a status, so a locked or
        # damaged file is named on the status line instead of showing an empty
        # panel as though that were the user's actual usage.
        note = db.migrate(self.db_path)
        self._schema_note = "" if note in ("current", "migrated", "created") else note
        self._usage_ready = True
        # Explicitly hidden and unfocusable before the first focus pass: a
        # CSS-hidden Input still receives focus, and then every printable
        # shortcut is typed into a field the user cannot see.
        inp = self.query_one("#filter-input", Input)
        inp.display = False
        inp.can_focus = False
        self.refresh_data()
        # After the initial activation has settled, put the keyboard on the
        # table rather than the range Select. Deferred because focusing during
        # mount races that activation and can bounce the active tab back.
        self.call_after_refresh(self._focus_active_table)
        # One incremental sync on launch, then the screen stays still. New rows
        # arrive only through `r` or by reopening the TUI.
        self._request_sync()

    # --- tab cycling (uses the real TabbedContent.active API) --------------

    def _cycle_tab(self, delta: int) -> None:
        tc = self.query_one(TabbedContent)
        try:
            idx = self.TAB_IDS.index(tc.active)
        except ValueError:
            idx = 0
        tc.active = self.TAB_IDS[(idx + delta) % len(self.TAB_IDS)]

    def action_next_tab(self) -> None:
        self._cycle_tab(1)

    def action_previous_tab(self) -> None:
        self._cycle_tab(-1)

    # --- the window --------------------------------------------------------

    @property
    def active_tab(self) -> str:
        try:
            return self.query_one(TabbedContent).active or self.INITIAL_TAB
        except Exception:
            return self.INITIAL_TAB

    def _now_ms(self) -> int:
        """The current time in epoch milliseconds, via the injectable clock."""
        return int(self._clock() * 1000)

    def _range_of(self, pane):
        """The window value for a pane, or ``None`` for the all-time Plugins tab
        (which is not in ``self.ranges``)."""
        return self.ranges.get(pane)

    def _bounds_for(self, pane):
        """``(start_ms, end_ms)`` for a pane's **own** window, from now.

        Never cached: every refresh derives the window from the current time, so
        a long-running app does not drift and a ``follow_now`` window keeps
        following. ``None`` as the start means all-time.
        """
        return db.bounds_of(self._range_of(pane), self._now_ms())

    def _window(self, tab=None):
        """``(start, end, label)`` for a tab: the older three-tuple interface."""
        tab = tab or self.active_tab
        value = self._range_of(tab)
        start, end = self._bounds_for(tab)
        return start, end, db.window_label(value)

    def _sync_range_widget(self) -> None:
        """Point the shared range Select at the active tab's own window.

        Every tab keeps its own range, so switching tabs shows that tab's range,
        not the one you last picked elsewhere. The Plugins tab has no window, so
        the Select is hidden there. Setting ``Select.value`` posts a ``Changed``
        message, which ``on_select_changed`` ignores when it already matches the
        active tab's range (so this never re-triggers a refresh loop).
        """
        try:
            sel = self.query_one("#range", Select)
        except Exception:
            return
        pane = self.active_tab
        value = self._range_of(pane)
        if value is None:
            sel.display = False
            self._set_date_label("")
            return
        sel.display = True
        # A custom window is shown as "Custom…" in the Select, with the span
        # itself on the 📅 control next to it.
        target = CUSTOM_RANGE if isinstance(value, db.CustomWindow) else value
        if sel.value != target:
            sel.value = target
        self._set_date_label(db.window_label(value),
                             isinstance(value, db.CustomWindow))

    def _set_date_label(self, text: str, custom: bool = False) -> None:
        try:
            self.query_one("#topbar", TopBar).set_range_label(text, custom=custom)
        except Exception:
            pass

    def _open_date_range(self, pane) -> None:
        """Open the date picker for a pane and apply the picked window to it."""
        modal = DateRangeModal(self._range_of(pane), self._now_ms())

        def done(result) -> None:
            if result is None:
                self._sync_range_widget()   # cancel: revert the Select
                return
            self.ranges[pane] = result
            self._sync_range_widget()
            self._refresh_active_tab()

        self.push_screen(modal, done)

    # --- status line -------------------------------------------------------

    def _set_status(self, msg: str) -> None:
        self.query_one("#status", Static).update(msg)

    def _status_line(self) -> str:
        """The status line, composed from what the app currently knows.

        One composer, because `s`, a failed re-query and a sync with nothing to
        index each used to overwrite the line with a message that then stayed
        there — "Syncing…" outliving a finished sync was the visible one.
        """
        try:
            counts = db.q_overview(self.conn)
        except sqlite3.Error as exc:
            return f"! {type(exc).__name__}: {exc}"
        # The bar is clipped from the right, so freshness and the drift signal
        # come first. The database path and schema version live in
        # `qcut health`; printing them here cut the useful tail off at 80 cols.
        status = (f"{count_n(counts['builtin_tool_calls'], 'builtin call')} · "
                  f"{count_n(counts['model_responses'], 'response')} · "
                  f"{counts['commands']:,} cmd · "
                  f"unclaimed {fmt_n(counts['unparsed_records'])} · "
                  f"last {ts(counts['last_ts'])}")
        if self._syncing:
            status += " · syncing"
        if self._schema_note:
            status += f" · ! {self._schema_note}"
        if self._sync_note:
            # Every number above is the last known one, not the current one.
            status += f" · ! {self._sync_note}"
        if self._query_note:
            status += f" · ! {self._query_note}"
        return status

    def _report_query_error(self, exc: sqlite3.Error) -> None:
        """Name a failed re-query instead of leaving the old page on screen.

        A tab that could not be refreshed keeps its previous rows, which look
        exactly like usage the user still has; a locked or damaged file has to
        say so in the one place the user reads. Kept apart from a sync failure:
        one successful query must not clear the other's warning.
        """
        self._query_note = f"{type(exc).__name__}: {exc}"
        self._set_status(self._status_line())

    # --- data fill / refresh -----------------------------------------------

    def refresh_data(self, conn=None) -> None:
        """Re-fill every tab that is currently populated.

        A full refresh (after a sync that indexed something) touches each tab,
        because a new row can land anywhere; tab switches, filter edits and a
        sort re-query only the visible one.
        """
        own = conn is None
        self._query_note = ""
        if own:
            try:
                conn = db.open_db(self.db_path, readonly=True)
            except sqlite3.Error as exc:
                self._report_query_error(exc)
                return
        try:
            self._fill_dashboard(conn)
            self._fill_usage(conn)
            self._fill_tools(conn)
            self._fill_skills(conn)
            self._fill_agents(conn)
            self._fill_plugins(conn)
            self._fill_mcp(conn)
            self._fill_tokens(conn)
        except sqlite3.Error as exc:
            self._report_query_error(exc)
        finally:
            if own:
                conn.close()
        self._sync_range_widget()
        self._set_status(self._status_line())
        self._apply_responsive_layout()

    @staticmethod
    def _panel_line(label: str, value: str) -> str:
        # Fixed-width label + right-aligned value keeps the panels aligned
        # without relying on text-align. 12 + 12 = 24 columns, so the line does
        # not wrap inside a quarter-width panel at 120+ columns.
        return f"[dim]{label:<12}[/dim]{value:>12}"

    def _put_panel(self, wid: str, label: str, val: str) -> None:
        self.query_one(wid, Static).update(self._panel_line(label, val))

    @staticmethod
    def _hit_rate(hit, miss, write) -> str:
        frac = db.cache_hit_rate(hit, miss, write)
        return "-" if frac is None else f"{frac * 100:.1f}%"

    # --- Dashboard ---------------------------------------------------------

    def _fill_dashboard(self, conn, start=None, end=None) -> None:
        if start is None and end is None:
            start, end, _ = self._window("dashboard")
        kpi = db.q_usage_kpi(conn, start, end)
        summary = db.q_usage_summary(conn, start, end)
        models = db.q_usage_model_stats(conn, start, end)
        tools = db.q_tools(conn, limit=self.cfg["top_n"], start_ts=start, end_ts=end)

        self._put_panel("#dash-sum-requests", "Requests",
                        f"{kpi['requests']:,}")
        self._put_panel("#dash-sum-credits", "Credits",
                        fmt_credit_total(kpi["credits"], kpi["credits_carried"]))
        self._put_panel("#dash-sum-sessions", "Sessions",
                        f"{kpi['model_sessions']:,}")
        self._put_panel("#dash-sum-projects", "Projects",
                        f"{kpi['model_projects']:,}")
        self._put_panel("#dash-kpi-builtin", "Tool calls",
                        f"{kpi['builtin_tool_calls']:,}")
        self._put_panel("#dash-kpi-skills", "Skills", f"{kpi['skills']:,}")
        self._put_panel("#dash-kpi-agents", "Agents", f"{kpi['agents']:,}")
        self._put_panel("#dash-kpi-mcp", "MCP", f"{kpi['mcp']:,}")
        self._put_panel("#dash-kpi-plugins", "Plugins", f"{kpi['plugins']:,}")

        # A window whose calls all failed to report a token sums to zero in every
        # token column while possibly billing real credits; `tok_rep` is the
        # single flag that turns those structural zeros into `—` here and on the
        # Usage page, so the two panels can never disagree about one window.
        tok_rep = summary["token_covered"] > 0
        self._put_panel("#dash-tok-usage", "Usage Total",
                        fmt_tok(summary["usage_total_tokens"], tok_rep))
        self._put_panel("#dash-tok-input", "Input",
                        fmt_tok(summary["input_tokens"], tok_rep))
        self._put_panel("#dash-tok-output", "Output",
                        fmt_tok(summary["output_tokens"], tok_rep))
        self._put_panel("#dash-tok-credits", "Credits",
                        fmt_credit_total(kpi["credits"], kpi["credits_carried"]))
        self._put_panel("#dash-cache-hit", "Cache hit",
                        fmt_tok(summary["cache_hit"], tok_rep))
        self._put_panel("#dash-cache-miss", "Cache miss",
                        fmt_tok(summary["cache_miss"], tok_rep))
        self._put_panel("#dash-cache-write", "Cache write",
                        fmt_tok(summary["cache_write"], tok_rep))
        self._put_panel("#dash-tok-hit-rate", "Cache hit rate",
                        self._hit_rate(summary["cache_hit"], summary["cache_miss"],
                                       summary["cache_write"]))

        if not models:
            self.query_one("#dash-models", Static).update(
                "nothing in this window")
        else:
            top = models[:self.cfg["top_n"]]
            # Sized to the panel: a fixed 41-cell line fits the three-up row at
            # 140 columns until a credit total or a count grows a digit.
            panel = self.query_one("#dash-models", Static)
            budget = (panel.size.width or 41) - 1
            nums = [fmt_compact_n(r["usage_total_tokens"]) for r in top]
            creds = [fmt_credit_total(r["credits"], r["credits_carried"]) for r in top]
            num_w = max(len(n) for n in nums)
            cred_w = max(len(c) for c in creds)
            label_w = max(8, budget - (num_w + cred_w + 8))
            def name(r):
                # The same merged label as the Tokens tab: a built-in row says
                # `Built-in`, a BYOK row names the channel that served it.
                return clip(f"{r['model']} · {db.provider_label(r['provider'])}",
                            label_w)
            self.query_one("#dash-models", Static).update("\n".join(
                f"{i}. {name(r):<{label_w}}{n:>{num_w}} tok {c:>{cred_w}}"
                for i, (r, n, c) in enumerate(zip(top, nums, creds), 1)))

        # The name-keyed list keeps 0-call tools under a window; drop them.
        live = [r for r in tools if r["calls"]][:self.cfg["top_n"]]
        if not live:
            self.query_one("#dash-tools", Static).update("nothing in this window")
        else:
            # Sized to the panel too: the old 20/12 variant wrapped the latency
            # onto its own line, and a fixed 38 fits only until the counts grow.
            panel = self.query_one("#dash-tools", Static)
            budget = (panel.size.width or 41) - 1
            calls = [count_n(r["calls"], "call") for r in live]
            spans = [ms(r["avg_ms"]) for r in live]
            call_w = max(len(c) for c in calls)
            span_w = max(len(s) for s in spans)
            name_w = max(8, budget - (call_w + span_w + 6))
            self.query_one("#dash-tools", Static).update("\n".join(
                f"{i}. {clip(r['tool_name'], name_w):<{name_w}}"
                f"{c:>{call_w}} · {s:>{span_w}}"
                for i, (r, c, s) in enumerate(zip(live, calls, spans), 1)))

        self._render_project_board(conn, start, end)
        if not any(kpi.values()) and not (summary["requests"] or 0):
            self.query_one("#dash-note", Static).update(
                "No data in this window — widen the range above.")
        else:
            self.query_one("#dash-note", Static).update(
                "Provider: Built-in = Qoder, else BYOK · `—` = no token · "
                "`-` = no data · Enter = open a project")
        self.call_after_refresh(self._fit_dashboard)

    #: Each .dash-row carries one row of bottom margin.
    DASH_MARGIN = 1

    def _fit_dashboard(self, settle: bool = True) -> None:
        """Let the project ledger take the height the rows above it leave.

        Every dashboard row is content-sized, so on a tall terminal the box
        ended a dozen rows above the status line. The ledger is the only panel
        that can *use* extra rows — it shows one per project — so it grows into
        them, and only into them: when the rows above already fill the box the
        spare is zero and the ledger keeps its natural height, which is what
        keeps a long list scrolling instead of clipped.

        The spare is measured from the rows above and the table's own row
        count, never from the table's current height, so calling this twice
        gives the same answer.
        """
        try:
            box = self.query_one("#dash-box")
            note = self.query_one("#dash-note", Static)
            rows = [self.query_one(f"#dash-row-{i}") for i in (1, 2, 3)]
            table = self.query_one("#dash-projects", DataTable)
        except Exception:  # the Dashboard is not mounted yet
            return
        natural = 1 + table.row_count          # the header, then one row each
        above = (note.size.height + rows[0].size.height + rows[1].size.height
                 + 3 * self.DASH_MARGIN)
        spare = box.size.height - above - (natural + 3)   # + title + border
        table.styles.min_height = natural + max(0, spare)
        # One correction pass: the sizes above are the ones the *previous*
        # layout left, so a window switch can make the first answer too
        # generous. Re-running after this refresh lands on the settled ones.
        if settle:
            self.call_after_refresh(lambda: self._fit_dashboard(False))

    def _render_project_board(self, conn, start, end) -> None:
        rows = db.q_project_board(conn, start, end)
        out, keys = [], []
        for r in rows:
            rep = bool(r["token_covered"])
            out.append([
                r["project"] or "(no project)", fmt_n(r["requests"]),
                fmt_tok(r["usage_total_tokens"], rep),
                fmt_credit_total(r["credits"], r["credits_carried"]),
                pct(r["peak_context"]), ts(r["last_used"])])
            keys.append([r["project"] if r["project"] is not None
                         else NULL_PROJECT_KEY])
        self._fill_wide("dash-projects", out, EMPTY_MSG["projects"],
                        key_index=0, key_kind="project", key_rows=keys)

    # Block characters for the per-day sparkline (index 0 = no activity).
    _SPARK = " ▁▂▃▄▅▆▇█"

    # --- Usage page --------------------------------------------------------

    def _render_usage_window(self) -> None:
        value = self._range_of("usage")
        start, end = self._bounds_for("usage")
        end_dt = datetime.fromtimestamp(end / 1000)
        if isinstance(value, db.CustomWindow):
            # A custom window can carry minute precision, so the panel shows the
            # full span with times.
            start_s = ("all" if start is None else datetime.fromtimestamp(
                start / 1000).strftime("%Y-%m-%d %H:%M"))
            span = f"{start_s} → {end_dt:%Y-%m-%d %H:%M}"
            label = "Custom range"
        else:
            # Presets are whole local calendar days: a clock time would be noise.
            # "Today" is one day, so printing the same date twice read like a bug.
            label = db.USAGE_RANGE_LABELS[value]
            end_date = end_dt.strftime("%Y-%m-%d")
            if start is None:
                span = end_date
            else:
                start_date = datetime.fromtimestamp(start / 1000).strftime("%Y-%m-%d")
                span = (start_date if start_date == end_date
                        else f"{start_date} — {end_date}")
        self.query_one("#usage-window", Static).update(f"{label}\n{span}")

    def _render_summary_panels(self, summary: dict) -> None:
        req = summary["requests"] or 0
        tok_rep = summary["token_covered"] > 0
        self._put_panel("#sum-requests", "Requests", f"{req:,}")
        self._put_panel("#sum-sessions", "Sessions",
                        f"{summary['sessions'] or 0:,}")
        self._put_panel("#sum-projects", "Projects",
                        f"{summary['projects'] or 0:,}")
        # Usage Total = Input + Output + cache hit; a display-only re-add, and the
        # only total this page shows.
        self._put_panel("#sum-usage-total", "Usage Total",
                        fmt_tok(summary["usage_total_tokens"], tok_rep))
        self._put_panel("#sum-input", "Input",
                        fmt_tok(summary["input_tokens"], tok_rep))
        self._put_panel("#sum-output", "Output",
                        fmt_tok(summary["output_tokens"], tok_rep))
        self._put_panel("#sum-cache-hit", "Cache hit",
                        fmt_tok(summary["cache_hit"], tok_rep))
        self._put_panel("#sum-cache-miss", "Cache miss",
                        fmt_tok(summary["cache_miss"], tok_rep))
        self._put_panel("#sum-cache-write", "Cache write",
                        fmt_tok(summary["cache_write"], tok_rep))
        self._put_panel("#sum-hit-rate", "Cache hit rate",
                        self._hit_rate(summary["cache_hit"], summary["cache_miss"],
                                       summary["cache_write"]))
        carried = summary["credits_carried"]
        self._put_panel("#sum-credits", "Credits",
                        fmt_credit_total(summary["credits"], carried))
        self._put_panel("#sum-list-price", "List price",
                        fmt_credit_total(summary["original_credits"], carried))
        self._put_panel("#sum-agent-credits", "Agent credits",
                        fmt_credit_total(summary["agent_credits"],
                                         summary["agent_credits_carried"]))
        avg = (summary["credits"] / req
               if req and carried else None)
        self._put_panel("#sum-avg-credits", "Avg / request",
                        fmt_credits(avg))

    def _fill_usage(self, conn, start=None, end=None) -> None:
        if not self._usage_ready:
            return
        if start is None and end is None:
            start, end = self._bounds_for("usage")
        self._render_usage_window()
        summary = db.q_usage_summary(conn, start, end)
        self._render_summary_panels(summary)
        query = self._tab_filters.get("usage", "").strip()
        mode = self._sort_mode.get("usage", 0)
        sort_label = USAGE_SORT_FIELDS[mode % len(USAGE_SORT_FIELDS)][0]
        rows = db.q_usage_request_logs(
            conn, start, end, limit=self.cfg["log_limit"],
            text=query or None, order_by=sort_label)
        total = summary["requests"] or 0
        parts = []
        if rows:
            parts.append("Enter opens a call · `—` = no token reported · "
                         "`-` = no data · Usage Total re-adds cache hit "
                         "(already inside Input) · cache miss derived")
            parts.append(f"sort: {sort_label}")
            if query:
                parts.append(f'filter "{query}" · {len(rows):,} matches')
            elif total > len(rows):
                parts.append(f"showing {fmt_n(len(rows))} of "
                             f"{fmt_n(total)} requests")
            if db.q_token_capture_gap(conn, start, end):
                parts.append(TOKEN_CAPTURE_HINT)
        self.query_one("#usage-note", Static).update(" · ".join(parts))
        out = []
        for r in rows:
            # Per row, not per window: one list can hold both route kinds, and a
            # BYOK response must keep its real tokens while the hosted row above
            # it shows `—`.
            rep = db.row_reports_tokens(r)
            out.append([
                ts(r["ts"]), r["model"] or "-", db.provider_label(r["provider"]),
                fmt_tok(r["usage_total_tokens"], rep),
                fmt_credits(r["credits"]),
                fmt_tok(r["input_tokens"], rep),
                fmt_tok(r["output_tokens"], rep),
                fmt_tok(r["cache_read_tokens"], rep),
                fmt_tok(r["cache_miss_tokens"], rep),
                fmt_tok(r["cache_creation_tokens"], rep),
                self._hit_rate(r["cache_read_tokens"], r["cache_miss_tokens"],
                               r["cache_creation_tokens"]),
            ])
        # Keyed by the call itself (`model` + message id), so Enter opens that
        # request's full detail instead of a model-wide response list. The key
        # kind is `call` precisely so the app router does not read the message
        # id as a model name.
        empty = (f'No requests match "{query}" in this window' if query
                 else EMPTY_MSG["usage"])
        self._fill_wide("usage-table", out, empty,
                        key_index=(0, 1), key_kind="call",
                        key_rows=[["model", r["message_id"]] for r in rows])

    # --- the entity tabs ---------------------------------------------------

    def _set_filter_note(self, tid: str, text: str) -> None:
        try:
            note = self.query_one(f"#filter-{tid}", Static)
        except Exception:
            return
        note.update(text)
        note.display = bool(text)

    def _sort_fields_for(self, pane):
        if pane == "usage":
            return USAGE_SORT_FIELDS
        return SORT_FIELDS.get(pane, ())

    def _sorted_indices(self, rows, fields, mode: int) -> list:
        """Indices of ``rows`` in the selected order; None-valued last.

        Returns indices rather than rows so the caller can slice the key rows
        with the same positions.
        """
        order = list(range(len(rows)))
        if not mode or not fields:
            return order
        _label, key, descending = fields[(mode - 1) % len(fields)]
        present = [i for i in order if rows[i][key] is not None]
        missing = [i for i in order if rows[i][key] is None]
        present.sort(
            key=lambda i: (str(rows[i][key]).lower()
                           if isinstance(rows[i][key], str) else rows[i][key]),
            reverse=descending)
        return present + missing

    @staticmethod
    def _matches(cells, query: str) -> bool:
        if not query:
            return True
        for cell in cells:
            plain = cell.plain if hasattr(cell, "plain") else cell
            if query in str(plain).lower():
                return True
        return False

    def _sort_label(self, pane) -> str:
        mode = self._sort_mode.get(pane, 0)
        if pane == "usage":
            return USAGE_SORT_FIELDS[mode % len(USAGE_SORT_FIELDS)][0]
        fields = SORT_FIELDS.get(pane, ())
        if not mode or not fields:
            return "tab order"
        return fields[(mode - 1) % len(fields)][0]

    def _entity_note(self, pane, shown: int, scope: int, total: int,
                     noun: str, base: str = "") -> str:
        """The one-line summary above a tab's table.

        Always visible because the active sort is state the page must name; a
        silent reorder is indistinguishable from data that changed. The filter
        count is `shown of scope` so a narrowed page never looks like the whole
        page, and the unused hint is the only place `a` is advertised.
        """
        parts = []
        query = self._tab_filters.get(pane, "").strip()
        if query:
            parts.append(f'filter "{query}" · {shown:,} of {scope:,}')
        else:
            parts.append(count_n(scope, noun))
        if pane in SHOW_ALL_TABS:
            if self.show_all:
                parts.append("incl. installed-unused")
            elif scope != total:
                parts.append(f"{count_n(total - scope, noun)} unused hidden"
                             " — press a")
        if base:
            parts.append(base)
        parts.append(f"sort: {self._sort_label(pane)}")
        return " · ".join(parts)

    def _render_entity(self, pane, rows, cells_for, empty, *,
                       keys_for=None, key_index=0, key_kind=None,
                       noun="row", base="", used=None) -> None:
        """Sort, filter, and fill one entity tab from its query rows.

        The filter runs over the rendered cells, so a category or a state
        narrows the page as well as a name. Keys are sliced with the same
        indices as the cells: a filtered page must still route each remaining
        row to its own detail screen.
        """
        scope = [r for r in rows if used is None or self.show_all or used(r)]
        scope_count = len(scope)
        order = self._sorted_indices(scope, self._sort_fields_for(pane),
                                     self._sort_mode.get(pane, 0))
        scope = [scope[i] for i in order]
        cells = [cells_for(r) for r in scope]
        keys = [keys_for(r) for r in scope] if keys_for else None
        query = self._tab_filters.get(pane, "").strip().lower()
        if query:
            keep = [i for i, c in enumerate(cells) if self._matches(c, query)]
            scope = [scope[i] for i in keep]
            cells = [cells[i] for i in keep]
            if keys is not None:
                keys = [keys[i] for i in keep]
        table_empty = (f'no rows match "{query}" in this tab'
                       if query and not cells else empty)
        self._fill_wide(f"{pane}-table", cells, table_empty,
                        key_index=key_index, key_kind=key_kind, key_rows=keys)
        self._set_filter_note(pane, self._entity_note(
            pane, len(scope), scope_count, len(rows), noun, base))

    def _fill_tools(self, conn, start=None, end=None) -> None:
        rows = db.q_tools(conn, start_ts=start, end_ts=end)
        self._render_entity(
            "tools", rows,
            lambda r: [r["tool_name"], fmt_n(r["calls"]), fmt_n(r["completed"]),
                       fmt_n(r["failed"]), fmt_n(r["pending"]), ms(r["avg_ms"]),
                       ts(r["last_used"])],
            EMPTY_MSG["tools"], key_kind="tool", noun="tool",
            used=lambda r: r["calls"])

    def _fill_skills(self, conn, start=None, end=None) -> None:
        rows = db.q_skills(conn, start_ts=start, end_ts=end)
        self._render_entity(
            "skills", rows,
            lambda r: [r["skill"], fmt_n(r["calls"]), fmt_n(r["completed"]),
                       fmt_n(r["failed"]), r["plugin"] or "-",
                       ts(r["last_used"])],
            EMPTY_MSG["skills"], key_kind="skill", noun="skill",
            used=lambda r: r["calls"])

    def _fill_agents(self, conn, start=None, end=None) -> None:
        rows = db.q_agents(conn, start_ts=start, end_ts=end)
        self._render_entity(
            "agents", rows,
            lambda r: [r["agent_type"], fmt_n(r["runs"]), fmt_n(r["completed"]),
                       fmt_n(r["failed"]), fmt_n(r["requests"]),
                       fmt_tok(r["usage_total_tokens"],
                               bool(r["token_covered"])),
                       fmt_credit_total(r["credits"], r["credits_carried"]),
                       pct(r["peak_context"]), ts(r["last_used"])],
            EMPTY_MSG["agents"], key_kind="agent", noun="agent",
            used=lambda r: r["runs"])

    def _fill_plugins(self, conn, start=None, end=None) -> None:
        # Plugins is the inventory tab and has no window: an installed-but-unused
        # plugin is the answer to "what do I have?", not noise. `uses` is the
        # attributed-call count; resources come from the install manifest.
        self._render_entity(
            "plugins", db.q_plugins(conn),
            lambda r: [r["plugin"], r["source"] or "-", r["version"] or "-",
                       fmt_n(r["uses"]), fmt_n(r["skills"]),
                       fmt_n(r["agents"]), fmt_n(r["commands"]),
                       ts(r["last_used"])],
            EMPTY_MSG["plugins"], key_kind="plugin", noun="plugin",
            base="uses = attributed calls · resources = installed skills /"
                 " agents / commands")

    def _fill_mcp(self, conn, start=None, end=None) -> None:
        # The key is built from the raw (server, tool) pair, never the rendered
        # cells: an absent half renders as `—`, and a drill-down must receive the
        # empty value it can test, not an em dash it would search for.
        rows = db.q_mcp(conn, start_ts=start, end_ts=end)
        self._render_entity(
            "mcp", rows,
            lambda r: [(r["server"] or "(admin)"), (r["tool"] or "—"),
                       fmt_n(r["calls"]), fmt_n(r["completed"]),
                       fmt_n(r["failed"]), ms(r["avg_ms"]),
                       ts(r["last_used"])],
            EMPTY_MSG["mcp"], key_index=(0, 1), key_kind="mcp",
            keys_for=lambda r: [r["server"] or "", r["tool"] or ""],
            noun="MCP target", used=lambda r: r["calls"])

    def _fill_tokens(self, conn, start=None, end=None) -> None:
        rows = db.q_models(conn, start_ts=start, end_ts=end)

        def cells(r):
            # `token_covered` only decides whether a literal zero is a
            # measurement or a `—`; it is no longer shown as a percentage.
            rep = bool(r["token_covered"])
            return [
                r["model"], db.provider_label(r["provider"]),
                fmt_tok(r["usage_total_tokens"], rep),
                fmt_credit_total(r["credits"], r["credits_carried"]),
                fmt_n(r["requests"]),
                fmt_tok(r["input_tokens"], rep), fmt_tok(r["output_tokens"], rep),
                fmt_tok(r["cache_read_tokens"], rep),
                fmt_tok(r["cache_miss_tokens"], rep),
                fmt_tok(r["cache_creation_tokens"], rep),
                self._hit_rate(r["cache_read_tokens"], r["cache_miss_tokens"],
                               r["cache_creation_tokens"]),
                ts(r["last_used"])]

        base = ("Provider: Built-in (Qoder) or the BYOK channel that served"
                " it · `—` = this route reported no token · `-` = no data ·"
                " Usage Total re-adds cache hit (already inside Input)") if rows else ""
        if db.q_token_capture_gap(conn, start, end):
            base = f"{base} · {TOKEN_CAPTURE_HINT}" if base else TOKEN_CAPTURE_HINT
        self._render_entity(
            "tokens", rows, cells, EMPTY_MSG["tokens"],
            # One model name can appear under two providers, so the drill-down
            # must carry the channel too or it widens back to both.
            keys_for=lambda r: [r["model"], r["provider"] or ""],
            key_index=(0, 1), key_kind="model", noun="model", base=base)

    # --- refresh plumbing --------------------------------------------------

    def _refresh_active_tab(self, conn=None) -> None:
        """Refresh only the visible tab (tab activation / filter / sort).

        Hidden tabs are left alone; they are populated when the user opens them
        (see ``on_tabbed_content_tab_activated``).
        """
        if not self._usage_ready:
            return
        pane = self.active_tab
        own = conn is None
        self._query_note = ""
        if own:
            try:
                conn = db.open_db(self.db_path, readonly=True)
            except sqlite3.Error as exc:
                self._report_query_error(exc)
                return
        try:
            start, end, _ = self._window(pane)
            if pane == "dashboard":
                self._fill_dashboard(conn, start, end)
            elif pane == "usage":
                self._fill_usage(conn, start, end)
            elif pane == "plugins":
                self._fill_plugins(conn)
            elif pane == "tools":
                self._fill_tools(conn, start, end)
            elif pane == "skills":
                self._fill_skills(conn, start, end)
            elif pane == "agents":
                self._fill_agents(conn, start, end)
            elif pane == "mcp":
                self._fill_mcp(conn, start, end)
            elif pane == "tokens":
                self._fill_tokens(conn, start, end)
        except sqlite3.Error as exc:
            self._report_query_error(exc)
        finally:
            if own:
                conn.close()
        self._sync_range_widget()
        self._set_status(self._status_line())
        self._apply_responsive_layout()

    def _refresh_active(self) -> None:
        """The old name for a single-tab refresh, kept for callers/tests."""
        self._refresh_active_tab()

    def _request_sync(self) -> None:
        # Guard against concurrent syncs: if one is running, mark a pending
        # request and return; the running worker retries on completion.
        if self._sync_running:
            self._sync_pending = True
            return
        self._sync_running = True
        self._syncing = True
        self._set_status(self._status_line())
        self.run_worker(self._do_sync, thread=True, group="sync")

    def _do_sync(self) -> None:
        # Runs in a worker thread. qcut-sync.run() opens its own RW connection and
        # uses the incremental sync_state table, so no connection is shared
        # across threads.
        try:
            if self.sync_mod is None:
                self.sync_mod = db.load_sync()
            stats = self.sync_mod.run(quiet=True, db_path=self.db_path)
            self.call_from_thread(self._on_sync_done, True, stats, None)
        except Exception as exc:  # report, never swallow
            self.call_from_thread(self._on_sync_done, False, None, str(exc))

    def _on_sync_done(self, ok, stats, err) -> None:
        self._sync_running = False
        self._syncing = False
        if ok:
            # A later success is the repair of an earlier failure; leaving the
            # old warning on the bar made a transient error permanent.
            self._sync_note = ""
            if (stats or {}).get("scanned", 0) > 0:
                self.refresh_data()          # new rows can touch any tab
            else:
                self._refresh_active_tab()   # nothing changed -> light refresh
                # A light refresh does not rewrite the bar, so without this the
                # transient "Syncing…" from `r` survives the sync that finished.
                self._set_status(self._status_line())
            self.notify(f"Synced {count_n((stats or {}).get('scanned', 0), 'file')}")
        else:
            # The same composer as the data line: a failed sync is a fact *about*
            # the numbers, not a replacement for them.
            self._sync_note = "Sync failed: " + (err or "unknown error")[:200]
            self._set_status(self._status_line())
            self.notify("Sync failed — keeping current data", severity="error")
        if self._sync_pending:
            self._sync_pending = False
            self._request_sync()

    # --- responsive layout -------------------------------------------------

    def _apply_responsive_layout(self, width=None) -> None:
        # Panels side by side on wide terminals; two-up when narrow; stacked
        # below that, where a second column would wrap a value mid-line. Every
        # summary page (Dashboard + Usage) uses .summary-panels or .dash-row.
        # Only .summary-panels has a rule for `stacked`; dash rows never two-up.
        if width is None:
            width = self.size.width
        try:
            compact = width < self.COMPACT_WIDTH
            stacked = width < self.STACKED_WIDTH
            for selector in (".summary-panels", ".dash-row"):
                for el in self.query(selector):
                    el.set_class(compact, "compact")
                    el.set_class(stacked, "stacked")
        except Exception:
            pass

    def on_resize(self, event) -> None:
        # event.size carries the NEW size (self.size still lags here).
        self._apply_responsive_layout(event.size.width)
        self._plan_wide_tables(event.size.width)
        # The ledger is re-fitted by DashBox.on_resize: this box is not laid out
        # at its new height yet, so a fit measured here would use the old one.

    # --- events ------------------------------------------------------------

    def on_tabbed_content_tab_activated(self, event) -> None:
        # Each tab keeps its own needle: show it (or hide the row) before the
        # refresh that applies it.
        self._sync_filter_bar()
        self._refresh_active_tab()
        self._focus_active_table()
        if self._tab_filters.get(self.active_tab, "").strip():
            self.call_after_refresh(self._anchor_active_table_top)

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id != "range":
            return
        pane = self.active_tab
        if self._range_of(pane) is None:
            return                        # Plugins has no window
        if event.value == CUSTOM_RANGE:
            # "Custom…" opens the picker — unless this is the programmatic sync
            # that points the Select at an already-custom window.
            if not isinstance(self._range_of(pane), db.CustomWindow):
                self._open_date_range(pane)
            return
        if event.value == self._range_of(pane):
            return                        # programmatic sync, not a user change
        self.ranges[pane] = event.value
        self._sync_range_widget()         # refresh the 📅 label
        self._refresh_active_tab()        # re-query only the visible tab

    def on_button_pressed(self, event: Button.Pressed) -> None:
        # Only the top bar's 📅 control reaches the app; the modal handles its
        # own buttons (it is the active screen).
        if event.button.id == "date-range":
            pane = self.active_tab
            if self._range_of(pane) is not None:
                self._open_date_range(pane)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        # This router belongs to the tab panes and to nothing else. Every table in
        # the app is filled through the same `_fill_wide`, so a pushed screen's row
        # keys carry the same `<kind>\t<value>` shape — and routing one here opens
        # the wrong thing: a call id was being read as an entity name, which pushed
        # an empty `HistoryScreen` (or a second `PluginScreen`) *on top of* the
        # detail the user had just asked for. Textual bubbles the event here after
        # the owning screen has handled it, so the table's identity is the only
        # thing that can tell the two apart.
        if event.data_table.id not in self.WIDE_TABLES:
            return
        key = event.row_key.value
        if not key:
            return
        if "\t" not in key:
            return
        kind, _, rest = key.partition("\t")
        pane = self.active_tab
        start, end, label = self._window(pane)
        if kind == "call":
            # A Usage row's key is `call\tmodel\t<message id>`: the row is one
            # response, so Enter opens that call's own detail — not a model-wide
            # list that would make the user find the same row again.
            call_kind, _, message_id = rest.partition("\t")
            if call_kind != "model" or not message_id:
                return
            self.push_screen(CallScreen("model", message_id, "Usage request"))
        elif kind == "mcp":
            # An MCP row is keyed by (server, tool). A tool row opens that tool's
            # history under its server; a server-only row opens the whole server.
            # Both query by the server, because q_history maps mcp to that column.
            server, _, tool = rest.partition("\t")
            if not server:
                return
            if tool:
                self.push_screen(HistoryScreen(
                    "mcp", server, display=f"{server} · {tool}", start_ts=start,
                    end_ts=end, window_label=label, tool=tool))
            else:
                self.push_screen(HistoryScreen(
                    "mcp", server, start_ts=start, end_ts=end,
                    window_label=label))
        elif kind == "plugin":
            self.push_screen(PluginScreen(rest))
        elif kind == "model":
            # A Tokens row is (model, provider): the provider is optional, and
            # passing it keeps the drill-down on the row that was selected.
            model, _, provider = rest.partition("\t")
            self.push_screen(ModelResponsesScreen(
                model, start, end, label, provider=provider or None))
        elif kind == "project":
            # A Dashboard project row opens its session ledger; the session row
            # then opens the requests, and a request its own detail.
            project = None if rest == NULL_PROJECT_KEY else rest
            self.push_screen(ProjectScreen(project, start, end, label))
        else:
            self.push_screen(HistoryScreen(kind, rest, start_ts=start,
                                           end_ts=end, window_label=label))

    def on_unmount(self) -> None:
        # The app owns one long-lived connection; letting the process exit on it
        # leaves a WAL side file behind on some filesystems.
        try:
            self.conn.close()
        except Exception:
            pass

    # --- actions -----------------------------------------------------------

    def check_action(self, action: str, parameters) -> bool | None:
        """Hide a key where pressing it could only do nothing.

        A pushed detail screen owns its keys: this app's shortcuts are disabled
        there, while the screen's own Back binding stays alive. On the main
        screen, sort/filter/expand disappear where no table or no expansion
        exists instead of leaving dead keys in the footer.
        """
        if len(self.screen_stack) > 1:
            return action not in self.APP_SHORTCUT_ACTIONS
        pane = self.active_tab
        if action in ("next_tab", "previous_tab"):
            return True
        if action == "toggle_show_all":
            return pane in SHOW_ALL_TABS
        if action == "cycle_sort":
            return bool(self._sort_fields_for(pane))
        if action in ("open_filter", "close_filter"):
            return pane in FILTERABLE_TABS
        if action == "date_range":
            return self._range_of(pane) is not None
        return True

    def action_refresh(self) -> None:
        # R = incremental sync + reload DB + refresh the UI (not just a re-render
        # of already-loaded data).
        self._set_status("Syncing…")
        self._request_sync()

    def action_toggle_show_all(self) -> None:
        # A = the entity tabs' union switch. Off is "what this window used",
        # matching Tokens; on is installed ∪ ever used, with zero rows visible.
        if self.active_tab not in SHOW_ALL_TABS:
            return
        self.show_all = not self.show_all
        self.notify("Showing all known entities" if self.show_all
                    else "Showing entities used in this window")
        self._refresh_active_tab()

    def action_cycle_sort(self) -> None:
        pane = self.active_tab
        fields = self._sort_fields_for(pane)
        if not fields:
            return
        mode = self._sort_mode.get(pane, 0)
        if pane == "usage":
            # Usage's first entry is its default order, not "tab order".
            self._sort_mode[pane] = (mode + 1) % len(fields)
        else:
            self._sort_mode[pane] = (mode + 1) % (len(fields) + 1)
        self._refresh_active_tab()

    def action_date_range(self) -> None:
        pane = self.active_tab
        if self._range_of(pane) is None:
            return
        self._open_date_range(pane)

    # --- the filter bar ----------------------------------------------------

    def _active_table_id(self):
        pane = self.active_tab
        if pane == "dashboard":
            return "dash-projects"
        if pane not in FILTERABLE_TABS:
            return None
        return "usage-table" if pane == "usage" else f"{pane}-table"

    def _focus_active_table(self) -> None:
        table_id = self._active_table_id()
        if table_id is None:
            return
        try:
            self.query_one(f"#{table_id}", DataTable).focus()
        except Exception:
            pass

    def _anchor_active_table_top(self) -> None:
        """Filter edits show the first match instead of pinning a deep row.

        ``_fill`` restores the cursor by row key on refreshes and sorts. Under
        a live filter that would keep a deep selection and make the list look
        as if it scrolls by itself, hiding the first matches.
        """
        table_id = self._active_table_id()
        if table_id is None:
            return
        try:
            table = self.query_one(f"#{table_id}", DataTable)
        except Exception:
            return
        if table.row_count:
            table.move_cursor(row=0)
            table.scroll_home(animate=False)

    def _sync_filter_bar(self) -> None:
        """Show the active tab's saved filter, or hide the row entirely."""
        try:
            inp = self.query_one("#filter-input", Input)
        except Exception:
            return
        pane = self.active_tab
        value = (self._tab_filters.get(pane, "")
                 if pane in FILTERABLE_TABS else "")
        self._loading_filter = True
        inp.value = value
        self._loading_filter = False
        inp.display = bool(value.strip())
        inp.can_focus = bool(value.strip())

    def action_open_filter(self) -> None:
        pane = self.active_tab
        if pane not in FILTERABLE_TABS:
            return
        try:
            inp = self.query_one("#filter-input", Input)
        except Exception:
            return
        inp.display = True
        inp.can_focus = True
        self._loading_filter = True
        inp.value = self._tab_filters.get(pane, "")
        self._loading_filter = False
        inp.focus()
        inp.cursor_position = len(inp.value)
        self.call_after_refresh(self._anchor_active_table_top)

    def action_close_filter(self) -> None:
        # Esc = cancel: the needle goes away with the bar.
        pane = self.active_tab
        try:
            inp = self.query_one("#filter-input", Input)
        except Exception:
            return
        if not inp.display and not self._tab_filters.get(pane):
            return
        self._loading_filter = True
        inp.value = ""
        self._loading_filter = False
        inp.display = False
        inp.can_focus = False
        self._tab_filters[pane] = ""
        self._refresh_active_tab()
        self._focus_active_table()
        self.call_after_refresh(self._anchor_active_table_top)

    def on_input_changed(self, event: Input.Changed) -> None:
        # The date modal owns its own inputs; anything else bubbling here is
        # not the filter bar.
        if event.input.id != "filter-input" or self._loading_filter:
            return
        pane = self.active_tab
        if pane not in FILTERABLE_TABS:
            return
        self._tab_filters[pane] = event.value.strip()
        self._refresh_active_tab()
        self.call_after_refresh(self._anchor_active_table_top)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "filter-input":
            return
        # Enter hands the keys back to the table but keeps the bar visible:
        # an applied filter is state the page must keep showing.
        applied = bool(self._tab_filters.get(self.active_tab, "").strip())
        event.input.display = applied
        event.input.can_focus = applied
        self._focus_active_table()

    def action_pop_screen(self) -> None:
        self.pop_screen()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Qoder CN CLI usage TUI")
    parser.add_argument("--db", default=str(db.DB_PATH))
    args = parser.parse_args(argv)
    try:
        cfg = db.load_config()
    except db.ConfigError as exc:
        print(f"qcut: {exc}", file=sys.stderr)
        return 2
    try:
        TrackerApp(db_path=args.db, cfg=cfg).run()
    except db.SchemaTooNew as exc:
        # Named like an unreadable config file: the app cannot render a schema it
        # does not understand, and a traceback would not say which file to fix.
        print(f"qcut: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
