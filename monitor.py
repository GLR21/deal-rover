#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Deal Rover entrypoint: interactive CLI and the `check` command.

Manages the monitored products (in targets.json) and runs the checks.
Store and URL are OPTIONAL when adding a product:
  - With a store, the product is monitored only there.
  - Without one ("all stores"), it is looked up in every store whose
    provider allows search by model, plus every store you give a URL for.
Stores come from the plugins in providers/; a store without a plugin still
works with a product URL (generic schema.org extraction).

Commands:
    python monitor.py            # interactive menu
    python monitor.py check      # run one check (what the scheduler calls)

Data file: targets.json (can be edited by hand).
"""

import os
import sys
import json
import time

import core
from core import (
    load_state, save_state, send_telegram, build_alert, log,
    SCRIPT_DIR,
)
from messages import render
from providers import get_provider, all_providers, deal_sources
from providers.base import Target
import deals

# Where the targets are kept (separate from the code).
TARGETS_FILE = os.environ.get("MONITOR_TARGETS_FILE",
                              os.path.join(SCRIPT_DIR, "targets.json"))

PRICE_MODES = ("cash", "installments")


# --------------------------------------------------------------------------- #
# Targets file
# --------------------------------------------------------------------------- #

def load_targets() -> list:
    """Reads targets.json. Returns a list of dicts."""
    if not os.path.exists(TARGETS_FILE):
        return []
    try:
        with open(TARGETS_FILE, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError) as exc:
        print(f"[warning] could not read {TARGETS_FILE}: {exc}")
        return []


def save_targets(targets: list) -> None:
    try:
        with open(TARGETS_FILE, "w", encoding="utf-8") as fh:
            json.dump(targets, fh, ensure_ascii=False, indent=2)
    except OSError as exc:
        print(f"[error] could not save {TARGETS_FILE}: {exc}")


def _make_key(model: str, store: str) -> str:
    """Builds a unique key for the target."""
    base = (store or "multi").lower() + "_" + model.lower()
    base = "".join(c for c in base if c.isalnum() or c == "_")
    key = base
    n = 2
    existing = {t["key"] for t in load_targets()}
    while key in existing:
        key = f"{base}_{n}"
        n += 1
    return key


# --------------------------------------------------------------------------- #
# Stores that allow search (for the "all stores" mode)
# --------------------------------------------------------------------------- #

def searchable_stores() -> list:
    """The stores whose providers allow search by model."""
    return [name for name, prov in all_providers().items() if prov.allows_search]


# --------------------------------------------------------------------------- #
# Interactive CLI
# --------------------------------------------------------------------------- #

def _ask(text: str, required: bool = True) -> str:
    while True:
        answer = input(text).strip()
        if answer or not required:
            return answer
        print("  (required)")


def _choose_store() -> str:
    """Store menu. Returns the store name, or '' for "all stores"."""
    stores = list(all_providers())
    print("\nWhich store should be monitored?")
    print("  [0] All stores (search by model where allowed)")
    for i, name in enumerate(stores, start=1):
        kind = "search" if all_providers()[name].allows_search else "URL only"
        print(f"  [{i}] {name}  ({kind})")
    print("  ...or type any other store name (generic provider, URL only)")
    while True:
        choice = input("> ").strip()
        if choice == "0":
            return ""  # all stores
        if choice.isdigit() and 1 <= int(choice) <= len(stores):
            return stores[int(choice) - 1]
        if choice and not choice.isdigit():
            return choice
        print("  invalid option")


def add_interactive() -> None:
    print("\n=== Add a product ===")
    model = _ask("Model code (e.g. CMW32GX4M4C3200C16): ")
    name = _ask("Friendly name for the alerts: ")
    store = _choose_store()

    urls = {}
    if store == "":
        # "All stores": search where allowed; ask for a URL everywhere else.
        with_search = searchable_stores()
        without_search = [s for s in all_providers() if s not in with_search]
        print(f"\nIn the stores with search ({', '.join(with_search) or 'none'}), "
              "the product is found by its model automatically.")
        if without_search:
            print(f"The stores without search ({', '.join(without_search)}) only "
                  "join if you give the product URL there.")
            for s in without_search:
                u = _ask(f"  URL at {s} (empty = don't monitor there): ", required=False)
                if u:
                    urls[s] = u
        print("Other stores: add them to \"urls\" in targets.json.")
    else:
        prov = all_providers().get(store)
        if prov is not None and prov.allows_search:
            u = _ask(f"URL at {store} (empty = search by model): ", required=False)
        else:
            print(f"{store} doesn't allow search; the URL is required.")
            u = _ask(f"URL at {store}: ")
        if u:
            urls[store] = u

    # Plausible price range (optional). Keeps junk from broken pages from
    # being taken as a price. Empty = the core's wide default range.
    print("\nPlausible price range (optional, avoids wrong readings).")
    pmin_txt = _ask("  Minimum price (empty = default): ", required=False)
    pmax_txt = _ask("  Maximum price (empty = default): ", required=False)

    # Target price: alert when the price drops to it. Empty = the default.
    ptarget_txt = _ask(f"\nTarget price (empty = default "
                       f"{core.format_price(core.DEFAULT_TARGET_PRICE)}): ", required=False)

    def _num(txt):
        if not txt:
            return None
        try:
            return float(txt.replace(",", "."))
        except ValueError:
            print(f"  (invalid value '{txt}', ignored)")
            return None

    new = {
        "key": _make_key(model, store),
        "name": name,
        "model": model,
        "store": store,      # "" = all stores
        "urls": urls,        # store -> url (may be empty when search exists)
        "min_price": _num(pmin_txt),
        "max_price": _num(pmax_txt),
        "target_price": _num(ptarget_txt),
    }
    targets = load_targets()
    targets.append(new)
    save_targets(targets)
    print(f"\n✓ Added (key: {new['key']}).")


def list_targets() -> None:
    targets = load_targets()
    if not targets:
        print("\n(no products yet)")
        return
    print(f"\n=== {len(targets)} monitored product(s) ===")
    for t in targets:
        store = t.get("store") or "All stores"
        print(f"\n• {t['name']}" + ("  (DISABLED)" if t.get("enabled") is False else ""))
        print(f"    key:    {t['key']}")
        print(f"    model:  {t.get('model') or '(deals only)'}")
        print(f"    store:  {store}")
        for s, u in (t.get("urls") or {}).items():
            print(f"    url[{s}]: {u}")
        pmin, pmax = t.get("min_price"), t.get("max_price")
        if pmin is not None or pmax is not None:
            low = core.format_price(pmin) if pmin is not None else "-"
            high = core.format_price(pmax) if pmax is not None else "-"
            print(f"    range:  {low} to {high}")
        ptarget = t.get("target_price")
        print(f"    target: {core.format_price(ptarget)}" if ptarget is not None
              else f"    target: {core.format_price(core.DEFAULT_TARGET_PRICE)} (default)")


def remove_interactive() -> None:
    targets = load_targets()
    if not targets:
        print("\n(nothing to remove)")
        return
    print("\n=== Remove a product ===")
    for i, t in enumerate(targets, start=1):
        store = t.get("store") or "All"
        print(f"  [{i}] {t['name']}  ({store})")
    choice = input("Number to remove (empty = cancel): ").strip()
    if not choice:
        return
    if choice.isdigit() and 1 <= int(choice) <= len(targets):
        removed = targets.pop(int(choice) - 1)
        save_targets(targets)
        print(f"✓ Removed: {removed['name']}")
    else:
        print("invalid option")


# --------------------------------------------------------------------------- #
# Checking (expands each target into its stores and uses the providers)
# --------------------------------------------------------------------------- #

def _expand_to_stores(t: dict) -> list:
    """
    Turns a target (possibly "all stores") into a list of concrete
    (store, Target) pairs to check.
    """
    if t.get("enabled") is False:
        # Disabled target: stays in targets.json (with its history in the
        # state), but is not checked. To re-enable, remove the field.
        return []
    model = t.get("model")
    if not model:
        # DEALS-ONLY target (e.g. "10-piece glass container set", no specific
        # product): there is nothing to look up in the stores. deals.py
        # handles it through its `coupon_terms`.
        return []
    name = t["name"]
    urls = t.get("urls") or {}
    fixed_store = t.get("store") or ""
    pmin = t.get("min_price")        # None = the core's default range
    pmax = t.get("max_price")
    ptarget = t.get("target_price")  # None = DEFAULT_TARGET_PRICE
    term = t.get("search_term")      # None = the model itself (see Target)
    mode = t.get("price_mode") or "cash"
    if mode not in PRICE_MODES:
        log.warning("Target %s: unknown price_mode %r; using \"cash\".",
                    t["key"], t.get("price_mode"))
        mode = "cash"

    pairs = []
    if fixed_store:
        # A specific store: use the URL if there is one; otherwise, search.
        tgt = Target(key=t["key"], name=name, store=fixed_store, model=model,
                     fixed_url=urls.get(fixed_store), min_price=pmin, max_price=pmax,
                     target_price=ptarget, search_term=term, price_mode=mode)
        pairs.append((fixed_store, tgt))
    else:
        # "All stores": stores with search always join; others, only with a URL.
        for store in dict.fromkeys([*all_providers(), *urls]):
            url = urls.get(store)
            if url or get_provider(store).allows_search:
                subkey = f"{t['key']}__{store.lower()}"
                tgt = Target(key=subkey, name=f"{name} [{store}]",
                             store=store, model=model, fixed_url=url,
                             min_price=pmin, max_price=pmax,
                             target_price=ptarget, search_term=term, price_mode=mode)
                pairs.append((store, tgt))
    return pairs


def check_all() -> None:
    targets = load_targets()
    if not targets:
        log.info("No targets in %s; nothing to do.", TARGETS_FILE)
        return

    state = load_state()

    for t in targets:
        for store, tgt in _expand_to_stores(t):
            log.info("Checking: %s (%s)", tgt.name, store)
            provider = get_provider(store)
            result = provider.lookup(tgt)
            new = result.state

            if not new.valid:
                log.warning("  -> invalid reading; keeping the state and moving on.")
                time.sleep(2)
                continue

            log.info("  -> [%s] available=%s price=%s",
                     result.source, new.available, new.price)

            effective_url = result.url or tgt.fixed_url
            product = {"name": tgt.name, "store": store, "url": effective_url}

            old = state.get(tgt.key)
            alert = build_alert(product, old, new, tgt.target_price)
            if alert:
                log.info("  -> CHANGE detected, sending Telegram.")
                send_telegram(alert)

            # Only report a new URL relative to the last SAVED state (not the
            # static targets.json), so the warning doesn't repeat every cycle.
            previous_url = old.get("url") if old else tgt.fixed_url
            if (result.source == "search" and effective_url
                    and effective_url != previous_url):
                send_telegram(render("url_changed", name=tgt.name, store=store,
                                     url=effective_url))

            entry = new.to_dict()
            entry["url"] = effective_url
            entry["source"] = result.source
            # This store's all-time low (the key is already per store, so each
            # keeps its own). The "new low" alert reads this field.
            prices = [p for p in (new.price, (old or {}).get("min_seen")) if p is not None]
            if prices:
                entry["min_seen"] = min(prices)
            state[tgt.key] = entry
            time.sleep(2)

    # Deals and coupons: sources separate from the stores (see deals.py).
    # Only for the targets that ask, and each deal is announced once
    # (tracked in the state).
    try:
        for message in deals.check_deals(targets, state, deal_sources()):
            log.info("  -> DEAL found, sending Telegram.")
            send_telegram(message)
    except Exception:
        # Deals are an extra: a failure here must never break the price check.
        log.exception("Deal check failed; carrying on without it.")

    save_state(state)


# --------------------------------------------------------------------------- #
# Main menu
# --------------------------------------------------------------------------- #

def menu() -> None:
    while True:
        print("\n┌─ Deal Rover ──────────────────┐")
        print("│ [1] Add a product             │")
        print("│ [2] List products             │")
        print("│ [3] Remove a product          │")
        print("│ [4] Run a check now           │")
        print("│ [5] Quit                      │")
        print("└───────────────────────────────┘")
        choice = input("> ").strip()
        if choice == "1":
            add_interactive()
        elif choice == "2":
            list_targets()
        elif choice == "3":
            remove_interactive()
        elif choice == "4":
            print("\nRunning a check...\n")
            check_all()
            print("\nCheck finished.")
        elif choice == "5":
            print("Bye!")
            return
        else:
            print("invalid option")


def main() -> None:
    core.setup_logging()
    # Non-interactive mode for the scheduler: `python monitor.py check`
    if len(sys.argv) > 1 and sys.argv[1] == "check":
        check_all()
        return
    # No arguments: interactive menu.
    try:
        menu()
    except (KeyboardInterrupt, EOFError):
        print("\nBye.")


if __name__ == "__main__":
    main()
