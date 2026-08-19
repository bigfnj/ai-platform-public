// Terminal Fun — a federated React module for the platform shell. Exposes ./module.
// A picker grid of terminal games/toys (each with an ⓘ how-to-play panel) + an xterm.js
// terminal, plus a docked AI assistant at the bottom that answers questions about the page
// and can tune the current toy on the fly (relaunching it over the same WebSocket).
import { useCallback, useEffect, useRef, useState } from 'react'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import '@xterm/xterm/css/xterm.css'

interface Category { id: string; label: string }
interface Item { id: string; label: string; icon: string; category: string; watch: boolean; info: string; tunable: boolean; saveable: boolean }
interface ChatMsg { role: 'user' | 'assistant'; content: string }

// Model status chips (mirrors GET /api/models). Four states, one visual language across every
// rail: missing RED = not installed, cold BLUE = installed but not resident, warming ORANGE = a
// broker job is loading it, loaded GREEN = resident. The red/blue split is the operationally
// useful one — red needs an `ollama pull`, blue just needs someone to ask a question.
type ModelState = 'missing' | 'cold' | 'warming' | 'loaded'
interface ModelSlot { slot: string; label: string; role: string; model: string; state: ModelState }
interface ModelsStatus { broker: string; models: ModelSlot[]; items: number }

const STATE_TEXT: Record<ModelState, string> = {
  missing: 'not found',
  cold: 'cold',
  warming: 'warming up',
  loaded: 'GPU · ready',
}

function ModelChips({ status }: { status: ModelsStatus | null }) {
  if (!status) return <span className="status m">checking…</span>
  if (status.broker !== 'ok') return <span className="status m">● broker unreachable</span>
  return (
    <div className="status">
      {status.models.map((m) => (
        <span key={m.slot} title={`${m.role} → ${m.model} · ${STATE_TEXT[m.state]}`}>
          <i className={`dot ${m.state}`} />
          {m.label} ({m.model}) <span className="m">{STATE_TEXT[m.state]}</span>
        </span>
      ))}
      <span className="m">{status.items} games &amp; toys</span>
    </div>
  )
}

const enc = new TextEncoder()
const IN = 0x00 // input / output data frame tag
const RESIZE = 0x01 // resize control frame tag
const APPLY = 0x02 // apply-new-settings control frame tag
const STATUS = '\x04' // server status line prefix (text frame)

const CSS = `
.ft { --i:var(--text-primary,#e6edf3); --mut:var(--text-secondary,#8b98a9);
  --s1:var(--surface-1,#0f141b); --s2:var(--surface-2,#161c26); --bd:var(--border,#263042);
  --ac:var(--accent,#2a78d6); color:var(--i); display:flex; flex-direction:column;
  height:calc(100vh - 132px); min-height:520px; }
.ft .body { flex:1; min-height:0; display:flex; flex-direction:column; }

/* --- header: title, statement, then model chips ------------------------------
 * Same shape as the smb-partner-enablement, co-worker and gemini-cx rails so every rail reads
 * as one product. The header takes its natural height and .body flexes into what is left, so
 * the .ft height calc above needs no adjustment. */
/* Header rule: a 2px divider under every rail's header, with 18px of breathing room above it.
   Shared convention across all rails so the shell reads as one product rather than a set of
   apps that happen to be adjacent (see web/THEMING.md). Uses the rail's local ink alias, which
   derives from --text-primary, so it inverts correctly with light/dark and needs no per-theme
   override. The 18px matters: with a status-chip row under the title the content otherwise
   crowds the line. */
.ft .head { display:flex; align-items:flex-start; gap:12px; flex-wrap:wrap; margin:2px 2px 14px;
  padding-bottom:18px; border-bottom:2px solid var(--i); }
.ft .head .logo { font-size:26px; line-height:1; }
.ft .head .titles { display:flex; flex-direction:column; gap:4px; min-width:0; }
.ft .head h1 { margin:0; font-size:19px; font-weight:650; letter-spacing:-.01em; }
.ft .head .stmt { color:var(--mut); font-size:12.5px; max-width:80ch; }

/* Model chips. NOTE: this rail already uses .chip for the game tiles (play/watch/AI/resume),
 * so the status chips deliberately use .status/.dot and never .chip. */
.ft .status { display:flex; gap:16px; flex-wrap:wrap; font-size:12px; margin-top:2px; }
.ft .status .m { color:var(--mut); }
/* These four colours are FIXED rather than palette-derived, on purpose: status colours stay
 * semantic and stable across every palette (web/THEMING.md rule 4), because a reader has to be
 * able to tell "not installed" from "cold" on any theme. */
.ft .dot { width:8px; height:8px; border-radius:50%; display:inline-block; margin-right:6px;
  vertical-align:1px; background:var(--muted,#6b7280); }
.ft .dot.missing { background:var(--critical,#f85149); }
.ft .dot.cold    { background:var(--info,#58a6ff); }
.ft .dot.warming { background:var(--warning,#f0883e); animation:ft-pulse 1.1s ease-in-out infinite; }
.ft .dot.loaded  { background:var(--good,#3fb950); }
@keyframes ft-pulse { 50% { opacity:.35; } }

.ft .menu { flex:1; overflow:auto; padding:2px 2px 10px; }
.ft .lead { color:var(--mut); font-size:13.5px; margin:2px 0 18px; }
.ft .sec { font-weight:750; font-size:12px; letter-spacing:.08em; text-transform:uppercase;
  color:var(--mut); margin:18px 2px 10px; }
.ft .grid { display:grid; gap:12px; grid-template-columns:repeat(auto-fill,minmax(178px,1fr)); }
.ft .tile { position:relative; text-align:left; cursor:pointer; color:var(--i);
  background:var(--s2); border:1px solid var(--bd); border-radius:12px; padding:14px 15px;
  display:flex; flex-direction:column; gap:8px; transition:border-color .12s, transform .06s; }
.ft .tile:hover { border-color:var(--ac); transform:translateY(-1px); }
.ft .tile .ic { font-size:26px; line-height:1; }
.ft .tile .nm { font-weight:650; font-size:14px; padding-right:26px; }
.ft .chips { display:flex; gap:6px; }
.ft .chip { font-size:10.5px; font-weight:700; letter-spacing:.04em; text-transform:uppercase;
  padding:2px 7px; border-radius:999px; border:1px solid var(--bd); color:var(--mut); }
.ft .chip.play { color:var(--good,#3fb950); border-color:var(--bd); }
.ft .chip.watch { color:var(--warning,#d29922); border-color:var(--bd); }
.ft .chip.resume { color:var(--success,#3fb950); border-color:var(--bd); }
.ft .chip.reset { color:var(--danger,#f85149); border-color:var(--bd); cursor:pointer; }
.ft .chip.reset:hover { background:var(--danger,#f85149); color:#fff; border-color:transparent; }
.ft-modal .discard { font:inherit; font-weight:650; font-size:13px; margin:18px 10px 0 0; padding:7px 16px;
  border-radius:8px; border:1px solid var(--danger,#f85149); background:transparent; color:var(--danger,#f85149); cursor:pointer; }
.ft .chip.ai { color:var(--ac); border-color:var(--ac); }
.ft .info-btn { position:absolute; top:9px; right:9px; width:22px; height:22px; border-radius:50%;
  border:1px solid var(--bd); background:var(--s1); color:var(--mut); font:inherit; font-size:12px;
  font-weight:800; font-style:italic; cursor:pointer; display:inline-flex; align-items:center;
  justify-content:center; line-height:1; }
.ft .info-btn:hover { color:#fff; background:var(--grad-accent,var(--ac)); border-color:transparent; }

.ft .bar { display:flex; align-items:center; gap:10px; margin-bottom:10px; flex-wrap:wrap; }
.ft .back { font:inherit; font-weight:650; font-size:13px; padding:7px 13px; border-radius:999px;
  border:1px solid var(--bd); background:transparent; color:var(--i); cursor:pointer; }
.ft .back:hover { border-color:var(--ac); }
.ft .title { font-weight:700; font-size:15px; display:inline-flex; gap:8px; align-items:center; }
.ft .sp { flex:1; }
.ft .status { font-size:12.5px; color:var(--mut); }
.ft .dot.on { background:var(--good,#3fb950); } /* PTY connected — not a model state */
.ft .ghost { font:inherit; font-weight:600; font-size:13px; padding:6px 12px; border-radius:8px;
  border:1px solid var(--bd); background:transparent; color:var(--i); cursor:pointer; }
.ft .ghost:hover { border-color:var(--ac); }
.ft .termwrap { flex:1; min-height:0; display:flex; flex-direction:column; }
.ft .term { flex:1; min-height:0; background:var(--s1); border:1px solid var(--bd);
  border-radius:10px; padding:8px; overflow:hidden; }

.ft .chat { border-top:1px solid var(--bd); margin-top:10px; padding-top:10px; display:flex;
  flex-direction:column; gap:8px; height:190px; flex:none; }
.ft .chat .log { flex:1; min-height:0; overflow:auto; display:flex; flex-direction:column; gap:8px; }
.ft .chat .msg { font-size:13px; line-height:1.5; padding:8px 11px; border-radius:11px;
  max-width:86%; white-space:pre-wrap; word-break:break-word; }
.ft .chat .msg.user { align-self:flex-end; background:var(--grad-accent,var(--ac)); color:#fff; }
.ft .chat .msg.assistant { align-self:flex-start; background:var(--s2); border:1px solid var(--bd); }
.ft .chat .hint { font-size:12px; color:var(--mut); }
.ft .chat .hint b { color:var(--i); font-weight:650; }
.ft .chat .row { display:flex; gap:8px; }
.ft .chat input.ask { flex:1; font:inherit; font-size:13.5px; padding:10px 13px; border-radius:11px;
  border:1px solid var(--bd); background:var(--s1); color:var(--i); }
.ft .chat input.ask::placeholder { color:var(--mut); }
.ft .chat input.ask:focus { outline:none; border-color:var(--ac); }
.ft .chat .send { font:inherit; font-weight:650; font-size:13px; padding:10px 18px; border-radius:11px;
  border:1px solid transparent; background:var(--grad-accent,var(--ac)); color:#fff; cursor:pointer; }
.ft .chat .send:disabled { opacity:.5; cursor:default; }

.ft-modal-ov { position:fixed; inset:0; background:rgba(0,0,0,.55); display:flex; align-items:center;
  justify-content:center; z-index:1000; padding:20px; }
.ft-modal { background:var(--s2,#161c26); color:var(--text-primary,#e6edf3);
  border:1px solid var(--border,#263042); border-radius:14px; max-width:560px; width:100%;
  max-height:80vh; overflow:auto; padding:22px 24px; box-shadow:0 20px 60px rgba(0,0,0,.5); }
.ft-modal h3 { margin:0; font-size:18px; display:flex; gap:10px; align-items:center; }
.ft-modal .kind { font-size:11px; font-weight:700; text-transform:uppercase; letter-spacing:.05em;
  color:var(--text-secondary,#8b98a9); margin-top:4px; }
.ft-modal pre { white-space:pre-wrap; word-break:break-word; font:inherit; font-size:13.5px;
  line-height:1.55; margin:14px 0 0; }
.ft-modal .close { font:inherit; font-weight:650; font-size:13px; margin-top:18px; padding:7px 16px;
  border-radius:8px; border:1px solid transparent; background:var(--grad-accent,var(--accent,#2a78d6));
  color:#fff; cursor:pointer; }
`

function HelpModal({ item, onClose, hasSave, onDiscard }: { item: Item; onClose: () => void; hasSave?: boolean; onDiscard?: () => void }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])
  return (
    <div className="ft-modal-ov" onClick={onClose}>
      <div className="ft-modal" onClick={(e) => e.stopPropagation()}>
        <h3><span>{item.icon}</span>{item.label}</h3>
        <div className="kind">{item.watch ? 'Watch — no input needed' : 'Play — interactive'}{item.tunable ? ' · tunable by the assistant' : ''}{item.saveable ? ' · saves your progress' : ''}</div>
        <pre>{item.info || 'No instructions available.'}</pre>
        {item.saveable && (
          <pre style={{ marginTop: 12, opacity: .85 }}>{hasSave
            ? 'You have a game in progress — reopening this tile resumes it. Save from inside the game before you leave (NetHack: Shift+S · Crawl: S), then discard below to start fresh.'
            : 'This game saves your progress: save from inside the game before leaving (NetHack: Shift+S · Crawl: S), and reopening the tile picks up where you left off.'}</pre>
        )}
        <div>
          {hasSave && onDiscard && <button className="discard" onClick={onDiscard}>Discard saved game</button>}
          <button className="close" onClick={onClose}>Got it</button>
        </div>
      </div>
    </div>
  )
}

export default function TerminalFunModule() {
  const [cats, setCats] = useState<Category[]>([])
  const [items, setItems] = useState<Item[]>([])
  const [saved, setSaved] = useState<Set<string>>(new Set())
  const [active, setActive] = useState<Item | null>(null)
  const [help, setHelp] = useState<Item | null>(null)
  const [connected, setConnected] = useState(false)
  const [status, setStatus] = useState('')

  // AI assistant
  const [msgs, setMsgs] = useState<ChatMsg[]>([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [tuneHint, setTuneHint] = useState('')
  const [models, setModels] = useState<ModelsStatus | null>(null)
  const params = useRef<Record<string, unknown>>({})
  const logRef = useRef<HTMLDivElement | null>(null)

  const termHost = useRef<HTMLDivElement | null>(null)
  const term = useRef<Terminal | null>(null)
  const fit = useRef<FitAddon | null>(null)
  const ws = useRef<WebSocket | null>(null)
  const ro = useRef<ResizeObserver | null>(null)

  useEffect(() => {
    const el = document.createElement('style')
    el.textContent = CSS
    document.head.appendChild(el)
    return () => { document.head.removeChild(el) }
  }, [])

  useEffect(() => {
    fetch('/terminal-fun/api/catalog')
      .then((r) => r.json())
      .then((d) => { setCats(d.categories ?? []); setItems(d.items ?? []) })
      .catch(() => { setCats([]); setItems([]) })
  }, [])

  // Poll the model chips. Residency changes on its own — the broker evicts on a keep_alive
  // expiry and asking the assistant warms the model back up — so a one-shot fetch would show a
  // state that silently goes stale. 6s matches the shell's own status cadence, and the warming
  // window is ~7s on this box, so a transition is actually visible rather than missed.
  useEffect(() => {
    let live = true
    const tick = () => {
      fetch('/terminal-fun/api/models')
        .then((r) => r.json())
        .then((d) => { if (live) setModels(d) })
        .catch(() => { if (live) setModels(null) })
    }
    tick()
    const id = window.setInterval(tick, 6000)
    return () => { live = false; window.clearInterval(id) }
  }, [])

  // Which saveable games this user has an in-progress save for (drives the "resume" chip).
  const refreshSaves = useCallback(() => {
    fetch('/terminal-fun/api/saves').then((r) => r.json())
      .then((d) => setSaved(new Set<string>(d.saves ?? []))).catch(() => {})
  }, [])
  useEffect(() => { refreshSaves() }, [refreshSaves])

  const discardSave = useCallback((id: string) => {
    fetch(`/terminal-fun/api/saves/${id}`, { method: 'DELETE' }).then(() => refreshSaves()).catch(() => {})
    setHelp(null)
  }, [refreshSaves])

  // newest message sits at the TOP of the log (just under the input) and pushes older ones down
  useEffect(() => { logRef.current?.scrollTo({ top: 0 }) }, [msgs, busy])

  // When a tunable toy opens, learn its default settings + a one-line "what you can change".
  useEffect(() => {
    params.current = {}
    setTuneHint('')
    setMsgs([])  // each screen gets a fresh conversation so context always matches what's open
    if (active?.tunable) {
      fetch(`/terminal-fun/api/tunables/${active.id}`)
        .then((r) => r.json())
        .then((d) => { params.current = d.defaults ?? {}; setTuneHint(d.schema?.intro ?? '') })
        .catch(() => {})
    }
  }, [active])

  const teardown = useCallback(() => {
    ro.current?.disconnect(); ro.current = null
    if (ws.current) { ws.current.onclose = null; ws.current.close(); ws.current = null }
    term.current?.dispose(); term.current = null
    fit.current = null
    setConnected(false)
  }, [])

  useEffect(() => () => teardown(), [teardown])

  const applyParams = useCallback((p: Record<string, unknown>) => {
    const sock = ws.current
    if (sock && sock.readyState === WebSocket.OPEN) {
      const payload = enc.encode(JSON.stringify(p))
      const frame = new Uint8Array(payload.length + 1)
      frame[0] = APPLY
      frame.set(payload, 1)
      sock.send(frame)
    }
  }, [])

  const connect = useCallback((item: Item) => {
    if (!termHost.current) return
    teardown()
    setStatus('connecting…')

    const cs = getComputedStyle(document.documentElement)
    const v = (name: string, fb: string) => cs.getPropertyValue(name).trim() || fb
    const t = new Terminal({
      cursorBlink: !item.watch,
      disableStdin: item.watch,
      fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace',
      fontSize: 14,
      scrollback: 5000,
      theme: {
        background: v('--surface-1', '#0f141b'),
        foreground: v('--text-primary', '#e6edf3'),
        cursor: v('--accent', '#2a78d6'),
      },
    })
    const f = new FitAddon()
    t.loadAddon(f)
    t.open(termHost.current)
    f.fit()
    term.current = t
    fit.current = f

    const proto = location.protocol === 'https:' ? 'wss' : 'ws'
    const url = `${proto}://${location.host}/terminal-fun/ws/${item.id}?cols=${t.cols}&rows=${t.rows}`
    const sock = new WebSocket(url)
    sock.binaryType = 'arraybuffer'
    ws.current = sock

    sock.onopen = () => { setConnected(true); setStatus(''); if (!item.watch) t.focus() }
    sock.onclose = () => { setConnected(false) }
    sock.onerror = () => { setStatus('connection error') }
    sock.onmessage = (ev) => {
      if (typeof ev.data === 'string') {
        if (ev.data[0] === STATUS) setStatus(ev.data.slice(1))
        return
      }
      const u = new Uint8Array(ev.data as ArrayBuffer)
      if (u[0] === IN) t.write(u.subarray(1))
    }

    const send = (tag: number, payload: Uint8Array) => {
      if (sock.readyState !== WebSocket.OPEN) return
      const frame = new Uint8Array(payload.length + 1)
      frame[0] = tag
      frame.set(payload, 1)
      sock.send(frame)
    }
    if (!item.watch) t.onData((d) => send(IN, enc.encode(d)))
    t.onResize(({ cols, rows }) => send(RESIZE, enc.encode(JSON.stringify({ cols, rows }))))

    const obs = new ResizeObserver(() => { try { f.fit() } catch { /* mid-teardown */ } })
    obs.observe(termHost.current)
    ro.current = obs
  }, [teardown])

  useEffect(() => {
    if (active) connect(active)
    return () => teardown()
  }, [active, connect, teardown])

  const backToMenu = () => { teardown(); setActive(null); setStatus(''); refreshSaves() }

  const sendChat = useCallback(async () => {
    const text = input.trim()
    if (!text || busy) return
    setInput('')
    const history = msgs.slice(-6)
    setMsgs((m) => [...m, { role: 'user', content: text }])
    setBusy(true)
    try {
      const r = await fetch('/terminal-fun/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: text, history, item: active?.id ?? null, params: params.current }),
      })
      const d = await r.json()
      setMsgs((m) => [...m, { role: 'assistant', content: d.reply ?? '(no reply)' }])
      if (d.action?.type === 'set_params' && active && d.action.item === active.id) {
        params.current = { ...params.current, ...d.action.params }
        applyParams(params.current)
      }
    } catch {
      setMsgs((m) => [...m, { role: 'assistant', content: '(the assistant is unavailable right now)' }])
    } finally {
      setBusy(false)
    }
  }, [input, busy, msgs, active, applyParams])

  const modal = help ? <HelpModal item={help} hasSave={saved.has(help.id)} onDiscard={() => discardSave(help.id)} onClose={() => setHelp(null)} /> : null

  const menu = (
    <div className="menu">
      <div className="lead">Pick something to run in a live terminal. Tap the <b>ⓘ</b> on any tile for how to play, or ask the assistant below. Everything is self-hosted — nothing leaves this box.</div>
      {cats.map((c) => {
        const mine = items.filter((i) => i.category === c.id)
        if (!mine.length) return null
        return (
          <div key={c.id}>
            <div className="sec">{c.label}</div>
            <div className="grid">
              {mine.map((i) => (
                <div key={i.id} className="tile" role="button" tabIndex={0} onClick={() => setActive(i)}>
                  <button className="info-btn" title={`How to play ${i.label}`} onClick={(e) => { e.stopPropagation(); setHelp(i) }}>i</button>
                  <span className="ic">{i.icon}</span>
                  <span className="nm">{i.label}</span>
                  <span className="chips">
                    <span className={`chip ${i.watch ? 'watch' : 'play'}`}>{i.watch ? 'watch' : 'play'}</span>
                    {i.tunable && <span className="chip ai">AI</span>}
                    {i.saveable && saved.has(i.id) && <span className="chip resume">resume</span>}
                    {i.saveable && saved.has(i.id) && (
                      <span className="chip reset" role="button" tabIndex={0} title={`Delete your saved ${i.label} game and start fresh`}
                        onClick={(e) => { e.stopPropagation(); if (confirm(`Delete your saved ${i.label} game and start from scratch? This can't be undone.`)) discardSave(i.id) }}>reset</span>
                    )}
                  </span>
                </div>
              ))}
            </div>
          </div>
        )
      })}
      {!items.length && <div className="lead">Loading…</div>}
    </div>
  )

  const terminalView = active && (
    <div className="termwrap">
      <div className="bar">
        <button className="back" onClick={backToMenu}>← Menu</button>
        <span className="title"><span>{active.icon}</span>{active.label}</span>
        <span className="sp" />
        <span className="status"><span className={`dot ${connected ? 'on' : ''}`} />{connected ? (active.watch ? 'playing' : 'connected') : status || 'idle'}</span>
        <button className="ghost" onClick={() => setHelp(active)}>ⓘ How to play</button>
        <button className="ghost" onClick={() => connect(active)}>Restart</button>
      </div>
      <div className="term" ref={termHost} />
    </div>
  )

  return (
    <div className="ft">
      <header className="head">
        <span className="logo" aria-hidden="true">🕹️</span>
        <div className="titles">
          <h1>Terminal Fun</h1>
          <span className="stmt">
            Self-hosted terminal games and toys, with an AI helper that can explain them and
            retune them live — sandboxed in its own container, nothing leaves this box
          </span>
          <ModelChips status={models} />
        </div>
      </header>
      <div className="body">
        {active ? terminalView : menu}
      </div>
      <div className="chat">
        <div className="row">
          <input
            className="ask"
            placeholder="Ask me about anything on this page"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') sendChat() }}
          />
          <button className="send" disabled={busy || !input.trim()} onClick={sendChat}>Ask</button>
        </div>
        {active?.tunable && tuneHint && (
          <div className="hint">💡 I can change <b>{active.label}</b>: {tuneHint}. Try “make it {active.id === 'cmatrix' ? 'red and rainbow' : 'faster'}”.</div>
        )}
        <div className="log" ref={logRef}>
          {busy && <div className="msg assistant">…</div>}
          {msgs.map((m, idx) => ({ m, idx })).reverse().map(({ m, idx }) => (
            <div key={idx} className={`msg ${m.role}`}>{m.content}</div>
          ))}
        </div>
      </div>
      {modal}
    </div>
  )
}
