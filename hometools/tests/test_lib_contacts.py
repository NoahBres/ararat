from __future__ import annotations

from hometools.lib import contacts


def test_looks_like_identifier_email():
    assert contacts.looks_like_identifier("noah@example.com") is True


def test_looks_like_identifier_phone():
    assert contacts.looks_like_identifier("+1 (555) 123-4567") is True


def test_looks_like_identifier_name_is_false():
    assert contacts.looks_like_identifier("Kirill") is False


def test_normalize_identifier_phone():
    assert contacts.normalize_identifier("+1 (555) 123-4567") == "+15551234567"


def test_normalize_identifier_email_lowercases():
    assert contacts.normalize_identifier("Noah@Example.com") == "noah@example.com"


def test_resolve_identifier_input_skips_lookup(monkeypatch):
    def _boom():
        raise AssertionError("should not query contacts for an identifier input")

    monkeypatch.setattr(contacts, "_load_all_contacts", _boom)
    result = contacts.resolve("+1 555 123 4567")
    assert result == [{"name": None, "identifiers": ["+15551234567"]}]


def test_resolve_fuzzy_name_groups_by_contact(monkeypatch):
    fake_contacts = [
        {"name": "Kirill Petrov", "phone": "+1 555 999 0001", "email": None},
        {"name": "Kirill Petrov", "phone": None, "email": "kirill@example.com"},
        {"name": "Kira Jones", "phone": "+1 555 999 0002", "email": None},
    ]
    monkeypatch.setattr(contacts, "_load_all_contacts", lambda: fake_contacts)

    result = contacts.resolve("kirill")
    assert result[0]["name"] == "Kirill Petrov"
    assert set(result[0]["identifiers"]) == {"+15559990001", "kirill@example.com"}


def test_resolve_no_match_returns_empty(monkeypatch):
    monkeypatch.setattr(contacts, "_load_all_contacts", lambda: [])
    assert contacts.resolve("nobody like this exists") == []


def test_lookup_name_matches_phone(monkeypatch):
    fake_contacts = [{"name": "Mom", "phone": "+1 555 000 1111", "email": None}]
    monkeypatch.setattr(contacts, "_load_all_contacts", lambda: fake_contacts)
    assert contacts.lookup_name("+15550001111") == "Mom"


def test_lookup_name_matches_email(monkeypatch):
    fake_contacts = [{"name": "Work Bot", "phone": None, "email": "bot@work.com"}]
    monkeypatch.setattr(contacts, "_load_all_contacts", lambda: fake_contacts)
    assert contacts.lookup_name("BOT@work.com") == "Work Bot"


def test_lookup_name_no_match_returns_none(monkeypatch):
    monkeypatch.setattr(contacts, "_load_all_contacts", lambda: [])
    assert contacts.lookup_name("+15550009999") is None
