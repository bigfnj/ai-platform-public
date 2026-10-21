// Where the desk pet's speech bubble goes (src/deskpet/bubble.ts), and that DeskPet.tsx uses it.
//
//   node apps/platform/frontend/tests/deskpet-bubble.test.js
//
// The bubble was placed at the pet's centre with translate(-50%, -100%) and no width. Near the
// right edge it shrank to one word per line and was clipped; near the left edge it was cut off
// at x = 0. The browser half (the built shell, a dragged pet, the bubble's measured rect) is
// web/tests/browser/railcheck.py shell-deskpet-bubble. Mutation-tested by mutate_deskpet_bubble.mjs.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { BUBBLE_GAP, BUBBLE_MARGIN, BUBBLE_MAX_W, placeBubble } from '../src/deskpet/bubble.ts'

const VIEW = { w: 1440, h: 900 }
const BOX = { w: 200, h: 40 }
const pet = (x, top, h = 64) => ({ x, top, bottom: top + h })

test('with room on every side the bubble is centred over the pet, the gap above its head', () => {
  assert.deepEqual(placeBubble(pet(500, 400), BOX, VIEW), { left: 400, top: 400 - BUBBLE_GAP - 40 })
})

test('near the right edge it slides left and stays inside the window', () => {
  assert.equal(placeBubble(pet(1435, 400), BOX, VIEW).left, VIEW.w - BUBBLE_MARGIN - BOX.w)
  assert.equal(placeBubble(pet(VIEW.w + 30, 400), BOX, VIEW).left, VIEW.w - BUBBLE_MARGIN - BOX.w, 'a pet half off screen')
})

test('near the left edge it slides right instead of going off at x = 0', () => {
  assert.equal(placeBubble(pet(5, 400), BOX, VIEW).left, BUBBLE_MARGIN)
  assert.equal(placeBubble(pet(-40, 400), BOX, VIEW).left, BUBBLE_MARGIN, 'a pet half off screen')
})

test('with no room over the pet it goes under it, and with no room either way it stays inside', () => {
  assert.equal(placeBubble(pet(500, 20), BOX, VIEW).top, 20 + 64 + BUBBLE_GAP, 'under the pet')
  const short = { w: 1440, h: 120 }
  const { top } = placeBubble(pet(500, 30), BOX, short)
  assert.ok(top >= BUBBLE_MARGIN && top + BOX.h <= short.h - BUBBLE_MARGIN, `top ${top} in a 120px window`)
})

test('a bubble wider than the room between the margins is pinned to the left margin', () => {
  assert.equal(placeBubble(pet(100, 400), { w: 400, h: 40 }, { w: 300, h: 900 }).left, BUBBLE_MARGIN)
})

test('wherever the pet is, the bubble is inside the window and never on the pet', () => {
  // A sweep, so the property holds between the hand-picked cases above too. Both axes vary,
  // including pet positions partly off screen, and three box sizes, one of them the cap.
  let n = 0
  for (const box of [{ w: 60, h: 24 }, BOX, { w: BUBBLE_MAX_W, h: 90 }]) {
    for (let x = -60; x <= VIEW.w + 60; x += 37) {
      for (let top = -40; top <= VIEW.h; top += 29) {
        const p = pet(x, top)
        const at = placeBubble(p, box, VIEW)
        const where = `pet (${x}, ${top}), box ${box.w}x${box.h}: ${JSON.stringify(at)}`
        assert.ok(at.left >= BUBBLE_MARGIN && at.left + box.w <= VIEW.w - BUBBLE_MARGIN, `x outside, ${where}`)
        assert.ok(at.top >= BUBBLE_MARGIN && at.top + box.h <= VIEW.h - BUBBLE_MARGIN, `y outside, ${where}`)
        // On screen, the bubble sits wholly above or wholly below the sprite.
        if (p.top >= 0 && p.bottom <= VIEW.h)
          assert.ok(at.top + box.h <= p.top || at.top >= p.bottom, `over the pet, ${where}`)
        n++
      }
    }
  }
  assert.ok(n > 3000, `only ${n} placements checked`)
})

test('DeskPet places the measured, text-sized bubble with placeBubble and no translate', () => {
  const src = readFileSync(new URL('../src/deskpet/DeskPet.tsx', import.meta.url), 'utf8')
  assert.match(src, /const at = placeBubble\(pos, size, \{ w: window\.innerWidth, h: window\.innerHeight \}\)/)
  assert.match(src, /left: at\.left,\s+top: at\.top,/)
  assert.match(src, /width: "max-content",/, 'a width that follows the left offset squeezes the text again')
  assert.match(src, /const w = el\.offsetWidth;\s+const h = el\.offsetHeight;\s+setSize\(/)
  assert.doesNotMatch(src, /translate\(-50%/, 'the old centring transform is back')
})
