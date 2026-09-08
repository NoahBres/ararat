from __future__ import annotations

from rtk_api.tools import things as things_tools


def test_list_today(monkeypatch):
    fake_tasks = [
        {
            "uuid": "abc123",
            "type": "to-do",
            "title": "buy milk",
            "status": "incomplete",
            "notes": "",
            "start": "Today",
            "start_date": "2026-09-08",
            "deadline": None,
            "created": "2026-09-08 10:00:00",
            "modified": "2026-09-08 10:00:00",
        }
    ]
    monkeypatch.setattr(things_tools.things_lib, "today", lambda: fake_tasks)

    result = things_tools.list_tasks("today")
    assert result == [
        {
            "uuid": "abc123",
            "type": "to-do",
            "title": "buy milk",
            "status": "incomplete",
            "notes": "",
            "start": "Today",
            "start_date": "2026-09-08",
            "deadline": None,
            "created": "2026-09-08 10:00:00",
            "modified": "2026-09-08 10:00:00",
        }
    ]


def test_list_unknown_view_raises():
    try:
        things_tools.list_tasks("bogus")  # type: ignore[arg-type]
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_get_missing_returns_none(monkeypatch):
    monkeypatch.setattr(things_tools.things_lib, "get", lambda uuid: None)
    assert things_tools.get("nope") is None


def test_get_resolves_project_title(monkeypatch):
    records = {
        "todo-1": {
            "uuid": "todo-1",
            "type": "to-do",
            "title": "write report",
            "status": "incomplete",
            "project": "proj-1",
        },
        "proj-1": {"uuid": "proj-1", "type": "project", "title": "Q3 Planning"},
    }
    monkeypatch.setattr(things_tools.things_lib, "get", lambda uuid: records.get(uuid))

    result = things_tools.get("todo-1")
    assert result["project_title"] == "Q3 Planning"


def test_list_today_sorts_like_app(monkeypatch):
    # Library order is raw today_index ascending; the app groups by
    # todayIndexReferenceDate (newest first) then today_index ascending.
    fake_tasks = [
        {"uuid": "old-first", "type": "to-do", "title": "old first",
         "today_index": -300, "start_date": "2026-09-04"},
        {"uuid": "new-second", "type": "to-do", "title": "new second",
         "today_index": -200, "start_date": "2026-09-04"},
        {"uuid": "new-first", "type": "to-do", "title": "new first",
         "today_index": -100, "start_date": "2026-09-07"},
    ]
    refs = {"old-first": 100, "new-second": 200, "new-first": 200}
    monkeypatch.setattr(things_tools.things_lib, "today", lambda: list(fake_tasks))
    monkeypatch.setattr(things_tools, "_today_index_refs", lambda uuids: refs)

    result = things_tools.list_tasks("today")
    assert [t["uuid"] for t in result] == ["new-second", "new-first", "old-first"]


def test_list_today_falls_back_to_library_order(monkeypatch):
    fake_tasks = [
        {"uuid": "a", "type": "to-do", "title": "a", "today_index": 2},
        {"uuid": "b", "type": "to-do", "title": "b", "today_index": 1},
    ]
    monkeypatch.setattr(things_tools.things_lib, "today", lambda: list(fake_tasks))
    monkeypatch.setattr(things_tools, "_today_index_refs", lambda uuids: {})

    result = things_tools.list_tasks("today")
    assert [t["uuid"] for t in result] == ["a", "b"]


def test_list_inbox_not_resorted(monkeypatch):
    fake_tasks = [
        {"uuid": "a", "type": "to-do", "title": "a"},
        {"uuid": "b", "type": "to-do", "title": "b"},
    ]
    monkeypatch.setattr(things_tools.things_lib, "inbox", lambda: list(fake_tasks))

    def fail(uuids):
        raise AssertionError("_today_index_refs must not run for inbox")

    monkeypatch.setattr(things_tools, "_today_index_refs", fail)
    result = things_tools.list_tasks("inbox")
    assert [t["uuid"] for t in result] == ["a", "b"]


def test_tasks_in_requires_exactly_one_arg():
    try:
        things_tools.tasks_in()
        assert False, "expected ValueError"
    except ValueError:
        pass
    try:
        things_tools.tasks_in(project_uuid="a", area_uuid="b")
        assert False, "expected ValueError"
    except ValueError:
        pass
