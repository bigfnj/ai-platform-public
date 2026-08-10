import { useEffect, useState } from "react";
import type { MouseEvent } from "react";
import { api } from "../api";
import { RecipeGrid } from "../components/RecipeGrid";
import { Empty, Spinner } from "../components/ui";
import type { RecipeList, RecipeSummary } from "../types";

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

  useEffect(() => {
    const t = setTimeout(() => {
      const q: Record<string, unknown> = { kind, limit: 200 };
      if (query) q.q = query;
      if (semantic && query.trim()) q.semantic = true;
      if (favOnly) q.fav = true;
      if (facet) { if (kind === "beverage") q.spirit = facet; else q.category = facet; }
      setList(null);
      api.recipes(q).then(setList).catch(() => setList({ items: [], total: 0, limit: 0, offset: 0 }));
    }, 180);
    return () => clearTimeout(t);
  }, [kind, query, semantic, facet, favOnly, refresh]);

  // "To Try" (untested) recipes — surfaced in their own band on the Kitchen main page.
  useEffect(() => {
    if (kind === "meal")
      api.recipes({ kind, category: "To Try", limit: 200 }).then((r) => setUntested(r.items)).catch(() => setUntested([]));
    else setUntested([]);
  }, [kind, refresh]);

  const noun = kind === "beverage" ? "cocktails" : "recipes";
  // "To Try" is pinned to the bottom of the rail under an "Untested" separator.
  const railFacets = facets.filter((f) => f.name !== "To Try");
  const toTryFacet = facets.find((f) => f.name === "To Try");
  const showUntested = kind === "meal" && facet === null && !favOnly && untested.length > 0;
  const mainItems = showUntested && list ? list.items.filter((r) => r.category !== "To Try") : list?.items ?? [];
  const count = showUntested ? mainItems.length : list?.total ?? 0;
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
          : mainItems.length === 0 ? <Empty>No {noun}{favOnly ? " favorited yet" : " match"}.</Empty>
          : <>
              <div className="rb-count" style={{ marginBottom: 12 }}>
                {favOnly ? "♥ " : ""}{count} {noun}
              </div>
              <RecipeGrid items={mainItems} onOpen={onOpen} onFav={onFav} />
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
