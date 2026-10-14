"""Keeps every test on the default (English) templates, even when a local
messages.json with the user's own texts exists next to the code."""

import pytest

import messages


@pytest.fixture(autouse=True)
def _default_messages(tmp_path, monkeypatch):
    monkeypatch.setattr(messages, "MESSAGES_FILE", str(tmp_path / "no-messages.json"))
    messages._overrides.cache_clear()
    yield
    messages._overrides.cache_clear()
