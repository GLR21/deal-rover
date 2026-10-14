"""T1: price parsers (core._parse_price_text / core._parse_price_number,
core.format_price).

Covers the default Brazilian format (R$ 1.234,56) and a configured dollar
format. Prices are the most common source of silent errors: implausible
values (e.g. 0.21) have shown up in production from badly parsed broken
pages.
"""

import core



def test_parse_price_text_brazilian_format():
    assert core._parse_price_text("R$ 1.234,56") == 1234.56


def test_parse_price_text_without_prefix():
    assert core._parse_price_text("0,21") == 0.21


def test_parse_price_text_without_cents_is_none():
    # "1.234" lacks the comma + 2 decimals the regex requires.
    assert core._parse_price_text("1.234") is None


def test_parse_price_text_empty_is_none():
    assert core._parse_price_text("") is None
    assert core._parse_price_text(None) is None


def test_parse_price_number_meta_tag_format():
    assert core._parse_price_number("749.99") == 749.99


def test_parse_price_number_integer():
    assert core._parse_price_number("1500") == 1500.0


def test_parse_price_number_falls_back_to_brazilian_parser():
    # meta tags sometimes come in Brazilian format anyway.
    assert core._parse_price_number("1.234,56") == 1234.56


def test_parse_price_number_empty_is_none():
    assert core._parse_price_number("") is None


def test_dollar_format(monkeypatch):
    monkeypatch.setattr(core, "DECIMAL_SEPARATOR", ".")
    monkeypatch.setattr(core, "CURRENCY_FORMAT", "${}")
    assert core._parse_price_text("Now $1,234.56, was $1,500.00") == 1234.56
    assert core.format_price(1234.5) == "$1,234.50"


def test_default_format_is_brazilian_real():
    assert core.format_price(1234.5) == "R$ 1.234,50"
