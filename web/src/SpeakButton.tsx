// 🔊 Read-aloud chip. A rail drops one under any block of text worth hearing.
//
// Takes the text EXPLICITLY rather than scraping the DOM. A global "read the page" button
// has to guess where the content is and inevitably reads navigation and button labels; the
// rail already knows which string it means, so it says so.
//
// Safe to place anywhere: /api/platform/tts_light is ungated on the broker (CPU/ONNX, no
// eviction), so pressing this mid-conversation cannot displace the model in use.
import { useCallback, useEffect, useRef, useState } from 'react'
import { platformApi } from './platformApi'
import { resolveSpeech } from './speech_lang'
import { playWav, speakable, stopSpeaking } from './voice'

const LANG_KEY = 'platform-voice-lang'
const VOICE_KEY = 'platform-voice-voice'
const SPEED_KEY = 'platform-voice-speed'

const lsGet = (k: string, fb: string) => {
  try { return localStorage.getItem(k) || fb } catch { return fb }
}

export interface SpeakButtonProps {
  /** The text to read. Markdown is stripped before synthesis. */
  text: string
  /** Kokoro voice id. Defaults to whatever the top-bar settings chose. */
  voice?: string
  /** Kokoro language letter: 'a' American English, 'b' British, 'e' Spanish. */
  langCode?: string
  /** Compact variant for tight rows. */
  small?: boolean
  label?: string
  className?: string
}

export function SpeakButton({ text, voice, langCode, small, label, className }: SpeakButtonProps) {
  const [state, setState] = useState<'idle' | 'loading' | 'speaking'>('idle')
  const alive = useRef(true)

  // Set on mount as well as cleared on unmount: StrictMode's dev double-invoke
  // (mount → cleanup → remount) would otherwise leave this false forever.
  useEffect(() => {
    alive.current = true
    return () => { alive.current = false; stopSpeaking() }
  }, [])

  const onClick = useCallback(async () => {
    if (state === 'speaking' || state === 'loading') { stopSpeaking(); setState('idle'); return }
    const body = speakable(text || '')
    if (!body) return

    setState('loading')
    // The voice and language are RESOLVED, not passed straight through. Sending neither let
    // the broker default to American English over Spanish text, which read the Spanish with
    // English phonemes — see speech_lang.ts. No call site passes langCode, so this is the
    // only thing standing between Spanish content and that output.
    const pick = resolveSpeech(body, {
      voice,
      langCode,
      storedVoice: lsGet(VOICE_KEY, ''),
      storedLang: lsGet(LANG_KEY, ''),
    })
    try {
      const out = await platformApi.ttsLight({
        text: body,
        voice: pick.voice,
        lang_code: pick.lang_code,
        speed: Number(lsGet(SPEED_KEY, '1')) || undefined,
      })
      if (!alive.current) return
      setState('speaking')
      playWav(out.audio_b64, {
        text: body,
        lang: out.lang,
        onEnd: () => { if (alive.current) setState('idle') },
      })
    } catch {
      // The broker path is down (no media worker, or Kokoro unconfigured). Read-aloud may
      // fall back to on-device speechSynthesis — unlike dictation, this is local and the
      // text is already on screen, so there is nothing to leak.
      if (!alive.current) return
      setState('speaking')
      // The resolved language, not the prop: on-device speechSynthesis picks an es-MX voice
      // from this, so the degraded path had the same English-over-Spanish bug as the main one.
      playWav('', { text: body, lang: pick.lang_code === 'e' ? 'es' : 'en',
                    onEnd: () => { if (alive.current) setState('idle') } })
    }
  }, [state, text, voice, langCode])

  const busy = state !== 'idle'
  return (
    <button
      type="button"
      className={`vc-speak${small ? ' sm' : ''}${busy ? ' on' : ''}${className ? ` ${className}` : ''}`}
      onClick={onClick}
      disabled={!text?.trim()}
      title={busy ? 'Stop' : 'Read aloud'}
      aria-label={busy ? 'Stop reading' : 'Read aloud'}
    >
      <span aria-hidden="true">{state === 'loading' ? '…' : busy ? '⏹' : '🔊'}</span>
      {label !== '' && <span className="vc-speak-l">{label ?? (busy ? 'Stop' : 'Read aloud')}</span>}
    </button>
  )
}

export default SpeakButton
