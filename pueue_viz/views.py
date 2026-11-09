"""Page handlers: filter -> sort -> paginate -> render."""

import socket
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from typing import Callable

from . import render as r
from .pueue import PueueClient, Snapshot, Task
from .render import MUTED_DASH, esc

SORT_KEYS: dict[str, Callable[[Task], object]] = {
    "id": lambda t: t.task_id,
    "label": lambda t: t.label.lower() or None,
    "command": lambda t: t.command.lower(),
    "state": lambda t: t.state_label,
    "group": lambda t: t.group,
    "path": lambda t: t.path,
    "created": lambda t: t.created,
    "start": lambda t: t.start,
    "end": lambda t: t.end,
    "elapsed": lambda t: t.elapsed,
    "waited": lambda t: t.waited,
}


@dataclass
class App:
    client: PueueClient
    page_size: int = 20
    refresh: int = 10
    host: str = field(default_factory=socket.gethostname)


@dataclass
class Page:
    status: int
    html: str
    content_type: str = "text/html; charset=utf-8"


# ---------------------------------------------------------------- list helpers

def paginate(items: list, page: int, size: int) -> tuple[list, int, int]:
    """Return (items on page, clamped page number, number of pages)."""
    n_pages = max(1, -(-len(items) // size))
    page = min(max(1, page), n_pages)
    return items[(page - 1) * size: page * size], page, n_pages


def parse_page(value: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 1


def parse_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def apply_filters(tasks: list[Task], params: dict) -> list[Task]:
    q = params.get("q", "").strip().lower()
    state = params.get("state", "")
    group = params.get("group", "")
    d_from, d_to = parse_date(params.get("from", "")), parse_date(params.get("to", ""))
    out = []
    for t in tasks:
        if q and not (q in t.command.lower() or q in t.label.lower() or q in t.path.lower()
                      or q.lstrip("#") == str(t.task_id)):
            continue
        if state and state not in (t.state, t.state_label, t.state_group):
            continue
        if group and t.group != group:
            continue
        # compare calendar days in the viewer's time zone, not the server's
        if d_from and (not t.created or r.to_display(t.created).date() < d_from):
            continue
        if d_to and (not t.created or r.to_display(t.created).date() > d_to):
            continue
        out.append(t)
    return out


def sort_tasks(tasks: list[Task], params: dict, default: str) -> list[Task]:
    key = params.get("sort") if params.get("sort") in SORT_KEYS else default
    params["sort"] = key
    reverse = params.get("order", "desc") != "asc"
    params["order"] = "desc" if reverse else "asc"
    fn = SORT_KEYS[key]
    present = [t for t in tasks if fn(t) is not None]
    missing = [t for t in tasks if fn(t) is None]  # always last, whatever the order
    return sorted(present, key=fn, reverse=reverse) + missing


def counts(tasks: list[Task]) -> dict:
    c = Counter(t.category for t in tasks)
    return {"all": len(tasks), "running": c["running"], "pending": c["pending"], "finished": c["finished"]}


def wait_reason(task: Task, snap: Snapshot) -> tuple[str, str]:
    """(short reason, longer explanation) for a task that has not started."""
    if task.state == "Stashed":
        if task.enqueue_at:
            return "Scheduled", "Stashed with --delay; pueue queues it at the time shown"
        return "Stashed", f"Not queued; start it with `pueue enqueue {task.task_id}`"
    if task.state == "Locked":
        return "Locked", "Being edited (pueue edit); it returns to its previous state when done"
    by_id = {t.task_id: t for t in snap.tasks}
    waiting = [d for d in task.dependencies if d in by_id and by_id[d].category != "finished"]
    if waiting:
        return ("Dependency", "Waiting for " + ", ".join(f"#{d}" for d in waiting) + " to finish")
    group = snap.groups.get(task.group, {})
    if group.get("status") == "Paused":
        return "Group paused", f"Group {task.group!r} is paused (`pueue start -g {task.group}`)"
    busy = sum(1 for t in snap.tasks if t.group == task.group and t.category == "running")
    limit = group.get("parallel", 0)
    return "No free slot", (f"Waiting for a free slot in group {task.group!r} ({busy} of "
                    f"{limit if limit else 'unlimited'} running)")


def list_page(app: App, params: dict, path: str, title: str, heading: str, select: Callable[[Task], bool],
              columns: list[r.Column], default_sort: str, intro: str = "", refresh: bool = True) -> Page:
    result = app.client.snapshot()
    tasks = result.value.tasks
    subset = [t for t in tasks if select(t)]
    states = sorted({t.state for t in subset})
    if params.get("state") and params["state"] not in states:
        states.append(params["state"])
    groups = sorted(result.value.groups)
    filtered = sort_tasks(apply_filters(subset, params), params, default_sort)
    shown, page, n_pages = paginate(filtered, parse_page(params.get("page")), app.page_size)
    params["page"] = str(page)
    show_states = len(states) > 1 or bool(params.get("state"))
    body = (f'<h1>{esc(heading)}</h1>{intro}'
            + r.filter_form(path, params, states if show_states else [], groups)
            + '<div id="results" data-swap>'
            + r.table(columns, shown, path, params)
            + r.pagination(path, params, page, n_pages, len(filtered), app.page_size)
            + "</div>")
    html = r.layout(title, path, body, counts(tasks), app.host, result.fetched_at, result.error,
                    app.refresh if refresh else 0)
    return Page(200, html)


# ---------------------------------------------------------------- columns

def elapsed_cell(t: Task) -> str:
    if t.elapsed is None:
        return MUTED_DASH
    note = ' <small title="includes time spent paused">(paused)</small>' if t.state == "Paused" else ""
    return esc(r.fmt_td(t.elapsed)) + note


COL_ID = ("id", "ID", r.task_link)
COL_LABEL = ("label", "Label", r.label_cell)
COL_CMD = ("command", "Command", r.command_cell)
COL_STATE = ("state", "State", r.badge)
COL_GROUP = ("group", "Group", r.group_cell)
COL_CREATED = ("created", "Added", lambda t: r.fmt_dt(t.created))
COL_START = ("start", "Started", lambda t: r.fmt_dt(t.start))
COL_END = ("end", "Ended", lambda t: r.fmt_dt(t.end))
COL_ELAPSED = ("elapsed", "Run time", elapsed_cell)


# ---------------------------------------------------------------- pages

def groups_table(snap: Snapshot) -> str:
    rows = []
    for name in sorted(snap.groups, key=lambda n: (n != "default", n)):
        g = snap.groups[name]
        c = Counter(t.state_group for t in snap.tasks if t.group == name)
        cls = "s-paused" if g["status"] == "Paused" else "s-running" if g["status"] == "Running" else "s-completed"
        link = esc(r.query_url("/", {"group": name}))
        rows.append(
            f'<tr><td><a href="{link}">{esc(name)}</a></td>'
            f'<td><span class="badge {cls}">{esc(g["status"])}</span></td>'
            f'<td>{g["parallel"] or "∞"}</td><td>{c["running"]}</td><td>{c["paused"]}</td>'
            f'<td>{c["pending"]}</td><td>{c["completed"]}</td><td>{c["failed"]}</td><td>{c["cancelled"]}</td></tr>')
    return ('<div class="table-wrap"><table><thead><tr><th>Group</th><th>Status</th><th>Parallel</th>'
            '<th>Running</th><th>Paused</th><th>Waiting</th><th>Success</th><th>Failed</th><th>Killed</th>'
            f'</tr></thead><tbody>{"".join(rows)}</tbody></table></div>')


def overview(app: App, params: dict) -> Page:
    snap = app.client.snapshot().value
    states = Counter(t.state for t in snap.tasks)
    groups = Counter(t.state_group for t in snap.tasks)
    cards = [
        ("running", "Running", states["Running"], "/running?state=Running"),
        ("paused", "Paused", states["Paused"], "/running?state=Paused"),
        ("pending", "Queued", states["Queued"] + states["Locked"], "/queue?state=Queued"),
        ("stashed", "Stashed", states["Stashed"], "/queue?state=Stashed"),
        ("completed", "Success", groups["completed"], "/finished?state=Success"),
        ("failed", "Failed", groups["failed"], "/finished?state=failed"),
        ("cancelled", "Killed", groups["cancelled"], "/finished?state=Killed"),
    ]
    html_cards = "".join(
        f'<a class="card c-{key}" href="{href}"><span class="num">{n}</span><span>{label}</span></a>'
        for key, label, n, href in cards)
    intro = (f'<div class="cards" id="cards" data-swap>{html_cards}</div>'
             f'<h2>Groups</h2><div id="groups" data-swap>{groups_table(snap)}</div><h2>All tasks</h2>')
    columns = [COL_ID, COL_LABEL, COL_CMD, COL_STATE, COL_GROUP, COL_CREATED, COL_START, COL_END, COL_ELAPSED]
    return list_page(app, params, "/", "Overview", "Overview", lambda t: True, columns, "id", intro)


def running(app: App, params: dict) -> Page:
    columns = [COL_ID, COL_LABEL, ("command", "Command", lambda t: r.command_cell(t, " wide")), COL_STATE,
               COL_GROUP, COL_START, COL_ELAPSED, ("path", "Path", lambda t: f'<span class="mono path" title="{esc(t.path)}">{esc(t.path)}</span>')]
    return list_page(app, params, "/running", "Running", "Running tasks",
                     lambda t: t.category == "running", columns, "start")


def queue(app: App, params: dict) -> Page:
    snap = app.client.snapshot().value

    def reason_cell(t: Task) -> str:
        short, long = wait_reason(t, snap)
        extra = f' <small>{r.fmt_dt(t.enqueue_at)}</small>' if t.enqueue_at else ""
        return f'<span class="reason" title="{esc(long)}">{esc(short)}</span>{extra}'

    columns = [COL_ID, COL_LABEL, COL_CMD, COL_STATE, COL_GROUP, ("", "Why waiting", reason_cell),
               ("", "Depends on", r.deps_cell), COL_CREATED,
               ("waited", "Waiting", lambda t: esc(r.fmt_td(t.waited)) if t.waited is not None else MUTED_DASH)]
    intro = '<p class="muted">Hover a reason to see what the task is waiting for.</p>'
    return list_page(app, params, "/queue", "Queue", "Queued and stashed tasks",
                     lambda t: t.category == "pending", columns, "id", intro)


def finished(app: App, params: dict) -> Page:
    columns = [COL_ID, COL_LABEL, COL_CMD, COL_STATE, COL_GROUP, COL_START, COL_END, COL_ELAPSED]
    return list_page(app, params, "/finished", "Finished", "Finished tasks",
                     lambda t: t.category == "finished", columns, "end")


def task_detail(app: App, params: dict, task_id: str) -> Page:
    result = app.client.snapshot()
    snap = result.value
    try:
        task = app.client.task(int(task_id))
    except ValueError:
        return Page(404, r.error_page(404, f"Invalid task id {task_id!r}"))
    if task is None:
        return Page(404, r.error_page(404, f"Task {task_id} not found (it may have been cleaned or removed)"))

    def row(label, value_html):
        return f"<dt>{esc(label)}</dt><dd>{value_html}</dd>"

    reason = ""
    if task.category == "pending":
        short, long = wait_reason(task, snap)
        reason = row("Why waiting", f'{esc(short)} <small class="muted">{esc(long)}</small>')
    dependents = [t.task_id for t in snap.tasks if task.task_id in t.dependencies]
    group = snap.groups.get(task.group, {})
    fields = [
        row("State", r.badge(task)),
        row("Exit code", f'<span class="mono {"bad" if task.exit_code else "muted"}">{task.exit_code}</span>')
        if task.exit_code is not None else "",
        row("Spawn error", f'<span class="mono bad">{esc(task.spawn_error)}</span>') if task.spawn_error else "",
        reason,
        row("Group", r.group_cell(task) + f' <small>{esc(group.get("status", "?"))} · '
                                          f'{group.get("parallel") or "unlimited"} parallel</small>'),
        row("Label", esc(task.label)) if task.label else "",
        row("Command", f'<pre class="cmdblock">{esc(task.command)}</pre>'),
        row("Original command", f'<pre class="cmdblock">{esc(task.original_command)}</pre>')
        if task.original_command and task.original_command != task.command else "",
        row("Path", f'<span class="mono">{esc(task.path or "–")}</span>'),
        row("Priority", str(task.priority)) if task.priority else "",
        row("Depends on", r.deps_cell(task)) if task.dependencies else "",
        row("Needed by", " ".join(f'<a class="mono" href="/task/{d}">#{d}</a>' for d in sorted(dependents)))
        if dependents else "",
        row("Added", r.fmt_dt(task.created)),
        row("Enqueued", r.fmt_dt(task.enqueued)) if task.enqueued else "",
        row("Scheduled for", r.fmt_dt(task.enqueue_at)) if task.enqueue_at else "",
        row("Waited in queue", esc(r.fmt_td(task.waited))) if task.waited is not None else "",
        row("Started", r.fmt_dt(task.start)) if task.start else "",
        row("Ended", r.fmt_dt(task.end)) if task.end else "",
        row("Run time", elapsed_cell(task)) if task.start else "",
    ]
    title = task.label or f"Task {task.task_id}"
    body = (f'<p class="crumb"><a href="/">Overview</a> › task <span class="mono">{task.task_id}</span></p>'
            f'<div id="detail" data-swap><h1>{esc(title)} {r.badge(task)}</h1>'
            f'<dl class="fields">{"".join(fields)}</dl></div>'
            f'<p class="muted">Logs: <code>pueue log {task.task_id}</code> · '
            f'follow: <code>pueue follow {task.task_id}</code></p>')
    html = r.layout(f"Task {task.task_id}", "", body, counts(snap.tasks), app.host,
                    result.fetched_at, result.error, app.refresh if task.is_active else 0)
    return Page(200, html)
