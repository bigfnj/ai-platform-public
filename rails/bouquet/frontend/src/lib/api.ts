// API client for the bouquet rail module. Calls are namespaced under /bouquet so
// the platform gateway proxies /bouquet/api/* to this app's FastAPI backend (and
// the standalone dev vite proxy does the same). analyze() is a buffered POST: the
// broker + gateway both buffer, so the report arrives whole, not streamed.

const BASE = "/bouquet";

export interface ImageRef {
  file: string;
  url: string;
  descriptor: string;
  author: string | null;
  license: string | null;
  license_url: string | null;
  source_page: string | null;
}

export interface FlowerSummary {
  slug: string;
  title: string;
  oneliner: string;
  thumb: string | null;
  image_count: number;
}

export interface FlowerDetail {
  slug: string;
  title: string;
  oneliner: string;
  common_names: string[];
  sections: Record<string, string>;
  markdown: string;
  images: ImageRef[];
}

export interface IdentifiedFlower {
  name: string;
  colors: string[];
  confidence?: "high" | "medium" | "low" | string;
  notes?: string;
  slug?: string | null;      // resolved KB profile (identify annotates this)
  in_library?: boolean;      // whether the name maps to a KB profile
}

export interface Inventory {
  flowers: IdentifiedFlower[];
  greenery: string[];
  palette: string;
  arrangement: string;
  context: string;
}

export interface IdentifyResult {
  image_token: string;
  inventory: Inventory;
}

// The edited inventory the florist sends back to generate. Names are re-resolved
// server-side, so slug/in_library are advisory only.
export interface GenerateReq {
  image_token: string;
  inventory: Inventory;
  guidance: string;
  mode: "analysis" | "florist";
}

export interface AnalyzeResult {
  id: number;
  image_url: string;
  mode: "analysis" | "florist";
  title: string;
  inventory: Inventory;
  matched: FlowerSummary[];
  matched_slugs: string[];
  unprofiled: string[];
  report_md: string;
  guidance: string;
  model: string;
}

export interface AnalysisListItem {
  id: number;
  created_at: string;
  mode: "analysis" | "florist";
  title: string;
  image_url: string | null;
  model: string;
  matched: string[];
  unprofiled: string[];
}

export interface AnalysisDetail extends AnalysisListItem {
  inventory: Inventory;
  report_md: string;
  guidance: string;
}

export interface ResolveResult { slug: string | null; title: string | null; in_library: boolean }

export interface Reference { slug: string; label: string }

async function getJSON<T>(url: string): Promise<T> {
  const r = await fetch(BASE + url);
  if (!r.ok) throw new Error(`${url} -> ${r.status}`);
  return r.json();
}

interface JobStatus<T> { status: "running" | "done" | "error"; result: T | null; error: string | null }

const sleep = (ms: number) => new Promise((res) => setTimeout(res, ms));

// The slow broker steps run as background jobs (a cold 27B load can exceed
// Cloudflare's ~100s edge timeout if held open as one request). Start the job, then
// poll a fast status endpoint until it finishes. Sync validation errors (415/400/404)
// surface immediately from the start call.
async function pollJob<T>(startUrl: string, init: RequestInit): Promise<T> {
  const r = await fetch(BASE + startUrl, init);
  if (!r.ok) throw new Error(`${startUrl} -> ${r.status}: ${await r.text().catch(() => "")}`);
  const { job_id } = (await r.json()) as { job_id: string };
  const deadline = Date.now() + 15 * 60 * 1000;   // safety cap
  while (Date.now() < deadline) {
    await sleep(2000);
    const jr = await fetch(`${BASE}/api/jobs/${job_id}`);
    if (!jr.ok) throw new Error(`job ${job_id} -> ${jr.status}`);
    const j = (await jr.json()) as JobStatus<T>;
    if (j.status === "done") return j.result as T;
    if (j.status === "error") throw new Error(j.error || "the model could not finish");
  }
  throw new Error("timed out waiting for the model");
}

export const api = {
  health: () => getJSON<{ ok: boolean; broker: boolean; flowers: number }>("/api/health"),

  // library
  flowers: () => getJSON<{ flowers: FlowerSummary[] }>("/api/flowers"),
  flower: (slug: string) => getJSON<FlowerDetail>(`/api/flowers/${encodeURIComponent(slug)}`),
  references: () => getJSON<{ references: Reference[] }>("/api/references"),
  resolve: (name: string) =>
    getJSON<ResolveResult>(`/api/resolve?name=${encodeURIComponent(name)}`),

  // pipeline — step 1: identify (parks the upload, returns an editable inventory)
  identify: (file: File): Promise<IdentifyResult> => {
    const fd = new FormData();
    fd.append("image", file);
    return pollJob<IdentifyResult>("/api/identify", { method: "POST", body: fd });
  },

  // pipeline — step 2: generate the report from the corrected inventory
  generate: (req: GenerateReq): Promise<AnalyzeResult> =>
    pollJob<AnalyzeResult>("/api/generate", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(req),
    }),

  // saved analyses (single-tenant library)
  analyses: () => getJSON<{ analyses: AnalysisListItem[] }>("/api/analyses"),
  analysis: (id: number) => getJSON<AnalysisDetail>(`/api/analyses/${id}`),
  deleteAnalysis: async (id: number): Promise<void> => {
    const r = await fetch(`${BASE}/api/analyses/${id}`, { method: "DELETE" });
    if (!r.ok) throw new Error(`delete -> ${r.status}`);
  },
};
