// Dictation chip for a rail. Hands the transcript to the rail via a callback.
//
// THIS IS THE PRIMARY DICTATION PATH, and the reason is worth stating plainly: it never touches
// the DOM. The rail passes `onTranscript` and wires it to its own state setter, so the text
// arrives through React the same way typing would.
//
// The alternative — one mic in the shell chrome that writes into whatever field has focus — has
// to fake typing into a foreign React-controlled input, and that requires four separate
// workarounds, every one of which is a silent-failure mode:
//
//   * React overrides the node's `value` setter and attaches a value tracker, so `el.value = x`
//     records the new value and then concludes nothing changed. onChange never fires: the text
//     sits visibly in the box while the rail's state still holds the old value, so the field
//     looks dictated-into and saving writes nothing. Bypassing it means reaching for the
//     PROTOTYPE descriptor's setter.
//   * The target has to be captured on `focusin` (capture phase), because by click time focus
//     has moved to the mic button.
//   * The button needs onMouseDown preventDefault, or clicking it blurs the field — firing any
//     save-on-blur the rail has.
//   * The caret has to be restored in a requestAnimationFrame, or React's re-render sends it to
//     the end.
//
// A callback has none of that. VoiceControls still exists as a fallback for rails that have not
// adopted a chip, which is why that machinery is still in the tree — but nothing depends on it,
// and a rail should prefer this.

import { useCallback, useEffect, useRef, useState } from 'react'
import { loadVoicePrefs } from './VoiceControls'
import { canRecord, record, type Recorder } from './voice'

export interface DictateButtonProps {
  /**
   * Receives the transcript. Wire this to the rail's own setter, e.g.
   * `onTranscript={(t) => setQuery((q) => (q ? q + ' ' + t : t))}`.
   *
   * The rail decides whether to append, replace, or insert — it is the only party that knows.
   */
  onTranscript: (text: string) => void
  /** STT language hint ('en', 'es'…). Omit to use the user's saved preference, then auto-detect. */
  language?: string
  label?: string
  className?: string
  title?: string
  disabled?: boolean
}

export function DictateButton({
  onTranscript,
  language,
  label,
  className,
  title,
  disabled,
}: DictateButtonProps) {
  const [listening, setListening] = useState(false)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const recorder = useRef<Recorder | null>(null)
  const live = useRef(true)

  useEffect(() => {
    live.current = true
    return () => {
      live.current = false
      // Abandon capture on unmount rather than leaving the mic hot on a rail nobody is looking at.
      recorder.current?.cancel()
      recorder.current = null
    }
  }, [])

  const click = useCallback(async () => {
    if (busy) return
    if (listening) {
      const rec = recorder.current
      recorder.current = null
      setListening(false)
      if (!rec) return
      setBusy(true)
      try {
        const out = await rec.stop()
        if (!live.current) return
        const text = out.text.trim()
        if (text) onTranscript(text)
        setErr('')
      } catch (e) {
        // Broker down means dictation is DOWN. There is deliberately no browser-speech
        // fallback: SpeechRecognition streams the microphone to a cloud service, and falling
        // back to it silently would exfiltrate exactly the audio that must stay local.
        if (live.current) setErr(e instanceof Error ? e.message : String(e))
      } finally {
        if (live.current) setBusy(false)
      }
      return
    }
    setErr('')
    try {
      const lang = language ?? loadVoicePrefs().language ?? ''
      recorder.current = await record(lang || undefined)
      if (!live.current) {
        recorder.current.cancel()
        recorder.current = null
        return
      }
      setListening(true)
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    }
  }, [busy, listening, language, onTranscript])

  if (!canRecord()) return null

  return (
    <button
      type="button"
      className={`dictate-btn${listening ? ' on' : ''}${busy ? ' busy' : ''} ${className ?? ''}`}
      onClick={click}
      disabled={disabled || busy}
      title={err || title || (listening ? 'Stop and insert' : 'Dictate')}
      aria-pressed={listening}
    >
      {busy ? '…' : listening ? '⏹' : '🎤'}
      {label ? <span className="dictate-lbl">{label}</span> : null}
    </button>
  )
}
