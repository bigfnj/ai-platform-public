import { useEffect, useState } from 'react'
import { RailHeader, ModelChips } from '@web-core'
import { fetchCapabilities, type Capabilities } from './api'
import IndexPanel from './components/IndexPanel'
import BuildPanel from './components/BuildPanel'
import './theme.css'

const POLL_MS = 6000

export default function CourseBuilderModule() {
  const [caps, setCaps] = useState<Capabilities | null>(null)
  const [capsTick, setCapsTick] = useState(0)

  function reload() { setCapsTick(t => t + 1) }

  useEffect(() => {
    let live = true
    const load = () =>
      fetchCapabilities()
        .then(c => { if (live) setCaps(c) })
        .catch(() => { /* keep last state */ })
    load()
    const id = window.setInterval(load, POLL_MS)
    return () => { live = false; window.clearInterval(id) }
  }, [capsTick])

  const indexStats = caps?.index ?? { present: false, chunks: 0, corpus: '', embed_role: '' }

  return (
    <div className="course-builder">
      <RailHeader
        icon="📚"
        title="Course Builder"
        subtitle="Point at a local markdown corpus, index it once, then generate a grounded course-source .md from a prompt. Upload the output to OpenMAIC."
        chips={
          <ModelChips
            status={caps ? (caps.broker === 'ok' ? 'ok' : 'unreachable') : 'checking'}
            models={caps?.models}
          />
        }
        actions={
          <>
            <span
              className={`cb-index-badge ${indexStats.present ? 'present' : 'missing'}`}
              title={indexStats.corpus || 'No corpus indexed'}
            >
              {indexStats.present
                ? `✓ ${indexStats.chunks.toLocaleString()} chunks`
                : '✗ No index'}
            </span>
            <button className="cb-btn secondary" style={{ marginLeft: 8 }} onClick={reload}>
              Refresh
            </button>
          </>
        }
      />

      <div className="cb-body">
        <IndexPanel
          indexPresent={indexStats.present}
          indexChunks={indexStats.chunks}
          indexCorpus={indexStats.corpus}
          onIndexChanged={reload}
        />
        <BuildPanel indexPresent={indexStats.present} />
      </div>
    </div>
  )
}
