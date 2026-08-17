import { useEffect, useMemo, useRef, useState } from 'react'
import { generatePackage, getScenarios } from './api'
import { canSpeak, speak, stopSpeaking } from './voice'
import type {
  AnalysisEvent,
  RetrievalEvent,
  Scenario,
  ScenarioPackage,
  Stage,
  StageState,
} from './types'

/**
 * Minimal markdown renderer for the subset the generator emits: `## headings`, `**bold**`,
 * `- bullets`, and paragraphs.
 *
 * Deliberately element-based rather than `dangerouslySetInnerHTML`. This renders model output,
 * which is untrusted text — HTML injection through a generated answer would be a real hole, and
 * the rail otherwise has no runtime dependencies to justify pulling in a markdown library.
 */
function Markdown({ text }: { text: string }) {
  const blocks = useMemo(() => {
    const out: JSX.Element[] = []
    let list: string[] = []
    const flush = () => {
      if (!list.length) return
      out.push(
        <ul key={`u${out.length}`}>
          {list.map((li, i) => (
            <li key={i}>{inline(li)}</li>
          ))}
        </ul>,
      )
      list = []
    }
    for (const raw of (text || '').split('\n')) {
      const line = raw.trimEnd()
      if (!line.trim()) {
        flush()
        continue
      }
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
      if (bullet) {
        list.push(bullet[1])
        continue
      }
      flush()
      out.push(<p key={`p${out.length}`}>{inline(line)}</p>)
    }
    flush()
    return out
  }, [text])
  return <div className="md">{blocks}</div>
}

/** `**bold**` only — the generator does not emit nested emphasis. */
function inline(s: string): (string | JSX.Element)[] {
  return s.split(/(\*\*[^*]+\*\*)/g).filter(Boolean).map((part, i) =>
    part.startsWith('**') && part.endsWith('**')
      ? <strong key={i}>{part.slice(2, -2)}</strong>
      : part,
  )
}

/** Mirrors `UNKNOWN_LABEL` in scenarios.py. */
const UNKNOWN = 'Not sure yet'

function ReadAloud({ text }: { text: string }) {
  const [on, setOn] = useState(false)
  useEffect(() => () => stopSpeaking(), [])
  if (!canSpeak() || !text) return null
  return (
    <button
      className="ghost"
      onClick={() => {
        if (on) {
          stopSpeaking()
          setOn(false)
          return
        }
        setOn(true)
        speak({ mode: 'browser', text, lang: 'en-US' }, () => setOn(false))
      }}
    >
      {on ? '■ Stop' : '🔊 Read aloud'}
    </button>
  )
}

/**
 * The live reasoning trace shown beside the checklist.
 *
 * Everything here is real telemetry from the generation passes, not narration: the hard
 * constraints came from a deterministic rule table, the retrievals are the actual chunks each
 * pass is standing on with their cosine scores, and the text is the model's own output streaming
 * in. Showing it is the point — a partner (or an audience) can see the tool reasoning over
 * sourced Microsoft material rather than being asked to trust a spinner.
 */
function Trace({
  analysis,
  retrievals,
  live,
  activeKey,
  stages,
}: {
  analysis: AnalysisEvent | null
  retrievals: RetrievalEvent[]
  live: Record<string, string>
  activeKey: string | null
  stages: Stage[]
}) {
  const endRef = useRef<HTMLDivElement | null>(null)
  const label = (key: string) =>
    stages.find((s) => s.key === key)?.label.replace(/…$/, '') ?? key

  // Follow the tail as new reasoning arrives, which is what makes it read as live.
  useEffect(() => {
    endRef.current?.scrollIntoView({ block: 'nearest' })
  }, [retrievals.length, activeKey, live])

  return (
    <div className="trace" aria-live="polite" aria-label="Live reasoning">
      <div className="tracehead">Live reasoning</div>

      {analysis && (
        <div className="tblock">
          <div className="tkey">Diagnostic analysed</div>
          <div className="tline">
            {analysis.known} answer{analysis.known === 1 ? '' : 's'} given
            {analysis.unknown.length > 0 && `, ${analysis.unknown.length} left open`}
          </div>
          {analysis.unknown.map((q) => (
            <div key={q} className="tline open">open: {q}</div>
          ))}
          {analysis.constraints.length === 0 ? (
            <div className="tline">No hard eligibility limits triggered.</div>
          ) : (
            analysis.constraints.map((c, i) => (
              // Shown in full: these are the rules the answer is not allowed to violate, and
              // they are the most defensible thing the rail produces.
              <div key={i} className="tline rule">rule: {c}</div>
            ))
          )}
        </div>
      )}

      {retrievals.map((r) => (
        <div className="tblock" key={r.key}>
          <div className="tkey">{label(r.key)}</div>
          {r.hits.length === 0 ? (
            <div className="tline open">nothing retrieved — this pass is ungrounded</div>
          ) : (
            r.hits.slice(0, 4).map((h, i) => (
              <div key={i} className="tline">
                <span className="tscore">{h.score.toFixed(2)}</span>
                {h.title || h.source}
                <span className="tsrc">{h.collection}</span>
              </div>
            ))
          )}
          {live[r.key] && (
            <div className="tstream">{live[r.key].slice(-600)}</div>
          )}
        </div>
      ))}

      {!analysis && <div className="tline">Waiting for the first pass…</div>}
      <div ref={endRef} />
    </div>
  )
}

type Step = 'pick' | 'ask' | 'run' | 'done'

export default function Builder() {
  const [scenarios, setScenarios] = useState<Scenario[] | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)

  const [step, setStep] = useState<Step>('pick')
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

  useEffect(() => {
    getScenarios()
      .then((r) => setScenarios(r.scenarios))
      .catch((e) => setLoadError(String(e.message || e)))
    return () => cancelRef.current?.()
  }, [])

  const scenario = scenarios?.find((s) => s.id === picked) ?? null
  const stages: Stage[] = scenario?.stages ?? []
  const tabs = scenario?.tabs ?? []

  const reset = () => {
    cancelRef.current?.()
    cancelRef.current = null
    stopSpeaking()
    setStep('pick')
    setPicked(null)
    setQIndex(0)
    setAnswers({})
    setStageState({})
    setPkg(null)
    setRunError(null)
    setOutTab('scenario_card')
    setAnalysis(null)
    setRetrievals([])
    setLive({})
    setActiveKey(null)
  }

  const start = (scenarioId: string) => {
    setPicked(scenarioId)
    setQIndex(0)
    setAnswers({})
    setStep('ask')
  }

  const choose = (questionId: string, option: string) => {
    const next = { ...answers, [questionId]: option }
    setAnswers(next)
    if (!scenario) return
    if (qIndex + 1 < scenario.questions.length) {
      setQIndex(qIndex + 1)
      return
    }
    run(scenario.id, next)
  }

  const run = (scenarioId: string, finalAnswers: Record<string, string>) => {
    setStep('run')
    setRunError(null)
    setStageState(Object.fromEntries(stages.map((s) => [s.key, 'pending' as StageState])))
    setAnalysis(null)
    setRetrievals([])
    setLive({})
    setActiveKey(null)
    cancelRef.current = generatePackage(
      { scenario_id: scenarioId, answers: finalAnswers },
      {
        onStage: (e) => {
          setStageState((prev) => ({ ...prev, [e.key]: e.state }))
          if (e.state === 'active') setActiveKey(e.key)
        },
        onAnalysis: (e) => setAnalysis(e),
        onRetrieval: (e) => setRetrievals((prev) => [...prev, e]),
        // Appended per pass rather than one buffer, so the trace shows which pass produced what.
        onToken: (key, token) =>
          setLive((prev) => ({ ...prev, [key]: (prev[key] ?? '') + token })),
        onPackage: (p) => {
          setPkg(p)
          // Land on the first tab that actually produced content — a failed pass should not
          // greet the partner with an empty panel.
          const first = (scenarios?.find((x) => x.id === scenarioId)?.tabs ?? [])
            .find((t) => (p.outputs[t.key] || '').trim())
          setOutTab(first?.key ?? 'scenario_card')
          setStep('done')
        },
        onError: (detail) => {
          setRunError(detail)
          setStep('done')
        },
      },
    )
  }

  if (loadError) {
    return (
      <div className="card">
        <h3>Scenario Builder unavailable</h3>
        <p className="muted">Could not load scenarios: {loadError}</p>
      </div>
    )
  }
  if (!scenarios) return <p className="muted center">Loading scenarios…</p>

  // --- Step 1: pick a scenario ---------------------------------------------
  if (step === 'pick' || !scenario) {
    return (
      <>
        <div className="center">
          <h2 style={{ margin: '8px 0' }}>Tell me about your customer</h2>
          <p className="muted">What industry are they in and what's driving this conversation?</p>
        </div>
        <div className="grid">
          {scenarios.map((s) => (
            <button key={s.id} className="card scenario" onClick={() => start(s.id)}>
              <div className="icon">{s.icon}</div>
              <div className="title">{s.title}</div>
              <div className="fit">{s.fit}</div>
              <p className="muted">{s.situation}</p>
            </button>
          ))}
        </div>
        <p className="muted center" style={{ fontSize: 12 }}>
          {/* Question count varies by industry — depth follows the Microsoft surface area rather
              than a fixed number, so the copy reads it off the data. */}
          A short diagnostic, then a grounded pre-call package. Around twenty seconds.
        </p>
      </>
    )
  }

  // --- Step 2: the four-question diagnostic --------------------------------
  if (step === 'ask') {
    const q = scenario.questions[qIndex]
    const total = scenario.questions.length
    return (
      <div className="card">
        <div className="qhead">
          <button className="ghost" onClick={reset}>‹ Start over</button>
          <span className="muted">Question {qIndex + 1} of {total}</span>
        </div>
        <div className="qbar" role="progressbar" aria-valuenow={qIndex + 1} aria-valuemin={1}
             aria-valuemax={total}>
          <span style={{ width: `${((qIndex + 1) / total) * 100}%` }} />
        </div>
        <h3 style={{ marginBottom: 4 }}>{q.prompt}</h3>
        <p className="why"><strong>Why this matters</strong> — {q.why}</p>
        <div className="opts">
          {q.options.filter((o) => o !== UNKNOWN).map((opt) => (
            <button key={opt} className="opt" onClick={() => choose(q.id, opt)}>
              {opt}
            </button>
          ))}
        </div>
        {/* Set apart from the real answers: it is an honest out, not a fifth option of equal
            standing. Choosing it sends the question to the Discovery Playbook. */}
        {q.options.includes(UNKNOWN) && (
          <button className="opt unsure" onClick={() => choose(q.id, UNKNOWN)}>
            {UNKNOWN} — ask this on the call
          </button>
        )}
        {qIndex > 0 && (
          <button className="ghost" onClick={() => setQIndex(qIndex - 1)}>‹ Previous question</button>
        )}
      </div>
    )
  }

  // --- Step 3: generation, with an honest checklist and a live trace -------
  if (step === 'run') {
    return (
      <div className="card">
        <h3>{scenario.icon} Building your pre-call package</h3>
        <div className="runsplit">
          <div>
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
            <p className="muted" style={{ fontSize: 12 }}>
              Each line is a separate grounded pass over the knowledge base, not a progress
              animation.
            </p>
          </div>
          <Trace
            analysis={analysis}
            retrievals={retrievals}
            live={live}
            activeKey={activeKey}
            stages={stages}
          />
        </div>
      </div>
    )
  }

  // --- Step 4: the package -------------------------------------------------
  const nextMove = pkg?.outputs.next_move?.trim() || ''
  const body = pkg?.outputs[outTab]?.trim() || ''
  const cites = pkg?.citations[outTab] ?? []
  const suppressed = pkg?.suppressed?.[outTab] ?? 0
  const openQuestions = (pkg?.answers ?? []).filter((a) => a.answer === UNKNOWN)

  return (
    <div className="card">
      <div className="qhead">
        <button className="ghost" onClick={reset}>‹ New diagnostic</button>
        <span className="muted">{scenario.icon} {scenario.title}</span>
      </div>

      {runError && <p className="ungrounded">Generation failed: {runError}</p>}

      {openQuestions.length > 0 && (
        <p className="muted" style={{ fontSize: 12 }}>
          You left {openQuestions.length} question{openQuestions.length === 1 ? '' : 's'} open, so
          nothing here assumes an answer — {openQuestions.length === 1 ? 'it appears' : 'they appear'}{' '}
          first in the Discovery Playbook instead.
        </p>
      )}

      {pkg && !pkg.grounded && (
        <p className="ungrounded">
          Nothing was retrieved from the knowledge base for this scenario, so this package is not
          grounded. Treat it as a prompt for your own thinking, not as guidance.
        </p>
      )}

      {nextMove && (
        <div className="nextmove">
          <Markdown text={nextMove} />
          <ReadAloud text={nextMove} />
        </div>
      )}

      <div className="tabs" role="tablist">
        {tabs.map((t) => {
          const empty = !(pkg?.outputs[t.key] || '').trim()
          return (
            <button
              key={t.key}
              role="tab"
              aria-selected={outTab === t.key}
              className="tab"
              disabled={empty}
              title={empty ? 'This pass produced nothing' : undefined}
              onClick={() => { stopSpeaking(); setOutTab(t.key) }}
            >
              {t.label}
            </button>
          )
        })}
      </div>

      {body ? <Markdown text={body} /> : <p className="muted">This section produced no output.</p>}

      {suppressed > 0 && (
        <p className="muted" style={{ fontSize: 12 }}>
          {suppressed} statement{suppressed === 1 ? '' : 's'} withheld because {suppressed === 1
            ? 'it contained a figure'
            : 'they contained figures'}{' '}
          not supported by the source material. Collect real numbers from the customer instead.
        </p>
      )}

      {body && <ReadAloud text={body} />}

      {cites.length > 0 && (
        <div className="cites">
          {cites.map((c, i) => (
            <span key={i} className="cite" title={`${c.collection}/${c.source}`}>
              {c.title || c.source}
            </span>
          ))}
        </div>
      )}
    </div>
  )
}
