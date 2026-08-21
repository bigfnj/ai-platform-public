/* Meeting Atlas — the rail's federated module.
 *
 * Everything renders inside a single `.ma` wrapper (the manifest's css_wrapper), so
 * the platform palette reaches it by inheritance and theme.css can scope every rule.
 *
 * Design notes worth keeping:
 *  - ONE hero figure per view. A dashboard that leads with eight numbers leads with none.
 *  - Charts carry a single series hue; magnitude uses the sequential ramp, and a true
 *    zero gets a hairline rather than the lightest fill so "none" and "a little" can
 *    never be confused.
 *  - Every chart has a table twin, so no value is reachable only by hovering.
 *  - Summaries are rendered as CLAIMS. Each action item's citation is checked against
 *    the transcript by the backend, and the badges say what checked out.
 */

import {
  createContext, useCallback, useContext, useEffect, useLayoutEffect,
  useMemo, useRef, useState, type ReactNode,
} from 'react'
import './theme.css'
import {
  audioUrl, fetchMeeting, fetchMeetings, reindex,
  type Action, type Corpus, type Detail, type Meeting,
} from './api'

const DOW = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
  'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
const BUCKET_S = 30 // must match the backend's indexer

/* ---------------------------------------------------------------- format */
const pad = (n: number) => String(n).padStart(2, '0')

function clock(s: number): string {
  const t = Math.max(0, Math.floor(s))
  const h = Math.floor(t / 3600)
  const m = Math.floor((t % 3600) / 60)
  return (h ? `${h}:${pad(m)}` : `${m}`) + `:${pad(t % 60)}`
}

function hm(sec: number): string {
  const m = Math.round(sec / 60)
  if (m < 60) return `${m}m`
  return `${Math.floor(m / 60)}h${m % 60 ? ` ${m % 60}m` : ''}`
}

/** Hero figure. Under an hour, "0.4 hours" is unreadable — switch the unit rather
 *  than the precision, so the number always carries weight. */
function heroTime(sec: number): { fig: string; unit: string } {
  if (sec < 3600) return { fig: String(Math.round(sec / 60)), unit: 'minutes in meetings' }
  const h = sec / 3600
  return { fig: h >= 10 ? h.toFixed(0) : h.toFixed(1), unit: 'hours in meetings' }
}

const num = (n: number) => n.toLocaleString()
const dt = (s: string) => new Date(s) // backend sends local ISO with no zone
const ymd = (d: Date) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
const hhmm = (d: Date) => `${pad(d.getHours())}:${pad(d.getMinutes())}`
const dlabel = (d: Date) => `${DOW[(d.getDay() + 6) % 7]} ${d.getDate()} ${MON[d.getMonth()]}`
const addDays = (d: Date, n: number) => {
  const x = new Date(d); x.setDate(x.getDate() + n); return x
}

function isoWeekKey(d: Date): string {
  const t = new Date(d.getFullYear(), d.getMonth(), d.getDate())
  t.setDate(t.getDate() + 3 - ((t.getDay() + 6) % 7))
  const wk1 = new Date(t.getFullYear(), 0, 4)
  const n = 1 + Math.round(
    ((t.getTime() - wk1.getTime()) / 864e5 - 3 + ((wk1.getDay() + 6) % 7)) / 7)
  return `${t.getFullYear()}-W${pad(n)}`
}

function isoWeekStart(key: string): Date {
  const [y, w] = key.split('-W').map(Number)
  const jan4 = new Date(y, 0, 4)
  const mon = new Date(jan4)
  mon.setDate(jan4.getDate() - ((jan4.getDay() + 6) % 7) + (w - 1) * 7)
  return mon
}

/** Minimal inline markdown: **bold** and *italic*. Model output uses both. */
function inl(s: string): ReactNode[] {
  const out: ReactNode[] = []
  const re = /\*\*(.+?)\*\*|\*(\S.*?)\*/g
  let last = 0
  let m: RegExpExecArray | null
  let k = 0
  while ((m = re.exec(s))) {
    if (m.index > last) out.push(s.slice(last, m.index))
    if (m[1] !== undefined) out.push(<b key={k++}>{m[1]}</b>)
    else out.push(<i key={k++}>{m[2]}</i>)
    last = m.index + m[0].length
  }
  if (last < s.length) out.push(s.slice(last))
  return out
}

const sum = <T,>(a: T[], f: (x: T) => number) => a.reduce((t, x) => t + f(x), 0)

/* ---------------------------------------------------------------- tooltip */
type TipFn = (node: ReactNode | null, ev?: { clientX: number; clientY: number }) => void
const TipCtx = createContext<TipFn>(() => {})
const useTip = () => useContext(TipCtx)

/** Container width, measured after layout. Charts are drawn at real pixel widths
 *  rather than a guessed padding subtraction — that guess is what put an earlier
 *  build 18px past the viewport. */
function useMeasure<T extends HTMLElement>() {
  const ref = useRef<T | null>(null)
  const [w, setW] = useState(0)
  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    const read = () => setW(el.clientWidth)
    read()
    if (typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver(read)
    ro.observe(el)
    return () => ro.disconnect()
  }, [])
  return [ref, w] as const
}

/* ---------------------------------------------------------------- ramp */
/** Six-step sequential bucket. A true zero returns 0 and gets NO fill — only a
 *  hairline — so an empty cell can never read as a small one. */
function qBucket(v: number, max: number): number {
  if (!v) return 0
  return Math.min(6, Math.max(1, Math.ceil((v / max) * 6)))
}
const qVar = (i: number) => (i ? `var(--ma-q${i})` : 'transparent')
const isDarkFill = (i: number) => i >= 4

/* ---------------------------------------------------------------- pieces */
function Card({ title, note, extra, children }: {
  title?: string; note?: string; extra?: ReactNode; children: ReactNode
}) {
  return (
    <div className="card">
      {title || extra ? (
        <div className="card-head">
          {title ? <h3>{title}</h3> : null}
          {note ? <p>{note}</p> : null}
          <div className="spacer" />
          {extra}
        </div>
      ) : null}
      {children}
    </div>
  )
}

function Tile({ label, value, unit, note, meter }: {
  label: string; value: string; unit?: string; note?: string; meter?: number
}) {
  return (
    <div className="tile">
      <div className="lbl">{label}</div>
      <div className="val">{value}{unit ? <span>{unit}</span> : null}</div>
      {note ? <div className="note">{note}</div> : null}
      {meter != null ? (
        <div className="meter">
          <i style={{ width: `${Math.max(0, Math.min(100, meter)).toFixed(1)}%` }} />
        </div>
      ) : null}
    </div>
  )
}

function Hero({ fig, unit, note }: { fig: string; unit: string; note?: ReactNode }) {
  return (
    <div className="hero">
      <div className="fig">{fig}</div>
      <div className="sub">{unit}{note ? <small>{note}</small> : null}</div>
    </div>
  )
}

const Empty = ({ children }: { children: ReactNode }) =>
  <div className="empty">{children}</div>

const ICON = {
  ok: <path d="M20 6L9 17l-5-5" />,
  warn: <><path d="M12 9v5M12 17.5v.01" /><path d="M10.3 3.9L2.4 18a2 2 0 001.7 3h15.8a2 2 0 001.7-3L13.7 3.9a2 2 0 00-3.4 0z" /></>,
  bad: <path d="M18 6L6 18M6 6l12 12" />,
  dup: <><rect x="9" y="9" width="12" height="12" rx="2" /><path d="M5 15V5a2 2 0 012-2h10" /></>,
  play: <path d="M9 5l10 7-10 7z" />,
}

function Badge({ kind, icon, text, onClick }: {
  kind: '' | 'ok' | 'warn' | 'bad'
  icon: keyof typeof ICON
  text: string
  onClick?: () => void
}) {
  const svg = (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
      {ICON[icon]}
    </svg>
  )
  if (onClick) {
    return (
      <button type="button" className={`badge ${kind}`} onClick={onClick}>
        {svg}<span>{text}</span>
      </button>
    )
  }
  return <span className={`badge ${kind}`}>{svg}<span>{text}</span></span>
}

/** Verification badges for one action item.
 *
 * The backend has already located each cited quote in the real transcript. This only
 * renders the verdict — and every badge pairs its colour with an icon and words, so
 * meaning never rests on hue. */
function actionBadges(a: Action, onJump?: (sec: number) => void): ReactNode[] {
  const out: ReactNode[] = []
  const jump = (sec: number) => onJump ? () => onJump(sec) : undefined
  if (a.quote_missing) {
    out.push(<Badge key="miss" kind="bad" icon="bad" text="quote not in transcript" />)
  } else if (a.ts_mismatch && a.quote_at != null) {
    out.push(<Badge key="mm" kind="warn" icon="warn"
      text={`cited ${clock(a.claimed_at ?? 0)} · found ${clock(a.quote_at)}`}
      onClick={jump(a.quote_at)} />)
  } else if (a.quote_at != null) {
    out.push(<Badge key="ok" kind="ok" icon="ok"
      text={`verified ${clock(a.quote_at)}`} onClick={jump(a.quote_at)} />)
  } else if (a.claimed_at != null) {
    out.push(<Badge key="at" kind="" icon="play" text={clock(a.claimed_at)}
      onClick={jump(a.claimed_at)} />)
  }
  if (a.quote_reused) {
    out.push(<Badge key="dup" kind="warn" icon="dup" text="quote reused" />)
  }
  if (a.due_suspect) {
    out.push(<Badge key="due" kind="warn" icon="warn" text="due date implausible" />)
  }
  return out
}

function TableTwin({ build }: { build: () => ReactNode }) {
  const [open, setOpen] = useState(false)
  return (
    <>
      <button type="button" className="pill" aria-expanded={open}
        onClick={() => setOpen(!open)}>Table</button>
      {open ? <div className="tblwrap">{build()}</div> : null}
    </>
  )
}

function Tbl({ cols, rows }: { cols: { label: string; num?: boolean }[]; rows: (string | number)[][] }) {
  return (
    <table className="tbl">
      <thead>
        <tr>{cols.map((c, i) => <th key={i} className={c.num ? 'num' : undefined}>{c.label}</th>)}</tr>
      </thead>
      <tbody>
        {rows.map((r, i) => (
          <tr key={i}>
            {r.map((v, j) => (
              <td key={j} className={cols[j]?.num ? 'num' : undefined}>{v}</td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  )
}

/* ---------------------------------------------------------------- sparkline */
function Sparkline({ activity, w, h }: { activity: number[]; w: number; h: number }) {
  const max = Math.max(1, ...activity)
  const n = Math.max(1, activity.length)
  const bw = Math.max(1, w / n - 1)
  return (
    <svg width={w} height={h} viewBox={`0 0 ${w} ${h}`} role="img"
      aria-label="speech activity over the meeting">
      {activity.map((v, i) => {
        const bh = v ? Math.max(1, (v / max) * h) : 0
        if (!bh) return null
        return <rect key={i} x={((i * w) / n).toFixed(2)} y={(h - bh).toFixed(2)}
          width={bw.toFixed(2)} height={bh.toFixed(2)}
          rx={Math.min(1.5, bw / 2)} fill="var(--ma-q3)" />
      })}
    </svg>
  )
}

/* ---------------------------------------------------------------- day ribbon */
function DayRibbon({ list, date, onOpen }: {
  list: Meeting[]; date: string; onOpen: (id: string) => void
}) {
  const [ref, W] = useMeasure<HTMLDivElement>()
  const tip = useTip()
  const LANE = 40, AX = 22, padL = 6, padR = 6

  let lo = 7 * 60, hi = 19 * 60
  for (const m of list) {
    lo = Math.min(lo, m.start_min - 30)
    hi = Math.max(hi, m.start_min + m.duration_s / 60 + 30)
  }
  lo = Math.max(0, Math.floor(lo / 60) * 60)
  hi = Math.min(1440, Math.ceil(hi / 60) * 60)
  const span = Math.max(60, hi - lo)
  const iw = Math.max(40, W - padL - padR)
  const x = (min: number) => padL + ((min - lo) / span) * iw
  const stepH = span > 10 * 60 ? 2 : 1
  const ticks: number[] = []
  for (let mn = lo; mn <= hi; mn += 60 * stepH) ticks.push(mn)

  const today = ymd(new Date())
  const now = new Date()
  const nowMin = now.getHours() * 60 + now.getMinutes()

  return (
    <div className="chart" ref={ref}>
      {W > 0 ? (
        <svg width={W} height={LANE + AX} viewBox={`0 0 ${W} ${LANE + AX}`} role="img"
          aria-label="meetings across the day">
          {ticks.map((mn) => (
            <g key={mn}>
              <line x1={x(mn)} y1={0} x2={x(mn)} y2={LANE} className="grid-line" />
              <text x={x(mn)} y={LANE + 15} className="tick" textAnchor="middle">
                {pad(Math.floor(mn / 60) % 24)}:00
              </text>
            </g>
          ))}
          <line x1={padL} y1={LANE} x2={W - padR} y2={LANE} className="axis-line" />
          {list.map((m) => {
            const x0 = x(m.start_min)
            const x1 = Math.max(x0 + 3, x(m.start_min + m.duration_s / 60))
            return (
              <g key={m.id}>
                {/* 2px surface gap between touching blocks, not a border */}
                <rect x={x0 + 1} y={6} width={Math.max(2, x1 - x0 - 2)}
                  height={LANE - 12} rx={4} className="rb-block" />
                <rect x={x0 - 4} y={0} width={x1 - x0 + 8} height={LANE} className="hit"
                  onClick={() => onOpen(m.id)}
                  onMouseMove={(e) => tip(
                    <>
                      <div className="t">{m.title}</div>
                      <div className="r"><span>Time</span>
                        <b>{hhmm(dt(m.start))}–{hhmm(dt(m.end))}</b></div>
                      <div className="r"><span>Length</span><b>{hm(m.duration_s)}</b></div>
                      <div className="r"><span>Words</span><b>{num(m.words)}</b></div>
                    </>, e)}
                  onMouseLeave={() => tip(null)} />
              </g>
            )
          })}
          {date === today && nowMin >= lo && nowMin <= hi ? (
            <line x1={x(nowMin)} y1={2} x2={x(nowMin)} y2={LANE - 2} className="now-line" />
          ) : null}
        </svg>
      ) : null}
    </div>
  )
}

/* ---------------------------------------------------------------- columns */
type ColRow = { label: string; full: string; value: number; note?: string; onClick?: () => void }

function ColumnChart({ rows, height, fmtVal, fmtTick, valueLabel }: {
  rows: ColRow[]; height: number
  fmtVal: (v: number) => string; fmtTick: (v: number) => string; valueLabel: string
}) {
  const [ref, W] = useMeasure<HTMLDivElement>()
  const tip = useTip()
  const H = height, padL = 46, padR = 12, padT = 16, padB = 30
  const iw = Math.max(40, W - padL - padR)
  const ih = H - padT - padB
  const max = Math.max(...rows.map((r) => r.value), 0) || 1

  // clean tick steps
  const raw = max / 4
  const mag = Math.pow(10, Math.floor(Math.log10(raw || 1)))
  const step = ([1, 2, 2.5, 5, 10].map((s) => s * mag).find((s) => s >= raw) ?? 10 * mag)
  const ticks: number[] = []
  for (let v = 0; v <= max + step * 0.001; v += step) ticks.push(Math.round(v * 1000) / 1000)
  const top = ticks[ticks.length - 1] || 1
  const slot = iw / Math.max(1, rows.length)
  const bw = Math.min(24, Math.max(4, slot - 6))
  const peak = rows.reduce((a, b) => (b.value > a.value ? b : a), rows[0])

  return (
    <div className="chart" ref={ref}>
      {W > 0 ? (
        <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`} role="img"
          aria-label={`${valueLabel} per column`}>
          {ticks.map((t) => {
            const y = padT + ih - (t / top) * ih
            return (
              <g key={t}>
                <line x1={padL} y1={y} x2={W - padR} y2={y}
                  className={t ? 'grid-line' : 'axis-line'} />
                <text x={padL - 8} y={y + 4} className="tick" textAnchor="end">
                  {fmtTick(t)}
                </text>
              </g>
            )
          })}
          {rows.map((r, i) => {
            const cx = padL + slot * i + slot / 2
            const bh = (r.value / top) * ih
            const rr = Math.min(4, bh)
            return (
              <g key={r.label + i}>
                {bh > 0 ? (
                  // square at the baseline, 4px rounded data-end
                  <path className="bar" d={
                    `M${cx - bw / 2} ${padT + ih}V${padT + ih - bh + rr}` +
                    `a${rr} ${rr} 0 0 1 ${rr} -${rr}h${bw - 2 * rr}` +
                    `a${rr} ${rr} 0 0 1 ${rr} ${rr}V${padT + ih}Z`} />
                ) : null}
                <text x={cx} y={H - 9} className="tick" textAnchor="middle">{r.label}</text>
                {/* label selectively: only the peak carries a direct value */}
                {r === peak && r.value > 0 ? (
                  <text x={cx} y={padT + ih - bh - 6} className="dlabel"
                    textAnchor="middle">{fmtVal(r.value)}</text>
                ) : null}
                <rect x={cx - slot / 2} y={padT} width={slot} height={ih} className="hit"
                  onClick={r.onClick}
                  onMouseMove={(e) => tip(
                    <>
                      <div className="t">{r.full}</div>
                      <div className="r"><span>{valueLabel}</span><b>{fmtVal(r.value)}</b></div>
                      {r.note ? <div className="r"><span>{r.note}</span></div> : null}
                    </>, e)}
                  onMouseLeave={() => tip(null)} />
              </g>
            )
          })}
        </svg>
      ) : null}
    </div>
  )
}

/* ---------------------------------------------------------------- heatmap */
function Heatmap({ list, onCell }: { list: Meeting[]; onCell?: (col: number) => void }) {
  const tip = useTip()
  const { cells, hours, max } = useMemo(() => {
    const map = new Map<string, number>()
    let loH = 8, hiH = 18
    for (const m of list) {
      const d = dt(m.start)
      const col = (d.getDay() + 6) % 7
      let t = d.getHours() * 60 + d.getMinutes()
      let left = m.duration_s / 60
      while (left > 0.01 && t < 24 * 60) {
        const h = Math.floor(t / 60)
        const take = Math.min(left, 60 - (t % 60))
        const k = `${col}|${h}`
        map.set(k, (map.get(k) ?? 0) + take)
        loH = Math.min(loH, h); hiH = Math.max(hiH, h)
        t += take; left -= take
      }
    }
    const hrs: number[] = []
    for (let h = loH; h <= hiH; h++) hrs.push(h)
    return { cells: map, hours: hrs, max: Math.max(1, ...map.values()) }
  }, [list])

  return (
    <div>
      <div className="hm" style={{ gridTemplateColumns: '42px repeat(7, minmax(0,1fr))' }}>
        <div />
        {DOW.map((d) => <div key={d} className="hlbl">{d}</div>)}
        {hours.map((h) => (
          <div key={h} style={{ display: 'contents' }}>
            <div className="vlbl">{pad(h)}:00</div>
            {DOW.map((_, col) => {
              const v = cells.get(`${col}|${h}`) ?? 0
              const b = qBucket(v, max)
              const common = {
                className: `cell${b ? '' : ' z'}`,
                style: { background: qVar(b) },
                onMouseMove: (e: { clientX: number; clientY: number }) => v ? tip(
                  <>
                    <div className="t">{DOW[col]} {pad(h)}:00</div>
                    <div className="r"><span>In meetings</span>
                      <b>{Math.round(v)} min</b></div>
                  </>, e) : undefined,
                onMouseLeave: () => tip(null),
              }
              return v && onCell
                ? <button type="button" key={col} {...common} onClick={() => onCell(col)} />
                : <div key={col} {...common} />
            })}
          </div>
        ))}
      </div>
      <div className="scale">
        <span>0</span>
        {[1, 2, 3, 4, 5, 6].map((i) => <i key={i} style={{ background: qVar(i) }} />)}
        <span>{Math.round(max)} min in the hour</span>
      </div>
    </div>
  )
}

function heatmapTable(list: Meeting[]): ReactNode {
  const map = new Map<string, number>()
  for (const m of list) {
    const d = dt(m.start)
    const col = (d.getDay() + 6) % 7
    let t = d.getHours() * 60 + d.getMinutes()
    let left = m.duration_s / 60
    while (left > 0.01 && t < 24 * 60) {
      const h = Math.floor(t / 60)
      const take = Math.min(left, 60 - (t % 60))
      map.set(`${col}|${h}`, (map.get(`${col}|${h}`) ?? 0) + take)
      t += take; left -= take
    }
  }
  const rows = [...map.entries()].sort((a, b) => b[1] - a[1]).map(([k, v]) => {
    const [c, h] = k.split('|')
    return [DOW[+c], `${pad(+h)}:00`, Math.round(v)]
  })
  return <Tbl cols={[{ label: 'Day' }, { label: 'Hour' }, { label: 'Minutes', num: true }]}
    rows={rows} />
}

/* ---------------------------------------------------------------- calendar */
function Calendar({ monthKey, byDate, onDay }: {
  monthKey: string; byDate: Map<string, Meeting[]>; onDay: (date: string) => void
}) {
  const tip = useTip()
  const [y, mo] = monthKey.split('-').map(Number)
  const first = new Date(y, mo - 1, 1)
  const startPad = (first.getDay() + 6) % 7
  const days = new Date(y, mo, 0).getDate()
  const today = ymd(new Date())

  const perDay = new Map<string, number>()
  for (const [k, list] of byDate) {
    if (k.startsWith(monthKey)) perDay.set(k, sum(list, (m) => m.duration_s))
  }
  const max = Math.max(1, ...perDay.values())

  const cells: ReactNode[] = []
  for (let i = 0; i < startPad; i++) cells.push(<div key={`p${i}`} className="day out" />)
  for (let d = 1; d <= days; d++) {
    const key = `${y}-${pad(mo)}-${pad(d)}`
    const secs = perDay.get(key) ?? 0
    const n = (byDate.get(key) ?? []).length
    const b = qBucket(secs, max)
    const cls = `day${isDarkFill(b) ? ' dark' : ''}${key === today ? ' today' : ''}`
    const inner = (
      <>
        <div className="n">{d}</div>
        {n ? <div className="c">{hm(secs)}</div> : null}
      </>
    )
    if (secs) {
      cells.push(
        <button type="button" key={key} className={cls} style={{ background: qVar(b) }}
          onClick={() => onDay(key)}
          onMouseMove={(e) => tip(
            <>
              <div className="t">{dlabel(new Date(y, mo - 1, d))}</div>
              <div className="r"><span>Meetings</span><b>{n}</b></div>
              <div className="r"><span>Total</span><b>{hm(secs)}</b></div>
            </>, e)}
          onMouseLeave={() => tip(null)}>{inner}</button>,
      )
    } else {
      cells.push(<div key={key} className={cls}>{inner}</div>)
    }
  }

  return (
    <div>
      <div className="cal">
        {DOW.map((d) => <div key={d} className="dow">{d}</div>)}
        {cells}
      </div>
      <div className="scale">
        <span>none</span>
        {[1, 2, 3, 4, 5, 6].map((i) => <i key={i} style={{ background: qVar(i) }} />)}
        <span>{hm(max)} in a day</span>
      </div>
    </div>
  )
}

/* ---------------------------------------------------------------- meeting row */
function MeetingRow({ m, onOpen }: { m: Meeting; onOpen: (id: string) => void }) {
  const tip = useTip()
  const d = dt(m.start)
  const meta: string[] = [hm(m.duration_s), `${num(m.words)} words`]
  if (m.n_actions) meta.push(`${m.n_actions} action${m.n_actions > 1 ? 's' : ''}`)
  if (m.n_decisions) meta.push(`${m.n_decisions} decision${m.n_decisions > 1 ? 's' : ''}`)
  if (!m.has_summary) meta.push('no summary')
  if (m.flags) meta.push(`${m.flags} flagged`)
  return (
    <button type="button" className="mrow" onClick={() => onOpen(m.id)}>
      <div className="when">{hhmm(d)}<small>{DOW[(d.getDay() + 6) % 7]}</small></div>
      <div>
        <div className="ttl">{m.title}</div>
        <div className="meta">{meta.map((x, i) => <span key={i}>{x}</span>)}</div>
      </div>
      <div className="spark"
        onMouseMove={(e) => tip(
          <>
            <div className="t">Speech activity</div>
            <div className="r"><span>Talk density</span>
              <b>{Math.round((m.density ?? 0) * 100)}%</b></div>
            <div className="r"><span>Pace</span><b>{m.wpm} wpm</b></div>
          </>, e)}
        onMouseLeave={() => tip(null)}>
        <Sparkline activity={m.activity} w={118} h={26} />
      </div>
    </button>
  )
}

/* ---------------------------------------------------------------- digest */
/** The roll-up the rail exists for: what got decided and assigned across a period,
 *  without opening every meeting. Rendered from the list payload, so it costs no
 *  extra request. */
function Digest({ list, period, onOpen }: {
  list: Meeting[]; period: string; onOpen: (id: string, at?: number) => void
}) {
  const decisions = list.flatMap((m) => (m.decisions ?? []).map((text) => ({ text, m })))
  const actions = list.flatMap((m) => (m.actions ?? []).map((a) => ({ a, m })))

  const themes = new Map<string, number>()
  for (const m of list) for (const k of m.keywords ?? []) {
    themes.set(k.t, (themes.get(k.t) ?? 0) + k.n)
  }
  const top = [...themes.entries()].sort((a, b) => b[1] - a[1]).slice(0, 14)

  const owners = new Map<string, { a: Action; m: Meeting }[]>()
  for (const x of actions) {
    const k = x.a.owner || 'Unassigned'
    const arr = owners.get(k) ?? []
    arr.push(x); owners.set(k, arr)
  }
  const uniformMeetings = list.filter(
    (m) => (m.actions ?? []).some((a) => a.due_uniform)).length

  return (
    <div>
      <div className="section">
        <h4>What came up</h4>
        {top.length ? (
          <div className="kw">
            {top.map(([t, n]) => <span key={t}>{t}<b>{n}</b></span>)}
          </div>
        ) : <Empty>No transcript text in this {period}.</Empty>}
      </div>

      <div className="section">
        <h4>Decisions ({decisions.length})</h4>
        {decisions.length ? (
          <ul className="bullets">
            {decisions.map((d, i) => (
              <li key={i}>
                {inl(d.text)}{' '}
                <button type="button" className="crumb" onClick={() => onOpen(d.m.id)}>
                  — {d.m.title}
                </button>
              </li>
            ))}
          </ul>
        ) : (
          <Empty>
            No decisions recorded. A meeting needs a summary for this — see <strong>INGEST.md</strong>.
          </Empty>
        )}
      </div>

      <div className="section">
        <h4>Action items ({actions.length})</h4>
        {uniformMeetings ? (
          <div style={{ marginBottom: 10 }}>
            <Badge kind="warn" icon="warn" text={
              `${uniformMeetings} meeting${uniformMeetings > 1 ? 's' : ''} repeated one due date across every item`} />
          </div>
        ) : null}
        {actions.length ? (
          <table className="tbl">
            <thead>
              <tr><th>Owner</th><th>Task</th><th>Due</th><th>Evidence</th></tr>
            </thead>
            <tbody>
              {[...owners.entries()].sort().flatMap(([owner, items]) =>
                items.map(({ a, m }, i) => (
                  <tr key={`${owner}-${i}`}>
                    <td>{i ? '' : <b>{owner}</b>}</td>
                    <td>
                      {inl(a.task)}{' '}
                      <button type="button" className="crumb" onClick={() => onOpen(m.id)}>
                        {m.date}
                      </button>
                    </td>
                    <td>{a.due || '—'}</td>
                    <td>
                      <div className="act">
                        <div className="row">
                          {actionBadges(a, (sec) => onOpen(m.id, sec))}
                        </div>
                      </div>
                    </td>
                  </tr>
                )))}
            </tbody>
          </table>
        ) : <Empty>No action items in this {period}.</Empty>}
      </div>
    </div>
  )
}

/* ---------------------------------------------------------------- scope nav */
function ScopeNav({ keys, cur, fmt, onGo }: {
  keys: string[]; cur: string; fmt: (k: string) => ReactNode; onGo: (k: string) => void
}) {
  const i = keys.indexOf(cur)
  return (
    <div className="scope">
      <button type="button" className="nav" title="Previous with meetings"
        disabled={i <= 0} onClick={() => onGo(keys[i - 1])}>‹</button>
      <div className="lbl">{fmt(cur)}</div>
      <button type="button" className="nav" title="Next with meetings"
        disabled={i < 0 || i >= keys.length - 1} onClick={() => onGo(keys[i + 1])}>›</button>
    </div>
  )
}

/* ================================================================ views */
type Nav = {
  openMeeting: (id: string, at?: number) => void
  goDay: (d: string) => void
  goWeek: (w: string) => void
}

function DayView({ date, byDate, dates, nav }: {
  date: string; byDate: Map<string, Meeting[]>; dates: string[]; nav: Nav
}) {
  const list = byDate.get(date) ?? []
  const total = sum(list, (m) => m.duration_s)
  const spoken = sum(list, (m) => m.spoken_s)
  const words = sum(list, (m) => m.words)
  const ht = heroTime(total)
  const span = list.length
    ? (Math.max(...list.map((m) => m.start_min + m.duration_s / 60))
      - Math.min(...list.map((m) => m.start_min))) * 60
    : 0

  return (
    <>
      <div className="bar">
        <ScopeNav keys={dates} cur={date} onGo={nav.goDay} fmt={(k) => {
          const d = new Date(`${k}T12:00:00`)
          return <>{dlabel(d)}<small>{d.getFullYear()}</small></>
        }} />
      </div>

      <Card>
        <Hero fig={list.length ? ht.fig : '0'}
          unit={list.length ? ht.unit : 'no meetings'}
          note={list.length
            ? `${list.length} meeting${list.length > 1 ? 's' : ''} · ${hm(span)} from first start to last end`
            : date} />
        {list.length ? (
          <div className="tiles">
            <Tile label="Meetings" value={String(list.length)}
              note={list.length > 1
                ? `longest ${hm(Math.max(...list.map((m) => m.duration_s)))}` : undefined} />
            <Tile label="Talk density" value={`${Math.round((spoken / total) * 100)}%`}
              note={`${hm(total - spoken)} of silence`} meter={(spoken / total) * 100} />
            <Tile label="Words spoken" value={num(words)}
              note={`${Math.round(words / (spoken / 60))} wpm average`} />
            <Tile label="Action items" value={String(sum(list, (m) => m.n_actions))}
              note={sum(list, (m) => m.flags)
                ? `${sum(list, (m) => m.flags)} need checking` : 'none flagged'} />
            <Tile label="Questions asked" value={String(sum(list, (m) => m.questions))}
              note="segments ending in ?" />
          </div>
        ) : null}
      </Card>

      {!list.length ? (
        <Card title="Nothing recorded">
          <Empty>
            No meeting carries this date. Use the arrows to jump to a day that has meetings.
          </Empty>
        </Card>
      ) : (
        <>
          <Card title="The day" note="click a block to open the meeting">
            <DayRibbon list={list} date={date} onOpen={nav.openMeeting} />
          </Card>
          <Card title="Meetings">
            <div className="mlist">
              {list.map((m) => <MeetingRow key={m.id} m={m} onOpen={nav.openMeeting} />)}
            </div>
          </Card>
          <Card title="Day digest" note="decisions and actions from every summary today">
            <Digest list={list} period="day" onOpen={nav.openMeeting} />
          </Card>
        </>
      )}
    </>
  )
}

function WeekView({ week, byWeek, byDate, weeks, nav }: {
  week: string; byWeek: Map<string, Meeting[]>; byDate: Map<string, Meeting[]>
  weeks: string[]; nav: Nav
}) {
  const list = byWeek.get(week) ?? []
  const mon = isoWeekStart(week)
  const total = sum(list, (m) => m.duration_s)
  const spoken = sum(list, (m) => m.spoken_s)
  const ht = heroTime(total)
  const prevKey = weeks[weeks.indexOf(week) - 1]
  const prev = prevKey ? sum(byWeek.get(prevKey) ?? [], (m) => m.duration_s) : 0
  const daysWith = new Set(list.map((m) => m.date)).size
  const before = list.filter((m) => m.start_min < 12 * 60)

  const dayRows: ColRow[] = []
  for (let i = 0; i < 7; i++) {
    const d = addDays(mon, i)
    const k = ymd(d)
    const dl = (byDate.get(k) ?? []).filter((m) => m.week === week)
    dayRows.push({
      label: DOW[i], full: dlabel(d), value: sum(dl, (m) => m.duration_s) / 3600,
      note: dl.length ? `${dl.length} meeting${dl.length > 1 ? 's' : ''}` : 'no meetings',
      onClick: dl.length ? () => nav.goDay(k) : undefined,
    })
  }

  return (
    <>
      <div className="bar">
        <ScopeNav keys={weeks} cur={week} onGo={nav.goWeek} fmt={(k) => {
          const s = isoWeekStart(k), e = addDays(s, 6)
          return <>{`${s.getDate()} ${MON[s.getMonth()]} – ${e.getDate()} ${MON[e.getMonth()]}`}
            <small>{k}</small></>
        }} />
      </div>

      <Card>
        <Hero fig={ht.fig} unit={`${ht.unit} this week`} note={
          `${list.length} meeting${list.length === 1 ? '' : 's'} · ${daysWith} day${daysWith === 1 ? '' : 's'} with meetings` +
          (prev ? ` · ${total >= prev ? '+' : ''}${Math.round(((total - prev) / prev) * 100)}% vs ${prevKey}` : '')} />
        {list.length ? (
          <div className="tiles">
            <Tile label="Average length" value={hm(total / list.length)}
              note={`longest ${hm(Math.max(...list.map((m) => m.duration_s)))}`} />
            <Tile label="Talk density" value={`${Math.round((spoken / total) * 100)}%`}
              note={`${hm(total - spoken)} of silence`} meter={(spoken / total) * 100} />
            <Tile label="Before noon"
              value={`${Math.round((before.length / list.length) * 100)}%`}
              note={`${before.length} of ${list.length} meetings`} />
            <Tile label="Words spoken" value={num(sum(list, (m) => m.words))}
              note={`${Math.round(sum(list, (m) => m.words) / (spoken / 60))} wpm average`} />
            <Tile label="Action items" value={String(sum(list, (m) => m.n_actions))}
              note={sum(list, (m) => m.flags)
                ? `${sum(list, (m) => m.flags)} need checking` : 'none flagged'} />
          </div>
        ) : null}
      </Card>

      {!list.length ? (
        <Card title="Nothing recorded"><Empty>No meetings in this week.</Empty></Card>
      ) : (
        <>
          <div className="grid cols-2">
            <Card title="Load by weekday" note="hours in meetings"
              extra={<TableTwin build={() => (
                <Tbl cols={[{ label: 'Day' }, { label: 'Meetings', num: true },
                  { label: 'Hours', num: true }]}
                  rows={dayRows.map((r) => [r.full,
                    (r.note ?? '').match(/^\d+/)?.[0] ?? '0', r.value.toFixed(2)])} />
              )} />}>
              <ColumnChart rows={dayRows} height={190} valueLabel="In meetings"
                fmtVal={(v) => (v ? hm(v * 3600) : '0')} fmtTick={(t) => `${t}h`} />
            </Card>
            <Card title="When meetings land" note="minutes per weekday and hour"
              extra={<TableTwin build={() => heatmapTable(list)} />}>
              <Heatmap list={list} onCell={(col) => {
                const k = ymd(addDays(mon, col))
                if (byDate.has(k)) nav.goDay(k)
              }} />
            </Card>
          </div>
          <Card title="Meetings this week">
            <div className="mlist">
              {[...list].sort((a, b) => (a.start < b.start ? -1 : 1))
                .map((m) => <MeetingRow key={m.id} m={m} onOpen={nav.openMeeting} />)}
            </div>
          </Card>
          <Card title="Week digest" note="everything decided and assigned this week">
            <Digest list={list} period="week" onOpen={nav.openMeeting} />
          </Card>
        </>
      )}
    </>
  )
}

function MonthView({ month, byMonth, byDate, byWeek, months, nav, onGoMonth }: {
  month: string; byMonth: Map<string, Meeting[]>; byDate: Map<string, Meeting[]>
  byWeek: Map<string, Meeting[]>; months: string[]; nav: Nav
  onGoMonth: (m: string) => void
}) {
  const list = byMonth.get(month) ?? []
  const total = sum(list, (m) => m.duration_s)
  const spoken = sum(list, (m) => m.spoken_s)
  const ht = heroTime(total)
  const daysWith = new Set(list.map((m) => m.date)).size
  const dayTotals = [...byDate.entries()].filter(([k]) => k.startsWith(month))
    .map(([, l]) => sum(l, (m) => m.duration_s))
  const wkKeys = [...new Set(list.map((m) => m.week))].sort()

  return (
    <>
      <div className="bar">
        <ScopeNav keys={months} cur={month} onGo={onGoMonth} fmt={(k) => {
          const [y, m] = k.split('-')
          return <>{MON[+m - 1]}<small>{y}</small></>
        }} />
      </div>

      <Card>
        <Hero fig={ht.fig} unit={`${ht.unit} this month`} note={
          `${list.length} meeting${list.length === 1 ? '' : 's'} across ${daysWith} day${daysWith === 1 ? '' : 's'}`} />
        {list.length ? (
          <div className="tiles">
            <Tile label="Average length" value={hm(total / list.length)}
              note={`${list.length} meetings`} />
            <Tile label="Busiest day" value={hm(Math.max(...dayTotals, 0))}
              note="per-day total" />
            <Tile label="Talk density" value={`${Math.round((spoken / total) * 100)}%`}
              note={`${hm(total - spoken)} of silence`} meter={(spoken / total) * 100} />
            <Tile label="Words spoken" value={num(sum(list, (m) => m.words))} />
            <Tile label="Action items" value={String(sum(list, (m) => m.n_actions))}
              note={sum(list, (m) => m.flags)
                ? `${sum(list, (m) => m.flags)} need checking` : 'none flagged'} />
          </div>
        ) : null}
      </Card>

      {!list.length ? (
        <Card title="Nothing recorded"><Empty>No meetings in this month.</Empty></Card>
      ) : (
        <>
          <Card title="Calendar" note="shade = time in meetings; click a day"
            extra={<TableTwin build={() => (
              <Tbl cols={[{ label: 'Date' }, { label: 'Meetings', num: true },
                { label: 'Total' }]}
                rows={[...byDate.entries()].filter(([k]) => k.startsWith(month)).sort()
                  .map(([k, l]) => [k, l.length, hm(sum(l, (m) => m.duration_s))])} />
            )} />}>
            <Calendar monthKey={month} byDate={byDate} onDay={nav.goDay} />
          </Card>

          {wkKeys.length >= 2 ? (
            <Card title="Week over week" note="hours in meetings"
              extra={<TableTwin build={() => (
                <Tbl cols={[{ label: 'Week of' }, { label: 'Meetings', num: true },
                  { label: 'Hours', num: true }]}
                  rows={wkKeys.map((k) => {
                    const l = (byWeek.get(k) ?? []).filter((m) => m.month === month)
                    const s = isoWeekStart(k)
                    return [`Week of ${dlabel(s)}`, l.length,
                      (sum(l, (m) => m.duration_s) / 3600).toFixed(2)]
                  })} />
              )} />}>
              <ColumnChart height={180} valueLabel="In meetings"
                fmtVal={(v) => hm(v * 3600)} fmtTick={(t) => `${t}h`}
                rows={wkKeys.map((k) => {
                  const l = (byWeek.get(k) ?? []).filter((m) => m.month === month)
                  const s = isoWeekStart(k)
                  return {
                    label: `${s.getDate()} ${MON[s.getMonth()]}`,
                    full: `Week of ${dlabel(s)}`,
                    value: sum(l, (m) => m.duration_s) / 3600,
                    note: `${l.length} meeting${l.length === 1 ? '' : 's'}`,
                    onClick: () => nav.goWeek(k),
                  }
                })} />
            </Card>
          ) : (
            <Card title="Week over week">
              <Empty>
                Needs meetings in at least <strong>two ISO weeks</strong> of this month
                before a trend means anything.
              </Empty>
            </Card>
          )}

          <Card title="Month digest" note="every decision and action item this month">
            <Digest list={list} period="month" onOpen={nav.openMeeting} />
          </Card>
        </>
      )}
    </>
  )
}

function ActionsView({ meetings, nav }: { meetings: Meeting[]; nav: Nav }) {
  const all = meetings.flatMap((m) => (m.actions ?? []).map((a) => ({ a, m })))
  const flagged = all.filter(({ a }) =>
    a.due_suspect || a.ts_mismatch || a.quote_missing || a.due_uniform || a.quote_reused)
  const owners = new Set(all.map(({ a }) => a.owner || 'Unassigned')).size

  return (
    <>
      <Card>
        <Hero fig={String(all.length)} unit="action items across every meeting"
          note={all.length
            ? `${flagged.length} with a citation that did not check out · ${owners} owners`
            : 'nothing captured yet'} />
      </Card>
      {all.length ? (
        <Card title="Every action item" note="grouped by owner">
          <Digest list={meetings} period="archive" onOpen={nav.openMeeting} />
        </Card>
      ) : (
        <Card title="Nothing to show">
          <Empty>
            Action items come from meeting summaries. See <strong>INGEST.md</strong> for how
            an ingest task supplies them, then hit Re-index.
          </Empty>
        </Card>
      )}
    </>
  )
}

function SearchView({ meetings, nav }: { meetings: Meeting[]; nav: Nav }) {
  const [q, setQ] = useState('')
  const [details, setDetails] = useState<Map<string, Detail>>(new Map())
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [ran, setRan] = useState('')

  const run = useCallback(async () => {
    const needle = q.trim()
    if (!needle) return
    setLoading(true); setErr(null)
    try {
      const missing = meetings.filter((m) => !details.has(m.id))
      if (missing.length) {
        const got = await Promise.all(missing.map((m) =>
          fetchMeeting(m.id).then((r) => [m.id, r.detail] as const).catch(() => null)))
        const next = new Map(details)
        for (const g of got) if (g) next.set(g[0], g[1])
        setDetails(next)
      }
      setRan(needle)
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }, [q, meetings, details])

  const results = useMemo(() => {
    if (!ran) return []
    const needle = ran.toLowerCase()
    const out: { m: Meeting; hits: { st: number; text: string }[]; meta: string[] }[] = []
    for (const m of meetings) {
      const det = details.get(m.id)
      const hits = (det?.segments ?? [])
        .filter(([, , text]) => text.toLowerCase().includes(needle))
        .map(([st, , text]) => ({ st, text }))
      const meta: string[] = []
      if (m.title.toLowerCase().includes(needle)) meta.push('matches the title')
      if ((m.overview ?? '').toLowerCase().includes(needle)
        || (m.decisions ?? []).some((d) => d.toLowerCase().includes(needle))
        || (m.actions ?? []).some((a) => (a.task ?? '').toLowerCase().includes(needle))) {
        meta.push('matches the summary')
      }
      if (hits.length || meta.length) out.push({ m, hits, meta })
    }
    return out
  }, [ran, details, meetings])

  const totalHits = sum(results, (r) => r.hits.length)

  return (
    <>
      <div className="bar">
        <div className="searchwrap">
          <input className="ma-input" type="search" value={q}
            placeholder="Search every transcript, summary and action item"
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') void run() }} />
        </div>
        <button type="button" className="pill" onClick={() => void run()}
          disabled={loading || !q.trim()}>
          {loading ? 'Searching…' : 'Search'}
        </button>
      </div>

      {err ? (
        <Card title="Search failed"><Empty>{err}</Empty></Card>
      ) : loading ? (
        <Card><Empty>Loading {meetings.length} transcript
          {meetings.length === 1 ? '' : 's'}…</Empty></Card>
      ) : !ran ? (
        <Card><Empty>
          Type a phrase and press Enter. Search is a plain case-insensitive substring
          match across every transcript, summary and action item.
        </Empty></Card>
      ) : (
        <>
          <Card>
            <h2 style={{ fontSize: 18, margin: '0 0 4px' }}>&ldquo;{ran}&rdquo;</h2>
            <div style={{ color: 'var(--muted)', fontSize: 13 }}>
              {results.length
                ? `${totalHits} spoken mention${totalHits === 1 ? '' : 's'} in ${results.length} meeting${results.length === 1 ? '' : 's'}`
                : 'no matches'}
            </div>
          </Card>
          {results.map(({ m, hits, meta }) => (
            <Card key={m.id} title={m.title}
              note={`${m.date} · ${hits.length} mention${hits.length === 1 ? '' : 's'}`}>
              {meta.length ? (
                <div style={{ fontSize: 12, color: 'var(--muted)', marginBottom: 8 }}>
                  {meta.join(' · ')}
                </div>
              ) : null}
              {hits.slice(0, 8).map((h, i) => {
                const idx = h.text.toLowerCase().indexOf(ran.toLowerCase())
                return (
                  <div className="seg" key={i}>
                    <button type="button" className="ts"
                      onClick={() => nav.openMeeting(m.id, h.st)}>{clock(h.st)}</button>
                    <div>
                      {h.text.slice(0, idx)}
                      <mark>{h.text.substr(idx, ran.length)}</mark>
                      {h.text.slice(idx + ran.length)}
                    </div>
                  </div>
                )
              })}
              {hits.length > 8 ? (
                <div style={{ fontSize: 12, color: 'var(--muted)', marginTop: 6 }}>
                  +{hits.length - 8} more in this meeting
                </div>
              ) : null}
              <button type="button" className="pill" style={{ marginTop: 10 }}
                onClick={() => nav.openMeeting(m.id)}>Open meeting</button>
            </Card>
          ))}
        </>
      )}
    </>
  )
}

/* ---------------------------------------------------------------- detail */
function MeetingView({ id, seekTo, onBack, goDay }: {
  id: string; seekTo?: number; onBack: () => void; goDay: (d: string) => void
}) {
  const [data, setData] = useState<{ meeting: Meeting; detail: Detail } | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [find, setFind] = useState('')
  const [active, setActive] = useState<number | null>(null)
  const audioRef = useRef<HTMLAudioElement | null>(null)
  const rowRefs = useRef<Map<number, HTMLDivElement>>(new Map())
  const [stripRef, stripW] = useMeasure<HTMLDivElement>()
  const tip = useTip()

  useEffect(() => {
    let alive = true
    setData(null); setErr(null)
    fetchMeeting(id)
      .then((r) => { if (alive) setData(r) })
      .catch((e) => { if (alive) setErr(e instanceof Error ? e.message : String(e)) })
    return () => { alive = false }
  }, [id])

  const segs = data?.detail.segments ?? []

  const seek = useCallback((sec: number) => {
    let best = 0
    segs.forEach((s, i) => { if (s[0] <= sec + 0.4) best = i })
    setActive(best)
    const el = rowRefs.current.get(best)
    if (el) el.scrollIntoView({ block: 'center', behavior: 'smooth' })
    const a = audioRef.current
    if (a) { try { a.currentTime = sec } catch { /* not seekable yet */ } }
  }, [segs])

  // Synchronous-on-data, not in an animation frame: rAF is paused entirely in a
  // hidden or background tab, which would silently swallow a deep-linked jump.
  useEffect(() => {
    if (data && seekTo != null) seek(seekTo)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, seekTo])

  if (err) {
    return (
      <Card title="Could not load this meeting">
        <Empty>{err} — the index may have been rebuilt. Go back and try again.</Empty>
      </Card>
    )
  }
  if (!data) return <Card><Empty>Loading meeting…</Empty></Card>

  const { meeting: m, detail: d } = data
  const start = dt(m.start)
  const needle = find.trim().toLowerCase()
  const act = m.activity
  const maxAct = Math.max(1, ...act)
  const H = 46
  const hasSpeakers = m.speakers && m.speakers.length > 0

  return (
    <>
      <div className="dhead">
        <button type="button" className="crumb" onClick={onBack}>‹ back</button>
        <h1>{m.title}</h1>
        <div className="sub">
          <span style={{ fontVariantNumeric: 'tabular-nums' }}>
            {hhmm(start)}–{hhmm(dt(m.end))}
          </span>
          <button type="button" className="crumb" onClick={() => goDay(m.date)}>
            {dlabel(start)}
          </button>
          <span>{hm(m.duration_s)}</span>
          <span>{num(m.words)} words</span>
          {!m.titled ? <span style={{ color: 'var(--muted)' }}>untitled in Meetily</span> : null}
          {m.transcript_source && m.transcript_source !== 'Meetily' ? (
            <span style={{ color: 'var(--muted)' }}>
              transcript: {m.transcript_model || m.transcript_source}
            </span>
          ) : null}
          {m.summary_model ? (
            <span style={{ color: 'var(--muted)' }}>
              summary: {m.summary_model}{m.summary_source ? ` via ${m.summary_source}` : ''}
            </span>
          ) : null}
        </div>
      </div>

      <div className="tiles">
        <Tile label="Talk density" value={`${Math.round((m.density ?? 0) * 100)}%`}
          note={`${hm(m.duration_s - m.spoken_s)} of silence`}
          meter={(m.density ?? 0) * 100} />
        <Tile label="Pace" value={String(m.wpm)} unit="wpm" note="while someone was speaking" />
        <Tile label="Pauses" value={String(m.pauses)}
          note={`≥2s · longest ${Math.round(m.longest_gap_s)}s`} />
        <Tile label="Questions" value={String(m.questions)} note="segments ending in ?" />
        <Tile label="Longest single take" value={`${Math.round(m.longest_seg_s)}s`}
          note={`${m.n_segments} segments`} />
      </div>

      {hasSpeakers ? (
        <Card title="Who talked" note="from the enriched transcript's speaker labels"
          extra={<TableTwin build={() => (
            <Tbl cols={[{ label: 'Speaker' }, { label: 'Minutes', num: true },
              { label: 'Words', num: true }, { label: 'Turns', num: true }]}
              rows={m.speakers.map((s) => [s.speaker, (s.seconds / 60).toFixed(1),
                s.words, s.turns])} />
          )} />}>
          <div className="mlist">
            {m.speakers.map((s) => (
              <div key={s.speaker} className="tile">
                <div className="lbl">{s.speaker}</div>
                <div className="val">{Math.round((s.share ?? 0) * 100)}<span>%</span></div>
                <div className="note">{hm(s.seconds)} · {num(s.words)} words · {s.turns} turns</div>
                <div className="meter"><i style={{ width: `${(s.share ?? 0) * 100}%` }} /></div>
              </div>
            ))}
          </div>
        </Card>
      ) : null}

      <div className="grid cols-32" style={{ marginTop: 14 }}>
        <div>
          {d.summary ? (
            <>
              <Card title="Summary" note={m.flags
                ? `${m.flags} citation${m.flags > 1 ? 's' : ''} did not check out — see the badges`
                : 'citations checked against the transcript'}>
                {d.summary.overview ? (
                  <div className="section">
                    <h4>Summary</h4>
                    <div className="overview">
                      {d.summary.overview.split(/\n\s*\n/).map((p, i) =>
                        <p key={i}>{inl(p)}</p>)}
                    </div>
                  </div>
                ) : null}
                {d.summary.decisions.length ? (
                  <div className="section">
                    <h4>Key decisions</h4>
                    <ul className="bullets">
                      {d.summary.decisions.map((x, i) => <li key={i}>{inl(x)}</li>)}
                    </ul>
                  </div>
                ) : null}
                {d.summary.actions.length ? (
                  <div className="section">
                    <h4>Action items</h4>
                    {d.summary.actions.some((a) => a.due_uniform) ? (
                      <div style={{ marginBottom: 10 }}>
                        <Badge kind="warn" icon="warn"
                          text="every item carries the same due date — the model filled it in" />
                      </div>
                    ) : null}
                    {d.summary.actions.map((a, i) => (
                      <div className="act" key={i}>
                        <div className="task">
                          {a.owner ? <><span className="owner">{a.owner}</span> — </> : null}
                          {inl(a.task)}
                          {a.due ? <span style={{ color: 'var(--muted)' }}> due {a.due}</span> : null}
                        </div>
                        <div className="row">{actionBadges(a, seek)}</div>
                      </div>
                    ))}
                  </div>
                ) : null}
                {d.summary.highlights.length ? (
                  <div className="section">
                    <h4>Discussion highlights</h4>
                    {d.summary.highlights.map((h, i) => (
                      <div className="hl" key={i}>
                        {h.title ? <b>{inl(h.title)}</b> : null}
                        <span>{inl(h.body)}</span>
                      </div>
                    ))}
                  </div>
                ) : null}
              </Card>
              <Card>
                <details>
                  <summary style={{ cursor: 'pointer', color: 'var(--muted)', fontSize: 12 }}>
                    Raw model output
                  </summary>
                  <pre style={{
                    whiteSpace: 'pre-wrap', fontSize: 12,
                    color: 'var(--ink-2)', margin: '10px 0 0',
                  }}>{d.summary.raw}</pre>
                </details>
              </Card>
            </>
          ) : (
            <Card title="No summary">
              <Empty>
                Nothing has summarised this meeting yet. The metrics and transcript on this
                page do not depend on one — see <strong>INGEST.md</strong> for how a
                summary gets supplied.
              </Empty>
            </Card>
          )}
          {m.keywords.length ? (
            <Card title="Recurring terms" note="weighted against every other meeting">
              <div className="kw">
                {m.keywords.map((k) => <span key={k.t}>{k.t}<b>{k.n}</b></span>)}
              </div>
            </Card>
          ) : null}
        </div>

        <div>
          <Card title="Transcript"
            note={`${m.n_segments} segments · click a timestamp to jump`}>
            {m.has_audio ? (
              <audio ref={audioRef} controls preload="none" src={audioUrl(m.id)} />
            ) : null}

            <div className="chart" ref={stripRef}>
              {stripW > 0 ? (
                <svg width={stripW} height={H} viewBox={`0 0 ${stripW} ${H}`} role="img"
                  aria-label="words spoken over the meeting">
                  {act.map((v, i) => {
                    const bw = Math.max(1, stripW / act.length - 1)
                    const bh = v ? Math.max(1.5, (v / maxAct) * (H - 6)) : 0
                    if (!bh) return null
                    return <rect key={i} x={((i * stripW) / act.length).toFixed(2)}
                      y={(H - 4 - bh).toFixed(2)} width={bw.toFixed(2)}
                      height={bh.toFixed(2)} rx={Math.min(1.5, bw / 2)}
                      fill="var(--ma-q3)" />
                  })}
                  <line x1={0} y1={H - 3} x2={stripW} y2={H - 3} className="axis-line" />
                  {active != null && segs[active] ? (
                    <line className="now-line"
                      x1={(segs[active][0] / m.duration_s) * stripW} y1={0}
                      x2={(segs[active][0] / m.duration_s) * stripW} y2={H - 3} />
                  ) : null}
                  <rect x={0} y={0} width={stripW} height={H} className="hit"
                    onClick={(e) => {
                      const r = e.currentTarget.getBoundingClientRect()
                      seek(((e.clientX - r.left) / r.width) * m.duration_s)
                    }}
                    onMouseMove={(e) => {
                      const r = e.currentTarget.getBoundingClientRect()
                      const i = Math.min(act.length - 1, Math.max(0,
                        Math.floor(((e.clientX - r.left) / r.width) * act.length)))
                      tip(<>
                        <div className="t">{clock(i * BUCKET_S)}</div>
                        <div className="r"><span>Words in 30s</span><b>{act[i]}</b></div>
                      </>, e)
                    }}
                    onMouseLeave={() => tip(null)} />
                </svg>
              ) : null}
            </div>

            <input className="ma-input" type="search" value={find}
              style={{ margin: '12px 0 8px' }} placeholder="Find in this transcript"
              onChange={(e) => setFind(e.target.value)} />

            <div className="tx">
              {segs.map(([st, du, text, who], i) => {
                const prevEnd = i ? segs[i - 1][0] + segs[i - 1][1] : 0
                const gap = i && st - prevEnd > 8 ? Math.round(st - prevEnd) : 0
                const idx = needle ? text.toLowerCase().indexOf(needle) : -1
                if (needle && idx < 0) return null
                return (
                  <div key={i}>
                    {gap ? <div className="gap">{gap}s of silence</div> : null}
                    <div className={`seg${active === i ? ' on' : ''}`}
                      ref={(el) => { if (el) rowRefs.current.set(i, el) }}>
                      <button type="button" className="ts" title={`Jump to ${clock(st)}`}
                        onClick={() => seek(st)}>{clock(st)}</button>
                      <div>
                        {who ? <span className="who">{who}</span> : null}
                        {idx >= 0 ? (
                          <>
                            {text.slice(0, idx)}
                            <mark>{text.substr(idx, needle.length)}</mark>
                            {text.slice(idx + needle.length)}
                          </>
                        ) : text}
                        {du > 45 ? null : null}
                      </div>
                    </div>
                  </div>
                )
              })}
            </div>
          </Card>
        </div>
      </div>
    </>
  )
}

/* ================================================================ root */
type Tab = 'day' | 'week' | 'month' | 'actions' | 'search'

export default function MeetingAtlasModule() {
  const [corpus, setCorpus] = useState<Corpus | null>(null)
  const [meetings, setMeetings] = useState<Meeting[]>([])
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(true)
  const [tab, setTab] = useState<Tab>('day')
  const [open, setOpen] = useState<{ id: string; at?: number } | null>(null)
  const [day, setDay] = useState('')
  const [week, setWeek] = useState('')
  const [month, setMonth] = useState('')
  const [tipNode, setTipNode] = useState<ReactNode | null>(null)
  const [tipPos, setTipPos] = useState({ x: 0, y: 0 })

  const showTip = useCallback<TipFn>((node, ev) => {
    setTipNode(node)
    if (ev) setTipPos({ x: ev.clientX + 14, y: ev.clientY - 12 })
  }, [])

  const load = useCallback(async () => {
    setBusy(true); setErr(null)
    try {
      const r = await fetchMeetings()
      setCorpus(r.corpus)
      setMeetings(r.meetings)
      const dates = [...new Set(r.meetings.map((m) => m.date))].sort()
      const latest = dates[dates.length - 1]
      if (latest) {
        setDay((d) => d || latest)
        setWeek((w) => w || isoWeekKey(new Date(`${latest}T12:00:00`)))
        setMonth((mo) => mo || latest.slice(0, 7))
      }
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }, [])

  useEffect(() => { void load() }, [load])

  const byDate = useMemo(() => groupBy(meetings, (m) => m.date), [meetings])
  const byWeek = useMemo(() => groupBy(meetings, (m) => m.week), [meetings])
  const byMonth = useMemo(() => groupBy(meetings, (m) => m.month), [meetings])
  const dates = useMemo(() => [...byDate.keys()].sort(), [byDate])
  const weeks = useMemo(() => [...byWeek.keys()].sort(), [byWeek])
  const months = useMemo(() => [...byMonth.keys()].sort(), [byMonth])

  const nav: Nav = useMemo(() => ({
    openMeeting: (id, at) => setOpen({ id, at }),
    goDay: (d) => {
      setDay(d)
      setWeek(isoWeekKey(new Date(`${d}T12:00:00`)))
      setMonth(d.slice(0, 7))
      setTab('day'); setOpen(null)
    },
    goWeek: (w) => { setWeek(w); setTab('week'); setOpen(null) },
  }), [])

  const doReindex = async () => {
    setBusy(true)
    try { await reindex(); await load() } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally { setBusy(false) }
  }

  let body: ReactNode
  if (err) {
    body = (
      <Card title="Backend unreachable">
        <Empty>
          <strong>{err}</strong><br /><br />
          The Meeting Atlas backend did not answer. Check that its container is running
          and that the recordings directory is mounted.
        </Empty>
      </Card>
    )
  } else if (busy && !meetings.length) {
    body = <Card><Empty>Loading meetings…</Empty></Card>
  } else if (corpus && !corpus.available) {
    body = (
      <Card title="No recordings mounted">
        <Empty>
          The backend cannot see <strong>{corpus.recordings_root || 'its recordings directory'}</strong>.
          Bind-mount the Meetily recordings folder into the container and hit Re-index.
        </Empty>
      </Card>
    )
  } else if (!meetings.length) {
    body = (
      <Card title="No meetings yet">
        <Empty>
          The recordings directory is visible but holds no meeting with a transcript.
          Record something in Meetily, then hit Re-index.
        </Empty>
      </Card>
    )
  } else if (open) {
    body = <MeetingView id={open.id} seekTo={open.at} onBack={() => setOpen(null)}
      goDay={nav.goDay} />
  } else if (tab === 'day') {
    body = <DayView date={day || dates[dates.length - 1]} byDate={byDate}
      dates={dates} nav={nav} />
  } else if (tab === 'week') {
    body = <WeekView week={week || weeks[weeks.length - 1]} byWeek={byWeek}
      byDate={byDate} weeks={weeks} nav={nav} />
  } else if (tab === 'month') {
    body = <MonthView month={month || months[months.length - 1]} byMonth={byMonth}
      byDate={byDate} byWeek={byWeek} months={months} nav={nav}
      onGoMonth={(mo) => { setMonth(mo); setTab('month') }} />
  } else if (tab === 'actions') {
    body = <ActionsView meetings={meetings} nav={nav} />
  } else {
    body = <SearchView meetings={meetings} nav={nav} />
  }

  const tabs: [Tab, string][] = [
    ['day', 'Day'], ['week', 'Week'], ['month', 'Month'],
    ['actions', 'Actions'], ['search', 'Search'],
  ]

  return (
    <TipCtx.Provider value={showTip}>
      <div className="ma">
        <div className="head">
          <h2>Meeting Atlas</h2>
          <div className="tabs">
            {tabs.map(([k, label]) => (
              <button type="button" key={k}
                className={`tab${tab === k && !open ? ' on' : ''}`}
                onClick={() => { setTab(k); setOpen(null) }}>{label}</button>
            ))}
          </div>
          <div className="spacer" />
          {corpus ? (
            <div className="sub">
              {corpus.n_meetings} meeting{corpus.n_meetings === 1 ? '' : 's'}
              {corpus.n_flagged ? ` · ${corpus.n_flagged} flagged citation${corpus.n_flagged === 1 ? '' : 's'}` : ''}
              {' · indexed '}{(corpus.generated_at || '').replace('T', ' ')}
            </div>
          ) : null}
          <button type="button" className="pill" onClick={() => void doReindex()}
            disabled={busy} title="Rebuild the index from disk">
            {busy ? 'Working…' : 'Re-index'}
          </button>
        </div>

        {body}

        {tipNode ? (
          <div className="tip" style={{ left: tipPos.x, top: tipPos.y }}>{tipNode}</div>
        ) : null}
      </div>
    </TipCtx.Provider>
  )
}

function groupBy<T>(items: T[], key: (x: T) => string): Map<string, T[]> {
  const g = new Map<string, T[]>()
  for (const it of items) {
    const k = key(it)
    const arr = g.get(k) ?? []
    arr.push(it)
    g.set(k, arr)
  }
  for (const arr of g.values()) {
    arr.sort((a, b) => ((a as unknown as Meeting).start < (b as unknown as Meeting).start ? -1 : 1))
  }
  return g
}
