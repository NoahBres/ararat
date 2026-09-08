"""Things 3 tools: read tools backed directly by the `things` library (which
reads the app's sqlite database) rather than shelling out to `things-cli` --
avoids subprocess overhead and gives us structured dicts for free; write
tools driven by the `things:///...` URL scheme (`open` launches/targets the
running Things app -- there is no HTTP API).

The Things URL scheme has **no delete action** -- to remove a to-do you can
only complete/cancel it (things.complete / things.update) or delete it by
hand in the app. Every write tool below is documented with that limitation
again at the call site.

Docs: https://culturedcode.com/things/support/articles/2803573/
"""

from __future__ import annotations

import json
import subprocess
import time
from datetime import datetime, timedelta
from typing import Any, Literal
from urllib.parse import quote

from rtk_api.config import get_settings
from rtk_api.registry import tool


class _LazyThings:
    """Lazy proxy for the `things` library.

    `import things` globs the Things 3 group container at import time, which
    under launchd triggers macOS's "access data from other apps" TCC prompt and
    blocks the whole process until someone clicks it. Deferring the import to
    first use keeps the server bootable (and /health answerable) even when the
    permission hasn't been granted yet. Tests monkeypatch attributes on this
    proxy directly, which shadows the delegated lookups.
    """

    def __getattr__(self, name: str):
        import things  # noqa: PLC0415 - deliberate lazy import (see docstring)

        return getattr(things, name)


things_lib = _LazyThings()

View = Literal["inbox", "today", "upcoming", "anytime", "someday", "logbook"]

_VIEW_FUNC_NAMES = {
    "inbox": "inbox",
    "today": "today",
    "upcoming": "upcoming",
    "anytime": "anytime",
    "someday": "someday",
    "logbook": "logbook",
}

_COMPACT_KEYS = (
    "uuid",
    "type",
    "title",
    "status",
    "notes",
    "start",
    "start_date",
    "deadline",
    "project",
    "project_title",
    "area",
    "area_title",
    "heading",
    "heading_title",
    "tags",
    "created",
    "modified",
)


class _TitleResolver:
    """Small per-call cache so we don't hit the DB repeatedly resolving the
    same project/area uuid to a title across a list of tasks.
    """

    def __init__(self) -> None:
        self._cache: dict[str, str | None] = {}

    def title_for(self, uuid: str | None) -> str | None:
        if not uuid:
            return None
        if uuid not in self._cache:
            record = things_lib.get(uuid)
            self._cache[uuid] = record.get("title") if record else None
        return self._cache[uuid]


def _compact(task: dict[str, Any], resolver: _TitleResolver) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in _COMPACT_KEYS:
        if key in task:
            out[key] = task[key]

    if "project" in task and "project_title" not in out:
        out["project_title"] = resolver.title_for(task.get("project"))
    if "area" in task and "area_title" not in out:
        out["area_title"] = resolver.title_for(task.get("area"))

    return out


@tool("things.list")
def list_tasks(view: View, limit: int = 200) -> list[dict]:
    """List Things to-dos in a given view: inbox, today, upcoming, anytime,
    someday, or logbook. Returns compact dicts (uuid, title, status, notes,
    dates, project/area titles, tags).
    """
    func_name = _VIEW_FUNC_NAMES.get(view)
    if func_name is None:
        raise ValueError(f"unknown view {view!r}; expected one of {sorted(_VIEW_FUNC_NAMES)}")
    tasks = getattr(things_lib, func_name)()
    resolver = _TitleResolver()
    return [_compact(t, resolver) for t in tasks[:limit]]


@tool("things.search")
def search(query: str, limit: int = 50) -> list[dict]:
    """Search Things to-dos, projects, and areas by title/notes text."""
    results = things_lib.search(query)
    resolver = _TitleResolver()
    return [_compact(t, resolver) for t in results[:limit]]


@tool("things.get")
def get(uuid: str) -> dict | None:
    """Get a single Things item (to-do, project, area, or heading) by uuid,
    including notes, checklist items, project/area, tags, and dates.
    """
    record = things_lib.get(uuid)
    if record is None:
        return None
    resolver = _TitleResolver()
    compact = _compact(record, resolver)
    if "items" in record:
        compact["items"] = [_compact(i, resolver) for i in record["items"]]
    if "checklist" in record:
        compact["checklist"] = record["checklist"]
    return compact


@tool("things.projects")
def projects() -> list[dict]:
    """List all Things projects."""
    resolver = _TitleResolver()
    return [_compact(p, resolver) for p in things_lib.projects()]


@tool("things.areas")
def areas() -> list[dict]:
    """List all Things areas."""
    return list(things_lib.areas())


@tool("things.tasks_in")
def tasks_in(project_uuid: str | None = None, area_uuid: str | None = None) -> list[dict]:
    """List incomplete to-dos within a given project or area (pass exactly
    one of project_uuid / area_uuid).
    """
    if bool(project_uuid) == bool(area_uuid):
        raise ValueError("pass exactly one of project_uuid or area_uuid")
    resolver = _TitleResolver()
    if project_uuid:
        tasks = things_lib.todos(project=project_uuid)
    else:
        tasks = things_lib.todos(area=area_uuid)
    return [_compact(t, resolver) for t in tasks]


# --------------------------------------------------------------------------
# Write tools (Things URL scheme)
# --------------------------------------------------------------------------
#
# The URL scheme (things:///add, things:///update, things:///add-project,
# things:///json) is one-way: `open`-ing a URL tells Things to do something,
# but gives us no direct return value. So after firing the URL we poll the
# sqlite DB (via the `things` library) for the change to land and report
# back what we found. There is no delete action anywhere in the scheme.


def build_things_url(action: str, params: dict[str, Any]) -> str:
    """Build a `things:///<action>?...` URL, URL-encoding every value.

    Pure function (no `open` call) so it's unit-testable on its own. Keys
    whose value is `None` are omitted entirely. Booleans become the literal
    strings "true"/"false". Callers are responsible for pre-joining list
    values into the comma- or newline-separated strings the URL scheme
    expects (e.g. tags -> "a,b,c", checklist items -> "a\\nb\\nc") before
    passing them in here.
    """
    parts = []
    for key, value in params.items():
        if value is None:
            continue
        if isinstance(value, bool):
            value = "true" if value else "false"
        parts.append(f"{quote(str(key), safe='')}={quote(str(value), safe='')}")
    query = "&".join(parts)
    return f"things:///{action}" + (f"?{query}" if query else "")


def _open_url(url: str) -> None:
    """Open a things:// URL via macOS `open`. `open` launches Things if it
    isn't already running, but a call that races a cold start can fail --
    retry once after a short pause before giving up.
    """
    try:
        subprocess.run(["open", url], check=True)
    except subprocess.CalledProcessError:
        time.sleep(1.0)
        subprocess.run(["open", url], check=True)


def _parse_things_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(str(value), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def _poll_for_match(
    fetch, title: str, since: datetime, timeout: float = 3.0, interval: float = 0.25
) -> dict | None:
    """Poll `fetch()` (a callable returning a list of Things dicts) until it
    yields an item with an exact `title` match created at/after `since`
    (with a few seconds of slack for clock/DB skew), or `timeout` elapses.
    """
    deadline = time.monotonic() + timeout
    cutoff = since - timedelta(seconds=5)
    while True:
        for item in fetch():
            if item.get("title") != title:
                continue
            created = _parse_things_dt(item.get("created"))
            if created is None or created >= cutoff:
                return item
        if time.monotonic() >= deadline:
            return None
        time.sleep(interval)


@tool("things.add", write=True)
def add(
    title: str,
    notes: str | None = None,
    when: str | None = None,
    deadline: str | None = None,
    tags: list[str] | None = None,
    list: str | None = None,
    checklist: list[str] | None = None,
) -> dict:
    """Create a new Things to-do via the `things:///add` URL scheme.

    `when` accepts "today", "tomorrow", "evening", "anytime", "someday", or
    a "YYYY-MM-DD" date. `list` is the target project or area title/uuid.
    There is no delete action in the URL scheme.

    After firing the URL, polls the Things DB for up to ~3s for a new to-do
    with this exact title created just now, and returns
    `{"uuid", "title", "confirmed"}` -- `confirmed` is `False` (with
    `uuid: None`) if the poll didn't find it in time, but the to-do may
    still have been created (e.g. Things was slow to launch).
    """
    params = {
        "title": title,
        "notes": notes,
        "when": when,
        "deadline": deadline,
        "tags": ",".join(tags) if tags else None,
        "list": list,
        "checklist-items": "\n".join(checklist) if checklist else None,
    }
    url = build_things_url("add", params)
    since = datetime.now()
    _open_url(url)
    match = _poll_for_match(
        lambda: things_lib.tasks(type="to-do", status=None, last="1d"),
        title,
        since,
    )
    if match:
        return {"uuid": match["uuid"], "title": match["title"], "confirmed": True}
    return {"uuid": None, "title": title, "confirmed": False}


@tool("things.update", write=True)
def update(
    uuid: str,
    title: str | None = None,
    notes: str | None = None,
    append_notes: str | None = None,
    when: str | None = None,
    deadline: str | None = None,
    tags: list[str] | None = None,
    add_tags: list[str] | None = None,
    completed: bool | None = None,
    canceled: bool | None = None,
) -> dict | None:
    """Update an existing Things to-do via the `things:///update` URL
    scheme. Requires `THINGS_AUTH_TOKEN` (Settings > General > Enable
    Things URLs in the Things app) -- raises `ValueError` clearly if unset.
    There is no delete action in the URL scheme; use `canceled=True` or
    `completed=True` to retire a to-do instead.

    After firing the URL, re-reads and returns the item via `things.get`.
    """
    settings = get_settings()
    token = settings.things_auth_token
    if not token:
        raise ValueError(
            "THINGS_AUTH_TOKEN is not configured; cannot use things.update "
            "(set it in ~/.config/rtk-api/env or the process environment)"
        )
    params = {
        "auth-token": token,
        "id": uuid,
        "title": title,
        "notes": notes,
        "append-notes": append_notes,
        "when": when,
        "deadline": deadline,
        "tags": ",".join(tags) if tags is not None else None,
        "add-tags": ",".join(add_tags) if add_tags else None,
        "completed": completed,
        "canceled": canceled,
    }
    url = build_things_url("update", params)
    _open_url(url)
    time.sleep(0.3)
    return get(uuid)


@tool("things.complete", write=True)
def complete(uuid: str) -> dict | None:
    """Mark a Things to-do as completed. Convenience wrapper over
    `things.update(uuid, completed=True)`. There is no delete action in the
    URL scheme -- this is the closest equivalent to "removing" a to-do.
    """
    return update(uuid, completed=True)


@tool("things.add_project", write=True)
def add_project(
    title: str,
    notes: str | None = None,
    area: str | None = None,
    when: str | None = None,
    deadline: str | None = None,
    tags: list[str] | None = None,
    todos: list[str] | None = None,
) -> dict:
    """Create a new Things project via the `things:///add-project` URL
    scheme. `todos` are initial to-do titles added to the project. There is
    no delete action in the URL scheme.

    After firing the URL, polls the Things DB for up to ~3s for a new
    project with this exact title created just now, and returns
    `{"uuid", "title", "confirmed"}` (see `things.add` for `confirmed`
    semantics).
    """
    params = {
        "title": title,
        "notes": notes,
        "area": area,
        "when": when,
        "deadline": deadline,
        "tags": ",".join(tags) if tags else None,
        "to-dos": "\n".join(todos) if todos else None,
    }
    url = build_things_url("add-project", params)
    since = datetime.now()
    _open_url(url)
    match = _poll_for_match(
        lambda: things_lib.tasks(type="project", status=None, last="1d"),
        title,
        since,
    )
    if match:
        return {"uuid": match["uuid"], "title": match["title"], "confirmed": True}
    return {"uuid": None, "title": title, "confirmed": False}


@tool("things.batch", write=True)
def batch(commands: list[dict]) -> dict:
    """Run a batch of Things URL-scheme JSON commands via `things:///json`.

    Each entry in `commands` must be a dict with at least `type` (e.g.
    "to-do", "project", "heading") and `attributes` keys, per the "JSON"
    section of the Things URL scheme docs:
    https://culturedcode.com/things/support/articles/2803573/

    Commands are passed through with minimal validation -- callers are
    responsible for shaping them correctly per those docs. Requires
    `THINGS_AUTH_TOKEN`. There is no delete action in the URL scheme.
    """
    if not isinstance(commands, list) or not commands:
        raise ValueError("commands must be a non-empty list")
    for item in commands:
        if not isinstance(item, dict) or "type" not in item or "attributes" not in item:
            raise ValueError("each command must be a dict with 'type' and 'attributes' keys")
    settings = get_settings()
    token = settings.things_auth_token
    if not token:
        raise ValueError(
            "THINGS_AUTH_TOKEN is not configured; cannot use things.batch "
            "(set it in ~/.config/rtk-api/env or the process environment)"
        )
    data = json.dumps(commands)
    url = build_things_url("json", {"auth-token": token, "data": data})
    _open_url(url)
    return {"submitted": len(commands)}
