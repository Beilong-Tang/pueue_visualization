"""HTML building blocks. Every dynamic value goes through esc()."""

from contextvars import ContextVar
from datetime import datetime, timedelta, tzinfo
from html import escape
from typing import Callable
from urllib.parse import urlencode
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .pueue import Task, now as _now


def esc(value) -> str:
    return escape("" if value is None else str(value), quote=True)


# ---------------------------------------------------------------- formatting

# The viewer's time zone for the current request (set by server.py from the
# browser's pueueviz_tz cookie). None means the server's own zone.
DISPLAY_TZ: ContextVar[tzinfo | None] = ContextVar("DISPLAY_TZ", default=None)


def parse_tz(name: str | None) -> ZoneInfo | None:
    """'America/New_York' -> ZoneInfo, or None if missing/unknown."""
    if not name:
        return None
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return None


def to_display(dt: datetime) -> datetime:
    """Convert a time to the viewer's zone (naive times are taken as server-local)."""
    aware = dt.astimezone() if dt.tzinfo is None else dt
    return aware.astimezone(DISPLAY_TZ.get())


def tz_label() -> str:
    """Short name of the zone times are shown in, e.g. 'EST'."""
    return to_display(_now()).strftime("%Z")


def fmt_dt(dt: datetime | None, now: datetime | None = None) -> str:
    """'10-29 14:05' in the viewer's zone; tooltip has the full time, server time and relative time."""
    if dt is None:
        return '<span class="muted">–</span>'
    now = now or _now()
    local, server = to_display(dt), dt.astimezone()
    short = local.strftime("%m-%d %H:%M") if local.year == to_display(now).year else local.strftime("%Y-%m-%d")
    title = f"{local:%Y-%m-%d %H:%M:%S %Z} · server {server:%m-%d %H:%M %Z} · {fmt_rel(dt, now)}"
    return f'<time datetime="{server.isoformat()}" title="{esc(title)}">{short}</time>'


def fmt_rel(dt: datetime, now: datetime | None = None) -> str:
    delta = (now or _now()) - dt
    if delta.total_seconds() < 0:
        return "in " + fmt_td(-delta, parts=1)
    return fmt_td(delta, parts=1) + " ago"


def fmt_td(td: timedelta | None, parts: int = 2) -> str:
    """'1d 22h', '1h 37m', '5m 34s', '12s'."""
    if td is None:
        return "–"
    secs = int(td.total_seconds())
    units = [("d", 86400), ("h", 3600), ("m", 60), ("s", 1)]
    out = []
    for name, size in units:
        n, secs = divmod(secs, size)
        if n or out:
            out.append(f"{n}{name}")
    return " ".join(out[:parts]) if out else "0s"


def query_url(path: str, params: dict, **changes) -> str:
    merged = {**params, **changes}
    merged = {k: v for k, v in merged.items() if v not in (None, "")}
    return path + ("?" + urlencode(merged) if merged else "")


# ---------------------------------------------------------------- components

MUTED_DASH = '<span class="muted">–</span>'


def badge(task: Task) -> str:
    title = task.spawn_error or ""
    return f'<span class="badge s-{task.state_group}" title="{esc(title)}">{esc(task.state_label)}</span>'


def task_link(task: Task) -> str:
    return f'<a class="mono" href="/task/{task.task_id}">{task.task_id}</a>'


def command_cell(task: Task, width: str = "") -> str:
    return f'<span class="cmd mono{width}" title="{esc(task.command)}">{esc(task.command)}</span>'


def label_cell(task: Task) -> str:
    return f'<span class="name" title="{esc(task.label)}">{esc(task.label)}</span>' if task.label else MUTED_DASH


def group_cell(task: Task) -> str:
    return f'<a class="grp" href="{esc(query_url("/", {"group": task.group}))}">{esc(task.group)}</a>'


def deps_cell(task: Task) -> str:
    if not task.dependencies:
        return MUTED_DASH
    return " ".join(f'<a class="mono" href="/task/{d}">#{d}</a>' for d in task.dependencies)


Column = tuple[str, str, Callable[[Task], str]]  # (sort key or "", label, cell renderer)


def table(columns: list[Column], tasks: list[Task], path: str, params: dict) -> str:
    if not tasks:
        return '<p class="empty">No tasks match.</p>'
    sort, order = params.get("sort", ""), params.get("order", "desc")
    head = []
    for key, label, _ in columns:
        if not key:
            head.append(f"<th>{esc(label)}</th>")
            continue
        new_order = "asc" if key == sort and order == "desc" else "desc"
        arrow = (" ▼" if order == "desc" else " ▲") if key == sort else ""
        url = query_url(path, params, sort=key, order=new_order, page=None)
        head.append(f'<th><a href="{esc(url)}">{esc(label)}{arrow}</a></th>')
    rows = []
    for task in tasks:
        cells = "".join(f"<td>{fn(task)}</td>" for _, _, fn in columns)
        rows.append(f'<tr class="row-{task.state_group}">{cells}</tr>')
    return (f'<div class="table-wrap"><table><thead><tr>{"".join(head)}</tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div>')


def pagination(path: str, params: dict, page: int, n_pages: int, total: int, page_size: int) -> str:
    first = (page - 1) * page_size + 1 if total else 0
    last = min(page * page_size, total)
    info = f'<span class="muted">{first}–{last} of {total}</span>'
    if n_pages <= 1:
        return f'<nav class="pager">{info}</nav>'

    def link(p: int, label: str, disabled: bool = False, current: bool = False) -> str:
        if current:
            return f'<span class="current">{label}</span>'
        if disabled:
            return f'<span class="disabled">{label}</span>'
        return f'<a href="{esc(query_url(path, params, page=p))}">{label}</a>'

    # 1 … 4 5 [6] 7 8 … 20
    shown = sorted({1, n_pages, *range(max(1, page - 2), min(n_pages, page + 2) + 1)})
    nums, prev = [], 0
    for p in shown:
        if p - prev > 1:
            nums.append('<span class="gap">…</span>')
        nums.append(link(p, str(p), current=p == page))
        prev = p
    return (f'<nav class="pager">{link(1, "« First", page == 1)}{link(page - 1, "‹ Prev", page == 1)}'
            f'{"".join(nums)}{link(page + 1, "Next ›", page == n_pages)}'
            f'{link(n_pages, "Last »", page == n_pages)}{info}</nav>')


def filter_form(path: str, params: dict, states: list[str], groups: list[str]) -> str:
    def options(values, selected, blank):
        opts = [f'<option value="">{esc(blank)}</option>']
        for v in values:
            sel = " selected" if v == selected else ""
            opts.append(f'<option value="{esc(v)}"{sel}>{esc(v)}</option>')
        return "".join(opts)

    keep = "".join(f'<input type="hidden" name="{esc(k)}" value="{esc(params[k])}">'
                   for k in ("sort", "order") if params.get(k))
    state_sel = (f'<select name="state" aria-label="State">{options(states, params.get("state", ""), "All states")}</select>'
                 if states else "")
    group_sel = (f'<select name="group" aria-label="Group">{options(groups, params.get("group", ""), "All groups")}</select>'
                 if len(groups) > 1 or params.get("group") else "")
    # app.js applies changes immediately; the button is only for browsers without JS
    return f'''<form class="filters" id="filters" method="get" action="{esc(path)}">
  <input type="search" name="q" value="{esc(params.get("q", ""))}" placeholder="Search command, label or ID" aria-label="Search">
  {state_sel}
  {group_sel}
  <label>Added from <input type="date" name="from" value="{esc(params.get("from", ""))}"></label>
  <label>to <input type="date" name="to" value="{esc(params.get("to", ""))}"></label>
  {keep}
  <button type="submit" class="nojs">Filter</button>
  <a class="reset" href="{esc(path)}">Reset</a>
</form>'''


NAV = [("/", "Overview", "all"), ("/running", "Running", "running"),
       ("/queue", "Queue", "pending"), ("/finished", "Finished", "finished")]


def layout(title: str, active: str, body: str, counts: dict, host: str,
           fetched_at: float | None = None, error: str | None = None, refresh: int = 0) -> str:
    nav = []
    for href, label, key in NAV:
        cls = ' class="active"' if href == active else ""
        n = counts.get(key)
        count = f' <span class="count">{n}</span>' if n is not None else ""
        nav.append(f'<a href="{href}"{cls}>{esc(label)}{count}</a>')
    banner = '<div id="banner" data-swap>'
    if error:
        when = to_display(datetime.fromtimestamp(fetched_at)).strftime("%H:%M:%S") if fetched_at else "?"
        banner += f'<div class="banner">pueue did not answer; showing data from {when}. <small>{esc(error)}</small></div>'
    banner += "</div>"
    updated = (f'<span id="updated" data-swap data-ts="{fetched_at:.0f}">updated '
               f'{to_display(datetime.fromtimestamp(fetched_at)):%H:%M:%S}</span>') if fetched_at else ""
    zone_name = getattr(DISPLAY_TZ.get(), "key", "")
    tz_note = (f'<span class="tz" title="Times are shown in {esc(zone_name or "the server")} time. '
               f'Hover a time to also see the server time.">times in {esc(tz_label())}</span>')
    toggle = (f'<label class="auto"><input type="checkbox" id="autorefresh" data-seconds="{refresh}"> '
              f'auto-refresh {refresh}s</label>') if refresh else ""
    return f'''<!doctype html>
<html lang="en" data-tz="{esc(zone_name)}"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)} · Pueue Tasks</title>
<link rel="stylesheet" href="/static/style.css">
<link rel="icon" href="/static/favicon.svg" type="image/svg+xml">
<script src="/static/app.js" defer></script>
</head><body>
<div id="loadbar" aria-hidden="true"></div>
<header><div class="brand">Pueue Tasks <span class="muted">· {esc(host)}</span></div>
<nav class="tabs" id="tabs" data-swap>{"".join(nav)}</nav>
<div class="status">{tz_note}{updated}<button type="button" id="refresh-btn" class="js-only"
  title="Fetch the latest data from pueue now">↻ Refresh</button>{toggle}</div></header>
{banner}
<main>{body}</main>
</body></html>'''


def error_page(status: int, message: str) -> str:
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{status} · Pueue Tasks</title><link rel="stylesheet" href="/static/style.css"></head>
<body><main><h1>{status}</h1><p>{esc(message)}</p><p><a href="/">Back to overview</a></p></main></body></html>'''
