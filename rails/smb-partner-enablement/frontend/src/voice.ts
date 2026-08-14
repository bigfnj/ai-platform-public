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
