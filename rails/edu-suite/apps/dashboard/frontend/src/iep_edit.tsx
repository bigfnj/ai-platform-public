// IEP Present-Levels review/generate view. Loads the OCR-extracted 8 sections
// from the upload/extract job, shows each as CURRENT (read-only, from the PDF)
// beside a YOUR-INPUT box, then POSTs to /generate-iep, which creates a new job
// that elaborates the English narrative. Renders inside the module's `.edu` container.
import { useEffect, useState } from 'react'
import { generateIep, getPresentLevels } from './api'

// (key, label) for the 8 narrative sections — must match the backend SECTION order.
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

export default function IepForm({
  jobId,
  onDone,
  onCancel,
}: {
  jobId: string
  onDone: (newId: string) => void
  onCancel: () => void
}) {
  const [hdr, setHdr] = useState<Record<string, string>>({})
  const [current, setCurrent] = useState<Record<string, string>>({})
  const [input, setInput] = useState<Record<string, string>>({})
  const [name, setName] = useState('')
  const [meta, setMeta] = useState('')
  const [msg, setMsg] = useState('Loading…')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    getPresentLevels(jobId)
      .then((pl) => {
        if (pl.error) {
          setMsg(pl.error)
          return
        }
        setHdr(pl.header || {})
        setCurrent(pl.sections || {})
        setName(pl.header?.student_name || '')
        setMsg('')
      })
      .catch((e) => setMsg(String(e)))
  }, [jobId])

  const generate = async () => {
    const sections: Record<string, { current: string; input: string }> = {}
    for (const [k] of SECTIONS) sections[k] = { current: current[k] || '', input: input[k] || '' }
    const nm = name.trim() || 'Present Levels'
    setBusy(true)
    setMsg('Generating…')
    try {
      const d = await generateIep(jobId, {
        filled: { name: nm, header: hdr, meta: meta.trim(), sections },
        name: nm,
      })
      if (d.id) onDone(d.id)
      else setMsg(d.error || 'error')
    } catch (e) {
      setMsg(String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="grid">
      <div className="card">
        <div className="row" style={{ justifyContent: 'space-between', alignItems: 'center', gap: 12 }}>
          <h3 style={{ margin: 0 }}>Present Levels — review &amp; add your input</h3>
          <button className="ghost" onClick={onCancel} title="Close without generating (back to jobs)">
            ✕ Close
          </button>
        </div>
        <label>Student</label>
        <input type="text" value={name} onChange={(e) => setName(e.target.value)} />
        <label>Student context (grade, age, EL status, disability) — optional</label>
        <input
          type="text"
          value={meta}
          onChange={(e) => setMeta(e.target.value)}
          placeholder="e.g. grade 8, age 14, English Learner"
        />
        <p className="muted">
          Job {jobId} · Left = what was extracted from the uploaded PDF. Right = your new
          notes/data to fold in. The model elaborates each section in <b>English</b>; leave a box
          empty to just polish the current text. Missing numbers become [bracketed placeholders]
          to fill in. This is a DRAFT for the IEP team to review and approve.
        </p>
      </div>

      {SECTIONS.map(([k, label]) => (
        <div className="card" key={k}>
          <h3>{label}</h3>
          <div className="row" style={{ alignItems: 'flex-start', gap: 12 }}>
            <div style={{ flex: 1 }}>
              <label>Current (from PDF)</label>
              <textarea
                readOnly
                rows={5}
                value={current[k] || ''}
                style={{ width: '100%', opacity: 0.85 }}
              />
            </div>
            <div style={{ flex: 1 }}>
              <label>Your input</label>
              <textarea
                rows={5}
                value={input[k] || ''}
                onChange={(e) => setInput((p) => ({ ...p, [k]: e.target.value }))}
                placeholder="new observations, scores, notes…"
                style={{ width: '100%' }}
              />
            </div>
          </div>
        </div>
      ))}

      <div className="card">
        <button onClick={generate} disabled={busy}>
          Generate present-levels narrative
        </button>{' '}
        <button className="ghost" onClick={onCancel}>
          Cancel
        </button>{' '}
        <span className="muted">{msg}</span>
      </div>
    </div>
  )
}
