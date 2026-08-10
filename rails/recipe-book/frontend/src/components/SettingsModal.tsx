import { useEffect, useState } from "react";
import { api } from "../api";
import type { IconStatus, PlanSettings } from "../types";
import { Spinner } from "./ui";

// Admin-only meal-plan settings. Gated by the gear in the header (shown only when the
// gateway reports the user as admin); the backend re-checks on save.
export function SettingsModal({ initial, onClose, onSaved }: {
  initial: PlanSettings;
  onClose: () => void;
  onSaved: (s: { plan_retention_days: number; plan_recency_days: number }) => void;
}) {
  const [retention, setRetention] = useState(String(initial.plan_retention_days));
  const [recency, setRecency] = useState(String(initial.plan_recency_days));
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  // Recipe icons (admin): status + broker-heavy (re)generation. Poll while a run is in flight.
  const [icons, setIcons] = useState<IconStatus | null>(null);
  const [iconErr, setIconErr] = useState("");
  const loadIcons = async () => {
    try { setIcons(await api.iconStatus()); } catch (e: any) { setIconErr(e?.message || "Couldn't read icon status."); }
  };
  useEffect(() => { loadIcons(); }, []);
  useEffect(() => {
    if (!icons?.running) return;
    const t = setInterval(loadIcons, 4000);
    return () => clearInterval(t);
  }, [icons?.running]);

  const regen = async (force: boolean) => {
    if (force && !window.confirm(
      "Re-author every recipe's icon subject and re-render all icons? This holds the GPU for " +
      "a while (~an hour) — other AI features across the platform will queue behind it until it finishes.")) return;
    setIconErr("");
    try { setIcons(await api.regenIcons(force)); } catch (e: any) { setIconErr(e?.message || "Couldn't start."); }
  };

  const [rLo, rHi] = initial.ranges.plan_retention_days;
  const [cLo, cHi] = initial.ranges.plan_recency_days;

  const save = async () => {
    setErr(""); setBusy(true);
    try {
      const res = await api.putSettings({
        plan_retention_days: Number(retention),
        plan_recency_days: Number(recency),
      });
      onSaved(res);
      onClose();
    } catch (e: any) {
      setErr(e?.message || "Couldn't save settings.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="rb-modal-bg" onClick={onClose}>
      <div className="rb-modal" onClick={(e) => e.stopPropagation()} style={{ maxWidth: 470 }}>
        <button className="close" onClick={onClose}>×</button>
        <div className="kicker">Admin</div>
        <h2 style={{ marginTop: 2 }}>Meal-plan settings</h2>

        <div className="set-fld">
          <label>History retention <small>days</small></label>
          <input type="number" min={rLo} max={rHi} value={retention}
            onChange={(e) => setRetention(e.target.value)} />
          <p className="set-help">
            Past entries older than this are removed by the nightly cleanup; recent weeks stay
            browsable via ‹ Prev. Default {initial.defaults.plan_retention_days} (≈6 months). Range {rLo}–{rHi}.
          </p>
        </div>

        <div className="set-fld">
          <label>AI recency window <small>days</small></label>
          <input type="number" min={cLo} max={cHi} value={recency}
            onChange={(e) => setRecency(e.target.value)} />
          <p className="set-help">
            “Plan with AI” won’t re-suggest a meal planned within this many days (recent past or
            already scheduled). 0 turns the filter off. Default {initial.defaults.plan_recency_days} (≈6 weeks). Range {cLo}–{cHi}.
          </p>
        </div>

        <div className="set-fld">
          <label>Recipe icons</label>
          <p className="set-help">
            {icons
              ? <>{icons.ready}/{icons.total} icons rendered{icons.pending ? `, ${icons.pending} missing` : " — all present"}.</>
              : "Loading…"}
            {icons?.running && (
              <> &nbsp;<Spinner /> {icons.phase === "subjects" ? "Authoring subjects…" : "Rendering…"} runs in the background — you can close this.</>
            )}
          </p>
          <p className="set-help">
            Distinctive per-recipe icons. “Generate missing” only covers new recipes;
            “Regenerate all” re-does everything on the GPU (slow, and other AI features queue behind it).
          </p>
          <div className="row" style={{ gap: 8 }}>
            <button className="btn ghost" onClick={() => regen(false)}
              disabled={!!icons?.running || icons?.pending === 0}>
              Generate missing
            </button>
            <button className="btn ghost" onClick={() => regen(true)} disabled={!!icons?.running}>
              Regenerate all
            </button>
          </div>
          {iconErr && <div className="err" style={{ marginTop: 6 }}>{iconErr}</div>}
        </div>

        {err && <div className="err" style={{ marginTop: 6 }}>{err}</div>}
        <div className="row" style={{ marginTop: 16 }}>
          <button className="btn primary" onClick={save} disabled={busy}>
            {busy ? <><Spinner /> &nbsp;Saving…</> : "Save"}
          </button>
          <button className="btn ghost" onClick={onClose}>Cancel</button>
        </div>
      </div>
    </div>
  );
}
