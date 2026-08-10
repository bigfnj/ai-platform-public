// Recipe Book as a federated React module for the platform shell. Exposes ./module.
// No own top-level chrome beyond its sub-nav — the shell provides the app rail; this
// paints its own editorial theme (warm Kitchen / dark-gold Bar) inside a `.recipe-book`
// wrapper. One SPA over the FastAPI /recipe-book/api backend.
import { useCallback, useEffect, useState } from "react";
import type { MouseEvent } from "react";
import { api, setActingOwner } from "./api";
import { BrowseView } from "./views/BrowseView";
import { PlannerView } from "./views/PlannerView";
import { ShoppingView } from "./views/ShoppingView";
import { InventoryView } from "./views/InventoryView";
import { RecipeModal } from "./components/RecipeModal";
import { AddRecipeModal } from "./components/AddRecipeModal";
import { SettingsModal } from "./components/SettingsModal";
import type { PlanSettings } from "./types";
import "./theme.css";

type Tab = "kitchen" | "bar" | "plan" | "shopping" | "pantry" | "barcart";
const TABS: { id: Tab; label: string }[] = [
  { id: "kitchen", label: "Kitchen" }, { id: "bar", label: "Bar" },
  { id: "plan", label: "Plan" }, { id: "shopping", label: "Shopping" },
  { id: "pantry", label: "Pantry" }, { id: "barcart", label: "Bar Cart" },
];

// The rail's warm "kitchen" (light) vs dark-gold "bar" palette follows the SHELL's
// light/dark toggle (data-theme on <html>), applied across every tab — not the tab.
function readThemeMode(): "kitchen" | "bar" {
  const t = document.documentElement.getAttribute("data-theme");
  if (t === "dark") return "bar";
  if (t === "light") return "kitchen";
  return window.matchMedia?.("(prefers-color-scheme: dark)")?.matches ? "bar" : "kitchen";
}

export default function RecipeBookModule() {
  const [tab, setTab] = useState<Tab>("kitchen");
  const [q, setQ] = useState("");
  const [openId, setOpenId] = useState<string | null>(null);
  const [bump, setBump] = useState(0);
  const [ai, setAi] = useState(true);  // semantic (AI) search — ON by default so a query
  // is understood by meaning ("things with chicken" → chicken dishes), not just keywords.
  const [mode, setMode] = useState<"kitchen" | "bar">(readThemeMode);
  const [adding, setAdding] = useState(false);
  const [cats, setCats] = useState<string[]>([]);
  const [settings, setSettings] = useState<PlanSettings | null>(null);  // null until loaded
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [users, setUsers] = useState<string[]>([]);   // admin: roster for the "view as" picker
  const [viewAs, setViewAs] = useState("");            // "" = the admin's own data

  const refresh = useCallback(() => setBump((b) => b + 1), []);

  // load settings once — reveals the admin gear only if the gateway marks the user admin
  useEffect(() => { api.getSettings().then(setSettings).catch(() => {}); }, []);

  // admin only: the roster of users for the "view as" dropdown
  useEffect(() => {
    if (settings?.is_admin) api.users().then((r) => setUsers(r.users)).catch(() => {});
  }, [settings?.is_admin]);

  // meal categories for the Add-recipe picker (refreshed when the catalog changes)
  useEffect(() => {
    api.categories().then((r) =>
      setCats(r.categories.filter((c) => c.kind !== "beverage").map((c) => c.name))).catch(() => {});
  }, [bump]);
  const onFav = useCallback(async (id: string, e: MouseEvent) => {
    e.stopPropagation();
    await api.toggleFavorite(id);
    refresh();
  }, [refresh]);

  // follow the shell's light/dark toggle (and system changes) live
  useEffect(() => {
    const update = () => setMode(readThemeMode());
    const mo = new MutationObserver(update);
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    const mq = window.matchMedia?.("(prefers-color-scheme: dark)");
    mq?.addEventListener?.("change", update);
    return () => { mo.disconnect(); mq?.removeEventListener?.("change", update); };
  }, []);

  const showSearch = tab === "kitchen" || tab === "bar";

  return (
    <div className="recipe-book" data-mode={mode}>
      <div className="rb-top">
        <div className="rb-brand">Recipe Book<small>Kitchen &amp; Bar</small></div>
        <nav className="rb-nav">
          {TABS.map((t) => (
            <button key={t.id} className={tab === t.id ? "on" : ""} onClick={() => setTab(t.id)}>{t.label}</button>
          ))}
        </nav>
        <div className="rb-right">
          {showSearch && (
            <div className="rb-actions">
              <button className="rb-add" onClick={() => setAdding(true)} title="Add a recipe">
                ＋ Add recipe
              </button>
              <div className="rb-search">
                <input value={q} onChange={(e) => setQ(e.target.value)}
                  placeholder={tab === "bar" ? "Search cocktails…" : "Search recipes…"} />
                <button className={"chip" + (ai ? " spirit" : " ghost")} style={{ cursor: "pointer" }}
                  title="Semantic AI search — find by idea, not just keyword"
                  onClick={() => setAi((v) => !v)}>✨ AI</button>
              </div>
            </div>
          )}
          {settings?.is_admin && users.length > 0 && (
            <select className="rb-viewas" value={viewAs} aria-label="View as user"
              title="Admin: view and edit another user's plan, pantry & bar"
              onChange={(e) => {
                const v = e.target.value;
                setViewAs(v);
                setActingOwner(v || null);
                refresh();  // re-fetch every scoped view for the selected user
              }}>
              <option value="">👤 My data</option>
              {users.map((u) => <option key={u} value={u}>{u}</option>)}
            </select>
          )}
          {settings?.is_admin && (
            <button className="rb-gear" title="Meal-plan settings (admin)"
              onClick={() => setSettingsOpen(true)} aria-label="Settings">⚙</button>
          )}
        </div>
      </div>

      <div className="rb-body">
        {tab === "kitchen" && <BrowseView kind="meal" query={q} semantic={ai} onOpen={setOpenId} onFav={onFav} refresh={bump} />}
        {tab === "bar" && <BrowseView kind="beverage" query={q} semantic={ai} onOpen={setOpenId} onFav={onFav} refresh={bump} />}
        {tab === "plan" && <PlannerView onOpen={setOpenId} refresh={bump} bump={refresh} />}
        {tab === "shopping" && <ShoppingView refresh={bump} />}
        {tab === "pantry" && <InventoryView domain="kitchen" onOpen={setOpenId} onFav={onFav} refresh={bump} />}
        {tab === "barcart" && <InventoryView domain="bar" onOpen={setOpenId} onFav={onFav} refresh={bump} />}
      </div>

      {openId && <RecipeModal id={openId} isAdmin={!!settings?.is_admin}
        onClose={() => setOpenId(null)} onChanged={refresh} />}
      {adding && (
        <AddRecipeModal categories={cats} defaultKind={tab === "bar" ? "beverage" : "meal"}
          isAdmin={!!settings?.is_admin}
          onClose={() => setAdding(false)}
          onCreated={(id) => { setAdding(false); refresh(); setOpenId(id); }} />
      )}
      {settingsOpen && settings && (
        <SettingsModal initial={settings} onClose={() => setSettingsOpen(false)}
          onSaved={(s) => setSettings((cur) => (cur ? { ...cur, ...s } : cur))} />
      )}
    </div>
  );
}
