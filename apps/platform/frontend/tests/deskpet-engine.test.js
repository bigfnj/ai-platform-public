// The desk pet's vendored engine (src/deskpet/esheep.js): its two local fixes.
//
//   node apps/platform/frontend/tests/deskpet-engine.test.js
//
// 1. The expression evaluator understands Convert(expr,System.Int32) and %, the forms Pearl's
//    black-sheep walk uses. Without them every step of that walk threw, logged a console error
//    and evaluated its repeat count to 0.
// 2. A spawn whose child would be stranded off-screen is not picked. Pearl's bathtub gag is
//    landscape geometry; on a portrait window the tub and the dive both happened off-screen.
//
// esheep.js touches the DOM only in its constructor and at run time, and guards its module-level
// lookups, so node imports it. Methods are called on Object.create(eSheep.prototype) with plain
// fields, and the pet XML's <spawns> and <childs> are read from the REAL animation.xml into
// minimal stand-ins for the DOM nodes the engine walks, so the data under test is the shipped
// pet, not a fixture that could drift from it.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import eSheep, { dotnetToInt32, spriteStranded } from '../src/deskpet/esheep.js'

const XML = readFileSync(new URL('../src/deskpet/animation.xml', import.meta.url), 'utf8')
const BLACK_SHEEP_REPEAT = '23+(Convert(screenW/2,System.Int32)%30)/7'

// --- minimal DOM stand-ins for the parts of the XML the spawn code reads ---------------------
function el(tag, attrs = {}, kids = [], text = '') {
  return {
    tag,
    textContent: text,
    getAttribute: (n) => (n in attrs ? attrs[n] : null),
    getElementsByTagName: (t) => kids.filter((k) => k.tag === t),
  }
}
const field = (body, tag) => body.match(new RegExp(`<${tag}[^>]*>([\\s\\S]*?)</${tag}>`))[1].trim()
const SPAWNS = [...XML.matchAll(/<spawn id="(\d+)" probability="(\d+)">([\s\S]*?)<\/spawn>/g)].map(([, id, p, b]) =>
  el('spawn', { id, probability: p }, ['x', 'y', 'next'].map((t) => el(t, {}, [], field(b, t)))))
const CHILDS = [...XML.matchAll(/<child animationid="(\d+)">([\s\S]*?)<\/child>/g)].map(([, id, b]) =>
  el('child', { animationid: id }, ['x', 'y', 'next'].map((t) => el(t, {}, [], field(b, t)))))
// <animations> is deliberately EMPTY: _spawnESheep then finds no animation node for the pick, so
// it starts no child (which would need a real DOM) and only records which spawn it chose.
const DOC = {
  getElementsByTagName: (t) => ({
    spawns: [el('spawns', {}, SPAWNS)], childs: [el('childs', {}, CHILDS)], animations: [el('animations')],
  })[t] ?? [],
}

function sheep(fields = {}) {
  return Object.assign(Object.create(eSheep.prototype), {
    screenW: 1280, screenH: 720, imageW: 40, imageH: 40, imageX: 7, imageY: 9, randS: 50, xmlDoc: DOC,
    _nextESheepStep() {},  // the runtime loop: not under test, and it would schedule timers
  }, fields)
}

/** Run fn with console.error captured; returns what it logged. */
function quietly(fn) {
  const real = console.error
  const logged = []
  console.error = (...a) => logged.push(a.join(' '))
  try { fn() } finally { console.error = real }
  return logged
}

/** Run fn with Math.random pinned. */
function withRandom(r, fn) {
  const real = Math.random
  Math.random = () => r
  try { return fn() } finally { Math.random = real }
}

// =============================================================================================
// 1. Convert(...,System.Int32) and %
// =============================================================================================

test('the pet XML really carries the black-sheep repeat expression, twice', () => {
  // Witness: if the pet changes, the tests below must be re-aimed rather than pass on nothing.
  assert.equal(XML.split(`repeat="${BLACK_SHEEP_REPEAT}"`).length - 1, 2)
})

test('the black-sheep repeat expression evaluates as the desktop engine does, with no error', () => {
  for (const [screenW, want] of [
    [1280, 23 + (640 % 30) / 7],   // 640: whole
    [1283, 23 + (642 % 30) / 7],   // 641.5 -> 642: the tie goes to the even integer
    [1281, 23 + (640 % 30) / 7],   // 640.5 -> 640: likewise
    [390, 23 + (195 % 30) / 7],
  ]) {
    let got
    const logged = quietly(() => { got = sheep({ screenW })._parseKeyWords(BLACK_SHEEP_REPEAT) })
    assert.deepEqual(logged, [], `screenW=${screenW} logged an error`)
    assert.ok(Math.abs(got - want) < 1e-12, `screenW=${screenW}: ${got} != ${want}`)
  }
})

test('Convert follows .NET Convert.ToInt32(double): nearest, ties to even, overflow throws', () => {
  for (const [v, want] of [[640.5, 640], [641.5, 642], [2.4, 2], [2.6, 3], [-2.5, -2], [-3.5, -4], [-0.4, 0], [7, 7]])
    assert.equal(dotnetToInt32(v), want, `Convert(${v})`)
  for (const v of [NaN, Infinity, 3e9, -3e9]) assert.throws(() => dotnetToInt32(v), `Convert(${v}) must throw`)
  assert.equal(sheep()._evalArithmetic('Convert(5/2,System.Int32)'), 2)
  assert.equal(sheep()._evalArithmetic('Convert( 7/2 , System.Int32 )+1'), 5)
})

test('% keeps the sign of the dividend, at the precedence of * and /', () => {
  const s = sheep()
  assert.equal(s._evalArithmetic('7%3'), 1)
  assert.equal(s._evalArithmetic('-7%3'), -1)       // sign of the dividend, as in C#
  assert.equal(s._evalArithmetic('2+7%3*2'), 4)     // 2 + ((7 % 3) * 2)
})

test('anything outside arithmetic and that one Convert form is still refused', () => {
  // The evaluator replaced eval() as a security boundary; adding Convert must not open it.
  const s = sheep()
  for (const bad of ['alert(1)', 'Convert(1,System.Int64)', 'Convert(1)', 'Convert 1', '1,2',
    'System.Int32', 'Convert(1,System.Int32', 'Convert(1,System.Int32)Convert(2,System.Int32)',
    'constructor', 'Convert(1,System.Int32).x', '(1,System.Int32)'])
    assert.throws(() => s._evalArithmetic(bad), `accepted: ${bad}`)
  // And the caller's contract is unchanged: a refused position logs and reads as 0.
  let got
  const logged = quietly(() => { got = s._parseKeyWords('alert(1)') })
  assert.equal(got, 0)
  assert.equal(logged.length, 1)
})

// =============================================================================================
// 2. The spawn filter
// =============================================================================================

const ids = (list) => list.map((s) => s.getAttribute('id'))
const ALL = ids(SPAWNS)
const RAND_S = [0, 12.5, 25, 50, 75, 99.9]

test('the XML stand-ins read the real spawns and child definitions', () => {
  assert.equal(SPAWNS.length, XML.split('<spawn ').length - 1)
  assert.equal(CHILDS.length, XML.split('<child ').length - 1)
  const dive = SPAWNS.find((s) => s.getElementsByTagName('next')[0].textContent === '21')
  assert.ok(dive, 'no spawn starts animation 21 (the dive into the bathtub)')
  assert.equal(dive.getAttribute('id'), '3')
})

test('a sprite one size beyond an edge is an entrance; further out it is stranded', () => {
  const [w, h, W, H] = [40, 40, 1280, 720]
  for (const [x, y] of [[-40, 680], [1280, 680], [843, 680], [0, -40], [10, 720]])
    assert.equal(spriteStranded(x, y, w, h, W, H), false, `(${x},${y}) is reachable`)
  for (const [x, y] of [[-364, 804], [-80, 680], [1320, 680], [10, -80], [10, 760]])
    assert.equal(spriteStranded(x, y, w, h, W, H), true, `(${x},${y}) is stranded`)
})

test('on a portrait window the bathtub spawn is dropped exactly when its tub is stranded', () => {
  // The tub's x from the pet's own expression: screenW - areaH/2 - (randS*areaH/2)/120. At
  // 390x844 that is -32 for randS 0 (8 px still on screen, the dive lands on it, so it stays)
  // and stranded from randS ~14 up. Nothing other than spawn 3 is ever dropped.
  let dropped = 0
  for (const randS of [0, 12.5, 14, 25, 50, 75, 99.9]) {
    const tubX = Math.trunc(390 - 844 / 2 - (randS * 844 / 2) / 120)
    const stranded = tubX <= -2 * 40
    const s = sheep({ screenW: 390, screenH: 844, randS })
    const want = stranded ? ALL.filter((id) => id !== '3') : ALL
    assert.deepEqual(ids(s._spawnCandidates()), want, `randS=${randS} (tub at x=${tubX})`)
    dropped += stranded
  }
  assert.ok(dropped >= 4, `only ${dropped} of the sampled randS values strand the tub`)
})

test('on a landscape window every spawn stays a candidate, in document order', () => {
  // Same list in the same order is what keeps the weighted pick, and its single random draw,
  // exactly as it was before the filter existed.
  for (const [W, H] of [[1280, 720], [1920, 937], [1366, 657], [1024, 768], [937, 937]])
    for (const randS of RAND_S) {
      const got = sheep({ screenW: W, screenH: H, randS })._spawnCandidates()
      assert.deepEqual(got, SPAWNS, `${W}x${H} randS=${randS}`)
    }
})

test('checking candidates moves nothing', () => {
  const s = sheep({ screenW: 390, screenH: 844 })
  s._spawnCandidates()
  assert.deepEqual([s.imageX, s.imageY], [7, 9])
})

test('if every spawn were stranded, the pet still spawns rather than vanishing', () => {
  const only = [el('spawn', { id: '1', probability: '10' },
    [el('x', {}, [], '0'), el('y', {}, [], '0'), el('next', {}, [], '21')])]
  const doc = { getElementsByTagName: (t) => ({ spawns: [el('spawns', {}, only)], childs: [el('childs', {}, CHILDS)] })[t] ?? [] }
  const s = sheep({ screenW: 390, screenH: 844, xmlDoc: doc })
  assert.equal(s._spawnCandidates().length, 1)
})

// The spawn pick as it stood before the filter, frozen as the oracle for "landscape unchanged".
function legacyPick(r) {
  let prob = 0
  for (const s of SPAWNS) prob += parseInt(s.getAttribute('probability'))
  const rand = r * prob
  prob = 0
  for (let i = 0; i < SPAWNS.length; i++) {
    prob += parseInt(SPAWNS[i].getAttribute('probability'))
    if (prob >= rand || i === SPAWNS.length - 1) return SPAWNS[i].getElementsByTagName('next')[0].textContent
  }
}

test('_spawnESheep picks from the candidates: portrait never dives, landscape picks as before', () => {
  for (let k = 0; k < 100; k++) {
    const r = k / 100
    const wide = sheep()
    withRandom(r, () => wide._spawnESheep())
    assert.equal(wide.animationId, legacyPick(r), `1280x720, random=${r}`)
    const tall = sheep({ screenW: 390, screenH: 844 })
    withRandom(r, () => tall._spawnESheep())
    assert.notEqual(tall.animationId, '21', `390x844, random=${r} picked the dive`)
  }
  // The witness that the comparison can tell them apart: 0.16 is the dive on landscape.
  const wide = sheep()
  withRandom(0.16, () => wide._spawnESheep())
  assert.equal(wide.animationId, '21')
})
