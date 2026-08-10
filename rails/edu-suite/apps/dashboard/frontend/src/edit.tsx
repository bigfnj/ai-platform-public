// TeachTown Builder review/edit view (React port of the old standalone edit page).
// Loads the AI-drafted unit, lets the instructor edit weeks/vocab/missions, then
// builds it (optionally with EN/ES + audio) via /api/jobs/{id}/finalize, which
// creates a new build job. Renders inside the module's `.edu` container.
import { useEffect, useState } from 'react'
import { finalize, getUnit, type Mission, type Vocab } from './api'

type Week = { learn: string; v: Vocab[] }

export default function EditUnit({
  jobId,
  onDone,
  onCancel,
}: {
  jobId: string
  onDone: (newId: string) => void
  onCancel: () => void
}) {
  const [label, setLabel] = useState('')
  const [heroP, setHeroP] = useState('')
  const [weeks, setWeeks] = useState<Record<string, Week>>({})
  const [missions, setMissions] = useState<Mission[]>([])
  const [activities, setActivities] = useState<Record<string, unknown>>({})
  const [enrich, setEnrich] = useState(false)
  const [msg, setMsg] = useState('Loading…')
  const [building, setBuilding] = useState(false)

  useEffect(() => {
    getUnit(jobId)
      .then((u) => {
        if (u.error) {
          setMsg(u.error)
          return
        }
        setLabel(u.label || '')
        setHeroP(u.hero?.p || '')
        setWeeks(u.weekInfo || {})
        setMissions(u.missions || [])
        setActivities(u.activities || {})
        setMsg('')
      })
      .catch((e) => setMsg(String(e)))
  }, [jobId])

  const setLearn = (wk: string, val: string) =>
    setWeeks((p) => ({ ...p, [wk]: { ...p[wk], learn: val } }))
  const setVocab = (wk: string, i: number, col: 0 | 1 | 2, val: string) =>
    setWeeks((p) => ({
      ...p,
      [wk]: {
        ...p[wk],
        v: p[wk].v.map((row, ri) =>
          ri === i
            ? ((col === 0
                ? [val, row[1], row[2]]
                : col === 1
                  ? [row[0], val, row[2]]
                  : [row[0], row[1], val]) as Vocab)
            : row,
        ),
      },
    }))
  const addVocab = (wk: string) =>
    setWeeks((p) => ({ ...p, [wk]: { ...p[wk], v: [...p[wk].v, ['', '', ''] as Vocab] } }))
  const removeVocab = (wk: string, i: number) =>
    setWeeks((p) => ({ ...p, [wk]: { ...p[wk], v: p[wk].v.filter((_, ri) => ri !== i) } }))

  const setMission = (i: number, col: 2 | 3 | 4 | 5, val: string) =>
    setMissions((p) =>
      p.map((m, mi) => {
        if (mi !== i) return m
        const c = [...m] as Mission
        if (col === 5) c[5] = val.split(',').map((s) => s.trim()).filter(Boolean)
        else c[col] = val
        return c
      }),
    )

  const build = async () => {
    const cleanWeeks: Record<string, Week> = {}
    for (const [wk, w] of Object.entries(weeks)) {
      cleanWeeks[wk] = { learn: w.learn, v: w.v.filter(([word]) => word.trim()) }
    }
    const name = label.trim() || 'Unit'
    const unit = { label: name, hero: { h1: name, p: heroP }, weekInfo: cleanWeeks, missions, activities }
    setBuilding(true)
    setMsg('Building…')
    try {
      const d = await finalize(jobId, { unit, name, enrich, audio: enrich })
      if (d.id) onDone(d.id)
      else setMsg(d.error || 'error')
    } catch (e) {
      setMsg(String(e))
    } finally {
      setBuilding(false)
    }
  }

  return (
    <div className="grid">
      <div className="card">
        <div className="row" style={{ justifyContent: 'space-between', alignItems: 'center', gap: 12 }}>
          <h3 style={{ margin: 0 }}>Review &amp; edit the drafted unit</h3>
          <button className="ghost" onClick={onCancel} title="Close without building (back to jobs)">
            ✕ Close
          </button>
        </div>
        <label>Unit name</label>
        <input type="text" value={label} onChange={(e) => setLabel(e.target.value)} />
        <p className="muted">
          Job {jobId} · edit the vocabulary and activities the AI drafted, then build.
        </p>
      </div>

      <div className="card">
        <h3>Weeks &amp; vocabulary</h3>
        {Object.keys(weeks)
          .sort((a, b) => Number(a) - Number(b))
          .map((wk) => (
            <div key={wk} style={{ marginBottom: 16 }}>
              <label>{wk === '0' ? 'Overview' : `Week ${wk}`} — learning summary</label>
              <input
                type="text"
                value={weeks[wk].learn}
                onChange={(e) => setLearn(wk, e.target.value)}
              />
              <label>Vocabulary (word — meaning — section)</label>
              {weeks[wk].v.map((row, i) => (
                <div className="row" key={i}>
                  <input
                    type="text"
                    placeholder="word"
                    value={row[0]}
                    onChange={(e) => setVocab(wk, i, 0, e.target.value)}
                  />
                  <input
                    type="text"
                    placeholder="meaning"
                    value={row[1]}
                    onChange={(e) => setVocab(wk, i, 1, e.target.value)}
                  />
                  <select
                    value={row[2] || ''}
                    onChange={(e) => setVocab(wk, i, 2, e.target.value)}
                    title="Which section (subject) this word belongs to"
                  >
                    <option value="">— section</option>
                    <option value="ELA">ELA</option>
                    <option value="Math">Math</option>
                    <option value="Science">Science</option>
                    <option value="Social Studies">Social Studies</option>
                  </select>
                  <button className="ghost" onClick={() => removeVocab(wk, i)}>✕</button>
                </div>
              ))}
              <button className="ghost" onClick={() => addVocab(wk)}>+ word</button>
            </div>
          ))}
      </div>

      <div className="card">
        <h3>Activities</h3>
        {missions.map((m, i) => (
          <div key={i} style={{ borderTop: '1px solid var(--e-border)', paddingTop: 10, marginTop: 10 }}>
            <div className="muted">
              {m[0] === 0 ? 'Overview' : `Week ${m[0]}`} · {m[1]}
              {m[6] ? ` · ${m[6].split('/').pop()}` : ''}
            </div>
            <label>Title</label>
            <input type="text" value={m[2]} onChange={(e) => setMission(i, 2, e.target.value)} />
            <label>Prompt</label>
            <input type="text" value={m[3]} onChange={(e) => setMission(i, 3, e.target.value)} />
            <label>Type</label>
            <select value={m[4]} onChange={(e) => setMission(i, 4, e.target.value)}>
              <option value="choice">choice</option>
              <option value="type">type</option>
              <option value="sort">sort</option>
            </select>
            <label>Options (comma separated)</label>
            <input
              type="text"
              value={m[5].join(', ')}
              onChange={(e) => setMission(i, 5, e.target.value)}
            />
          </div>
        ))}
      </div>

      <div className="card">
        <div className="check">
          <input id="edit-enrich" type="checkbox" checked={enrich}
            onChange={(e) => setEnrich(e.target.checked)} />
          <label htmlFor="edit-enrich" style={{ margin: 0 }}>Add Spanish + audio</label>
        </div>
        <div style={{ marginTop: 12 }}>
          <button onClick={build} disabled={building}>Build unit</button>{' '}
          <button className="ghost" onClick={onCancel}>Cancel</button>{' '}
          <span className="muted">{msg}</span>
        </div>
      </div>
    </div>
  )
}
