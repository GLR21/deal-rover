"""T7: the plugin interface (providers/ and deals.py).

A store without a plugin must still work through the generic provider, and
deals must be matched, announced once and gated by store.
"""

import deals
from providers import get_provider
from providers.base import Provider, Deal, Target


def test_unknown_store_gets_the_generic_provider():
    provider = get_provider("Some Shop Without A Plugin")
    assert type(provider) is Provider
    assert provider.store == "Some Shop Without A Plugin"
    assert provider.allows_search is False


def test_generic_provider_reads_schema_org():
    html = """<html><head><script type="application/ld+json">
    {"@type": "Product", "name": "Widget X1",
     "offers": {"price": "199.90", "availability": "https://schema.org/InStock"}}
    </script></head><body><h1>Widget X1</h1>""" + "<p>details</p>" * 60 + "</body></html>"
    state = Provider().extract(html, Target(key="k", name="n", store="s", model="X1"))
    assert state.valid and state.available and state.price == 199.90


class _Source:
    name = "fake"

    def __init__(self, found):
        self.found = found

    def deals(self, targets):
        return self.found


def test_deal_with_coupon_alerts_once():
    targets = [{"key": "k", "name": "Widget", "store": "Shop", "coupon_terms": ["widget", "x1"]}]
    source = _Source([Deal(id="fake:1", title="Widget X1 128GB", store="Shop",
                           price=99.0, codes=("SAVE10",))])
    state = {}
    first = deals.check_deals(targets, state, [source])
    assert len(first) == 1 and "SAVE10" in first[0]
    assert deals.check_deals(targets, state, [source]) == []


def test_deal_without_coupon_needs_the_target_price():
    targets = [{"key": "k", "name": "Widget", "store": "Shop",
                "coupon_terms": ["widget"], "target_price": 50.0}]
    source = _Source([Deal(id="fake:1", title="Widget", store="Shop", price=99.0)])
    assert deals.check_deals(targets, {}, [source]) == []


def test_store_wide_coupon_only_for_monitored_stores():
    targets = [{"key": "k", "name": "Widget", "store": "Shop", "coupon_terms": ["x"]}]
    source = _Source([Deal(id="a", title="Sale", store="Other", codes=("A",), store_wide=True),
                      Deal(id="b", title="Sale", store="Shop", codes=("B",), store_wide=True)])
    messages = deals.check_deals(targets, {}, [source])
    assert len(messages) == 1 and "<code>B</code>" in messages[0]


def test_a_failing_source_does_not_stop_the_others():
    class Broken:
        name = "broken"

        def deals(self, targets):
            raise RuntimeError("site changed")

    targets = [{"key": "k", "name": "Widget", "store": "Shop", "coupon_terms": ["widget"]}]
    ok = _Source([Deal(id="ok", title="Widget", store="Shop", price=1.0, codes=("C",))])
    assert len(deals.check_deals(targets, {}, [Broken(), ok])) == 1
