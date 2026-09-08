from __future__ import annotations

from hometools.tools import things as things_tools


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
