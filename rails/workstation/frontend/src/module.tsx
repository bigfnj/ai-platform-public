// Workstation terminal as a federated React module for the platform shell.
// Exposes ./module. A browser xterm.js terminal wired over a WebSocket to the
// backend's PTY-over-SSH bridge, with preset tabs (Shell / Claude Code / Codex).
// Themed off the shell's CSS variables (with fallbacks) so it matches every app.
import { useCallback, useEffect, useRef, useState } from 'react'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import '@xterm/xterm/css/xterm.css'

interface Preset {
  id: string
  label: string
  icon: string
}

const CSS = `
.wt { --i:var(--text-primary,#e6edf3); --mut:var(--text-secondary,#8b98a9);
  --s1:var(--surface-1,#0f141b); --s2:var(--surface-2,#161c26); --bd:var(--border,#263042);
  --ac:var(--accent,#2a78d6); color:var(--i); display:flex; flex-direction:column;
  height:calc(100vh - 132px); min-height:440px; }
.wt .bar { display:flex; align-items:center; gap:8px; margin-bottom:10px; flex-wrap:wrap; }
.wt .tab { font:inherit; font-weight:650; font-size:13px; padding:7px 14px; border-radius:999px;
  border:1px solid var(--bd); background:transparent; color:var(--mut); cursor:pointer;
  display:inline-flex; gap:6px; align-items:center; }
.wt .tab.on { background:var(--grad-accent, var(--ac)); color:#fff; border-color:transparent; }
.wt .sp { flex:1; }
.wt .status { font-size:12.5px; color:var(--mut); }
.wt .dot { width:8px; height:8px; border-radius:50%; display:inline-block; background:#6b7280; margin-right:6px; }
.wt .dot.on { background:#3fb950; }
.wt .ghost { font:inherit; font-weight:600; font-size:13px; padding:6px 12px; border-radius:8px;
  border:1px solid var(--bd); background:transparent; color:var(--i); cursor:pointer; }
.wt .ghost:disabled { opacity:.5; cursor:default; }
.wt .term { flex:1; min-height:0; background:var(--s1); border:1px solid var(--bd);
  border-radius:10px; padding:8px; overflow:hidden; }
.wt .hint { color:var(--mut); text-align:center; padding:36px; }
`

const IN = 0x00 // input / output data frame tag
const RESIZE = 0x01 // resize control frame tag
const STATUS = '\x04' // server status line prefix (text frame)

export default function WorkstationModule() {
  const [presets, setPresets] = useState<Preset[]>([])
  const [active, setActive] = useState<string | null>(null)
  const [connected, setConnected] = useState(false)
  const [status, setStatus] = useState('')

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
    fetch('/workstation/api/presets')
      .then((r) => r.json())
      .then((d) => setPresets(d.presets ?? []))
      .catch(() => setPresets([]))
  }, [])

  const teardown = useCallback(() => {
    ro.current?.disconnect(); ro.current = null
    if (ws.current) { ws.current.onclose = null; ws.current.close(); ws.current = null }
    term.current?.dispose(); term.current = null
    fit.current = null
    setConnected(false)
  }, [])

  useEffect(() => () => teardown(), [teardown])

  const open = useCallback((presetId: string) => {
    if (!termHost.current) return
    teardown()
    setActive(presetId)
    setStatus('connecting…')

    const cs = getComputedStyle(document.documentElement)
    const v = (name: string, fb: string) => cs.getPropertyValue(name).trim() || fb
    const t = new Terminal({
      cursorBlink: true,
      fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace',
      fontSize: 13,
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
    const url = `${proto}://${location.host}/workstation/ws/${presetId}?cols=${t.cols}&rows=${t.rows}`
    const sock = new WebSocket(url)
    sock.binaryType = 'arraybuffer'
    ws.current = sock
    const enc = new TextEncoder()

    sock.onopen = () => { setConnected(true); setStatus(''); t.focus() }
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
    t.onData((d) => send(IN, enc.encode(d)))
    t.onResize(({ cols, rows }) => send(RESIZE, enc.encode(JSON.stringify({ cols, rows }))))

    const obs = new ResizeObserver(() => { try { f.fit() } catch { /* mid-teardown */ } })
    obs.observe(termHost.current)
    ro.current = obs
  }, [teardown])

  return (
    <div className="wt">
      <div className="bar">
        {presets.map((p) => (
          <button key={p.id} className={`tab ${active === p.id ? 'on' : ''}`} onClick={() => open(p.id)}>
            <span>{p.icon}</span>{p.label}
          </button>
        ))}
        <span className="sp" />
        <span className="status"><span className={`dot ${connected ? 'on' : ''}`} />{connected ? 'connected' : status || 'idle'}</span>
        <button className="ghost" disabled={!active} onClick={() => active && open(active)}>Reconnect</button>
      </div>
      <div className="term" ref={termHost}>
        {!active && <div className="hint">Pick a session above to open a terminal on your workstation.</div>}
      </div>
    </div>
  )
}
