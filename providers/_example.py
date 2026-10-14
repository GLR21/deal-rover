# -*- coding: utf-8 -*-
"""
TEMPLATE for your own plugins. Files starting with "_" are not loaded:
copy this one to e.g. providers/mystore.py, rename the classes and keep
only what your store needs. Most stores need nothing at all: a target with
a product URL works without a provider (generic schema.org extraction).

Everything below targets example.com and is only illustrative.
"""

import re
import requests
from typing import Iterable, Optional

from bs4 import BeautifulSoup

from core import ProductState, HEADERS
from providers.base import (
    Provider, DealSource, Deal, Target, LookupResult,
    model_matches, title_matches_model_exact, price_ok,
)


class ExampleStoreProvider(Provider):
    store = "ExampleStore"   # the name you use in targets.json
    allows_search = True     # ONLY if the store's robots.txt allows /search

    # --- 1) a JSON API (optional): the fastest and most reliable source ---- #
    def lookup_api(self, target: Target) -> Optional[LookupResult]:
        resp = requests.get("https://api.example.com/products",
                            params={"q": target.model}, headers=HEADERS, timeout=20)
        if resp.status_code != 200:
            return None                      # falls back to the fixed URL
        for item in resp.json().get("items", []):
            # Confirm the model: a search returns neighbors and accessories.
            if not title_matches_model_exact(target.model, item.get("title", "")):
                continue
            price = float(item["price"])
            if not price_ok(price, target):  # own API: check the range yourself
                return None
            return LookupResult(
                state=ProductState(available=item.get("in_stock", False),
                                   price=price, title=item["title"]),
                url=target.fixed_url or item.get("url"),
                source="api",
            )
        return None

    # --- 2) a page parser (optional): when schema.org is not enough -------- #
    def extract(self, html: str, target: Target) -> ProductState:
        state = super().extract(html, target)  # broken page, range, JSON-LD
        if not state.valid:
            return state
        tag = BeautifulSoup(html, "html.parser").select_one(".final-price")
        if tag is not None:
            # e.g. "$ 1,234.56" -> 1234.56; adapt to the store's format
            digits = re.sub(r"[^\d.]", "", tag.get_text())
            if digits:
                state.price = float(digits)
                state.valid = price_ok(state.price, target)
        return state

    # --- 3) search (optional): rediscovers a product whose URL changed ----- #
    def search_url(self, model: str) -> Optional[str]:
        return f"https://www.example.com/search/{model}"

    def parse_search_for_url(self, html: str, model: str) -> Optional[str]:
        for m in re.finditer(r'href="(/product/\d+/[^"]+)"', html):
            if model_matches(model, m.group(1)):
                return "https://www.example.com" + m.group(1)
        return None


class ExampleCouponFeed(DealSource):
    name = "example-feed"

    def deals(self, targets: list) -> Iterable[Deal]:
        if not any(t.get("coupon_terms") for t in targets):
            return []                        # nobody wants deals: no request
        resp = requests.get("https://deals.example.com/feed.json",
                            headers=HEADERS, timeout=20)
        resp.raise_for_status()
        return [
            Deal(id=f"example-feed:{d['id']}",   # prefix: ids are global
                 title=d["title"], store=d["store"], url=d["url"],
                 price=d.get("price"), codes=tuple(filter(None, [d.get("coupon")])))
            for d in resp.json()
        ]
