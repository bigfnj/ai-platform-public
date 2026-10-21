import { useEffect, useState } from 'react'
import { getCapabilities } from './api'
import { RailHeader, ModelChips } from '@web-core'
import Builder from './builder'
import type { Capabilities } from './types'
import { canListen, canRecord, getAudioInputDevices, getAudioOutputDevices, setAudioInput, setAudioSink, stopSpeaking } from './voice'
import { useVoiceSession } from './voicehooks'
import './theme.css'

type Tab = 'about' | 'builder' | 'voice'

const TABS: { id: Tab; icon: string; label: string }[] = [
  { id: 'about', icon: '💡', label: 'What This Is' },
  { id: 'builder', icon: '📋', label: 'Scenario Builder' },
  { id: 'voice', icon: '🎤', label: 'Voice Chat' },
]

const LS_SINK = 'smb-audio-sink'
const LS_INPUT = 'smb-audio-input'
const lsGet = (k: string) => { try { return localStorage.getItem(k) || '' } catch { return '' } }
const lsSet = (k: string, v: string) => { try { localStorage.setItem(k, v) } catch {} }

// Whether this browser can route audio to a chosen output. Asked of the prototype ONCE: it
// was a throwaway `new Audio()` per render of the picker, which re-renders on the 6 s
// capabilities tick, so ~600 discarded media elements an hour per open tab.
const HAS_SINK_ID = typeof HTMLMediaElement !== 'undefined' &&
  typeof (HTMLMediaElement.prototype as { setSinkId?: unknown }).setSinkId === 'function'

// The pickers' two icons, stroked in currentColor so they take the button's ink in every
// palette. They were the emoji 🎙️ and 🔊, which paint their own grey: on a light header the
// strongest quarter of their pixels reached as little as 1.9:1 and 1.6:1
// (smb-device-icons in web/tests/browser/railchecks/smb_partner_enablement.py).
const ICON = { viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 2,
  strokeLinecap: 'round' as const, strokeLinejoin: 'round' as const, 'aria-hidden': true }

function MicIcon() {
  return (
    <svg {...ICON}>
      <rect x="9" y="3" width="6" height="11" rx="3" />
      <path d="M5 11a7 7 0 0 0 14 0M12 18v3M8 21h8" />
    </svg>
  )
}

function SpeakerIcon() {
  return (
    <svg {...ICON}>
      <path d="M4 9h4l5-4v14l-5-4H4z" />
      <path d="M16.5 8.5a5 5 0 0 1 0 7M19 6a8.5 8.5 0 0 1 0 12" />
    </svg>
  )
}

/** Audio device picker — lives inside the SMB rail's own header (no fixed positioning). */
function AudioDevicePicker() {
  const [outputs, setOutputs] = useState<MediaDeviceInfo[]>([])
  const [inputs, setInputs] = useState<MediaDeviceInfo[]>([])
  const [sinkId, setSinkId] = useState(() => lsGet(LS_SINK))
  const [inputId, setInputId] = useState(() => lsGet(LS_INPUT))
  const [openOut, setOpenOut] = useState(false)
  const [openIn, setOpenIn] = useState(false)

  useEffect(() => {
    // Restore saved devices into the voice module on mount.
    const savedSink = lsGet(LS_SINK)
    const savedInput = lsGet(LS_INPUT)
    if (savedSink) setAudioSink(savedSink)
    if (savedInput) void setAudioInput(savedInput)

    const refresh = () => {
      getAudioOutputDevices().then(setOutputs)
      getAudioInputDevices().then(setInputs)
    }
    refresh()
    navigator.mediaDevices?.addEventListener?.('devicechange', refresh)
    return () => navigator.mediaDevices?.removeEventListener?.('devicechange', refresh)
  }, [])

  const hasMediaDevices = !!navigator.mediaDevices?.enumerateDevices
  const labeledOut = outputs.filter((d) => d.label)
  const labeledIn = inputs.filter((d) => d.label)
  // No media devices API at all → nothing to show
  if (!hasMediaDevices) return null

  const requestPermission = () =>
    navigator.mediaDevices.getUserMedia({ audio: true }).then((s) => s.getTracks().forEach((t) => t.stop())).catch(() => {})

  const pickOut = (id: string) => { setSinkId(id); setAudioSink(id); lsSet(LS_SINK, id); setOpenOut(false) }
  const pickIn = (id: string) => { setInputId(id); void setAudioInput(id); lsSet(LS_INPUT, id); setOpenIn(false) }

  const noPermission = !labeledIn.length && !labeledOut.length

  return (
    <div className="audio-pickers">
      <div className="audio-picker-wrap">
        <button className="ghost icon-btn" title="Microphone" aria-label="Microphone"
          onClick={() => { setOpenIn((o) => !o); setOpenOut(false) }}><MicIcon /></button>
        {openIn && (
          <div className="audio-menu">
            <div className="audio-menu-label">Microphone</div>
            {noPermission
              ? <button className="audio-item" onClick={() => { requestPermission(); setOpenIn(false) }}>Allow microphone access…</button>
              : labeledIn.map((d) => (
                  <button key={d.deviceId} className={`audio-item${d.deviceId === inputId ? ' active' : ''}`} onClick={() => pickIn(d.deviceId)}>
                    <span className="audio-check">{d.deviceId === inputId ? '✓' : ''}</span>{d.label}
                  </button>
                ))
            }
          </div>
        )}
      </div>
      {HAS_SINK_ID && (
        <div className="audio-picker-wrap">
          <button className="ghost icon-btn" title="Speaker" aria-label="Speaker"
            onClick={() => { setOpenOut((o) => !o); setOpenIn(false) }}><SpeakerIcon /></button>
          {openOut && (
            <div className="audio-menu">
              <div className="audio-menu-label">Speaker</div>
              {noPermission
                ? <button className="audio-item" onClick={() => { requestPermission(); setOpenOut(false) }}>Allow microphone access…</button>
                : labeledOut.map((d) => (
                    <button key={d.deviceId} className={`audio-item${d.deviceId === sinkId ? ' active' : ''}`} onClick={() => pickOut(d.deviceId)}>
                      <span className="audio-check">{d.deviceId === sinkId ? '✓' : ''}</span>{d.label}
                    </button>
                  ))
              }
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// The model-state dots are now the shared @web-core <ModelChips> (the four-state red/blue/
// orange/green contract lives there, on .rail-chip-dot). caps.models is already the ModelChip
// shape (slot/label/model/state/role), so it forwards straight through, and the caps lifecycle
// maps onto the component's three states: null → checking, broker !== 'ok' → unreachable, else
// ok. This rail's own extras — the binary Voice indicator and the corpus summary — ride in the
// `trailing` slot so they still sit after the model chips, as the old inline status row showed.
function SmbChips({ caps }: { caps: Capabilities | null }) {
  return (
    <ModelChips
      status={caps ? (caps.broker === 'ok' ? 'ok' : 'unreachable') : 'checking'}
      models={caps?.models}
      trailing={
        caps && caps.broker === 'ok' ? (
          <>
            <span title={caps.voice.note}>
              <i className={`dot ${caps.voice.effective !== 'off' ? 'on' : 'off'}`} />
              Voice ({caps.voice.effective === 'broker' ? 'Kokoro' : caps.voice.effective})
            </span>{' · '}
            {caps.corpus.chunks} chunks · {caps.corpus.collections} collections
          </>
        ) : undefined
      }
    />
  )
}

function About() {
  return (
    <>
      <div className="center">
        <h2 style={{ margin: '8px 0' }}>What is this?</h2>
        <p className="muted" style={{ maxWidth: 620, margin: '0 auto' }}>
          A two-minute AI coach that turns a partner's basic knowledge about their next customer
          into a complete, Microsoft-specific battle plan for the meeting.
        </p>
      </div>
      <div className="grid">
        <div className="card">
          <h4>🤝 Who it's for</h4>
          <p>
            Microsoft's reseller partners — the local IT companies selling Microsoft 365 and
            Azure to small businesses.
          </p>
        </div>
        <div className="card">
          <h4>🔥 The problem</h4>
          <p>
            Before a customer meeting they need to prep. Right now they either skip it or spend
            30 minutes cobbling it together. Inconsistent, slow, and the bad ones cost deals.
          </p>
        </div>
        <div className="card">
          <h4>⚡ What it does</h4>
          <p>
            Pick an industry, answer four quick questions. Thirty seconds in, you get a full
            meeting kit — what to ask, what products fit, how to handle objections, and exactly
            what to do in Partner Center after.
          </p>
        </div>
      </div>
      <div className="card">
        <h4>📈 Why it matters</h4>
        <p>
          Microsoft's SMB revenue runs through these partners. A prepared partner closes more
          deals. This makes every partner as good as the best one — in two minutes, on any device.
        </p>
      </div>
    </>
  )
}

function VoiceChat({ caps }: { caps: Capabilities | null }) {
  // All of it lives in VoiceSession (voicesession.ts), shared with the mobile Chat; this only
  // renders it. Unmounting (a tab switch) disposes the session: the answer in flight, its
  // watchdog, the recording, a browser recognition and this chat's read-aloud all end there.
  const [st, session] = useVoiceSession()
  const s = () => session.current

  // Server-side STT when the broker's media worker is up, because only that path can use
  // the microphone the user picked. Web Speech is the fallback, and it always listens to
  // the OS default device no matter what the picker says.
  const serverStt = caps?.voice?.stt === 'broker' && canRecord()

  return (
    <>
      <div className="askrow">
        <input
          value={st.draft}
          onChange={(e) => s()?.setDraft(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && s()?.submit(st.draft, false)}
          placeholder="Ask anything about Microsoft SMB sales…"
          disabled={st.busy}
        />
        <button className="go" onClick={() => s()?.submit(st.draft, false)}
                disabled={st.busy || !st.draft.trim()}>
          Ask
        </button>
        <button className="go"
                onClick={() => (st.draft.trim() ? s()?.submit(st.draft, true) : s()?.mic(serverStt))}
                disabled={st.busy || st.hearing || st.thinking}>
          🔊 Ask + speak
        </button>
        {(serverStt || canListen()) && (
          st.recording ? (
            // Recording has no automatic endpoint detection, so stopping is the user's call.
            <button className="ghost recording" onClick={() => s()?.stopRecording()}>
              ⏹ Stop &amp; send
            </button>
          ) : st.hearing ? (
            // The browser's recognition ends on its own when you stop talking; this is the
            // way to abandon it instead, which nothing offered before.
            <button className="ghost recording" onClick={() => s()?.cancelListening()}>
              ✕ Cancel
            </button>
          ) : (
            <button className="ghost" onClick={() => s()?.mic(serverStt)}
                    disabled={st.busy || st.thinking}>
              {st.thinking ? '… transcribing' : '🎤 Speak'}
            </button>
          )
        )}
        {st.speaking && (
          <button className="ghost" onClick={() => s()?.stopVoice()}>
            ⏹ Stop voice
          </button>
        )}
      </div>
      {st.err && <p className="muted" style={{ color: '#f87171', margin: '4px 0' }}>⚠ Mic: {st.err}</p>}
      {!st.turns.length && (
        <p className="muted center">
          MCEM stages · Azure migration · Copilot · deal registration · co-sell
        </p>
      )}
      {st.turns.map((t) => (
        <div className="card" key={t.id}>
          <strong>{t.question}</strong>
          <div className="answer" style={{ marginTop: 10 }}>
            {t.answer}
            {t.streaming && <span className="muted"> ▍</span>}
          </div>
          {t.error && <p className="muted">⚠ {t.error}</p>}
          {!t.streaming && !t.grounded && !t.error && (
            <p className="ungrounded">
              No matching SME content was retrieved — this answer is ungrounded. Add material to
              the knowledge base before relying on it.
            </p>
          )}
          {t.citations.length > 0 && (
            <div className="cites">
              {t.citations.map((c) => (
                <div className="cite" key={c.n}>
                  [{c.n}] {c.collection} / {c.source}
                  {c.title && ` — ${c.title}`}
                </div>
              ))}
            </div>
          )}
        </div>
      ))}
      {caps && caps.corpus.chunks === 0 && (
        <p className="muted">
          The knowledge base is empty, so every answer will be ungrounded. Add markdown under{' '}
          <code>seed/knowledge-base/</code> and re-ingest.
        </p>
      )}
    </>
  )
}

/**
 * The live mobile preview: a phone frame overlaid on the desktop app, rendering the real
 * mobile build in an iframe. An iframe rather than a re-render of the same components, so the
 * preview cannot drift from what a partner actually gets on their phone — this is the moment
 * that carries "portable from web to mobile", so it must not be a mock.
 */
function MobilePreview({ onClose }: { onClose: () => void }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <div className="mpreview" onClick={onClose}>
      <div className="mphone" onClick={(e) => e.stopPropagation()}>
        <div className="mscreen">
          <div className="mnotch" />
          <iframe src="/smb-partner-enablement/m/" title="Mobile preview" />
          <div className="mhome" />
        </div>
      </div>
      <p className="muted">
        Live mobile preview · tap outside or press <kbd>Esc</kbd> to exit
      </p>
    </div>
  )
}

export default function SmbPartnerModule() {
  const [tab, setTab] = useState<Tab>('about')
  const [caps, setCaps] = useState<Capabilities | null>(null)
  const [preview, setPreview] = useState(false)

  // Poll the capabilities payload — the chips are derived from live residency, which changes
  // on its own: the broker evicts on a keep_alive expiry, and asking a question warms the
  // model back up. A one-shot fetch on mount froze the chips at page-load state, so a model
  // that loaded afterwards (i.e. the moment you actually used the rail) kept reporting `cold`
  // forever while the shell's own top-bar widget correctly showed it resident. 6s matches the
  // cadence co-worker, terminal-fun, gemini-cx and recipe-book already use, and the warming
  // window is ~7s on this box, so a transition is visible rather than skipped over.
  useEffect(() => {
    let live = true
    const tick = () => {
      getCapabilities()
        .then((d) => { if (live) setCaps(d) })
        .catch(() => { if (live) setCaps(null) })
    }
    tick()
    const id = window.setInterval(tick, 6000)
    return () => { live = false; window.clearInterval(id); stopSpeaking() }
  }, [])

  return (
    <div className="smbp">
      {/* The shared @web-core <RailHeader>: icon · title · subtitle · chips, with the rail's own
          audio controls in the actions slot. The 🤝 is this rail's catalog.py icon, so the
          header matches the sidebar item that opened it. The tab bar below stays put — it is a
          separate row, not part of the header. */}
      <RailHeader
        icon="🤝"
        title="SMB Partner Enablement"
        subtitle="A two-minute meeting kit for Microsoft SMB partners — grounded only in curated SME material, every claim cited"
        chips={<SmbChips caps={caps} />}
        actions={<AudioDevicePicker />}
      />
      <div className="tabs" role="tablist">
        {TABS.map((t) => (
          <button
            key={t.id}
            role="tab"
            aria-selected={tab === t.id}
            className="tab"
            onClick={() => setTab(t.id)}
          >
            {t.icon} {t.label}
          </button>
        ))}
      </div>
      {tab === 'about' && <About />}
      {tab === 'builder' && <Builder />}
      {tab === 'voice' && <VoiceChat caps={caps} />}

      <button className="mobile-fab" onClick={() => setPreview(true)}>
        📱 Mobile
      </button>
      {preview && <MobilePreview onClose={() => setPreview(false)} />}

      <footer className="muted center" style={{ fontSize: 11, paddingTop: 8 }}>
        Partner Center SMB Readiness · rebuilt from the team hackathon
        prototype
      </footer>
    </div>
  )
}
