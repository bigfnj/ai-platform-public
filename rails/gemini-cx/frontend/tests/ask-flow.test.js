// The answer pane's question flow and Read aloud, under plain `node --test` with fakes. No
// bundler and no React: askflow.ts and readaloud.ts are plain state machines, and api.ts touches
// the browser only at call time, so node's type stripping loads all three directly.
//
// Each section is a defect recorded against this rail's frontend, reproduced against the fakes:
//   - Stop left the 12 s watchdog armed, so a cancelled question was asked again over HTTP;
//   - a stream that closed cleanly mid-answer showed the partial answer as complete;
//   - Read aloud spoke after its button was gone (an await with no alive check), and
//     unmounting it stopped audio it had not started.
import { test } from 'node:test'
import assert from 'node:assert/strict'

function fakeTimers() {
  let next = 1
  const pending = new Map()
  return {
    setTimeout: (fn, ms) => { const id = next++; pending.set(id, fn); return id },
    clearTimeout: (id) => { pending.delete(id) },
    run: () => { for (const [id, fn] of [...pending]) { pending.delete(id); fn() } },
    get count() { return pending.size },
  }
}

function deferred() {
  let resolve, reject
  const promise = new Promise((res, rej) => { resolve = res; reject = rej })
  return { promise, resolve, reject }
}

const flush = () => new Promise((r) => setImmediate(r))

const { startAsk, WATCHDOG_MS } = await import('../src/askflow.ts')
const { ReadAloudControl } = await import('../src/readaloud.ts')

function flow() {
  const timers = fakeTimers()
  const log = { streams: [], buffered: [] }
  const deps = {
    ...timers,
    askStreaming: (req, onFrame, onClose) => {
      const s = { req, onFrame, onClose, cancelled: false }
      log.streams.push(s)
      return () => { s.cancelled = true }
    },
    askBuffered: (req) => { const d = deferred(); log.buffered.push(d); return d.promise },
  }
  const seen = { cites: [], tokens: [], done: [], errors: [] }
  const abort = startAsk(deps, { question_id: 'pricing' }, {
    onCitations: (c) => seen.cites.push(c),
    onToken: (t) => seen.tokens.push(t),
    onDone: (r) => seen.done.push(r),
    onError: (e) => seen.errors.push(e),
  })
  return { timers, log, seen, abort, stream: log.streams[0] }
}

test('Stop ends the watchdog too, so the cancelled question is never asked again', () => {
  const { timers, log, seen, abort, stream } = flow()
  stream.onFrame({ type: 'retrieval', citations: [{ n: 1 }] })
  abort()                                   // the Stop button
  assert.equal(stream.cancelled, true)
  assert.equal(timers.count, 0, 'the watchdog survived the Stop')
  timers.run()
  assert.equal(log.buffered.length, 0, 'a second generation of a cancelled question went out')
  stream.onFrame({ type: 'token', text: 'late' })
  assert.deepEqual(seen.tokens, [])
})

test('no token inside the watchdog: the buffered route answers, once', async () => {
  const { timers, log, seen, stream } = flow()
  timers.run()
  assert.equal(stream.cancelled, true)
  assert.equal(log.buffered.length, 1)
  log.buffered[0].resolve({ answer: 'buffered', citations: [] })
  await flush()
  assert.deepEqual(seen.done, [{ answer: 'buffered', citations: [] }])
  stream.onClose()
  assert.equal(seen.done.length, 1)
})

test('a clean close after some tokens is reported, not shown as a finished answer', () => {
  const { seen, stream, log } = flow()
  stream.onFrame({ type: 'token', text: 'GECX pricing ' })
  stream.onClose('the connection closed before the answer finished', false)
  assert.deepEqual(seen.errors, ['the connection closed before the answer finished'])
  assert.deepEqual(seen.done, [])
  assert.equal(log.buffered.length, 0)
})

test('a close before any token falls back to the buffered route', () => {
  const { log, seen, stream } = flow()
  stream.onClose('the connection closed before the answer finished', false)
  assert.equal(log.buffered.length, 1)
  assert.deepEqual(seen.errors, [])
})

test('an error FRAME is shown, not retried', () => {
  const { log, seen, stream } = flow()
  stream.onClose('model failed to load', true)
  assert.deepEqual(seen.errors, ['model failed to load'])
  assert.equal(log.buffered.length, 0)
})

test('done after tokens finishes with no error', () => {
  const { seen, stream, timers } = flow()
  stream.onFrame({ type: 'token', text: 'a' })
  assert.equal(timers.count, 0, 'the first token disarms the watchdog')
  stream.onClose(undefined, false)
  assert.deepEqual(seen.done, [undefined])
  assert.deepEqual(seen.errors, [])
})

test('a buffered answer that lands after Stop is dropped', async () => {
  const { timers, log, seen, abort } = flow()
  timers.run()
  abort()
  log.buffered[0].resolve({ answer: 'x', citations: [] })
  await flush()
  assert.deepEqual(seen.done, [])
})

test('the watchdog is the documented 12 s', () => {
  assert.equal(WATCHDOG_MS, 12000)
})

// --- Read aloud ---------------------------------------------------------------------------------

function readAloud() {
  const log = { spoken: [], stops: 0, synth: null }
  const deps = {
    speakText: () => { log.synth = deferred(); return log.synth.promise },
    speak: (p, onEnd) => log.spoken.push({ p, onEnd }),
    stopSpeaking: () => { log.stops++ },
  }
  return { c: new ReadAloudControl(deps, () => {}), log }
}

const VOICE = { mode: 'broker', text: 'hi', lang: 'en', audio_b64: 'QUJD' }

test('a Read aloud gone before synthesis returns says nothing', async () => {
  const { c, log } = readAloud()
  const pending = c.toggle('GECX pricing is not published')
  c.dispose()                               // a new question (the button's key) or the rail left
  log.synth.resolve(VOICE)
  await pending
  assert.equal(log.spoken.length, 0)
})

test('a new question stops the old answer\'s audio', async () => {
  const { c, log } = readAloud()
  const pending = c.toggle('answer A')
  log.synth.resolve(VOICE)
  await pending
  assert.equal(c.state.on, true)
  c.dispose()                               // remounted for answer B
  assert.equal(log.stops, 1)
})

test('a Read aloud that never played stops nothing when it goes', () => {
  const { c, log } = readAloud()
  c.dispose()
  assert.equal(log.stops, 0)
})

// --- api.ts -------------------------------------------------------------------------------------

class FakeSocket {
  static last = null
  constructor() { this.sent = []; this.closed = 0; FakeSocket.last = this }
  send(d) { this.sent.push(d) }
  close() { this.closed++ }
  frame(obj) { this.onmessage?.({ data: JSON.stringify(obj) }) }
}
globalThis.WebSocket = FakeSocket
globalThis.location = { protocol: 'https:', host: 'platform.example' }
const api = await import('../src/api.ts')

function streamed() {
  const closes = []
  const frames = []
  const cancel = api.askStreaming({ question: 'q' }, (f) => frames.push(f),
                                  (err, fromServer) => closes.push([err, fromServer]))
  return { ws: FakeSocket.last, closes, frames, cancel }
}

test('a close with no done or error frame says the answer did not finish', () => {
  const { ws, closes } = streamed()
  ws.frame({ type: 'token', text: 'x' })
  ws.onclose()
  assert.deepEqual(closes, [[api.CLOSED_EARLY, false]])
})

test('done then close is a clean finish', () => {
  const { ws, closes } = streamed()
  ws.frame({ type: 'done' })
  ws.onclose()
  assert.deepEqual(closes, [[undefined, false]])
})

test('nothing is reported after a cancel', () => {
  const { ws, closes, frames, cancel } = streamed()
  cancel()
  ws.frame({ type: 'token', text: 'late' })
  ws.onclose()
  assert.deepEqual(closes, [])
  assert.deepEqual(frames, [])
})

// --- voice.ts: a stopped clip tells its caller ----------------------------------------------------
//
// pause() fires no `ended`, so a stopped or preempted clip never reported its end and the button
// that started it stayed on Stop. Latent with one Read aloud per question; the smb twin shipped
// it with two buttons on screen. Same fix, same test.

class FakeAudio {
  static made = []
  constructor(src) { this.src = src; FakeAudio.made.push(this) }
  play() { return Promise.resolve() }
  pause() { this.paused = true }
}
globalThis.Audio = FakeAudio
const voice = await import('../src/voice.ts')
const tick = () => new Promise((r) => setImmediate(r))

test('a preempted clip fires its own onEnd, once', async () => {
  const ends = { a: 0, b: 0 }
  voice.speak(VOICE, () => ends.a++)
  await tick()
  voice.speak(VOICE, () => ends.b++)
  await tick()
  assert.equal(ends.a, 1, 'A never heard that B took over')
  voice.stopSpeaking()
  assert.equal(ends.b, 1, 'Stop never told B its clip ended')
  FakeAudio.made.at(-1).onended?.()       // a late `ended` after the stop
  assert.deepEqual(ends, { a: 1, b: 1 })
})

test('stopSpeaking twice calls the clip\'s onEnd once', () => {
  let n = 0
  voice.speak(VOICE, () => n++)
  voice.stopSpeaking()
  voice.stopSpeaking()
  assert.equal(n, 1)
})
