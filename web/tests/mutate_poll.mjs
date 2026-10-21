// Mutation harness for poll.test.js. NOT a test file: it rewrites web/src/poll.ts.
//
//     node web/tests/mutate_poll.mjs
//
// Each mutation is one of the poll defects the audit found somewhere in the 18 pollers:
// polling a hidden tab, overlapping a slow answer, a 'stop' nobody honours, a throw that kills
// the loop, an unmount that leaves the request running.
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import { runMutations } from './mutation_suite.mjs'

const REPO = join(dirname(fileURLToPath(import.meta.url)), '..', '..')
const SRC = join(REPO, 'web', 'src', 'poll.ts')

runMutations({
  repo: REPO,
  suite: join(REPO, 'web', 'tests', 'poll.test.js'),
  mutations: [
    {
      label: 'a hidden page is polled like a visible one',
      file: SRC,
      old: '    if (env.hidden()) {\n      owed = true\n      return\n    }\n',
      new: '',
      expect: 'nothing is fetched while the page is hidden, and the owed tick runs on return',
    },
    {
      label: 'the owed tick is dropped when the page comes back',
      file: SRC,
      old: '    owed = false\n    void fire()\n',
      new: '    owed = false\n',
      expect: 'nothing is fetched while the page is hidden, and the owed tick runs on return',
    },
    {
      label: 'every visibility change runs a tick, owed or not',
      file: SRC,
      old: '    if (stopped || env.hidden() || !owed || inFlight || timer !== null) return',
      new: '    if (stopped || env.hidden() || inFlight) return',
      expect: 'a visibility change with no tick owed does not add one',
    },
    {
      label: 'the next tick is scheduled before the answer arrives (setInterval semantics)',
      file: SRC,
      old: '    inFlight = true\n    let verdict: PollVerdict\n',
      new: '    inFlight = true\n    schedule()\n    let verdict: PollVerdict\n',
      expect: 'a slow tick is never overlapped: the next one waits for it to settle',
    },
    {
      label: "'stop' is ignored",
      file: SRC,
      old: "    if (verdict === 'stop') {\n      stop()\n      return\n    }\n",
      new: '',
      expect: "a tick that returns 'stop' ends the poll and lets go of the page",
    },
    {
      label: 'stop leaves the visibility listener behind',
      file: SRC,
      old: '    unsubscribe()\n    ac.abort()',
      new: '    ac.abort()',
      expect: "a tick that returns 'stop' ends the poll and lets go of the page",
    },
    {
      label: 'a throwing tick ends the poll',
      file: SRC,
      old: '    } catch {\n      verdict = undefined\n    }',
      new: "    } catch {\n      verdict = 'stop'\n    }",
      expect: 'a tick that throws does not end the poll',
    },
    {
      label: 'stop() no longer aborts the request in flight',
      file: SRC,
      old: '    unsubscribe()\n    ac.abort()',
      new: '    unsubscribe()',
      expect: 'stop() aborts the in-flight request and nothing is scheduled after it settles',
    },
    {
      label: 'a tick settling after stop() schedules another',
      file: SRC,
      old: '    inFlight = false\n    if (stopped) return\n',
      new: '    inFlight = false\n    stopped = false\n',
      expect: 'stop() aborts the in-flight request and nothing is scheduled after it settles',
    },
    {
      label: 'a poll started hidden ticks anyway at start',
      file: SRC,
      old: '  void fire()\n  return stop\n',
      new: '  void tick(ac.signal)\n  schedule()\n  return stop\n',
      expect: 'a poll started in a hidden page waits for it to be visible',
    },
    {
      label: 'stallDetector never resets on a change',
      file: SRC,
      old: '    same = signature === last ? same + 1 : 0\n',
      new: '    same = same + 1\n',
      expect: 'stallDetector: true once N answers in a row change nothing, and a change resets it',
    },
    {
      label: 'stallDetector stalls one answer early',
      file: SRC,
      old: '    return same >= limit\n',
      new: '    return same >= limit - 1\n',
      expect: 'stallDetector: true once N answers in a row change nothing, and a change resets it',
    },
  ],
})
