// Read-aloud chip for a rail. Takes the text EXPLICITLY.
//
// WHY THE TEXT IS A PROP AND NOT SCRAPED
// A shell-level "read the page" button has to guess what to read, and guessing means walking the
// DOM — which picks up nav labels, button text, badge counts and chip captions, producing a
// recitation of the furniture. Only the rail knows which block it means: the answer, not the
// answer plus the sidebar. So the shell owns dictation (one field, wherever focus is) and the
// rail owns read-aloud (one passage, named by the rail).
//
// Reads the user's saved voice/speed prefs, so the voice matches everywhere without a rail
// knowing they exist. With no prefs saved it sends neither, and the broker applies the platform
// default — changeable for everyone from broker settings, no rebuild.

import { useCallback, useEffect, useRef, useState } from 'react'
import { loadVoicePrefs, VOICES } from './VoiceControls'
import { isSpeaking, playWav, speakable, speakLocal, stopSpeaking, synthesize } from './voice'

export interface SpeakButtonProps {
  /** The text to read. Markdown is fine — it is flattened before synthesis. */
  text: string
  /** Optional label beside the icon. */
  label?: string
  className?: string
  /**
   * Fall back to on-device speechSynthesis if the broker fails. Safe for OUTPUT (nothing leaves
   * the machine, and the text is already on screen) — unlike dictation, which has no fallback.
   */
  localFallback?: boolean
  title?: string
}

export function SpeakButton({
  text,
  label,
  className,
  localFallback = true,
  title,
}: SpeakButtonProps) {
  const [on, setOn] = useState(false)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const live = useRef(true)

  useEffect(() => {
    live.current = true
    return () => {
      live.current = false
      // Stop on unmount, or navigating away leaves a disembodied voice reading the old rail.
      if (isSpeaking()) stopSpeaking()
    }
  }, [])

  const click = useCallback(async () => {
    if (on || busy) {
      stopSpeaking()
      setOn(false)
      setBusy(false)
      return
    }
    const body = speakable(text)
    if (!body) return
    setErr('')
    setBusy(true)
    const prefs = loadVoicePrefs()
    // Voice and language are sent as a PAIR, resolved from the voice id — never one alone.
    const meta = VOICES.find((v) => v.id === prefs.voice)
    try {
      const out = await synthesize(body, {
        ...(meta ? { voice: meta.id, langCode: meta.langCode } : {}),
        ...(prefs.speed && prefs.speed !== 1 ? { speed: prefs.speed } : {}),
      })
      if (!live.current) return
      setBusy(false)
      setOn(true)
      await playWav(out.audio_b64, () => live.current && setOn(false))
    } catch (e) {
      if (!live.current) return
      setBusy(false)
      if (localFallback && speakLocal(body, () => live.current && setOn(false))) {
        // Say which path is speaking: the voice will sound different from everyone else's, and
        // silently substituting it invites "why does this rail sound wrong" with no answer.
        setOn(true)
        setErr('broker voice unavailable — using this device')
        return
      }
      setOn(false)
      setErr(e instanceof Error ? e.message : String(e))
    }
  }, [on, busy, text, localFallback])

  return (
    <button
      className={`speak-btn${on ? ' on' : ''}${busy ? ' busy' : ''} ${className ?? ''}`}
      onClick={click}
      disabled={!text?.trim()}
      title={err || title || (on ? 'Stop' : 'Read aloud')}
      aria-pressed={on}
    >
      {busy ? '…' : on ? '⏹' : '🔊'}
      {label ? <span className="speak-lbl">{label}</span> : null}
    </button>
  )
}
