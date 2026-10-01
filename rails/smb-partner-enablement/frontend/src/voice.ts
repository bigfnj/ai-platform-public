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
let _sinkId: string = ''       // '' = browser default audio output
let _inputDeviceId: string = '' // '' = browser default mic

/** List available audio OUTPUT devices (labels only available after mic permission granted). */
export async function getAudioOutputDevices(): Promise<MediaDeviceInfo[]> {
  if (!navigator.mediaDevices?.enumerateDevices) return []
  try {
    const devices = await navigator.mediaDevices.enumerateDevices()
    return devices.filter((d) => d.kind === 'audiooutput')
  } catch {
    return []
  }
}

/** List available audio INPUT devices (labels only available after mic permission granted). */
export async function getAudioInputDevices(): Promise<MediaDeviceInfo[]> {
  if (!navigator.mediaDevices?.enumerateDevices) return []
  try {
    const devices = await navigator.mediaDevices.enumerateDevices()
    return devices.filter((d) => d.kind === 'audioinput')
  } catch {
    return []
  }
}

/** Persist the chosen output sink ID so all subsequent speak() calls route there. */
export function setAudioSink(deviceId: string): void {
  _sinkId = deviceId
}

/**
 * Prefer a specific microphone for subsequent listen() calls.
 * Calls getUserMedia() with that device first so Chromium-based browsers prime
 * the audio subsystem on that input before SpeechRecognition starts.
 * (SpeechRecognition has no official deviceId API — this is best-effort.)
 */
export async function setAudioInput(deviceId: string): Promise<void> {
  _inputDeviceId = deviceId
  if (!deviceId || !navigator.mediaDevices?.getUserMedia) return
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: { deviceId: { exact: deviceId } } })
    stream.getTracks().forEach((t) => t.stop())
  } catch { /* ignore — just priming */ }
}

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
// Female-name list covers Microsoft neural (Ava, Emma, Jenny, Aria, Michelle, Elizabeth, Sonia,
// Libby, Maisie) and Google/macOS voices. Ranked before gender-neutral tiers so the user
// consistently gets a female voice whether Kokoro falls back or not.
const FEMALE_NAMES = /ava|emma|jenny|aria|michelle|elizabeth|sonia|libby|maisie|zira|samantha|victoria|karen|moira|veena/i

const VOICE_TIERS: ((v: SpeechSynthesisVoice) => boolean)[] = [
  (v) => FEMALE_NAMES.test(v.name) && /natural/i.test(v.name),  // female Microsoft neural
  (v) => FEMALE_NAMES.test(v.name) && /online/i.test(v.name),   // female Microsoft network
  (v) => FEMALE_NAMES.test(v.name),                              // any female-named voice
  (v) => /natural/i.test(v.name),          // any Microsoft neural
  (v) => /online/i.test(v.name),           // any Microsoft network voice
  (v) => /google/i.test(v.name),           // Chrome network voices
  (v) => !/david|guy|mark|hazel/i.test(v.name), // anything but known-male/flat legacy
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

function _speakBrowser(payload: VoicePayload, onEnd?: () => void): void {
  if (!canSpeak()) { onEnd?.(); return }
  const utterance = new SpeechSynthesisUtterance(payload.text)
  utterance.lang = payload.lang === 'en' ? 'en-US' : payload.lang
  const voice = pickVoice(utterance.lang)
  if (voice) utterance.voice = voice
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
    audio.onended = () => { current = null; onEnd?.() }
    const tryPlay = async () => {
      // Route to user-selected output device when setSinkId is available (Chrome/Edge).
      if (_sinkId && typeof (audio as any).setSinkId === 'function') {
        try { await (audio as any).setSinkId(_sinkId) } catch { /* ignore — fall through to default */ }
      }
      try {
        await audio.play()
      } catch (err) {
        // Autoplay blocked or device error — fall back to browser speech so there's always audio.
        console.warn('[voice] broker audio play() failed, falling back to browser TTS:', err)
        current = null
        _speakBrowser(payload, onEnd)
      }
    }
    void tryPlay()
    return
  }
  _speakBrowser(payload, onEnd)
}

/**
 * Record one utterance from the microphone the user actually chose, for server-side STT.
 *
 * This exists because `SpeechRecognition` has no device API — it always listens to the OS
 * default input. Selecting a headset in our picker and then speaking into it produced a
 * `no-speech` error, because Chrome was listening to a different (silent) device the whole
 * time. `getUserMedia({ deviceId })` DOES honour the selection, so recording ourselves and
 * transcribing on the broker is the only way the picker can mean anything.
 *
 * Returns the recorded audio as base64 plus the container suffix, for `/api/transcribe`.
 */
export type Recorder = { stop: () => void; cancel: () => void }

const MIME_CANDIDATES = [
  'audio/webm;codecs=opus',
  'audio/webm',
  'audio/ogg;codecs=opus',
  'audio/mp4',
]

function pickMime(): string {
  if (typeof MediaRecorder === 'undefined') return ''
  return MIME_CANDIDATES.find((m) => MediaRecorder.isTypeSupported?.(m)) || ''
}

export const canRecord = () =>
  typeof MediaRecorder !== 'undefined' && !!navigator.mediaDevices?.getUserMedia

function suffixFor(mime: string): string {
  if (mime.includes('ogg')) return '.ogg'
  if (mime.includes('mp4')) return '.mp4'
  return '.webm'
}

export async function record(handlers: {
  onReady: (audio_b64: string, suffix: string) => void
  onError?: (detail: string) => void
  /** Fires once the stream is live, so the UI can say "listening" only when it truly is. */
  onStart?: () => void
  /** Auto-stop after this many ms so a forgotten recording cannot run forever. */
  maxMs?: number
}): Promise<Recorder | null> {
  if (!canRecord()) {
    handlers.onError?.('recording is not supported in this browser')
    return null
  }
  const constraint: MediaTrackConstraints = _inputDeviceId
    ? { deviceId: { exact: _inputDeviceId } }
    : {}
  let stream: MediaStream
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: constraint })
  } catch (err: any) {
    // An exact deviceId that no longer exists (headset unplugged) throws OverconstrainedError;
    // retry on the default rather than leaving the user with a dead mic button.
    if (_inputDeviceId) {
      try {
        stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      } catch (err2: any) {
        handlers.onError?.(String(err2?.name || err2?.message || 'microphone unavailable'))
        return null
      }
    } else {
      handlers.onError?.(String(err?.name || err?.message || 'microphone unavailable'))
      return null
    }
  }

  const mime = pickMime()
  const rec = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined)
  const chunks: BlobPart[] = []
  let cancelled = false

  const release = () => stream.getTracks().forEach((t) => t.stop())

  rec.ondataavailable = (e) => { if (e.data?.size) chunks.push(e.data) }
  rec.onerror = () => { release(); handlers.onError?.('recording failed') }
  rec.onstop = async () => {
    release()
    if (cancelled) return
    const blob = new Blob(chunks, { type: rec.mimeType || mime || 'audio/webm' })
    if (!blob.size) { handlers.onError?.('no audio was captured'); return }
    const buf = await blob.arrayBuffer()
    // Chunked conversion: a single String.fromCharCode(...bytes) blows the argument limit
    // on anything longer than a few seconds of audio.
    const bytes = new Uint8Array(buf)
    let binary = ''
    for (let i = 0; i < bytes.length; i += 0x8000) {
      binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000))
    }
    handlers.onReady(btoa(binary), suffixFor(rec.mimeType || mime))
  }

  rec.start()
  handlers.onStart?.()
  const timer = setTimeout(() => { if (rec.state === 'recording') rec.stop() },
                           handlers.maxMs ?? 30000)

  return {
    stop: () => { clearTimeout(timer); if (rec.state === 'recording') rec.stop() },
    cancel: () => {
      cancelled = true
      clearTimeout(timer)
      if (rec.state === 'recording') rec.stop()
      else release()
    },
  }
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
  // Best-effort: if a specific mic was chosen, prime getUserMedia so Chromium routes there.
  if (_inputDeviceId && navigator.mediaDevices?.getUserMedia) {
    void navigator.mediaDevices.getUserMedia({ audio: { deviceId: { exact: _inputDeviceId } } })
      .then((s) => { s.getTracks().forEach((t) => t.stop()); rec.start() })
      .catch(() => rec.start())
  } else {
    rec.start()
  }
  return { stop: () => rec.stop() }
}
