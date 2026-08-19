import { useCallback, useEffect, useRef, useState } from 'react'
import { askStream, getCapabilities, transcribe } from './api'
import Builder from './builder'
import type { Capabilities, ModelState, Turn } from './types'
import type { Recorder } from './voice'
import { canListen, canRecord, getAudioInputDevices, getAudioOutputDevices, listen, record, setAudioInput, setAudioSink, speak, stopSpeaking } from './voice'
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

  const hasSinkId = typeof (new Audio() as any).setSinkId === 'function'
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
        <button className="ghost icon-btn" title="Microphone" onClick={() => { setOpenIn((o) => !o); setOpenOut(false) }}>🎙️</button>
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
      {hasSinkId && (
        <div className="audio-picker-wrap">
          <button className="ghost icon-btn" title="Speaker" onClick={() => { setOpenOut((o) => !o); setOpenIn(false) }}>🔊</button>
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

// Four-state dot, identical in meaning across every rail: red = the model is not installed,
// blue = installed but not resident, orange = a job is warming it, green = resident. The
// red/blue distinction is the operationally useful one — red needs an `ollama pull`, blue just
// needs someone to ask a question. This rail rendered a two-state on/off dot until the chip
// contract was unified; "off" was hiding which of those two situations you were in.
const STATE_TEXT: Record<ModelState, string> = {
  missing: 'not found',
  cold: 'cold',
  warming: 'warming up',
  loaded: 'GPU · ready',
}

function AiStatus({ caps }: { caps: Capabilities | null }) {
  if (!caps) return <span className="muted">checking…</span>
  if (caps.broker !== 'ok') return <span className="muted">● broker unreachable</span>
  return (
    <div className="status">
      {caps.models.map((m) => (
        <span key={m.slot} title={`${m.role} → ${m.model} · ${STATE_TEXT[m.state]}`}>
          <i className={`dot ${m.state}`} />
          {m.label} ({m.model}) <span className="muted">{STATE_TEXT[m.state]}</span>
        </span>
      ))}
      <span title={caps.voice.note}>
        <i className={`dot ${caps.voice.effective !== 'off' ? 'on' : 'off'}`} />
        Voice ({caps.voice.effective === 'broker' ? 'Kokoro' : caps.voice.effective})
      </span>
      <span className="muted">
        {caps.corpus.chunks} chunks · {caps.corpus.collections} collections
      </span>
    </div>
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
  const [question, setQuestion] = useState('')
  const [turns, setTurns] = useState<Turn[]>([])
  const [busy, setBusy] = useState(false)
  const [speaking, setSpeaking] = useState(false)
  const [hearing, setHearing] = useState(false)
  const [thinking, setThinking] = useState(false)
  const [sttErr, setSttErr] = useState('')
  const nextId = useRef(1)
  const recorder = useRef<Recorder | null>(null)

  // Switching tabs mid-recording must release the mic, or the browser keeps the capture
  // indicator lit and the device stays held.
  useEffect(() => () => { recorder.current?.cancel(); recorder.current = null }, [])

  const submit = useCallback(
    (text: string, spoken: boolean) => {
      const q = text.trim()
      if (!q || busy) return
      const id = nextId.current++
      setTurns((t) => [
        ...t,
        { id, question: q, answer: '', citations: [], grounded: false, streaming: true },
      ])
      setQuestion('')
      setBusy(true)
      const patch = (fn: (t: Turn) => Turn) =>
        setTurns((all) => all.map((t) => (t.id === id ? fn(t) : t)))

      askStream(
        { question: q, speak: spoken },
        {
          onCitations: (citations, grounded) => patch((t) => ({ ...t, citations, grounded })),
          onToken: (tok) => patch((t) => ({ ...t, answer: t.answer + tok })),
          onDone: ({ answer, voice }) => {
            patch((t) => ({ ...t, answer, streaming: false }))
            setBusy(false)
            if (spoken && voice) {
              setSpeaking(true)
              speak(voice, () => setSpeaking(false))
            }
          },
          onError: (detail) => {
            patch((t) => ({ ...t, streaming: false, error: detail }))
            setBusy(false)
          },
        },
      )
    },
    [busy],
  )

  // Server-side STT when the broker's media worker is up, because only that path can use
  // the microphone the user picked. Web Speech is the fallback, and it always listens to
  // the OS default device no matter what the picker says.
  const serverStt = caps?.voice?.stt === 'broker' && canRecord()

  const stopRecording = () => { recorder.current?.stop(); recorder.current = null }

  const micBrowser = () => {
    setHearing(true)
    listen({
      onResult: (t, final) => { setQuestion(t); if (final) submit(t, true) },
      onEnd: () => setHearing(false),
      onError: (detail) => { setHearing(false); setSttErr(detail) },
    })
  }

  const mic = async () => {
    if (hearing || thinking) return
    setSttErr('')
    if (!serverStt) { micBrowser(); return }
    const r = await record({
      onStart: () => setHearing(true),
      onError: (detail) => { setHearing(false); recorder.current = null; setSttErr(detail) },
      onReady: async (audio_b64, suffix) => {
        setHearing(false)
        recorder.current = null
        setThinking(true)
        try {
          const { text } = await transcribe(audio_b64, suffix)
          if (!text) { setSttErr('nothing was heard — try again'); return }
          setQuestion(text)
          submit(text, true)
        } catch (e: any) {
          setSttErr(String(e?.message || 'transcription failed'))
        } finally {
          setThinking(false)
        }
      },
    })
    recorder.current = r
  }

  return (
    <>
      <div className="askrow">
        <input
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && submit(question, false)}
          placeholder="Ask anything about Microsoft SMB sales…"
          disabled={busy}
        />
        <button className="go" onClick={() => submit(question, false)} disabled={busy || !question.trim()}>
          Ask
        </button>
        <button className="go" onClick={() => question.trim() ? submit(question, true) : mic()} disabled={busy || hearing || thinking}>
          🔊 Ask + speak
        </button>
        {(serverStt || canListen()) && (
          hearing && serverStt ? (
            // Recording has no automatic endpoint detection, so stopping is the user's call.
            <button className="ghost recording" onClick={stopRecording}>
              ⏹ Stop &amp; send
            </button>
          ) : (
            <button className="ghost" onClick={mic} disabled={busy || hearing || thinking}>
              {thinking ? '… transcribing' : hearing ? '● listening' : '🎤 Speak'}
            </button>
          )
        )}
        {speaking && (
          <button
            className="ghost"
            onClick={() => {
              stopSpeaking()
              setSpeaking(false)
            }}
          >
            ⏹ Stop voice
          </button>
        )}
      </div>
      {sttErr && <p className="muted" style={{ color: '#f87171', margin: '4px 0' }}>⚠ Mic: {sttErr}</p>}
      {!turns.length && (
        <p className="muted center">
          MCEM stages · Azure migration · Copilot · deal registration · co-sell
        </p>
      )}
      {turns.map((t) => (
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

  useEffect(() => {
    getCapabilities().then(setCaps).catch(() => setCaps(null))
    return () => stopSpeaking()
  }, [])

  return (
    <div className="smbp">
      {/* logo · titles · status, then the rail's own controls pushed right — the head shape
          every rail shares (co-worker, terminal-fun, gemini-cx). The 🤝 is this rail's
          catalog.py icon, so the header matches the sidebar item that opened it. Chips sit
          UNDER the bold title; minWidth:0 lets the chip row wrap instead of forcing the
          header wider than its container. */}
      <header className="smbp-head">
        <span className="smbp-logo" aria-hidden="true">🤝</span>
        <div className="smbp-titles">
          <h1>SMB Partner Enablement</h1>
          <span className="smbp-sub">
            A two-minute meeting kit for Microsoft SMB partners — grounded only in curated
            SME material, every claim cited
          </span>
          <AiStatus caps={caps} />
        </div>
        <span className="smbp-spacer" />
        <AudioDevicePicker />
      </header>
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
        Partner Center SMB Readiness · rebuilt from the SME&amp;C Account Planning Hackathon
        prototype
      </footer>
    </div>
  )
}
