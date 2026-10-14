#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Core of the monitor: fetching pages, extracting price and availability,
persistent state and Telegram alerts. Knows nothing about specific stores;
store-specific logic lives in the plugins under providers/.

How a check works:
    1. For each target in targets.json, a provider locates the product page
       (internal API, fixed URL or search, depending on the store).
    2. Price and availability are extracted (JSON-LD first, text heuristics
       as a fallback).
    3. The reading is compared with the last saved state (state.json).
    4. If something relevant changed, a Telegram message is sent.

Usage:
    python monitor.py            # interactive menu
    python monitor.py check      # single check (what the scheduler runs)
"""

import os
import re
import json
import logging
from dataclasses import dataclass
from typing import Optional

import requests
from bs4 import BeautifulSoup

from messages import render, join_lines

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

# Bot token and chat id come from environment variables only.
# (Never write the token in the code.)
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# DEFAULT target price: when a price drops to this, the monitor alerts.
# Each target can set its own with "target_price" in targets.json.
DEFAULT_TARGET_PRICE = float(os.environ.get("DEFAULT_TARGET_PRICE", "3000"))

# The script's own directory, so the scheduler finds the files from anywhere.
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# Where the previous readings (price/availability) are saved.
STATE_FILE = os.environ.get("MONITOR_STATE_FILE", os.path.join(SCRIPT_DIR, "state.json"))

# Log file (in addition to the terminal output).
LOG_FILE = os.environ.get("MONITOR_LOG_FILE", os.path.join(SCRIPT_DIR, "monitor.log"))

# How prices are written by the stores and in the alerts. The default is the
# Brazilian real: "R$ 1.234,56". US dollar: CURRENCY_FORMAT="${}" and
# DECIMAL_SEPARATOR="."; euro: CURRENCY_FORMAT="{} €" (comma decimals).
CURRENCY_FORMAT = os.environ.get("CURRENCY_FORMAT", "R$ {}")
DECIMAL_SEPARATOR = os.environ.get("DECIMAL_SEPARATOR", ",")

# Sent to the stores; many pick the language (and currency) from it.
ACCEPT_LANGUAGE = os.environ.get("ACCEPT_LANGUAGE", "pt-BR,pt;q=0.9,en;q=0.8")


# Headers that look like a real browser (fewer anti-bot blocks).
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": ACCEPT_LANGUAGE,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def setup_logging() -> None:
    """Sets up logging (file + terminal). Only the real entrypoint
    (monitor.py) calls this: importing this module must NOT create the log
    file by itself (that would break the tests and imports from scripts)."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.FileHandler(LOG_FILE, encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )


log = logging.getLogger("deal_rover")

# DEFAULT plausible price range for a product, used when a target does
# not set its own. Deliberately wide, to fit anything from cheap accessories
# to expensive hardware.
DEFAULT_MIN_PRICE = 50.0
DEFAULT_MAX_PRICE = 50000.0

# Snippets that, if found in the page's RENDERED text, mean it did not load
# correctly (leaked PHP source, error page, block page...). Matched against
# the rendered text, not the raw HTML, so a legitimate page that merely loads
# Cloudflare scripts is not mistaken for a Cloudflare challenge page.
BROKEN_PAGE_MARKERS = [
    "function get", "function set", "$this->", "<?php", "?>",
    "cloudflare", "access denied", "request blocked",
    "captcha", "checking your browser",
]


# --------------------------------------------------------------------------- #
# Data model
# --------------------------------------------------------------------------- #

@dataclass
class ProductState:
    available: bool
    price: Optional[float]
    title: Optional[str] = None
    valid: bool = True  # False when the reading was inconclusive (error/block)
    # Who actually sells it, when the source is a price comparator: the price
    # is the best offer's, and the alert must say whose it is. Free text, so a
    # provider can also use it for a non-price status ("28/10: SOLD OUT").
    seller: Optional[str] = None

    def to_dict(self) -> dict:
        d = {"available": self.available, "price": self.price, "title": self.title}
        if self.seller:
            d["seller"] = self.seller
        return d


# --------------------------------------------------------------------------- #
# Fetching pages
# --------------------------------------------------------------------------- #

# A real product page has tens of thousands of characters. An HTTP 200 much
# smaller than that is almost always a block/captcha page (some big stores
# answer 200, not 403). This only decides whether to try cloudscraper;
# `_is_broken_page` is what actually judges the page.
SHORT_PAGE_CHARS = 10000


def fetch_page(url: str) -> Optional[str]:
    """Downloads the page HTML. Returns None on failure/block."""
    # A 200 response that looks like a block page: kept as a last resort, in
    # case cloudscraper does not get through either.
    suspicious = None
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        if resp.status_code == 200:
            if len(resp.text) >= SHORT_PAGE_CHARS:
                return resp.text
            log.info("Short HTTP 200 (%s chars) from %s; trying cloudscraper.",
                     len(resp.text), url)
            suspicious = resp.text
        elif resp.status_code == 404:
            # Definitive error: cloudscraper won't help.
            log.warning("HTTP 404 for %s", url)
            return None
        else:
            log.warning("HTTP %s for %s", resp.status_code, url)
    except requests.RequestException as exc:
        log.warning("Network error for %s: %s", url, exc)

    # Fallback: cloudscraper solves Cloudflare's JS challenge.
    try:
        import cloudscraper
        scraper = cloudscraper.create_scraper(
            browser={"browser": "chrome", "platform": "windows", "mobile": False}
        )
        resp = scraper.get(url, headers=HEADERS, timeout=30)
        if resp.status_code == 200:
            return resp.text
        log.warning("cloudscraper got HTTP %s for %s", resp.status_code, url)
    except ImportError:
        log.warning("cloudscraper is not installed; skipped the fallback. "
                    "Install it with: pip install cloudscraper")
    except (requests.RequestException, OSError) as exc:
        log.warning("cloudscraper failed for %s: %s", url, exc)

    # Return the suspicious response (if any): `_is_broken_page` decides, and
    # the outcome stays the same as before: invalid reading, state preserved.
    return suspicious


# --------------------------------------------------------------------------- #
# Price and availability extraction
# --------------------------------------------------------------------------- #

# Words meaning OUT of stock (Portuguese and English).
OUT_OF_STOCK_MARKERS = [
    "avise-me", "avise quando", "me avise", "produto indisponivel",
    "produto indisponível", "indisponivel", "indisponível",
    "esgotado", "fora de estoque", "sem estoque", "out of stock",
]

# Words meaning the product can be bought.
IN_STOCK_MARKERS = [
    "comprar", "adicionar ao carrinho", "add to cart", "instock",
    "in stock", "em estoque", "disponivel", "disponível",
]


def _thousands_separator() -> str:
    return "." if DECIMAL_SEPARATOR == "," else ","


def _parse_price_text(text: str) -> Optional[float]:
    """Finds the first amount with cents in the text, in the configured
    format: 'R$ 1.234,56' -> 1234.56 (default) or '$1,234.56' -> 1234.56."""
    if not text:
        return None
    dec, thou = re.escape(DECIMAL_SEPARATOR), re.escape(_thousands_separator())
    m = re.search(rf"(\d[\d{thou}]*{dec}\d{{2}})(?!\d)", text)
    if not m:
        return None
    raw = m.group(1).replace(_thousands_separator(), "").replace(DECIMAL_SEPARATOR, ".")
    try:
        return float(raw)
    except ValueError:
        return None


def _parse_price_number(text: str) -> Optional[float]:
    """Converts a number already in '1234.56' format (meta/itemprop style)."""
    if not text:
        return None
    text = text.strip()
    # Decimal point format (e.g. '1234.56' or '1234')
    m = re.fullmatch(r"\d+(\.\d{1,2})?", text)
    if m:
        try:
            return float(text)
        except ValueError:
            return None
    # Sometimes it comes in Brazilian format; reuse the other parser.
    return _parse_price_text(text)


def _find_main_price(soup: BeautifulSoup, html: str) -> Optional[float]:
    """
    Looks for the MAIN price, from the most to the least reliable signal:
      1. <meta itemprop="price" content="..."> / property="product:price:amount"
      2. any element with itemprop="price" (content attribute or text)
      3. elements whose class/id contains 'price', 'preco' or 'valor'
      4. last resort: the first amount with cents in the whole HTML
    Keep this order: the last resort is the one that gets it wrong the most.
    """
    # 1) price meta tags (the most reliable signal)
    for attrs in (
        {"itemprop": "price"},
        {"property": "product:price:amount"},
        {"property": "og:price:amount"},
    ):
        tag = soup.find("meta", attrs=attrs)
        if tag and tag.get("content"):
            price = _parse_price_number(tag["content"])
            if price:
                return price

    # 2) any element with itemprop="price"
    for tag in soup.find_all(attrs={"itemprop": "price"}):
        if tag.get("content"):
            price = _parse_price_number(tag["content"])
            if price:
                return price
        price = _parse_price_text(tag.get_text(" ", strip=True))
        if price:
            return price

    # 3) elements with a price-like class/id
    candidates = soup.find_all(
        attrs={"class": re.compile(r"(price|preco|valor)", re.I)}
    ) + soup.find_all(
        attrs={"id": re.compile(r"(price|preco|valor)", re.I)}
    )
    for tag in candidates:
        price = _parse_price_text(tag.get_text(" ", strip=True))
        if price:
            return price

    # 4) last resort: the first price in the whole HTML
    return _parse_price_text(html)


def _iter_jsonld(soup: BeautifulSoup):
    """Iterates over every JSON-LD block of the page."""
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        # JSON-LD can be an object or a list of objects.
        if isinstance(data, list):
            for item in data:
                yield item
        else:
            yield data


def extract_from_jsonld(soup: BeautifulSoup) -> Optional[ProductState]:
    """Tries the structured data first: schema.org/Product with offers.price."""
    for item in _iter_jsonld(soup):
        if not isinstance(item, dict):
            continue
        kind = item.get("@type", "")
        if isinstance(kind, list):
            kind = " ".join(kind)
        if "Product" not in str(kind):
            continue

        title = item.get("name")
        offers = item.get("offers")
        if isinstance(offers, list):
            offers = offers[0] if offers else {}
        if not isinstance(offers, dict):
            continue

        price = offers.get("price")
        try:
            price = float(price) if price is not None else None
        except (ValueError, TypeError):
            price = None

        if price is None:
            # JSON-LD without a usable price: don't stop here, let the
            # heuristics (or another JSON-LD block) decide.
            continue

        availability = str(offers.get("availability", "")).lower()
        available = "instock" in availability or "in_stock" in availability
        # No declared availability but a price: assume available.
        if not availability:
            available = True

        return ProductState(available=available, price=price, title=title)
    return None


def extract_from_heuristics(html: str, soup: BeautifulSoup) -> ProductState:
    """Fallback: reads the page text."""
    text = soup.get_text(" ", strip=True).lower()

    out_of_stock = any(m in text for m in OUT_OF_STOCK_MARKERS)
    available = any(m in text for m in IN_STOCK_MARKERS) and not out_of_stock

    price = _find_main_price(soup, html)

    title = None
    if soup.title and soup.title.string:
        title = soup.title.string.strip()

    return ProductState(available=available, price=price, title=title)


def _is_broken_page(html: str, soup: BeautifulSoup, model: str = None) -> bool:
    """
    Tells whether the response is NOT a valid product page:
      - very short body (error pages tend to be small)
      - leaked PHP source (function get/set, $this->, <?php)
      - Cloudflare / captcha / access denied block pages

    The markers are matched against the RENDERED TEXT (soup.get_text()), never
    the raw HTML: a legitimate product page behind Cloudflare almost always
    mentions "cloudflare" in some <script> or comment, and matching the raw
    HTML would throw away good readings exactly in the stores this protects.

    `model` (optional): used as a "valid page" signal: a short page that does
    NOT contain the model code is treated as suspicious.
    """
    if not html:
        return True

    text = soup.get_text(" ", strip=True)
    sample = text[:5000].lower()

    for marker in BROKEN_PAGE_MARKERS:
        if marker in sample:
            return True

    # Suspiciously short page. With a model, it must appear in the text;
    # without one, the minimum size alone decides.
    if len(text) < 200:
        if model:
            norm = lambda s: re.sub(r"[^a-z0-9]", "", s.lower())
            if norm(model) not in norm(text):
                return True
        else:
            return True

    return False


def extract_product_info(html: str, model: str = None,
                         min_price: float = None, max_price: float = None) -> ProductState:
    """
    Main strategy: JSON-LD first, heuristics after.

    Optional parameters:
      - model:      product code, used as a "valid page" signal.
      - min_price / max_price: plausible price range for this product. When
        missing, the wide DEFAULT range is used.
    """
    pmin = min_price if min_price is not None else DEFAULT_MIN_PRICE
    pmax = max_price if max_price is not None else DEFAULT_MAX_PRICE

    soup = BeautifulSoup(html, "html.parser")

    # A clear error/block page (404, Cloudflare, leaked PHP...): don't try to
    # extract anything, mark the reading as invalid.
    if _is_broken_page(html, soup, model=model):
        log.warning("  -> page looks broken/blocked; reading ignored.")
        return ProductState(available=False, price=None, title=None, valid=False)

    state = extract_from_jsonld(soup)
    if state is None:
        state = extract_from_heuristics(html, soup)

    # Price sanity: drop values outside the product's plausible range (e.g. a
    # 0.21 that came from a broken page). If the price was the only evidence,
    # the whole reading is suspicious -> invalid.
    if state.price is not None and not (pmin <= state.price <= pmax):
        log.warning("  -> implausible price (%.2f, range %.2f-%.2f) dropped; "
                    "invalid reading.", state.price, pmin, pmax)
        state.price = None
        state.valid = False

    return state


# --------------------------------------------------------------------------- #
# Persistent state
# --------------------------------------------------------------------------- #

def load_state() -> dict:
    """Reads state.json. Returns {} if it is missing or corrupted."""
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except (json.JSONDecodeError, OSError):
            log.warning("state.json is corrupted; starting from scratch.")
    return {}


def save_state(state: dict) -> None:
    """Writes state.json atomically (write to .tmp, then replace), so the file
    is never left truncated if the process is killed mid-write."""
    tmp_path = STATE_FILE + ".tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False, indent=2)
        os.replace(tmp_path, STATE_FILE)
    except OSError as exc:
        log.error("Could not save the state: %s", exc)


# --------------------------------------------------------------------------- #
# Telegram
# --------------------------------------------------------------------------- #

def send_telegram(message: str) -> None:
    """Sends the message (already HTML) to the configured chat."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        log.warning("Telegram is not configured; message only logged:\n%s", message)
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }
    try:
        resp = requests.post(url, data=payload, timeout=15)
        if resp.status_code != 200:
            log.error("Telegram returned %s: %s", resp.status_code, resp.text)
    except requests.RequestException as exc:
        log.error("Failed to send the Telegram message: %s", exc)


def format_price(price: Optional[float]) -> str:
    """1234.56 -> 'R$ 1.234,56' (or as CURRENCY_FORMAT/DECIMAL_SEPARATOR say)."""
    if price is None:
        return render("price_unknown")
    amount = f"{price:,.2f}".replace(",", "\0").replace(".", DECIMAL_SEPARATOR)
    return CURRENCY_FORMAT.format(amount.replace("\0", _thousands_separator()))


def build_alert(product: dict, previous: Optional[dict], new: ProductState,
                target_price: Optional[float] = None) -> Optional[str]:
    """Decides whether something relevant changed and builds the alert.

    `target_price` is this product's target; None = DEFAULT_TARGET_PRICE.

    The texts come from messages.py (editable in messages.json). Name, store
    and URL are escaped there: the URL may have been discovered by scraping a
    store's search page, so it is not 100% trustworthy content."""
    store = product["store"]
    if new.seller:
        # Comparator: also show the store with the best offer.
        store = render("store_with_seller", store=store, seller=new.seller)
    url = product.get("url") or render("url_missing")
    price = format_price(new.price)

    # First time we see the product: only alert if it is available.
    if previous is None:
        if new.available:
            return render("first_seen", name=product["name"], store=store,
                          price=price, url=url)
        return None

    changes = []

    # Availability changed.
    if new.available and not previous.get("available"):
        changes.append(render("back_in_stock"))
    elif not new.available and previous.get("available"):
        changes.append(render("out_of_stock"))

    # `seller` changed: the store with the best offer in a comparator
    # (Store A -> Store B) or a free-text status (COMING SOON -> BUY). Without this rule, a switch between two "available" statuses
    # would not touch `available` and would go unnoticed.
    old_seller = previous.get("seller")
    if old_seller and new.seller and new.seller != old_seller:
        changes.append(render("seller_changed", old=old_seller, new=new.seller))

    # Price changed (only when both prices are valid).
    old_price = previous.get("price")
    if new.price is not None and old_price is not None and new.price != old_price:
        key = "price_down" if new.price < old_price else "price_up"
        changes.append(render(key, old=format_price(old_price), new=format_price(new.price)))

    # New lowest price (per store: state keys are already per store). The
    # first reading only RECORDS the low (min_seen missing) and is silent.
    min_seen = previous.get("min_seen")
    if new.price is not None and min_seen is not None and new.price < min_seen:
        changes.append(render("new_low", previous=format_price(min_seen)))

    # Reached the target price. Fires on the EDGE (it was above, now it is
    # at or below), never on the level: otherwise it would repeat every cycle
    # while the price stays low.
    target = target_price if target_price is not None else DEFAULT_TARGET_PRICE
    if (new.price is not None and old_price is not None
            and old_price > target >= new.price):
        changes.append(render("target_hit", target=format_price(target)))

    if not changes:
        return None

    return render("change", name=product["name"], store=store,
                  changes=join_lines(*changes), price=price, url=url)
