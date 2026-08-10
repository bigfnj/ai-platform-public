import { useEffect, useRef, useState } from "react";
import { api, type AnalyzeResult, type FlowerSummary, type Inventory } from "../lib/api";
import { Spinner } from "./ui";
import { ReportView } from "./ReportView";
import { InventoryEditor, type EditState } from "./InventoryEditor";
import { GuidanceBox } from "./GuidanceBox";
import { DescriptionCard } from "./DescriptionCard";
import { ConfirmDialog } from "./ConfirmDialog";

type Step = "upload" | "review" | "result";
type GenMode = "florist" | "analysis";

const EMPTY_EDIT: EditState = { flowers: [], palette: "", arrangement: "" };

export default function Analyze({ onOpenFlower, onSaved }: {
  onOpenFlower: (slug: string) => void;
  onSaved: () => void;
}) {
  const [step, setStep] = useState<Step>("upload");
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<string>("");
  const [dragging, setDragging] = useState(false);

  const [flowerList, setFlowerList] = useState<FlowerSummary[]>([]);
  const [imageToken, setImageToken] = useState("");
  const [edit, setEdit] = useState<EditState>(EMPTY_EDIT);
  const greeneryRef = useRef<string[]>([]);
  const contextRef = useRef<string>("");
  const [guidance, setGuidance] = useState("");

  const [result, setResult] = useState<AnalyzeResult | null>(null);
  const [identifying, setIdentifying] = useState(false);
  const [genMode, setGenMode] = useState<GenMode | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [error, setError] = useState("");
  const inputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => { api.flowers().then((r) => setFlowerList(r.flowers)).catch(() => {}); }, []);

  const pick = (f: File | null | undefined) => {
    if (!f) return;
    if (!/^image\/(jpeg|png|webp)$/.test(f.type)) {
      setError("Choose a JPEG, PNG, or WebP photo.");
      return;
    }
    setError("");
    setFile(f);
    setPreview(URL.createObjectURL(f));
  };

  const startOver = () => {
    setStep("upload"); setFile(null); setPreview(""); setImageToken("");
    setEdit(EMPTY_EDIT); setGuidance(""); setResult(null); setError("");
    greeneryRef.current = []; contextRef.current = "";
  };

  const identify = async () => {
    if (!file) return;
    setIdentifying(true); setError("");
    try {
      const { image_token, inventory } = await api.identify(file);
      setImageToken(image_token);
      setEdit({
        flowers: (inventory.flowers || []).map((fl, i) => ({
          id: `f${Date.now()}-${i}`,
          name: fl.name || "",
          colors: (fl.colors || []).join(", "),
          slug: fl.slug ?? null,
          in_library: !!fl.in_library,
        })),
        palette: inventory.palette || "",
        arrangement: inventory.arrangement || "",
      });
      greeneryRef.current = inventory.greenery || [];
      contextRef.current = inventory.context || "";
      setStep("review");
    } catch (e: any) {
      setError(e?.message || "identification failed");
    } finally {
      setIdentifying(false);
    }
  };

  const buildInventory = (): Inventory => ({
    flowers: edit.flowers
      .filter((f) => f.name.trim())
      .map((f) => ({
        name: f.name.trim(),
        colors: f.colors.split(",").map((c) => c.trim()).filter(Boolean),
      })),
    greenery: greeneryRef.current,
    palette: edit.palette.trim(),
    arrangement: edit.arrangement.trim(),
    context: contextRef.current,
  });

  const generate = async (mode: GenMode) => {
    const inventory = buildInventory();
    if (inventory.flowers.length === 0) { setError("Add at least one flower first."); return; }
    setGenMode(mode); setError("");
    try {
      const r = await api.generate({ image_token: imageToken, inventory, guidance, mode });
      setResult(r); setStep("result"); onSaved();
    } catch (e: any) {
      setError(e?.message || "the writer could not finish — try again");
    } finally {
      setGenMode(null);
    }
  };

  const doDelete = async () => {
    if (!result) return;
    setConfirmDelete(false);
    try { await api.deleteAnalysis(result.id); onSaved(); startOver(); }
    catch (e: any) { setError(e?.message || "delete failed"); }
  };

  // ---- step: upload -------------------------------------------------------
  if (step === "upload") {
    return (
      <div className="bq-analyze">
        <div
          className={"bq-drop" + (dragging ? " dragging" : "") + (preview ? " has-image" : "")}
          onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
          onDragLeave={() => setDragging(false)}
          onDrop={(e) => { e.preventDefault(); setDragging(false); pick(e.dataTransfer.files?.[0]); }}
          onClick={() => inputRef.current?.click()}
        >
          {preview ? (
            <img src={preview} alt="chosen bouquet" className="bq-drop-preview" />
          ) : (
            <div className="bq-drop-hint">
              <div className="bq-drop-icon">💐</div>
              <div><b>Drop a bouquet photo</b> or click to choose</div>
              <div className="bq-drop-sub">JPEG, PNG, or WebP</div>
            </div>
          )}
          <input ref={inputRef} type="file" accept="image/jpeg,image/png,image/webp"
            hidden onChange={(e) => pick(e.target.files?.[0])} />
        </div>
        <div className="bq-actions">
          <button className="bq-btn bq-btn-primary" disabled={!file || identifying} onClick={identify}>
            {identifying ? <><Spinner /> &nbsp;Reading the bouquet…</> : "Identify flowers →"}
          </button>
          {file && !identifying && <button className="bq-btn" onClick={startOver}>Clear</button>}
        </div>
        {identifying && <p className="bq-note">The vision model may take a minute on a cold start.</p>}
        {error && <p className="bq-error">{error}</p>}
      </div>
    );
  }

  // ---- step: review + generate -------------------------------------------
  if (step === "review") {
    return (
      <div className="bq-analyze">
        <div className="bq-result-bar">
          <button className="bq-btn" onClick={startOver}>← Start over</button>
          <span className="bq-result-title">Review the flowers, then write</span>
        </div>
        <div className="bq-review-grid">
          <div className="bq-review-side">
            {preview && <img className="bq-photo" src={preview} alt="the bouquet" />}
            {contextRef.current && (
              <p className="bq-note">Vision noted: {contextRef.current}</p>
            )}
          </div>
          <div className="bq-review-main">
            <InventoryEditor value={edit} onChange={setEdit} options={flowerList} />
            <GuidanceBox value={guidance} onChange={setGuidance} />
            <div className="bq-actions">
              <button className="bq-btn bq-btn-primary" disabled={!!genMode} onClick={() => generate("florist")}>
                {genMode === "florist" ? <><Spinner /> &nbsp;Writing…</> : "💐 Generate description"}
              </button>
              <button className="bq-btn" disabled={!!genMode} onClick={() => generate("analysis")}>
                {genMode === "analysis" ? <><Spinner /> &nbsp;Analyzing…</> : "🔬 Generate analysis report"}
              </button>
            </div>
            {genMode && <p className="bq-note">Loading the writing model — this can take a minute on a cold start.</p>}
            {error && <p className="bq-error">{error}</p>}
          </div>
        </div>
      </div>
    );
  }

  // ---- step: result -------------------------------------------------------
  return (
    <div className="bq-analyze">
      <div className="bq-result-bar">
        <button className="bq-btn" onClick={startOver}>← New bouquet</button>
        <button className="bq-btn" onClick={() => { setResult(null); setError(""); setStep("review"); }}>
          Edit &amp; regenerate
        </button>
        <span className="bq-result-title">{result?.title}</span>
        <span className="bq-spacer" />
        <button className="bq-btn" onClick={() => setConfirmDelete(true)}>Delete</button>
      </div>

      {result && (result.mode === "florist"
        ? <DescriptionCard imageUrl={result.image_url} text={result.report_md} guidance={result.guidance} />
        : <ReportView data={result} onOpenFlower={onOpenFlower} />)}

      {error && <p className="bq-error">{error}</p>}

      {confirmDelete && (
        <ConfirmDialog
          title="Delete this description?"
          body="This can't be undone."
          confirmLabel="Delete" danger
          onConfirm={doDelete} onCancel={() => setConfirmDelete(false)}
        />
      )}
    </div>
  );
}
