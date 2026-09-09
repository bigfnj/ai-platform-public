// Browser-side voice plumbing, shared by every rail through @web-core.
//
// Two halves, both degrading to silence rather than throwing:
//   speak()  — play a wav the broker synthesized (Kokoro), with a local browser fallback
//   record() — capture one utterance for the broker to transcribe (faster-whisper)
//
// PRIVACY, AND WHY THERE IS NO BROWSER-STT FALLBACK
// The obvious symmetry would be to fall back to the Web Speech API when the broker is
// unavailable, the way speak() falls back to speechSynthesis. Deliberately not done:
// SpeechRecognition streams the microphone to a CLOUD service. On this platform dictation is
// aimed at IEP present levels and other student-facing writing, so a silent cloud fallback
// would exfiltrate exactly the audio that must never leave the box. If the local path is
// down, dictation reports that it is down.
//
// speechSynthesis (output) is fine: it is on-device, and the text was already on screen.

const SINK_KEY = 'platform-voice-sink'
const INPUT_KEY = 'platform-voice-input'

const lsGet = (k: string) => { try { return localStorage.getItem(k) || '' } catch { return '' } }
export const lsSet = (k: string, v: string) => { try { localStorage.setItem(k, v) } catch { /* private mode */ } }

let current: HTMLAudioElement | null = null
let sinkId = lsGet(SINK_KEY)
let inputDeviceId = lsGet(INPUT_KEY)

export const canSpeak = () => typeof globalThis.speechSynthesis !== 'undefined'
export const canRecord = () =>
  typeof MediaRecorder !== 'undefined' && !!navigator.mediaDevices?.getUserMedia

// --- device selection -------------------------------------------------------

async function devices(kind: MediaDeviceKind): Promise<MediaDeviceInfo[]> {
  try {
    const all = await navigator.mediaDevices.enumerateDevices()
    return all.filter((d) => d.kind === kind)
  } catch {
    return []
  }
}

export const getAudioOutputDevices = () => devices('audiooutput')
export const getAudioInputDevices = () => devices('audioinput')

/** Output routing. Chromium-only (`setSinkId`); ignored elsewhere. */
export function setAudioSink(deviceId: string): void {
  sinkId = deviceId
  lsSet(SINK_KEY, deviceId)
}

export function setAudioInput(deviceId: string): void {
  inputDeviceId = deviceId
  lsSet(INPUT_KEY, deviceId)
}

export const getSelectedSink = () => sinkId
export const getSelectedInput = () => inputDeviceId

/**
 * Device labels are blank until the user has granted mic permission once.
 *
 * Returns null on success, or a human-readable reason on failure. It used to swallow every
 * error, which made three very different situations look identical — a dead button. The one
 * that actually bites: `navigator.mediaDevices` is UNDEFINED outside a secure context, so on
 * `http://elsewhere` or `http://192.168.1.11` (see deploy/Caddyfile — four origins, all plain
 * http) this throws a TypeError before any permission prompt can appear. The browser never
 * asks, nothing happens, and the panel has no way to say why. Only `http://platform.localhost`
 * and an https origin are trustworthy contexts.
 */
export async function primeDevicePermission(): Promise<string | null> {
  if (!globalThis.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
    return `Microphone access needs a secure origin. This page is ${location.origin} — reopen it on http://platform.localhost or over https.`
  }
  try {
    const s = await navigator.mediaDevices.getUserMedia({ audio: true })
    s.getTracks().forEach((t) => t.stop())
    return null
  } catch (e) {
    const err = e as DOMException
    if (err?.name === 'NotAllowedError') {
      return 'Microphone blocked for this site. Allow it in the browser’s site settings, then try again.'
    }
    if (err?.name === 'NotFoundError') return 'No microphone found on this machine.'
    return `Microphone unavailable: ${err?.name || String(e)}`
  }
}

// --- playback ---------------------------------------------------------------

export function stopSpeaking(): void {
  try { globalThis.speechSynthesis?.cancel() } catch { /* not supported */ }
  if (current) {
    try { current.pause() } catch { /* already gone */ }
    current = null
  }
}

function speakLocally(text: string, lang: string, onEnd?: () => void): void {
  if (!canSpeak()) { onEnd?.(); return }
  const u = new SpeechSynthesisUtterance(text)
  u.lang = lang.startsWith('es') ? 'es-MX' : 'en-US'
  u.rate = 0.95
  u.onend = () => onEnd?.()
  u.onerror = () => onEnd?.()
  speechSynthesis.speak(u)
}

/**
 * Play broker-synthesized audio. Falls back to on-device speechSynthesis when the wav
 * cannot play at all — most often autoplay policy, which blocks playback that is not
 * clearly user-initiated.
 */
export function playWav(audioB64: string, opts: { text?: string; lang?: string; onEnd?: () => void } = {}): void {
  stopSpeaking()
  const audio = new Audio(`data:audio/wav;base64,${audioB64}`)
  current = audio
  audio.onended = () => { current = null; opts.onEnd?.() }
  audio.onerror = () => { current = null; opts.onEnd?.() }
  const play = async () => {
    try {
      const withSink = audio as HTMLAudioElement & { setSinkId?: (id: string) => Promise<void> }
      if (sinkId && typeof withSink.setSinkId === 'function') {
        try { await withSink.setSinkId(sinkId) } catch { /* device gone; use default */ }
      }
      await audio.play()
    } catch {
      current = null
      if (opts.text) speakLocally(opts.text, opts.lang || 'en', opts.onEnd)
      else opts.onEnd?.()
    }
  }
  void play()
}

// --- recording --------------------------------------------------------------

export type Recorder = { stop: () => void; cancel: () => void }

const MIME_CANDIDATES = [
  'audio/webm;codecs=opus',   // Chrome/Edge
  'audio/webm',
  'audio/ogg;codecs=opus',    // Firefox
  'audio/mp4',                // Safari
]

function pickMime(): string {
  for (const m of MIME_CANDIDATES) {
    if (typeof MediaRecorder !== 'undefined' && MediaRecorder.isTypeSupported?.(m)) return m
  }
  return ''
}

/** Container suffix the broker uses to name the temp file PyAV then demuxes. */
function suffixFor(mime: string): string {
  if (mime.includes('ogg')) return '.ogg'
  if (mime.includes('mp4')) return '.mp4'
  return '.webm'
}

/** base64 of a Blob, chunked — String.fromCharCode blows its argument limit on big inputs. */
async function blobToBase64(blob: Blob): Promise<string> {
  const buf = new Uint8Array(await blob.arrayBuffer())
  let binary = ''
  const STEP = 0x8000
  for (let i = 0; i < buf.length; i += STEP) {
    binary += String.fromCharCode(...buf.subarray(i, i + STEP))
  }
  return btoa(binary)
}

/**
 * Record one utterance. Resolves to a handle, or null when recording is unsupported or the
 * microphone is refused.
 *
 * The browser records rather than using SpeechRecognition because SpeechRecognition offers
 * no device selection (it always takes the OS default) — and because it is a cloud service,
 * which this platform will not send student audio to.
 */
export async function record(handlers: {
  onReady: (audioB64: string, suffix: string) => void
  onError?: (message: string) => void
  maxMs?: number
}): Promise<Recorder | null> {
  if (!canRecord()) { handlers.onError?.('recording is not supported in this browser'); return null }

  const constraint: MediaStreamConstraints = {
    audio: inputDeviceId ? { deviceId: { exact: inputDeviceId } } : true,
  }
  let stream: MediaStream
  try {
    stream = await navigator.mediaDevices.getUserMedia(constraint)
  } catch (err) {
    // A remembered device that has since been unplugged throws OverconstrainedError; retry
    // on the default mic rather than making the user go and clear the setting.
    if ((err as DOMException)?.name === 'OverconstrainedError' && inputDeviceId) {
      try {
        stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      } catch {
        handlers.onError?.('no microphone available'); return null
      }
    } else {
      handlers.onError?.('microphone permission denied'); return null
    }
  }

  const mime = pickMime()
  const rec = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined)
  const chunks: BlobPart[] = []
  let cancelled = false

  rec.ondataavailable = (e) => { if (e.data.size) chunks.push(e.data) }
  rec.onstop = async () => {
    stream.getTracks().forEach((t) => t.stop())
    if (cancelled) return
    try {
      const blob = new Blob(chunks, { type: rec.mimeType || mime || 'audio/webm' })
      if (!blob.size) { handlers.onError?.('nothing was recorded'); return }
      handlers.onReady(await blobToBase64(blob), suffixFor(rec.mimeType || mime))
    } catch {
      handlers.onError?.('could not read the recording')
    }
  }

  rec.start()
  // A forgotten recording is a hot mic; always bound it.
  const timer = window.setTimeout(() => { if (rec.state === 'recording') rec.stop() },
                                  handlers.maxMs ?? 30000)

  return {
    stop: () => { window.clearTimeout(timer); if (rec.state === 'recording') rec.stop() },
    cancel: () => {
      cancelled = true
      window.clearTimeout(timer)
      if (rec.state === 'recording') rec.stop()
      stream.getTracks().forEach((t) => t.stop())
    },
  }
}

/**
 * Streaming dictation: record continuously and cut a SEGMENT each time the speaker pauses, so
 * each phrase can be transcribed and appended while they keep talking — "live" text without the
 * browser's cloud SpeechRecognition (everything still goes to the local broker).
 *
 * Segmentation is voice-activity based, not fixed-interval, so cuts land in silence and never
 * split a word: a Web Audio analyser tracks loudness against a noise floor calibrated from the
 * first fraction of a second; after speech, ~700ms below the floor ends the segment (or a hard
 * 12s cap, so one long unbroken sentence still flushes). Each segment is its own MediaRecorder
 * so the blob is independently decodable. `onSegment` fires with (base64, suffix, index); the
 * caller transcribes and must append IN INDEX ORDER (transcriptions can finish out of order).
 */
export async function recordStreaming(handlers: {
  onSegment: (audioB64: string, suffix: string, index: number) => void
  onError?: (message: string) => void
  maxMs?: number
}): Promise<Recorder | null> {
  if (!canRecord() || typeof AudioContext === 'undefined') return null

  const constraint: MediaStreamConstraints = {
    audio: inputDeviceId ? { deviceId: { exact: inputDeviceId } } : true,
  }
  let stream: MediaStream
  try {
    stream = await navigator.mediaDevices.getUserMedia(constraint)
  } catch (err) {
    if ((err as DOMException)?.name === 'OverconstrainedError' && inputDeviceId) {
      try { stream = await navigator.mediaDevices.getUserMedia({ audio: true }) }
      catch { handlers.onError?.('no microphone available'); return null }
    } else { handlers.onError?.('microphone permission denied'); return null }
  }

  const mime = pickMime()
  const suffix = suffixFor(mime)
  const ctx = new AudioContext()
  const analyser = ctx.createAnalyser()
  analyser.fftSize = 512
  ctx.createMediaStreamSource(stream).connect(analyser)
  const buf = new Uint8Array(analyser.fftSize)

  let cancelled = false
  let done = false
  let segIndex = 0
  let rec: MediaRecorder | null = null
  let chunks: BlobPart[] = []
  let hadSpeech = false
  let segStart = 0
  let silenceStart = 0
  let floor = 0.02          // noise floor, refined below
  let calibN = 0
  const SPEECH_MARGIN = 0.012, HANGOVER_MS = 700, MIN_SPEECH_MS = 250, MAX_SEG_MS = 12000

  const rms = (): number => {
    analyser.getByteTimeDomainData(buf)
    let s = 0
    for (let i = 0; i < buf.length; i++) { const v = (buf[i] - 128) / 128; s += v * v }
    return Math.sqrt(s / buf.length)
  }

  const startSegment = () => {
    if (cancelled || done) return
    chunks = []
    hadSpeech = false
    segStart = performance.now()
    silenceStart = 0
    rec = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined)
    rec.ondataavailable = (e) => { if (e.data.size) chunks.push(e.data) }
    rec.onstop = () => {
      const parts = chunks
      const emit = hadSpeech
      if (emit && !cancelled) {
        const blob = new Blob(parts, { type: (rec?.mimeType) || mime || 'audio/webm' })
        const idx = segIndex++
        if (blob.size) void blobToBase64(blob).then((b64) => { if (!cancelled) handlers.onSegment(b64, suffix, idx) })
      }
      if (!done && !cancelled) startSegment()   // roll straight into the next segment
    }
    rec.start()
  }

  // Cut the current segment (stop → onstop emits it → next segment starts).
  const cut = () => { if (rec && rec.state === 'recording') rec.stop() }

  const poll = window.setInterval(() => {
    if (cancelled || done) return
    const level = rms()
    if (calibN < 8) { floor = calibN === 0 ? level : (floor * calibN + level) / (calibN + 1); calibN++; return }
    const speaking = level > floor + SPEECH_MARGIN
    const now = performance.now()
    if (speaking) { hadSpeech = true; silenceStart = 0 }
    else if (hadSpeech) { if (!silenceStart) silenceStart = now }
    // End the segment on a real pause after speech, or when it has run too long.
    if (hadSpeech && ((silenceStart && now - silenceStart > HANGOVER_MS && now - segStart > MIN_SPEECH_MS)
                      || now - segStart > MAX_SEG_MS)) {
      cut()
    }
  }, 40)

  const cleanup = () => {
    window.clearInterval(poll)
    try { void ctx.close() } catch { /* already closed */ }
    stream.getTracks().forEach((t) => t.stop())
  }

  const overall = window.setTimeout(() => finish(), handlers.maxMs ?? 120000)
  function finish() {
    if (done || cancelled) return
    done = true
    window.clearTimeout(overall)
    cut()            // flush the final segment; its onstop won't restart because done=true
    // Give the last onstop a tick to emit, then release hardware.
    window.setTimeout(cleanup, 250)
  }

  startSegment()

  return {
    stop: () => finish(),
    cancel: () => {
      if (cancelled) return
      cancelled = true
      done = true
      window.clearTimeout(overall)
      if (rec && rec.state === 'recording') rec.stop()
      cleanup()
    },
  }
}

// --- text tidy-up ------------------------------------------------------------

/** Strip markdown/citation noise so read-aloud does not voice "asterisk asterisk". */
export function speakable(text: string): string {
  return text
    .replace(/```[\s\S]*?```/g, ' ')      // code fences
    .replace(/`([^`]*)`/g, '$1')
    .replace(/!\[[^\]]*\]\([^)]*\)/g, ' ') // images
    .replace(/\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/^\s{0,3}#{1,6}\s+/gm, '')
    .replace(/(\*\*|__|\*|_)/g, '')
    .replace(/^\s*[-*+]\s+/gm, '')
    .replace(/\[\d+\]/g, ' ')              // citation markers
    .replace(/\s{2,}/g, ' ')
    .trim()
}
