import { useState, useEffect, useRef } from 'react'
import { postJSON, streamEvents, getJSON, type Blueprint } from '../api'

interface Props {
  indexPresent: boolean
}

interface LogLine { text: string; kind: 'progress' | 'done' | 'error' }

type Tier = 'raw' | 'local' | 'offsite'

export default function BuildPanel({ indexPresent }: Props) {
  const [blueprints, setBlueprints] = useState<Blueprint[]>([])
  const [prompt, setPrompt] = useState('')
  const [blueprint, setBlueprint] = useState('')
  const [k, setK] = useState(12)
  const [tier, setTier] = useState<Tier>('offsite')
  const [title, setTitle] = useState('')
  const [running, setRunning] = useState(false)
  const [log, setLog] = useState<LogLine[]>([])
  const [resultMd, setResultMd] = useState<string | null>(null)
  const logRef = useRef<HTMLDivElement>(null)
  const stopRef = useRef<(() => void) | null>(null)

  useEffect(() => {
    getJSON<Blueprint[]>('/api/blueprints').then(setBlueprints).catch(() => {})
  }, [])

  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight
  }, [log])

  function appendLog(text: string, kind: LogLine['kind'] = 'progress') {
    setLog(prev => [...prev, { text, kind }])
  }

  const condenseRole = tier === 'local' ? '@chat' : tier === 'raw' ? '' : '@openmaic'

  async function startBuild() {
    if (!prompt.trim()) return
    setLog([])
    setResultMd(null)
    setRunning(true)
    try {
      await postJSON('/api/build/start', {
        prompt: prompt.trim(),
        blueprint: blueprint.trim(),
        k,
        raw: tier === 'raw',
        title: title.trim(),
        condense_role: condenseRole,
      })
      stopRef.current = streamEvents(
        '/api/build/events',
        (type, data) => {
          if (type === 'done') {
            appendLog('Build complete — ready to download.', 'done')
            setRunning(false)
            // Fetch the result for preview.
            fetch('/course-builder/api/build/result')
              .then(r => r.text())
              .then(setResultMd)
              .catch(() => {})
          } else if (type === 'error') {
            appendLog(data, 'error')
            setRunning(false)
          } else if (data) {
            appendLog(data)
          }
        },
        () => setRunning(false),
      )
    } catch (e) {
      appendLog(String(e), 'error')
      setRunning(false)
    }
  }

  function download() {
    if (!resultMd) return
    const blob = new Blob([resultMd], { type: 'text/markdown' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = (title.trim() || blueprint || 'course-source') + '.md'
    a.click()
    URL.revokeObjectURL(url)
  }

  return (
    <div className="cb-panel">
      <h2>✍️ Build Course</h2>

      <div className="cb-field">
        <label>Course brief / prompt</label>
        <textarea
          value={prompt}
          onChange={e => setPrompt(e.target.value)}
          placeholder="e.g. CIS-CSM certification prep for a CSM Admin"
          disabled={running}
          rows={3}
        />
      </div>

      <div className="cb-field">
        <label>Blueprint (exam domains)</label>
        <select value={blueprint} onChange={e => setBlueprint(e.target.value)} disabled={running}>
          <option value="">— no blueprint (required) —</option>
          {blueprints.map(bp => (
            <option key={bp.id} value={bp.id}>{bp.id} — {bp.name}</option>
          ))}
          {blueprints.length === 0 && (
            <option disabled value="">Loading… (check CB_BLUEPRINT_CSV is set)</option>
          )}
        </select>
        <span style={{ fontSize: '0.75rem', color: '#888' }}>
          Exam domains weight the retrieval budget. Set CB_BLUEPRINT_CSV to your blueprint-domains.csv.
        </span>
      </div>

      <div className="cb-field" style={{ maxWidth: 160 }}>
        <label>Chunks per module (k={k})</label>
        <input type="range" min={4} max={40} step={2} value={k}
          onChange={e => setK(Number(e.target.value))} disabled={running} />
      </div>

      <div className="cb-field">
        <label>Condensation</label>
        <div className="cb-radio-group">
          {([
            ['raw', 'Raw (no LLM)', 'Pure retrieval — fastest, largest output'],
            ['local', 'Local (@chat)', 'gemma3:4b — stays on this box, 8 GB card'],
            ['offsite', 'Offsite (@openmaic)', 'mistral-small3.2:24b — best quality'],
          ] as [Tier, string, string][]).map(([v, label, desc]) => (
            <label key={v} title={desc}>
              <input type="radio" name="tier" value={v}
                checked={tier === v} onChange={() => setTier(v)} disabled={running} />
              {label}
            </label>
          ))}
        </div>
      </div>

      <div className="cb-field">
        <label>Output title (optional)</label>
        <input type="text" value={title}
          onChange={e => setTitle(e.target.value)}
          placeholder="Defaults to the prompt text"
          disabled={running} />
      </div>

      <div className="cb-btn-row">
        <button className="cb-btn primary" onClick={startBuild}
          disabled={running || !indexPresent || !prompt.trim() || !blueprint}>
          {running ? 'Building…' : 'Generate .md'}
        </button>
        {running && (
          <button className="cb-btn secondary"
            onClick={() => { stopRef.current?.(); setRunning(false) }}>
            Cancel
          </button>
        )}
        {resultMd && !running && (
          <button className="cb-btn secondary" onClick={download}>⬇ Download .md</button>
        )}
      </div>

      {!indexPresent && (
        <p style={{ fontSize: '0.82rem', color: '#888', margin: 0 }}>
          Index the corpus first before building.
        </p>
      )}
      {indexPresent && !blueprint && !running && (
        <p style={{ fontSize: '0.82rem', color: '#888', margin: 0 }}>
          Select a blueprint to enable generation.
        </p>
      )}

      {log.length > 0 && (
        <div className="cb-log" ref={logRef}>
          {log.map((l, i) => (
            <p key={i} className={`cb-log-line ${l.kind !== 'progress' ? l.kind : ''}`}>
              {l.text}
            </p>
          ))}
        </div>
      )}

      {resultMd && !running && (
        <div className="cb-result">
          <span style={{ fontSize: '0.8rem', color: '#555' }}>
            Preview ({(resultMd.length / 1024).toFixed(1)} KB) — upload this .md in OpenMAIC
          </span>
          <div className="cb-result-preview">{resultMd.slice(0, 1500)}{resultMd.length > 1500 ? '\n…' : ''}</div>
        </div>
      )}
    </div>
  )
}
