import { useCallback, useEffect, useRef, useState } from 'react'
import { askStream, getCapabilities } from './api'
import Builder from './builder'
import type { Capabilities, Turn } from './types'
import { canListen, listen, speak, stopSpeaking } from './voice'
import './theme.css'

type Tab = 'about' | 'builder' | 'voice'

const TABS: { id: Tab; icon: string; label: string }[] = [
  { id: 'about', icon: '💡', label: 'What This Is' },
  { id: 'builder', icon: '📋', label: 'Scenario Builder' },
  { id: 'voice', icon: '🎤', label: 'Voice Chat' },
]

function AiStatus({ caps }: { caps: Capabilities | null }) {
  if (!caps) return <span className="muted">checking…</span>
  if (!caps.broker_reachable) return <span className="muted">● broker unreachable</span>
  return (
    <div className="status">
      {caps.models.map((m) => (
        <span key={m.slot} title={m.role}>
          <i className={`dot ${m.resident ? 'on' : 'off'}`} />
          {m.slot === 'reasoning' ? 'LLM' : 'Retrieval'} ({m.model}){' '}
          <span className="muted">{m.resident ? 'GPU · ready' : 'cold'}</span>
        </span>
      ))}
      <span title={caps.voice.note}>
        <i className={`dot ${caps.voice.effective !== 'off' ? 'on' : 'off'}`} />
        Voice ({caps.voice.effective})
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
  const nextId = useRef(1)

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

  const mic = () => {
    if (hearing) return
    setHearing(true)
    listen({
      onResult: (transcript, final) => {
        setQuestion(transcript)
        if (final) submit(transcript, true)
      },
      onEnd: () => setHearing(false),
      onError: () => setHearing(false),
    })
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
        <button className="go" onClick={() => submit(question, true)} disabled={busy || !question.trim()}>
          🔊 Ask + speak
        </button>
        {canListen() && (
          <button className="ghost" onClick={mic} disabled={busy || hearing}>
            {hearing ? '● listening' : '🎤 Speak'}
          </button>
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
        <div className="mnotch" />
        <iframe src="/smb-partner-enablement/m/" title="Mobile preview" />
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
      <header>
        <h2 style={{ margin: 0 }}>SMB Partner Enablement</h2>
        <AiStatus caps={caps} />
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
