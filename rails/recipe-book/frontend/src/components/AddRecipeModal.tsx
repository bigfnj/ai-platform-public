import { useEffect, useState } from "react";
import type { ChangeEvent, DragEvent, ReactNode } from "react";
import { api } from "../api";
import type { DraftResult, DupMatch, RecipeDraft } from "../types";
import { EditList } from "./EditList";
import { Spinner } from "./ui";

const SPIRITS = ["Gin", "Vodka", "Rum", "Whiskey", "Tequila", "Mezcal", "Brandy",
  "Wine & Sparkling", "Liqueur", "Non-Alcoholic"];
const ACCEPT = ".pdf,.doc,.docx,.txt,.md,.png,.jpg,.jpeg,.webp,.gif";

const emptyDraft = (kind: "meal" | "beverage", category: string): RecipeDraft => ({
  title: "", meta: "", kind, category,
  ingredients: [""], instructions: [""], shopping_list: [], source: "",
});

export function AddRecipeModal({ categories, defaultKind, isAdmin, onClose, onCreated }: {
  categories: string[];
  defaultKind: "meal" | "beverage";
  isAdmin: boolean;
  onClose: () => void;
  onCreated: (id: string) => void;
}) {
  const [kind, setKind] = useState<"meal" | "beverage">(defaultKind);
  const [mode, setMode] = useState<"paste" | "manual" | "upload">("paste");
  const [category, setCategory] = useState(categories[0] || "Entrees");
  const [spirit, setSpirit] = useState("Gin");
  const [text, setText] = useState("");
  const [draft, setDraft] = useState<RecipeDraft | null>(null);
  const [questions, setQuestions] = useState<string[]>([]);
  const [ask, setAsk] = useState("");
  const [busy, setBusy] = useState<"" | "draft" | "clarify" | "save">("");
  const [err, setErr] = useState("");
  // upload
  const [files, setFiles] = useState<File[]>([]);
  const [url, setUrl] = useState("");
  const [batch, setBatch] = useState(false);
  const [queue, setQueue] = useState<File[]>([]);
  const [qIndex, setQIndex] = useState(0);
  // duplicate-on-save
  const [dupes, setDupes] = useState<DupMatch[]>([]);

  // Non-admin contributions always land in the "To Try" staging bucket (the backend
  // enforces it too); admins file directly into any category.
  const catValue = !isAdmin ? "To Try" : (kind === "beverage" ? "Beverages" : category);
  const patch = (p: Partial<RecipeDraft>) => setDraft((d) => (d ? { ...d, ...p } : d));
  const catOptions = [...new Set([category, ...categories])];

  // clipboard paste of a screenshot / image anywhere in the modal -> upload tab
  useEffect(() => {
    const onPaste = (e: ClipboardEvent) => {
      const imgs: File[] = [];
      for (const it of e.clipboardData?.items || []) {
        if (it.type.startsWith("image/")) { const f = it.getAsFile(); if (f) imgs.push(f); }
      }
      if (imgs.length) { setMode("upload"); setDraft(null); setFiles((p) => [...p, ...imgs]); }
    };
    document.addEventListener("paste", onPaste);
    return () => document.removeEventListener("paste", onPaste);
  }, []);

  const applyResult = (res: DraftResult, autofill = false) => {
    setDraft(res.draft); setQuestions(res.questions); setDupes([]);
    if (autofill && res.suggest) {
      const k = res.suggest.kind === "beverage" ? "beverage" : "meal";
      setKind(k);
      if (k === "beverage") { if (res.suggest.spirit) setSpirit(res.suggest.spirit); }
      else if (res.suggest.category) setCategory(res.suggest.category);
    }
  };

  const switchMode = (m: "paste" | "manual" | "upload") => {
    setMode(m); setErr(""); setQuestions([]); setDupes([]);
    setDraft(m === "manual" ? emptyDraft(kind, catValue) : null);
  };
  const switchKind = (k: "meal" | "beverage") => {
    setKind(k);
    if (mode === "manual") setDraft(emptyDraft(k, k === "beverage" ? "Beverages" : category));
  };

  const genPreview = async () => {
    setErr(""); setBusy("draft");
    try {
      const hinted = kind === "beverage" && spirit ? `Base spirit: ${spirit}\n${text}` : text;
      applyResult(await api.draftRecipe({ mode: "paste", kind, category: catValue, text: hinted }));
    } catch (e: any) { setErr(e.message || "Could not read that."); }
    finally { setBusy(""); }
  };

  const extract = async () => {
    setErr(""); setBusy("draft");
    try {
      if (url.trim()) {
        applyResult(await api.extractUrl(url.trim()), true);
      } else if (files.length) {
        if (batch) { setQueue(files); setQIndex(0); applyResult(await api.extractFiles([files[0]]), true); }
        else applyResult(await api.extractFiles(files), true);
      } else { setErr("Add a file, paste a screenshot, or enter a URL."); }
    } catch (e: any) { setErr(e.message || "Could not extract that."); }
    finally { setBusy(""); }
  };

  const gotoBatch = async (i: number) => {
    setQIndex(i); setDupes([]); setDraft(null); setQuestions([]); setErr(""); setBusy("draft");
    try { applyResult(await api.extractFiles([queue[i]]), true); }
    catch (e: any) { setErr(e.message || "Could not extract that."); }
    finally { setBusy(""); }
  };
  const moreInBatch = mode === "upload" && batch && qIndex < queue.length - 1;

  const afterSave = async (savedId: string) => {
    if (moreInBatch) await gotoBatch(qIndex + 1);
    else onCreated(savedId);
  };
  const doCreate = async () => {
    setBusy("save"); setErr("");
    try { const res = await api.createRecipe({ ...draft!, kind, category: catValue }); await afterSave(res.id); }
    catch (e: any) { setErr(e.message || "Could not save."); setBusy(""); }
  };
  const doReplace = async (id: string) => {
    setBusy("save"); setErr("");
    try {
      await api.editContent(id, { ingredients: draft!.ingredients, instructions: draft!.instructions });
      await afterSave(id);
    } catch (e: any) { setErr(e.message || "Could not replace."); setBusy(""); }
  };
  const save = async () => {
    if (!draft) return;
    if (!draft.title.trim()) { setErr("Give it a title first."); return; }
    setErr(""); setBusy("save");
    try {
      const dup = await api.duplicateCheck({ title: draft.title, ingredients: draft.ingredients });
      if (dup.matches.length) { setDupes(dup.matches); setBusy(""); return; }
    } catch { /* non-fatal: fall through to create */ }
    await doCreate();
  };
  const clarify = async () => {
    if (!draft || !ask.trim()) return;
    setErr(""); setBusy("clarify");
    try {
      const res = await api.refineDraft({ ...draft, category: catValue }, ask.trim());
      setDraft(res.draft); setQuestions(res.questions); setAsk("");
    } catch (e: any) { setErr(e.message || "Assistant unavailable."); }
    finally { setBusy(""); }
  };
  const skipBatch = () => { if (moreInBatch) gotoBatch(qIndex + 1); else onClose(); };

  const onPick = (e: ChangeEvent<HTMLInputElement>) => {
    if (e.target.files) setFiles((p) => [...p, ...Array.from(e.target.files!)]);
  };
  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    if (e.dataTransfer.files?.length) setFiles((p) => [...p, ...Array.from(e.dataTransfer.files)]);
  };

  const shell = (body: ReactNode) => (
    <div className="rb-modal-bg" onClick={onClose}>
      <div className="rb-modal add-recipe" onClick={(e) => e.stopPropagation()}>{body}</div>
    </div>
  );

  return shell(
    <>
      <button className="close" onClick={onClose}>×</button>
      <div className="kicker">New recipe{moreInBatch || (batch && queue.length) ? ` · ${qIndex + 1} of ${queue.length}` : ""}</div>
      <h2 style={{ marginTop: 2 }}>Add a recipe</h2>

      <div className="row" style={{ marginTop: 10 }}>
        <div className="seg">
          <button className={kind === "meal" ? "on" : ""} onClick={() => switchKind("meal")}>Kitchen</button>
          <button className={kind === "beverage" ? "on" : ""} onClick={() => switchKind("beverage")}>Bar</button>
        </div>
        {!isAdmin ? (
          <label className="fld">Category
            <span className="fld-fixed" title="New recipes are added to To Try for an admin to review">To Try</span>
          </label>
        ) : kind === "meal" ? (
          <label className="fld">Category
            <select value={category} onChange={(e) => setCategory(e.target.value)}>
              {catOptions.map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
          </label>
        ) : (
          <label className="fld">Base spirit
            <select value={spirit} onChange={(e) => setSpirit(e.target.value)}>
              {SPIRITS.map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
          </label>
        )}
        <div className="seg" style={{ marginLeft: "auto" }}>
          <button className={mode === "paste" ? "on" : ""} onClick={() => switchMode("paste")}>Paste</button>
          <button className={mode === "manual" ? "on" : ""} onClick={() => switchMode("manual")}>Manual</button>
          <button className={mode === "upload" ? "on" : ""} onClick={() => switchMode("upload")}>Upload</button>
        </div>
      </div>

      {mode === "paste" && !draft && (
        <div style={{ marginTop: 14 }}>
          <textarea className="dump" value={text} onChange={(e) => setText(e.target.value)}
            placeholder={"Paste a recipe however it comes — a wall of text, a list, a screenshot's words. The assistant will structure it, and you can fix anything before saving."} />
          <div className="row" style={{ marginTop: 10 }}>
            <button className="btn primary" onClick={genPreview} disabled={busy === "draft" || text.trim().length < 8}>
              {busy === "draft" ? <><Spinner /> &nbsp;Reading…</> : "✨ Generate preview"}
            </button>
            {err && <span className="err">{err}</span>}
          </div>
        </div>
      )}

      {mode === "upload" && !draft && (
        <div style={{ marginTop: 14 }}>
          <label className="dropzone" onDragOver={(e) => e.preventDefault()} onDrop={onDrop}>
            <input type="file" accept={ACCEPT} multiple onChange={onPick} hidden />
            <div className="dz-big">Drop files or photos here, or click to choose</div>
            <div className="dz-sub">PDF · Word · text · images. You can also just paste a screenshot (Ctrl/⌘+V).</div>
          </label>
          {files.length > 0 && (
            <div className="file-chips">
              {files.map((f, i) => (
                <span key={i} className="file-chip">
                  {f.name || `pasted image ${i + 1}`}
                  <button onClick={() => setFiles(files.filter((_, n) => n !== i))} title="Remove">×</button>
                </span>
              ))}
            </div>
          )}
          <div className="row" style={{ marginTop: 10 }}>
            <input className="rb-search" style={{ flex: 1 }} value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="…or paste a recipe URL (blog, allrecipes, etc.)" />
          </div>
          <label className="opt" style={{ marginTop: 10 }}>
            <input type="checkbox" checked={batch} onChange={() => setBatch(!batch)} disabled={!!url.trim()} />
            <span><b>Each file is a separate recipe</b> — review + save them one at a time (digitize a whole recipe box). Off = all files are one recipe (a two-page spread, front/back of a card).</span>
          </label>
          <div className="row" style={{ marginTop: 12 }}>
            <button className="btn primary" onClick={extract} disabled={busy === "draft" || (!files.length && !url.trim())}>
              {busy === "draft" ? <><Spinner /> &nbsp;Reading…</> : "✨ Extract recipe"}
            </button>
            {err && <span className="err">{err}</span>}
          </div>
        </div>
      )}

      {draft && (
        <div className="draft-editor">
          <div className="rule" />
          {questions.length > 0 && (
            <div className="draft-q">
              <b>The assistant wants to check:</b>
              <ul>{questions.map((q, i) => <li key={i}>{q}</li>)}</ul>
              <span className="rb-count">Answer below with “Clarify”, or just edit the card directly.</span>
            </div>
          )}

          <label className="fld wide">Title
            <input value={draft.title} onChange={(e) => patch({ title: e.target.value })}
              placeholder="e.g. Weeknight Green Curry" />
          </label>
          <label className="fld wide">Meta <span className="hint">(yield / time / notes)</span>
            <input value={draft.meta} onChange={(e) => patch({ meta: e.target.value })}
              placeholder="Serves 4 · 35 min" />
          </label>

          <h4 className="sec-h">{kind === "beverage" ? "Spec" : "Ingredients"}</h4>
          <EditList items={draft.ingredients} onChange={(v) => patch({ ingredients: v })}
            placeholder={kind === "beverage" ? "2 oz gin" : "1 lb chicken thighs"} />

          <h4 className="sec-h" style={{ marginTop: 14 }}>Method</h4>
          <EditList items={draft.instructions} onChange={(v) => patch({ instructions: v })}
            ordered multiline placeholder="Describe the step…" />

          {draft.source && (
            <label className="fld wide">Source
              <input value={draft.source} onChange={(e) => patch({ source: e.target.value })} />
            </label>
          )}

          <div className="clarify">
            <input className="rb-search" style={{ flex: 1 }} value={ask}
              onChange={(e) => setAsk(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && clarify()}
              placeholder="Ask the assistant to adjust — “make it vegan”, “halve it”, “convert to grams”…" />
            <button className="btn ghost sm" onClick={clarify} disabled={busy === "clarify" || !ask.trim()}>
              {busy === "clarify" ? <><Spinner /> &nbsp;…</> : "Clarify"}
            </button>
          </div>

          {dupes.length > 0 && (
            <div className="dup-warn">
              <b>You may already have this:</b>
              {dupes.map((m) => (
                <div key={m.id} className="dup-row">
                  <span>{m.title} <small>· {m.category}</small></span>
                  {isAdmin && (
                    <button className="btn ghost sm" onClick={() => doReplace(m.id)} disabled={busy === "save"}>Replace it</button>
                  )}
                </div>
              ))}
              <div className="row" style={{ marginTop: 8 }}>
                <button className="btn primary sm" onClick={doCreate} disabled={busy === "save"}>Keep both (save new)</button>
                <button className="btn ghost sm" onClick={() => setDupes([])}>Cancel</button>
              </div>
            </div>
          )}

          {err && <div className="err" style={{ marginTop: 8 }}>{err}</div>}
          {dupes.length === 0 && (
            <div className="row" style={{ marginTop: 16 }}>
              <button className="btn primary" onClick={save} disabled={busy === "save"}>
                {busy === "save" ? <><Spinner /> &nbsp;Saving…</> : moreInBatch ? "Save & next" : "Save recipe"}
              </button>
              {moreInBatch && <button className="btn ghost" onClick={skipBatch}>Skip this one</button>}
              <button className="btn ghost" onClick={onClose}>Cancel</button>
              <span className="rb-count" style={{ marginLeft: "auto" }}>
                Saving to <b>{catValue}</b>{kind === "beverage" ? ` · ${spirit}` : ""}
              </span>
            </div>
          )}
        </div>
      )}
    </>,
  );
}
