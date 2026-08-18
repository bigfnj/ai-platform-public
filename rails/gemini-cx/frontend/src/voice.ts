/**
 * Client-side playback for "Read aloud". Adapted from the smb-partner-enablement rail, trimmed
 * to what this rail actually uses: playback only. SMB also carries `listen()` and audio-device
 * pickers for its voice-chat surface; this rail has no microphone input, so carrying that code
 * would be dead weight.
 *
 * Two paths, matching the backend seam in voice.py:
 *
 *  - a `broker` payload carries real Kokoro audio, played as a data URL.
 *  - anything else is spoken by the Web Speech API, which costs no GPU and therefore never
 *    evicts the resident answer model.
 *
 * Both degrade to silence rather than throwing: someone whose browser or device cannot do voice
 * should still get a working text app.
 */
import type { VoicePayload } from './types'

export const canSpeak = () => typeof globalThis.speechSynthesis !== 'undefined'

let current: HTMLAudioElement | null = null

/**
 * Pick the best available system voice for the browser fallback.
 *
 * This matters more than it looks. Web Speech defaults to the platform's *first* voice, which on
 * Windows is a legacy SAPI5 voice (David / Zira / Mark) — the flat robotic one. Sitting alongside
 * them, unused, are Microsoft's neural voices ("Microsoft Ava Online (Natural)" and friends) and,
 * in Chrome, Google's network voices. Both are dramatically better, and selecting one is free.
 *
 * Female names are ranked above the gender-neutral tiers so the fallback matches the female
 * Kokoro voice (af_heart) rather than switching gender when the broker is unavailable.
 */
const FEMALE_NAMES =
  /ava|emma|jenny|aria|michelle|elizabeth|sonia|libby|maisie|zira|samantha|victoria|karen|moira|veena/i

const VOICE_TIERS: ((v: SpeechSynthesisVoice) => boolean)[] = [
  (v) => FEMALE_NAMES.test(v.name) && /natural/i.test(v.name), // female Microsoft neural
  (v) => FEMALE_NAMES.test(v.name) && /online/i.test(v.name), // female Microsoft network
  (v) => FEMALE_NAMES.test(v.name), // any female-named voice
  (v) => /natural/i.test(v.name), // any Microsoft neural
  (v) => /online/i.test(v.name), // any Microsoft network voice
  (v) => /google/i.test(v.name), // Chrome network voices
  (v) => !/david|guy|mark|hazel/i.test(v.name), // anything but known-male/flat legacy
  () => true,
]

let chosen: SpeechSynthesisVoice | null = null

function pickVoice(lang: string): SpeechSynthesisVoice | null {
  if (!canSpeak()) return null
  // getVoices() is empty until the engine populates it, so this is re-resolved until it
  // succeeds rather than cached from a first empty call.
  const all = globalThis.speechSynthesis.getVoices()
  if (!all.length) return null
  if (chosen && all.includes(chosen)) return chosen
  const want = (lang || 'en-US').slice(0, 2).toLowerCase()
  const candidates = all.filter((v) => v.lang?.toLowerCase().startsWith(want))
  const pool = candidates.length ? candidates : all
  for (const tier of VOICE_TIERS) {
    const hit = pool.find(tier)
    if (hit) {
      chosen = hit
      return hit
    }
  }
  return pool[0] ?? null
}

if (typeof globalThis.speechSynthesis !== 'undefined') {
  // Chrome and Edge populate the voice list asynchronously; without this the first utterance of
  // a session gets the default voice even though a better one arrives moments later.
  globalThis.speechSynthesis.addEventListener?.('voiceschanged', () => {
    chosen = null
    pickVoice('en-US')
  })
}

export function stopSpeaking(): void {
  if (canSpeak()) globalThis.speechSynthesis.cancel()
  if (current) {
    current.pause()
    current = null
  }
}

function speakBrowser(payload: VoicePayload, onEnd?: () => void): void {
  if (!canSpeak()) {
    onEnd?.()
    return
  }
  const utterance = new SpeechSynthesisUtterance(payload.text)
  utterance.lang = payload.lang === 'en' ? 'en-US' : payload.lang
  const v = pickVoice(utterance.lang)
  if (v) utterance.voice = v
  utterance.rate = 0.95
  utterance.onend = () => onEnd?.()
  utterance.onerror = () => onEnd?.()
  globalThis.speechSynthesis.speak(utterance)
}

export function speak(payload: VoicePayload | undefined, onEnd?: () => void): void {
  if (!payload || payload.mode === 'off' || !payload.text) {
    onEnd?.()
    return
  }
  stopSpeaking()
  if (payload.mode === 'broker' && payload.audio_b64) {
    const audio = new Audio(`data:audio/wav;base64,${payload.audio_b64}`)
    current = audio
    audio.onended = () => {
      current = null
      onEnd?.()
    }
    audio.play().catch((err) => {
      // Autoplay blocked or a device error — fall back to browser speech so there is always
      // audio rather than a button that silently does nothing.
      console.warn('[voice] broker audio play() failed, falling back to browser TTS:', err)
      current = null
      speakBrowser(payload, onEnd)
    })
    return
  }
  speakBrowser(payload, onEnd)
}
