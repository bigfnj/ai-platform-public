import { useCallback, useEffect, useRef, useState } from 'react'
import { askStream, getCapabilities, getScenarios } from '../src/api'
import type { Capabilities, Scenario, Turn } from '../src/types'
import { canListen, listen, speak, stopSpeaking } from '../src/voice'

type Tab = 'about' | 'builder' | 'chat'

/** Short labels: the desktop names do not fit three-up on a 390px viewport. */
const TABS: { id: Tab; icon: string; label: string }[] = [
  { id: 'about', icon: '💡', label: 'This Is' },
  { id: 'builder', icon: '📋', label: 'Builder' },
  { id: 'chat', icon: '🎤', label: 'Chat' },
]

function Chat({ caps }: { caps: Capabilities | null }) {
  const [turns, setTurns] = useState<Turn[]>([])
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState(false)
  const [speaking, setSpeaking] = useState(false)
  const [hearing, setHearing] = useState(false)
  const [err, setErr] = useState('')
  const nextId = useRef(1)
  const tail = useRef<HTMLDivElement>(null)

  useEffect(() => {
    tail.current?.scrollIntoView({ behavior: 'smooth' })
  }, [turns])

  const submit = useCallback(
    (text: string) => {
      const q = text.trim()
      if (!q || busy) return
      const id = nextId.current++
      setTurns((t) => [
        ...t,
        { id, question: q, answer: '', citations: [], grounded: false, streaming: true },
      ])
      setDraft('')
      setErr('')
      setBusy(true)
      const patch = (fn: (t: Turn) => Turn) =>
        setTurns((all) => all.map((t) => (t.id === id ? fn(t) : t)))

      // speak:true — on a phone this is the whole point, so every turn is a spoken turn.
      askStream(
        { question: q, speak: true },
        {
          onCitations: (citations, grounded) => patch((t) => ({ ...t, citations, grounded })),
          onToken: (tok) => patch((t) => ({ ...t, answer: t.answer + tok })),
          onDone: ({ answer, voice }) => {
            patch((t) => ({ ...t, answer, streaming: false }))
            setBusy(false)
            if (voice) {
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
    if (hearing || busy) return
    stopSpeaking()
    setSpeaking(false)
    setHearing(true)
    const started = listen({
      onResult: (transcript, final) => {
        setDraft(transcript)
        if (final) submit(transcript)
      },
      onEnd: () => setHearing(false),
      onError: (detail) => {
        setHearing(false)
        setErr(detail)
      },
    })
    if (!started) setHearing(false)
  }

  const live = caps?.broker_reachable
  const engine = caps?.models.find((m) => m.slot === 'reasoning')

  return (
    <>
      <div className="chead">
        <h2>Voice Assistant</h2>
        {live && (
          <span className="badge">
            <i className="dot on" /> Live · {engine?.model ?? 'Ollama'}
          </span>
        )}
      </div>

      <div className="thread">
        {!turns.length && (
          <div className="empty">
            <div className="glyph">🎤</div>
            <h3>Ask anything about Microsoft SMB sales</h3>
            <p>MCEM stages · Azure migration · Copilot · deal registration · co-sell</p>
          </div>
        )}
        {turns.map((t) => (
          <div key={t.id}>
            <div className="bubble me">{t.question}</div>
            <div className="bubble bot">
              {t.answer || (t.streaming ? <span className="muted">thinking…</span> : null)}
              {t.streaming && t.answer && <span className="muted"> ▍</span>}
              {t.error && <div className="warn">⚠ {t.error}</div>}
              {!t.streaming && !t.grounded && !t.error && (
                <div className="warn">Not grounded in the knowledge base.</div>
              )}
              {t.citations.length > 0 && (
                <div className="cites">
                  {t.citations.map((c) => (
                    <div key={c.n}>
                      [{c.n}] {c.collection} / {c.source}
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        ))}
        <div ref={tail} />
      </div>

      {err && <div className="warn pad">{err}</div>}

      {speaking && (
        <button
          className="stopvoice"
          onClick={() => {
            stopSpeaking()
            setSpeaking(false)
          }}
        >
          ⏹ Stop voice
        </button>
      )}

      <div className="composer">
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && submit(draft)}
          placeholder="Type or tap the mic…"
          disabled={busy}
        />
        <button
          className={`mic ${hearing ? 'hearing' : ''}`}
          onClick={canListen() ? mic : () => submit(draft)}
          disabled={busy}
          aria-label={canListen() ? 'Tap to speak' : 'Send'}
        >
          {canListen() ? '🎤' : '➤'}
        </button>
      </div>
      <p className="hint">
        {busy ? 'Working…' : hearing ? 'Listening…' : canListen() ? 'Tap to speak' : 'Tap to send'}
      </p>
    </>
  )
}

export default function App() {
  const [tab, setTab] = useState<Tab>('chat')
  const [caps, setCaps] = useState<Capabilities | null>(null)
  const [scenarios, setScenarios] = useState<Scenario[]>([])

  useEffect(() => {
    getCapabilities().then(setCaps).catch(() => setCaps(null))
    return () => stopSpeaking()
  }, [])

  // Scenarios come from the backend so both surfaces stay in step with the generator.
  useEffect(() => {
    getScenarios()
      .then((r) => setScenarios(r.scenarios))
      .catch(() => setScenarios([]))
  }, [])

  return (
    <div className="m">
      <header className="mtop">
        <span className="brand">
          <span className="logo" aria-hidden />
          Partner Center
        </span>
        <span className="avatar">{(caps?.user ?? '?').slice(0, 1).toUpperCase()}</span>
      </header>

      <nav className="mtabs" role="tablist">
        {TABS.map((t) => (
          <button
            key={t.id}
            role="tab"
            aria-selected={tab === t.id}
            onClick={() => setTab(t.id)}
          >
            {t.icon} {t.label}
          </button>
        ))}
      </nav>

      <main className="mbody">
        {tab === 'chat' && <Chat caps={caps} />}
        {tab === 'about' && (
          <div className="pad">
            <h2>What is this?</h2>
            <p className="muted">
              A two-minute AI coach that turns what you know about your next customer into a
              complete, Microsoft-specific battle plan for the meeting.
            </p>
            <p className="muted">
              Pick an industry, answer four quick questions, and get back what to ask, what
              products fit, how to handle objections, and what to do in Partner Center after.
            </p>
          </div>
        )}
        {tab === 'builder' && (
          <div className="pad">
            <h2>Tell me about your customer</h2>
            {scenarios.map((s) => (
              <div className="scard" key={s.id}>
                <div className="icon">{s.icon}</div>
                <div>
                  <div className="title">{s.title}</div>
                  <div className="fit">{s.fit}</div>
                  <p className="muted">{s.situation}</p>
                </div>
              </div>
            ))}
            <p className="muted">
              {scenarios.length === 0
                ? 'Loading scenarios…'
                : 'The four-question diagnostic lands here once the desktop flow settles — ask in Chat in the meantime.'}
            </p>
          </div>
        )}
      </main>
    </div>
  )
}
