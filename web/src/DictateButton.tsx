// 🎤 Dictation chip. The mirror of <SpeakButton>: a rail drops one next to any field it
// wants speakable, and gets the transcript back as a plain callback.
//
// LIVE, and local-first, with a guaranteed floor. Paths, best → fallback:
//   1. Browser SpeechRecognition ON-DEVICE (Chrome 139+: `available()/install()` +
//      `processLocally = true`). Word-by-word live AND on-device — nothing leaves the box, no
//      server load. Preferred for EVERY rail when the language pack is present.
//   2. Browser SpeechRecognition CLOUD (older Chrome / pack not yet installed). Live, but the
//      mic audio goes to Google's/Microsoft's cloud STT, so it is OFF unless a call site opts in
//      with `allowCloudStt`. No call site does today. Meanwhile install() is kicked off so
//      on-device is ready next time.
//   3. Local broker faster-whisper — the on-device floor: segment-streaming (a phrase per pause)
//      then batch. Always available, so if the browser API changes or Google throttles it, or
//      there is no on-device pack, dictation still works.
//
// THE DEFAULT IS LOCAL. This was `localOnly`, opt-IN, and the comment here used to claim cloud
// was used "NOT on localOnly rails (edu / IEP / finance)". One of seven call sites passed it.
// The guard worked; it was simply not switched on where the comment said it was. Inverting it
// makes forgetting cost a slower local transcription instead of audio leaving the box.
// The decision itself lives in ./dictate_policy, where it can be tested.
// Interim words show beside the button; finalized phrases go to the field via `onText` (append).
import { useCallback, useEffect, useRef, useState } from 'react'
import { platformApi } from './platformApi'
import { canRecord, record, recordStreaming, type Recorder } from './voice'
import { chooseSttPath, type SttAvailability } from './dictate_policy'

// The Web Speech API is not in the TS DOM lib (and the on-device statics are newer still), so
// type it here. `available`/`install` are Chrome 139+; guarded with typeof before use.
type SRInstance = {
  lang: string; continuous: boolean; interimResults: boolean; processLocally?: boolean
  start: () => void; stop: () => void; abort: () => void
  onresult: ((e: { resultIndex: number; results: ArrayLike<{ isFinal: boolean; 0: { transcript: string } }> }) => void) | null
  onerror: ((e: { error: string }) => void) | null
  onend: (() => void) | null
}
type SROpts = { langs: string[]; processLocally?: boolean }
type SRCtor = (new () => SRInstance) & {
  available?: (o: SROpts) => Promise<string>
  install?: (o: SROpts) => Promise<boolean>
}
function getSRCtor(): SRCtor | null {
  const g = globalThis as unknown as { SpeechRecognition?: SRCtor; webkitSpeechRecognition?: SRCtor }
  return g.SpeechRecognition || g.webkitSpeechRecognition || null
}
const toBcp47 = (lang?: string) =>
  !lang ? 'en-US' : lang.includes('-') ? lang : `${lang}-${lang.toUpperCase()}`

export interface DictateButtonProps {
  /** Called with each finalized phrase. The rail decides where it goes (these callers append). */
  onText: (text: string) => void
  /** ISO/BCP-47 hint, e.g. 'es' / 'es-MX'. Omit for en-US. */
  language?: string
  /** Opt IN to cloud browser STT when on-device is unavailable. Omitted (the default) means
   *  audio never leaves the box: on-device browser STT if present, else the local broker.
   *  On-device is local, so it is used regardless of this flag. */
  allowCloudStt?: boolean
  /** Hard cap on one dictation session. */
  maxMs?: number
  small?: boolean
  label?: string
  className?: string
  onError?: (message: string) => void
}

type Session = { next: number; results: Record<number, string>; inflight: number; recording: boolean }

export function DictateButton({
  onText, language, allowCloudStt, maxMs, small, label, className, onError,
}: DictateButtonProps) {
  const [state, setState] = useState<'idle' | 'listening' | 'working'>('idle')
  const [note, setNote] = useState('')
  const recorder = useRef<Recorder | null>(null)   // local (broker) path handle
  const sr = useRef<SRInstance | null>(null)        // browser path handle
  const alive = useRef(true)

  // Set on mount as well as cleared on unmount: StrictMode's dev double-invoke
  // (mount → cleanup → remount) would otherwise leave this false forever.
  useEffect(() => {
    alive.current = true
    return () => {
      alive.current = false
      recorder.current?.cancel(); recorder.current = null
      try { sr.current?.abort() } catch { /* already gone */ } ; sr.current = null
    }
  }, [])

  const fail = useCallback((msg: string) => {
    if (!alive.current) return
    setNote(msg); onError?.(msg)
    window.setTimeout(() => alive.current && setNote(''), 4000)
  }, [onError])

  // --- local broker path (the floor): segment-streaming, then batch --------------------------
  const sendWhole = useCallback(async (audioB64: string, suffix: string) => {
    setState('working')
    try {
      const out = await platformApi.transcribe({ audio_b64: audioB64, suffix, language })
      if (!alive.current) return
      const text = (out.text || '').trim()
      if (text) onText(text); else fail('nothing heard')
    } catch { fail('local dictation unavailable') }
    finally { if (alive.current) setState('idle') }
  }, [language, onText, fail])

  const startLocal = useCallback(async () => {
    const s: Session = { next: 0, results: {}, inflight: 0, recording: true }
    const flush = () => {
      while (s.results[s.next] !== undefined) {
        const t = s.results[s.next]; delete s.results[s.next]; s.next++
        if (t && alive.current) onText(t)
      }
    }
    const settle = () => { if (!s.recording && s.inflight === 0 && alive.current) setState('idle') }
    const transcribeSeg = async (b64: string, suffix: string, idx: number) => {
      s.inflight++
      try { const out = await platformApi.transcribe({ audio_b64: b64, suffix, language }); s.results[idx] = (out.text || '').trim() }
      catch { s.results[idx] = '' }
      finally { s.inflight--; if (alive.current) { flush(); settle() } }
    }
    const live = await recordStreaming({
      maxMs: maxMs ?? 120000,
      onSegment: (b64, suffix, idx) => void transcribeSeg(b64, suffix, idx),
      onError: (m) => { recorder.current = null; s.recording = false; if (alive.current) setState('idle'); fail(m) },
    })
    if (live) {
      if (!alive.current) { live.cancel(); return }
      recorder.current = {
        stop: () => { s.recording = false; live.stop(); if (alive.current) setState(s.inflight ? 'working' : 'idle') },
        cancel: () => { s.recording = false; live.cancel() },
      }
      setState('listening'); return
    }
    const r = await record({
      maxMs: maxMs ?? 60000,
      onReady: (b64, suffix) => { recorder.current = null; void sendWhole(b64, suffix) },
      onError: (m) => { recorder.current = null; if (alive.current) setState('idle'); fail(m) },
    })
    if (!r || !alive.current) { r?.cancel(); return }
    recorder.current = r; setState('listening')
  }, [language, maxMs, onText, fail, sendWhole])

  // --- browser SpeechRecognition (on-device preferred, cloud where allowed) ------------------
  // Returns true if it started a browser session; false ⇒ caller falls back to the local broker.
  const tryBrowserSTT = useCallback(async (): Promise<boolean> => {
    const Ctor = getSRCtor()
    if (!Ctor) return false
    const lang = toBcp47(language)

    // Ask the browser what it can do on-device, then let ./dictate_policy decide. The choice is
    // out of here so it can be tested; what stays is the side effect (kicking off install()).
    let availability: SttAvailability = null
    try {
      if (typeof Ctor.available === 'function') {
        // The shim types available() as Promise<string> because the spec is still moving and a
        // browser may report a value we have not seen. Narrowing here rather than widening the
        // shim keeps every unknown value in the same bucket as 'unavailable', which
        // chooseSttPath already treats as "not on-device" -- the safe side.
        availability = await Ctor.available(
          { langs: [lang], processLocally: true }) as SttAvailability
        if ((availability === 'downloadable' || availability === 'downloading')
            && typeof Ctor.install === 'function') {
          // Not ready on-device. Fetch the pack for next time (non-blocking).
          void Ctor.install({ langs: [lang], processLocally: true }).catch(() => {})
        }
      }
    } catch { availability = 'unavailable' }

    const path = chooseSttPath({ hasCtor: true, availability, allowCloudStt })
    if (path === 'broker') return false   // caller falls through to faster-whisper
    const processLocally = path === 'ondevice'

    const rec = new Ctor()
    rec.lang = lang
    rec.continuous = true
    rec.interimResults = true
    if (processLocally) { try { rec.processLocally = true } catch { /* property may be readonly on older builds */ } }

    rec.onresult = (e) => {
      let interim = ''
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const r = e.results[i]
        if (r.isFinal) { const f = r[0].transcript.trim(); if (f && alive.current) onText(f) }
        else interim += r[0].transcript
      }
      if (alive.current) setNote(interim ? `… ${interim.trim()}` : '')
    }
    rec.onerror = (e) => {
      const err = e.error
      // Any pre-result failure (no pack, no network to the cloud service, blocked) ⇒ fall to
      // the local broker so dictation still works.
      if (err === 'language-not-supported' || err === 'network' || err === 'service-not-allowed' || err === 'not-allowed') {
        if (sr.current === rec) sr.current = null
        void startLocal()
        return
      }
      if (err !== 'aborted' && err !== 'no-speech') fail(`dictation: ${err}`)
    }
    rec.onend = () => { if (sr.current === rec) { sr.current = null; if (alive.current) { setNote(''); setState('idle') } } }

    try { rec.start() } catch { return false }
    sr.current = rec
    setState('listening')
    return true
  }, [language, allowCloudStt, onText, fail, startLocal])

  const toggle = useCallback(async () => {
    if (state === 'working') return
    if (state === 'listening') {
      if (sr.current) { try { sr.current.stop() } catch { /* */ } sr.current = null; setNote(''); setState('idle') }
      else { recorder.current?.stop(); recorder.current = null }
      return
    }
    setNote('')
    if (await tryBrowserSTT()) return
    await startLocal()
  }, [state, tryBrowserSTT, startLocal])

  if (!canRecord() && !getSRCtor()) return null

  const text = label ?? (state === 'listening' ? 'Stop & insert'
    : state === 'working' ? 'Transcribing…' : 'Dictate')

  return (
    <span className="vc-dict-wrap">
      <button
        type="button"
        className={`vc-speak${small ? ' sm' : ''}${state === 'listening' ? ' rec' : ''}${className ? ` ${className}` : ''}`}
        onClick={toggle}
        disabled={state === 'working'}
        title={state === 'listening' ? 'Listening — text appears as you speak; click to stop' : 'Dictate into this field'}
        aria-label={state === 'listening' ? 'Stop recording' : 'Dictate'}
      >
        <span aria-hidden="true">{state === 'working' ? '…' : state === 'listening' ? '⏹' : '🎤'}</span>
        {label !== '' && <span className="vc-speak-l">{text}</span>}
      </button>
      {note && <span className="vc-note" role="status">{note}</span>}
    </span>
  )
}

export default DictateButton
