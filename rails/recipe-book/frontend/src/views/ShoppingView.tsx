import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import { Empty, Spinner } from "../components/ui";
import type { PlannerEntry, ShoppingItem } from "../types";

// Local YYYY-MM-DD — must match PlannerView (never toISOString(), which is UTC and
// shifts the day for western timezones, desyncing the shopping range from the plan).
const ymd = (x: Date) =>
  `${x.getFullYear()}-${String(x.getMonth() + 1).padStart(2, "0")}-${String(x.getDate()).padStart(2, "0")}`;
function weekDates(offset = 0): string[] {
  const d = new Date();
  d.setHours(0, 0, 0, 0);
  d.setDate(d.getDate() + offset * 7 - ((d.getDay() + 6) % 7)); // back to local Monday
  return Array.from({ length: 7 }, (_, i) => {
    const x = new Date(d); x.setDate(d.getDate() + i);
    return ymd(x);
  });
}
const WEEK_LABELS = ["This Week", "Next Week", "Week After"];

type GtStatus = { app_configured: boolean; connected: boolean; email: string | null; list_title: string };

export function ShoppingView({ refresh }: { refresh: number }) {
  const [entries, setEntries] = useState<PlannerEntry[] | null>(null);
  const [items, setItems] = useState<ShoppingItem[] | null>(null);
  const [sel, setSel] = useState<Set<number>>(new Set([0, 1, 2])); // default: all three weeks
  // per-user Google Tasks connection state (each user links their own account)
  const [gt, setGt] = useState<GtStatus | null>(null);
  const [gtBusy, setGtBusy] = useState(false);
  const [sending, setSending] = useState(false);
  const [sendMsg, setSendMsg] = useState<string | null>(null);

  const weeks = useMemo(() => [0, 1, 2].map((i) => weekDates(i)), []);

  // Am I connected to Google Tasks? Also re-check when the OAuth popup posts back.
  useEffect(() => {
    const refresh = () => api.gtasksStatus().then(setGt).catch(() => setGt(null));
    refresh();
    const onMsg = (e: MessageEvent) => {
      if (e.data && e.data.source === "gtasks") {
        refresh();
        setSendMsg(e.data.ok ? "✓ Google Tasks connected" : `Couldn't connect: ${e.data.msg || ""}`);
        window.setTimeout(() => setSendMsg(null), 6000);
      }
    };
    window.addEventListener("message", onMsg);
    return () => window.removeEventListener("message", onMsg);
  }, []);

  // load the 3-week plan so we know which recipes fall in which week
  useEffect(() => {
    setEntries(null);
    api.planner(weeks[0][0], weeks[2][6]).then((r) => setEntries(r.entries)).catch(() => setEntries([]));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refresh]);

  const idsByWeek = useMemo(() => {
    const map: string[][] = [[], [], []];
    (entries || []).forEach((e) => {
      if (!e.recipe_id) return;
      const wi = weeks.findIndex((w) => w.includes(e.date));
      if (wi >= 0) map[wi].push(e.recipe_id);
    });
    return map;
  }, [entries, weeks]);

  const selectedIds = useMemo(() => {
    const s = new Set<string>();
    [...sel].forEach((wi) => idsByWeek[wi].forEach((id) => s.add(id)));
    return [...s];
  }, [sel, idsByWeek]);

  // aggregate the shopping list for exactly the selected weeks' recipes
  useEffect(() => {
    if (entries === null) return;
    if (selectedIds.length === 0) { setItems([]); return; }
    setItems(null);
    api.shopping(undefined, undefined, selectedIds.join(",")).then((r) => setItems(r.items)).catch(() => setItems([]));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedIds.join(","), entries === null]);

  const toggle = (wi: number) => {
    const s = new Set(sel); s.has(wi) ? s.delete(wi) : s.add(wi); setSel(s);
  };
  const check = async (it: ShoppingItem) => {
    await api.checkShopping(it.key, !it.checked);
    setItems((items || []).map((x) => (x.key === it.key ? { ...x, checked: !x.checked } : x)));
  };

  const connect = async () => {
    setGtBusy(true); setSendMsg(null);
    try {
      const { url } = await api.gtasksConnect();
      // user-initiated → popup isn't blocked; it posts back a message on completion
      window.open(url, "gtasks", "width=520,height=660");
    } catch (e) {
      setSendMsg(`Couldn't start connect: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setGtBusy(false);
    }
  };

  const disconnect = async () => {
    setGtBusy(true);
    try { await api.gtasksDisconnect(); const s = await api.gtasksStatus(); setGt(s); }
    catch { /* leave state as-is */ }
    finally { setGtBusy(false); }
  };

  const sendToPhone = async () => {
    setSending(true); setSendMsg(null);
    try {
      const r = await api.sendShopping(selectedIds.join(","));
      setSendMsg(r.sent > 0
        ? `📲 Sent ${r.sent} item${r.sent === 1 ? "" : "s"} to “${r.list}” in Google Tasks`
        : (r.detail || "Nothing to send."));
    } catch (e) {
      setSendMsg(`Couldn't send: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setSending(false);
      window.setTimeout(() => setSendMsg(null), 6000);
    }
  };

  const done = (items || []).filter((i) => i.checked).length;
  const toBuy = (items || []).length - done;
  return (
    <div style={{ maxWidth: 680 }}>
      <div className="row" style={{ marginBottom: 14, alignItems: "baseline" }}>
        <strong style={{ fontFamily: "var(--serif)", fontSize: 20 }}>Shopping list</strong>
        {items && <span className="rb-count">{done}/{items.length} items · pantry-covered items dropped</span>}
        {gt?.app_configured && !gt.connected && (
          <button className="chip ghost" style={{ cursor: "pointer", padding: "6px 12px", marginLeft: "auto" }}
            disabled={gtBusy} onClick={connect}
            title="Link your Google account to send the list to your phone (Google Tasks)">
            {gtBusy ? "Opening…" : "🔗 Connect Google Tasks"}
          </button>
        )}
        {gt?.connected && items && items.length > 0 && (
          <button className="chip spirit" style={{ cursor: "pointer", padding: "6px 12px", marginLeft: "auto" }}
            disabled={sending || toBuy === 0} onClick={sendToPhone}
            title={toBuy === 0 ? "Everything is checked off" : `Send ${toBuy} unchecked item(s) to Google Tasks`}>
            {sending ? "Sending…" : "📲 Send to Phone"}
          </button>
        )}
      </div>
      {gt?.connected && (
        <div className="rb-count" style={{ marginBottom: 12 }}>
          Google Tasks: {gt.email || "connected"} ·{" "}
          <button onClick={disconnect} disabled={gtBusy}
            style={{ background: "none", border: 0, color: "var(--accent)", cursor: "pointer", padding: 0, font: "inherit" }}>
            Disconnect
          </button>
        </div>
      )}
      {sendMsg && <div className="rb-count" style={{ marginBottom: 12 }}>{sendMsg}</div>}
      <div className="row" style={{ marginBottom: 16, gap: 8 }}>
        {WEEK_LABELS.map((lbl, wi) => (
          <button key={wi} className={"chip" + (sel.has(wi) ? " spirit" : " ghost")}
            style={{ cursor: "pointer", padding: "8px 14px" }} onClick={() => toggle(wi)}>
            {sel.has(wi) ? "✓ " : ""}{lbl}{idsByWeek[wi]?.length ? ` · ${idsByWeek[wi].length}` : ""}
          </button>
        ))}
      </div>
      {entries === null || items === null ? <Empty><Spinner /> &nbsp;Building…</Empty>
        : items.length === 0 ? (
          <Empty>{sel.size === 0
            ? "Pick a week above to build the list."
            : "Nothing to buy for the selected weeks — plan some recipes (Plan tab), or your pantry already covers them."}</Empty>
        ) : (
          <div className="shop-list">
            {items.map((it) => (
              <label className={"shop-row" + (it.checked ? " on" : "")} key={it.key}>
                <input type="checkbox" checked={it.checked} onChange={() => check(it)} />
                <span>{it.label}</span>
                {it.sources.length > 0 && <span className="src">{it.sources.slice(0, 2).join(", ")}</span>}
              </label>
            ))}
          </div>
        )}
    </div>
  );
}
