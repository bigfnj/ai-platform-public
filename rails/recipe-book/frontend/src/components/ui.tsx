import { useState } from "react";
import type { ReactNode } from "react";
import { iconUrl } from "../api";
import type { RecipeSummary } from "../types";

export function Spinner() {
  return <span className="spinner" />;
}

/** "1 step", "2 steps": a count with its noun, singular for exactly one. The card meta read
 *  "1 steps" on every one-step recipe. */
export function plural(n: number, one: string, many = `${one}s`): string {
  return `${n} ${n === 1 ? one : many}`;
}

const CAT_GLYPH: Record<string, string> = {
  Beverages: "🍸", Breakfast: "🍳", Soups: "🍲", Pasta: "🍝", Ramen: "🍜",
  "Salads & Dressings": "🥗", Desserts: "🍰", Baking: "🥐", BBQ: "🍖", Smoker: "🍖",
  "Crock Pot": "🍲", Entrees: "🍽️", "Side Dishes": "🥔", Hummus: "🥣",
  Preserves: "🫙", Yonanas: "🍦", "Sauces, Rubs, Marinades": "🥫", "Date Night": "🕯️",
};

export function RecipeIcon({ r }: { r: RecipeSummary }) {
  const [failed, setFailed] = useState(false);
  const glyph = r.kind === "beverage" ? "🍸" : (CAT_GLYPH[r.category] ?? "🍽️");
  if (r.icon_status !== "ready" || failed) {
    return <span className="rx-icon">{glyph}</span>;
  }
  return (
    <span className="rx-icon">
      <img src={iconUrl(r.id)} alt="" loading="lazy" onError={() => setFailed(true)} />
    </span>
  );
}

// An empty star is an outline (☆), not a filled star in a faint colour: it reads as unset by
// its shape in every palette, and theme.css paints it --muted so it still reaches 3:1.
export function Stars({ value, onSet }: { value: number; onSet?: (n: number) => void }) {
  return (
    <span className="stars">
      {[1, 2, 3, 4, 5].map((n) => (
        <span key={n} className={n <= value ? "" : "off"}
          style={onSet ? { cursor: "pointer" } : undefined}
          onClick={onSet ? (e) => { e.stopPropagation(); onSet(n === value ? 0 : n); } : undefined}>
          {n <= value ? "★" : "☆"}
        </span>
      ))}
    </span>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}
