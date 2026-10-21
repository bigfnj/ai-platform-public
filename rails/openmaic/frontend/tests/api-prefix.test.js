// The request shim the rail patch tool writes into an OpenMAIC checkout as
// instrumentation-client.ts (rails/openmaic/tools/platform-api-prefix.ts).
//
//   node --test rails/openmaic/frontend/tests/api-prefix.test.js
//
// Plain node: the shim has no imports, so node's type stripping loads it directly, and its
// install call is skipped because node has no `window`. Driven from pytest by
// tests/test_frontend_helpers.py.
//
// The defect: under the basePath, OpenMAIC's hand-written fetch('/api/...') calls
// resolved against the origin root and 404ed at the gateway, three of them on every page load.
// What this file cannot show is that Next runs the shim before hydration and inlines the base:
// that was checked by hand against a real Next 16.3.3 build when the shim was written.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { installApiPrefix, normaliseBase, rewriteApiUrl } from '../../tools/platform-api-prefix.ts'

const BASE = '/openmaic/api/app'
const PAGE = 'http://localhost:1111/openmaic/api/app/classroom/1'
const LOAD3 = ['/api/server-providers', '/api/comfyui-workflows', '/api/access-code/status']

function fakeWindow(pathname = '/openmaic/api/app/classroom/1') {
  const calls = []
  class FakeEventSource {
    static CONNECTING = 0
    static OPEN = 1
    static CLOSED = 2
    constructor(url, init) { this.url = url; this.init = init }
  }
  class FakeXHR {
    open(...args) { calls.push({ api: 'xhr', args }) }
  }
  const win = {
    location: { href: `http://localhost:1111${pathname}`, pathname },
    Request,
    EventSource: FakeEventSource,
    XMLHttpRequest: FakeXHR,
  }
  win.fetch = function (input, init) {
    calls.push({ api: 'fetch', input, init, self: this })
    return Promise.resolve('ok')
  }
  return { win, calls, FakeEventSource, FakeXHR }
}

test('normaliseBase drops a trailing slash and refuses anything that is not a path', () => {
  assert.equal(normaliseBase('/openmaic/api/app/'), BASE)
  assert.equal(normaliseBase(BASE), BASE)
  assert.equal(normaliseBase(''), '')
  assert.equal(normaliseBase(undefined), '')
  assert.equal(normaliseBase('openmaic/api/app'), '')
})

test('the three routes the app fetches on every load go under the basePath', () => {
  for (const r of LOAD3) assert.equal(rewriteApiUrl(r, BASE, PAGE), BASE + r)
})

test('query and fragment survive, and a root-relative string stays root-relative', () => {
  assert.equal(rewriteApiUrl('/api/folders/a%20b?mode=ungroup&x=1&x=2#h', BASE, PAGE),
    BASE + '/api/folders/a%20b?mode=ungroup&x=1&x=2#h')
})

test('an absolute same-origin URL is moved and comes back absolute', () => {
  assert.equal(rewriteApiUrl('http://localhost:1111/api/stages', BASE, PAGE),
    'http://localhost:1111' + BASE + '/api/stages')
})

test('everything that is not a same-origin /api/ URL is left exactly as it was', () => {
  for (const u of [
    'https://dashscope.aliyuncs.com/api/v1/services/audio', // a provider's own /api/
    '//elsewhere.example/api/x',                           // protocol-relative, other origin
    BASE + '/api/server-providers',                        // already under the prefix
    BASE + '/_next/static/chunks/a.js',                    // Next's own, already prefixed
    '/logos/openai.svg',                                   // a root asset (rail.json root_assets)
    '/apix/y',
    '/api',
    'data:text/plain,hi',
    'http://[',                                            // does not parse: returned, no throw
  ]) assert.equal(rewriteApiUrl(u, BASE, PAGE), u, u)
})

test('with no base nothing is rewritten', () => {
  assert.equal(rewriteApiUrl('/api/server-providers', '', PAGE), '/api/server-providers')
})

test('install refuses with no base, and on a page that is not under the base', () => {
  const a = fakeWindow()
  const before = a.win.fetch
  assert.equal(installApiPrefix(a.win, ''), false)
  assert.equal(installApiPrefix(a.win, undefined), false)
  assert.equal(a.win.fetch, before)
  const b = fakeWindow('/')                      // the shell's own document
  const shellFetch = b.win.fetch
  assert.equal(installApiPrefix(b.win, BASE), false)
  assert.equal(b.win.fetch, shellFetch)
  const c = fakeWindow('/openmaic/api/application') // shares the prefix text, not the path
  assert.equal(installApiPrefix(c.win, BASE), false)
})

test('fetch: string, Request and URL inputs are moved; init and this are passed through', async () => {
  const { win, calls } = fakeWindow()
  assert.equal(installApiPrefix(win, BASE + '/'), true)
  const init = { headers: { x: '1' } }
  await win.fetch('/api/server-providers', init)
  await win.fetch(new Request('http://localhost:1111/api/verify-model', { method: 'POST', body: 'b' }))
  await win.fetch(new URL('/api/usage', 'http://localhost:1111/'))
  await win.fetch('https://other.example/api/z')
  assert.equal(calls[0].input, BASE + '/api/server-providers')
  assert.equal(calls[0].init, init)
  assert.equal(calls[0].self, win)
  assert.ok(calls[1].input instanceof Request)
  assert.equal(calls[1].input.url, 'http://localhost:1111' + BASE + '/api/verify-model')
  assert.equal(calls[1].input.method, 'POST')
  assert.equal(await calls[1].input.text(), 'b')
  assert.equal(calls[2].input, 'http://localhost:1111' + BASE + '/api/usage')
  assert.equal(calls[3].input, 'https://other.example/api/z')
})

test('XMLHttpRequest.open moves the URL and keeps every other argument', () => {
  const { win, calls, FakeXHR } = fakeWindow()
  installApiPrefix(win, BASE)
  new FakeXHR().open('GET', '/api/access-code/status')
  new FakeXHR().open('PUT', '/api/x', false, 'u', 'p')
  assert.deepEqual(calls[0].args, ['GET', BASE + '/api/access-code/status'])
  assert.deepEqual(calls[1].args, ['PUT', BASE + '/api/x', false, 'u', 'p'])
})

test('EventSource is a subclass: the URL moves, instanceof and the statics still hold', () => {
  const { win, FakeEventSource } = fakeWindow()
  installApiPrefix(win, BASE)
  const es = new win.EventSource('/api/agent/owner-events', { withCredentials: true })
  assert.equal(es.url, BASE + '/api/agent/owner-events')
  assert.deepEqual(es.init, { withCredentials: true })
  assert.ok(es instanceof FakeEventSource)
  assert.equal(win.EventSource.OPEN, 1)
})

test('a second install changes nothing, so nothing is wrapped (or prefixed) twice', async () => {
  const { win, calls } = fakeWindow()
  installApiPrefix(win, BASE)
  const once = win.fetch
  assert.equal(installApiPrefix(win, BASE), true)
  assert.equal(win.fetch, once)
  await win.fetch('/api/comfyui-workflows')
  assert.equal(calls.length, 1)
  assert.equal(calls[0].input, BASE + '/api/comfyui-workflows')
})
