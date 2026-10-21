import type { MouseEvent } from "react";
import { RecipeIcon, Stars, plural } from "./ui";
import type { RecipeSummary } from "../types";

export function RecipeGrid({ items, onOpen, onFav, showNeed }: {
  items: (RecipeSummary & { need?: string })[];
  onOpen: (id: string) => void;
  onFav: (id: string, e: MouseEvent) => void;
  showNeed?: boolean;   // render the single missing ingredient ("one away" section)
}) {
  return (
    <div className="rb-grid">
      {items.map((r) => (
        <div className="rb-card" key={r.id} onClick={() => onOpen(r.id)}>
          {/* Outline heart when unset, as the facet rail's favourites toggle already does. */}
          <button className={"rb-fav" + (r.favorite ? " on" : "")}
            title="Favorite" onClick={(e) => onFav(r.id, e)}>{r.favorite ? "♥" : "♡"}</button>
          <div className="rb-card-head">
            <RecipeIcon r={r} />
            <div>
              <div className="kicker">
                {r.kind === "beverage" ? (r.primary_spirit || "Cocktail") : r.category}
              </div>
              <h3>{r.title}</h3>
            </div>
          </div>
          <div className="meta">
            {r.kind === "beverage" ? (
              <>
                {r.glass && <span className="chip">{r.glass}</span>}
                {r.technique && <span className="chip ghost">{r.technique}</span>}
              </>
            ) : (
              <>
                <span>{plural(r.ingredient_count, "ingredient")}</span>
                <span>{plural(r.step_count, "step")}</span>
              </>
            )}
            {r.rating && <Stars value={r.rating.stars} />}
          </div>
          {showNeed && r.need && (
            <div className="rb-need">Need: <b>{r.need}</b></div>
          )}
        </div>
      ))}
    </div>
  );
}
