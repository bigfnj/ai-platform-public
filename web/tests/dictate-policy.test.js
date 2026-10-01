// Which STT path a dictation press may use. Plain `node --test`, like speech-lang.test.js:
// dictate_policy.ts has no runtime imports, so node's type stripping loads it directly and this
// needs no bundler and no React harness.
//
// This test exists because the thing it guards has no visible failure mode. A dictation that
// wrongly reaches Google's cloud STT transcribes perfectly; the only symptom is that a child's
// voice left the machine. Six of seven call sites were in that state, and the guard that was
// supposed to stop it was documented, working, and switched on in one place.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { chooseSttPath } from '../src/dictate_policy.ts'

const AVAILABILITIES = ['available', 'downloadable', 'downloading', 'unavailable', null]

test('on-device is chosen whenever it is ready, opt-in or not', () => {
  // On-device is LOCAL. Keeping it available without the opt-in is what stops the inversion
  // costing the good path on every rail.
  assert.equal(chooseSttPath({ hasCtor: true, availability: 'available' }), 'ondevice')
  assert.equal(
    chooseSttPath({ hasCtor: true, availability: 'available', allowCloudStt: true }), 'ondevice')
})

test('without the opt-in, nothing reaches a cloud recognizer', () => {
  for (const availability of AVAILABILITIES) {
    const got = chooseSttPath({ hasCtor: true, availability })
    assert.notEqual(got, 'cloud',
      `availability=${availability} chose ${got} with no allowCloudStt`)
  }
})

test('the default for a rail that says nothing is the local broker', () => {
  // The exact case that was leaking: an older Chrome with no on-device statics to ask.
  assert.equal(chooseSttPath({ hasCtor: true, availability: null }), 'broker')
  assert.equal(chooseSttPath({ hasCtor: true, availability: 'downloadable' }), 'broker')
  assert.equal(chooseSttPath({ hasCtor: true, availability: 'unavailable' }), 'broker')
})

test('cloud is reachable only by an explicit opt-in', () => {
  assert.equal(
    chooseSttPath({ hasCtor: true, availability: null, allowCloudStt: true }), 'cloud')
  assert.equal(
    chooseSttPath({ hasCtor: true, availability: 'downloadable', allowCloudStt: true }), 'cloud')
})

test('a falsy opt-in is not an opt-in', () => {
  // undefined is the default; false must behave identically, or a rail that deliberately
  // writes allowCloudStt={false} gets the opposite of what it asked for.
  for (const flag of [undefined, false]) {
    assert.equal(chooseSttPath({ hasCtor: true, availability: null, allowCloudStt: flag }),
      'broker', `allowCloudStt=${flag} must mean local`)
  }
})

test('no browser API at all falls to the broker, even with the opt-in', () => {
  assert.equal(chooseSttPath({ hasCtor: false, availability: null }), 'broker')
  assert.equal(
    chooseSttPath({ hasCtor: false, availability: 'available', allowCloudStt: true }), 'broker')
})

test('every combination returns one of the three known paths', () => {
  const paths = new Set(['ondevice', 'cloud', 'broker'])
  for (const hasCtor of [true, false]) {
    for (const availability of AVAILABILITIES) {
      for (const allowCloudStt of [undefined, false, true]) {
        const got = chooseSttPath({ hasCtor, availability, allowCloudStt })
        assert.ok(paths.has(got), `${hasCtor}/${availability}/${allowCloudStt} -> ${got}`)
      }
    }
  }
})
