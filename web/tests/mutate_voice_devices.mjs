// Mutation harness for voice-devices.test.js. NOT a test file: it rewrites web/src/voice.ts,
// read_aloud.ts and platformApi.ts.
//
//     node web/tests/mutate_voice_devices.mjs
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import { runMutations } from './mutation_suite.mjs'

const REPO = join(dirname(fileURLToPath(import.meta.url)), '..', '..')
const SRC = join(REPO, 'web', 'src', 'voice.ts')
const READ = join(REPO, 'web', 'src', 'read_aloud.ts')
const API = join(REPO, 'web', 'src', 'platformApi.ts')

runMutations({
  repo: REPO,
  suite: join(REPO, 'web', 'tests', 'voice-devices.test.js'),
  mutations: [
    {
      label: 'the microphone is cached at load again (the old module variable)',
      file: SRC,
      old: "const selectedInput = () => lsGet(INPUT_KEY)\n",
      new: "const loadedInput = lsGet(INPUT_KEY)\nconst selectedInput = () => loadedInput\n",
      expect: 'a microphone chosen after this copy loaded is the one recording asks for',
    },
    {
      label: 'the speaker is cached at load again',
      file: SRC,
      old: "const selectedSink = () => lsGet(SINK_KEY)\n",
      new: "const loadedSink = lsGet(SINK_KEY)\nconst selectedSink = () => loadedSink\n",
      expect: 'a speaker chosen after this copy loaded is the one playback routes to',
    },
    {
      label: 'an unmount stops any playback (the old unconditional stop)',
      file: SRC,
      old: '  if (owner && currentOwner === owner) stopSpeaking()\n',
      new: '  stopSpeaking()\n',
      expect: "an unmounting chip stops only its own playback",
    },
    {
      label: 'a replaced playback is paused without a word',
      file: SRC,
      old: '  currentEnd = null\n  end?.()\n}',
      new: '  currentEnd = null\n}',
      expect: 'the chip whose audio another one replaces is told it ended',
    },
    {
      label: 'onEnd can run twice',
      file: SRC,
      old: '  return () => { if (!done) { done = true; fn() } }\n',
      new: '  return () => { done = true; fn() }\n',
      expect: 'the chip whose audio another one replaces is told it ended',
    },
    // A read-aloud chip's reads (read_aloud.ts), and the request they abort.
    {
      label: 'Stop aborts nothing, so the request runs on',
      file: READ,
      old: '    stop(): void {\n      live?.abort()\n      live = null\n',
      new: '    stop(): void {\n      live = null\n',
      expect: 'Stop aborts the read-aloud request in flight, and the read plays nothing',
    },
    {
      label: "ttsLight drops the caller's signal",
      file: API,
      old: "'/api/platform/tts_light', { method: 'POST', body: JSON.stringify(body), signal }),",
      new: "'/api/platform/tts_light', { method: 'POST', body: JSON.stringify(body) }),",
      expect: 'Stop aborts the read-aloud request in flight, and the read plays nothing',
    },
    {
      label: 'a new read leaves the one before it asking',
      file: READ,
      old: '      live?.abort()\n      const mine = new AbortController()\n',
      new: '      const mine = new AbortController()\n',
      expect: 'a new read aborts the one before it',
    },
    {
      label: 'a reply to a stopped read plays',
      file: READ,
      old: '      if (!mine.signal.aborted) play(answer)\n',
      new: '      play(answer)\n',
      expect: 'a reply to a stopped read plays nothing, even from a request that ignored the abort',
    },
    {
      label: 'a stopped read whose request fails falls back to the on-device voice',
      file: READ,
      old: '        if (!mine.signal.aborted) fallback()\n',
      new: '        fallback()\n',
      expect: 'a failed request after Stop does not fall back to the on-device voice',
    },
    {
      label: 'a failed read nobody stopped does not fall back',
      file: READ,
      old: '        if (!mine.signal.aborted) fallback()\n',
      new: '        void fallback\n',
      expect: 'a read nobody stopped plays its reply, and one whose request fails falls back',
    },
  ],
})
