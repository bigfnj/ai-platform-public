import { useEffect, useMemo, useRef, useState } from 'react'
import { generatePackage, getScenarios } from './api'
import { canSpeak, speak, stopSpeaking } from './voice'
import type {
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

const OUTPUT_TABS: { key: string; label: string }[] = [
  { key: 'scenario_card', label: 'Scenario Card' },
  { key: 'discovery', label: 'Discovery Playbook' },
  { key: 'customer_qa', label: 'Customer Q&A' },
  { key: 'roi', label: 'Value Summary' },
]

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

type Step = 'pick' | 'ask' | 'run' | 'done'

export default function Builder() {
  const [scenarios, setScenarios] = useState<Scenario[] | null>(null)
  const [stages, setStages] = useState<Stage[]>([])
  const [loadError, setLoadError] = useState<string | null>(null)

  const [step, setStep] = useState<Step>('pick')
  const [picked, setPicked] = useState<string | null>(null)
  const [qIndex, setQIndex] = useState(0)
  const [answers, setAnswers] = useState<Record<string, string>>({})

  const [stageState, setStageState] = useState<Record<string, StageState>>({})
  const [pkg, setPkg] = useState<ScenarioPackage | null>(null)
  const [runError, setRunError] = useState<string | null>(null)
  const [outTab, setOutTab] = useState('scenario_card')
  const cancelRef = useRef<(() => void) | null>(null)

  useEffect(() => {
    getScenarios()
      .then((r) => {
        setScenarios(r.scenarios)
        setStages(r.stages)
      })
      .catch((e) => setLoadError(String(e.message || e)))
    return () => cancelRef.current?.()
  }, [])

  const scenario = scenarios?.find((s) => s.id === picked) ?? null

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
    cancelRef.current = generatePackage(
      { scenario_id: scenarioId, answers: finalAnswers },
      {
        onStage: (e) => setStageState((prev) => ({ ...prev, [e.key]: e.state })),
        onPackage: (p) => {
          setPkg(p)
          // Land on the first tab that actually produced content — a failed pass should not
          // greet the partner with an empty panel.
          const first = OUTPUT_TABS.find((t) => (p.outputs[t.key] || '').trim())
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
          Four questions, then a grounded pre-call package. Around twenty seconds.
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

  // --- Step 3: generation, with an honest checklist ------------------------
  if (step === 'run') {
    return (
      <div className="card">
        <h3>{scenario.icon} Building your pre-call package</h3>
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
          Each line is a separate grounded pass over the knowledge base, not a progress animation.
        </p>
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
        {OUTPUT_TABS.map((t) => {
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
