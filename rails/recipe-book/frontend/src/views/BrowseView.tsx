import { useEffect, useRef, useState } from "react";
import type { MouseEvent } from "react";
import { api } from "../api";
import { RecipeGrid } from "../components/RecipeGrid";
import { Empty, Spinner, plural } from "../components/ui";
import type { RecipeList, RecipeSummary } from "../types";

const PAGE = 200;

export function BrowseView({ kind, query, semantic, onOpen, onFav, refresh }: {
  kind: "meal" | "beverage";
  query: string;
  semantic: boolean;
  onOpen: (id: string) => void;
  onFav: (id: string, e: MouseEvent) => void;
  refresh: number;
}) {
  const [facets, setFacets] = useState<{ name: string; count: number }[]>([]);
  const [facet, setFacet] = useState<string | null>(null);
  const [favOnly, setFavOnly] = useState(false);
  const [list, setList] = useState<RecipeList | null>(null);
  const [untested, setUntested] = useState<RecipeSummary[]>([]);
  // The grid loads PAGE recipes at a time and "Show more" asks for the next page. It used to be
  // one request capped at 200 with no way past it (841 recipes on disk), and the header count
  // came from that truncated page, so it read "~200 recipes".
  const [shown, setShown] = useState(PAGE);
  // Which query the current list answers. A favourite toggle or "Show more" refetches the same
  // query and keeps the grid on screen; only a NEW query blanks it behind the spinner. Every
  // refetch used to call setList(null), so one heart click remounted all 200 cards.
  const listKey = useRef("");

  useEffect(() => {
    setFacet(null);
    setFavOnly(false);
    // "To Try" is the staging bucket for contributions; it's a clickable category in the
    // rail (jump straight to the review queue) AND still previewed in the Untested band on
    // the "All" page.
    if (kind === "beverage") api.spirits().then((r) => setFacets(r.spirits)).catch(() => {});
    else api.categories().then((r) =>
      setFacets(r.categories.filter((c) => c.kind !== "beverage"))).catch(() => {});
  }, [kind]);

  const key = JSON.stringify([kind, query, semantic, facet, favOnly]);
  useEffect(() => { setShown(PAGE); }, [key]);
  useEffect(() => {
    const t = setTimeout(() => {
      const q: Record<string, unknown> = { kind, limit: shown };
      if (query) q.q = query;
      if (semantic && query.trim()) q.semantic = true;
      if (favOnly) q.fav = true;
      if (facet) { if (kind === "beverage") q.spirit = facet; else q.category = facet; }
      if (listKey.current !== key) { listKey.current = key; setList(null); }
      api.recipes(q).then(setList).catch(() => setList({ items: [], total: 0, limit: 0, offset: 0 }));
    }, 180);
    return () => clearTimeout(t);
  }, [key, shown, refresh]);

  // "To Try" (untested) recipes — surfaced in their own band on the Kitchen main page.
  useEffect(() => {
    if (kind === "meal")
      api.recipes({ kind, category: "To Try", limit: 200 }).then((r) => setUntested(r.items)).catch(() => setUntested([]));
    else setUntested([]);
  }, [kind, refresh]);

  const noun = kind === "beverage" ? "cocktail" : "recipe";
  // "To Try" is pinned to the bottom of the rail under an "Untested" separator.
  const railFacets = facets.filter((f) => f.name !== "To Try");
  const toTryFacet = facets.find((f) => f.name === "To Try");
  const showUntested = kind === "meal" && facet === null && !favOnly && untested.length > 0;
  const mainItems = showUntested && list ? list.items.filter((r) => r.category !== "To Try") : list?.items ?? [];
  // The whole match, not the page: the untested band is shown apart, so it comes off the total.
  const total = list?.total ?? 0;
  const count = showUntested ? Math.max(0, total - untested.length) : total;
  const more = !!list && list.items.length < total;
  return (
    <div className="rb-layout">
      <div className="rb-rail">
        <h4>
          {kind === "beverage" ? "Base spirit" : "Category"}
          <button className={"fav-head" + (favOnly ? " on" : "")} title="Show favorites only"
            onClick={() => setFavOnly((v) => !v)}>{favOnly ? "♥" : "♡"}</button>
        </h4>
        <button className={facet === null ? "on" : ""} onClick={() => setFacet(null)}>All</button>
        {railFacets.map((f) => (
          <button key={f.name} className={facet === f.name ? "on" : ""} onClick={() => setFacet(f.name)}>
            {f.name} <span>{f.count}</span>
          </button>
        ))}
        {toTryFacet && (
          <>
            <div className="rail-sep">Untested</div>
            <button className={facet === "To Try" ? "on" : ""} onClick={() => setFacet("To Try")}>
              {toTryFacet.name} <span>{toTryFacet.count}</span>
            </button>
          </>
        )}
      </div>
      <div>
        {!list ? <Empty><Spinner /> &nbsp;Loading…</Empty>
          : mainItems.length === 0 ? <Empty>No {noun}s{favOnly ? " favorited yet" : " match"}.</Empty>
          : <>
              <div className="rb-count" style={{ marginBottom: 12 }}>
                {favOnly ? "♥ " : ""}{plural(count, noun)}
              </div>
              <RecipeGrid items={mainItems} onOpen={onOpen} onFav={onFav} />
              {more && (
                <div style={{ textAlign: "center", marginTop: 18 }}>
                  <button className="btn ghost" onClick={() => setShown((n) => n + PAGE)}>
                    Show more ({list!.items.length} of {total})
                  </button>
                </div>
              )}
            </>}
        {showUntested && (
          <div style={{ marginTop: 28 }}>
            <div className="fav-sep untested"><span>⚗ Untested · To Try</span></div>
            <RecipeGrid items={untested} onOpen={onOpen} onFav={onFav} />
          </div>
        )}
      </div>
    </div>
  );
}
