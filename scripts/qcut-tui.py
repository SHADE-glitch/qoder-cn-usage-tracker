#!/usr/bin/env python3
"""qcut-tui — the eight-tab Textual UI for the Qoder CN usage tracker.

    python3 scripts/qcut-tui.py

The app is a *reader* of the index built by ``qcut sync`` — except that it also
writes: ``on_mount`` runs ``db.migrate()`` and the auto-sync worker runs the same
indexer the CLI does. Anything that assumes the TUI is read-only is wrong.

Tabs: Dashboard · Tools · Skills · Agents · Plugins · MCP · Tokens · Usage.
Each tab keeps its own window; the Plugins tab has none (an install fact is
always all-time). The window comes from the shared top bar: a range ``Select``
with a ``Custom…`` entry, beside a 📅 control that opens the date-range picker.
Every number is an integer, a formatted value, ``-`` (no measurement contributed
to this window) or ``—`` (the route reports credits instead of tokens) — never a
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
    """Render one token measure, given whether its route reports tokens at all.

    Three states, and they are not the same statement:

    ``-``   nothing in this window contributed (a NULL sum, or a NULL field)
    ``—``   there *is* a number, and it is a route-level zero — Qoder's own
            routes bill in credits and answer every token field with a literal
            ``0``, so a sum of those zeros is not a total. Naming it ``0``
            would claim "this model used no tokens", which is a different fact.
    ``1,234`` a reported measurement.

    The guard reads the value too, not only the flag: a non-zero is shown
    whatever `reported` claims. The flag is computed elsewhere from the same
    fields it then hides, so a bug there must not be able to swallow a
    measurement — `test_usage.py::TokenReportingTest` holds this.
    """
    if value is None:
        return "-"
    if not reported and not value:
        return "—"
    return fmt_n(value)


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


def provider_cell(value):
    """The Provider column: the channel name for 3P, `-` for Qoder's own route.

    `Route` already says `Built-in`; repeating it under Provider put one fact in
    two adjacent columns. Keeping Provider for the channel name makes the pair
    read as family + name instead of the same value twice.
    """
    if value == "builtin":
        return "-"
    return db.provider_label(value)


# Column sets. The Usage page is cc-switch's request list with Qoder's fields:
# time · model · provider · route · usage total · input · output · API total ·
# cache hit/miss/write · credits · hit rate. Cache *miss* is derived (the input
# that was not read from cache) and the page note says so; there is no duration
# or HTTP-status column because the transcripts carry neither. `Route` is the
# one-word billing family (Built-in / 3P); `Provider` keeps the channel name.
USAGE_COLUMNS = ("Time", "Model", "Provider", "Route", "Credits", "Usage Total",
                 "Input", "Output", "API Total", "Cache hit", "Cache miss",
                 "Cache write", "Hit rate")
# Tokens tab: the per-model rollup, same measure family. `Tok reported` is kept
# where cc-switch's near-always-100% "Coverage" was dropped: on Qoder a route
# that reports credits only is a real, recurring 0, so the column is evidence
# rather than noise. The same fact is what turns that row's token cells into `—`
# — one expression (`db._TOKENS_REPORTED_EXPR`) feeds both, so the yes/no and the
# dash cannot disagree.
TOKENS_COLUMNS = ("Model", "Provider", "Route", "Credits", "Requests",
                  "Usage Total", "Input", "Output", "API Total", "Cache hit",
                  "Cache miss", "Cache write", "Hit rate", "Tok reported")

TOOLS_COLUMNS = ("tool", "calls", "ok", "fail", "pending", "avg", "last used")
SKILLS_COLUMNS = ("skill", "calls", "ok", "plugin", "last used")
AGENTS_COLUMNS = ("agent type", "calls", "ok", "last used")
PLUGINS_COLUMNS = ("plugin", "version", "uses", "skills", "agents", "cmds",
                   "last used")
MCP_COLUMNS = ("server", "tool", "calls", "ok", "avg", "last used")

# A drill-down mirrors the table it came from. `model` keeps the whole measure
# family so the history screen agrees with the Tokens tab it was opened from.
HISTORY_COLUMNS = {
    "tool": ("when", "tool", "category", "status", "duration", "project",
             "session"),
    "skill": ("when", "skill", "status", "duration", "project", "session"),
    "agent": ("when", "agent type", "status", "duration", "project", "session"),
    "model": ("when", "model", "provider", "route", "credits", "input",
              "output", "api total", "cache hit", "cache miss", "cache write",
              "stop"),
    "mcp": ("when", "server", "tool", "status", "duration", "session"),
}
# Twelve columns need ~180 cells: on an 80-wide terminal most of this table used
# to sit past the right edge with no key that would bring it back.
RESPONSE_COLUMNS = ("when", "model", "provider", "route", "credits", "input",
                    "output", "api total", "cache hit", "cache miss",
                    "cache write", "usage total", "hit rate", "message id")
# A plugin's own breakdown: attributed calls and installed resources.
PLUGIN_COLUMNS = ("when", "kind", "target", "marketplace", "session", "project")

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
    "total_tokens": "api total", "usage_total_tokens": "usage total",
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
        elif col in _DETAIL_TOKEN_COLS:
            # The parenthesised raw value stays visible for a reported measure,
            # but a structural zero has no number to show: `—` alone.
            text = f"{fmt_n(value)}  ({value})" if reports_tokens else "—"
        else:
            text = str(value)
        out.append((label, text))
    return out


EMPTY_MSG = {
    "tools": "no tool calls in this window",
    "skills": "no skill invocations in this window",
    "agents": "no subagent calls in this window",
    "plugins": "no plugins installed or used",
    "mcp": "no MCP servers configured or used",
    "tokens": "no model responses in this window",
    "usage": "No requests in this window — widen the range above.",
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
                           padding: 0 1; background: $panel;
                           color: $foreground; }
    TopBar > #date-range:hover { background: $boost; }
    TopBar > #range { width: 30; }
    TopBar > #range SelectCurrent { border: none; padding: 0 1;
                                    background: $panel; }
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
            # A compact 📅 control showing the active tab's window; pressing it
            # opens the date picker (the same thing as choosing "Custom…").
            yield Button("📅", id="date-range")
            yield Select(RANGE_OPTIONS, value=self._range_value,
                         allow_blank=False, id="range")

    def set_range_label(self, text: str) -> None:
        """Show the active tab's window on the 📅 control ('' hides it)."""
        try:
            btn = self.query_one("#date-range", Button)
        except Exception:
            return
        btn.label = f"📅 {text}" if text else "📅"
        btn.display = bool(text)
        # A Button does not re-lay-out when its label grows, so the control kept
        # the width it was built with ("📅 Today" = 10 cells) and cut the longer
        # span down to "📅 10-06" until the next full relayout.
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
    """The custom date-range picker: preset chips, two cards, two grids.

    Left column: the shortcut chips, an editable START TIME and END TIME (a
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
    # (chip label, widget id, preset key). "Today" is the human name for the
    # single whole calendar day the model calls "1d"; the rest are terse because
    # all seven chips have to sit on one row inside the width budget below.
    # Every preset the tabs can take is here, so anything reachable from the
    # Select is reachable from the picker too.
    PRESETS = (
        ("Today", "dr-1d", "1d"),
        ("2d", "dr-2d", "2d"),
        ("3d", "dr-3d", "3d"),
        ("7d", "dr-7d", "7d"),
        ("14d", "dr-14d", "14d"),
        ("30d", "dr-30d", "30d"),
        ("All", "dr-all", "all"),
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
    /* Width budget, so the whole picker fits an 80-column terminal:
       1 border + 1 padding + 53 (left column, of which the seven preset chips
       need 41) + 1 margin + 21 (7 day cells of 3, and 6 hour cells of 3) +
       1 padding + 1 border = 79. The day and hour cells are Statics, not
       Buttons: a Button renders a two-digit label as one digit at width 3.
       Measured, not derived: #dr-box.region.right is asserted <= 80 at an
       80-wide terminal. */
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
    it. A rule that covers two of the nine tables is not a rule, so the
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
                 window_label="all time"):
        super().__init__()
        self.kind = kind
        self.entity = name
        self.heading = display or name
        self.start_ts = start_ts
        self.end_ts = end_ts
        self.window_label = window_label
        self._wide_rows = {}
        self._call_ids = []

    @property
    def name_cap(self):
        return self.app.name_cap

    def compose(self) -> ComposeResult:
        yield TopBar(f"{self.kind}: {self.heading}")
        yield Static(id="hist-head")
        yield Static("", id="colnote-hist", classes="colnote")
        yield DataTable(id="hist-table", zebra_stripes=True)
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
                                      self.start_ts, self.end_ts)
        lines = [f"[b]{self.kind}: {self.heading}[/b]  ·  window: {self.window_label}"]
        if summary is not None:
            lines.append("  " + "   ".join(
                f"{key} = {self._summary_value(key, summary[key], summary)}"
                for key in summary.keys()))
        self.query_one("#hist-head", Static).update("\n".join(lines))
        rows = db.q_history(conn, self.kind, self.entity,
                            limit=self.app.cfg["detail_limit"],
                            start_ts=self.start_ts, end_ts=self.end_ts)
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
    def _summary_value(key, value, summary=None):
        if key in ("first_used", "last_used"):
            return ts(value)
        if key in ("credits",):
            if summary is not None and "credits_carried" in summary.keys():
                return fmt_credit_total(value, summary["credits_carried"])
            return fmt_credits(value)
        if key == "avg_ms":
            return ms(value)
        if key == "tokens_reported":
            return fmt_n(value)
        if summary is not None and key in _DETAIL_TOKEN_COLS:
            # A tool/skill summary carries no token fields at all, so the flag
            # is looked up rather than assumed — and a summary that does not
            # report one is shown as a plain number, never as `—`.
            keys = summary.keys()
            return fmt_tok(value, bool(summary["tokens_reported"])
                           if "tokens_reported" in keys else True)
        if isinstance(value, int):
            return fmt_n(value)
        return value

    @staticmethod
    def _model_row(r):
        rep = db.row_reports_tokens(r)
        return [ts(r["ts"]), r["model"], provider_cell(r["provider"]),
                db.route_label(r["provider"]), fmt_credits(r["credits"]),
                fmt_tok(r["input_tokens"], rep), fmt_tok(r["output_tokens"], rep),
                fmt_tok(r["total_tokens"], rep), fmt_tok(r["cache_read_tokens"], rep),
                fmt_tok(r["cache_miss_tokens"], rep),
                fmt_tok(r["cache_creation_tokens"], rep),
                r["stop_reason"] or "-"]

    def on_data_table_row_selected(self, event) -> None:
        if event.data_table.id != "hist-table":
            return
        try:
            index = self.query_one("#hist-table", DataTable).cursor_row
        except Exception:
            return
        if 0 <= index < len(self._call_ids):
            self.app.push_screen(
                CallScreen(self.kind, self._call_ids[index], self.heading))


class ModelResponsesScreen(WideTableMixin, Screen):
    """Every model response of one model, inside the tab's window."""

    BINDINGS = [Binding("escape,q", "app.pop_screen", "Back")]
    WIDE_TABLES = {"resp-table": (RESPONSE_COLUMNS, "colnote-resp", 1)}

    def __init__(self, model, start_ts=None, end_ts=None,
                 window_label="all time"):
        super().__init__()
        self.model = model
        self.start_ts = start_ts
        self.end_ts = end_ts
        self.window_label = window_label
        self._wide_rows = {}

    @property
    def name_cap(self):
        return self.app.name_cap

    def compose(self) -> ComposeResult:
        yield TopBar(f"model: {self.model}")
        yield Static(id="resp-head")
        yield Static("", id="colnote-resp", classes="colnote")
        yield DataTable(id="resp-table", zebra_stripes=True)
        yield Footer()

    def on_mount(self) -> None:
        self.title = f"model: {self.model}"
        for tid, (cols, _note, _chrome) in self.WIDE_TABLES.items():
            table = self.query_one(f"#{tid}", DataTable)
            table.cursor_type = "row"
            table.add_columns(*cols)
        conn = self.app.conn
        summary = db.q_entity_summary(conn, "model", self.model, self.start_ts,
                                      self.end_ts)
        lines = [f"[b]model: {self.model}[/b]  ·  window: {self.window_label}"]
        if summary is not None:
            lines.append("  " + "   ".join(
                f"{key} = {HistoryScreen._summary_value(key, summary[key], summary)}"
                for key in summary.keys()))
        self.query_one("#resp-head", Static).update("\n".join(lines))
        rows = db.q_history(conn, "model", self.model,
                            limit=self.app.cfg["detail_limit"],
                            start_ts=self.start_ts, end_ts=self.end_ts)
        out = []
        for r in rows:
            rep = db.row_reports_tokens(r)
            out.append([ts(r["ts"]), r["model"],
                        provider_cell(r["provider"]),
                        db.route_label(r["provider"]),
                        fmt_credits(r["credits"]),
                        fmt_tok(r["input_tokens"], rep),
                        fmt_tok(r["output_tokens"], rep),
                        fmt_tok(r["total_tokens"], rep),
                        fmt_tok(r["cache_read_tokens"], rep),
                        fmt_tok(r["cache_miss_tokens"], rep),
                        fmt_tok(r["cache_creation_tokens"], rep),
                        fmt_tok(r["usage_total_tokens"], rep),
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
        yield TopBar(f"plugin: {self.plugin}")
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
        data = [[ts(r["ts"]), r["kind"], r["target"],
                 r["marketplace"] or "-", r["session_id"] or "-",
                 r["project"] or "-"] for r in rows]
        # An installed-but-never-called resource still belongs on the plugin's
        # page: the list is the union, keyed by name, exactly like the tabs.
        data += [["-", r["kind"], f"{r['name']} (installed)", "-", "-", "-"]
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


# --- the app ---------------------------------------------------------------


class TrackerApp(WideTableMixin, App):
    """The eight-tab Qoder CN usage UI."""

    CSS = """
    #topbar { dock: top; }
    TabbedContent { height: 1fr; }
    .colnote { padding: 0 1; color: $text-muted; height: auto; display: none; }
    DataTable { height: 1fr; }
    /* NOT docked: the Footer docks itself to the bottom, and a second bottom
       dock lands on the same row and is painted over — which hid the unclaimed
       count, the schema version and the database path behind the footer. In
       normal flow the bar takes the row directly above the footer. */
    #status { height: 1; color: $text-muted; padding: 0 1; }
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
    #panel-runtime, #panel-dash-cache, #panel-dash-tools,
    #panel-dash-activity { margin: 0; }
    .panel-title { text-style: bold; color: $accent; }
    #usage-status, #usage-window { padding: 0; }
    #usage-note, #dash-note, #hist-head, #resp-head, #plugin-head {
        padding: 0 1; color: $text-muted; height: auto; }
    #dash-activity { color: $accent; }
    #dash-activity-axis { color: $text-muted; }
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
    /* The drill-down detail: fixed label column, wrapping value column. */
    #call-body { height: 1fr; overflow-y: auto; padding: 0 1; }
    .call-label { width: 18; color: $text-muted; }
    .call-value { width: 1fr; }
    #call-title { padding: 1 0 0 0; }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh", "Sync now"),
        Binding("s", "toggle_auto_sync", "Auto sync"),
        Binding("tab", "next_tab", "Next tab", priority=True),
        Binding("shift+tab", "previous_tab", "Prev tab", priority=True),
    ]
    TITLE = "qcut — Qoder CN usage"
    # No built-in Header means no ctrl+p palette; disable it outright too.
    ENABLE_COMMAND_PALETTE = False

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
        self._status_note = ""
        self._usage_ready = False
        self._auto_sync = True
        self._sync_running = False
        self._sync_pending = False
        self._last_sync = None
        self._last_refresh = None
        self.sync_mod = None

    @property
    def name_cap(self):
        return self.cfg["name_cap"]

    # --- layout ---

    def compose(self) -> ComposeResult:
        yield TopBar(self.TITLE, with_range=True,
                     range_value=self.ranges[self.INITIAL_TAB], id="topbar")
        with TabbedContent(initial=self.INITIAL_TAB):
            for tid, title in TABS:
                with TabPane(title, id=tid):
                    if tid == "dashboard":
                        yield Vertical(
                            Horizontal(
                                Vertical(
                                    Static("Usage", classes="panel-title"),
                                    Static(id="dash-kpi-tools"),
                                    Static(id="dash-kpi-skills"),
                                    Static(id="dash-kpi-agents"),
                                    Static(id="dash-kpi-mcp"),
                                    Static(id="dash-kpi-plugins"),
                                    classes="usage-panel", id="panel-dash-kpi",
                                ),
                                Vertical(
                                    Static("Tokens", classes="panel-title"),
                                    Static(id="dash-tok-requests"),
                                    Static(id="dash-tok-input"),
                                    Static(id="dash-tok-output"),
                                    Static(id="dash-tok-api"),
                                    Static(id="dash-tok-usage"),
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
                                    Static("Runtime", classes="panel-title"),
                                    Static(id="dash-rt-completed"),
                                    Static(id="dash-rt-incomplete"),
                                    Static(id="dash-rt-avg"),
                                    Static(id="dash-rt-sessions"),
                                    Static(id="dash-rt-projects"),
                                    classes="usage-panel", id="panel-dash-runtime",
                                ),
                                Vertical(
                                    Static("Calls per day", classes="panel-title"),
                                    Static(id="dash-activity"),
                                    Static(id="dash-activity-axis"),
                                    classes="usage-panel",
                                    id="panel-dash-activity",
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
                                    Static(id="sum-missing"),
                                    classes="usage-panel", id="panel-window",
                                ),
                                Vertical(
                                    Static("Model tokens", classes="panel-title"),
                                    Static(id="sum-usage-total"),
                                    Static(id="sum-input"),
                                    Static(id="sum-output"),
                                    Static(id="sum-total"),
                                    Static(id="sum-credits"),
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
                                    Static("Runtime", classes="panel-title"),
                                    Static(id="usage-status"),
                                    classes="usage-panel", id="panel-runtime",
                                ),
                                id="usage-panels", classes="summary-panels",
                            ),
                            Static(id="usage-note"),
                            Static("", id="colnote-usage", classes="colnote"),
                            DataTable(id="usage-table", zebra_stripes=True),
                            id="usage-box",
                        )
                    else:
                        yield Static("", id=f"colnote-{tid}", classes="colnote")
                        yield DataTable(id=f"{tid}-table", zebra_stripes=True)
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
        # Auto sync (incremental, ~30s) + UI refresh (~5s). Both are timers, not
        # subprocesses; the sync runs in a worker thread (see _do_sync).
        self.set_interval(self.cfg["sync_secs"], self._auto_sync_tick)
        self.set_interval(self.cfg["refresh_secs"], self._auto_refresh_tick)
        self.refresh_data()

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
        self._set_date_label(db.window_label(value))

    def _set_date_label(self, text: str) -> None:
        try:
            self.query_one("#topbar", TopBar).set_range_label(text)
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
        # Order matters on an 80-column terminal: the line is clipped from the
        # right, so the drift signal and the counts come first and the database
        # path — the least time-sensitive fact on the bar — goes last.
        status = (f"unclaimed {fmt_n(counts['unparsed_records'])} · "
                  f"{count_n(counts['tool_calls'], 'tool call')} · "
                  f"{count_n(counts['skills'], 'skill')} · "
                  f"{count_n(counts['agents'], 'agent')} · "
                  f"{count_n(counts['plugins_used'], 'plugin')} · "
                  f"{counts['mcp']:,} mcp · "
                  f"{count_n(counts['model_responses'], 'model response')} · "
                  f"last {ts(counts['last_ts'])}")
        if not self._auto_sync:
            status += " · auto-sync OFF"
        status += f" · schema v{db.stored_version(self.conn)}"
        if self._schema_note:
            status += f" · ! {self._schema_note}"
        status += f" · db {self.db_path}"
        if self._status_note:
            # Every number above is the last known one, not the current one.
            status += f" · ! {self._status_note}"
        return status

    def _report_query_error(self, exc: sqlite3.Error) -> None:
        """Name a failed re-query instead of leaving the old page on screen.

        A tab that could not be refreshed keeps its previous rows, which look
        exactly like usage the user still has; a locked or damaged file has to
        say so in the one place the user reads.
        """
        self._status_note = f"{type(exc).__name__}: {exc}"
        self._set_status(self._status_line())

    # --- data fill / refresh -----------------------------------------------

    def refresh_data(self, conn=None) -> None:
        """Re-fill every tab that is currently populated.

        A full refresh (F5-style, after a sync that indexed something) touches
        each tab, because a new row can land anywhere; the 5s timer only
        re-queries the visible one.
        """
        own = conn is None
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
        act = db.q_usage_activity(conn, start, end)
        models = db.q_usage_model_stats(conn, start, end)
        tools = db.q_tools(conn, limit=self.cfg["top_n"], start_ts=start, end_ts=end)
        daily = db.q_usage_daily(conn, start, end)

        self._put_panel("#dash-kpi-tools", "Tool calls", f"{kpi['tool_calls']:,}")
        self._put_panel("#dash-kpi-skills", "Skills", f"{kpi['skills']:,}")
        self._put_panel("#dash-kpi-agents", "Agents", f"{kpi['agents']:,}")
        self._put_panel("#dash-kpi-mcp", "MCP", f"{kpi['mcp']:,}")
        self._put_panel("#dash-kpi-plugins", "Plugins", f"{kpi['plugins']:,}")

        # A window of Qoder-hosted responses sums to zero in every token column
        # while billing real credits; `tok_rep` is the single flag that turns
        # those structural zeros into `—` here and on the Usage page, so the two
        # panels can never disagree about the same window.
        tok_rep = bool(summary["tokens_reported"])
        self._put_panel("#dash-tok-requests", "Requests",
                        f"{summary['requests'] or 0:,}")
        self._put_panel("#dash-tok-input", "Input",
                        fmt_tok(summary["input_tokens"], tok_rep))
        self._put_panel("#dash-tok-output", "Output",
                        fmt_tok(summary["output_tokens"], tok_rep))
        self._put_panel("#dash-tok-api", "API Total",
                        fmt_tok(summary["total_tokens"], tok_rep))
        self._put_panel("#dash-tok-usage", "Usage Total",
                        fmt_tok(summary["usage_total_tokens"], tok_rep))
        self._put_panel("#dash-tok-hit-rate", "Hit rate",
                        self._hit_rate(summary["cache_hit"], summary["cache_miss"],
                                       summary["cache_write"]))

        self._put_panel("#dash-cache-hit", "Cache hit",
                        fmt_tok(summary["cache_hit"], tok_rep))
        self._put_panel("#dash-cache-miss", "Cache miss",
                        fmt_tok(summary["cache_miss"], tok_rep))
        self._put_panel("#dash-cache-write", "Cache write",
                        fmt_tok(summary["cache_write"], tok_rep))

        self._put_panel("#dash-rt-completed", "Completed",
                        f"{act['completed'] or 0:,}")
        self._put_panel("#dash-rt-incomplete", "Incomplete",
                        f"{act['incomplete'] or 0:,}")
        self._put_panel("#dash-rt-avg", "Avg tool time", ms(act["avg_ms"]))
        self._put_panel("#dash-rt-sessions", "Sessions",
                        f"{act['sessions'] or 0:,}")
        self._put_panel("#dash-rt-projects", "Projects",
                        f"{act['projects'] or 0:,}")

        if not models:
            self.query_one("#dash-models", Static).update(
                "nothing in this window")
        else:
            top = models[:self.cfg["top_n"]]
            def name(r):
                tag = " · 3P" if r["provider"] not in (None, "builtin") else ""
                return clip(r["model"] + tag, 24)
            self.query_one("#dash-models", Static).update("\n".join(
                f"{i}. {name(r):<24}"
                f"{fmt_credit_total(r['credits'], r['credits_carried']):>10}"
                f"  {count_n(r['requests'], 'req'):>10}"
                for i, r in enumerate(top, 1)))

        # The name-keyed list keeps 0-call tools under a window; drop them.
        live = [r for r in tools if r["calls"]][:self.cfg["top_n"]]
        if not live:
            self.query_one("#dash-tools", Static).update("nothing in this window")
        else:
            self.query_one("#dash-tools", Static).update("\n".join(
                f"{i}. {clip(r['tool_name'], 20):<20}"
                f"{count_n(r['calls'], 'call'):>12} · {ms(r['avg_ms'])}"
                for i, r in enumerate(live, 1)))

        self._render_dashboard_activity(daily, start, end)
        if not any(kpi.values()) and not (summary["requests"] or 0):
            self.query_one("#dash-note", Static).update(
                "No data in this window — widen the range above.")
        else:
            self.query_one("#dash-note", Static).update(
                "Route: Built-in = Qoder's own, 3P = a BYOK/third-party channel "
                "(Provider names it). Credits are Qoder's own metric; a BYOK route "
                "reports tokens instead, never both. `—` = this window answers in "
                "credits, not tokens; `-` = nothing contributed to it at all.")

    # Block characters for the per-day sparkline (index 0 = no activity).
    _SPARK = " ▁▂▃▄▅▆▇█"

    def _sparkline(self, values) -> str:
        peak = max(values) if values else 0
        if peak <= 0:
            # A quiet window still draws something: an empty string left a blank
            # region on the Dashboard, which reads as "widget broken" rather than
            # "no calls". A dotted baseline says the same thing as the zero-height
            # bars it replaces, without inventing a bar.
            return "·" * len(values)
        out = []
        for v in values:
            level = 0 if v <= 0 else min(8, max(1, round(v / peak * 8)))
            out.append(self._SPARK[level])
        return "".join(out)

    def _render_dashboard_activity(self, daily, start, end) -> None:
        """A one-line sparkline of requests per local day across the window.

        ``daily`` only carries days that had calls, so the gaps are filled from
        the window bounds. For the all-time range the series starts at the first
        day with data (never from the epoch).
        """
        counts = {r["day"]: r["calls"] for r in daily}
        now = self._now_ms() if end is None else end
        end_day = datetime.fromtimestamp(now / 1000).date()
        if start is not None:
            start_day = datetime.fromtimestamp(start / 1000).date()
        elif daily:
            start_day = datetime.strptime(daily[0]["day"], "%Y-%m-%d").date()
        else:
            start_day = end_day
        days, d = [], start_day
        while d <= end_day:
            days.append(d)
            d += timedelta(days=1)
        series = [counts.get(day.isoformat(), 0) for day in days]
        self.query_one("#dash-activity", Static).update(self._sparkline(series))
        # The day list is never empty (a window always contains at least today),
        # so the branch that matters is "no calls in it", not "no days".
        if any(series):
            self.query_one("#dash-activity-axis", Static).update(
                f"{days[0]:%m-%d} → {days[-1]:%m-%d} · peak {max(series):,}/day")
        else:
            self.query_one("#dash-activity-axis", Static).update(
                f"{days[0]:%m-%d} → {days[-1]:%m-%d} · no activity")

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

    def _render_usage_status(self) -> None:
        self.query_one("#usage-status", Static).update(
            f"Auto sync: {'ON' if self._auto_sync else 'OFF'}\n"
            f"Last sync: {local_time(self._last_sync)}\n"
            f"Last refresh: {local_time(self._last_refresh)}")

    def _render_summary_panels(self, summary: dict) -> None:
        req = summary["requests"] or 0
        tok_rep = bool(summary["tokens_reported"])
        self._put_panel("#sum-requests", "Requests", f"{req:,}")
        # Usage Total = Input + Output + cache hit; a display-only re-add, shown
        # beside the API total and never instead of it.
        self._put_panel("#sum-usage-total", "Usage Total",
                        fmt_tok(summary["usage_total_tokens"], tok_rep))
        self._put_panel("#sum-input", "Input",
                        fmt_tok(summary["input_tokens"], tok_rep))
        self._put_panel("#sum-output", "Output",
                        fmt_tok(summary["output_tokens"], tok_rep))
        self._put_panel("#sum-total", "API Total",
                        fmt_tok(summary["total_tokens"], tok_rep))
        self._put_panel("#sum-credits", "Credits",
                        fmt_credit_total(summary["credits"],
                                         summary["credits_carried"]))
        self._put_panel("#sum-cache-hit", "Cache hit",
                        fmt_tok(summary["cache_hit"], tok_rep))
        self._put_panel("#sum-cache-miss", "Cache miss",
                        fmt_tok(summary["cache_miss"], tok_rep))
        self._put_panel("#sum-cache-write", "Cache write",
                        fmt_tok(summary["cache_write"], tok_rep))
        self._put_panel("#sum-hit-rate", "Hit rate",
                        self._hit_rate(summary["cache_hit"], summary["cache_miss"],
                                       summary["cache_write"]))
        missing = []
        if summary["missing_input"]:
            missing.append(f"{summary['missing_input']:,} missing input")
        if summary["missing_output"]:
            missing.append(f"{summary['missing_output']:,} missing output")
        if req == 0:
            note = "no data in window"
        elif missing:
            note = ", ".join(missing)
        else:
            note = "complete"
        # The panel reports completeness either way; naming it "Incomplete" made
        # the good case read "Incomplete: complete".
        self._put_panel("#sum-missing", "Completeness", note)

    def _fill_usage(self, conn, start=None, end=None) -> None:
        if not self._usage_ready:
            return
        if start is None and end is None:
            start, end = self._bounds_for("usage")
        self._last_refresh = self._now_ms()
        self._render_usage_window()
        self._render_usage_status()
        self._render_summary_panels(db.q_usage_summary(conn, start, end))
        rows = db.q_usage_request_logs(conn, start, end,
                                       limit=self.cfg["log_limit"])
        self.query_one("#usage-note", Static).update(
            "Newest first · Route: Built-in (Qoder) or 3P (BYOK) · Provider "
            "names the channel · Usage Total re-adds cache hit (it is already "
            "inside Input) · cache miss is derived · `-` = nothing in this window, `—` "
            "= a route that bills in credits and answers every token field with "
            "0 (a BYOK route is the reverse: real tokens, no credits field). "
            "No duration or status column: the transcripts carry neither."
            if rows else "")
        out = []
        for r in rows:
            # Per row, not per window: one list can hold both route kinds, and a
            # BYOK response must keep its real tokens while the hosted row above
            # it shows `—`.
            rep = db.row_reports_tokens(r)
            out.append([
                ts(r["ts"]), r["model"] or "-", provider_cell(r["provider"]),
                db.route_label(r["provider"]), fmt_credits(r["credits"]),
                fmt_tok(r["usage_total_tokens"], rep), fmt_tok(r["input_tokens"], rep),
                fmt_tok(r["output_tokens"], rep), fmt_tok(r["total_tokens"], rep),
                fmt_tok(r["cache_read_tokens"], rep),
                fmt_tok(r["cache_miss_tokens"], rep),
                fmt_tok(r["cache_creation_tokens"], rep),
                self._hit_rate(r["cache_read_tokens"], r["cache_miss_tokens"],
                               r["cache_creation_tokens"]),
            ])
        # Keyed by message id, so selecting a request opens its own detail.
        self._fill_wide("usage-table", out, EMPTY_MSG["usage"],
                        key_index=0, key_kind="model",
                        key_rows=[[r["message_id"]] for r in rows])

    # --- the entity tabs ---------------------------------------------------

    def _fill_tools(self, conn, start=None, end=None) -> None:
        self._fill_wide(
            "tools-table",
            [[r["tool_name"], fmt_n(r["calls"]), fmt_n(r["completed"]),
              fmt_n(r["failed"]), fmt_n(r["pending"]), ms(r["avg_ms"]),
              ts(r["last_used"])]
             for r in db.q_tools(conn, start_ts=start, end_ts=end)],
            EMPTY_MSG["tools"], key_index=0, key_kind="tool")

    def _fill_skills(self, conn, start=None, end=None) -> None:
        self._fill_wide(
            "skills-table",
            [[r["skill"], fmt_n(r["calls"]), fmt_n(r["completed"]),
              r["plugin"] or "-", ts(r["last_used"])]
             for r in db.q_skills(conn, start_ts=start, end_ts=end)],
            EMPTY_MSG["skills"], key_index=0, key_kind="skill")

    def _fill_agents(self, conn, start=None, end=None) -> None:
        self._fill_wide(
            "agents-table",
            [[r["agent_type"], fmt_n(r["calls"]), fmt_n(r["completed"]),
              ts(r["last_used"])]
             for r in db.q_agents(conn, start_ts=start, end_ts=end)],
            EMPTY_MSG["agents"], key_index=0, key_kind="agent")

    def _fill_plugins(self, conn, start=None, end=None) -> None:
        self._fill_wide(
            "plugins-table",
            [[r["plugin"], r["version"] or "-", fmt_n(r["uses"]),
              fmt_n(r["skills"]), fmt_n(r["agents"]), fmt_n(r["commands"]),
              ts(r["last_used"])]
             for r in db.q_plugins(conn)],
            EMPTY_MSG["plugins"], key_index=0, key_kind="plugin")

    def _fill_mcp(self, conn, start=None, end=None) -> None:
        self._fill_wide(
            "mcp-table",
            [[r["server"] or "—", r["tool"] or "—", fmt_n(r["calls"]),
              fmt_n(r["completed"]), ms(r["avg_ms"]), ts(r["last_used"])]
             for r in db.q_mcp(conn, start_ts=start, end_ts=end)],
            EMPTY_MSG["mcp"], key_index=(0, 1), key_kind="mcp")

    def _fill_tokens(self, conn, start=None, end=None) -> None:
        rows = []
        for r in db.q_models(conn, start_ts=start, end_ts=end):
            # `tokens_reported` is the same count the last column shows as
            # yes/no, and it is the same expression the Usage page and the
            # Dashboard use — so a `—` here and a `no` there are one fact.
            rep = bool(r["tokens_reported"])
            rows.append([
                r["model"], provider_cell(r["provider"]),
                db.route_label(r["provider"]),
                fmt_credit_total(r["credits"], r["credits_carried"]),
                fmt_n(r["requests"]), fmt_tok(r["usage_total_tokens"], rep),
                fmt_tok(r["input_tokens"], rep), fmt_tok(r["output_tokens"], rep),
                fmt_tok(r["total_tokens"], rep), fmt_tok(r["cache_read_tokens"], rep),
                fmt_tok(r["cache_miss_tokens"], rep),
                fmt_tok(r["cache_creation_tokens"], rep),
                self._hit_rate(r["cache_read_tokens"], r["cache_miss_tokens"],
                               r["cache_creation_tokens"]),
                "yes" if rep else "no"])
        self._fill_wide("tokens-table", rows, EMPTY_MSG["tokens"],
                        key_index=0, key_kind="model")

    # --- refresh plumbing --------------------------------------------------

    def _refresh_active_tab(self, conn=None) -> None:
        """Refresh only the visible tab (5s timer / tab activation).

        Hidden tabs are left alone; they are populated when the user opens them
        (see ``on_tabbed_content_tab_activated``).
        """
        if not self._usage_ready:
            return
        pane = self.active_tab
        own = conn is None
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

    def _auto_refresh_tick(self) -> None:
        # Only the visible tab is re-queried; hidden tabs refresh when the user
        # opens them. The is_running guard matters because a timer can fire once
        # more while the app is shutting down (e.g. run_test's teardown), after
        # the widgets are gone — query_one then raises NoMatches from inside the
        # timer and fails the test run.
        if not self.is_running:
            return
        self._refresh_active_tab()

    def _auto_sync_tick(self) -> None:
        # The same shutdown guard as _auto_refresh_tick: a timer can fire once
        # more while the app is closing.
        if not self.is_running:
            return
        if self._auto_sync:
            self._request_sync()

    def _request_sync(self) -> None:
        # Guard against concurrent syncs: if one is running, mark a pending
        # request and return; the running worker retries on completion.
        if self._sync_running:
            self._sync_pending = True
            return
        self._sync_running = True
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
        if ok:
            self._last_sync = self._now_ms()
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
            self._status_note = "Sync failed: " + (err or "unknown error")[:200]
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

    # --- events ------------------------------------------------------------

    def on_tabbed_content_tab_activated(self, event) -> None:
        self._refresh_active_tab()

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
        if kind == "mcp":
            # An MCP row is keyed by (server, tool). q_history maps "mcp" to the
            # mcp_usage.tool column, so query by the TOOL and show "server · tool"
            # as the title. A server-less row has no entity to open.
            server, _, tool = rest.partition("\t")
            if not tool or tool == "-":
                return
            self.push_screen(HistoryScreen(
                "mcp", tool, display=f"{server} · {tool}", start_ts=start,
                end_ts=end, window_label=label))
        elif kind == "plugin":
            self.push_screen(PluginScreen(rest))
        elif kind == "model":
            self.push_screen(ModelResponsesScreen(rest, start, end, label))
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

    def action_refresh(self) -> None:
        # R = incremental sync + reload DB + refresh the UI (not just a re-render
        # of already-loaded data).
        self._set_status("Syncing…")
        self._request_sync()

    def action_toggle_auto_sync(self) -> None:
        # S = toggle the automatic sync switch. Distinct from R: it does not
        # trigger a sync itself.
        self._auto_sync = not self._auto_sync
        # The line itself carries the state ("· auto-sync OFF"); a bespoke
        # message here used to wipe the counts off the bar until an unrelated
        # refresh happened to put them back.
        self._set_status(self._status_line())
        self.notify(f"Auto sync: {'ON' if self._auto_sync else 'OFF'}")
        if self._usage_ready:
            try:
                self._render_usage_status()
            except Exception:
                pass

    def action_pop_screen(self) -> None:
        self.pop_screen()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Qoder CN usage TUI")
    parser.add_argument("--db", default=str(db.DB_PATH))
    args = parser.parse_args(argv)
    try:
        cfg = db.load_config()
    except db.ConfigError as exc:
        print(f"qcut: {exc}", file=sys.stderr)
        return 2
    TrackerApp(db_path=args.db, cfg=cfg).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
