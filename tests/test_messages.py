"""T6: editable alert templates (messages.py).

The texts can be overridden in messages.json without touching the code, so
the override has to be safe: values are always HTML-escaped, a broken user
template falls back to the default instead of losing the alert, and an empty
template removes its line.
"""

import json

import pytest

import messages


@pytest.fixture
def overrides(tmp_path, monkeypatch):
    """Points messages.py at a temporary messages.json with the given content."""
    def _write(content: dict):
        path = tmp_path / "messages.json"
        path.write_text(json.dumps(content), encoding="utf-8")
        monkeypatch.setattr(messages, "MESSAGES_FILE", str(path))
        messages._overrides.cache_clear()
    yield _write
    messages._overrides.cache_clear()


def test_default_template_escapes_values():
    text = messages.render("deal_store", store="A & B <i>")
    assert text == "Store: A &amp; B &lt;i&gt;"


def test_override_replaces_only_its_key(overrides):
    overrides({"back_in_stock": "✅ VOLTOU AO ESTOQUE"})
    assert messages.render("back_in_stock") == "✅ VOLTOU AO ESTOQUE"
    assert messages.render("out_of_stock") == messages.DEFAULTS["out_of_stock"]


def test_broken_override_falls_back_to_the_default(overrides):
    overrides({"price_down": "📉 {old} -> {wrong_placeholder}"})
    text = messages.render("price_down", old="R$ 10,00", new="R$ 9,00")
    assert text == "📉 Dropped from R$ 10,00 to R$ 9,00"


def test_empty_template_drops_the_line(overrides):
    overrides({"deal_disclaimer": ""})
    text = messages.join_lines("line 1", messages.render("deal_disclaimer"), "line 2")
    assert text == "line 1\nline 2"


def test_html_values_are_not_escaped_twice():
    inner = messages.render("deal_coupon", coupon="A&B")
    outer = messages.join_lines(inner)
    assert outer == "Coupon: <code>A&amp;B</code>"
