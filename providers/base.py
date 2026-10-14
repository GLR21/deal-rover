# -*- coding: utf-8 -*-
"""
The provider INTERFACE. Everything a store plugin needs lives here.

There are two kinds of plugin, both loaded from the providers/ folder:

  Provider    knows how to read ONE product at ONE store. Subclass it, set
              `store`, and override only what that store needs (see the
              hooks below). `Provider` itself is a working generic provider:
              fixed URL + schema.org/heuristic extraction. It is what a store
              without a plugin gets.

  DealSource  finds coupons and deals somewhere else (a deal aggregator, a
              store's coupon page, a feed...) and yields `Deal`s. deals.py
              matches them against the targets and announces each one once.

Every provider returns a ProductState from core.py and keeps its safety nets:
broken-page detection, plausible price range and the `valid` flag. An invalid
reading never alerts: the monitor keeps the previous state.

See providers/_example.py for a template and the README for the full guide.
"""

from __future__ import annotations

import re
import json
import logging
from dataclasses import dataclass
from typing import Iterable, Optional

import requests

from core import (
    ProductState,
    fetch_page,
    extract_product_info,
    DEFAULT_MIN_PRICE,
    DEFAULT_MAX_PRICE,
)

log = logging.getLogger("deal_rover.providers")


# --------------------------------------------------------------------------- #
# Data passed in and out of a provider
# --------------------------------------------------------------------------- #

@dataclass
class Target:
    """
    A product to monitor at one store, identified primarily by its MODEL
    code. Built by monitor.py from targets.json.
    """
    key: str                        # unique id in state.json
    name: str                       # friendly name for the alerts
    store: str                      # the provider's `store`
    model: str                      # e.g. "CMW32GX4M4C3200C16" (the search key)
    fixed_url: Optional[str] = None # shortcut; may be None or go stale
    min_price: Optional[float] = None  # plausible range; None = core default
    max_price: Optional[float] = None
    target_price: Optional[float] = None  # None = DEFAULT_TARGET_PRICE
    search_term: Optional[str] = None  # free-text query for stores whose search
                                       # can't find a model code; None = model
    price_mode: str = "cash"           # "cash" or "installments", for stores
                                       # that show both prices


@dataclass
class LookupResult:
    """What a provider returns after locating a product."""
    state: ProductState
    url: Optional[str] = None       # effective URL where it was found (may change!)
    source: str = "unknown"         # "api", "fixed_url", "search" or "none"


@dataclass
class Deal:
    """A coupon or deal found by a DealSource."""
    id: str                         # unique and STABLE: each id is announced once
    title: str                      # offer title; matched against `coupon_terms`
    store: str                      # who sells it
    url: str = ""
    price: Optional[float] = None   # final price (already with the coupon)
    old_price: Optional[float] = None
    codes: tuple = ()               # coupon codes, if any
    store_wide: bool = False        # a coupon for the whole store, not for one
                                    # product: announced when any enabled target
                                    # uses `store`, no `coupon_terms` match needed


# --------------------------------------------------------------------------- #
# Helpers for provider authors
# --------------------------------------------------------------------------- #

def title_matches_model_exact(model: str, title: str) -> bool:
    """
    STRICT match of the model code inside a title, able to tell close variants
    apart. E.g. 'KF432C16BBA/8' must match 'KF432C16BBA/8' and NOT
    'KF432C16BB/8' (no RGB) nor 'KF432C16BBA/16'.

    Normalizes only spaces and case, and requires the full code to appear
    without an alphanumeric character right after it (so 'KF432C16BBA/1' does
    not match inside 'KF432C16BBA/16').
    """
    if not title:
        return False
    m = model.strip().lower().replace(" ", "")
    t = title.strip().lower().replace(" ", "")
    idx = t.find(m)
    if idx == -1:
        return False
    end = idx + len(m)
    if end < len(t) and t[end].isalnum():
        return False
    return True


def model_matches(model: str, text: str) -> bool:
    """
    LOOSE match, to confirm a search result really is the product: ignores
    case and separators ('-'), and matches either the full code or the base
    before a slash (e.g. 'KF432C16BBA/8' matches a URL slug with 'kf432c16bba').
    """
    if not text:
        return False
    norm = lambda s: re.sub(r"[^a-z0-9]", "", s.lower())
    wanted = norm(model)
    base = norm(model.split("/")[0])
    text_norm = norm(text)
    return wanted in text_norm or (len(base) >= 6 and base in text_norm)


def price_ok(price: float, target: Target) -> bool:
    """
    Is the price inside the target's plausible range? Call it when the price
    comes from your OWN parser or API: only `extract_product_info` applies the
    range check by itself.
    """
    pmin = target.min_price if target.min_price is not None else DEFAULT_MIN_PRICE
    pmax = target.max_price if target.max_price is not None else DEFAULT_MAX_PRICE
    return pmin <= price <= pmax


# --------------------------------------------------------------------------- #
# Provider
# --------------------------------------------------------------------------- #

class Provider:
    """
    A store. `lookup()` runs a cascade, stopping at the first valid reading:

        1. lookup_api()   the store's JSON API, when it has one
        2. fixed URL      the target's URL, read with extract()
        3. search         search_url() + parse_search_for_url(), only when
                          `allows_search` (respect the store's robots.txt)

    Hooks to override (all optional):
      - store:          the name used in targets.json (required to register)
      - allows_search:  True only if robots.txt allows the search route
      - lookup_api(target)           -> LookupResult | None
      - extract(html, target)        -> ProductState  (own page parser)
      - search_url(model)            -> str | None
      - parse_search_for_url(html, model) -> str | None

    Return None (or an invalid ProductState) when unsure: a wrong reading
    becomes a false alert, a missing one only waits for the next cycle.
    """
    store: str = ""
    allows_search: bool = False

    def lookup_api(self, target: Target) -> Optional[LookupResult]:
        """Override if the store has a JSON API. Returns None if not / on failure."""
        return None

    def extract(self, html: str, target: Target) -> ProductState:
        """
        DEFAULT extraction: the core's generic parser (JSON-LD
        schema.org/Product first, heuristics after), which covers most
        retailers. Both the fixed URL and the search go through here, so
        override here (and only here) when a store needs its own parser.
        """
        return extract_product_info(html, model=target.model,
                                    min_price=target.min_price,
                                    max_price=target.max_price)

    def search_url(self, model: str) -> Optional[str]:
        """Override to build the store's search URL."""
        return None

    def parse_search_for_url(self, html: str, model: str) -> Optional[str]:
        """
        Override to pick, from the results HTML, the URL of the product whose
        title/code matches the model. Returns None if not found.
        """
        return None

    # ---- strategy 2: fixed URL --------------------------------------------- #
    def _try_url(self, url: str, target: Target) -> Optional[LookupResult]:
        html = fetch_page(url)
        if html is None:
            return None
        state = self.extract(html, target)
        if not state.valid:
            return None
        return LookupResult(state=state, url=url, source="fixed_url")

    # ---- strategy 3: search by model --------------------------------------- #
    def _try_search(self, target: Target) -> Optional[LookupResult]:
        surl = self.search_url(target.model) if self.allows_search else None
        if not surl:
            log.info("    [%s] no search fallback (robots.txt or search not "
                     "implemented for this store); end of the cascade.", self.store)
            return None
        html = fetch_page(surl)
        if html is None:
            return None
        new_url = self.parse_search_for_url(html, target.model)
        if not new_url:
            log.info("    [%s] search did not find model %s.",
                     self.store, target.model)
            return None
        # Found a candidate URL: confirm by reading the product page.
        html2 = fetch_page(new_url)
        if html2 is None:
            return None
        state = self.extract(html2, target)
        if not state.valid:
            return None
        return LookupResult(state=state, url=new_url, source="search")

    # ---- the full cascade -------------------------------------------------- #
    def lookup(self, target: Target) -> LookupResult:
        """
        Runs the cascade and always returns a LookupResult. If everything
        fails, the state is invalid (and the monitor ignores it).
        """
        # Expected errors (network, parsing, a response in an unexpected
        # format) count as a failure of that strategy and the cascade moves
        # on. Anything else (a real bug) is logged with a traceback, so it
        # does not hide behind an "invalid reading".
        EXPECTED_ERRORS = (requests.RequestException, json.JSONDecodeError,
                           KeyError, TypeError, ValueError, AttributeError)

        # 1) JSON API (if the store has one)
        try:
            r = self.lookup_api(target)
            if r and r.state.valid:
                log.info("    [%s] via API: available=%s price=%s",
                         self.store, r.state.available, r.state.price)
                return r
        except EXPECTED_ERRORS as exc:
            log.warning("    [%s] API error: %s", self.store, exc)
        except Exception:
            log.exception("    [%s] unexpected API error", self.store)

        # 2) Fixed URL
        if target.fixed_url:
            try:
                r = self._try_url(target.fixed_url, target)
                if r:
                    log.info("    [%s] via fixed URL: available=%s price=%s",
                             self.store, r.state.available, r.state.price)
                    return r
                log.info("    [%s] fixed URL failed/invalid; trying search.",
                         self.store)
            except EXPECTED_ERRORS as exc:
                log.warning("    [%s] fixed URL error: %s", self.store, exc)
            except Exception:
                log.exception("    [%s] unexpected fixed URL error", self.store)

        # 3) Search by model (fallback that rediscovers the URL)
        try:
            r = self._try_search(target)
            if r:
                log.info("    [%s] via SEARCH: available=%s price=%s (new url: %s)",
                         self.store, r.state.available, r.state.price, r.url)
                return r
        except EXPECTED_ERRORS as exc:
            log.warning("    [%s] search error: %s", self.store, exc)
        except Exception:
            log.exception("    [%s] unexpected search error", self.store)

        # Everything failed -> invalid reading (the monitor keeps the last state).
        return LookupResult(
            state=ProductState(available=False, price=None, title=None, valid=False),
            url=target.fixed_url,
            source="none",
        )


class SchemaOrgProvider(Provider):
    """
    A store that publishes `schema.org/Product` with `offers.price` (most
    retailers do). No parser of its own: the inherited `extract()` already
    reads JSON-LD. It only adds a stricter rule (**a page without a price is
    an invalid reading**) for stores that answer scrapers with a login wall
    or anti-bot page as HTTP 200 with enough text to pass as a real page.
    Without it, that page would become `available=False` and the next cycle
    would send a false "out of stock".
    """

    def extract(self, html: str, target: Target) -> ProductState:
        state = super().extract(html, target)
        if state.valid and state.price is None:
            log.warning("    [%s] page without a price (login wall/anti-bot?); "
                        "invalid reading.", self.store)
            state.valid = False
        return state


# --------------------------------------------------------------------------- #
# DealSource
# --------------------------------------------------------------------------- #

class DealSource:
    """
    Somewhere coupons and deals are published. Implement `deals()`.

    It receives the ENABLED targets so it can skip its requests when nobody
    needs it (e.g. a store's coupon page when no target uses that store).
    Return the deals as found; deals.py does the matching, the "announce
    once" bookkeeping and the message. Raising is fine: the error is logged
    and the other sources still run.
    """
    name: str = ""

    def deals(self, targets: list) -> Iterable[Deal]:
        raise NotImplementedError
