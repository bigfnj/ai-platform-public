import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { askStream, generatePackage, getCapabilities, getScenarios, speakText, transcribe } from '../src/api'
import type {
  AnalysisEvent,
  Capabilities,
  RetrievalEvent,
  Scenario,
  ScenarioPackage,
  Stage,
  StageState,
  Turn,
} from '../src/types'
import type { Recorder } from '../src/voice'
import { canListen, canRecord, listen, record, speak, stopSpeaking } from '../src/voice'

type Tab = 'about' | 'builder' | 'chat'

const TABS: { id: Tab; icon: string; label: string }[] = [
  { id: 'about', icon: '💡', label: 'This Is' },
  { id: 'builder', icon: '📋', label: 'Builder' },
  { id: 'chat', icon: '🎤', label: 'Chat' },
]

// ---------------------------------------------------------------------------
// Shared utilities (mirrors desktop builder.tsx — same logic, mobile layout)
// ---------------------------------------------------------------------------

function Markdown({ text }: { text: string }) {
  const blocks = useMemo(() => {
    const out: JSX.Element[] = []
    let list: string[] = []
    const flush = () => {
      if (!list.length) return
      out.push(
        <ul key={`u${out.length}`}>
          {list.map((li, i) => <li key={i}>{inline(li)}</li>)}
        </ul>,
      )
      list = []
    }
    for (const raw of (text || '').split('\n')) {
      const line = raw.trimEnd()
      if (!line.trim()) { flush(); continue }
      const heading = /^(#{2,4})\s+(.*)$/.exec(line)
      if (heading) {
        flush()
        const level = heading[1].length
        const content = inline(heading[2])
        out.push(level <= 2
          ? <h4 key={`h${out.length}`}>{content}</h4>
          : <h5 key={`h${out.length}`}>{content}</h5>)
        continue
      }
      const bullet = /^\s*[-*]\s+(.*)$/.exec(line)
      if (bullet) { list.push(bullet[1]); continue }
      flush()
      out.push(<p key={`p${out.length}`}>{inline(line)}</p>)
    }
    flush()
    return out
  }, [text])
  return <div className="md">{blocks}</div>
}

function inline(s: string): (string | JSX.Element)[] {
  return s.split(/(\*\*[^*]+\*\*)/g).filter(Boolean).map((part, i) =>
    part.startsWith('**') && part.endsWith('**')
      ? <strong key={i}>{part.slice(2, -2)}</strong>
      : part,
  )
}

const UNKNOWN = 'Not sure yet'


function ReadAloud({ text }: { text: string }) {
  const [on, setOn] = useState(false)
  const [loading, setLoading] = useState(false)
  useEffect(() => () => stopSpeaking(), [])
  if (!text) return null

  const toggle = async () => {
    if (on) { stopSpeaking(); setOn(false); return }
    if (loading) return
    setLoading(true)
    try {
      const payload = await speakText(text)
      setOn(true)
      speak(payload as any, () => setOn(false))
    } catch {
      setOn(true)
      speak({ mode: 'browser', text, lang: 'en-US' }, () => setOn(false))
    } finally {
      setLoading(false)
    }
  }

  return (
    <button className="ghost" style={{ marginTop: 8 }} onClick={toggle} disabled={loading}>
      {loading ? '…' : on ? '■ Stop' : '🔊 Read aloud'}
    </button>
  )
}

// ---------------------------------------------------------------------------
// Trace — collapsible on mobile (stacked, not side-by-side)
// ---------------------------------------------------------------------------

function Trace({
  analysis, retrievals, live, activeKey, stages,
}: {
  analysis: AnalysisEvent | null
  retrievals: RetrievalEvent[]
  live: Record<string, string>
  activeKey: string | null
  stages: Stage[]
}) {
  const [open, setOpen] = useState(false)
  const endRef = useRef<HTMLDivElement | null>(null)
  const label = (key: string) => stages.find((s) => s.key === key)?.label.replace(/…$/, '') ?? key

  useEffect(() => {
    if (open) endRef.current?.scrollIntoView({ block: 'nearest' })
  }, [retrievals.length, activeKey, live, open])

  return (
    <div className="trace-wrap">
      <button className="trace-toggle ghost" onClick={() => setOpen((o) => !o)}>
        {open ? '▲ Hide reasoning' : '▼ Show live reasoning'}
      </button>
      {open && (
        <div className="trace" aria-live="polite">
          <div className="tracehead">Live reasoning</div>
          {analysis && (
            <div className="tblock">
              <div className="tkey">Diagnostic analysed</div>
              <div className="tline">
                {analysis.known} answer{analysis.known === 1 ? '' : 's'} given
                {analysis.unknown.length > 0 && `, ${analysis.unknown.length} left open`}
              </div>
              {analysis.unknown.map((q) => <div key={q} className="tline open">open: {q}</div>)}
              {analysis.constraints.length === 0
                ? <div className="tline">No hard eligibility limits triggered.</div>
                : analysis.constraints.map((c, i) => <div key={i} className="tline rule">rule: {c}</div>)
              }
            </div>
          )}
          {retrievals.map((r) => (
            <div className="tblock" key={r.key}>
              <div className="tkey">{label(r.key)}</div>
              {r.hits.length === 0
                ? <div className="tline open">nothing retrieved — this pass is ungrounded</div>
                : r.hits.slice(0, 3).map((h, i) => (
                    <div key={i} className="tline">
                      <span className="tscore">{h.score.toFixed(2)}</span>
                      {h.title || h.source}
                      <span className="tsrc"> {h.collection}</span>
                    </div>
                  ))
              }
              {live[r.key] && <div className="tstream">{live[r.key].slice(-400)}</div>}
            </div>
          ))}
          {!analysis && <div className="tline">Waiting for the first pass…</div>}
          <div ref={endRef} />
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Builder — full diagnostic → generation → package flow
// ---------------------------------------------------------------------------

type BuildStep = 'pick' | 'ask' | 'run' | 'done'

function Builder({ scenarios }: { scenarios: Scenario[] | null }) {
  const [step, setStep] = useState<BuildStep>('pick')
  const [picked, setPicked] = useState<string | null>(null)
  const [qIndex, setQIndex] = useState(0)
  const [answers, setAnswers] = useState<Record<string, string>>({})
  const [stageState, setStageState] = useState<Record<string, StageState>>({})
  const [pkg, setPkg] = useState<ScenarioPackage | null>(null)
  const [runError, setRunError] = useState<string | null>(null)
  const [analysis, setAnalysis] = useState<AnalysisEvent | null>(null)
  const [retrievals, setRetrievals] = useState<RetrievalEvent[]>([])
  const [live, setLive] = useState<Record<string, string>>({})
  const [activeKey, setActiveKey] = useState<string | null>(null)
  const [outTab, setOutTab] = useState('scenario_card')
  const cancelRef = useRef<(() => void) | null>(null)
  const topRef = useRef<HTMLDivElement | null>(null)

  const scenario = scenarios?.find((s) => s.id === picked) ?? null
  const stages: Stage[] = scenario?.stages ?? []
  const tabs = scenario?.tabs ?? []

  const reset = () => {
    cancelRef.current?.()
    cancelRef.current = null
    stopSpeaking()
    setStep('pick'); setPicked(null); setQIndex(0); setAnswers({})
    setStageState({}); setPkg(null); setRunError(null); setOutTab('scenario_card')
    setAnalysis(null); setRetrievals([]); setLive({}); setActiveKey(null)
  }

  // Scroll to top of builder pane whenever the step changes.
  useEffect(() => { topRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }) }, [step])
  useEffect(() => () => { cancelRef.current?.() }, [])

  const start = (id: string) => { setPicked(id); setQIndex(0); setAnswers({}); setStep('ask') }

  const choose = (questionId: string, option: string) => {
    const next = { ...answers, [questionId]: option }
    setAnswers(next)
    if (!scenario) return
    if (qIndex + 1 < scenario.questions.length) { setQIndex(qIndex + 1); return }
    run(scenario.id, next)
  }

  const run = (scenarioId: string, finalAnswers: Record<string, string>) => {
    setStep('run')
    setRunError(null)
    setStageState(Object.fromEntries(stages.map((s) => [s.key, 'pending' as StageState])))
    setAnalysis(null); setRetrievals([]); setLive({}); setActiveKey(null)
    cancelRef.current = generatePackage(
      { scenario_id: scenarioId, answers: finalAnswers },
      {
        onStage: (e) => {
          setStageState((prev) => ({ ...prev, [e.key]: e.state }))
          if (e.state === 'active') setActiveKey(e.key)
        },
        onAnalysis: (e) => setAnalysis(e),
        onRetrieval: (e) => setRetrievals((prev) => [...prev, e]),
        onToken: (key, token) =>
          setLive((prev) => ({ ...prev, [key]: (prev[key] ?? '') + token })),
        onPackage: (p) => {
          setPkg(p)
          const first = (scenarios?.find((x) => x.id === scenarioId)?.tabs ?? [])
            .find((t) => (p.outputs[t.key] || '').trim())
          setOutTab(first?.key ?? 'scenario_card')
          setStep('done')
        },
        onError: (detail) => { setRunError(detail); setStep('done') },
      },
    )
  }

  if (!scenarios) return <p className="muted" style={{ padding: 16 }}>Loading scenarios…</p>

  // Step 1: pick ------------------------------------------------------------------
  if (step === 'pick' || !scenario) {
    return (
      <div className="bpad" ref={topRef}>
        <h2 style={{ margin: '0 0 4px' }}>Tell me about your customer</h2>
        <p className="muted" style={{ margin: '0 0 16px', fontSize: 13 }}>
          What industry? I'll generate a grounded pre-call package in about 20 seconds.
        </p>
        {scenarios.map((s) => (
          <button key={s.id} className="scard" style={{ width: '100%', textAlign: 'left', cursor: 'pointer', background: 'var(--s2)', border: '1px solid var(--bd)', borderRadius: 12, display: 'flex', gap: 12, alignItems: 'flex-start' }} onClick={() => start(s.id)}>
            <div className="icon" style={{ fontSize: 24, flexShrink: 0 }}>{s.icon}</div>
            <div>
              <div className="title" style={{ fontWeight: 600 }}>{s.title}</div>
              <div className="fit" style={{ color: 'var(--accent)', fontSize: 12, margin: '2px 0 6px' }}>{s.fit}</div>
              <p className="muted" style={{ margin: 0, fontSize: 13 }}>{s.situation}</p>
            </div>
          </button>
        ))}
      </div>
    )
  }

  // Step 2: question flow ---------------------------------------------------------
  if (step === 'ask') {
    const q = scenario.questions[qIndex]
    const total = scenario.questions.length
    return (
      <div className="bpad" ref={topRef}>
        <div className="qhead">
          <button className="ghost" onClick={reset}>‹ Back</button>
          <span className="muted" style={{ fontSize: 13 }}>{qIndex + 1} / {total}</span>
        </div>
        <div className="qbar"><span style={{ width: `${((qIndex + 1) / total) * 100}%` }} /></div>
        <h3 style={{ margin: '0 0 8px', fontSize: 16 }}>{q.prompt}</h3>
        <p className="why">{q.why}</p>
        <div className="opts">
          {q.options.filter((o) => o !== UNKNOWN).map((opt) => (
            <button key={opt} className="opt" onClick={() => choose(q.id, opt)}>{opt}</button>
          ))}
        </div>
        {q.options.includes(UNKNOWN) && (
          <button className="opt unsure" onClick={() => choose(q.id, UNKNOWN)}>
            {UNKNOWN} — ask this on the call
          </button>
        )}
        {qIndex > 0 && (
          <button className="ghost" style={{ marginTop: 12 }} onClick={() => setQIndex(qIndex - 1)}>
            ‹ Previous question
          </button>
        )}
      </div>
    )
  }

  // Step 3: generation ------------------------------------------------------------
  if (step === 'run') {
    return (
      <div className="bpad" ref={topRef}>
        <h3 style={{ margin: '0 0 12px', fontSize: 16 }}>{scenario.icon} Building your package…</h3>
        <ul className="checklist">
          {stages.map((s) => {
            const st = stageState[s.key] ?? 'pending'
            return (
              <li key={s.key} className={`ck ${st}`}>
                <span className="ckmark">
                  {st === 'done' ? '✓' : st === 'error' ? '!' : st === 'active' ? '◍' : '○'}
                </span>
                {s.label}
              </li>
            )
          })}
        </ul>
        <p className="muted" style={{ fontSize: 12, marginBottom: 12 }}>
          Each line is a separate grounded pass over the knowledge base.
        </p>
        <Trace
          analysis={analysis}
          retrievals={retrievals}
          live={live}
          activeKey={activeKey}
          stages={stages}
        />
      </div>
    )
  }

  // Step 4: package ---------------------------------------------------------------
  const nextMove = pkg?.outputs.next_move?.trim() || ''
  const body = pkg?.outputs[outTab]?.trim() || ''
  const cites = pkg?.citations[outTab] ?? []
  const suppressed = pkg?.suppressed?.[outTab] ?? 0
  const openQs = (pkg?.answers ?? []).filter((a) => a.answer === UNKNOWN)

  return (
    <div className="bpad" ref={topRef}>
      <div className="qhead">
        <button className="ghost" onClick={reset}>‹ New diagnostic</button>
        <span className="muted" style={{ fontSize: 13 }}>{scenario.icon} {scenario.title}</span>
      </div>

      {runError && <p className="ungrounded">Generation failed: {runError}</p>}

      {openQs.length > 0 && (
        <p className="muted" style={{ fontSize: 12 }}>
          {openQs.length} question{openQs.length === 1 ? '' : 's'} left open — nothing assumes an
          answer here; {openQs.length === 1 ? 'it appears' : 'they appear'} first in the Discovery
          Playbook instead.
        </p>
      )}

      {pkg && !pkg.grounded && (
        <p className="ungrounded">
          Nothing was retrieved from the knowledge base — treat this as a prompt, not guidance.
        </p>
      )}

      {nextMove && (
        <div className="nextmove">
          <Markdown text={nextMove} />
          <ReadAloud text={nextMove} />
        </div>
      )}

      <div className="ptabs" role="tablist">
        {tabs.map((t) => {
          const empty = !(pkg?.outputs[t.key] || '').trim()
          return (
            <button
              key={t.key}
              role="tab"
              aria-selected={outTab === t.key}
              className={`ptab${outTab === t.key ? ' sel' : ''}${empty ? ' dis' : ''}`}
              disabled={empty}
              onClick={() => { stopSpeaking(); setOutTab(t.key) }}
            >
              {t.label}
            </button>
          )
        })}
      </div>

      {body
        ? <><Markdown text={body} /><ReadAloud text={body} /></>
        : <p className="muted">This section produced no output.</p>}

      {suppressed > 0 && (
        <p className="muted" style={{ fontSize: 12 }}>
          {suppressed} statement{suppressed === 1 ? '' : 's'} withheld — {suppressed === 1 ? 'it' : 'they'}{' '}
          contained figures not in the source material.
        </p>
      )}

      {cites.length > 0 && (
        <div className="cites">
          {cites.map((c, i) => (
            <div key={i} className="cite">{c.title || c.source} · {c.collection}</div>
          ))}
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Chat
// ---------------------------------------------------------------------------

function Chat({ caps, onSpeakChange }: { caps: Capabilities | null; onSpeakChange: (b: boolean) => void }) {
  const [turns, setTurns] = useState<Turn[]>([])
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState(false)
  const [hearing, setHearing] = useState(false)
  const [thinking, setThinking] = useState(false)

  const setSpeak = onSpeakChange
  const [err, setErr] = useState('')
  const nextId = useRef(1)
  const tail = useRef<HTMLDivElement>(null)
  const recorder = useRef<Recorder | null>(null)

  // Leaving the chat tab mid-recording must release the mic.
  useEffect(() => () => { recorder.current?.cancel(); recorder.current = null }, [])

  useEffect(() => { tail.current?.scrollIntoView({ behavior: 'smooth' }) }, [turns])

  const submit = useCallback(
    (text: string) => {
      const q = text.trim()
      if (!q || busy) return
      const id = nextId.current++
      setTurns((t) => [...t, { id, question: q, answer: '', citations: [], grounded: false, streaming: true }])
      setDraft('')
      setErr('')
      setBusy(true)
      const patch = (fn: (t: Turn) => Turn) => setTurns((all) => all.map((t) => (t.id === id ? fn(t) : t)))
      askStream(
        { question: q, speak: true },
        {
          onCitations: (citations, grounded) => patch((t) => ({ ...t, citations, grounded })),
          onToken: (tok) => patch((t) => ({ ...t, answer: t.answer + tok })),
          onDone: ({ answer, voice }) => {
            patch((t) => ({ ...t, answer, streaming: false }))
            setBusy(false)
            if (voice) { setSpeak(true); speak(voice, () => setSpeak(false)) }
          },
          onError: (detail) => { patch((t) => ({ ...t, streaming: false, error: detail })); setBusy(false) },
        },
      )
    },
    [busy],
  )

  // Server-side Whisper when the broker's media worker is up: it keeps the audio on the
  // platform instead of Google's servers, and it works in browsers with no Web Speech at all.
  const serverStt = caps?.voice?.stt === 'broker' && canRecord()

  const mic = async () => {
    if (hearing || busy || thinking) return
    stopSpeaking(); setSpeak(false); setErr('')
    if (!serverStt) {
      setHearing(true)
      const started = listen({
        onResult: (transcript, final) => { setDraft(transcript); if (final) submit(transcript) },
        onEnd: () => setHearing(false),
        onError: (detail) => { setHearing(false); setErr(detail) },
      })
      if (!started) setHearing(false)
      return
    }
    if (recorder.current) { recorder.current.stop(); recorder.current = null; return }
    recorder.current = await record({
      onStart: () => setHearing(true),
      onError: (detail) => { setHearing(false); recorder.current = null; setErr(detail) },
      onReady: async (audio_b64, suffix) => {
        setHearing(false)
        recorder.current = null
        setThinking(true)
        try {
          const { text } = await transcribe(audio_b64, suffix)
          if (!text) { setErr('nothing was heard — try again'); return }
          setDraft(text)
          submit(text)
        } catch (e: any) {
          setErr(String(e?.message || 'transcription failed'))
        } finally {
          setThinking(false)
        }
      },
    })
  }

  // 'ok' | 'unreachable' under the shared chip envelope (was a boolean broker_reachable).
  // The mobile surface deliberately keeps a single binary "Live" badge rather than the
  // desktop's four-state chips: on a phone the actionable question is only whether the
  // assistant will answer, and an `ollama pull` is not something you do from here.
  const live = caps?.broker === 'ok'
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
                  {t.citations.map((c) => <div key={c.n}>[{c.n}] {c.collection} / {c.source}</div>)}
                </div>
              )}
            </div>
          </div>
        ))}
        <div ref={tail} />
      </div>
      {err && <div className="warn pad">{err}</div>}
{/* stop button is now in the App header, controlled by onSpeakChange */}
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
          onClick={(serverStt || canListen()) ? mic : () => submit(draft)}
          disabled={busy || thinking}
          aria-label={hearing ? 'Stop and send' : (serverStt || canListen()) ? 'Tap to speak' : 'Send'}
        >
          {hearing ? '⏹' : (serverStt || canListen()) ? '🎤' : '➤'}
        </button>
      </div>
      <p className="hint">
        {busy ? 'Working…'
          : thinking ? 'Transcribing…'
          : hearing ? (serverStt ? 'Recording — tap ⏹ when done' : 'Listening…')
          : (serverStt || canListen()) ? 'Tap to speak' : 'Tap to send'}
      </p>
    </>
  )
}

// ---------------------------------------------------------------------------
// App root
// ---------------------------------------------------------------------------

export default function App() {
  const [tab, setTab] = useState<Tab>('chat')
  const [caps, setCaps] = useState<Capabilities | null>(null)
  const [scenarios, setScenarios] = useState<Scenario[] | null>(null)
  const [speaking, setSpeaking] = useState(false)

  useEffect(() => {
    getCapabilities().then(setCaps).catch(() => setCaps(null))
    getScenarios().then((r) => setScenarios(r.scenarios)).catch(() => setScenarios([]))
    return () => stopSpeaking()
  }, [])

  return (
    <div className="m">
      <header className="mtop">
        <span className="brand">
          <span className="logo" aria-hidden />
          Partner Center
        </span>
        {speaking && (
          <button
            className="stop-audio-btn"
            onClick={() => { stopSpeaking(); setSpeaking(false) }}
            aria-label="Stop audio"
          >
            ⏹ Stop
          </button>
        )}
        <span className="avatar">{(caps?.user ?? '?').slice(0, 1).toUpperCase()}</span>
      </header>

      <nav className="mtabs" role="tablist">
        {TABS.map((t) => (
          <button key={t.id} role="tab" aria-selected={tab === t.id} onClick={() => setTab(t.id)}>
            {t.icon} {t.label}
          </button>
        ))}
      </nav>

      <main className="mbody">
        {tab === 'chat' && <Chat caps={caps} onSpeakChange={setSpeaking} />}
        {tab === 'about' && (
          <div className="pad">
            <h2>What is this?</h2>
            <p className="muted">
              A two-minute AI coach that turns what you know about your next customer into a
              complete, Microsoft-specific battle plan for the meeting.
            </p>
            <p className="muted">
              Pick an industry, answer a short diagnostic, and get back what to ask, what
              products fit, how to handle objections, and what to do in Partner Center after.
            </p>
          </div>
        )}
        {tab === 'builder' && (
          <div className="bscroll">
            <Builder scenarios={scenarios} />
          </div>
        )}
      </main>
    </div>
  )
}
