// web-core's voice plumbing (src/voice.ts): the chosen devices are read at call time, and a
// read-aloud chip stops only its own playback. And its reads (src/read_aloud.ts): Stop aborts
// the request in flight, which platformApi.ttsLight hands its signal.
//
//   node web/tests/voice-devices.test.js
//
// Plain node with stubs for the browser pieces voice.ts touches (localStorage, Audio,
// navigator.mediaDevices, MediaRecorder, speechSynthesis), and fetch: none of the three modules
// has a runtime import, so node loads the real ones. Mutation-tested by
// web/tests/mutate_voice_devices.mjs.
import { test } from 'node:test'
import assert from 'node:assert/strict'

const store = new Map()
globalThis.localStorage = {
  getItem: (k) => (store.has(k) ? store.get(k) : null),
  setItem: (k, v) => store.set(k, String(v)),
}
const played = []
class FakeAudio {
  constructor(src) { this.src = src; this.paused = false; this.sinks = []; played.push(this) }
  async setSinkId(id) { this.sinks.push(id) }
  async play() {}
  pause() { this.paused = true }
}
globalThis.Audio = FakeAudio
globalThis.speechSynthesis = { cancel() {}, speak() {} }
const constraints = []
Object.defineProperty(globalThis.navigator, 'mediaDevices', {
  configurable: true,
  value: {
    getUserMedia: async (c) => { constraints.push(c); throw Object.assign(new Error('no'), { name: 'NotAllowedError' }) },
  },
})
globalThis.MediaRecorder = class { static isTypeSupported() { return true } }

// Imported AFTER the stubs and BEFORE any device is chosen: what a rail bundle loaded at
// startup sees. The choice made later, in the top bar's own copy of web-core, reaches it only
// through localStorage.
const voice = await import('../src/voice.ts')
const tick = () => new Promise((r) => setTimeout(r, 0))

test('a microphone chosen after this copy loaded is the one recording asks for', async () => {
  store.set('platform-voice-input', 'mic-B')
  await voice.record({ onReady() {}, onError() {} })
  assert.deepEqual(constraints.at(-1), { audio: { deviceId: { exact: 'mic-B' } } })
})

test('a speaker chosen after this copy loaded is the one playback routes to', async () => {
  store.set('platform-voice-sink', 'spk-B')
  voice.playWav('AAAA', {})
  await tick()
  assert.deepEqual(played.at(-1).sinks, ['spk-B'])
  voice.stopSpeaking()
})

test("an unmounting chip stops only its own playback", async () => {
  const a = {}, b = {}
  let aEnded = 0
  voice.playWav('AAAA', { owner: a, onEnd: () => { aEnded++ } })
  const audioA = played.at(-1)
  voice.stopSpeakingFor(b)          // chip B unmounts while A reads
  assert.equal(audioA.paused, false, "B's unmount cut off A")
  assert.equal(aEnded, 0)
  voice.stopSpeakingFor(a)
  assert.equal(audioA.paused, true)
  assert.equal(aEnded, 1)
})

test('the chip whose audio another one replaces is told it ended', async () => {
  let aEnded = 0, bEnded = 0
  voice.playWav('AAAA', { owner: {}, onEnd: () => { aEnded++ } })
  voice.playWav('BBBB', { owner: {}, onEnd: () => { bEnded++ } })
  assert.equal(aEnded, 1, 'A stays on "Stop" for audio that is no longer playing')
  assert.equal(bEnded, 0)
  played.at(-1).onended()
  played.at(-1).onended()
  assert.equal(bEnded, 1, 'onEnd ran more than once')
})

// --- a read-aloud chip's reads (src/read_aloud.ts, through src/platformApi.ts) -----------------
// SpeakButton.tsx hands each click's read to a reader: Stop, the next click and an unmount abort
// the one in flight. Both modules have no runtime imports, so node loads the real ones.
const { createReader } = await import('../src/read_aloud.ts')
const { platformApi } = await import('../src/platformApi.ts')
const WAV = { audio_b64: 'AAAA', sample_rate: 24000, voice: 'af_heart', lang: 'en' }

// fetch, holding each request open until the test answers it or its signal aborts it, as a slow
// media worker holds /api/platform/tts_light. Returns the requests, each with `answer(status,
// body)`; a request whose signal aborts rejects with AbortError, as fetch does.
function holdRequests() {
  const asked = []
  globalThis.fetch = (url, init = {}) => new Promise((resolve, reject) => {
    asked.push({ url, init, answer: (status, body) => resolve(new Response(JSON.stringify(body), { status })) })
    init.signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')))
  })
  return asked
}

// One read the way SpeakButton starts it, recording what it did.
function startRead(reader, seen, ask = (signal) => platformApi.ttsLight({ text: 'hello' }, signal)) {
  return reader.read({ ask, play: (out) => seen.push(['play', out.audio_b64]), fallback: () => seen.push(['fallback']) })
}

test('Stop aborts the read-aloud request in flight, and the read plays nothing', async () => {
  const asked = holdRequests()
  const reader = createReader()
  const seen = []
  const done = startRead(reader, seen)
  await tick()
  assert.equal(asked.length, 1)
  assert.equal(asked[0].url, '/api/platform/tts_light')
  reader.stop()
  const aborted = asked[0].init.signal?.aborted === true
  // Not aborted: answer it, so that the verdict is this test failing, not the suite hanging.
  if (!aborted) asked[0].answer(200, WAV)
  await done
  assert.equal(aborted, true, 'Stop left the request running')
  assert.deepEqual(seen, [])
})

test('a new read aborts the one before it', async () => {
  const asked = holdRequests()
  const reader = createReader()
  const seen = []
  const first = startRead(reader, seen)
  await tick()
  const second = startRead(reader, seen)
  await tick()
  const aborted = asked[0].init.signal?.aborted === true
  if (!aborted) asked[0].answer(200, { ...WAV, audio_b64: 'OLD' })
  asked[1].answer(200, WAV)
  await Promise.all([first, second])
  assert.equal(aborted, true, 'the first read is still asking')
  assert.deepEqual(seen, [['play', 'AAAA']])
})

test('a reply to a stopped read plays nothing, even from a request that ignored the abort', async () => {
  const reader = createReader()
  const seen = []
  let reply
  const done = startRead(reader, seen, () => new Promise((resolve) => { reply = resolve }))
  await tick()
  reader.stop()
  reply(WAV)
  await done
  assert.deepEqual(seen, [])
})

test('a failed request after Stop does not fall back to the on-device voice', async () => {
  const reader = createReader()
  const seen = []
  let fail
  const done = startRead(reader, seen, () => new Promise((_, reject) => { fail = reject }))
  await tick()
  reader.stop()
  fail(new Error('no media worker'))
  await done
  assert.deepEqual(seen, [])
})

test('a read nobody stopped plays its reply, and one whose request fails falls back', async () => {
  const asked = holdRequests()
  const reader = createReader()
  const seen = []
  const ok = startRead(reader, seen)
  await tick()
  asked[0].answer(200, WAV)
  await ok
  const failed = startRead(reader, seen)
  await tick()
  asked[1].answer(503, { detail: 'no media worker' })
  await failed
  assert.deepEqual(seen, [['play', 'AAAA'], ['fallback']])
})
