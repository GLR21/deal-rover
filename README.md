# Deal Rover 🛰️

**Like a Mars rover, but for online stores: a small, self-hosted explorer that roams the pages you point it at on its own, looking for price drops, restocks, coupons and any status you can read from a page, and radios back to you on Telegram.**

![Python](https://img.shields.io/badge/python-3.10%2B-blue) ![License: MIT](https://img.shields.io/badge/license-MIT-green) ![Tests](https://img.shields.io/badge/tests-pytest-brightgreen)

Deal Rover runs on a schedule (cron or Windows Task Scheduler). You list what you want to watch, it checks a few times a day, compares with what it saw before, and only messages you when something **actually** changed.

It ships **without any store**. A product page that publishes standard [schema.org](https://schema.org/Product) data works out of the box with just its URL. For anything else (a store's JSON API, a page that needs its own parser, a search that finds a product whose URL moved, a coupon feed) you write a small **provider** plugin against a documented interface.

```text
🔔 Acme Headphones H100
Store: SomeShop
📉 Dropped from R$ 459,90 to R$ 389,90
🏷 NEW LOW (previous low: R$ 409,90)
🎯 HIT YOUR TARGET of R$ 400,00
Current price: R$ 389,90
https://www.example.com/product/123/acme-h100
```

## Features

- **Price and stock tracking** per store, with a **new-low** alert and a **target price** that fires once when crossed (not every run while it stays low).
- **Works without code** for pages with schema.org product data (JSON-LD), with text heuristics as a fallback.
- **Pluggable stores:** drop a file in `providers/` and it is picked up. No registry to edit.
- **Coupons and deals** from any source you plug in, matched against your targets by keywords and announced once.
- **Not only prices:** a provider can report any status as text (a ticket date going from "sold out" to "buy", the store with the best offer in a comparator) and every change alerts.
- **Alert texts you can rewrite** in any language without touching the code (`messages.json`).
- **Never alerts on uncertainty:** blocked pages, captchas and implausible prices are discarded and the last good reading is kept.

## Quick start

Requirements: Python 3.10+ and a Telegram account. Tested on Ubuntu 24.04 (including WSL2 on Windows 11).

```bash
git clone https://github.com/<your-user>/deal-rover.git
cd deal-rover
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
```

**1. Create your Telegram bot.** Talk to [@BotFather](https://t.me/BotFather), send `/newbot` and copy the token. Then send any message to your new bot and open `https://api.telegram.org/bot<TOKEN>/getUpdates`. Your chat id is in `"chat":{"id": ...}`.

**2. Store the credentials** (this file is git-ignored):

```bash
cp run.sh.example run.sh && chmod +x run.sh
nano run.sh   # paste TELEGRAM_TOKEN and TELEGRAM_CHAT_ID
```

**3. Say what to watch.** Start from the examples and edit (also git-ignored):

```bash
cp targets.example.json targets.json
```

…or add products interactively with `./.venv/bin/python monitor.py`.

**4. Run a check:**

```bash
./run.sh
```

The first run records what it sees and only alerts about products that are available; from then on, you only hear about changes. Without a token, alerts are just written to `monitor.log`, which is handy for trying it out.

## Configuring targets

`targets.json` is a list of targets. Each one is a product (or a deal search) in one store or in many.

| Field | Required | Meaning |
|---|---|---|
| `key` | yes | unique id, used in `state.json` |
| `name` | yes | the name shown in the alerts |
| `store` | yes | a store name, or `""` for **all stores** (see below) |
| `model` | usually | the product code (e.g. `CMW16GX4M2C3200C16`). Providers use it to confirm they found the right product. Omit it for a deals-only target |
| `urls` | depends | `{ "Store": "product URL" }`; required for stores without a provider or without search |
| `min_price` / `max_price` | no | plausible range; readings outside it are treated as garbage |
| `target_price` | no | alert when the price crosses it (default: `DEFAULT_TARGET_PRICE`, 3000) |
| `price_mode` | no | `"cash"` (default) or `"installments"`, for providers that can read both |
| `coupon_terms` | no | words that must all appear in a deal title to alert about it (`"a\|b"` = a or b) |
| `deal_store` | no | only deals from this store |
| `search_term` | no | free-text query, for providers whose store search can't find a model code |
| `enabled` | no | `false` pauses the target but keeps it and its history |

A store name is either a **registered provider** (its `store` attribute) or anything else, in which case the **generic provider** reads the URL you give.

### Recipes

**A product in one store.** No provider is needed if the page has schema.org data.

```json
{
  "key": "headphones",
  "name": "Acme Headphones H100",
  "model": "ACME-H100",
  "store": "SomeShop",
  "urls": { "SomeShop": "https://www.example.com/product/123/acme-h100" },
  "min_price": 200, "max_price": 2000, "target_price": 400
}
```

**The same product everywhere you can reach.** `store: ""` checks every store you give a URL for, plus every provider that can search by model. Each store keeps its own history and low.

```json
{
  "key": "ram_everywhere",
  "name": "DDR4 2x8GB kit",
  "model": "F4-3200C16D-16GTZN",
  "store": "",
  "urls": {
    "SomeShop": "https://www.example.com/product/456/f4-3200c16d-16gtzn",
    "OtherShop": "https://shop.example.org/p/f4-3200c16d-16gtzn"
  }
}
```

**Deals only.** No product page, just "tell me when a good deal shows up" in the deal sources you plugged in. Deals with a coupon always alert; without one, only at or below `target_price`.

```json
{
  "key": "glass_containers",
  "name": "10-piece glass food container set",
  "store": "",
  "urls": {},
  "target_price": 100,
  "coupon_terms": ["glass", "10 piece|10-piece", "300ml|370ml|400ml|500ml"]
}
```

Add `coupon_terms` to a product target to watch deals for it as well.

**Done with it?** Add `"enabled": false` instead of deleting. The URLs and the price history stay there for next time.

## Writing a provider

A provider teaches Deal Rover one store. Create a file in `providers/`: every module there is loaded automatically, except those starting with `_`. [`providers/_example.py`](providers/_example.py) is a commented template to copy.

```text
providers/
├── __init__.py     loader (finds the plugins), don't edit
├── base.py         THE INTERFACE: Provider, DealSource, Target, Deal, helpers
├── _example.py     template (ignored by the loader)
├── mystore.py      ← your providers, one file per store
└── mydeals.py      ← your deal sources
```

Your own plugin files are **git-ignored by default** (only the three files above are versioned), so the code that knows your stores stays on your machine. Delete the `providers/*` lines from `.gitignore` if you want to commit them.

### The interface

`Provider.lookup(target)` runs a cascade and stops at the first valid reading:

1. **`lookup_api(target)`**: the store's JSON API, if it has one.
2. **fixed URL**: the target's URL for this store, read by `extract(html, target)`.
3. **search**: `search_url(model)` + `parse_search_for_url(html, model)`, only when `allows_search = True`. It rediscovers a product whose URL changed.

Override only what the store needs:

| Hook | Default | Override when |
|---|---|---|
| `store` | none | always: the name used in `targets.json` |
| `allows_search` | `False` | the store's `robots.txt` allows its search route and you implemented the two search hooks |
| `lookup_api(target) -> LookupResult \| None` | `None` | the store has a JSON API (often the one its own page calls; look at DevTools → Network) |
| `extract(html, target) -> ProductState` | schema.org JSON-LD, then heuristics | the page has no structured data, or the generic heuristics pick the wrong price |
| `search_url(model)` / `parse_search_for_url(html, model)` | `None` | you enabled search |

The smallest useful provider is two lines:

```python
from providers.base import Provider

class SomeShopProvider(Provider):
    store = "SomeShop"
```

A store whose pages sometimes come back as a login wall or anti-bot page that *looks* like a product page should subclass `SchemaOrgProvider` instead: it treats a page without a price as an invalid reading.

### Rules of thumb

- **When unsure, return `None` or an invalid `ProductState`** (`valid=False`). A wrong reading becomes a false alert; a missing one only waits for the next run, and the previous state is kept.
- **Confirm the model** in API and search results with `title_matches_model_exact` (strict: tells `ABC/8` from `ABC/16`) or `model_matches` (loose: ignores case and separators). Searches return neighbors and accessories.
- **Check the price range** with `price_ok(price, target)` when the price comes from your own parser or API. Only the default `extract()` does it for you.
- **Call `super().extract()` first** inside your own `extract()`: you inherit broken-page and captcha detection.
- **Use `core.fetch_page(url)`** to download pages: browser headers, a `cloudscraper` fallback for JavaScript challenges and block detection come with it.
- **Non-price statuses** go in `ProductState.seller` (free text, shown next to the store). Every change of it alerts, even when `available` stays the same.
- **Respect `robots.txt`**: no search where the store forbids it.

### Deal sources

A `DealSource` finds coupons and deals somewhere (an aggregator, a store's coupon page, a feed) and returns `Deal`s:

```python
from providers.base import DealSource, Deal

class MyFeed(DealSource):
    name = "my-feed"

    def deals(self, targets):
        if not any(t.get("coupon_terms") for t in targets):
            return []                 # nobody wants deals: skip the request
        return [Deal(id="my-feed:42", title="Acme H100 headphones", store="SomeShop",
                     url="https://...", price=349.90, codes=("SAVE10",))]
```

`deals.py` does the rest: it matches each deal title against the targets' `coupon_terms` (and `deal_store`), skips deal ids it already announced, and builds the message. A `Deal` with `store_wide=True` is a coupon for a whole store: it is announced when any enabled target uses that store, with no keyword match. Give each deal a **stable, prefixed `id`**: it is what keeps it from being announced twice. A source that raises is logged and skipped; the others still run.

## Customizing the alert messages

Every text comes from a template in [`messages.py`](messages.py). To change any of them, or to translate them all, create a `messages.json` with only the keys you want:

```json
{
  "back_in_stock": "✅ It's back in stock!",
  "price_down": "📉 Price drop: {old} → {new}",
  "deal_disclaimer": ""
}
```

- `{name}`, `{price}`, `{old}`… are filled in for you; values are HTML-escaped, and you can use Telegram's HTML tags (`<b>`, `<code>`…).
- An empty template (`""`) removes that line.
- A broken template (say, a typo in a placeholder) never loses an alert: it is logged and the default text is used.
- [`messages.example.json`](messages.example.json) lists every key with its placeholders: `cp messages.example.json messages.json` and edit.

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `TELEGRAM_TOKEN` | none | bot token; without it, alerts only go to the log |
| `TELEGRAM_CHAT_ID` | none | chat that receives the alerts |
| `DEFAULT_TARGET_PRICE` | `3000` | target price for targets without `target_price` |
| `MONITOR_TARGETS_FILE` | `targets.json` | targets file |
| `MONITOR_STATE_FILE` | `state.json` | where readings are remembered |
| `MONITOR_LOG_FILE` | `monitor.log` | log file |
| `MONITOR_MESSAGES_FILE` | `messages.json` | custom alert texts |
| `CURRENCY_FORMAT` | `R$ {}` | how prices are shown; `{}` is the amount |
| `DECIMAL_SEPARATOR` | `,` | decimal separator used by the stores and in the alerts (the other of `.`/`,` is the thousands separator) |
| `ACCEPT_LANGUAGE` | `pt-BR,pt;q=0.9,en;q=0.8` | language sent to the stores; many choose language and currency from it |

Paths default to the project folder, so the scheduler can run it from anywhere.

**Currency.** The default is the Brazilian real (`R$ 1.234,56`). For other stores, set the format they use:

```bash
# US dollar: $1,234.56
export CURRENCY_FORMAT='${}' DECIMAL_SEPARATOR='.' ACCEPT_LANGUAGE='en-US,en;q=0.9'
# Euro: 1.234,56 €
export CURRENCY_FORMAT='{} €' DECIMAL_SEPARATOR=','
```

The format is used both to read prices from pages (when there is no structured data) and to write them in the alerts. The availability heuristics understand English and Portuguese words; a provider can always decide availability itself.

**A second schedule** (e.g. something that must be checked every 10 minutes) needs its **own** targets, state and log files. Two schedules writing the same `state.json` would lose each other's updates:

```bash
#!/bin/bash
# run_frequent.sh
DIR="$(dirname "$0")"
sleep $((RANDOM % 90))   # random delay on every run
export MONITOR_TARGETS_FILE="$DIR/targets_frequent.json"
export MONITOR_STATE_FILE="$DIR/state_frequent.json"
export MONITOR_LOG_FILE="$DIR/frequent.log"
exec "$DIR/run.sh"
```

## Usage

```bash
./.venv/bin/python monitor.py          # menu: add, list, remove, check now
./.venv/bin/python monitor.py check    # one check (what the scheduler runs)
```

## Scheduling

Run it **a few times a day**, not every few minutes: more requests means more chances of being blocked.

### Linux (cron)

```cron
# 4x a day, with up to 45 min of random delay
0 9,13,18,22 * * * sleep $(shuf -i 0-2700 -n 1); /home/USER/deal-rover/run.sh >> /home/USER/deal-rover/cron.log 2>&1
```

### Windows + WSL2 (Task Scheduler)

Cron inside WSL doesn't work on its own: WSL shuts the distro down soon after the last terminal closes. Let Windows start it instead. In an **administrator** PowerShell, once:

```powershell
$action = New-ScheduledTaskAction -Execute "wsl.exe" `
    -Argument "-d Ubuntu -- /home/USER/deal-rover/run.sh"
$triggers = "09:00","13:00","18:00","22:00" | ForEach-Object {
    New-ScheduledTaskTrigger -Daily -At $_ -RandomDelay (New-TimeSpan -Minutes 30)
}
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 1) `
    -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType S4U
Register-ScheduledTask -TaskName "DealRover" -Action $action -Trigger $triggers `
    -Settings $settings -Principal $principal
```

For a frequent schedule, use a trigger that repeats **every 10 minutes indefinitely** (`New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 10)`), optionally with an expiration date.

Things that are easy to miss:

- **Battery:** by default the Task Scheduler silently skips tasks on battery. The `-AllowStartIfOnBatteries` flag (or unticking "Start only if on AC power") fixes it.
- **Sleep:** nothing runs while the PC sleeps. `-StartWhenAvailable` runs a missed check once when it wakes up. To wake the PC for it, you need both "Wake the computer to run this task" **and** wake timers enabled in the power plan.
- **The task list doesn't refresh itself:** press F5 to see the real status. `0x0` is success.
- **A check doesn't log an "end" line.** The last log line is the last target; the modification time of `state.json` (saved at the end) tells you it finished.

## How it works

```
targets.json ──► monitor.py ──► providers/ (your plugins, or the generic one) ──► ProductState
                     │                                                               │
                     │                 state.json (last reading per store) ◄─────────┤
                     │                                                               ▼
                     └──► deals.py ◄── DealSources          core.build_alert ──► messages.py ──► Telegram
```

1. Each target is expanded into one check per store.
2. The store's **provider** runs its cascade: API → fixed URL → search.
3. The page is validated: block pages, captchas, login walls and out-of-range prices make the reading **invalid**: the previous state is kept and nothing is sent. "I couldn't read it" is never confused with "it's gone".
4. A valid reading is compared with the saved one; alerts are built from the templates and sent.
5. Deals are collected once per run from every deal source.

The design decisions are in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Project structure

```
core.py          fetching, extraction, broken-page detection, state, Telegram, alert rules
deals.py         matches deals to targets, announces each once
messages.py      alert templates (overridable via messages.json)
monitor.py       CLI menu and the `check` command
providers/       the provider interface, a template, and your plugins
run.sh.example   template for run.sh (credentials)
tests/           pytest suite (no network access)
docs/            architecture notes
```

## Tests

```bash
./.venv/bin/pip install -r requirements-dev.txt
./.venv/bin/python -m pytest
```

The suite never touches the network: price parsing, broken-page detection (including the Cloudflare false positive), the alert state machine, model matching, the plugin interface and the message templates are tested with hand-built inputs.

## Responsible use

This is a personal tool. Keep the request rate low, respect each site's `robots.txt` (only implement search where it is allowed) and check its terms of use before monitoring it. When a site blocks a request, the reading is simply discarded until the next scheduled run. Don't add retries or rotating identities.

## License

[MIT](LICENSE)
