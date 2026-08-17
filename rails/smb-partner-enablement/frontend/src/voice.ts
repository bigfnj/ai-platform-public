import type { VoicePayload } from './types'

/**
 * Client-side voice. Two halves, both browser-native:
 *
 *  - `speak()`  renders an answer. A broker payload carries real audio (XTTS); otherwise the
 *               Web Speech API synthesizes locally, which costs no GPU and therefore never
 *               evicts the resident RAG model.
 *  - `listen()` captures the question. This has no server-side counterpart at all — there is
 *               no STT model in the broker, so speech input is the browser's or it does not
 *               exist.
 *
 * Both degrade to silence rather than throwing: a partner who cannot use voice on their
 * device should still get a working text app.
 */

type SpeechRecognitionLike = {
  lang: string
  continuous: boolean
  interimResults: boolean
  start: () => void
  stop: () => void
  onresult: ((e: any) => void) | null
  onerror: ((e: any) => void) | null
  onend: (() => void) | null
}

const RecognitionCtor: (new () => SpeechRecognitionLike) | undefined =
  (globalThis as any).SpeechRecognition || (globalThis as any).webkitSpeechRecognition

export const canSpeak = () => typeof globalThis.speechSynthesis !== 'undefined'
export const canListen = () => RecognitionCtor !== undefined

let current: HTMLAudioElement | null = null

/**
 * Pick the best available system voice.
 *
 * This matters more than it looks. Web Speech defaults to the platform's *first* voice, which on
 * Windows is a legacy SAPI5 voice (David / Zira / Mark) — the flat robotic one. Sitting alongside
 * them, unused, are Microsoft's neural voices ("Microsoft Ava Online (Natural)" and friends) and,
 * in Chrome, Google's network voices. Both are dramatically better, and selecting one is free.
 *
 * This is a stopgap, not the destination: real voice quality for this rail means Kokoro through
 * the broker (see BACKLOG.md). Until that lands, this is the difference between a demo that
 * sounds broken and one that sounds acceptable.
 */
const VOICE_TIERS: ((v: SpeechSynthesisVoice) => boolean)[] = [
  (v) => /natural/i.test(v.name),          // Microsoft neural (Edge/Windows)
  (v) => /online/i.test(v.name),           // Microsoft network voices
  (v) => /google/i.test(v.name),           // Chrome network voices
  (v) => !/david|zira|mark|hazel/i.test(v.name), // anything but the known-flat legacy set
  () => true,
]

let chosen: SpeechSynthesisVoice | null = null

function pickVoice(lang: string): SpeechSynthesisVoice | null {
  if (!canSpeak()) return null
  // getVoices() is empty until the engine populates it, so this is re-resolved until it succeeds
  // rather than cached from a first empty call.
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

/** Names of the voices actually available, best first — useful when diagnosing bad playback. */
export function availableVoices(): string[] {
  if (!canSpeak()) return []
  const all = globalThis.speechSynthesis.getVoices()
  const best = pickVoice('en-US')
  return all
    .map((v) => (v === best ? `${v.name} (${v.lang}) ← selected` : `${v.name} (${v.lang})`))
    .sort((a) => (a.includes('← selected') ? -1 : 0))
}

if (typeof globalThis.speechSynthesis !== 'undefined') {
  // Chrome and Edge populate the voice list asynchronously; without this the first utterance of
  // a session gets the default voice even though a better one arrives moments later.
  globalThis.speechSynthesis.addEventListener?.('voiceschanged', () => {
    chosen = null
    pickVoice('en-US')
  })
}

export function stopSpeaking() {
  if (canSpeak()) globalThis.speechSynthesis.cancel()
  if (current) {
    current.pause()
    current = null
  }
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
    void audio.play().catch(() => onEnd?.())
    return
  }
  if (!canSpeak()) {
    onEnd?.()
    return
  }
  const utterance = new SpeechSynthesisUtterance(payload.text)
  utterance.lang = payload.lang === 'en' ? 'en-US' : payload.lang
  const voice = pickVoice(utterance.lang)
  if (voice) utterance.voice = voice
  // Slightly slower than default: the legacy voices in particular run too fast to follow when
  // reading a licensing recommendation aloud.
  utterance.rate = 0.95
  utterance.onend = () => onEnd?.()
  utterance.onerror = () => onEnd?.()
  globalThis.speechSynthesis.speak(utterance)
}

export type Listener = { stop: () => void }

export function listen(handlers: {
  onResult: (transcript: string, final: boolean) => void
  onError?: (detail: string) => void
  onEnd?: () => void
  lang?: string
}): Listener | null {
  if (!RecognitionCtor) {
    handlers.onError?.('speech recognition is not available in this browser')
    return null
  }
  const rec = new RecognitionCtor()
  rec.lang = handlers.lang || 'en-US'
  rec.continuous = false
  rec.interimResults = true
  rec.onresult = (e: any) => {
    let transcript = ''
    let final = false
    for (let i = e.resultIndex; i < e.results.length; i += 1) {
      transcript += e.results[i][0].transcript
      if (e.results[i].isFinal) final = true
    }
    handlers.onResult(transcript.trim(), final)
  }
  rec.onerror = (e: any) => handlers.onError?.(String(e?.error || 'recognition failed'))
  rec.onend = () => handlers.onEnd?.()
  rec.start()
  return { stop: () => rec.stop() }
}
