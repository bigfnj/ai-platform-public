"""mutate_railcheck.py's mutations of recipe-book's frontend. NOT a test file.

Each names the check in railchecks/recipe_book.py that must fail. mutate_railcheck.py's header
says how a run works, and why this file ships exactly when its rail does.
"""
from __future__ import annotations

from . import Mutation

MUTATIONS = [
    Mutation("recipe-book's guard is inverted, so every reply renders as escaped text", "recipe-book",
             "lib/markdown.ts", "  if (!DOMPurify.isSupported) return", "  if (DOMPurify.isSupported) return",
             "xss-recipe-book"),
    Mutation("recipe-book no longer clears view-as on unmount", "recipe-book", "module.tsx",
             "  useEffect(() => () => setActingOwner(null), []);\n", "", "viewas-recipe-book"),
    # One of the fixed probes the audit measured (.empty: every empty panel on the platform).
    Mutation("recipe-book's .empty loses its wrapper again", "recipe-book", "theme.css",
             ".recipe-book .empty {", ".empty {", "css-recipe-book"),
    # The rail's own prefix is not exempt: meeting-atlas renders .ma .rb-block.
    Mutation("recipe-book's .rb-card loses its wrapper again", "recipe-book", "theme.css",
             ".recipe-book .rb-card {", ".rb-card {", "css-recipe-book"),
    # A class nobody listed: only the probes derived from the built stylesheet can see it.
    Mutation("recipe-book gains a new unscoped rule", "recipe-book", "theme.css",
             "@keyframes rb-spin", ".tag { margin: 3px; }\n@keyframes rb-spin", "css-recipe-book"),
    # recipe-book: planner double add, Kitchen paging, the grid kept on refetch, the search note.
    Mutation("a pick being added can be added again", "recipe-book", "components/AiPlannerModal.tsx",
             '    if (!r || r.status !== "pending" || adding.current.has(i)) return;\n',
             '    if (!r || r.status !== "pending") return;\n', "rb-planner-double-accept"),
    Mutation("Show more is never offered", "recipe-book", "views/BrowseView.tsx",
             "  const more = !!list && list.items.length < total;", "  const more = !!list && list.items.length < 0;",
             "rb-kitchen-count"),
    Mutation("every refetch blanks the grid again", "recipe-book", "views/BrowseView.tsx",
             "      if (listKey.current !== key) { listKey.current = key; setList(null); }\n",
             "      listKey.current = key; setList(null);\n", "rb-fav-keeps-grid"),
    Mutation("the search note is not rendered", "recipe-book", "module.tsx",
             '{notice && <div className="rb-search-note" role="status">⚠ {notice}</div>}',
             '{notice && null}', "rb-search-notice"),
    Mutation("the picked Base spirit is dropped on save again", "recipe-book", "components/AddRecipeModal.tsx",
             'category: catValue, spirit: kind === "beverage" ? spirit : "" });',
             'category: catValue });', "rb-add-spirit"),
    Mutation("an untouched picker forces Gin again", "recipe-book", "components/AddRecipeModal.tsx",
             'const [spirit, setSpirit] = useState("");', 'const [spirit, setSpirit] = useState("Gin");',
             "rb-add-spirit"),
    # Content notes from walking the UI: counts, unset controls, the Bar facet heading and the
    # Shopping tab's width. `plural.name ? "" : ""` keeps the helper read, since an unread import
    # fails tsc and the build with it (and tsc refuses a bare function in a condition, TS2774).
    Mutation("a one-step card reads '1 steps' again", "recipe-book", "components/RecipeGrid.tsx",
             '<span>{plural(r.step_count, "step")}</span>', "<span>{r.step_count} steps</span>",
             "rb-card-counts"),
    Mutation("the Kitchen header says '1 recipes' again", "recipe-book", "views/BrowseView.tsx",
             "{plural(count, noun)}", '{count} {noun}s{plural.name ? "" : ""}', "rb-card-counts"),
    Mutation("an unset heart is painted in the border colour again", "recipe-book", "theme.css",
             "  font-size: 17px; color: var(--muted); line-height: 1; }",
             "  font-size: 17px; color: var(--card-border); line-height: 1; }", "rb-unset-controls"),
    Mutation("empty stars are painted in the border colour again", "recipe-book", "theme.css",
             ".recipe-book .stars .off { color: var(--muted); }",
             ".recipe-book .stars .off { color: var(--card-border); }", "rb-unset-controls"),
    Mutation("an unticked ingredient box is outlined in the border colour again", "recipe-book",
             "theme.css", "border: 1.6px solid var(--muted);", "border: 1.6px solid var(--card-border);",
             "rb-unset-controls"),
    Mutation("a card's unset heart is the filled glyph again", "recipe-book", "components/RecipeGrid.tsx",
             '{r.favorite ? "♥" : "♡"}</button>', "♥</button>", "rb-unset-controls"),
    Mutation("the recipe's unset heart is the filled glyph again", "recipe-book", "components/RecipeModal.tsx",
             'onClick={toggleFav}>{r.favorite ? "♥" : "♡"}</button>', "onClick={toggleFav}>♥</button>",
             "rb-unset-controls"),
    Mutation("the facet heart takes the facet buttons' full width again", "recipe-book", "theme.css",
             "\n  width: auto; flex: none; display: inline-block; }", " }", "rb-facet-heading"),
    Mutation("the Shopping tab is capped at 680px again", "recipe-book", "views/ShoppingView.tsx",
             '<div className="shop-view">', '<div className="shop-view" style={{ maxWidth: 680 }}>',
             "rb-shopping-width"),
    Mutation("the shopping list runs in one column again", "recipe-book", "theme.css",
             ".recipe-book .shop-list { grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); "
             "column-gap: 10px; }", "", "rb-shopping-width"),
]
