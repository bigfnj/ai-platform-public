"""recipe-book's checks: the assistant reply rendered inert, the stylesheet kept inside the rail, an
admin's view-as ending with the module, the planner, Kitchen paging, favourites, search note and
Add recipe fixes, and four layout and wording checks (counts, unset controls, the Bar facet
heading, the Shopping tab's width). recipe_api also answers for the rail the shell checks mount
(railchecks/shell.py).
"""
from __future__ import annotations

from datetime import date

from .harness import (PAYLOAD, Api, NoFixture, assert_inert, css_leaks, each_theme, glyph_contrast, mount,
                      rail, serve)


def recipe_api(payload: str, admin: bool = True, n: int = 1, search: dict | None = None,
               to_try: int = 0) -> Api:
    summary = {"id": "r1", "title": "Test Soup", "category": "Soups", "kind": "meal", "ingredient_count": 1,
               "step_count": 1, "is_collection": False, "base_spirits": [], "primary_spirit": "", "glass": "",
               "technique": "", "icon_status": "none", "favorite": False, "rating": None, "tags": []}
    detail = dict(summary, meta="", source="", ingredients=["1 onion"], instructions=["Cook it."],
                  shopping_list=[], extra_sections=[], rel_path="soups/test.md", attributes=[],
                  attributes_auto=[])

    def api(method, path, q, body):
        if path == "/api/capabilities":
            return {"broker": "ok", "models": []}
        if path == "/api/settings":
            return {"plan_retention_days": 30, "plan_recency_days": 14, "is_admin": admin,
                    "defaults": {"plan_retention_days": 30, "plan_recency_days": 14},
                    "ranges": {"plan_retention_days": [1, 365], "plan_recency_days": [1, 60]}}
        if path == "/api/users":
            return {"users": ["owner-two", "bob"]}
        if path == "/api/categories":
            return {"categories": [{"name": "Soups", "kind": "meal", "count": 1}]}
        if path == "/api/spirits":
            return {"spirits": []}
        if path == "/api/recipes/duplicate_check" and method == "POST":
            return {"matches": []}
        if path == "/api/recipes" and method == "POST":
            return {"id": "r1", "category": body.get("category"), "kind": body.get("kind"),
                    "spirit": body.get("spirit", "")}
        if path == "/api/recipes":
            # The first `to_try` recipes sit in the "To Try" band, which the Kitchen page shows
            # apart from the grid, as the real catalog does.
            every = [dict(summary, id=f"r{i + 1}", title=f"Test Soup {i + 1}",
                          category="To Try" if i < to_try else "Soups") for i in range(n)]
            if (q.get("category") or [""])[0] == "To Try":
                band = [r for r in every if r["category"] == "To Try"]
                return {"items": band, "total": len(band), "limit": 200, "offset": 0}
            limit = int((q.get("limit") or ["200"])[0])
            return {"items": every[:limit], "total": n, "limit": limit, "offset": 0}
        if path == "/api/search/status":
            return search or {"built": True, "count": n, "model": "m"}
        if path == "/api/planner/propose" and method == "POST":
            return {"items": [
                {"date": d, "slot": "dinner", "ptype": "meal", "recipe_id": "r1", "title": "Test Soup 1",
                 "summary": summary, "why": "w"} for d in body["dates"][:2]]}
        if path == "/api/recipes/r1":
            return detail
        if path == "/api/assistant" and method == "POST":
            return {"markdown": payload, "mode": body.get("mode", "")}
        if path in ("/api/pantry", "/api/bar"):
            return {"items": [{"id": 1, "name": "onion", "kind": "on_hand"}]}
        if path in ("/api/pantry/match", "/api/bar/pour"):
            return {"results": [], "inventory": {}}
        if path == "/api/planner":
            entry = {"id": 1, "date": "" if q.get("tray") else "2026-01-05", "slot": "dinner",
                     "recipe_id": "r1", "title": "Test Soup", "servings": 2, "recipe": summary}
            return {"entries": [entry]}
        if path == "/api/planner/shopping":
            return {"items": [{"key": "onion", "label": "onion", "detail": "1", "sources": ["Test Soup"],
                               "checked": False}], "recipe_count": 1}
        if path == "/api/gtasks/status":
            return {"app_configured": False, "connected": False, "email": None, "list_title": ""}
        if path == "/api/tags":
            return {"tags": []}
        if path.startswith("/api/"):
            # Every other scoped read (planner, pantry, bar, shopping, tags): an empty answer is
            # enough for the views to render, and the call is still recorded for the owner check.
            return {}
        raise NoFixture(path)

    return api


def check_xss_recipe_book(page) -> list[str]:
    r = rail("recipe-book", recipe_api(PAYLOAD))
    serve(page, r)
    mount(page, r)
    page.click(".rb-card")
    page.get_by_role("button", name="Substitutions").click()
    problems = assert_inert(page, ".assist-out", "assistant reply")
    return problems + [f"unanswered: {u}" for u in r.unanswered]


def check_css_recipe_book(page) -> list[str]:
    return css_leaks(page, rail("recipe-book", recipe_api("ok")))


def check_rb_planner_double_accept(page) -> list[str]:
    """A double-click on "Accept all remaining" adds each pick once (the planner keeps dupes)."""
    page.add_init_script("""(() => { const f = window.fetch; window.fetch = (u, i) =>
      (i && i.method === 'POST' && /\\/api\\/planner(\\?|$)/.test(String(u)))
        ? new Promise((r) => setTimeout(() => r(f(u, i)), 400)) : f(u, i) })()""")
    r = rail("recipe-book", recipe_api("ok"))
    serve(page, r)
    mount(page, r)
    page.get_by_role("button", name="Plan", exact=True).click()
    page.get_by_role("button", name="Plan with AI").click()
    page.get_by_role("button", name="Build the plan").click()
    btn = page.get_by_role("button", name="Accept all remaining")
    btn.wait_for(timeout=5000)
    btn.dblclick()
    page.wait_for_timeout(1800)
    adds = [c for c in r.calls if c.path == "/api/planner" and c.method == "POST"]
    out = [] if len(adds) == 2 else [f"{len(adds)} plan entries added for 2 picks after a double-click"]
    # A row's own Accept, then "Accept all" while that add is still out (400 ms here): the pick
    # in flight must not be added a second time by the loop.
    page.get_by_role("button", name="Done").click()
    page.get_by_role("button", name="Plan with AI").click()
    page.get_by_role("button", name="Build the plan").click()
    page.get_by_role("button", name="Accept all remaining").wait_for(timeout=5000)
    r.calls.clear()
    page.locator(".ai-pick-btns button", has_text="Accept").first.click()
    page.get_by_role("button", name="Accept all remaining").click()
    page.wait_for_timeout(1800)
    adds = [c for c in r.calls if c.path == "/api/planner" and c.method == "POST"]
    if len(adds) != 2:
        out.append(f"{len(adds)} plan entries for 2 picks after a row Accept then Accept all")
    return out


def check_rb_kitchen_count(page) -> list[str]:
    """450 recipes, 5 of them in the To Try band: the header says 445 (not the 195 the first
    page holds outside the band), and Show more pages on."""
    r = rail("recipe-book", recipe_api("ok", n=450, to_try=5))
    serve(page, r)
    mount(page, r)
    page.wait_for_selector(".rb-card", timeout=8000)
    out = []
    head = page.inner_text(".rb-body .rb-count")
    if "445" not in head:
        out.append(f"header reads {head!r}, expected 445")
    try:
        page.get_by_role("button", name="Show more").click(timeout=3000)
        page.wait_for_function("document.querySelectorAll('.rb-card').length === 400", timeout=5000)
    except Exception:
        out.append(f"no next page: {page.locator('.rb-card').count()} cards shown")
    return out


def check_rb_fav_keeps_grid(page) -> list[str]:
    """A heart click refetches the list without blanking the grid, and does not re-read the
    category list; the screen reads the categories once (twice with a recipe open)."""
    page.add_init_script("""(() => { const f = window.fetch; window.fetch = (u, i) =>
      /\\/api\\/recipes\\?/.test(String(u)) && window.__slow ? new Promise((r) => setTimeout(() => r(f(u, i)), 800)) : f(u, i) })()""")
    r = rail("recipe-book", recipe_api("ok", n=30))
    serve(page, r)
    mount(page, r)
    page.wait_for_selector(".rb-card", timeout=8000)
    page.wait_for_timeout(500)
    cats0 = sum(1 for c in r.calls if c.path == "/api/categories")
    page.evaluate("window.__slow = true")
    page.locator(".rb-card .rb-fav").first.click()
    page.wait_for_timeout(450)          # the list refetch is out
    out = []
    if page.locator(".rb-card").count() == 0:
        out.append("the grid blanked behind a spinner while a favourite toggle refetched it")
    page.wait_for_timeout(800)
    cats1 = sum(1 for c in r.calls if c.path == "/api/categories")
    if cats0 != 1:
        out.append(f"{cats0} category reads on the Kitchen screen, expected 1")
    if cats1 != cats0:
        out.append(f"a favourite toggle re-read the categories ({cats1 - cats0} more)")
    return out


def check_rb_search_notice(page) -> list[str]:
    """A stale or failed AI index says so under the search box; a healthy one says nothing."""
    out = []
    for status, want in (
        ({"built": True, "count": 3, "model": "m", "stale": "built with bge-m3, @embed is now qwen3-embedding"}, "matching by keyword"),
        ({"built": True, "count": 3, "model": "m", "last_error": "BrokerError: 502"}, "rebuild failed"),
        ({"built": True, "count": 3, "model": "m"}, None),
    ):
        page.goto("about:blank")
        r = rail("recipe-book", recipe_api("ok", search=status))
        serve(page, r)
        mount(page, r)
        page.wait_for_selector(".rb-card", timeout=8000)
        page.wait_for_timeout(400)
        note = page.locator(".rb-search-note")
        text = note.inner_text() if note.count() else None
        if want and (not text or want not in text):
            out.append(f"{status}: note {text!r}, expected one saying {want!r}")
        if not want and text:
            out.append(f"healthy index, but the note says {text!r}")
    return out


def check_rb_add_spirit(page) -> list[str]:
    """Add recipe, Bar, Manual: the Base spirit picked is what POST /api/recipes carries, and
    an untouched picker sends "" (derive it) rather than a spirit nobody chose."""
    out = []
    for pick, want, footer in ((None, "", "spirit from the ingredients"), ("Mezcal", "Mezcal", "Mezcal")):
        page.goto("about:blank")
        r = rail("recipe-book", recipe_api("ok"))
        serve(page, r)
        mount(page, r)
        page.wait_for_selector(".rb-card", timeout=8000)
        page.locator(".rb-add").first.click()
        modal = page.locator(".add-recipe")
        modal.get_by_role("button", name="Bar", exact=True).click()
        modal.get_by_role("button", name="Manual", exact=True).click()
        modal.get_by_placeholder("e.g. Weeknight Green Curry").fill("Smoked Sour")
        modal.get_by_placeholder("2 oz gin").first.fill("2 oz tequila")
        modal.get_by_placeholder("Describe the step").first.fill("Shake.")
        if pick:
            modal.locator("select").select_option(pick)
        said = modal.locator(".rb-count").inner_text()
        if footer not in said:
            out.append(f"pick {pick!r}: the footer reads {said!r}, expected it to say {footer!r}")
        modal.get_by_role("button", name="Save recipe").click()
        page.wait_for_timeout(800)
        posts = [c.body for c in r.calls if c.path == "/api/recipes" and c.method == "POST"]
        sent = posts[0].get("spirit") if posts and isinstance(posts[0], dict) else None
        if len(posts) != 1 or sent != want:
            out.append(f"pick {pick!r}: {len(posts)} save(s), spirit sent {sent!r}, expected {want!r}")
    return out


def check_viewas_recipe_book(page) -> list[str]:
    """An admin's "view as" must not outlive the module: after a remount every request is the
    admin's own (no ?owner=) and the picker says so."""
    r = rail("recipe-book", recipe_api("ok", admin=True))
    serve(page, r)
    mount(page, r)
    page.wait_for_selector(".rb-viewas")
    page.select_option(".rb-viewas", "owner-two")
    page.get_by_role("button", name="Pantry").click()
    page.wait_for_timeout(500)
    acting = [c for c in r.calls if c.query.get("owner") == ["owner-two"]]
    problems = [] if acting else ["view-as never took effect, so the remount half proves nothing"]
    page.evaluate("window.__unmount()")
    r.calls.clear()
    page.evaluate("window.__mount()")
    page.wait_for_selector(".rb-viewas")
    page.get_by_role("button", name="Pantry").click()
    page.wait_for_timeout(500)
    leaked = sorted({f"{c.method} {c.path}" for c in r.calls if "owner" in c.query})
    if leaked:
        problems.append(f"after remount these still carried ?owner=: {leaked}")
    picker = page.eval_on_selector(".rb-viewas", "el => el.value")
    if picker != "":
        problems.append(f"after remount the picker reads {picker!r}, not My data")
    if not r.calls:
        problems.append("no request after the remount, so nothing was checked")
    return problems


def check_rb_card_counts(page) -> list[str]:
    """A one-step recipe's card read '1 steps'. A count of one is singular and every other
    count plural, on the card meta and in the Kitchen header."""
    base = recipe_api("ok")
    out: list[str] = []
    for items, header in (([(1, 1)], "1 recipe"), ([(1, 1), (2, 3)], "2 recipes")):
        def api(method, path, q, body, items=items):
            got = base(method, path, q, body)
            if path == "/api/recipes" and got.get("items"):
                cards = [dict(got["items"][0], id=f"r{i + 1}", title=f"Dish {i + 1}",
                              ingredient_count=ing, step_count=st) for i, (ing, st) in enumerate(items)]
                return dict(got, items=cards, total=len(cards))
            return got

        page.goto("about:blank")
        r = rail("recipe-book", api)
        serve(page, r)
        mount(page, r)
        page.wait_for_selector(".rb-card", timeout=8000)
        page.wait_for_timeout(250)
        metas = page.eval_on_selector_all(
            ".rb-card .meta", "els => els.map((e) => e.innerText.replace(/\\s+/g, ' ').trim())")
        want = [f"{ing} ingredient{'' if ing == 1 else 's'} {st} step{'' if st == 1 else 's'}"
                for ing, st in items]
        if metas != want:
            out.append(f"card meta reads {metas}, expected {want}")
        head = page.inner_text(".rb-body .rb-count").strip()
        if head != header:
            out.append(f"the Kitchen header reads {head!r}, expected {header!r}")
        out += [f"unanswered: {u}" for u in r.unanswered]
    return out


def check_rb_unset_controls(page) -> list[str]:
    """An unset favourite heart, an empty rating star and an unticked ingredient box were
    painted in the border colour, 1.3-1.4:1 against the card in dark mode, so the controls could
    not be found. Each must reach 3:1 (WCAG 1.4.11) in every palette and mode, measured from the
    pixels: the heart on its card, and the stars, heart and box in the open recipe. They are thin
    outlines, mostly antialiased edge, so the strongest tenth of their ink (p90) is the glyph's
    core and is what is held to 3:1; in the border colour all 42 themes failed it. An unset
    heart is also a different glyph from a set one, so it does not lean on colour alone."""
    base = recipe_api("ok", n=2)

    def api(method, path, q, body):
        got = base(method, path, q, body)
        if path == "/api/recipes" and len(got.get("items", [])) == 2:   # the second is a favourite
            return dict(got, items=[got["items"][0], dict(got["items"][1], favorite=True)])
        return got

    r = rail("recipe-book", api)
    serve(page, r)
    mount(page, r)
    page.wait_for_selector(".rb-card .rb-fav.on", timeout=8000)
    out: list[str] = []
    unset, on = (page.inner_text(s).strip() for s in (".rb-card .rb-fav:not(.on)", ".rb-card .rb-fav.on"))
    if unset == on:
        out.append(f"an unset heart on a card draws the same glyph as a set one ({unset!r})")
    page.locator(".rb-card h3").first.click()
    in_modal = page.locator(".rb-modal .rb-fav:not(.on)").first.inner_text().strip()
    if in_modal == on:
        out.append(f"an unset heart in the recipe draws the set glyph ({on!r})")
    page.locator(".rb-modal .close").first.click()
    page.wait_for_selector(".rb-modal", state="detached", timeout=4000)
    heart = page.locator(".rb-card .rb-fav:not(.on)").first

    def measure(pal: str, mode: str) -> list[str]:
        probs = []
        got = glyph_contrast(page, heart)
        if got["ink"] < 8 or got["p90"] < 3:
            probs.append(f"unset heart on a card: {got['p90']:.2f}:1 over {got['ink']} ink px")
        page.locator(".rb-card h3").first.click()
        page.wait_for_selector(".rb-modal .stars", timeout=8000)
        # The box is all border, so it is measured as its border box.
        for label, sel, whole in (("empty stars in the recipe", ".rb-modal .stars", False),
                                  ("unset heart in the recipe", ".rb-modal .rb-fav:not(.on)", False),
                                  ("unticked ingredient box", ".rb-modal .ing-row:not(.on) .box", True)):
            got = glyph_contrast(page, page.locator(sel).first, whole)
            if got["ink"] < 8 or got["p90"] < 3:
                probs.append(f"{label}: {got['p90']:.2f}:1 over {got['ink']} ink px")
        page.locator(".rb-modal .close").first.click()
        page.wait_for_selector(".rb-modal", state="detached", timeout=4000)
        return probs

    out += each_theme(page, measure)
    return out + [f"unanswered: {u}" for u in r.unanswered]


def check_rb_facet_heading(page) -> list[str]:
    """On the Bar tab the facet heading 'Base spirit' broke onto two lines with its heart
    pushed aside: the facet-button rule (width: 100%) also matched the heart in the heading.
    Both headings fit on one line with the heart right after the words."""
    base = recipe_api("ok")

    def api(method, path, q, body):
        if path == "/api/spirits":
            return {"spirits": [{"name": "Gin", "count": 3}, {"name": "Non-Alcoholic", "count": 2}]}
        return base(method, path, q, body)

    r = rail("recipe-book", api)
    serve(page, r)
    mount(page, r)
    page.wait_for_selector(".rb-card", timeout=8000)
    out: list[str] = []
    for tab in ("Kitchen", "Bar"):
        page.get_by_role("button", name=tab, exact=True).click()
        page.wait_for_timeout(400)
        lines, gap = page.eval_on_selector(".rb-rail h4", """(h) => {
          const text = [...h.childNodes].find((n) => n.nodeType === 3 && n.textContent.trim());
          const range = document.createRange(); range.selectNodeContents(text);
          const rects = [...range.getClientRects()];
          const words = rects[rects.length - 1], heart = h.querySelector('.fav-head').getBoundingClientRect();
          return [new Set(rects.map((q) => Math.round(q.top))).size, Math.round(heart.left - words.right)];
        }""")
        if lines != 1:
            out.append(f"{tab}: the facet heading takes {lines} lines")
        if not 0 <= gap <= 16:
            out.append(f"{tab}: the heart sits {gap}px from the heading's words")
    return out + [f"unanswered: {u}" for u in r.unanswered]


def check_rb_shopping_width(page) -> list[str]:
    """The Shopping tab was capped at 680px, so at 1440 its right half stood empty and
    'Connect Google Tasks' floated mid-page. It spans the tab, the button sits at its right
    edge, and the list runs in more than one column."""
    base = recipe_api("ok")
    today = date.today().isoformat()

    def api(method, path, q, body):
        if path == "/api/gtasks/status":
            return {"app_configured": True, "connected": False, "email": None, "list_title": ""}
        if path == "/api/planner":
            return {"entries": [{"id": 1, "date": today, "slot": "dinner", "recipe_id": "r1",
                                 "title": "Test Soup", "servings": 2}]}
        if path == "/api/planner/shopping":
            return {"items": [{"key": f"k{i}", "label": f"item {i}", "detail": "1", "sources": ["Test Soup"],
                               "checked": False} for i in range(8)], "recipe_count": 1}
        return base(method, path, q, body)

    page.set_viewport_size({"width": 1440, "height": 900})
    r = rail("recipe-book", api)
    serve(page, r)
    mount(page, r)
    page.wait_for_selector(".rb-card", timeout=8000)
    page.get_by_role("button", name="Shopping", exact=True).click()
    page.wait_for_selector(".shop-row", timeout=8000)
    got = page.evaluate("""() => {
      const body = document.querySelector('.rb-body'), cs = getComputedStyle(body);
      const inner = body.getBoundingClientRect().width - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight);
      const view = body.firstElementChild.getBoundingClientRect();
      const btn = [...document.querySelectorAll('.rb-body button')].find((b) => /Connect Google Tasks/.test(b.textContent));
      const tops = new Set([...document.querySelectorAll('.shop-row')].map((e) => Math.round(e.getBoundingClientRect().top)));
      const rows = document.querySelectorAll('.shop-row').length;
      return { inner, view: view.width, viewRight: view.right,
               btnRight: btn ? btn.getBoundingClientRect().right : null, perRow: rows / tops.size };
    }""")
    out: list[str] = []
    if got["view"] < 0.97 * got["inner"]:
        out.append(f"the Shopping tab is {got['view']:.0f}px wide in a {got['inner']:.0f}px tab")
    if got["btnRight"] is None:
        out.append("no Connect Google Tasks button, so its place was not checked")
    elif got["viewRight"] - got["btnRight"] > 2:
        out.append(f"Connect Google Tasks ends {got['viewRight'] - got['btnRight']:.0f}px short of the tab's right edge")
    if got["perRow"] < 2:
        out.append(f"the list runs in one column ({got['perRow']:.1f} items a row) at 1440px")
    return out + [f"unanswered: {u}" for u in r.unanswered]


#: This module's checks: name -> function. railcheck.py's RAILS names each one, fixes its
#: place in --list and says which frontends it mounts.
CHECKS = {
    "xss-recipe-book": check_xss_recipe_book,
    "viewas-recipe-book": check_viewas_recipe_book,
    "css-recipe-book": check_css_recipe_book,
    "rb-planner-double-accept": check_rb_planner_double_accept,
    "rb-kitchen-count": check_rb_kitchen_count,
    "rb-fav-keeps-grid": check_rb_fav_keeps_grid,
    "rb-search-notice": check_rb_search_notice,
    "rb-add-spirit": check_rb_add_spirit,
    "rb-card-counts": check_rb_card_counts,
    "rb-unset-controls": check_rb_unset_controls,
    "rb-facet-heading": check_rb_facet_heading,
    "rb-shopping-width": check_rb_shopping_width,
}
