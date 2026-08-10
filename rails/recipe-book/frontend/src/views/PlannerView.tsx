import { useEffect, useState } from "react";
import type { DragEvent } from "react";
import { api } from "../api";
import { Empty, Spinner } from "../components/ui";
import { AiPlannerModal } from "../components/AiPlannerModal";
import type { PlannerEntry } from "../types";

// Local YYYY-MM-DD. Never use toISOString() here: it serializes in UTC, so for a
// user west of Greenwich an evening date rolls a day forward — the cell's date string
// would then disagree with its weekday label and with what the backend stored, cards
// would fail `e.date === d` and vanish, and drops would save the wrong day.
const ymd = (x: Date) =>
  `${x.getFullYear()}-${String(x.getMonth() + 1).padStart(2, "0")}-${String(x.getDate()).padStart(2, "0")}`;
function weekDates(offset = 0): string[] {
  const d = new Date();
  d.setHours(0, 0, 0, 0);                                       // avoid DST/time-of-day drift
  d.setDate(d.getDate() + offset * 7 - ((d.getDay() + 6) % 7)); // back to local Monday
  return Array.from({ length: 7 }, (_, i) => {
    const x = new Date(d); x.setDate(d.getDate() + i);
    return ymd(x);
  });
}
const DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const TODAY = ymd(new Date());

// Meal slots — Dinner is the default; a card's slot is editable inline.
const SLOTS: [string, string][] = [
  ["breakfast", "Breakfast"], ["lunch", "Lunch"], ["snack", "Snack"],
  ["drink", "Drink"], ["dinner", "Dinner"],
];
const normSlot = (s: string) => {
  const v = (s || "dinner").toLowerCase();
  if (v === "drinks") return "drink";
  return SLOTS.some(([k]) => k === v) ? v : "dinner";
};
const slotLabel = (s: string) => SLOTS.find(([k]) => k === normSlot(s))?.[1] ?? "Dinner";

export function PlannerView({ onOpen, refresh, bump }: {
  onOpen: (id: string) => void; refresh: number; bump: () => void;
}) {
  const [base, setBase] = useState(0);
  const [entries, setEntries] = useState<PlannerEntry[] | null>(null);
  const [tray, setTray] = useState<PlannerEntry[]>([]);
  const [dropTarget, setDropTarget] = useState<string | null>(null);   // date, "" (tray), or null
  const [dragging, setDragging] = useState(false);
  const [aiOpen, setAiOpen] = useState(false);
  const [pairing, setPairing] = useState<{ id: number; loading: boolean } | null>(null);
  const [pairErr, setPairErr] = useState("");
  const weeks = Array.from({ length: 3 }, (_, i) => weekDates(base + i));  // this week + next two
  const rangeStart = weeks[0][0];
  const rangeEnd = weeks[2][6];

  const reload = () => {
    api.planner(rangeStart, rangeEnd).then((r) => setEntries(r.entries)).catch(() => setEntries([]));
    api.planTray().then((r) => setTray(r.entries)).catch(() => setTray([]));
  };
  useEffect(() => {
    setEntries(null);
    reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [base, refresh]);

  const del = async (id: number) => { await api.deletePlan(id); reload(); bump(); };
  const setSlot = async (id: number, slot: string) => { await api.updatePlan(id, { slot }); reload(); };

  // Manual beverage pairing: suggest + add a drink for a given dinner's day.
  const pairDrink = async (e: PlannerEntry, ptype: "cocktail" | "wine") => {
    setPairErr("");
    setPairing({ id: e.id, loading: true });
    try {
      await api.pairDrink({ date: e.date, ptype });
      setPairing(null);
      reload(); bump();
    } catch (err: any) {
      setPairing(null);
      setPairErr(err?.message || "Couldn't find a pairing.");
    }
  };
  const weekName = (off: number, dates: string[]) =>
    off === 0 ? "This week" : off === 1 ? "Next week" : `Week of ${dates[0].slice(5)}`;

  // --- drag & drop -----------------------------------------------------------
  const onDragStart = (ev: DragEvent, id: number) => {
    ev.dataTransfer.setData("text/plain", String(id));
    ev.dataTransfer.effectAllowed = "move";
    setDragging(true);
  };
  const onDragEnd = () => { setDragging(false); setDropTarget(null); };
  const allowDrop = (ev: DragEvent, target: string) => { ev.preventDefault(); setDropTarget(target); };
  const onDrop = async (ev: DragEvent, date: string) => {
    ev.preventDefault();
    setDropTarget(null); setDragging(false);
    const id = Number(ev.dataTransfer.getData("text/plain"));
    if (!id) return;
    await api.updatePlan(id, { date });
    reload(); bump();
  };

  const chip = (e: PlannerEntry, inTray = false) => {
    const canPair = !inTray && !!e.date && normSlot(e.slot) === "dinner";
    const open = pairing?.id === e.id;
    return (
    <div className={"plan-entry" + (inTray ? " tray" : "")} key={e.id} draggable
      onDragStart={(ev) => onDragStart(ev, e.id)} onDragEnd={onDragEnd}>
      <div className="pe-body">
        <select className="slot-sel" value={normSlot(e.slot)} title="Meal slot"
          onChange={(ev) => setSlot(e.id, ev.target.value)}
          onClick={(ev) => ev.stopPropagation()} onMouseDown={(ev) => ev.stopPropagation()}>
          {SLOTS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </select>
        <div className="pe-title" style={{ cursor: e.recipe_id ? "pointer" : "default" }}
          onClick={() => e.recipe_id && onOpen(e.recipe_id)}>
          {e.recipe?.title || e.title || "—"}
        </div>
        {open && (
          <div className="pe-pair" onMouseDown={(ev) => ev.stopPropagation()}>
            {pairing?.loading ? (
              <span className="pe-pair-busy"><Spinner /> &nbsp;finding a drink…</span>
            ) : (
              <>
                <span className="pe-pair-lbl">Pair</span>
                <button className="btn ghost sm" title="Suggest a cocktail from your Bar"
                  onClick={(ev) => { ev.stopPropagation(); pairDrink(e, "cocktail"); }}>🍸 Cocktail</button>
                <button className="btn ghost sm" title="Suggest a wine style"
                  onClick={(ev) => { ev.stopPropagation(); pairDrink(e, "wine"); }}>🍷 Wine</button>
                <button className="pe-pair-x" title="Cancel"
                  onClick={(ev) => { ev.stopPropagation(); setPairing(null); }}>×</button>
              </>
            )}
          </div>
        )}
      </div>
      <div className="pe-actions">
        {canPair && (
          <button className="pe-pair-btn" title="Pair a drink"
            onMouseDown={(ev) => ev.stopPropagation()}
            onClick={(ev) => { ev.stopPropagation(); setPairErr(""); setPairing(open ? null : { id: e.id, loading: false }); }}>🍷</button>
        )}
        <button className="pe-x" onClick={() => del(e.id)} title="Remove">×</button>
      </div>
    </div>
    );
  };

  return (
    <div className={dragging ? "planner dragging" : "planner"}>
      <div className="row" style={{ marginBottom: 16 }}>
        <button className="btn ghost sm" onClick={() => setBase(base - 1)}>‹ Prev</button>
        <strong style={{ fontFamily: "var(--serif)", fontSize: 18 }}>
          {base === 0 ? "Next 3 weeks" : `From ${rangeStart.slice(5)}`}
        </strong>
        <button className="btn ghost sm" onClick={() => setBase(base + 1)}>Next ›</button>
        {base !== 0 && <button className="btn ghost sm" onClick={() => setBase(0)}>Today</button>}
        <button className="btn primary sm" style={{ marginLeft: "auto" }} onClick={() => setAiOpen(true)}>
          ✨ Plan with AI
        </button>
      </div>
      <div className="rb-count" style={{ marginBottom: 14 }}>
        Drag a card from the tray below onto any day, click a slot to change the meal, or 🍷 a dinner to add a drink pairing.
      </div>
      {pairErr && <div className="err" style={{ marginBottom: 12 }}>{pairErr}</div>}

      {!entries ? <Empty><Spinner /> &nbsp;Loading…</Empty> : (
        <div style={{ display: "grid", gap: 22 }}>
          {weeks.map((dates, wi) => (
            <div key={wi}>
              <div className="week-label">{weekName(base + wi, dates)}</div>
              <div className="plan-grid">
                {dates.map((d, i) => (
                  <div className={"plan-day" + (d === TODAY ? " today" : "") + (dropTarget === d ? " drop" : "")}
                    key={d} onDragOver={(ev) => allowDrop(ev, d)}
                    onDragLeave={() => setDropTarget((t) => (t === d ? null : t))}
                    onDrop={(ev) => onDrop(ev, d)}>
                    <h5>{DOW[i]} · {d.slice(5)}</h5>
                    {entries.filter((e) => e.date === d).map((e) => chip(e))}
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* unassigned tray */}
      <div className={"plan-tray" + (dropTarget === "" ? " drop" : "")}
        onDragOver={(ev) => allowDrop(ev, "")}
        onDragLeave={() => setDropTarget((t) => (t === "" ? null : t))}
        onDrop={(ev) => onDrop(ev, "")}>
        <div className="tray-head">
          <span>Tray · unplanned</span>
          <span className="rb-count">{tray.length ? "drag onto a day →" : "empty"}</span>
        </div>
        {tray.length === 0
          ? <div className="tray-empty">Add recipes from the Kitchen or Bar tabs — they land here, ready to drag onto a day.</div>
          : <div className="tray-cards">{tray.map((e) => chip(e, true))}</div>}
      </div>

      {aiOpen && (
        <AiPlannerModal
          weeks={weeks}
          weekLabels={weeks.map((dates, i) => weekName(base + i, dates))}
          onClose={() => setAiOpen(false)}
          onDone={() => { reload(); bump(); }} />
      )}
    </div>
  );
}
