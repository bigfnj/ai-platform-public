import { useState, useRef, useEffect } from 'react'
import { postJSON, deleteJSON, streamEvents } from '../api'

interface Props {
  indexPresent: boolean
  indexChunks: number
  indexCorpus: string
  onIndexChanged: () => void
}

interface LogLine { text: string; kind: 'progress' | 'done' | 'error' }

export default function IndexPanel({ indexPresent, indexChunks, indexCorpus, onIndexChanged }: Props) {
  const [corpusPath, setCorpusPath] = useState(indexCorpus || '')
  const [resume, setResume] = useState(false)
  const [limit, setLimit] = useState(0)
  const [running, setRunning] = useState(false)
  const [log, setLog] = useState<LogLine[]>([])
  const logRef = useRef<HTMLDivElement>(null)
  const stopRef = useRef<(() => void) | null>(null)

  // Pre-fill corpus path from existing index when it loads.
  useEffect(() => {
    if (indexCorpus && !corpusPath) setCorpusPath(indexCorpus)
  }, [indexCorpus])

  // Auto-scroll log.
  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight
  }, [log])

  function appendLog(text: string, kind: LogLine['kind'] = 'progress') {
    setLog(prev => [...prev, { text, kind }])
  }

  async function startIndex() {
    if (!corpusPath.trim()) return
    setLog([])
    setRunning(true)
    try {
      await postJSON('/api/index/start', {
        corpus_path: corpusPath.trim(),
        resume,
        limit: limit || 0,
      })
      stopRef.current = streamEvents(
        '/api/index/events',
        (type, data) => {
          if (type === 'done') {
            appendLog('Index complete.', 'done')
            setRunning(false)
            onIndexChanged()
          } else if (type === 'error') {
            appendLog(data, 'error')
            setRunning(false)
          } else if (data) {
            appendLog(data)
          }
        },
        () => { setRunning(false); onIndexChanged() },
      )
    } catch (e) {
      appendLog(String(e), 'error')
      setRunning(false)
    }
  }

  async function dropIndex() {
    if (!confirm('Drop the current index? This cannot be undone.')) return
    await deleteJSON('/api/index')
    setLog([{ text: 'Index dropped.', kind: 'done' }])
    onIndexChanged()
  }

  return (
    <div className="cb-panel">
      <h2>📂 Corpus Index</h2>

      <div className="cb-field">
        <label>Corpus directory</label>
        <input
          type="text"
          value={corpusPath}
          onChange={e => setCorpusPath(e.target.value)}
          placeholder="C:\Users\you\servicenow-docs  or  ~/my-corpus"
          disabled={running}
        />
        <span style={{ fontSize: '0.75rem', color: '#888' }}>
          Any folder of .md files. Sub-directories are included recursively.
        </span>
      </div>

      <div className="cb-check-row">
        <input id="resume" type="checkbox" checked={resume}
          onChange={e => setResume(e.target.checked)} disabled={running} />
        <label htmlFor="resume">Resume (skip already-indexed paths)</label>
      </div>

      <div className="cb-field" style={{ maxWidth: 200 }}>
        <label>Limit (0 = all files)</label>
        <input
          type="number"
          min={0}
          value={limit}
          onChange={e => setLimit(parseInt(e.target.value) || 0)}
          disabled={running}
        />
      </div>

      <div className="cb-btn-row">
        <button className="cb-btn primary" onClick={startIndex}
          disabled={running || !corpusPath.trim()}>
          {running ? 'Indexing…' : 'Build Index'}
        </button>
        {running && (
          <button className="cb-btn secondary" onClick={() => { stopRef.current?.(); setRunning(false) }}>
            Cancel
          </button>
        )}
        {indexPresent && !running && (
          <button className="cb-btn danger" onClick={dropIndex}>Drop Index</button>
        )}
      </div>

      {indexPresent && !running && log.length === 0 && (
        <div style={{ fontSize: '0.82rem', color: '#555' }}>
          <strong>{indexChunks.toLocaleString()} chunks</strong> indexed
          {indexCorpus && <> from <code style={{ fontSize: '0.78rem' }}>{indexCorpus}</code></>}
        </div>
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
    </div>
  )
}
