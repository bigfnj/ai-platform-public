"""In-memory recipe catalog, loaded from the DB at startup and rebuilt after a
re-import. Single worker, read-mostly, so a module global is fine.
"""
from __future__ import annotations

from recipe_book import db
from recipe_book.catalog import Catalog

_catalog: Catalog | None = None


def reload() -> Catalog:
    global _catalog
    con = db.connect()
    try:
        _catalog = db.load_catalog(con)
    finally:
        con.close()
    return _catalog


def catalog() -> Catalog:
    if _catalog is None:
        return reload()
    return _catalog
