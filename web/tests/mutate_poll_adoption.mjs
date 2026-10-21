// Mutation harness for poll-adoption.test.js. NOT a test file: it rewrites rail sources.
//
//     node web/tests/mutate_poll_adoption.mjs
//     node web/tests/mutate_poll_adoption.mjs --list     # name every case; run nothing
//
// The shell's and web-core's cases are here. A case that quotes a rail's frontend lives in
// web/tests/mutations/poll-adoption/<rail>.mjs and goes wherever that rail goes: the public
// snapshot withholds the withheld rails' modules (tools/publish.json), and there this harness
// names each withheld module by its file and runs the rest. RAILS names every module.
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import { railCases, runMutations } from './mutation_suite.mjs'

const HERE = dirname(fileURLToPath(import.meta.url))
const REPO = join(HERE, '..', '..')
const RAILS = ['finance', 'course-builder']

const CORE = [
  {
    label: "the shell's status poll goes back to a raw interval",
    file: join(REPO, 'apps', 'platform', 'frontend', 'src', 'ShellModelWidget.tsx'),
    old: '  usePoll((signal) => refresh(signal), STATUS_MS, [refresh])\n',
    new: '  void setInterval\n  usePoll((signal) => refresh(signal), STATUS_MS, [refresh])\n  setInterval(() => void refresh(), STATUS_MS)\n',
    expect: 'no raw setInterval outside the allowed timers',
  },
  {
    label: 'the voice meter allowance goes stale',
    file: join(REPO, 'web', 'src', 'voice.ts'),
    old: '  const poll = window.setInterval(() => {',
    new: '  const poll = window.setTimeout(() => {',
    expect: 'every allowed timer is still there (a stale allowance hides the next one)',
  },
]

const { mutations, withheld } = await railCases({ repo: REPO, dir: join(HERE, 'mutations', 'poll-adoption'), rails: RAILS })
runMutations({
  repo: REPO,
  suite: join(REPO, 'web', 'tests', 'poll-adoption.test.js'),
  mutations: [...CORE, ...mutations],
  withheld,
})
