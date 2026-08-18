// Gemini Enterprise CX — federated React module for the platform shell. Exposes ./module.
//
// The interface is a question deck plus one answer pane, and that shape is the design decision
// worth defending. A bare chat box over a corpus nobody has read produces a bad first
// experience: the user does not yet know what GECX is, so they cannot know what to ask, and
// their opening question is usually one the corpus cannot ground. The deck makes the corpus's
// own strengths clickable — and the first group is deliberately the set of questions where
// Google's marketing and Google's documentation disagree, because that is where a grounded
// assistant beats reading the product page.
//
// No own top bar or theme — the shell provides those; this renders inside a `.gemini-cx`
// wrapper and adopts the shell's palette via shared tokens (see web/THEMING.md).
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { askBuffered, askStreaming, fetchCapabilities, fetchDeck } from './api'
import { renderMarkdown } from './markdown'
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

function ModelPill({ caps }: { caps: Capabilities | null }) {
  if (!caps) return null
  const { models, corpus } = caps
  const down = models.broker !== 'ok'
  return (
    <div className="gcx-pills">
      <span className={'gcx-pill' + (down ? ' bad' : ' good')} title={
        down ? 'The broker is unreachable — answers are unavailable' : 'Broker reachable'}>
        {down ? 'broker down' : 'broker ok'}
      </span>
      <span className="gcx-pill" title="The model that writes the grounded answer">
        {models.rag_model}{models.rag_resident ? ' · warm' : ''}
      </span>
      <span className="gcx-pill" title="The retrieval embedder">
        {models.embed_model}{models.embed_resident ? ' · warm' : ''}
      </span>
      <span className="gcx-pill" title="Indexed chunks across all collections">
        {corpus.chunks} chunks · {corpus.collections} collections
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

function Citations({ cites }: { cites: Citation[] }) {
  if (!cites.length) return null
  return (
    <div className="gcx-cites">
      <h4>Sources</h4>
      {cites.map((c) => (
        <div className="gcx-cite" key={c.n}>
          <span className="gcx-cite-n">{c.n}</span>
          <div className="gcx-cite-body">
            <strong>{c.title || c.source}</strong>
            <span className="gcx-cite-meta">
              {titleCase(c.collection)} · <code>{c.source}</code> · {c.score.toFixed(3)}
            </span>
          </div>
        </div>
      ))}
    </div>
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
        <span className="gcx-logo" aria-hidden="true">🎧</span>
        <div className="gcx-titles">
          <h1>Gemini Enterprise CX</h1>
          <span className="gcx-sub">
            Grounded answers on Google Cloud&apos;s Gemini Enterprise for Customer Experience —
            every claim cited, status never smoothed over
          </span>
        </div>
        <span className="gcx-spacer" />
        <ModelPill caps={caps} />
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
                Start with <strong>Get it right</strong> — those are the questions where GECX&apos;s
                launch announcement and its product documentation give different answers.
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

              <Citations cites={cites} />
            </>
          )}
        </main>
      </div>
    </div>
  )
}
