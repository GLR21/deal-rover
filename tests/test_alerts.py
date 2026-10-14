"""T4: the alert state machine (core.build_alert).

Decides whether Telegram rings or stays quiet. Covers availability and price
transitions, the new-low and target-price rules, and the cases where nothing
relevant changed and no alert must be sent. Assertions use the default
(English) templates from messages.py.
"""

import core
from core import ProductState


def _product(url="https://example.com/product"):
    return {"name": "Test Product", "store": "Test Store", "url": url}


def test_first_time_available_alerts():
    alert = core.build_alert(_product(), None, ProductState(available=True, price=100.0))
    assert alert is not None
    assert "AVAILABLE" in alert


def test_first_time_unavailable_is_silent():
    alert = core.build_alert(_product(), None, ProductState(available=False, price=None))
    assert alert is None


def test_back_in_stock():
    previous = {"available": False, "price": 100.0}
    new = ProductState(available=True, price=100.0)
    alert = core.build_alert(_product(), previous, new)
    assert alert is not None
    assert "BACK IN STOCK" in alert


def test_out_of_stock():
    previous = {"available": True, "price": 100.0}
    new = ProductState(available=False, price=100.0)
    alert = core.build_alert(_product(), previous, new)
    assert alert is not None
    assert "Out of stock" in alert


def test_price_dropped():
    previous = {"available": True, "price": 100.0}
    new = ProductState(available=True, price=90.0)
    alert = core.build_alert(_product(), previous, new)
    assert alert is not None
    assert "Dropped" in alert


def test_price_went_up():
    previous = {"available": True, "price": 90.0}
    new = ProductState(available=True, price=100.0)
    alert = core.build_alert(_product(), previous, new)
    assert alert is not None
    assert "Went up" in alert


def test_nothing_changed_is_silent():
    previous = {"available": True, "price": 100.0}
    new = ProductState(available=True, price=100.0)
    assert core.build_alert(_product(), previous, new) is None


def test_no_price_on_both_sides_and_same_stock_is_silent():
    previous = {"available": True, "price": None}
    new = ProductState(available=True, price=None)
    assert core.build_alert(_product(), previous, new) is None


def test_target_fires_only_when_crossing():
    # Edge, not level: crossing 3000 alerts; staying below doesn't repeat it.
    crossing = core.build_alert(_product(), {"available": True, "price": 3100.0},
                                ProductState(available=True, price=2900.0), 3000.0)
    assert "TARGET" in crossing
    staying = core.build_alert(_product(), {"available": True, "price": 2900.0},
                               ProductState(available=True, price=2800.0), 3000.0)
    assert "TARGET" not in staying


def test_seller_change_alerts_even_with_same_availability():
    # A ticket going from "coming soon" to "buy" doesn't change `available`.
    previous = {"available": True, "price": None, "seller": "28/10: EM BREVE"}
    new = ProductState(available=True, price=None, seller="28/10: COMPRAR")
    alert = core.build_alert(_product(), previous, new)
    assert alert is not None
    assert "EM BREVE" in alert and "COMPRAR" in alert


def test_name_and_url_are_html_escaped():
    product = {"name": "A & B <script>", "store": "Test Store",
               "url": "https://example.com/?a=1&b=2"}
    alert = core.build_alert(product, None, ProductState(available=True, price=10.0))
    assert "<script>" not in alert
    assert "&lt;script&gt;" in alert
    assert "&amp;" in alert


def test_missing_url_does_not_print_none():
    product = {"name": "Test Product", "store": "Test Store", "url": None}
    alert = core.build_alert(product, None, ProductState(available=True, price=10.0))
    assert "None" not in alert
