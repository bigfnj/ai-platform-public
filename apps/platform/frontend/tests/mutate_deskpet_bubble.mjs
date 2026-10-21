// Mutation harness for deskpet-bubble.test.js. NOT a test file: it rewrites the sources.
//
//     node apps/platform/frontend/tests/mutate_deskpet_bubble.mjs
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import { runMutations } from '../../../../web/tests/mutation_suite.mjs'

const HERE = dirname(fileURLToPath(import.meta.url))
const REPO = join(HERE, '..', '..', '..', '..')
const PLACE = join(HERE, '..', 'src', 'deskpet', 'bubble.ts')
const PET = join(HERE, '..', 'src', 'deskpet', 'DeskPet.tsx')

const T = {
  centred: 'with room on every side the bubble is centred over the pet, the gap above its head',
  right: 'near the right edge it slides left and stays inside the window',
  left: 'near the left edge it slides right instead of going off at x = 0',
  under: 'with no room over the pet it goes under it, and with no room either way it stays inside',
  wide: 'a bubble wider than the room between the margins is pinned to the left margin',
  source: 'DeskPet places the measured, text-sized bubble with placeBubble and no translate',
}

runMutations({
  repo: REPO,
  suite: join(HERE, 'deskpet-bubble.test.js'),
  mutations: [
    {
      label: 'no right-edge clamp (the clipped "mailman" bubble)',
      file: PLACE,
      old: '  const left = clamp(pet.x - box.w / 2, m, view.w - m - box.w)\n',
      new: '  const left = Math.max(m, pet.x - box.w / 2)\n',
      expect: T.right,
    },
    {
      label: 'no left-edge clamp (the bubble cut off at x = 0)',
      file: PLACE,
      old: '  const left = clamp(pet.x - box.w / 2, m, view.w - m - box.w)\n',
      new: '  const left = Math.min(pet.x - box.w / 2, view.w - m - box.w)\n',
      expect: T.left,
    },
    {
      label: 'never under the pet, so a pet at the top lifts its bubble off screen',
      file: PLACE,
      old: '  else if (below + box.h <= view.h - m) top = below\n',
      new: '  else if (below < 0) top = below\n',
      expect: T.under,
    },
    {
      label: 'the gap over the pet is dropped',
      file: PLACE,
      old: '  const above = pet.top - BUBBLE_GAP - box.h\n',
      new: '  const above = pet.top - box.h\n',
      expect: T.centred,
    },
    {
      label: 'clamp lets a box wider than the room run past the left margin',
      file: PLACE,
      old: '  return Math.max(lo, Math.min(v, hi))\n',
      new: '  return Math.min(hi, Math.max(v, lo))\n',
      expect: T.wide,
    },
    {
      label: 'the bubble is sized by its left offset again (one word per line)',
      file: PET,
      old: '        width: "max-content",\n',
      new: '',
      expect: T.source,
    },
    {
      label: 'the old centring transform comes back',
      file: PET,
      old: '        left: at.left,\n        top: at.top,\n',
      new: '        left: pos.x,\n        top: Math.max(8, pos.top - 10),\n        transform: "translate(-50%, -100%)",\n',
      expect: T.source,
    },
  ],
})
