import { useState } from "react";
import type { ReactNode } from "react";
import { api } from "../api";
import type { PlanProposalItem } from "../types";
import { Spinner } from "./ui";

const SLOTS: [string, string][] = [
  ["breakfast", "Breakfast"], ["lunch", "Lunch"], ["dinner", "Dinner"],
  ["snack", "Snack"], ["drink", "Drink of the day"],
];
const DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const slotLabel = (s: string) =>
  ({ breakfast: "Breakfast", lunch: "Lunch", dinner: "Dinner", snack: "Snack", drink: "Drink" }[s] || s);

type Row = PlanProposalItem & { status: "pending" | "accepted" | "skipped"; seen: string[] };

export function AiPlannerModal({ weeks, weekLabels, onClose, onDone }: {
  weeks: string[][];
  weekLabels: string[];
  onClose: () => void;
  onDone: () => void;
}) {
  const [phase, setPhase] = useState<"setup" | "review">("setup");
  const [weekOn, setWeekOn] = useState<boolean[]>([true, false, false]);
  const [dayOn, setDayOn] = useState<Set<string>>(() => new Set(weeks[0] || []));
  const [slots, setSlots] = useState<Set<string>>(new Set(["dinner"]));
  const [optimize, setOptimize] = useState(true);
  const [pairing, setPairing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [rows, setRows] = useState<Row[]>([]);

  const toggleSlot = (s: string) => {
    const next = new Set(slots); next.has(s) ? next.delete(s) : next.add(s); setSlots(next);
  };
  // Toggling a week on selects all its days; off clears them. Days can then be turned off individually.
  const toggleWeek = (i: number) => {
    const on = !weekOn[i];
    setWeekOn(weekOn.map((v, n) => (n === i ? on : v)));
    setDayOn((prev) => { const next = new Set(prev); weeks[i].forEach((d) => on ? next.add(d) : next.delete(d)); return next; });
  };
  const toggleDay = (d: string) =>
    setDayOn((prev) => { const next = new Set(prev); next.has(d) ? next.delete(d) : next.add(d); return next; });

  const build = async () => {
    const dates = [...dayOn].sort();
    if (!dates.length || slots.size === 0) { setErr("Pick at least one day and one meal."); return; }
    setErr(""); setBusy(true);
    try {
      const res = await api.proposePlan({
        dates, slots: [...slots], optimize_shopping: optimize, drink_pairing: pairing,
      });
      if (!res.items.length) { setErr("Those slots are already filled — nothing to plan."); setBusy(false); return; }
      setRows(res.items.map((it) => ({ ...it, status: "pending", seen: it.recipe_id ? [it.recipe_id] : [] })));
      setPhase("review");
    } catch (e: any) { setErr(e.message || "Assistant unavailable."); }
    finally { setBusy(false); }
  };

  const accept = async (i: number) => {
    const r = rows[i];
    await api.addPlan({ date: r.date, slot: r.slot, recipe_id: r.recipe_id, title: r.recipe_id ? "" : r.title, servings: 2 });
    setRows((rs) => rs.map((x, n) => (n === i ? { ...x, status: "accepted" } : x)));
  };
  const skip = (i: number) =>
    setRows((rs) => rs.map((x, n) => (n === i ? { ...x, status: "skipped" } : x)));
  const swap = async (i: number) => {
    const r = rows[i];
    setRows((rs) => rs.map((x, n) => (n === i ? { ...x, status: "pending", title: "…" } : x)));
    try {
      const excl = r.ptype === "wine" ? [r.title.replace("🍷 ", "")] : r.seen;
      const res = await api.swapPlan({ date: r.date, slot: r.slot, ptype: r.ptype, exclude_ids: excl });
      if (res.item) {
        const it = res.item;
        setRows((rs) => rs.map((x, n) => (n === i
          ? { ...it, status: "pending", seen: [...r.seen, ...(it.recipe_id ? [it.recipe_id] : [it.title.replace("🍷 ", "")])] }
          : x)));
      } else {
        setRows((rs) => rs.map((x, n) => (n === i ? { ...r } : x)));
      }
    } catch { setRows((rs) => rs.map((x, n) => (n === i ? { ...r } : x))); }
  };
  const acceptAll = async () => {
    for (let i = 0; i < rows.length; i++) if (rows[i].status === "pending") await accept(i);
  };

  const shell = (body: ReactNode) => (
    <div className="rb-modal-bg" onClick={onClose}>
      <div className="rb-modal ai-planner" onClick={(e) => e.stopPropagation()}>{body}</div>
    </div>
  );

  if (phase === "setup") {
    return shell(
      <>
        <button className="close" onClick={onClose}>×</button>
        <div className="kicker">Meal planner</div>
        <h2 style={{ marginTop: 2 }}>✨ Plan with AI</h2>
        <p className="rb-count" style={{ marginTop: 4 }}>
          Builds a plan from your library. You'll accept, swap, or skip each pick.
        </p>

        <h4 className="sec-h" style={{ marginTop: 16 }}>Weeks & days</h4>
        <div className="wk-stack">
          {weeks.map((wdates, i) => (
            <div key={i} className="wk-block">
              <button className={"chip pick" + (weekOn[i] ? " on" : "")} onClick={() => toggleWeek(i)}>{weekLabels[i]}</button>
              {weekOn[i] && (
                <div className="day-row">
                  {wdates.map((d, di) => (
                    <button key={d} className={"chip pick day" + (dayOn.has(d) ? " on" : "")} onClick={() => toggleDay(d)}>
                      {DOW[di]} <small>{d.slice(5)}</small>
                    </button>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>

        <h4 className="sec-h" style={{ marginTop: 16 }}>Meals to plan</h4>
        <div className="chip-row">
          {SLOTS.map(([v, l]) => (
            <button key={v} className={"chip pick" + (slots.has(v) ? " on" : "")} onClick={() => toggleSlot(v)}>{l}</button>
          ))}
        </div>

        <h4 className="sec-h" style={{ marginTop: 16 }}>Options</h4>
        <label className="opt"><input type="checkbox" checked={optimize} onChange={() => setOptimize(!optimize)} />
          <span><b>Optimize shopping</b> — favor meals that share ingredients so one trip covers more.</span></label>
        <label className="opt"><input type="checkbox" checked={pairing} onChange={() => setPairing(!pairing)} />
          <span><b>Drink pairing</b> — a wine or cocktail suggested for each dinner, added as its own Drink card.</span></label>

        {err && <div className="err" style={{ marginTop: 10 }}>{err}</div>}
        <div className="row" style={{ marginTop: 18 }}>
          <button className="btn primary" onClick={build} disabled={busy}>
            {busy ? <><Spinner /> &nbsp;Thinking…</> : "Build the plan"}
          </button>
          <button className="btn ghost" onClick={onClose}>Cancel</button>
        </div>
      </>,
    );
  }

  // review phase — grouped by date
  const dates = [...new Set(rows.map((r) => r.date))].sort();
  const remaining = rows.filter((r) => r.status === "pending").length;
  return shell(
    <>
      <button className="close" onClick={onClose}>×</button>
      <div className="kicker">Meal planner · review</div>
      <div className="row" style={{ alignItems: "baseline" }}>
        <h2 style={{ marginTop: 2 }}>Your proposed plan</h2>
        <span className="rb-count" style={{ marginLeft: "auto" }}>{remaining} left to decide</span>
      </div>

      <div className="ai-review">
        {dates.map((d) => {
          const dow = new Date(d + "T00:00:00").toLocaleDateString(undefined, { weekday: "long" });
          return (
            <div key={d} className="ai-day">
              <div className="ai-day-h">{dow} · {d.slice(5)}</div>
              {rows.map((r, i) => r.date !== d ? null : (
                <div key={i} className={"ai-pick " + r.status}>
                  <div className="ai-pick-main">
                    <span className="slot-badge">{slotLabel(r.slot)}{r.ptype === "wine" ? " · wine" : r.ptype === "cocktail" ? " · cocktail" : ""}</span>
                    <div className="ai-pick-title">{r.title}</div>
                    {r.why && <div className="ai-why">{r.why}</div>}
                  </div>
                  {r.status === "pending" ? (
                    <div className="ai-pick-btns">
                      <button className="btn primary sm" onClick={() => accept(i)}>Accept</button>
                      <button className="btn ghost sm" onClick={() => swap(i)}>Swap</button>
                      <button className="btn ghost sm" onClick={() => skip(i)}>Skip</button>
                    </div>
                  ) : (
                    <div className={"ai-status " + r.status}>{r.status === "accepted" ? "✓ Added" : "Skipped"}</div>
                  )}
                </div>
              ))}
            </div>
          );
        })}
      </div>

      <div className="row" style={{ marginTop: 16 }}>
        <button className="btn primary" onClick={acceptAll} disabled={!remaining}>Accept all remaining</button>
        <button className="btn ghost" onClick={() => { onDone(); onClose(); }}>Done</button>
      </div>
    </>,
  );
}
