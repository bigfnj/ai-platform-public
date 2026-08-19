// FALLBACK dictation: one 🎤 in the shell top bar that types into whatever rail field has focus,
// plus the settings popover (voice / speed / devices) that every voice surface reads its prefs
// from.
//
// PREFER DictateButton. A per-rail chip takes an onTranscript callback and hands the text to the
// rail's own state setter, so it never writes into a field it does not own. This component has to
// fake typing into a foreign React-controlled input, and everything below the "injecting" heading
// exists only for that: the prototype value setter (React's value tracker swallows a plain
// assignment and never fires onChange), the capturing focusin listener (activeElement is already
// the mic button by click time), onMouseDown preventDefault (clicking would blur the field and
// fire any save-on-blur), and the requestAnimationFrame caret restore (React's re-render sends
// the caret to the end). Four workarounds, each silent when it breaks.
//
// It stays because it is the only thing that works for a rail that has not adopted a chip — one
// control covering every rail at once, which is possible only because federated remotes share
// this document. Nothing depends on it.
//
// Read-aloud is per-rail for a different reason: only the rail knows WHICH passage it means. A
// shell-level "read the page" button reads nav and button labels. See SpeakButton.

import { useCallback, useEffect, useRef, useState } from 'react'
import {
  applyOutputDevice,
  audioDevices,
  canRecord,
  record,
  setAudioInput,
  setAudioOutput,
  stopSpeaking,
  type Recorder,
} from './voice'

const LS_KEY = 'platform-voice-prefs'

// Kokoro voices, grouped by the language their prefix implies. Voice and language always travel
// together here — picking a voice sets its lang code, because a Spanish voice under the English
// phonemiser produces noise rather than an accent.
export const VOICES: { id: string; label: string; langCode: string; lang: string }[] = [
  { id: 'af_heart', label: 'Heart — American female', langCode: 'a', lang: 'en' },
  { id: 'af_bella', label: 'Bella — American female', langCode: 'a', lang: 'en' },
  { id: 'am_michael', label: 'Michael — American male', langCode: 'a', lang: 'en' },
  { id: 'am_fenrir', label: 'Fenrir — American male', langCode: 'a', lang: 'en' },
  { id: 'bf_emma', label: 'Emma — British female', langCode: 'b', lang: 'en' },
  { id: 'bm_george', label: 'George — British male', langCode: 'b', lang: 'en' },
  { id: 'ef_dora', label: 'Dora — Spanish female', langCode: 'e', lang: 'es' },
  { id: 'em_alex', label: 'Alex — Spanish male', langCode: 'e', lang: 'es' },
]

export interface VoicePrefs {
  voice: string
  speed: number
  /** STT language hint; '' = let whisper detect. */
  language: string
  inputId: string | null
  outputId: string | null
}

const DEFAULTS: VoicePrefs = {
  // Empty means "use the platform default", which is a BROKER setting — so an admin can change
  // the default voice for everyone without a rebuild, and a user who never opened this popover
  // follows it. Pinning a value here would silently override that for every new user.
  voice: '',
  speed: 1,
  language: '',
  inputId: null,
  outputId: null,
}

export function loadVoicePrefs(): VoicePrefs {
  try {
    const raw = globalThis.localStorage?.getItem(LS_KEY)
    return raw ? { ...DEFAULTS, ...(JSON.parse(raw) as Partial<VoicePrefs>) } : { ...DEFAULTS }
  } catch {
    return { ...DEFAULTS }
  }
}

function savePrefs(p: VoicePrefs): void {
  try {
    globalThis.localStorage?.setItem(LS_KEY, JSON.stringify(p))
  } catch {
    /* private mode / quota — prefs just don't persist */
  }
}

// --- injecting into a React-controlled field --------------------------------

type Field = HTMLInputElement | HTMLTextAreaElement

/**
 * Write `next` into a React-controlled field so React actually notices.
 *
 * React attaches a value TRACKER to the node and overrides the node's own `value` setter.
 * Assigning `el.value = x` goes through that override: the tracker records the new value and
 * then concludes "nothing changed", so onChange never fires. The text sits visibly in the box
 * while the rail's state still holds the old value — the field looks dictated-into and saving
 * writes nothing. Calling the PROTOTYPE setter bypasses the override, then a bubbling 'input'
 * event drives React's synthetic onChange.
 */
function setFieldValue(el: Field, next: string): void {
  const proto =
    el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype
  const desc = Object.getOwnPropertyDescriptor(proto, 'value')
  desc?.set?.call(el, next)
  el.dispatchEvent(new Event('input', { bubbles: true }))
}

function isEditable(el: Element | null): el is Field {
  if (!el) return false
  if (el instanceof HTMLTextAreaElement) return !el.disabled && !el.readOnly
  if (el instanceof HTMLInputElement) {
    const ok = ['text', 'search', 'url', 'email', 'tel', 'number', '']
    return !el.disabled && !el.readOnly && ok.includes(el.type)
  }
  return false
}

/** Fields the shell owns are not dictation targets — only rail content is. */
function isOwnChrome(el: Element): boolean {
  return !!el.closest('[data-voice-ignore]')
}

export interface VoiceControlsProps {
  /** Rendered when dictation is unavailable. Default: a disabled mic with a reason. */
  unavailableTitle?: string
  className?: string
}

export function VoiceControls({ unavailableTitle, className }: VoiceControlsProps = {}) {
  const [prefs, setPrefs] = useState<VoicePrefs>(loadVoicePrefs)
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [listening, setListening] = useState(false)
  const [err, setErr] = useState('')
  const [devices, setDevices] = useState<{ inputs: MediaDeviceInfo[]; outputs: MediaDeviceInfo[] }>({
    inputs: [],
    outputs: [],
  })
  const recorder = useRef<Recorder | null>(null)
  // The field to dictate into, captured while it still HAS focus.
  const target = useRef<Field | null>(null)
  const popover = useRef<HTMLDivElement | null>(null)

  // Restore saved device choices into the voice module (it owns them at capture time).
  useEffect(() => {
    setAudioInput(prefs.inputId)
    setAudioOutput(prefs.outputId)
  }, [prefs.inputId, prefs.outputId])

  useEffect(() => {
    savePrefs(prefs)
  }, [prefs])

  /**
   * Track the focused field with a CAPTURING focusin listener.
   *
   * Reading document.activeElement when the mic is clicked is too late — by then focus has
   * moved to the button (or to the popover), so the transcript would have nowhere to go. Capture
   * phase also means we still see the event when a rail stops its propagation.
   */
  useEffect(() => {
    const onFocusIn = (e: FocusEvent) => {
      const el = e.target as Element | null
      if (el && isEditable(el) && !isOwnChrome(el)) target.current = el
    }
    document.addEventListener('focusin', onFocusIn, true)
    return () => document.removeEventListener('focusin', onFocusIn, true)
  }, [])

  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      if (popover.current && !popover.current.contains(e.target as Node)) setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false)
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  useEffect(() => {
    if (open) audioDevices().then(setDevices).catch(() => {})
  }, [open])

  useEffect(() => () => recorder.current?.cancel(), [])

  /** Insert at the selection and restore the caret after React re-renders. */
  const insert = useCallback((text: string) => {
    const el = target.current
    if (!el || !text) return
    const start = el.selectionStart ?? el.value.length
    const end = el.selectionEnd ?? start
    const before = el.value.slice(0, start)
    const after = el.value.slice(end)
    // A space if we're continuing a sentence, so dictating twice doesn't produce "wordword".
    const pad = before && !/\s$/.test(before) ? ' ' : ''
    const next = before + pad + text + after
    const caret = start + pad.length + text.length
    setFieldValue(el, next)
    // React re-renders from its own state and resets the caret to the end; restoring it in the
    // same tick would be overwritten, so wait one frame.
    requestAnimationFrame(() => {
      try {
        el.focus()
        el.setSelectionRange(caret, caret)
      } catch {
        /* element unmounted mid-dictation */
      }
    })
  }, [])

  const start = useCallback(async () => {
    setErr('')
    if (!target.current) {
      setErr('Click into a text field first, then dictate.')
      return
    }
    try {
      recorder.current = await record(prefs.language || undefined)
      setListening(true)
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    }
  }, [prefs.language])

  const finish = useCallback(async () => {
    const rec = recorder.current
    if (!rec) return
    recorder.current = null
    setListening(false)
    setBusy(true)
    try {
      const out = await rec.stop()
      insert(out.text.trim())
    } catch (e) {
      // Broker down means dictation is DOWN. There is deliberately no browser-speech fallback:
      // it would ship the microphone to a cloud service without saying so. See voice.ts.
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }, [insert])

  const toggle = useCallback(() => {
    if (listening) void finish()
    else void start()
  }, [listening, finish, start])

  if (!canRecord()) {
    return (
      <button
        className={`voice-mic off ${className ?? ''}`}
        disabled
        title={unavailableTitle ?? 'This browser cannot record audio'}
        data-voice-ignore
      >
        🎤
      </button>
    )
  }

  const voiceMeta = VOICES.find((v) => v.id === prefs.voice)

  return (
    <span className={`voice-wrap ${className ?? ''}`} data-voice-ignore>
      <button
        className={`voice-mic${listening ? ' on' : ''}${busy ? ' busy' : ''}`}
        // Without this, clicking the mic blurs the field being dictated into. The capturing
        // focusin listener would still hold the right target, but the user watches their caret
        // vanish — and any rail that saves on blur would fire mid-dictation.
        onMouseDown={(e) => e.preventDefault()}
        onClick={toggle}
        title={listening ? 'Stop and insert' : 'Dictate into the focused field'}
        aria-pressed={listening}
      >
        {busy ? '…' : '🎤'}
      </button>
      <button
        className="voice-gear"
        onMouseDown={(e) => e.preventDefault()}
        onClick={() => setOpen((o) => !o)}
        title="Voice settings"
      >
        ⚙
      </button>

      {err && (
        <span className="voice-err" role="status" title={err}>
          {err}
        </span>
      )}

      {open && (
        <div className="voice-pop" ref={popover} role="dialog" aria-label="Voice settings">
          <label className="voice-row">
            <span>Voice</span>
            <select
              value={prefs.voice}
              onChange={(e) => setPrefs((p) => ({ ...p, voice: e.target.value }))}
            >
              <option value="">Platform default</option>
              {VOICES.map((v) => (
                <option key={v.id} value={v.id}>
                  {v.label}
                </option>
              ))}
            </select>
          </label>

          <label className="voice-row">
            <span>Speed</span>
            <input
              type="range"
              min={0.6}
              max={1.6}
              step={0.05}
              value={prefs.speed}
              onChange={(e) => setPrefs((p) => ({ ...p, speed: Number(e.target.value) }))}
            />
            <em>{prefs.speed.toFixed(2)}×</em>
          </label>

          <label className="voice-row">
            <span>Dictation language</span>
            <select
              value={prefs.language}
              onChange={(e) => setPrefs((p) => ({ ...p, language: e.target.value }))}
            >
              <option value="">Auto-detect</option>
              <option value="en">English</option>
              <option value="es">Spanish</option>
              <option value="fr">French</option>
            </select>
          </label>

          <label className="voice-row">
            <span>Microphone</span>
            <select
              value={prefs.inputId ?? ''}
              onChange={(e) => setPrefs((p) => ({ ...p, inputId: e.target.value || null }))}
            >
              <option value="">System default</option>
              {devices.inputs.map((d) => (
                <option key={d.deviceId} value={d.deviceId}>
                  {d.label || 'Microphone'}
                </option>
              ))}
            </select>
          </label>

          <label className="voice-row">
            <span>Speaker</span>
            <select
              value={prefs.outputId ?? ''}
              onChange={(e) => setPrefs((p) => ({ ...p, outputId: e.target.value || null }))}
            >
              <option value="">System default</option>
              {devices.outputs.map((d) => (
                <option key={d.deviceId} value={d.deviceId}>
                  {d.label || 'Speaker'}
                </option>
              ))}
            </select>
          </label>

          {!devices.inputs.some((d) => d.label) && (
            <p className="voice-note">
              Device names appear after you allow microphone access once — the browser withholds
              them until then.
            </p>
          )}
          {voiceMeta && (
            <p className="voice-note">
              {voiceMeta.label} speaks {voiceMeta.lang === 'es' ? 'Spanish' : 'English'}; the
              language is set with the voice so it is never phonemised by the wrong one.
            </p>
          )}
          <button className="voice-stop" onClick={() => stopSpeaking()}>
            Stop playback
          </button>
        </div>
      )}
    </span>
  )
}

export { applyOutputDevice }
