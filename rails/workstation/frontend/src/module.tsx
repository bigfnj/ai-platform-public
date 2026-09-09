// Workstation terminal as a federated React module for the platform shell.
// Exposes ./module. A browser xterm.js terminal wired over a WebSocket to the
// backend's PTY-over-SSH bridge, with preset tabs (Shell / Claude Code / Codex).
// Themed off the shell's CSS variables (with fallbacks) so it matches every app.
import { useCallback, useEffect, useRef, useState } from 'react'
import { RailHeader } from '@web-core'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import '@xterm/xterm/css/xterm.css'

interface Preset {
  id: string
  label: string
  icon: string
}

// A desktop app published from the host over RDP RemoteApp. Clicking one downloads an
// .rdp launcher; the client's own RDP client opens just that application's window.
interface RdpApp {
  id: string
  label: string
  icon: string
}

const CSS = `
.wt { --i:var(--text-primary,#e6edf3); --mut:var(--text-secondary,#8b98a9);
  --s1:var(--surface-1,#0f141b); --s2:var(--surface-2,#161c26); --bd:var(--border,#263042);
  --ac:var(--accent,#2a78d6); color:var(--i); display:flex; flex-direction:column;
  /* 220px, not 132: the shared RailHeader now sits above this container and eats ~88px
     (content ~48px + padding-bottom 18 + 2px rule + margin-bottom 20), so subtract that
     on top of the original 132 shell-chrome allowance to keep the terminal in-viewport. */
  height:calc(100vh - 220px); min-height:440px; }
.wt .bar { display:flex; align-items:center; gap:8px; margin-bottom:10px; flex-wrap:wrap; }
.wt .tab { font:inherit; font-weight:650; font-size:13px; padding:7px 14px; border-radius:999px;
  border:1px solid var(--bd); background:transparent; color:var(--mut); cursor:pointer;
  display:inline-flex; gap:6px; align-items:center; text-decoration:none; }
.wt .tab.on { background:var(--grad-accent, var(--ac)); color:#fff; border-color:transparent; }
.wt .sp { flex:1; }
.wt .status { font-size:12.5px; color:var(--mut); }
/* A dead session used to be indistinguishable from a live one: same focus, same cursor,
   same last frame, while send() discards every keystroke on a closed socket. Mark it in
   the pane, the status and the button that fixes it. (No backticks in here: this whole
   block is a template literal, and one would end it.) */
.wt .status.ended { color:#d29922; font-weight:650; }
.wt .dot { width:8px; height:8px; border-radius:50%; display:inline-block; background:#6b7280; margin-right:6px; }
.wt .dot.on { background:#3fb950; }
.wt .ghost { font:inherit; font-weight:600; font-size:13px; padding:6px 12px; border-radius:8px;
  border:1px solid var(--bd); background:transparent; color:var(--i); cursor:pointer; }
.wt .ghost:disabled { opacity:.5; cursor:default; }
.wt .term { flex:1; min-height:0; background:var(--s1); border:1px solid var(--bd);
  border-radius:10px; padding:8px; overflow:hidden; }
.wt .term.dead { border-style:dashed; border-color:#d29922; opacity:.68; }
.wt .ghost.cta { border-color:#d29922; color:#d29922; }
.wt .hint { color:var(--mut); text-align:center; padding:36px; }
.wt .apps { display:flex; align-items:center; gap:8px; margin-bottom:10px; flex-wrap:wrap; }
.wt .apps .cap { font-size:12px; font-weight:650; letter-spacing:.04em; text-transform:uppercase;
  color:var(--mut); margin-right:2px; }
.wt .app { font:inherit; font-weight:650; font-size:13px; padding:7px 14px; border-radius:999px;
  border:1px solid var(--bd); background:var(--s2); color:var(--i); cursor:pointer;
  display:inline-flex; gap:6px; align-items:center; text-decoration:none; }
.wt .app:hover { border-color:var(--ac); }
.wt .apps .note { font-size:12.5px; color:var(--mut); }
`

const IN = 0x00 // input / output data frame tag
const RESIZE = 0x01 // resize control frame tag
const STATUS = '\x04' // server status line prefix (text frame)

export default function WorkstationModule() {
  const [presets, setPresets] = useState<Preset[]>([])
  const [rdpApps, setRdpApps] = useState<RdpApp[]>([])
  const [active, setActive] = useState<string | null>(null)
  const [connected, setConnected] = useState(false)
  // The socket closed on its own (the remote program quit, or the link dropped) rather
  // than because we tore it down to open another. Drives the "this terminal is dead" cues.
  const [ended, setEnded] = useState(false)
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

  // Published desktop apps. Absent unless the backend has an RDP host configured, so
  // the whole row stays hidden rather than showing a broken affordance.
  useEffect(() => {
    fetch('/workstation/api/remoteapp')
      .then((r) => r.json())
      .then((d) => setRdpApps(d.enabled ? (d.apps ?? []) : []))
      .catch(() => setRdpApps([]))
  }, [])

  const teardown = useCallback(() => {
    ro.current?.disconnect(); ro.current = null
    if (ws.current) { ws.current.onclose = null; ws.current.close(); ws.current = null }
    term.current?.dispose(); term.current = null
    fit.current = null
    setConnected(false)
    setEnded(false)
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
    // teardown() nulls this handler before closing, so it only fires for a close we did
    // NOT ask for. xterm keeps focus and keeps painting the last frame either way, and
    // `send` below silently discards input once the socket is shut — which is exactly
    // how "Claude Code quit at the trust prompt" reads as "the terminal ignores my keys".
    sock.onclose = () => {
      setConnected(false)
      setEnded(true)
      t.options.cursorBlink = false
      t.write('\r\n\x1b[33m[ session ended ]\x1b[0m \x1b[2mkeystrokes are no longer being '
        + 'sent. Press Reconnect to start a new session.\x1b[0m\r\n')
    }
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
    <>
      <RailHeader
        icon="💻"
        title="Workstation"
        subtitle="A browser terminal into the host over SSH — highest-privilege rail, entitlement-gated to a single owner."
      />
      <div className="wt">
      <div className="bar">
        {presets.map((p) => (
          <button key={p.id} className={`tab ${active === p.id ? 'on' : ''}`} onClick={() => open(p.id)}>
            <span>{p.icon}</span>{p.label}
          </button>
        ))}
        {rdpApps.map((a) => (
          <a key={a.id} className="tab" href={`/workstation/api/remoteapp/${a.id}.rdp`} download
             title="Opens in your own RDP client (app window only)">
            <span>{a.icon}</span>{a.label}
          </a>
        ))}
        <span className="sp" />
        <span className={`status ${ended ? 'ended' : ''}`}><span className={`dot ${connected ? 'on' : ''}`} />{connected ? 'connected' : status || 'idle'}</span>
        <button className={`ghost ${ended ? 'cta' : ''}`} disabled={!active} onClick={() => active && open(active)}>Reconnect</button>
      </div>
      <div className={`term ${ended ? 'dead' : ''}`} ref={termHost}>
        {!active && <div className="hint">Pick a session above to open a terminal on your workstation.</div>}
      </div>
      </div>
    </>
  )
}
