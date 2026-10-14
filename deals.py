# -*- coding: utf-8 -*-
"""
DEALS and coupons, sent alongside the price alerts.

The price on a product page is usually the list price; coupons and community
deals are published elsewhere. Each such place is a DealSource plugin in
providers/. This module asks every source for its deals, matches them
against the targets, announces each deal ONCE and builds the messages.

It doesn't send or save anything; monitor.py does, with `send_telegram`
and `save_state`.
"""

from __future__ import annotations

import logging
from typing import Optional

from core import format_price, DEFAULT_TARGET_PRICE
from messages import render, join_lines, code_list

log = logging.getLogger("deal_rover.deals")

# Where the already-announced deals are kept inside state.json. The
# underscores keep it from colliding with any target `key`.
STATE_KEY = "__coupons__"


def matches_terms(title: str, terms: list) -> bool:
    """
    Every term must appear in the offer title, ignoring case and spaces
    ("370 ml" matches "370ml"). A term can list alternatives separated by
    "|": "300ml|370ml|500ml" matches any of them.
    """
    norm = lambda s: s.lower().replace(" ", "")
    t = norm(title)
    return all(any(norm(alt) in t for alt in term.split("|")) for term in terms)


def _item_message(deal, targets: list) -> Optional[str]:
    """Message for a deal on ONE product, or None if no target wants it."""
    target = next((t for t in targets
                   if matches_terms(deal.title, t["coupon_terms"])
                   and (not t.get("deal_store")
                        or deal.store.lower() == t["deal_store"].lower())), None)
    if target is None or deal.price is None:
        return None

    # A deal WITH a coupon always alerts: the coupon is the news. WITHOUT
    # one, only if it is already at the target's explicit target price.
    # Otherwise every ordinary sale would be an alert.
    explicit = target.get("target_price")
    if not deal.codes and (explicit is None or deal.price > explicit):
        return None
    limit = explicit if explicit is not None else DEFAULT_TARGET_PRICE

    coupon = deal.codes and ", ".join(deal.codes)
    return join_lines(
        render("deal_header_coupon" if coupon else "deal_header", name=target["name"]),
        render("deal_store", store=deal.store),
        render("deal_title", title=deal.title[:90]),
        render("deal_price_with_coupon" if coupon else "deal_price",
               price=format_price(deal.price)),
        render("deal_old_price", price=format_price(deal.old_price))
        if deal.old_price is not None else "",
        render("deal_coupon", coupon=coupon) if coupon else "",
        render("deal_below_target", target=format_price(limit)) if deal.price <= limit else "",
        render("deal_url", url=deal.url) if deal.url else "",
        render("deal_disclaimer"),
    )


def check_deals(targets: list, state: dict, sources: list) -> list:
    """
    Returns the messages for the new deals. Only runs when some target asks
    for deals (`coupon_terms`); each deal id is remembered in state[STATE_KEY]
    so it is announced once.
    """
    targets = [t for t in targets if t.get("enabled") is not False]
    wanting = [t for t in targets if t.get("coupon_terms")]
    if not wanting:
        return []
    seen = state.setdefault(STATE_KEY, {})
    stores = {(t.get("store") or "").lower() for t in targets}
    messages = []

    for source in sources:
        try:
            found = list(source.deals(targets))
        except Exception:
            # Deals are an extra: one failing source never breaks the others.
            log.exception("Deal source %s failed; skipped.", source.name or source)
            continue
        for deal in found:
            if deal.id in seen:
                continue
            if deal.store_wide:
                if deal.store.lower() not in stores:
                    continue
                message = render("store_coupon", store=deal.store, title=deal.title,
                                 codes=code_list(deal.codes), url=deal.url)
            else:
                message = _item_message(deal, wanting)
                if message is None:
                    continue
            messages.append(message)
            seen[deal.id] = {"price": deal.price, "codes": list(deal.codes)}
    return messages
