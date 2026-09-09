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

  useEffect(() => () => { alive.current = false; stopSpeaking() }, [])

  const onClick = useCallback(async () => {
    if (state === 'speaking' || state === 'loading') { stopSpeaking(); setState('idle'); return }
    const body = speakable(text || '')
    if (!body) return

    setState('loading')
    try {
      const out = await platformApi.ttsLight({
        text: body,
        voice: voice || lsGet(VOICE_KEY, '') || undefined,
        lang_code: langCode || lsGet(LANG_KEY, '') || undefined,
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
      playWav('', { text: body, lang: langCode === 'e' ? 'es' : 'en',
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
