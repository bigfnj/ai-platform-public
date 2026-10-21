// The SMB rail's voice and streaming logic, under plain `node --test` with fakes for every
// browser effect. No bundler and no React: askflow.ts, voicesession.ts and readaloud.ts are
// plain state machines, and api.ts / voice.ts touch the browser only at call time, so node's
// type stripping loads all five directly.
//
// Each section is a defect the 2026-09-11 audits recorded (or the 2026-10-08 audit found) in
// this rail's frontend, reproduced against the fake and then held:
//   - the mobile mic could never stop a recording;
//   - a closed tab kept the answer stream, its watchdog and the read-aloud alive (VoiceChat
//     leaked its cancel handle; the mobile Chat discarded its own);
//   - audio started after the rail was left (an await with no alive check);
//   - a stream the gateway closed cleanly left the UI busy forever (no onclose);
//   - stopSpeaking() never told the clip's caller (pause() fires no `ended`);
//   - listen()'s handle was discarded, so a browser recognition could not be cancelled;
//   - with the media worker down the mic fell back to the browser's CLOUD recogniser, bypassing
//     the platform's local-by-default dictation policy (web-core dictate_policy.ts).
import { test } from 'node:test'
import assert from 'node:assert/strict'

// The platform's policy itself, not a copy: the session must make the decision the same
// function makes for every other rail's dictation chip.
const { chooseSttPath } = await import('../../../../web/src/dictate_policy.ts')

// --- fakes ------------------------------------------------------------------------------------

/** setTimeout/clearTimeout under the test's control: `run()` fires every pending timer. */
function fakeTimers() {
  let next = 1
  const pending = new Map()
  return {
    setTimeout: (fn, ms) => { const id = next++; pending.set(id, { fn, ms }); return id },
    clearTimeout: (id) => { pending.delete(id) },
    run: () => { for (const [id, t] of [...pending]) { pending.delete(id); t.fn() } },
    get count() { return pending.size },
  }
}

/** A deferred: a promise plus the functions that settle it. */
function deferred() {
  let resolve, reject
  const promise = new Promise((res, rej) => { resolve = res; reject = rej })
  return { promise, resolve, reject }
}

const flush = () => new Promise((r) => setImmediate(r))

/** Every dependency VoiceSession takes, recording what it was asked to do. */
function fakeDeps({ availability = 'available' } = {}) {
  const timers = fakeTimers()
  const log = { streams: [], asks: [], spoken: [], stops: 0, records: [], listens: [] }
  const deps = {
    ...timers,
    sttAvailability: async () => availability,
    hasRecognition: () => true,
    chooseSttPath,
    askStream: (body, handlers) => {
      const s = { body, handlers, cancelled: false }
      log.streams.push(s)
      return () => { s.cancelled = true }
    },
    ask: (body) => {
      const d = deferred()
      log.asks.push({ body, ...d })
      return d.promise
    },
    transcribe: (audio, suffix) => {
      const d = deferred()
      log.transcribing = d
      return d.promise
    },
    record: (handlers) => {
      const d = deferred()
      const rec = { handlers, stopped: 0, cancelled: 0,
                    stop() { this.stopped++ }, cancel() { this.cancelled++ } }
      log.records.push({ rec, ...d })
      return d.promise
    },
    listen: (handlers) => {
      const l = { handlers, processLocally: handlers.processLocally, stopped: 0, aborted: 0,
                  stop() { this.stopped++ }, abort() { this.aborted++ } }
      log.listens.push(l)
      return l
    },
    speak: (payload, onEnd) => { log.spoken.push({ payload, onEnd }) },
    stopSpeaking: () => { log.stops++ },
  }
  return { deps, log, timers }
}

const VOICE = { mode: 'broker', text: 'hi', lang: 'en', audio_b64: 'QUJD' }

const { VoiceSession } = await import('../src/voicesession.ts')
const { startAsk, WATCHDOG_MS } = await import('../src/askflow.ts')
const { ReadAloudControl } = await import('../src/readaloud.ts')

function session(opts) {
  const f = fakeDeps(opts)
  const states = []
  const s = new VoiceSession(f.deps, (st) => states.push(st))
  return { s, ...f, states }
}

// --- the mic ---------------------------------------------------------------------------------

test('a second press stops the recording the first started, and sends it', async () => {
  const { s, log } = session()
  s.mic(true)
  assert.equal(log.records.length, 1)
  const { rec, resolve } = log.records[0]
  rec.handlers.onStart()                // the stream is live: the UI now says "hearing"
  resolve(rec)
  await flush()
  assert.equal(s.state.hearing, true)
  assert.equal(s.state.recording, true)
  s.mic(true)                           // the ⏹ press
  assert.equal(rec.stopped, 1, 'the stop branch must be reachable while hearing')
  assert.equal(log.records.length, 1, 'and it must not start a second recording')
})

test('a press while the recording is being transcribed is ignored', async () => {
  const { s, log } = session()
  s.mic(true)
  const { rec, resolve } = log.records[0]
  rec.handlers.onStart()
  resolve(rec)
  await flush()
  s.mic(true)
  rec.handlers.onReady('QUJD', '.webm')   // stop delivered the clip; transcription starts
  await flush()
  assert.equal(s.state.thinking, true)
  s.mic(true)
  assert.equal(log.records.length, 1)
})

test('a browser recognition can be cancelled, and a late result from it submits nothing', async () => {
  const { s, log } = session()
  s.mic(false)
  await flush()
  assert.equal(log.listens.length, 1)
  const l = log.listens[0]
  assert.equal(s.state.hearing, true)
  s.mic(false)                          // the cancel press
  assert.equal(l.aborted, 1)
  assert.equal(s.state.hearing, false)
  l.handlers.onResult('what is the seat cap', true)
  assert.equal(log.streams.length, 0, 'a cancelled recognition still asked its question')
})

test('a final browser result asks its question, spoken', async () => {
  const { s, log } = session()
  s.mic(false)
  await flush()
  log.listens[0].handlers.onResult('what is the seat cap', true)
  assert.equal(log.streams.length, 1)
  assert.deepEqual(log.streams[0].body, { question: 'what is the seat cap', speak: true })
})

// --- the dictation policy: on-device or nothing, never the cloud by omission ---------------------

for (const availability of [null, 'downloadable', 'downloading', 'unavailable']) {
  test(`no on-device pack (${availability}) and no media worker: no cloud recogniser, and it says so`,
       async () => {
    const { s, log } = session({ availability })
    s.mic(false)
    await flush()
    assert.equal(log.listens.length, 0, 'the microphone went to a cloud recogniser')
    assert.equal(s.state.hearing, false)
    assert.match(s.state.err, /does not send your voice to a cloud/)
  })
}

test('an on-device pack is used, and the recogniser is told to stay local', async () => {
  const { s, log } = session({ availability: 'available' })
  s.mic(false)
  await flush()
  assert.equal(log.listens.length, 1)
  assert.equal(log.listens[0].processLocally, true)
})

test('a tab closed while availability was being asked starts nothing', async () => {
  const { s, log } = session({ availability: 'available' })
  s.mic(false)
  s.dispose()
  await flush()
  assert.equal(log.listens.length, 0)
})

// --- leaving the tab --------------------------------------------------------------------------

test('dispose mid-answer closes the stream and the watchdog never fires the buffered ask', () => {
  const { s, log, timers } = session()
  s.submit('seats?', true)
  assert.equal(timers.count, 1, 'the watchdog is armed')
  s.dispose()
  assert.equal(log.streams[0].cancelled, true)
  assert.equal(timers.count, 0, 'the watchdog outlived the tab')
  timers.run()
  assert.equal(log.asks.length, 0, 'a fresh POST /api/ask went out after the tab closed')
  log.streams[0].handlers.onDone({ answer: 'late', voice: VOICE })
  assert.equal(log.spoken.length, 0, 'the answer was read aloud with no panel on screen')
})

test('a recording transcribed after the tab closed asks nothing and says nothing', async () => {
  const { s, log } = session()
  s.mic(true)
  const { rec, resolve } = log.records[0]
  rec.handlers.onStart()
  resolve(rec)
  await flush()
  s.mic(true)
  rec.handlers.onReady('QUJD', '.webm')
  await flush()
  s.dispose()
  log.transcribing.resolve({ text: 'what is the seat cap' })
  await flush()
  assert.equal(log.streams.length, 0)
  assert.equal(log.spoken.length, 0)
})

test('a recording whose permission prompt outlived the tab is released, not started', async () => {
  const { s, log } = session()
  s.mic(true)
  const { rec, resolve } = log.records[0]
  s.dispose()
  resolve(rec)
  await flush()
  assert.equal(rec.cancelled, 1)
})

test('dispose ends a live recognition and only this session\'s read-aloud', async () => {
  const a = session()
  a.s.mic(false)
  await flush()
  a.s.dispose()
  assert.equal(a.log.listens[0].aborted, 1)
  assert.equal(a.log.stops, 1, 'mic() stops other audio before listening, once')

  const b = session()
  b.s.dispose()
  assert.equal(b.log.stops, 0, 'a session that was not speaking stopped somebody else\'s audio')
})

test('a spoken answer is read aloud and the stop state follows it', () => {
  const { s, log } = session()
  s.submit('seats?', true)
  const h = log.streams[0].handlers
  h.onToken('25 ')
  h.onDone({ answer: '25 seats', voice: VOICE })
  assert.equal(s.state.busy, false)
  assert.equal(log.spoken.length, 1)
  assert.equal(s.state.speaking, true)
  log.spoken[0].onEnd()
  assert.equal(s.state.speaking, false)
})

test('a typed question is not spoken', () => {
  const { s, log } = session()
  s.submit('seats?', false)
  log.streams[0].handlers.onDone({ answer: '25', voice: VOICE })
  assert.equal(log.spoken.length, 0)
})

// --- the stream with a fallback (askflow) -------------------------------------------------------

function flow() {
  const f = fakeDeps()
  const seen = { done: [], errors: [], tokens: [] }
  const abort = startAsk(f.deps, { question: 'q' }, {
    onToken: (t) => seen.tokens.push(t),
    onDone: (r) => seen.done.push(r),
    onError: (e) => seen.errors.push(e),
  })
  return { ...f, seen, abort }
}

test('no token inside the watchdog falls back to the buffered route, once', async () => {
  const { log, timers, seen } = flow()
  timers.run()
  assert.equal(log.streams[0].cancelled, true)
  assert.equal(log.asks.length, 1)
  log.asks[0].resolve({ answer: 'buffered', citations: [], grounded: true })
  await flush()
  assert.deepEqual(seen.done, [{ answer: 'buffered', voice: undefined, citations: [],
                                 grounded: true }])
  log.streams[0].handlers.onDone({ answer: 'too late' })
  assert.equal(seen.done.length, 1)
})

test('a token disarms the watchdog; a failure after it is an error, not a second ask', () => {
  const { log, timers, seen } = flow()
  log.streams[0].handlers.onToken('a')
  assert.equal(timers.count, 0)
  log.streams[0].handlers.onError('the connection closed before the answer finished')
  assert.equal(log.asks.length, 0)
  assert.deepEqual(seen.errors, ['the connection closed before the answer finished'])
})

test('a failure before any token tries the buffered route instead of erroring', () => {
  const { log, seen } = flow()
  log.streams[0].handlers.onError('connection failed')
  assert.equal(log.asks.length, 1)
  assert.deepEqual(seen.errors, [])
})

test('abort silences a buffered answer already in flight', async () => {
  const { log, timers, seen, abort } = flow()
  timers.run()
  abort()
  log.asks[0].resolve({ answer: 'x', citations: [], grounded: false })
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

test('a button that is gone before synthesis returns says nothing', async () => {
  const { c, log } = readAloud()
  const pending = c.toggle('the next move')
  c.dispose()
  log.synth.resolve(VOICE)
  await pending
  assert.equal(log.spoken.length, 0, 'audio started after the rail was left')
})

test('a button that is still there plays, and Stop stops it', async () => {
  const { c, log } = readAloud()
  const pending = c.toggle('the next move')
  log.synth.resolve(VOICE)
  await pending
  assert.equal(log.spoken.length, 1)
  assert.equal(c.state.on, true)
  await c.toggle('the next move')
  assert.equal(log.stops, 1)
  assert.equal(c.state.on, false)
})

test('unmounting a button that is not playing stops nobody else\'s audio', () => {
  const { c, log } = readAloud()
  c.dispose()
  assert.equal(log.stops, 0)
})

test('a failed synthesis falls back to the browser voice', async () => {
  const { c, log } = readAloud()
  const pending = c.toggle('the next move')
  log.synth.reject(new Error('503'))
  await pending
  assert.equal(log.spoken[0].p.mode, 'browser')
})

// --- api.ts: a stream that closes without a terminal frame --------------------------------------

class FakeSocket {
  static last = null
  constructor(url) {
    this.url = url
    this.sent = []
    this.closed = 0
    FakeSocket.last = this
  }
  send(data) { this.sent.push(data) }
  close() { this.closed++ }
  frame(obj) { this.onmessage?.({ data: typeof obj === 'string' ? obj : JSON.stringify(obj) }) }
}
globalThis.WebSocket = FakeSocket
globalThis.location = { protocol: 'https:', host: 'platform.example' }
const api = await import('../src/api.ts')

function streamed(which) {
  const seen = []
  const h = {
    onDone: (a) => seen.push(['done', a]),
    onPackage: (p) => seen.push(['package', p]),
    onError: (d) => seen.push(['error', d]),
    onToken: (...a) => seen.push(['token', ...a]),
  }
  const cancel = which === 'ask'
    ? api.askStream({ question: 'q' }, h)
    : api.generatePackage({ scenario_id: 's', answers: {} }, h)
  return { ws: FakeSocket.last, seen, cancel }
}

for (const which of ['ask', 'scenario']) {
  test(`${which}: a clean close with no terminal frame is an error, reported once`, () => {
    const { ws, seen } = streamed(which)
    ws.onopen()
    ws.frame(which === 'ask' ? { type: 'token', token: 'x' } : { type: 'token', key: 'k', token: 'x' })
    ws.onclose({ code: 1000 })
    const errors = seen.filter(([k]) => k === 'error')
    assert.deepEqual(errors, [['error', api.CLOSED_EARLY]])
  })

  test(`${which}: a transport error then its close is ONE error`, () => {
    const { ws, seen } = streamed(which)
    ws.onerror()
    ws.onclose({ code: 1006 })
    assert.deepEqual(seen, [['error', 'connection failed']])
  })

  test(`${which}: a finished stream's close is not an error`, () => {
    const { ws, seen } = streamed(which)
    ws.frame(which === 'ask' ? { type: 'done', answer: 'a' } : { type: 'package', package: { p: 1 } })
    ws.onclose({ code: 1000 })
    assert.equal(seen.length, 1)
    assert.equal(seen[0][0], which === 'ask' ? 'done' : 'package')
    assert.equal(ws.closed, 1)
  })

  test(`${which}: after cancel nothing is reported`, () => {
    const { ws, seen, cancel } = streamed(which)
    cancel()
    ws.frame({ type: 'error', detail: 'late' })
    ws.onclose({ code: 1000 })
    assert.deepEqual(seen, [])
  })

  test(`${which}: a frame that is not JSON is skipped, not thrown`, () => {
    const { ws, seen } = streamed(which)
    assert.doesNotThrow(() => ws.frame('not json'))
    assert.deepEqual(seen, [])
  })
}

// --- voice.ts: a stopped clip tells its caller; recognition can be aborted -----------------------

class FakeAudio {
  static made = []
  constructor(src) { this.src = src; this.paused = false; FakeAudio.made.push(this) }
  play() { return Promise.resolve() }
  pause() { this.paused = true }
}
globalThis.Audio = FakeAudio

class FakeRecognition {
  static made = []
  static answer = 'available'
  static installs = 0
  static async available() { return FakeRecognition.answer }
  static async install() { FakeRecognition.installs++ }
  constructor() { this.started = 0; this.stopped = 0; this.aborted = 0; FakeRecognition.made.push(this) }
  start() { this.started++ }
  stop() { this.stopped++ }
  abort() { this.aborted++ }
}
globalThis.webkitSpeechRecognition = FakeRecognition
const voice = await import('../src/voice.ts')

test('a preempted clip fires its own onEnd, once', async () => {
  const ends = { a: 0, b: 0 }
  voice.speak(VOICE, () => ends.a++)
  await flush()
  voice.speak(VOICE, () => ends.b++)
  await flush()
  assert.equal(ends.a, 1, 'A never heard that B took over, so its button stayed on Stop')
  voice.stopSpeaking()
  assert.equal(ends.b, 1)
  FakeAudio.made.at(-1).onended?.()       // a late `ended` after the stop
  assert.deepEqual(ends, { a: 1, b: 1 })
})

test('stopSpeaking with nothing playing calls nobody', () => {
  let n = 0
  voice.speak(VOICE, () => n++)
  voice.stopSpeaking()
  voice.stopSpeaking()
  assert.equal(n, 1)
})

test('an aborted recognition delivers no result and no error', () => {
  const got = []
  const l = voice.listen({ onResult: (t, f) => got.push(['result', t, f]),
                           onError: (e) => got.push(['error', e]) })
  const rec = FakeRecognition.made.at(-1)
  assert.equal(rec.started, 1)
  l.abort()
  assert.equal(rec.aborted, 1)
  rec.onresult({ resultIndex: 0, results: [Object.assign([{ transcript: 'hi' }], { isFinal: true })] })
  rec.onerror({ error: 'aborted' })
  assert.deepEqual(got, [])
})

test('stop() still delivers what was heard', () => {
  const got = []
  const l = voice.listen({ onResult: (t, f) => got.push([t, f]) })
  const rec = FakeRecognition.made.at(-1)
  l.stop()
  rec.onresult({ resultIndex: 0, results: [Object.assign([{ transcript: 'hi' }], { isFinal: true })] })
  assert.deepEqual(got, [['hi', true]])
})

test('listen() asked to stay local sets processLocally on the recogniser', () => {
  voice.listen({ onResult: () => {}, processLocally: true })
  assert.equal(FakeRecognition.made.at(-1).processLocally, true)
  voice.listen({ onResult: () => {} })
  assert.notEqual(FakeRecognition.made.at(-1).processLocally, true)
})

test('availability is what the browser reports, and a downloadable pack is fetched', async () => {
  FakeRecognition.answer = 'available'
  assert.equal(await voice.browserSttAvailability(), 'available')
  FakeRecognition.answer = 'downloadable'
  const before = FakeRecognition.installs
  assert.equal(await voice.browserSttAvailability(), 'downloadable')
  assert.equal(FakeRecognition.installs, before + 1)
  FakeRecognition.answer = 'something-new'
  assert.equal(await voice.browserSttAvailability(), 'unavailable', 'an unknown answer is not local')
  FakeRecognition.answer = 'available'
})
