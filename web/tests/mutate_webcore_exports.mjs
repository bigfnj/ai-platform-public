// Mutation harness for webcore-exports.test.js. NOT a test file: it rewrites web/src/index.ts.
//
//     node web/tests/mutate_webcore_exports.mjs
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import { runMutations } from './mutation_suite.mjs'

const REPO = join(dirname(fileURLToPath(import.meta.url)), '..', '..')
const SRC = join(REPO, 'web', 'src', 'index.ts')
const TEST = join(REPO, 'web', 'tests', 'webcore-exports.test.js')

runMutations({
  repo: REPO,
  suite: TEST,
  mutations: [
    {
      label: 'a dead voice export comes back',
      file: SRC,
      old: "export { VoiceControls } from './VoiceControls'\n",
      new: "export { VoiceControls } from './VoiceControls'\nexport { playWav, stopSpeaking } from './voice'\n",
      expect: 'every value export has an importer outside web-core, or a reason to stay',
    },
    {
      label: 'a dead primitive comes back',
      file: SRC,
      old: "export { Badge, Button } from './ui'\n",
      new: "export { Badge, Button, Spinner } from './ui'\n",
      expect: 'every value export has an importer outside web-core, or a reason to stay',
    },
    {
      // Re-anchored 2026-10-14: startPoll, the KEEP entry it removed, left the barrel itself.
      label: 'a KEEP entry outlives its export',
      file: SRC,
      old: "export { ModelChips, MODEL_STATE_TEXT } from './ModelChips'\n",
      new: "export { ModelChips } from './ModelChips'\n",
      expect: 'every KEEP entry is still exported and still unimported',
    },
    {
      label: 'startPoll is exported again with nothing importing it',
      file: SRC,
      old: "export { stallDetector } from './poll'\n",
      new: "export { startPoll, stallDetector } from './poll'\n",
      expect: 'every value export has an importer outside web-core, or a reason to stay',
    },
  ],
})
