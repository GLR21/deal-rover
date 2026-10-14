# -*- coding: utf-8 -*-
"""
Plugin folder: store providers and deal sources.

Every module here is imported once; each class DEFINED in it that subclasses
`Provider` with a `store` set, or subclasses `DealSource`, is registered.
Modules whose name starts with "_" are skipped (templates, shared helpers),
and so are classes with an empty `store` (bases meant to be subclassed).

A module that fails to import is logged and skipped: one broken plugin never
stops the others.
"""

import logging
import pkgutil
import importlib
from functools import lru_cache

from providers.base import Provider, DealSource

log = logging.getLogger("deal_rover.providers")


@lru_cache(maxsize=1)
def _load() -> tuple:
    providers, sources = {}, []
    for info in sorted(pkgutil.iter_modules(__path__), key=lambda i: i.name):
        if info.name.startswith("_") or info.name == "base":
            continue
        try:
            module = importlib.import_module(f"{__name__}.{info.name}")
        except Exception:
            log.exception("Could not load providers/%s; skipped.", info.name)
            continue
        for obj in vars(module).values():
            if not isinstance(obj, type) or obj.__module__ != module.__name__:
                continue
            if issubclass(obj, Provider) and obj.store:
                if obj.store in providers:
                    log.warning("Store %r is defined twice; keeping the first.", obj.store)
                    continue
                providers[obj.store] = obj()
            elif issubclass(obj, DealSource) and obj is not DealSource:
                sources.append(obj())
    return providers, sources


def all_providers() -> dict:
    """{store name: provider instance} for every registered store."""
    return _load()[0]


def deal_sources() -> list:
    """Every registered DealSource instance."""
    return _load()[1]


def get_provider(store: str) -> Provider:
    """The store's provider, or the generic one (fixed URL only)."""
    provider = all_providers().get(store)
    if provider is None:
        log.info("    [%s] no provider registered; using the generic one "
                 "(fixed URL + schema.org).", store)
        provider = Provider()
        provider.store = store
    return provider
