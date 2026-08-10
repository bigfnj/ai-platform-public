// Same-origin API + WebSocket helpers. In production the shell serves this remote under
// /ai-playground/ and the gateway proxies /ai-playground/api/* + /ai-playground/ws/*. In
// standalone dev, vite proxies the same paths to the backend on :8850.
const BASE = "/ai-playground";

export async function getJSON<T>(path: string): Promise<T> {
  const r = await fetch(BASE + path);
  if (!r.ok) throw new Error(`${path} -> ${r.status}`);
  return r.json();
}

export async function postJSON<T>(path: string, body: unknown): Promise<T> {
  const r = await fetch(BASE + path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error((await r.text()) || `${path} -> ${r.status}`);
  return r.json();
}

export async function uploadCorpus(name: string, files: FileList): Promise<any> {
  const fd = new FormData();
  fd.append("name", name);
  Array.from(files).forEach((f) => fd.append("files", f));
  const r = await fetch(BASE + "/api/rag/upload", { method: "POST", body: fd });
  if (!r.ok) throw new Error((await r.text()) || "upload failed");
  return r.json();
}

export async function deleteCorpus(id: number): Promise<void> {
  await fetch(BASE + `/api/rag/corpus/${id}`, { method: "DELETE" });
}

// --- Embedding Lab helpers -------------------------------------------------
export async function delJSON<T>(path: string): Promise<T> {
  const r = await fetch(BASE + path, { method: "DELETE" });
  if (!r.ok) throw new Error((await r.text()) || `${path} -> ${r.status}`);
  return r.json();
}

// Fire-and-return POST with no body (fetch/pull actions). Long-running; caller shows a spinner.
export async function postAction<T>(path: string): Promise<T> {
  const r = await fetch(BASE + path, { method: "POST" });
  if (!r.ok) throw new Error((await r.text()) || `${path} -> ${r.status}`);
  return r.json();
}

export async function uploadQuerySet(name: string, file: File): Promise<any> {
  const fd = new FormData();
  fd.append("name", name);
  fd.append("file", file);
  const r = await fetch(BASE + "/api/bench/querysets/upload", { method: "POST", body: fd });
  if (!r.ok) throw new Error((await r.text()) || "upload failed");
  return r.json();
}

export function wsURL(path: string): string {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${location.host}${BASE}${path}`;
}
