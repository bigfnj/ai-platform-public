import { useEffect, useState } from "react";
import type { FormEvent, MouseEvent } from "react";
import { api } from "../api";
import { RecipeGrid } from "../components/RecipeGrid";
import { Empty, Spinner } from "../components/ui";
import type { InvItem, MatchResult } from "../types";

const KINDS: [string, string][] = [
  ["on_hand", "On hand"], ["staple", "Staples"], ["unavailable", "Out / avoid"],
];

export function InventoryView({ domain, onOpen, onFav, refresh }: {
  domain: "kitchen" | "bar";
  onOpen: (id: string) => void;
  onFav: (id: string, e: MouseEvent) => void;
  refresh: number;
}) {
  const [items, setItems] = useState<InvItem[]>([]);
  const [kind, setKind] = useState("on_hand");
  const [input, setInput] = useState("");
  const [matches, setMatches] = useState<MatchResult[] | null>(null);

  const get = domain === "bar" ? api.bar : api.pantry;
  const add = domain === "bar" ? api.addBar : api.addPantry;
  const rem = domain === "bar" ? api.removeBar : api.removePantry;
  const runMatch = domain === "bar" ? () => api.pour(60) : () => api.pantryMatch("meal", 60);

  const loadItems = () => get().then((r) => setItems(r.items)).catch(() => setItems([]));
  useEffect(() => { loadItems(); /* eslint-disable-next-line */ }, [refresh]);
  useEffect(() => { setMatches(null); runMatch().then((r) => setMatches(r.results)).catch(() => setMatches([])); /* eslint-disable-next-line */ },
    [items.length, refresh]);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const v = input.trim(); if (!v) return;
    setInput(""); await add(v, kind); loadItems();
  };
  const del = async (id: number) => { await rem(id); loadItems(); };

  const title = domain === "bar" ? "Bar cart" : "Pantry";
  const matchLabel = domain === "bar" ? "What you can pour" : "What you can make";
  const shown = items.filter((i) => i.kind === kind);
  const hasOnHand = items.some((i) => i.kind === "on_hand");
  const emptyPrompt = domain === "bar"
    ? "Please add what you have on hand in your bar to see what cocktails you can create!"
    : "Please add what you have on hand in your pantry to see what recipes you can create!";
  const makeable = (matches ?? []).filter((m) => m.makeable);
  const almost = (matches ?? []).filter((m) => !m.makeable);
  const verb = domain === "bar" ? "fully pour" : "fully make";
  const oneAwayLabel = domain === "bar" ? "You’re one bottle away" : "You’re one ingredient away";

  return (
    <div className="rb-layout" style={{ gridTemplateColumns: "330px 1fr" }}>
      <div>
        <h4 className="sec-h">{title}</h4>
        <div className="inv-editor">
          <div className="inv-tabs">
            {KINDS.map(([k, l]) => (
              <button key={k} className={"chip" + (kind === k ? " spirit" : "")} onClick={() => setKind(k)}>{l}</button>
            ))}
          </div>
          <form onSubmit={submit} className="row">
            <input className="rb-search" style={{ flex: 1 }} value={input} onChange={(e) => setInput(e.target.value)}
              placeholder={domain === "bar" ? "Add a bottle or mixer…" : "Add an ingredient…"} />
            <button className="btn primary sm" type="submit">Add</button>
          </form>
          <div style={{ marginTop: 12 }}>
            {shown.map((i) => (
              <span className="inv-item" key={i.id}>{i.name}<button onClick={() => del(i.id)}>×</button></span>
            ))}
            {shown.length === 0 && <span className="rb-count">Nothing here yet.</span>}
          </div>
        </div>
      </div>
      <div>
        <h4 className="sec-h">{matchLabel}</h4>
        {!hasOnHand ? <Empty>{emptyPrompt}</Empty>
          : !matches ? <Empty><Spinner /> &nbsp;Matching…</Empty>
          : makeable.length === 0 && almost.length === 0
            ? <Empty>Nothing you can {verb} yet — add a few more {domain === "bar" ? "bottles" : "ingredients"}.</Empty>
          : <>
              {makeable.length > 0
                ? <RecipeGrid items={makeable.slice(0, 60)} onOpen={onOpen} onFav={onFav} />
                : <Empty>Nothing you can {verb} yet — but you’re close:</Empty>}
              {almost.length > 0 && (
                <>
                  <h4 className="sec-h" style={{ marginTop: 24 }}>{oneAwayLabel}</h4>
                  <RecipeGrid items={almost.slice(0, 60)} onOpen={onOpen} onFav={onFav} showNeed />
                </>
              )}
            </>}
      </div>
    </div>
  );
}
