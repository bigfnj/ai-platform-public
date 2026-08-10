import { useEffect, useState } from "react";
import type { FormEvent, ReactNode } from "react";
import { marked } from "marked";
import { api } from "../api";
import { RecipeIcon, Stars, Spinner } from "./ui";
import { EditList } from "./EditList";
import type { RecipeDetail } from "../types";

// Dietary/allergen tag vocabulary for the edit picker (mirrors attributes.ALL_TAGS).
const TAG_GROUPS: { label: string; tags: string[] }[] = [
  { label: "Diet", tags: ["vegetarian", "vegan", "pescatarian"] },
  { label: "Free from", tags: ["gluten-free", "dairy-free", "nut-free", "egg-free", "soy-free"] },
  { label: "Other", tags: ["spicy"] },
  { label: "Contains", tags: [
    "contains-dairy", "contains-egg", "contains-gluten", "contains-peanut", "contains-tree-nut",
    "contains-soy", "contains-fish", "contains-shellfish", "contains-sesame", "contains-coconut",
    "contains-pork", "contains-beef", "contains-poultry"] },
];
const ALL_TAGS = TAG_GROUPS.flatMap((g) => g.tags);

export function RecipeModal({ id, isAdmin, onClose, onChanged }: {
  id: string; isAdmin: boolean; onClose: () => void; onChanged: () => void;
}) {
  const [r, setR] = useState<RecipeDetail | null>(null);
  const [checked, setChecked] = useState<Set<number>>(new Set());
  const [assist, setAssist] = useState<{ loading: boolean; html: string; mode: string }>(
    { loading: false, html: "", mode: "" });
  const [toast, setToast] = useState("");
  const [askText, setAskText] = useState("");
  const [cats, setCats] = useState<string[]>([]);
  const [showCat, setShowCat] = useState(false);
  const [showSlot, setShowSlot] = useState(false);
  const [newCat, setNewCat] = useState("");
  const [editing, setEditing] = useState(false);
  const [edTitle, setEdTitle] = useState("");
  const [edIng, setEdIng] = useState<string[]>([]);
  const [edStep, setEdStep] = useState<string[]>([]);
  const [edTags, setEdTags] = useState<Set<string>>(new Set());
  const [saving, setSaving] = useState(false);

  useEffect(() => { setR(null); setChecked(new Set()); setAskText(""); setShowCat(false); setShowSlot(false); setEditing(false); api.recipe(id).then(setR).catch(() => {}); }, [id]);
  useEffect(() => { api.categories().then((d) => setCats(d.categories.map((c) => c.name))).catch(() => {}); }, []);

  const shell = (body: ReactNode) => (
    <div className="rb-modal-bg" onClick={onClose}>
      <div className="rb-modal" onClick={(e) => e.stopPropagation()}>{body}</div>
    </div>
  );
  if (!r) return shell(<div><Spinner /> &nbsp;Loading…</div>);

  const bev = r.kind === "beverage";
  // Meal slots offered when adding to the planner tray (day order). Beverages lead with Drink
  // but can still go in any meal slot (e.g. a breakfast smoothie).
  const planSlots: [string, string][] = bev
    ? [["drink", "Drink"], ["breakfast", "Breakfast"], ["lunch", "Lunch"], ["dinner", "Dinner"], ["snack", "Snack"]]
    : [["breakfast", "Breakfast"], ["lunch", "Lunch"], ["dinner", "Dinner"], ["snack", "Snack"]];
  const asHeader = (s: string) => { const m = s.match(/^\*\*(.+)\*\*$/); return m ? m[1] : null; };
  const toggleFav = async () => { await api.toggleFavorite(r.id); setR({ ...r, favorite: !r.favorite }); onChanged(); };
  const setRating = async (n: number) => {
    await api.setRating(r.id, n);
    setR({ ...r, rating: n ? { stars: n, note: r.rating?.note || "" } : null });
    onChanged();
  };
  const runAssist = async (mode: string, opts?: { servings?: number; prompt?: string }) => {
    setAssist({ loading: true, html: "", mode });
    try {
      const res = await api.assistant({ mode, recipe_id: r.id, servings: opts?.servings, prompt: opts?.prompt });
      setAssist({ loading: false, html: marked.parse(res.markdown) as string, mode });
    } catch (e: any) {
      setAssist({ loading: false, html: `<em>Assistant unavailable: ${e.message}</em>`, mode });
    }
  };
  const submitAsk = (e: FormEvent) => {
    e.preventDefault();
    const p = askText.trim();
    if (p) runAssist("ask", { prompt: p });
  };
  const addToPlan = async (slot: string, label: string) => {
    // Stage into the planner tray (empty date) with the chosen meal slot; the user then
    // drags it onto a day in Plan.
    setShowSlot(false);
    await api.addPlan({ date: "", slot, recipe_id: r.id, servings: 2 });
    setToast(`Added as ${label} to the tray — drag it onto a day`); onChanged();
    setTimeout(() => setToast(""), 2200);
  };
  const changeCat = async (cat: string) => {
    const c = cat.trim();
    if (!c || c === r.category) { setShowCat(false); return; }
    await api.setCategory(r.id, c);
    setR({ ...r, category: c, kind: c.toLowerCase() === "beverages" ? "beverage" : "meal" });
    setShowCat(false); setNewCat("");
    setToast(`Moved to ${c}`); onChanged();
    setTimeout(() => setToast(""), 1800);
  };
  const startEdit = () => {
    setEdTitle(r.title); setEdIng([...r.ingredients]); setEdStep([...r.instructions]);
    setEdTags(new Set(r.attributes)); setShowCat(false); setShowSlot(false); setEditing(true);
  };
  const toggleTag = (t: string) => setEdTags((prev) => {
    const next = new Set(prev);
    next.has(t) ? next.delete(t) : next.add(t);
    return next;
  });
  const saveEdit = async () => {
    setSaving(true);
    try {
      const newTitle = edTitle.trim();
      if (newTitle && newTitle !== r.title) await api.editTitle(r.id, newTitle);
      await api.editContent(r.id, { ingredients: edIng, instructions: edStep });
      await api.setAttributes(r.id, [...edTags]);
      const fresh = await api.recipe(r.id);
      setR(fresh); setEditing(false);
      setToast("Saved — shopping list updated"); onChanged();
      setTimeout(() => setToast(""), 2000);
    } catch (e: any) {
      setToast(e.message || "Save failed");
      setTimeout(() => setToast(""), 2500);
    } finally { setSaving(false); }
  };

  return shell(
    <>
      <button className="close" onClick={onClose}>×</button>
      <div className="kicker">{r.category}{r.is_collection ? " · collection" : ""}</div>
      <div className="titrow">
        {editing ? (
          <input className="rb-search" style={{ fontSize: 20, fontWeight: 700, flex: 1 }}
            value={edTitle} onChange={(e) => setEdTitle(e.target.value)} placeholder="Recipe title" />
        ) : (
          <h2>{r.title}</h2>
        )}
        <RecipeIcon r={r} />
      </div>
      <div className="metaline">
        {r.meta}{r.meta ? " · " : ""}
        <Stars value={r.rating?.stars || 0} onSet={setRating} />
        <button className={"rb-fav" + (r.favorite ? " on" : "")}
          style={{ position: "static", marginLeft: 8, fontSize: 15 }} onClick={toggleFav}>♥</button>
      </div>

      {bev && (r.base_spirits.length > 0 || r.glass || r.technique) && (
        <div className="row" style={{ marginTop: 12 }}>
          {r.base_spirits.map((s) => <span key={s} className="chip spirit">{s}</span>)}
          {r.glass && <span className="chip">{r.glass}</span>}
          {r.technique && <span className="chip ghost">{r.technique}</span>}
        </div>
      )}

      {(r.ingredients.length > 0 || editing) && (
        <>
          <div className="rule" />
          <h4 className="sec-h">{bev ? "Spec" : "Ingredients"}</h4>
          {editing ? (
            <EditList items={edIng} onChange={setEdIng}
              placeholder={bev ? "2 oz gin" : "1 lb chicken thighs"} />
          ) : bev ? (
            <div className="spec">{r.ingredients.map((i, n) => {
              const h = asHeader(i);
              return h ? <div key={n} className="spec-head">{h}</div> : <div key={n}>{i}</div>;
            })}</div>
          ) : (
            <div className="ing-list">
              {r.ingredients.map((ing, n) => {
                const h = asHeader(ing);
                if (h) return <div key={n} className="ing-head">{h}</div>;
                return (
                  <div key={n} className={"ing-row" + (checked.has(n) ? " on" : "")}
                    onClick={() => { const s = new Set(checked); s.has(n) ? s.delete(n) : s.add(n); setChecked(s); }}>
                    <span className="box">{checked.has(n) ? "✓" : ""}</span><span>{ing}</span>
                  </div>
                );
              })}
            </div>
          )}
        </>
      )}

      {(r.instructions.length > 0 || editing) && (
        <>
          <div className="rule" />
          <h4 className="sec-h">Method</h4>
          {editing ? (
            <EditList items={edStep} onChange={setEdStep} ordered multiline placeholder="Describe the step…" />
          ) : (
            <ol className="method">{r.instructions.map((s, n) => {
              const h = asHeader(s);
              return h ? <li key={n} className="mhead">{h}</li> : <li key={n}>{s}</li>;
            })}</ol>
          )}
        </>
      )}

      {editing && (
        <>
          <div className="rule" />
          <h4 className="sec-h">Dietary &amp; allergen tags</h4>
          <div className="cat-hint" style={{ marginBottom: 8 }}>
            Auto-detected from the ingredients. Remove any that are wrong or add missing ones — your edits stick.
          </div>
          <div className="cat-chips">
            {[...edTags].sort().map((t) => (
              <button key={t} className="chip pick on" onClick={() => toggleTag(t)} title="Remove">{t} ✕</button>
            ))}
            {edTags.size === 0 && <span className="cat-hint">No tags</span>}
          </div>
          <select className="rb-search" style={{ marginTop: 8 }} value=""
            onChange={(e) => { if (e.target.value) toggleTag(e.target.value); }}>
            <option value="">＋ add a tag…</option>
            {TAG_GROUPS.map((g) => (
              <optgroup key={g.label} label={g.label}>
                {g.tags.filter((t) => !edTags.has(t)).map((t) => <option key={t} value={t}>{t}</option>)}
              </optgroup>
            ))}
          </select>
        </>
      )}

      {!bev && r.shopping_list.length > 0 && (
        <div className="market">
          <h4 className="sec-h" style={{ marginBottom: 6 }}>Shopping list · empty-kitchen</h4>
          <ul>{r.shopping_list.map((s, n) => <li key={n}>{s}</li>)}</ul>
        </div>
      )}

      {r.extra_sections.map((sec, i) => (
        <div key={i} style={{ marginTop: 16 }}>
          <h4 className="sec-h">{sec.heading}</h4>
          {sec.ordered
            ? <ol className="method">{sec.items.map((it, n) => <li key={n}>{it}</li>)}</ol>
            : <ul>{sec.items.map((it, n) => <li key={n}>{it}</li>)}</ul>}
        </div>
      ))}

      <div className="row" style={{ marginTop: 20 }}>
        {editing ? (
          <>
            <button className="btn primary" onClick={saveEdit} disabled={saving}>
              {saving ? <><Spinner /> &nbsp;Saving…</> : "Save changes"}
            </button>
            <button className="btn ghost" onClick={() => setEditing(false)} disabled={saving}>Cancel</button>
          </>
        ) : (
          <>
            <button className="btn primary" onClick={() => { setShowCat(false); setShowSlot((v) => !v); }}>＋ Add to plan</button>
            <button className="btn ghost" onClick={toggleFav}>{r.favorite ? "♥ Favorited" : "♡ Save"}</button>
            {/* Editing the shared recipe (content, category, tags) is admin-only. */}
            {isAdmin && <button className="btn ghost" onClick={startEdit}>✎ Edit</button>}
            {isAdmin && (
              <button className="btn ghost" onClick={() => { setShowSlot(false); setShowCat((v) => !v); }}>⇄ Change category</button>
            )}
          </>
        )}
        {toast && <span className="rb-count">{toast}</span>}
      </div>
      {showSlot && (
        <div className="cat-picker">
          <div className="cat-hint">Add “{r.title}” to the tray as…</div>
          <div className="cat-chips">
            {planSlots.map(([v, l]) => (
              <button key={v} className="chip pick" onClick={() => addToPlan(v, l)}>{l}</button>
            ))}
          </div>
        </div>
      )}
      {showCat && (
        <div className="cat-picker">
          <div className="cat-hint">Move “{r.title}” from <b>{r.category}</b> to…</div>
          <div className="cat-chips">
            {cats.map((c) => (
              <button key={c} className={"chip pick" + (c === r.category ? " on" : "")}
                onClick={() => changeCat(c)}>{c}</button>
            ))}
          </div>
          <form className="row" style={{ marginTop: 8 }}
            onSubmit={(e) => { e.preventDefault(); changeCat(newCat); }}>
            <input className="rb-search" style={{ flex: 1 }} value={newCat}
              onChange={(e) => setNewCat(e.target.value)} placeholder="…or type a new category" />
            <button className="btn primary sm" type="submit" disabled={!newCat.trim()}>Move</button>
          </form>
        </div>
      )}

      {!editing && <div className="assist">
        <h4 className="sec-h">Ask the assistant</h4>
        <div className="assist-btns">
          <button className="btn ghost sm" onClick={() => runAssist("substitute")}>Substitutions</button>
          <button className="btn ghost sm" onClick={() => runAssist("double")}>Double the recipe</button>
          <button className="btn ghost sm" onClick={() => runAssist(bev ? "pairing" : "menu")}>
            {bev ? "Pairing" : "Build a menu"}
          </button>
        </div>
        <form onSubmit={submitAsk} className="row" style={{ marginTop: 10 }}>
          <input className="rb-search" style={{ flex: 1 }} value={askText}
            onChange={(e) => setAskText(e.target.value)}
            placeholder={bev
              ? "Ask anything — “scale to a pitcher”, “3x it”, “less sweet”…"
              : "Ask anything — “3x it properly”, “make it dairy-free”, “for a crowd of 12”…"} />
          <button className="btn primary sm" type="submit" disabled={!askText.trim()}>Ask</button>
        </form>
        {assist.mode && (
          <div className="assist-out">
            {assist.loading ? <><Spinner /> &nbsp;Thinking…</> : <div dangerouslySetInnerHTML={{ __html: assist.html }} />}
          </div>
        )}
      </div>}

      {r.source && <div className="metaline" style={{ marginTop: 16, fontSize: 11 }}>Source: {r.source}</div>}
    </>,
  );
}
