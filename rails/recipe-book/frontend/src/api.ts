// Typed client for the recipe-book backend. The gateway proxies this prefix to
// the FastAPI backend; standalone dev proxies it via vite.config.
import type {
  Category, DraftResult, DupMatch, IconStatus, InvItem, MatchResult, PlannerEntry, PlanProposalItem,
  PlanSettings, RecipeDetail, RecipeDraft, RecipeList, ShoppingItem, Spirit, Stats, TagRef,
} from "./types";

const BASE = "/recipe-book";

// Admin "view as user": when set, every request carries ?owner=<username> so the admin
// reads/writes that user's owner-scoped data. The backend only honors it for admins
// (verified header), so a non-admin setting it has no effect. null = the caller's own.
let _actingOwner: string | null = null;
export function setActingOwner(user: string | null): void {
  _actingOwner = user && user.trim() ? user.trim() : null;
}
export function actingOwner(): string | null { return _actingOwner; }
function withOwner(path: string): string {
  if (!_actingOwner) return path;
  return path + (path.includes("?") ? "&" : "?") + "owner=" + encodeURIComponent(_actingOwner);
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${withOwner(path)}`, {
    headers: { "content-type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    let detail = "";
    try { const b = await res.json(); detail = typeof b?.detail === "string" ? b.detail : JSON.stringify(b); }
    catch { detail = await res.text(); }
    throw new Error(detail || `${res.status} ${res.statusText}`);
  }
  return (await res.json()) as T;
}

const post = (p: string, body?: unknown) =>
  req(p, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });
const put = (p: string, body: unknown) => req(p, { method: "PUT", body: JSON.stringify(body) });

// multipart (file upload) — let the browser set the boundary; no JSON content-type
async function postForm<T>(path: string, form: FormData): Promise<T> {
  const res = await fetch(`${BASE}${withOwner(path)}`, { method: "POST", body: form });
  if (!res.ok) {
    let detail = "";
    try { const b = await res.json(); detail = typeof b?.detail === "string" ? b.detail : JSON.stringify(b); }
    catch { detail = await res.text(); }
    throw new Error(detail || `${res.status} ${res.statusText}`);
  }
  return (await res.json()) as T;
}
const patch = (p: string, body: unknown) => req(p, { method: "PATCH", body: JSON.stringify(body) });
const del = (p: string) => req(p, { method: "DELETE" });

function qs(params: Record<string, unknown>): string {
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params))
    if (v !== undefined && v !== null && v !== "" && v !== false) sp.set(k, String(v));
  const s = sp.toString();
  return s ? `?${s}` : "";
}

export interface RecipeQuery {
  q?: string; kind?: "all" | "meal" | "beverage"; category?: string;
  spirit?: string; fav?: boolean; tag?: number; semantic?: boolean; limit?: number; offset?: number;
}

export const iconUrl = (id: string) => `${BASE}/api/icon/${id}`;

export const api = {
  stats: () => req<Stats>("/api/stats"),
  categories: () => req<{ categories: Category[] }>("/api/categories"),
  spirits: () => req<{ spirits: Spirit[] }>("/api/spirits"),
  recipes: (q: RecipeQuery = {}) => req<RecipeList>(`/api/recipes${qs(q as Record<string, unknown>)}`),
  recipe: (id: string) => req<RecipeDetail>(`/api/recipes/${id}`),
  setCategory: (id: string, category: string) =>
    put(`/api/recipes/${id}/category`, { category }) as Promise<{ recipe_id: string; category: string; kind: string }>,

  // authoring
  draftRecipe: (b: {
    mode: "paste" | "manual"; kind: "meal" | "beverage"; category: string;
    text?: string; title?: string; meta?: string; ingredients?: string[]; instructions?: string[];
  }) => post("/api/recipes/draft", b) as Promise<DraftResult>,
  refineDraft: (draft: RecipeDraft, message: string) =>
    post("/api/recipes/draft/refine", { draft, message }) as Promise<DraftResult>,
  extractUrl: (url: string, kind = "", category = "") =>
    post("/api/recipes/extract/url", { url, kind, category }) as Promise<DraftResult>,
  extractFiles: (files: File[], kind = "", category = "") => {
    const fd = new FormData();
    files.forEach((f) => fd.append("files", f));
    fd.append("kind", kind); fd.append("category", category);
    return postForm<DraftResult>("/api/recipes/extract/files", fd);
  },
  duplicateCheck: (b: { title: string; ingredients: string[]; exclude_id?: string }) =>
    post("/api/recipes/duplicate_check", b) as Promise<{ matches: DupMatch[] }>,
  createRecipe: (b: RecipeDraft) =>
    post("/api/recipes", b) as Promise<{ id: string; category: string; kind: string }>,
  editTitle: (id: string, title: string) =>
    put(`/api/recipes/${id}/title`, { title }) as Promise<{ updated: string; title: string }>,
  editContent: (id: string, b: { ingredients: string[]; instructions: string[]; shopping_list?: string[] }) =>
    put(`/api/recipes/${id}/content`, b) as Promise<{ updated: string }>,
  setAttributes: (id: string, attributes: string[]) =>
    put(`/api/recipes/${id}/attributes`, { attributes }) as Promise<{ recipe_id: string; attributes: string[] }>,
  searchStatus: () => req<{ built: boolean; count: number; model: string }>("/api/search/status"),
  reindex: () => post("/api/search/reindex"),

  // admin: recipe-icon status + (re)generation (both broker-heavy; server is single-flight)
  iconStatus: () => req<IconStatus>("/api/icons/status"),
  regenIcons: (force: boolean) =>
    post(`/api/icons/repass${force ? "?force=true" : ""}`) as Promise<IconStatus & { queued: boolean }>,

  toggleFavorite: (id: string) => post(`/api/favorites/${id}`) as Promise<{ favorite: boolean }>,
  setRating: (id: string, stars: number, note = "") => put(`/api/ratings/${id}`, { stars, note }),

  tags: () => req<{ tags: TagRef[] }>("/api/tags"),
  createTag: (name: string, color = "accent") => post("/api/tags", { name, color }) as Promise<TagRef>,
  deleteTag: (id: number) => del(`/api/tags/${id}`),
  assignTag: (recipeId: string, tagId: number) => post(`/api/recipes/${recipeId}/tags`, { tag_id: tagId }),
  unassignTag: (recipeId: string, tagId: number) => del(`/api/recipes/${recipeId}/tags/${tagId}`),

  planner: (start?: string, end?: string) => req<{ entries: PlannerEntry[] }>(`/api/planner${qs({ start, end })}`),
  planTray: () => req<{ entries: PlannerEntry[] }>(`/api/planner${qs({ tray: true })}`),
  addPlan: (b: { date: string; slot: string; recipe_id?: string | null; title?: string; servings?: number }) =>
    post("/api/planner", b) as Promise<{ id: number }>,
  updatePlan: (id: number, b: { date?: string; slot?: string }) =>
    patch(`/api/planner/${id}`, b) as Promise<{ updated: number }>,
  deletePlan: (id: number) => del(`/api/planner/${id}`),
  proposePlan: (b: { dates: string[]; slots: string[]; optimize_shopping: boolean; drink_pairing: boolean }) =>
    post("/api/planner/propose", b) as Promise<{ items: PlanProposalItem[] }>,
  swapPlan: (b: { date: string; slot: string; ptype: string; exclude_ids: string[] }) =>
    post("/api/planner/propose/swap", b) as Promise<{ item: PlanProposalItem | null }>,
  pairDrink: (b: { date: string; ptype: "cocktail" | "wine"; exclude_ids?: string[] }) =>
    post("/api/planner/pair", { exclude_ids: [], ...b }) as Promise<{ entry: PlannerEntry & { why?: string } }>,
  shopping: (start?: string, end?: string, ids?: string) =>
    req<{ items: ShoppingItem[]; recipe_count: number }>(`/api/planner/shopping${qs({ start, end, ids })}`),
  checkShopping: (itemKey: string, checked: boolean) =>
    post("/api/planner/shopping/check", { item_key: itemKey, checked }),
  // "Send to Phone" (Google Tasks) — each user connects their own account.
  gtasksStatus: () => req<{ app_configured: boolean; connected: boolean; email: string | null; list_title: string }>("/api/gtasks/status"),
  gtasksConnect: () => req<{ url: string }>("/api/gtasks/connect"),
  gtasksDisconnect: () => post("/api/gtasks/disconnect") as Promise<{ connected: boolean }>,
  // push the still-unchecked items into the caller's Google Tasks (Tasks app / Calendar / Gmail)
  sendShopping: (ids?: string) =>
    post("/api/planner/shopping/send", { ids: ids ?? "" }) as Promise<{ sent: number; list: string; detail?: string }>,

  pantry: () => req<{ items: InvItem[] }>("/api/pantry"),
  addPantry: (name: string, kind: string) => post("/api/pantry", { name, kind }),
  removePantry: (id: number) => del(`/api/pantry/${id}`),
  pantryMatch: (kind = "meal", limit = 40) =>
    req<{ results: MatchResult[]; inventory: Record<string, string[]> }>(`/api/pantry/match${qs({ kind, limit })}`),

  bar: () => req<{ items: InvItem[] }>("/api/bar"),
  addBar: (name: string, kind: string) => post("/api/bar", { name, kind }),
  removeBar: (id: number) => del(`/api/bar/${id}`),
  pour: (limit = 40) =>
    req<{ results: MatchResult[]; inventory: Record<string, string[]> }>(`/api/bar/pour${qs({ limit })}`),

  assistant: (b: { mode: string; recipe_id?: string | null; prompt?: string; servings?: number; model?: string }) =>
    post("/api/assistant", b) as Promise<{ markdown: string; mode: string }>,

  getSettings: () => req<PlanSettings>("/api/settings"),
  putSettings: (b: { plan_retention_days?: number; plan_recency_days?: number }) =>
    put("/api/settings", b) as Promise<{ plan_retention_days: number; plan_recency_days: number }>,

  // multi-tenant admin: the caller's identity + the roster for the "view as user" picker
  whoami: () => req<{ user: string | null; is_admin: boolean }>("/api/whoami"),
  users: () => req<{ users: string[] }>("/api/users"),
};
