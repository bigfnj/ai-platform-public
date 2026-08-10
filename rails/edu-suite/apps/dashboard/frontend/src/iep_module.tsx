// IEP Present Levels — the single-page flow for the IEP-only platform app.
//
// Unlike the generic edu-suite dashboard (workflow picker + Jobs queue), this is one
// linear page: upload a SEIS PDF -> a quick JOBLESS parse fills the review boxes inline
// -> the teacher adds input per section -> Submit runs the elaboration pipeline -> the
// results screen renders the brand-new present levels with a Copy button per section (to
// paste straight into SEIS) plus a Download. A compact "Recent" list keeps finished
// generations for re-download without re-parsing.
//
// Exposed as the `iep_app/module` federated remote (see vite.config.iep.ts). Styles are
// injected as a <style> tag (federation doesn't reliably inject a remote's CSS) and use
// the shell's CSS variables with fallbacks so it themes light/dark.
import { useCallback, useEffect, useRef, useState } from 'react'
import {
  deleteJob,
  downloadUrl,
  generateIepStandalone,
  getJob,
  getPresentLevelsFinal,
  listJobs,
  parseIep,
  type Job,
} from './api'

// The 8 narrative sections, in form order — must match the backend SECTION order.
const SECTIONS: [string, string][] = [
  ['strengths_preferences_interests', 'Strengths / Preferences / Interests'],
  ['parent_input_concerns', 'Parent Input and Concerns'],
  ['preacademic_academic_functional', 'Preacademic / Academic / Functional Skills'],
  ['communication_development', 'Communication Development'],
  ['gross_fine_motor', 'Gross / Fine Motor Development'],
  ['social_emotional_behavioral', 'Social Emotional / Behavioral'],
  ['vocational', 'Vocational'],
  ['adaptive_daily_living', 'Adaptive / Daily Living Skills'],
]
// The generated output adds the flagged Areas of Need as a 9th block.
const OUT_SECTIONS: [string, string][] = [...SECTIONS, ['areas_of_need', 'Areas of Need']]

const CSS = `
.iep { --i-ink: var(--text-primary, #1f2733); --i-muted: var(--text-secondary, #68738a);
  --i-surface: var(--surface-1, #ffffff); --i-border: var(--border, #e2e8f0);
  --i-accent: var(--accent, #4e63d9); color: var(--i-ink); }
.iep .grid { display: grid; gap: 18px; max-width: 960px; }
.iep .card { background: var(--i-surface); border: 1px solid var(--i-border);
  border-radius: 14px; padding: 16px 18px; min-width: 0; }
.iep h3 { margin: 0 0 10px; font-size: 16px; }
.iep h4 { margin: 0; font-size: 14.5px; }
.iep label { display: block; font-weight: 650; font-size: 13px; margin: 10px 0 4px; }
.iep input[type=text], .iep input[type=file] { font: inherit; width: 100%;
  box-sizing: border-box; padding: 8px 10px; border: 1px solid var(--i-border);
  border-radius: 9px; background: var(--i-surface); color: var(--i-ink); }
.iep textarea { font: inherit; width: 100%; box-sizing: border-box; padding: 8px 10px;
  border: 1px solid var(--i-border); border-radius: 9px; background: var(--i-surface);
  color: var(--i-ink); min-height: 66px; resize: vertical; }
.iep button { font: inherit; font-weight: 700; padding: 9px 14px; border: 0;
  border-radius: 9px; background: var(--grad-accent, var(--i-accent)); color: #fff; cursor: pointer; }
.iep button:disabled { opacity: .5; cursor: default; }
.iep button.ghost { background: transparent; color: var(--i-ink);
  border: 1px solid var(--i-border); font-weight: 600; }
.iep button.mini { padding: 4px 10px; font-size: 12.5px; font-weight: 700; }
.iep a.dl { display: inline-block; font: inherit; font-weight: 700; font-size: 12.5px;
  padding: 4px 10px; border-radius: 9px; background: transparent; color: var(--i-ink);
  border: 1px solid var(--i-border); text-decoration: none; cursor: pointer; }
.iep .muted { color: var(--i-muted); font-size: 13px; }
.iep .lead { margin: 0 0 12px; font-size: 14px; line-height: 1.55; }
.iep .guide-row { display: flex; gap: 12px; margin: 8px 0; font-size: 14px; align-items: baseline; }
.iep .guide-row .k { flex: 0 0 120px; font-weight: 750; color: var(--i-muted); }
.iep .guide-row .v { flex: 1; }
.iep .row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.iep .cols { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; align-items: start; }
@media (max-width: 720px) { .iep .cols { grid-template-columns: 1fr; } }
.iep table { width: 100%; border-collapse: collapse; }
.iep th, .iep td { text-align: left; padding: 8px; border-bottom: 1px solid var(--i-border); font-size: 14px; }
.iep .badge { font-size: 12px; font-weight: 800; padding: 2px 9px; border-radius: 20px; text-transform: capitalize; }
.iep .queued { background: #eef1f6; color: #445; }
.iep .running { background: #fff0bf; color: #7a5b00; }
.iep .done { background: #d7f5e3; color: #1e7a4b; }
.iep .failed { background: #ffd9d9; color: #a12; }
.iep .link { color: var(--i-accent); cursor: pointer; text-decoration: none; }
.iep .stages { font-size: 13px; line-height: 1.7; }
.iep .warnbox { border: 1px solid #f2d98a; background: #fff8e6; color: #7a5b00;
  border-radius: 10px; padding: 10px 12px; font-size: 13px; }
.iep .errbox { border: 1px solid #f1b0b0; background: #fff2f2; border-radius: 10px;
  padding: 12px 14px; }
.iep .errhead { color: #b3261e; font-weight: 800; font-size: 14px; margin-bottom: 6px; }
.iep .errbody { font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 12.5px;
  line-height: 1.5; color: #7a1c16; white-space: pre-wrap; word-break: break-word; }
.iep .chip { display: inline-block; font-size: 12.5px; color: var(--i-muted);
  border: 1px solid var(--i-border); border-radius: 20px; padding: 2px 10px; margin: 0 6px 6px 0; }
.iep .out { white-space: pre-wrap; font-size: 14px; line-height: 1.55; margin: 6px 0 0; }
.iep .sechead { display: flex; justify-content: space-between; align-items: center; gap: 10px; }
.iep .copied { color: #1e7a4b; font-weight: 700; font-size: 12.5px; }
`

function badgeClass(s: string): string {
  return ['queued', 'running', 'done', 'failed'].includes(s) ? s : 'queued'
}

async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text)
      return true
    }
  } catch {
    /* fall through to the legacy path */
  }
  try {
    const ta = document.createElement('textarea')
    ta.value = text
    ta.style.position = 'fixed'
    ta.style.opacity = '0'
    document.body.appendChild(ta)
    ta.select()
    const ok = document.execCommand('copy')
    document.body.removeChild(ta)
    return ok
  } catch {
    return false
  }
}

type Phase = 'upload' | 'review' | 'generating' | 'done'

export default function IepApp() {
  const [phase, setPhase] = useState<Phase>('upload')
  const [name, setName] = useState('')
  const [meta, setMeta] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [parsing, setParsing] = useState(false)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')
  const fileRef = useRef<HTMLInputElement>(null)

  // Parsed (review) state.
  const [header, setHeader] = useState<Record<string, string>>({})
  const [current, setCurrent] = useState<Record<string, string>>({})
  const [input, setInput] = useState<Record<string, string>>({})
  const [warnings, setWarnings] = useState<string[]>([])

  // Generate / results state.
  const [jobId, setJobId] = useState<string | null>(null)
  const [detail, setDetail] = useState<Job | null>(null)
  const [finalSections, setFinalSections] = useState<Record<string, string>>({})
  const [finalHeader, setFinalHeader] = useState<Record<string, string>>({})
  const [finalName, setFinalName] = useState('')
  const [copied, setCopied] = useState<string | null>(null)

  const [recent, setRecent] = useState<Job[]>([])

  useEffect(() => {
    const style = document.createElement('style')
    style.textContent = CSS
    document.head.appendChild(style)
    return () => {
      document.head.removeChild(style)
    }
  }, [])

  const refreshRecent = useCallback(() => {
    listJobs('', 'iep_present_levels').then(setRecent).catch(() => {})
  }, [])

  useEffect(() => {
    refreshRecent()
    const id = setInterval(refreshRecent, 4000)
    return () => clearInterval(id)
  }, [refreshRecent])

  const resetAll = () => {
    setPhase('upload')
    setName('')
    setMeta('')
    setFile(null)
    setHeader({})
    setCurrent({})
    setInput({})
    setWarnings([])
    setJobId(null)
    setDetail(null)
    setFinalSections({})
    setFinalHeader({})
    setFinalName('')
    setMsg('')
    if (fileRef.current) fileRef.current.value = ''
  }

  // --- upload -> jobless parse ---------------------------------------------
  const parse = async () => {
    if (!file) {
      setMsg('Choose a SEIS Present-Levels PDF first.')
      return
    }
    setParsing(true)
    setMsg('Reading the PDF…')
    try {
      const pl = await parseIep(file)
      if (pl.error) {
        setMsg(pl.error)
        return
      }
      const hdr = pl.header || {}
      setHeader(hdr)
      setCurrent(pl.sections || {})
      setInput({})
      setWarnings(pl.warnings || [])
      if (!name.trim() && hdr.student_name) setName(hdr.student_name)
      setMsg('')
      setPhase('review')
    } catch (e) {
      setMsg(`Error: ${(e as Error).message}`)
    } finally {
      setParsing(false)
    }
  }

  // --- review -> generate ---------------------------------------------------
  const submit = async () => {
    const sections: Record<string, { current: string; input: string }> = {}
    for (const [k] of SECTIONS) sections[k] = { current: current[k] || '', input: input[k] || '' }
    const nm = name.trim() || header.student_name || 'Present Levels'
    setBusy(true)
    setMsg('Starting…')
    try {
      const d = await generateIepStandalone({
        filled: { name: nm, header, meta: meta.trim(), sections },
        name: nm,
      })
      if (d.id) {
        setJobId(d.id)
        setDetail(null)
        setPhase('generating')
        setMsg('')
        refreshRecent()
      } else {
        setMsg(d.error || 'Could not start the job.')
      }
    } catch (e) {
      setMsg(`Error: ${(e as Error).message}`)
    } finally {
      setBusy(false)
    }
  }

  // Poll the generate job; on done load the elaborated sections, on failure return to
  // the review form with the teacher's input intact so they can retry.
  useEffect(() => {
    if (phase !== 'generating' || !jobId) return
    let alive = true
    const tick = async () => {
      try {
        const j = await getJob(jobId)
        if (!alive) return
        setDetail(j)
        if (j.status === 'done') {
          const f = await getPresentLevelsFinal(jobId)
          if (!alive) return
          setFinalSections(f.sections || {})
          setFinalHeader(f.header || {})
          setFinalName(f.name || name)
          setPhase('done')
          refreshRecent()
        } else if (j.status === 'failed') {
          setMsg(j.error || 'Generation failed. Adjust and try again.')
          setPhase('review')
        }
      } catch {
        /* transient; keep polling */
      }
    }
    void tick()
    const id = setInterval(tick, 1200)
    return () => {
      alive = false
      clearInterval(id)
    }
  }, [phase, jobId, name, refreshRecent])

  const copySection = async (key: string, text: string) => {
    if (await copyText(text)) {
      setCopied(key)
      setTimeout(() => setCopied((c) => (c === key ? null : c)), 1600)
    } else {
      setMsg('Copy failed — select the text and copy manually.')
    }
  }

  const copyAll = async () => {
    const all = OUT_SECTIONS.map(([k, label]) => `${label}\n${finalSections[k] || ''}`).join('\n\n')
    if (await copyText(all)) {
      setCopied('__all__')
      setTimeout(() => setCopied((c) => (c === '__all__' ? null : c)), 1600)
    }
  }

  const openRecent = async (jb: Job) => {
    if (jb.status !== 'done') return
    try {
      const f = await getPresentLevelsFinal(jb.id)
      if (f.error) {
        setMsg(f.error)
        return
      }
      setJobId(jb.id)
      setFinalSections(f.sections || {})
      setFinalHeader(f.header || {})
      setFinalName(f.name || jb.name)
      setMsg('')
      setPhase('done')
    } catch (e) {
      setMsg(`Error: ${(e as Error).message}`)
    }
  }

  const removeRecent = async (id: string) => {
    if (!confirm('Delete this generated present levels and its files?')) return
    await deleteJob(id)
    if (jobId === id && phase === 'done') resetAll()
    refreshRecent()
  }

  const genEvents = (detail?.events ?? []).filter((e) =>
    ['stage_started', 'stage_finished', 'stage_progress', 'model', 'job_failed', 'job_finished'].includes(
      e.kind,
    ),
  )

  const metaChips = (hdr: Record<string, string>) =>
    (
      [
        ['Student', hdr.student_name],
        ['Birthdate', hdr.birthdate],
        ['IEP Date', hdr.iep_date],
      ] as [string, string | undefined][]
    )
      .filter(([, v]) => v)
      .map(([k, v]) => (
        <span className="chip" key={k}>
          <b>{k}:</b> {v}
        </span>
      ))

  return (
    <div className="iep">
      <div className="grid">
        {/* Intro — always shown */}
        <div className="card">
          <h3>IEP Present Levels</h3>
          <p className="lead">
            Turn a SEIS Present-Levels PDF into a fuller English present-levels narrative. Upload it,
            review the 8 extracted sections, add your notes and data, and the local model elaborates
            each section to paste into SEIS. English only; nothing leaves the local machine.
          </p>
          <div className="guide-row">
            <span className="k">How it works</span>
            <span className="v">
              Upload → the PDF is parsed on the spot into the 8 sections → you add input beside each
              → Submit generates a brand-new draft (8 sections + Areas of Need) with a Copy button
              per section. Missing numbers become <b>[bracketed placeholders]</b> to fill in. A DRAFT
              for the IEP team to review and approve.
            </span>
          </div>
        </div>

        {/* Upload / parse */}
        {phase === 'upload' && (
          <div className="card">
            <h3>Upload a Present-Levels PDF</h3>
            <label>Student / name (optional)</label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="defaults to the name read from the PDF"
            />
            <label>SEIS Present-Levels PDF</label>
            <input
              key="file"
              type="file"
              accept="application/pdf,.pdf"
              ref={fileRef}
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            />
            <p className="muted">
              The 8 sections are OCR-extracted right here (a few seconds) — the PDF is not saved to
              the server.
            </p>
            <div style={{ marginTop: 12 }}>
              <button onClick={parse} disabled={parsing || !file}>
                {parsing ? 'Parsing…' : 'Parse PDF'}
              </button>{' '}
              <span className="muted">{msg}</span>
            </div>
          </div>
        )}

        {/* Review + add input */}
        {phase === 'review' && (
          <>
            <div className="card">
              <div className="sechead">
                <h3 style={{ margin: 0 }}>Review &amp; add your input</h3>
                <button className="ghost mini" onClick={resetAll}>
                  ↻ Start over
                </button>
              </div>
              <div style={{ margin: '8px 0' }}>{metaChips(header)}</div>
              <label>Student / name</label>
              <input type="text" value={name} onChange={(e) => setName(e.target.value)} />
              <label>Student context (grade, age, EL status, disability) — optional</label>
              <input
                type="text"
                value={meta}
                onChange={(e) => setMeta(e.target.value)}
                placeholder="e.g. grade 8, age 14, English Learner"
              />
              <p className="muted">
                Left = what was extracted from the PDF. Right = your new notes/data to fold in. The
                model elaborates each section in <b>English</b>; leave a box empty to just polish the
                current text.
              </p>
              {warnings.length > 0 && (
                <div className="warnbox">
                  Some headings weren't found cleanly — double-check those sections extracted right:
                  <br />
                  {warnings.map((w) => w.replace('heading not found: ', '').replace(/^_/, '')).join(', ')}
                </div>
              )}
            </div>

            {SECTIONS.map(([k, label]) => (
              <div className="card" key={k}>
                <h4>{label}</h4>
                <div className="cols" style={{ marginTop: 8 }}>
                  <div>
                    <label>Current (from PDF)</label>
                    <textarea readOnly rows={5} value={current[k] || ''} style={{ opacity: 0.85 }} />
                  </div>
                  <div>
                    <label>Your input</label>
                    <textarea
                      rows={5}
                      value={input[k] || ''}
                      onChange={(e) => setInput((p) => ({ ...p, [k]: e.target.value }))}
                      placeholder="new observations, scores, notes…"
                    />
                  </div>
                </div>
              </div>
            ))}

            <div className="card">
              <button onClick={submit} disabled={busy}>
                Generate present-levels narrative
              </button>{' '}
              <button className="ghost" onClick={resetAll} disabled={busy}>
                Cancel
              </button>{' '}
              <span className="muted">{msg}</span>
            </div>
          </>
        )}

        {/* Generating */}
        {phase === 'generating' && (
          <div className="card">
            <h3>
              Generating… <span className="badge running">working</span>
            </h3>
            <p className="muted">
              The local model is elaborating the 8 sections. This usually takes under a couple of
              minutes.
            </p>
            <div className="stages">
              {genEvents.map((e, i) => {
                if (e.kind === 'stage_started')
                  return (
                    <div key={i}>
                      ▶ <b>{e.message || e.stage}</b>
                    </div>
                  )
                if (e.kind === 'stage_finished')
                  return (
                    <div key={i}>
                      {e.status === 'done' ? '✓' : '✕'} {e.stage}{' '}
                      <span className="muted">
                        {e.elapsed ? `${e.elapsed.toFixed(1)}s` : ''} {e.message || ''}
                      </span>
                    </div>
                  )
                if (e.kind === 'model')
                  return (
                    <div key={i} className="muted">
                      &nbsp;&nbsp;model {e.status}: {e.model}
                    </div>
                  )
                if (e.kind === 'stage_progress')
                  return (
                    <div key={i} className="muted">
                      &nbsp;&nbsp;{e.message}
                    </div>
                  )
                if (e.kind === 'job_failed')
                  return (
                    <div key={i} style={{ color: '#c0392b' }}>
                      <b>job failed: {e.message}</b>
                    </div>
                  )
                return (
                  <div key={i} style={{ color: '#1e8e5a' }}>
                    <b>job finished</b>
                  </div>
                )
              })}
              {genEvents.length === 0 && <div className="muted">Queued…</div>}
            </div>
          </div>
        )}

        {/* Results: preview + per-section copy + download */}
        {phase === 'done' && (
          <>
            <div className="card">
              <div className="sechead">
                <h3 style={{ margin: 0 }}>
                  Generated present levels <span className="badge done">draft</span>
                </h3>
                <div className="row">
                  <button className="mini" onClick={copyAll}>
                    {copied === '__all__' ? '✓ Copied all' : 'Copy all'}
                  </button>
                  {jobId && (
                    <a className="dl" href={downloadUrl(jobId)}>
                      ⭳ Download
                    </a>
                  )}
                  <button className="ghost mini" onClick={resetAll}>
                    + New
                  </button>
                </div>
              </div>
              <div style={{ margin: '10px 0 2px' }}>{metaChips(finalHeader)}</div>
              <p className="muted">
                An AI-elaborated DRAFT for the IEP team to review, individualize, and approve — not a
                final or legally binding document. Copy each section straight into its SEIS field.
              </p>
            </div>

            {OUT_SECTIONS.map(([k, label]) => (
              <div className="card" key={k}>
                <div className="sechead">
                  <h4>{label}</h4>
                  <button className="mini" onClick={() => copySection(k, finalSections[k] || '')}>
                    {copied === k ? '✓ Copied' : 'Copy'}
                  </button>
                </div>
                <p className="out">{finalSections[k] || <span className="muted">(empty)</span>}</p>
              </div>
            ))}

            <div className="card">
              <button onClick={resetAll}>+ Start a new one</button>{' '}
              <span className="muted">{finalName}</span>
            </div>
          </>
        )}

        {/* Recent generations (re-download without re-parsing) */}
        <div className="card">
          <h3>Recent</h3>
          <p className="muted" style={{ marginTop: 0 }}>
            Held generations auto-delete after 30 days.
          </p>
          <table>
            <thead>
              <tr>
                <th>Name</th>
                <th>Status</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {recent.map((jb) => (
                <tr key={jb.id}>
                  <td>
                    {jb.status === 'done' ? (
                      <span className="link" onClick={() => openRecent(jb)}>
                        {jb.name}
                      </span>
                    ) : (
                      jb.name
                    )}
                  </td>
                  <td>
                    <span className={`badge ${badgeClass(jb.status)}`}>
                      {jb.status === 'failed' ? 'error' : jb.status}
                    </span>
                  </td>
                  <td>
                    {jb.status === 'done' && (
                      <>
                        <span className="link" onClick={() => openRecent(jb)}>
                          open
                        </span>
                        {' · '}
                        <a className="link" href={downloadUrl(jb.id)}>
                          download
                        </a>
                        {' · '}
                      </>
                    )}
                    <span className="link" onClick={() => removeRecent(jb.id)}>
                      delete
                    </span>
                  </td>
                </tr>
              ))}
              {recent.length === 0 && (
                <tr>
                  <td className="muted" colSpan={3}>
                    No present levels generated yet.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}
