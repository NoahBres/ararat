from __future__ import annotations

from rtk_api.tools import system


def test_ping():
    assert system.ping() == {"pong": True}


def test_version():
    result = system.version()
    assert "version" in result


def test_echo():
    assert system.echo("hi there") == {"text": "hi there"}
