// Gemini Enterprise CX — federated React module for the platform shell. Exposes ./module.
//
// The interface is a question deck plus one answer pane, and that shape is the design decision
// worth defending. A bare chat box over a corpus nobody has read produces a bad first
// experience: the user does not yet know what GECX is, so they cannot know what to ask, and
// their opening question is usually one the corpus cannot ground. The deck makes the corpus's
// own strengths clickable — orientation first ("What it is"), then immediately the questions
// where Google's marketing and Google's documentation disagree ("Get it right"), because that
// second group is where a grounded assistant beats reading the product page.
//
// No own top bar or theme — the shell provides those; this renders inside a `.gemini-cx`
// wrapper and adopts the shell's palette via shared tokens (see web/THEMING.md).
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { askBuffered, askStreaming, fetchCapabilities, fetchDeck, speakText } from './api'
import { renderMarkdown } from './markdown'
import { speak, stopSpeaking } from './voice'
import { titleCase } from './types'
import type {
  AskRequest,
} from './api'
import type {
  Capabilities,
  Citation,
  DeckGroup,
  DeckProblem,
  DeckQuestion,
} from './types'
import './theme.css'

interface Asked {
  question: string
  collections: string[]
}

// The Gemini "spark" — a four-pointed star with concave sides, in Google's blue→purple→rose
// gradient. Inline SVG rather than an emoji because there is no Gemini glyph in Unicode. The
// gradient id is namespaced: every federated remote renders into the same document, so a bare
// id like "grad" would collide with another rail's defs and silently repaint one of them.
function GeminiIcon() {
  return (
    <svg width={26} height={26} viewBox="0 0 24 24" aria-hidden="true" style={{ display: 'block' }}>
      <defs>
        <linearGradient id="gcxGeminiSpark" x1="0" y1="0" x2="24" y2="24"
          gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor="#4285F4" />
          <stop offset="0.52" stopColor="#9B72CB" />
          <stop offset="1" stopColor="#D96570" />
        </linearGradient>
      </defs>
      <path
        d="M12 0C12 6.627 6.627 12 0 12c6.627 0 12 5.373 12 12 0-6.627 5.373-12 12-12C17.373 12 12 6.627 12 0z"
        fill="url(#gcxGeminiSpark)"
      />
    </svg>
  )
}

// Status line follows the smb-partner-enablement pattern exactly: a coloured dot per model, the
// slot name, the resolved model, and its residency in muted text. Same classes (.status, .dot,
// .muted) scoped under this rail's wrapper, same 8px dot, same 16px gap.
function AiStatus({ caps }: { caps: Capabilities | null }) {
  if (!caps) return <span className="muted">checking…</span>
  const m = caps.models
  if (m.broker !== 'ok') return <span className="muted">● broker unreachable</span>
  return (
    <div className="status">
      <span title="@gemini-cx-rag — writes the grounded answer">
        <i className={`dot ${m.rag_resident ? 'on' : 'off'}`} />
        LLM ({m.rag_model}){' '}
        <span className="muted">{m.rag_resident ? 'GPU · ready' : 'cold'}</span>
      </span>
      <span title="@embed — retrieval over the GECX corpus">
        <i className={`dot ${m.embed_resident ? 'on' : 'off'}`} />
        Retrieval ({m.embed_model}){' '}
        <span className="muted">{m.embed_resident ? 'GPU · ready' : 'cold'}</span>
      </span>
      <span className="muted">
        {caps.corpus.chunks} chunks · {caps.corpus.collections} collections
      </span>
    </div>
  )
}

function Deck({
  groups,
  problems,
  activeId,
  busy,
  onPick,
}: {
  groups: DeckGroup[]
  problems: DeckProblem[]
  activeId: string
  busy: boolean
  onPick: (q: DeckQuestion) => void
}) {
  // A question whose collections are missing would retrieve nothing and answer "not covered",
  // so it is disabled rather than left as a trap.
  const broken = useMemo(() => new Set(problems.map((p) => p.question)), [problems])
  return (
    <aside className="gcx-deck">
      {groups.map((g) => (
        <section className="gcx-group" key={g.id}>
          <h3>
            <span className="gcx-group-icon" aria-hidden="true">{g.icon}</span>
            {g.label}
          </h3>
          <p className="gcx-group-blurb">{g.blurb}</p>
          {g.questions.map((q) => {
            const bad = broken.has(q.id)
            return (
              <button
                key={q.id}
                className={'gcx-q' + (activeId === q.id ? ' on' : '') + (bad ? ' broken' : '')}
                onClick={() => onPick(q)}
                disabled={busy || bad}
                title={bad ? 'This question references a collection that is not in the corpus' : q.text}
              >
                {q.text}
              </button>
            )
          })}
        </section>
      ))}
    </aside>
  )
}

// Sources live in their own column rather than under the answer, and the column is rendered
// even when empty. That is deliberate: citations arrive on the `retrieval` frame, i.e. BEFORE
// the first token, so a column that appeared only once it had content would resize the answer
// pane a moment after the user hit ask — a visible jump on every single question.
// Read aloud — same component shape and same toggle semantics as the smb-partner-enablement
// rail. Server-side synthesis is Kokoro-82M through the broker's tts_light (no GPU gate, no
// eviction, so the answer model stays resident); if that call fails for any reason the catch
// speaks a browser-mode payload instead, so the button never silently does nothing.
//
// `disabled` while streaming rather than hidden: it appears with the first token and stays put,
// which avoids the answer pane reflowing mid-stream. Reading a half-finished answer aloud is
// not useful, so it only becomes clickable once the stream is done.
function ReadAloud({ text, streaming, mode }: { text: string; streaming: boolean; mode?: string }) {
  const [on, setOn] = useState(false)
  const [loading, setLoading] = useState(false)
  // Unmounts when a new question clears the answer, which is exactly when playback should stop.
  useEffect(() => () => stopSpeaking(), [])
  if (!text) return null

  const toggle = async () => {
    if (on) {
      stopSpeaking()
      setOn(false)
      return
    }
    if (loading) return
    setLoading(true)
    try {
      const payload = await speakText(text)
      setOn(true)
      speak(payload, () => setOn(false))
    } catch {
      setOn(true)
      speak({ mode: 'browser', text, lang: 'en-US' }, () => setOn(false))
    } finally {
      setLoading(false)
    }
  }

  const title = streaming
    ? 'Available once the answer finishes'
    : mode === 'broker'
      ? 'Kokoro-82M via the broker (af_heart)'
      : 'Your browser’s speech synthesis — the broker media worker is unavailable'

  return (
    <div className="gcx-acts">
      <button className="gcx-btn" onClick={toggle} disabled={loading || streaming} title={title}>
        {loading ? '…' : on ? '■ Stop' : '🔊 Read aloud'}
      </button>
    </div>
  )
}

function Sources({ cites, asked }: { cites: Citation[]; asked: boolean }) {
  return (
    <aside className="gcx-sources">
      <h4>Sources</h4>
      {cites.length === 0 ? (
        <p className="gcx-sources-empty">
          {asked
            ? 'Nothing was retrieved for this question, so the answer is not grounded — treat it with suspicion.'
            : 'The chunks behind an answer appear here, most relevant first.'}
        </p>
      ) : (
        cites.map((c) => (
          <div className="gcx-cite" key={c.n}>
            <span className="gcx-cite-n">{c.n}</span>
            <div className="gcx-cite-body">
              <strong>{c.title || c.source}</strong>
              <span className="gcx-cite-meta">
                {titleCase(c.collection)} · <code>{c.source}</code> · {c.score.toFixed(3)}
              </span>
            </div>
          </div>
        ))
      )}
    </aside>
  )
}

export default function GeminiCxModule() {
  const [groups, setGroups] = useState<DeckGroup[]>([])
  const [problems, setProblems] = useState<DeckProblem[]>([])
  const [caps, setCaps] = useState<Capabilities | null>(null)
  const [loadErr, setLoadErr] = useState('')

  const [activeId, setActiveId] = useState('')
  const [asked, setAsked] = useState<Asked | null>(null)
  const [answer, setAnswer] = useState('')
  const [cites, setCites] = useState<Citation[]>([])
  const [busy, setBusy] = useState(false)
  const [askErr, setAskErr] = useState('')
  const [typed, setTyped] = useState('')

  const cancelRef = useRef<(() => void) | null>(null)
  const answerRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    let live = true
    Promise.all([fetchDeck(), fetchCapabilities()])
      .then(([deck, c]) => {
        if (!live) return
        setGroups(deck.groups)
        setProblems(deck.problems)
        setCaps(c)
      })
      .catch((e) => live && setLoadErr(String(e instanceof Error ? e.message : e)))
    return () => {
      live = false
    }
  }, [])

  // Cancel any in-flight stream if the module unmounts mid-answer.
  useEffect(() => () => cancelRef.current?.(), [])

  const run = useCallback((req: AskRequest, label: Asked, id: string) => {
    cancelRef.current?.()
    setActiveId(id)
    setAsked(label)
    setAnswer('')
    setCites([])
    setAskErr('')
    setBusy(true)

    let gotAnything = false
    const cancel = askStreaming(
      req,
      (frame) => {
        if (frame.type === 'retrieval') {
          setCites(frame.citations)
          gotAnything = true
        } else if (frame.type === 'token') {
          gotAnything = true
          setAnswer((a) => a + frame.text)
        }
      },
      (err) => {
        cancelRef.current = null
        if (err && !gotAnything) {
          // The WebSocket never delivered anything — most likely a proxy refusing the upgrade.
          // Fall back to the buffered route rather than showing the user a dead panel.
          askBuffered(req)
            .then((r) => {
              setAnswer(r.answer)
              setCites(r.citations)
            })
            .catch((e) => setAskErr(String(e instanceof Error ? e.message : e)))
            .finally(() => setBusy(false))
          return
        }
        if (err) setAskErr(err)
        setBusy(false)
      },
    )
    cancelRef.current = cancel
  }, [])

  const pick = useCallback((q: DeckQuestion) => {
    run({ question_id: q.id }, { question: q.text, collections: q.collections ?? [] }, q.id)
  }, [run])

  const submitTyped = useCallback(() => {
    const text = typed.trim()
    if (!text) return
    // A free-typed question is deliberately unscoped — the user may be asking across topics,
    // and guessing a scope for them retrieves worse than not guessing.
    run({ question: text }, { question: text, collections: [] }, '')
  }, [run, typed])

  // Keep the newest tokens in view while streaming.
  useEffect(() => {
    if (busy && answerRef.current) {
      answerRef.current.scrollTop = answerRef.current.scrollHeight
    }
  }, [answer, busy])

  const corpusEmpty = caps !== null && caps.corpus.chunks === 0

  return (
    <div className="gemini-cx">
      <header className="gcx-head">
        <span className="gcx-logo"><GeminiIcon /></span>
        <div className="gcx-titles">
          <h1>Gemini Enterprise CX</h1>
          <span className="gcx-sub">
            Grounded answers on Google Cloud&apos;s Gemini Enterprise for Customer Experience —
            every claim cited, status never smoothed over
          </span>
        </div>
        <span className="gcx-spacer" />
        <AiStatus caps={caps} />
      </header>

      {loadErr && (
        <div className="gcx-err">
          Couldn&apos;t reach the rail API: {loadErr}
          <br />
          Is the <code>gemini-cx</code> backend up on :8880?
        </div>
      )}

      {corpusEmpty && (
        <div className="gcx-err">
          <strong>The corpus is empty.</strong> Nothing is indexed, so every answer will say the
          context does not cover it. An admin can re-run ingest with{' '}
          <code>POST /gemini-cx/api/ingest?force=true</code> — and check the backend log, because
          ingest needs the broker&apos;s embedder to be reachable.
        </div>
      )}

      {problems.length > 0 && (
        <div className="gcx-note">
          {problems.length} deck question{problems.length > 1 ? 's are' : ' is'} disabled because
          the collection{problems.length > 1 ? 's they reference' : ' it references'} are not in
          the corpus:{' '}
          {problems.map((p) => `${p.question} (${p.missing_collections.join(', ')})`).join(' · ')}
        </div>
      )}

      <div className="gcx-body">
        <Deck
          groups={groups}
          problems={problems}
          activeId={activeId}
          busy={busy}
          onPick={pick}
        />

        <main className="gcx-answer">
          <div className="gcx-askbar">
            <input
              className="gcx-ask-input"
              placeholder="…or ask your own question about GECX"
              value={typed}
              onChange={(e) => setTyped(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') submitTyped()
              }}
              disabled={busy}
            />
            <button className="gcx-btn primary" onClick={submitTyped} disabled={busy || !typed.trim()}>
              Ask
            </button>
            {busy && (
              <button
                className="gcx-btn"
                onClick={() => {
                  cancelRef.current?.()
                  cancelRef.current = null
                  setBusy(false)
                }}
              >
                Stop
              </button>
            )}
          </div>

          {!asked && !busy && (
            <div className="gcx-empty">
              <p>Pick a question on the left, or type your own.</p>
              <p className="gcx-empty-hint">
                <strong>What it is</strong> orients you; <strong>Get it right</strong> covers the
                questions where GECX&apos;s launch announcement and its product documentation give
                different answers.
              </p>
            </div>
          )}

          {asked && (
            <>
              <div className="gcx-asked">
                <h2>{asked.question}</h2>
                {asked.collections.length > 0 && (
                  <div className="gcx-scope">
                    scoped to {asked.collections.map((c) => (
                      <span className="gcx-scope-chip" key={c}>{titleCase(c)}</span>
                    ))}
                  </div>
                )}
              </div>

              {askErr && <div className="gcx-err">Couldn&apos;t answer: {askErr}</div>}

              <div className="gcx-prose" ref={answerRef}>
                {answer ? renderMarkdown(answer) : busy ? <p className="gcx-thinking">Retrieving and reasoning…</p> : null}
                {busy && answer && <span className="gcx-caret" aria-hidden="true" />}
              </div>

              <ReadAloud text={answer} streaming={busy} mode={caps?.voice?.effective} />
            </>
          )}
        </main>

        <Sources cites={cites} asked={asked !== null} />
      </div>
    </div>
  )
}
