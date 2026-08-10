"""Platform API tier: a FastAPI JSON surface over the recipe-book core.

The gateway proxies ``/recipe-book/api/*`` here. Every route lives under ``/api/*``.
Blocking work (sqlite, broker round-trips) runs in sync path operations, which
FastAPI executes in a threadpool, so the event loop stays free.

AI is buffered by design (see ``recipe_book.broker``): the assistant endpoints run
the model to completion and return the full result rather than streaming.
"""
from __future__ import annotations

from fastapi import BackgroundTasks, Depends, FastAPI

from recipe_book import broker, config, db, icons, ingest, maintenance, seed, semantic, state
from recipe_book.api import deps
from recipe_book.api.routers import (
    assistant, authoring, bar, gtasks, pantry, personalization, planner, recipes, settings,
)
from recipe_book.api.routers import icons as icon_routes

def create_api() -> FastAPI:
    # Ensure the DB + schema exist and load the in-memory catalog once at startup.
    config.ensure_dirs()
    con = db.connect()
    try:
        db.init_db(con)
        # If token encryption is now configured, encrypt any legacy plaintext refresh tokens.
        db.encrypt_gtasks_tokens_at_rest(con)
        # First run on an empty volume: hydrate the corpus from the bundled seed, then build
        # the catalog from it. Also covers a present-corpus/empty-DB case. A populated install
        # (existing rows) is left untouched — no seeding, no re-ingest.
        if con.execute("SELECT COUNT(*) FROM recipes").fetchone()[0] == 0:
            seed.hydrate_if_empty()
            ingest.ingest(con)
            con.commit()
            # Unpack the bundled seed icons (fresh volume) so the corpus ships illustrated
            # without an image GPU; mark those recipes ready.
            if seed.hydrate_icons():
                icons.reconcile(con)
                con.commit()
    finally:
        con.close()
    state.reload()
    semantic.load()  # load the cached semantic index if present (else search is lexical)

    app = FastAPI(title="recipe-book", version="0.1.0",
                  docs_url="/api/docs", openapi_url="/api/openapi.json")

    @app.get("/api/health")
    def health() -> dict:
        return {"ok": True, "broker": broker.up(), **state.catalog().stats()}

    @app.get("/api/whoami")
    def whoami(ident: deps.Identity = Depends(deps.identity)) -> dict:
        """The gateway-verified caller: drives the admin user picker in the UI."""
        return {"user": ident.user, "is_admin": ident.is_admin}

    @app.get("/api/users")
    def users(ident: deps.Identity = Depends(deps.require_admin)) -> dict:
        """Platform usernames with data, for the admin's 'view as user' dropdown."""
        con = db.connect()
        try:
            return {"users": db.list_users(con)}
        finally:
            con.close()

    for module in (recipes, authoring, personalization, planner, pantry, bar, assistant,
                   settings, icon_routes, gtasks):
        app.include_router(module.router)

    @app.post("/api/rebuild")
    def rebuild(_: deps.Identity = Depends(deps.require_admin)) -> dict:
        """Re-parse the whole markdown corpus into a fresh recipes table (disaster
        recovery / after a big source refresh), then reload the catalog. Admin-only: it's a
        global, expensive op (matches the admin gating on the icon-authoring routes)."""
        con = db.connect()
        try:
            stats = ingest.rebuild(con)
            icons.reconcile(con)  # keep icons that survived the rebuild marked ready
        finally:
            con.close()
        state.reload()
        return stats

    @app.get("/api/search/status")
    def search_status() -> dict:
        return semantic.status()

    @app.post("/api/search/reindex")
    def search_reindex(background: BackgroundTasks,
                       _: deps.Identity = Depends(deps.require_admin)) -> dict:
        """Build the semantic index (embed every recipe via the broker) in the
        background, then reload it. Poll /api/search/status. Admin-only: it drives the shared GPU
        broker, so an open trigger would let any user stack embedding runs on it (the central
        scheduler fires this with system-admin headers)."""
        def job():
            try:
                semantic.build(state.catalog())
            except Exception:
                pass
        background.add_task(job)
        return {"queued": True, **semantic.status()}

    @app.post("/api/maintenance/purge")
    def maintenance_purge(_: deps.Identity = Depends(deps.require_admin)) -> dict:
        """Trim meal-plan entries past the retention window. Admin-only; the central scheduler
        fires this daily with system-admin headers (this replaced the rail's nightly loop)."""
        return {"purged": maintenance.run_purge()}

    return app
