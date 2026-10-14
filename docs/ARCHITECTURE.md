# Architecture and design notes

This document explains *why* the code is shaped the way it is. Most of the
rules below came from something that went wrong against a real store page.

## Layers

| Module | Responsibility |
|---|---|
| `core.py` | Store-agnostic core: `fetch_page` (with the `cloudscraper` fallback), price/availability extraction, broken-page detection, `state.json`, Telegram and the alert rules (`build_alert`). `ProductState` is the currency between layers. |
| `providers/base.py` | The plugin interface: `Provider` (the lookup cascade, also the generic provider), `SchemaOrgProvider`, `DealSource`, the `Target`/`LookupResult`/`Deal` data classes and matching helpers. |
| `providers/__init__.py` | The loader: imports every module in `providers/` (except `_*.py`) and registers the `Provider` subclasses with a `store` and every `DealSource`. |
| `deals.py` | Matches the deals from every source against the targets, announces each once, builds the messages. |
| `messages.py` | Alert templates, overridable through `messages.json`. |
| `monitor.py` | Entrypoint: interactive CLI and the `check` command. The only caller of `core.setup_logging()`: importing `core` must have no side effects (it would create log files during tests). |

## Why plugins

Store code is the part that breaks: layouts change, APIs move, anti-bot rules
tighten. It is also the part that is personal: which stores, which quirks.
Keeping it out of the core means the core stays small and tested, a broken
store affects only its own readings, and nobody has to fork the project to
add theirs.

The loader is deliberately dumb: no entry points, no config file. A file in
`providers/` is a plugin; a class with a `store` is a provider. A module that
fails to import is logged and skipped. Two providers with the same `store`
keep the first one (alphabetical by file) and log a warning.

A store without a plugin is not an error: `get_provider()` returns a plain
`Provider` with that name, which reads the fixed URL with the generic
extractor.

## One check, end to end

1. `load_targets()` reads `targets.json`.
2. `_expand_to_stores()` turns each target into `(store, Target)` pairs. A
   target with `store: ""` becomes one pair per store that has a URL or a
   provider that can search, with the key suffixed (`__storename`), so
   **every store keeps its own history and its own low**. Disabled targets
   and deals-only targets (no `model`) produce no pairs.
3. `get_provider(store).lookup(target)` runs the cascade.
4. An **invalid** reading is logged and skipped; the previous state is kept.
5. A valid reading goes through `build_alert()`; if something changed, the
   message is sent.
6. Deals are collected once (`deals.check_deals`) inside a broad
   `try/except`: they are an extra and must never break the price check.
7. `save_state()` runs **once, at the end**, atomically (`.tmp` +
   `os.replace`), because the scheduler may kill the process at any moment.

## The cascade

`Provider.lookup` tries, in order, stopping at the first valid reading:

1. `lookup_api`: only where an API exists.
2. `_try_url`: the target's fixed URL.
3. `_try_search`: rediscovers a moved URL; only where `allows_search` is
   true, which must be decided by reading the store's `robots.txt`.

Both the fixed URL and the search go through `extract()`, the single
extraction hook. A store that needs its own parser overrides `extract()` and
nothing else, so no path is left behind.

Error handling is deliberate: *expected* errors (network, JSON, missing keys)
are a failed strategy and the cascade moves on; anything else is logged with a
traceback, so a real bug doesn't hide behind an "invalid reading".

A provider that finds the product by API should still prefer the target's
fixed URL when there is one: stores often list the same model more than once
(own stock and marketplace partners), and a search takes the first match.

## Invalid readings are the safety net

The whole design separates **"I couldn't read it"** from **"it's gone"**. A
reading is invalid when:

- `_is_broken_page()` sees an error/block page. Its markers are matched
  against the **rendered text**, never the raw HTML: legitimate pages behind
  Cloudflare mention "cloudflare" in their scripts, and matching raw HTML would
  discard good readings precisely in the stores this protects (there is a test
  for it).
- The price is outside the target's plausible range (`min_price`/`max_price`).
- A provider says the page can't be trusted, e.g. `SchemaOrgProvider` on a
  page without a price (a login wall served as HTTP 200).

An invalid reading never alerts and never overwrites the state. This is what
keeps intermittent blocks from turning into "out of stock / back in stock"
noise.

`fetch_page` also treats an **HTTP 200 shorter than 10 000 characters** as
suspicious and tries `cloudscraper` before accepting it: some big stores block
by answering 200 with a small captcha page.

A provider with its own parser should prefer **no reading over a guessed
one**. If the element that holds the price is missing, return an invalid
reading instead of falling back to "the first price on the page": that
fallback tends to pick another seller's offer, an installment or a
recommended product, and a wrong price means a false "new low" every run.

## Price extraction

`extract_product_info` runs `_is_broken_page`, then JSON-LD
(`schema.org/Product` → `offers.price`), then heuristics. A JSON-LD block
without a usable price doesn't stop the search. `_find_main_price` goes from
the most to the least reliable signal: price meta tags → `itemprop="price"` →
elements whose class/id mentions price → the first amount with cents in the HTML. The
last one is the one that gets it wrong the most; keep the order.

Prices are read and written in the configured format (`CURRENCY_FORMAT`,
`DECIMAL_SEPARATOR`; Brazilian real by default). Only amounts with two
decimals are taken from free text, so a stray "128" or "2026" is never read as
a price. The availability heuristics know English and Portuguese words.

## Alert rules (`core.build_alert`)

- **First reading:** alerts only if available; otherwise it just records.
- **Availability** changed: back in stock / out of stock.
- **Price** changed: only when both the old and the new price exist.
- **New low:** below `min_seen`, the per-store all-time low kept in the state.
  The first reading only records it.
- **Target price:** fires on the **edge** (`old > target >= new`), never on
  the level, otherwise it would repeat every run while the price stays low.
- **Seller changed:** `seller` is free text set by the provider: the store
  with the best offer in a comparator, or a status such as a ticket date's.
  A switch between two "available" statuses (e.g. coming soon → buy) doesn't
  change `available` and would otherwise go unnoticed. Known side effect: in a
  comparator, two stores tied on price can swap and produce a 🔄 without a
  price change.

All texts come from `messages.render()`, which HTML-escapes every value (names
and URLs can come from scraped pages) and falls back to the default template if
a user template is broken.

## Deals

- A `DealSource` only finds deals; matching, bookkeeping and messages live in
  `deals.py`, so every source behaves the same.
- Nothing runs unless some enabled target has `coupon_terms`. Sources also
  receive the targets so they can skip requests nobody needs.
- A deal **with** a coupon always alerts; **without** one, only at or below
  the target's explicit `target_price`, otherwise every ordinary sale would be
  an alert.
- A `store_wide` deal (a coupon for a whole store) is announced when any
  enabled target uses that store.
- Every announced id is remembered in `state["__coupons__"]`. Ids are global,
  so sources prefix them (`"myfeed:123"`).

## Scheduling

Run a few times a day with a random delay. Anything that must be checked
often gets its own script with **its own** targets, state and log files: two
schedules writing the same `state.json` would lose each other's updates. The
random delay lives in the script because a scheduler's random delay usually
applies once per trigger, not on each repetition.

## Tests

`pytest`, no network. Every test feeds hand-built HTML or dicts to pure
functions, and `tests/conftest.py` pins the default English templates even if
a local `messages.json` exists. No test imports a local plugin.
