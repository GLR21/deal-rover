#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Alert texts, as editable templates.

Every message the monitor sends is built from the templates in DEFAULTS. To
change the wording (or the language), create a `messages.json` next to the
code with only the keys you want to override; anything you leave out keeps
the default. `messages.example.json` lists every key to start from.

Templates use Python `str.format` placeholders (`{name}`, `{price}`...) and
Telegram's HTML subset (`<b>`, `<code>`...). Values are HTML-escaped before
they are inserted, so a product name like "A & B" can't break the message.
Setting a line template to "" drops that line from multi-line messages.

A broken override (unknown placeholder, unbalanced braces) never stops an
alert: it is logged and the default template is used instead.
"""

import os
import json
import logging
from functools import lru_cache
from html import escape

log = logging.getLogger("deal_rover.messages")

MESSAGES_FILE = os.environ.get(
    "MONITOR_MESSAGES_FILE",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "messages.json"))

DEFAULTS = {
    # --- price / stock alerts (core.build_alert) --------------------------- #
    "first_seen":      "🟢 <b>{name}</b>\nStore: {store}\nStatus: AVAILABLE\nPrice: {price}\n{url}",
    "change":          "🔔 <b>{name}</b>\nStore: {store}\n{changes}\nCurrent price: {price}\n{url}",
    "back_in_stock":   "✅ BACK IN STOCK",
    "out_of_stock":    "⛔ Out of stock",
    "seller_changed":  "🔄 {old} → {new}",
    "price_down":      "📉 Dropped from {old} to {new}",
    "price_up":        "📈 Went up from {old} to {new}",
    "new_low":         "🏷 NEW LOW (previous low: {previous})",
    "target_hit":      "🎯 HIT YOUR TARGET of {target}",
    "store_with_seller": "{store} ({seller})",
    "price_unknown":   "price not identified",
    "url_missing":     "(URL unavailable)",
    # --- monitor.check_all ------------------------------------------------- #
    "url_changed":     "🔗 <b>{name}</b>\nStore: {store}\nProduct URL changed. New URL:\n{url}",
    # --- deals and coupons (deals.check_deals) ----------------------------- #
    "deal_header_coupon":     "🎟 <b>Coupon: {name}</b>",
    "deal_header":            "🏷 <b>Deal: {name}</b>",
    "deal_store":             "Store: {store}",
    "deal_title":             "Offer: {title}",
    "deal_price_with_coupon": "Price with coupon: {price}",
    "deal_price":             "Price: {price}",
    "deal_old_price":         "Was: {price}",
    "deal_coupon":            "Coupon: <code>{coupon}</code>",
    "deal_below_target":      "🎯 BELOW YOUR TARGET of {target}",
    "deal_url":               "{url}",
    "deal_disclaimer":        "(community-posted deal; double-check before buying)",
    "store_coupon":           "🎟 <b>{store} coupons</b>\n{title}\n{codes}\n{url}",
}


class Html(str):
    """A string that is already safe HTML: `render` inserts it unescaped."""


@lru_cache(maxsize=1)
def _overrides() -> dict:
    """User templates from messages.json (read once per run; {} if absent)."""
    if not os.path.exists(MESSAGES_FILE):
        return {}
    try:
        with open(MESSAGES_FILE, encoding="utf-8") as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("Could not read %s (%s); using the default messages.", MESSAGES_FILE, exc)
        return {}
    if not isinstance(data, dict):
        log.warning("%s must be a JSON object; using the default messages.", MESSAGES_FILE)
        return {}
    unknown = sorted(k for k in data if k not in DEFAULTS and not k.startswith("_"))
    if unknown:
        log.warning("%s: unknown keys ignored (typo?): %s", MESSAGES_FILE, ", ".join(unknown))
    return {k: v for k, v in data.items() if k in DEFAULTS and isinstance(v, str)}


def render(key: str, **fields) -> Html:
    """Fills the template `key`, escaping every value that is not `Html`."""
    safe = {k: v if isinstance(v, Html) else escape(str(v)) for k, v in fields.items()}
    template = _overrides().get(key)
    if template is not None:
        try:
            return Html(template.format(**safe))
        except (KeyError, IndexError, ValueError) as exc:
            log.warning("messages.json: template %r is invalid (%r); using the default.",
                        key, exc)
    return Html(DEFAULTS[key].format(**safe))


def join_lines(*parts) -> Html:
    """Joins rendered lines, dropping empty ones (templates set to "")."""
    return Html("\n".join(p for p in parts if p))


def code_list(codes) -> Html:
    """'A, B' -> '<code>A</code>, <code>B</code>' (each code escaped)."""
    return Html(", ".join(f"<code>{escape(str(c))}</code>" for c in codes))
