// What a rail's model chip and the top-bar widget's Claude section say.
//
//   node web/tests/model-chips.test.js
//
// Plain node, like dictate-policy.test.js: src/model_chip_view.ts has no runtime imports, so
// node's type stripping loads it directly and nothing here needs a bundler or a React harness.
//
// TWO CLAIMS ARE PINNED HERE, AND THE SECOND IS THE ONE MOST LIKELY TO ROT.
//   1. A slot whose own-jobs backend is Claude says so: the chip reads "Claude" ("Claude ·
//      working" while a call is in flight), the dot is the fixed --cli violet, and the tooltip
//      names the local model as the FALLBACK with its state. A chip that kept saying "cold"
//      while every answer came from Claude would be the old screen lying.
//   2. Everything else renders exactly as it did before. That is checked against a frozen copy
//      of the pre-change inline code (legacyChip below), over every axis the old code read, so a
//      "harmless" tidy-up of the local path shows up as a diff rather than as nothing.
//
// The colour tests at the bottom read src/styles.css itself, because the first version passed
// every test here and still painted the Claude dot the same blue as a cold one on the real
// screen: the class was right and the colour behind it was the palette accent. They resolve the
// theme the way <html> does, for every palette in every mode, and hold every dot to 3:1 against
// the page and surface (WCAG 1.4.11) and 0.15 OKLab from every other dot.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import {
  MODEL_STATE_TEXT,
  chipDotClass,
  chipLabel,
  chipRemote,
  chipStateText,
  chipTitle,
  claudeFallbackWarning,
  claudeReadiness,
  claudeRows,
  claudeTriggerPill,
  formatElapsed,
} from '../src/model_chip_view.ts'

const STATES = ['missing', 'cold', 'warming', 'loaded']

// The text table as it stood before this change. Frozen here rather than read from the module,
// so editing MODEL_STATE_TEXT is a visible change to every old chip, not a silent one.
const LEGACY_TEXT = { missing: 'not found', cold: 'cold', warming: 'warming up', loaded: 'GPU · ready' }

// ModelChips.tsx's chip body before model_chip_view.ts existed, copied verbatim. The oracle for
// "an old broker's entry renders exactly as today".
function legacyChip(m) {
  const remote = !!m.upstream && m.upstream !== 'local'
  const title = remote
    ? `${m.role ?? m.label} → ${m.model ?? '?'} · ${LEGACY_TEXT[m.state]}`
      + ` · runs on '${m.upstream}', not this box`
      + ' · no "warming up" state off-box: the queue is on the other machine'
    : `${m.role ?? m.label} → ${m.model ?? '?'} · ${LEGACY_TEXT[m.state]}`
  return {
    label: `${m.label}${m.model ? ` (${m.model})` : ''}`,
    remote: remote ? m.upstream : null,
    text: LEGACY_TEXT[m.state],
    dot: `rail-chip-dot ${m.state}`,
    title,
  }
}

function view(m) {
  return {
    label: chipLabel(m), remote: chipRemote(m), text: chipStateText(m),
    dot: chipDotClass(m), title: chipTitle(m),
  }
}

// Every axis the old code read (state, model, role, upstream), crossed with the two new keys.
// `upstream` includes 'local' and absent, the two values where the remote branch must NOT run,
// and `backend` includes a value no broker sends today, which must take the local path too.
function* localChips() {
  for (const state of STATES)
    for (const model of [undefined, 'mistral-small3.2:24b'])
      for (const role of [undefined, '@iep'])
        for (const upstream of [undefined, 'local', 'gpu-box'])
          for (const backend of [undefined, 'ollama', 'codex'])
            for (const busy of [undefined, false, true]) {
              const m = { slot: 'llm', label: 'LLM', state }
              if (model !== undefined) m.model = model
              if (role !== undefined) m.role = role
              if (upstream !== undefined) m.upstream = upstream
              if (backend !== undefined) m.backend = backend
              if (busy !== undefined) m.busy = busy
              yield m
            }
}

const claudeChip = (extra = {}) => ({
  slot: 'llm', label: 'LLM', model: 'mistral-small3.2:24b', state: 'cold', role: '@iep',
  backend: 'claude', busy: false, ...extra,
})

test('the state table is unchanged', () => {
  assert.deepEqual({ ...MODEL_STATE_TEXT }, LEGACY_TEXT)
})

test('an entry with no backend renders exactly as before', () => {
  // The old-broker case: no `backend`, no `busy`. Byte-identical on every field.
  let n = 0
  for (const m of localChips()) {
    if ('backend' in m || 'busy' in m) continue
    assert.deepEqual(view(m), legacyChip(m), JSON.stringify(m))
    n++
  }
  assert.equal(n, 4 * 2 * 2 * 3, 'the generator stopped varying an axis')
})

test('an ollama chip is unchanged, and busy does nothing to it', () => {
  // backend 'ollama' and a backend value nobody sends yet both take the local path, with or
  // without busy. `warming` already means "a job of yours is waiting" on a local slot.
  let n = 0
  for (const m of localChips()) {
    assert.deepEqual(view(m), legacyChip(m), JSON.stringify(m))
    n++
  }
  assert.equal(n, 4 * 2 * 2 * 3 * 3 * 3)
})

test('a claude chip reads "Claude" in every state', () => {
  for (const state of STATES) {
    const m = claudeChip({ state })
    assert.equal(chipStateText(m), 'Claude', state)
    // The slot label stays; the local model name does not sit in the chip, where it would read
    // as the name of the thing answering.
    assert.equal(chipLabel(m), 'LLM')
    assert.equal(chipRemote(m), null)
  }
})

test('a busy claude chip says so in its text', () => {
  // The pulse alone failed on the real screen: a screenshot taken mid-draft showed a chip
  // identical to an idle one, and under prefers-reduced-motion there is no pulse at all.
  for (const state of STATES) {
    assert.equal(chipStateText(claudeChip({ state, busy: true })), 'Claude · working', state)
    assert.equal(chipStateText(claudeChip({ state, busy: false })), 'Claude', state)
  }
  const noBusyKey = claudeChip()
  delete noBusyKey.busy
  assert.equal(chipStateText(noBusyKey), 'Claude')
  // A local chip's text never mentions work in flight: `warming` already says it there, and
  // the legacy-oracle tests above pin that busy changes nothing on that path.
  assert.equal(chipStateText({ slot: 'llm', label: 'LLM', state: 'cold', busy: true }), 'cold')
})

test('a claude dot is the claude class, never a residency class', () => {
  for (const state of STATES) {
    const dot = chipDotClass(claudeChip({ state }))
    assert.equal(dot, 'rail-chip-dot claude', state)
    assert.ok(!dot.includes(state), `a ${state} class beside the word Claude`)
  }
})

test('busy adds the pulse class on a claude chip', () => {
  assert.equal(chipDotClass(claudeChip({ busy: true })), 'rail-chip-dot claude busy')
  assert.equal(chipDotClass(claudeChip({ busy: false })), 'rail-chip-dot claude')
  const noBusyKey = claudeChip()
  delete noBusyKey.busy
  assert.equal(chipDotClass(noBusyKey), 'rail-chip-dot claude')
  assert.match(chipTitle(claudeChip({ busy: true })), /working now/)
  assert.doesNotMatch(chipTitle(claudeChip({ busy: false })), /working now/)
})

test('the claude tooltip names the backend and the local fallback with its state', () => {
  for (const state of STATES) {
    const t = chipTitle(claudeChip({ state }))
    assert.match(t, /your own jobs on this slot run on Claude/)
    assert.match(t, /CLI backend/)
    assert.ok(t.includes('fallback: mistral-small3.2:24b'), t)
    assert.ok(t.includes(`(${LEGACY_TEXT[state]})`), `${state}: ${t}`)
    assert.ok(t.startsWith('@iep → Claude'), t)
  }
  // A delegated fallback says where it would run.
  assert.match(chipTitle(claudeChip({ upstream: 'gpu-box' })), / on 'gpu-box'$/)
  assert.doesNotMatch(chipTitle(claudeChip({ upstream: 'local' })), /on 'local'/)
  // No model resolved: the same '?' the local path shows.
  const bare = claudeChip()
  delete bare.model
  assert.ok(chipTitle(bare).includes('fallback: ? ('))
})

// --- a slot served off the broker (backend "external") -----------------------------------------
// openmaic's OPENMAIC_LLM_BASE_URL: the rail calls an endpoint of its own configuration, and its
// chip entry says so with backend and upstream "external" (docs/RAIL_CONTRACT.md, "Who answers a
// slot"). Its `state` is the rail's guess ('cold'), which the chip must not show as residency.

const externalChip = (extra = {}) => ({
  slot: 'reasoning', label: 'LLM', role: 'external', model: 'gpu-box.lan:11434', state: 'cold',
  upstream: 'external', backend: 'external', busy: false, ...extra,
})

test('an external chip says where it runs, not a residency nobody measured', () => {
  for (const state of STATES) {
    const m = externalChip({ state })
    assert.equal(chipStateText(m), 'external', state)
    assert.equal(chipLabel(m), 'LLM (gpu-box.lan:11434)')
    // The word is in the text already, and 'external' names no broker upstream.
    assert.equal(chipRemote(m), null)
    const dot = chipDotClass(m)
    assert.equal(dot, 'rail-chip-dot external', state)
    assert.ok(!dot.includes(state), `a ${state} class on a slot whose residency nobody can see`)
    const t = chipTitle(m)
    assert.ok(t.startsWith('external → gpu-box.lan:11434 · '), t)
    assert.match(t, /instead of the platform's broker/)
    assert.match(t, /not visible from here/)
    assert.doesNotMatch(t, /Claude|GPU|not this box/)
  }
  // busy changes nothing: no broker call, Claude's or anyone's, is in flight on it.
  assert.deepEqual(view(externalChip({ busy: true })), view(externalChip()))
  // No model reported: the same '?' the other paths show.
  const bare = externalChip()
  delete bare.model
  assert.equal(chipLabel(bare), 'LLM')
  assert.ok(chipTitle(bare).startsWith('external → ? · '))
})

// --- widget ------------------------------------------------------------------------------------

const NOW_S = 1_800_000_000
const NOW_MS = NOW_S * 1000

test('elapsed formatting', () => {
  assert.equal(formatElapsed(0), '0s')
  assert.equal(formatElapsed(12.9), '12s')
  assert.equal(formatElapsed(59.99), '59s')
  assert.equal(formatElapsed(60), '1m 00s')
  assert.equal(formatElapsed(185), '3m 05s')
  assert.equal(formatElapsed(3599), '59m 59s')
  assert.equal(formatElapsed(3600), '1h 00m')
  assert.equal(formatElapsed(3725), '1h 02m')
  // The browser's clock behind the broker's: zero, not a minus sign.
  assert.equal(formatElapsed(-4), '0s')
  assert.equal(formatElapsed(Number.NaN), '—')
})

test('widget rows come from cli.active, with rail or role and elapsed time', () => {
  const cli = {
    backend: 'claude', ready: true, for_you: true, roles: ['iep'], last_by_role: {},
    active: [
      { role: '@iep', rail: 'IEP', since: NOW_S - 12.4 },
      { role: '@vision', rail: null, since: NOW_S - 185 },
      { role: '@edu', rail: 'EDU-Suite', since: NOW_S - 3725, requester: 'owner-one' },
    ],
  }
  const rows = claudeRows(cli, NOW_MS)
  assert.equal(rows.length, 3)
  assert.deepEqual(rows.map((r) => [r.label, r.sub, r.elapsed, r.requester]), [
    ['IEP', '@iep', '12s', null],
    ['@vision', null, '3m 05s', null],
    ['EDU-Suite', '@edu', '1h 02m', 'owner-one'],
  ])
  assert.equal(new Set(rows.map((r) => r.key)).size, 3, 'row keys must be unique')
  assert.match(rows[0].title, /Not a GPU job/)
  // Two calls on one role in the same second still get distinct keys.
  const twin = claudeRows({ ...cli, active: [cli.active[0], cli.active[0]] }, NOW_MS)
  assert.notEqual(twin[0].key, twin[1].key)
})

test('no rows when the cli key is absent', () => {
  // Absent for anyone who is neither admin nor owner, and from an older gateway.
  assert.deepEqual(claudeRows(undefined, NOW_MS), [])
  assert.deepEqual(claudeRows(null, NOW_MS), [])
  assert.deepEqual(claudeRows({ backend: 'claude', ready: true, active: [] }, NOW_MS), [])
  // A partial block (no `active`) is not a crash.
  assert.deepEqual(claudeRows({ backend: 'claude', ready: true }, NOW_MS), [])
})

const ownerCli = (active) => ({
  backend: 'claude', ready: true, for_you: true, roles: ['iep'], last_by_role: {}, active,
})

test('the trigger pill appears only while a Claude call is in flight', () => {
  // The closed button is what a still frame shows. With no call it must add nothing, or the
  // button would claim Claude activity for a viewer who has none; with no `cli` key at all
  // (a non-owner) it must add nothing either.
  assert.equal(claudeTriggerPill(claudeRows(undefined, NOW_MS)), null)
  assert.equal(claudeTriggerPill(claudeRows(ownerCli([]), NOW_MS)), null)
  const one = claudeTriggerPill(claudeRows(ownerCli([{ role: '@iep', rail: 'IEP', since: NOW_S - 65 }]), NOW_MS))
  assert.ok(one)
  assert.equal(one.text, 'Claude · working')
  assert.match(one.title, /IEP \(@iep\), 1m 05s/)
  // It sits BESIDE the Ollama summary, so its title has to say the two do not interact.
  assert.match(one.title, /Not GPU jobs/)
})

test('the trigger pill counts calls and names each one', () => {
  // An admin's `active` holds every owner's calls, with the requester; the count goes in the
  // text so a still frame says how much is running, and the names go in the title.
  const admin = {
    ...ownerCli([
      { role: '@iep', rail: 'IEP', since: NOW_S - 12, requester: 'owner-one' },
      { role: '@vision', rail: null, since: NOW_S - 185, requester: 'owner-two' },
      { role: '@edu', rail: 'EDU-Suite', since: NOW_S - 3725, requester: 'owner-one' },
    ]),
  }
  const pill = claudeTriggerPill(claudeRows(admin, NOW_MS))
  assert.equal(pill.text, 'Claude ×3 · working')
  assert.match(pill.title, /answering 3 call\(s\)/)
  assert.ok(pill.title.includes('IEP (@iep) for owner-one, 12s'), pill.title)
  assert.ok(pill.title.includes('@vision for owner-two, 3m 05s'), pill.title)
  assert.ok(pill.title.includes('EDU-Suite (@edu) for owner-one, 1h 02m'), pill.title)
  const two = claudeTriggerPill(claudeRows(ownerCli(admin.active.slice(0, 2)), NOW_MS))
  assert.equal(two.text, 'Claude ×2 · working')
})

test('a fallback warning appears for ok:false', () => {
  const cli = {
    backend: 'claude', ready: true, for_you: true, roles: [], active: [],
    last_by_role: {
      '@iep': { ok: false, at: NOW_S - 30, reason: 'rate_limited' },
      '@edu': { ok: true, at: NOW_S - 10 },
    },
  }
  const w = claudeFallbackWarning(cli, NOW_MS)
  assert.ok(w)
  assert.equal(w.text, 'Claude fell back (rate_limited)')
  assert.match(w.title, /@iep: rate_limited, 30s ago/)
  assert.doesNotMatch(w.title, /@edu/)
  // Several roles: each reason once, newest first.
  const many = claudeFallbackWarning({
    ...cli,
    last_by_role: {
      '@a': { ok: false, at: NOW_S - 90, reason: 'timeout' },
      '@b': { ok: false, at: NOW_S - 5, reason: 'rate_limited' },
      '@c': { ok: false, at: NOW_S - 50, reason: 'timeout' },
    },
  }, NOW_MS)
  assert.equal(many.text, 'Claude fell back (rate_limited, timeout)')
  // A missing reason is named, not printed as "undefined".
  const noReason = claudeFallbackWarning({ ...cli, last_by_role: { '@x': { ok: false, at: NOW_S } } }, NOW_MS)
  assert.equal(noReason.text, 'Claude fell back (unknown)')
})

// An interruption (a Claude stream the CLI cut off after its first token) is not a fallback: no
// local answer came after it, so the badge must not say one did. The reason and the key its
// cause rides in are read from the broker, which records them (_cli_interrupted, one of Broker's
// CLI methods, written in broker_cli.py).
const BROKER_SRC = readFileSync(new URL('../../services/broker/app/broker_cli.py', import.meta.url), 'utf8')

test('an interrupted stream is named with its cause, never as a fallback', () => {
  assert.match(BROKER_SRC, /"reason": "interrupted",\s*"cause": cause,/,
    'the broker records an interruption under another reason or key now')
  const cli = {
    backend: 'claude', ready: true, for_you: true, roles: [], active: [],
    last_by_role: { '@rag': { ok: false, at: NOW_S - 20, reason: 'interrupted', cause: 'stalled' } },
  }
  const w = claudeFallbackWarning(cli, NOW_MS)
  assert.equal(w.text, 'Claude stopped mid-answer (stalled)')
  assert.match(w.title, /@rag: interrupted \(stalled\), 20s ago/)
  assert.match(w.title, /no local answer followed/)
  assert.doesNotMatch(w.text + w.title, /fell back|ran on the local model/)
  // Beside a fallback: each in its own words, and the detail newest first.
  const both = claudeFallbackWarning({
    ...cli,
    last_by_role: { ...cli.last_by_role, '@iep': { ok: false, at: NOW_S - 5, reason: 'rate_limited' } },
  }, NOW_MS)
  assert.equal(both.text, 'Claude fell back (rate_limited); stopped mid-answer (stalled)')
  assert.match(both.title, /^The latest Claude call ran on the local model instead\. A Claude answer stopped/)
  assert.match(both.title, /@iep: rate_limited, 5s ago; @rag: interrupted \(stalled\), 20s ago\./)
  // A broker that sent no cause: named, not printed as "undefined".
  const bare = claudeFallbackWarning({
    ...cli, last_by_role: { '@rag': { ok: false, at: NOW_S, reason: 'interrupted' } },
  }, NOW_MS)
  assert.equal(bare.text, 'Claude stopped mid-answer (unknown)')
})

test('no warning when the latest call succeeded', () => {
  const base = { backend: 'claude', ready: true, for_you: true, roles: [], active: [] }
  assert.equal(claudeFallbackWarning({ ...base, last_by_role: { '@iep': { ok: true, at: NOW_S } } }, NOW_MS), null)
  assert.equal(claudeFallbackWarning({ ...base, last_by_role: {} }, NOW_MS), null)
  // It clears on the next success: last_by_role holds only the newest outcome per role.
  const before = { '@iep': { ok: false, at: NOW_S - 60, reason: 'rate_limited' } }
  const after = { '@iep': { ok: true, at: NOW_S } }
  assert.ok(claudeFallbackWarning({ ...base, last_by_role: before }, NOW_MS))
  assert.equal(claudeFallbackWarning({ ...base, last_by_role: after }, NOW_MS), null)
  // An entry with no `ok` is not evidence of a fallback.
  assert.equal(claudeFallbackWarning({ ...base, last_by_role: { '@iep': { at: NOW_S } } }, NOW_MS), null)
  assert.equal(claudeFallbackWarning(undefined, NOW_MS), null)
})

test('readiness is admin-only and names what blocks it', () => {
  const owner = { backend: 'claude', ready: false, for_you: true, roles: ['iep'], active: [], last_by_role: {} }
  assert.equal(claudeReadiness(owner), null, 'an owner is told nothing about the install')
  assert.equal(claudeReadiness(undefined), null)
  const admin = {
    backend: 'claude', enabled: true, ready: true, blockers: [], exe: 'claude', owners: ['owner-one'],
    roles: ['iep', 'edu'], workdir: '', token_problem: null, calls: 7, refusals: {},
    fallbacks: { count: 0, last: null }, active: [], last_by_role: {},
  }
  const ready = claudeReadiness(admin)
  assert.deepEqual([ready.text, ready.tone], ['Claude: ready', 'good'])
  assert.match(ready.title, /iep, edu/)
  const off = claudeReadiness({ ...admin, enabled: false, ready: false, blockers: ['disabled'] })
  assert.deepEqual([off.text, off.tone], ['Claude: off', 'neutral'])
  const blocked = claudeReadiness({
    ...admin, ready: false, blockers: ['token_unreadable', 'workdir_missing'], token_problem: 'file not found',
  })
  assert.deepEqual([blocked.text, blocked.tone], ['Claude: not ready', 'warning'])
  assert.match(blocked.title, /token_unreadable, workdir_missing/)
  assert.match(blocked.title, /file not found/)
})

test('every role switched off in the Rails tab reads as off, unless a fault sits beside it', () => {
  // A decision, like the backend being switched off: a warning badge for it would teach every
  // admin to ignore the badge. But the calm badge must not hide a real fault next to it.
  const admin = {
    backend: 'claude', enabled: true, ready: false, blockers: ['cli_all_roles_off'], exe: 'claude',
    owners: ['owner-one'], roles: [], workdir: '', token_problem: null, calls: 0, refusals: {},
    fallbacks: { count: 0, last: null }, active: [], last_by_role: {},
  }
  const off = claudeReadiness(admin)
  assert.deepEqual([off.text, off.tone], ['Claude: off', 'neutral'])
  assert.match(off.title, /Admin > Rails/)
  const faulty = claudeReadiness({ ...admin, blockers: ['cli_all_roles_off', 'token_unreadable'] })
  assert.deepEqual([faulty.text, faulty.tone], ['Claude: not ready', 'warning'])
  const invalid = claudeReadiness({ ...admin, blockers: ['cli_roles_file_invalid'] })
  assert.deepEqual([invalid.text, invalid.tone], ['Claude: not ready', 'warning'])
})

test('every blocker slug the broker can emit reaches the admin', () => {
  // Read from the broker, not listed here: services/broker/app/broker_cli.py (Broker's CLI
  // methods) builds `blockers` in cli_state and _cli_env_blockers. workdir_unset is the newest.
  // The badge's title shows the slugs as they come (no slug -> text map anywhere in web/ or the
  // shell), so a map added later has to cover every one of these or this fails.
  const src = readFileSync(new URL('../../services/broker/app/broker_cli.py', import.meta.url), 'utf8')
  const start = src.indexOf('def _cli_env_blockers(')
  const end = src.indexOf('"blockers": blockers', start)
  assert.ok(start > 0 && end > start, 'the broker no longer builds blockers where this reads them')
  const slugs = [...new Set([...src.slice(start, end).matchAll(/(?:blockers|out)\.append\("([a-z_]+)"\)/g)].map((m) => m[1]))]
  assert.ok(slugs.includes('workdir_unset') && slugs.length >= 8, `slugs read: ${slugs}`)
  const admin = { backend: 'claude', enabled: true, ready: false, roles: ['iep'], token_problem: null }
  for (const slug of slugs) {
    const r = slug === 'disabled'
      ? claudeReadiness({ ...admin, enabled: false, blockers: [slug] })
      : claudeReadiness({ ...admin, blockers: [slug] })
    assert.ok(r && r.title, `${slug}: no readiness text at all`)
    if (slug === 'disabled') assert.match(r.title, /switched off/)
    else assert.ok(r.title.includes(slug), `${slug} does not reach the badge's title: ${r.title}`)
  }
})

// --- colour: what src/styles.css actually paints ------------------------------------------------

// The stylesheet as flat rules, { at, selectors, decls }. Only as much CSS as this file uses:
// comments stripped, @media bodies descended into (their condition kept in `at`), @keyframes
// skipped. The witness test below proves it reaches the palette blocks and the @media blocks,
// because a parser that silently saw only the first rule would pass every "defined once" check.
function parseCss(text) {
  const rules = []
  const walk = (s, at) => {
    let i = 0
    for (;;) {
      const open = s.indexOf('{', i)
      if (open < 0) return
      let depth = 1
      let j = open + 1
      for (; j < s.length && depth; j++) {
        if (s[j] === '{') depth++
        else if (s[j] === '}') depth--
      }
      const head = s.slice(i, open).trim()
      const body = s.slice(open + 1, j - 1)
      if (head.startsWith('@media')) walk(body, head)
      else if (!head.startsWith('@')) {
        const decls = {}
        for (const d of body.split(';')) {
          const k = d.indexOf(':')
          if (k > 0) decls[d.slice(0, k).trim()] = d.slice(k + 1).trim()
        }
        rules.push({ at, selectors: head.split(',').map((x) => x.trim().replace(/\s+/g, ' ')), decls })
      }
      i = j
    }
  }
  walk(text.replace(/\/\*[\s\S]*?\*\//g, ''), null)
  return rules
}

const RULES = parseCss(readFileSync(new URL('../src/styles.css', import.meta.url), 'utf8'))
const ROOT = RULES.find((r) => r.at === null && r.selectors.length === 1 && r.selectors[0] === ':root')

/** The value the LAST top-level rule for `sel` gives `prop` (the cascade, for equal specificity). */
function declOf(sel, prop) {
  const hits = RULES.filter((r) => r.at === null && r.selectors.includes(sel) && prop in r.decls)
  assert.ok(hits.length, `no ${prop} for ${sel}`)
  return hits[hits.length - 1].decls[prop]
}

// --- the theme as <html> resolves it, for every palette and mode ---------------------------------
// The shell puts data-palette and data-theme on <html> (App.tsx: "system" removes data-theme), so
// the tokens a chip dot reads are whatever the rules matching THAT element leave behind. Only the
// selector shapes this sheet uses on the root are modelled: `:root` or `[data-palette='x']`, more
// `[attr='v']`, and an optional `:where(:not([attr='v']))`. Specificity counts :root and each
// attribute as one class-level part (`:where` adds nothing), then source order breaks ties: the
// real cascade for these selectors, and the reason `[data-palette='x'][data-theme='dark']` beats
// the palette's own light block regardless of where each sits.
const ROOT_SEL = /^(:root)?((?:\[[\w-]+='[^']*'\])*)(?::where\(:not\(\[([\w-]+)='([^']*)'\]\)\))?$/

/** Specificity of `sel` if it matches an <html> with these attributes, else null. */
function rootMatch(sel, attrs) {
  const m = sel.match(ROOT_SEL)
  if (!m || (!m[1] && !m[2])) return null
  let spec = m[1] ? 1 : 0
  for (const [, k, v] of m[2].matchAll(/\[([\w-]+)='([^']*)'\]/g)) {
    if (attrs[k] !== v) return null
    spec++
  }
  if (m[3] && attrs[m[3]] === m[4]) return null
  return spec
}

/** Every custom property on <html> for one palette (null = none set) and mode. */
function themeTokens({ palette, theme, prefersDark }) {
  const attrs = {}
  if (palette) attrs['data-palette'] = palette
  if (theme) attrs['data-theme'] = theme
  const hits = []
  RULES.forEach((r, i) => {
    let spec = null
    for (const s of r.selectors) {
      const x = rootMatch(s, attrs)
      if (x !== null && (spec === null || x > spec)) spec = x
    }
    if (spec === null) return
    if (r.at !== null) {
      const scheme = r.at.match(/^@media\s*\(prefers-color-scheme:\s*(dark|light)\)$/)
      // A root rule under any other condition would be a token source this model ignores.
      assert.ok(scheme, `a root rule under an unmodelled ${r.at}: ${r.selectors.join(', ')}`)
      if ((scheme[1] === 'dark') !== prefersDark) return
    }
    hits.push({ spec, i, r })
  })
  hits.sort((a, b) => a.spec - b.spec || a.i - b.i)
  const tokens = {}
  for (const { r } of hits)
    for (const [k, v] of Object.entries(r.decls)) if (k.startsWith('--')) tokens[k] = v
  return tokens
}

/** `a, b` split at the top-level comma: var() arguments carry commas of their own. */
function splitTop(s) {
  let depth = 0
  for (let i = 0; i < s.length; i++) {
    if (s[i] === '(') depth++
    else if (s[i] === ')') depth--
    else if (s[i] === ',' && depth === 0) return [s.slice(0, i), s.slice(i + 1)]
  }
  return [s]
}

/** A declared value with its var() chain followed through `tokens`, as the element computes it. */
function colourOf(value, tokens, seen = []) {
  // The one color-mix form the sheet uses (the accent dots): `color-mix(in oklab, A N%, B)`,
  // which is A at N% and B at the rest, interpolated in OKLab. Anything else fails here rather
  // than being read as a literal and measured as garbage.
  const mix = value.match(/^color-mix\(in oklab,\s*([\s\S]+)\)$/)
  if (mix) {
    const parts = splitTop(mix[1])
    const first = parts.length === 2 && parts[0].trim().match(/^([\s\S]+?)\s+(\d+(?:\.\d+)?)%$/)
    assert.ok(first && !/%\s*$/.test(parts[1]), `only "color-mix(in oklab, A N%, B)" is modelled: ${value}`)
    return mixOklab(colourOf(first[1].trim(), tokens, seen), colourOf(parts[1].trim(), tokens, seen),
      Number(first[2]) / 100)
  }
  const m = value.match(/^var\((--[\w-]+)(?:,\s*(.+))?\)$/)
  // #rgb is the same colour as #rrggbb (a rule that writes `color: #fff` is measured, not refused)
  if (!m) return /^#[0-9a-f]{3}$/i.test(value) ? '#' + [...value.slice(1)].map((x) => x + x).join('') : value
  assert.ok(!seen.includes(m[1]), `var() cycle through ${m[1]}`)
  if (m[1] in tokens) return colourOf(tokens[m[1]], tokens, [...seen, m[1]])
  // No fallback is accepted silently: a token that is used and never defined is how --info
  // went unnoticed, painting the screen with a literal hidden in the rule.
  assert.ok(m[2], `${m[1]} is not defined here and has no fallback`)
  return colourOf(m[2].trim(), tokens, seen)
}

const PALETTES = [null, ...new Set(RULES.flatMap((r) => r.selectors)
  .map((s) => s.match(/^\[data-palette='([\w-]+)'\]/)?.[1]).filter(Boolean))]
// Every way <html> can be themed: the menu's Light / Dark / System, each on a light and a dark OS.
const MODES = []
for (const theme of ['light', 'dark', undefined])
  for (const prefersDark of [false, true])
    MODES.push({ theme, prefersDark, dark: theme === 'dark' || (theme === undefined && prefersDark) })
const THEMES = PALETTES.flatMap((palette) => MODES.map((m) => {
  const name = `${palette ?? '(no palette)'} ${m.theme ?? 'system'}${m.prefersDark ? ' on a dark OS' : ' on a light OS'}`
  return { ...m, palette, name, tokens: themeTokens({ palette, ...m }) }
}))

// 'external' is the neutral ink: held to the same 3:1 as every coloured dot, on every palette.
const DOTS = [...STATES, 'claude', 'external']
/** What `.rail-chip-dot.<dot>` paints on this theme. */
const dotColour = (dot, t) => colourOf(declOf(`.rail-chip-dot.${dot}`, 'background'), t.tokens)

function linearRgb(hex) {
  assert.match(hex, /^#[0-9a-f]{6}$/i, `not a #rrggbb colour: ${hex}`)
  return [1, 3, 5].map((i) => {
    const c = parseInt(hex.slice(i, i + 2), 16) / 255
    return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4
  })
}
// WCAG 2.x relative luminance and contrast ratio.
const luminance = (hex) => { const [r, g, b] = linearRgb(hex); return 0.2126 * r + 0.7152 * g + 0.0722 * b }
const contrast = (a, b) => {
  const x = luminance(a); const y = luminance(b)
  return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05)
}

// OKLab distance, the measure the distinctness tests threshold at 0.15. Measured, not picked:
// the accent the Claude dot used to borrow sat 0.09 (abyss dark, the palette in the screenshot)
// to 0.14 (default light) from the cold dot's #58a6ff and read as the same blue on screen;
// #a855f7 sits at 0.20.
function oklab(hex) {
  const [r, g, b] = linearRgb(hex)
  const l = Math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b)
  const m = Math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b)
  const s = Math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b)
  return [0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s,
    1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s,
    0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s]
}
const distance = (a, b) => { const p = oklab(a); const q = oklab(b); return Math.hypot(p[0] - q[0], p[1] - q[1], p[2] - q[2]) }

/** OKLab back to #rrggbb, clipped to sRGB (the inverse of oklab() above). */
function fromOklab([L, A, B]) {
  const l = (L + 0.3963377774 * A + 0.2158037573 * B) ** 3
  const m = (L - 0.1055613458 * A - 0.0638541728 * B) ** 3
  const s = (L - 0.0894841775 * A - 1.2914855480 * B) ** 3
  const lin = [4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
    -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
    -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s]
  return '#' + lin.map((c) => {
    const v = Math.min(1, Math.max(0, c))
    const e = v <= 0.0031308 ? 12.92 * v : 1.055 * v ** (1 / 2.4) - 0.055
    return Math.round(e * 255).toString(16).padStart(2, '0')
  }).join('')
}
/** color-mix(in oklab, a p, b): `a` weighted `p`, `b` the rest. */
function mixOklab(a, b, p) {
  const x = oklab(a)
  const y = oklab(b)
  return fromOklab(x.map((v, i) => v * p + y[i] * (1 - p)))
}

test('the css reader sees every palette block and the @media blocks', () => {
  // Witness for the colour tests below. --accent is redefined by every palette in every mode,
  // inside and outside @media, so a reader that stopped early or skipped @media shows it here.
  assert.ok(ROOT, 'no top-level :root rule')
  const accents = RULES.filter((r) => '--accent' in r.decls)
  assert.ok(accents.length > 40, `only ${accents.length} --accent definitions seen`)
  assert.ok(accents.some((r) => r.at && r.at.includes('prefers-color-scheme')))
  assert.ok(RULES.some((r) => r.selectors.some((s) => s.startsWith("[data-palette='nord']"))))
})

test('the claude dot and the widget paint with --cli, not the accent', () => {
  // The bug this pins: the class was right and the colour behind it was var(--accent), blue on
  // the default palette, so on the real screen a Claude dot looked exactly like a cold one.
  assert.equal(declOf('.rail-chip-dot.claude', 'background'), 'var(--cli)')
  assert.match(declOf('.rail-chip-dot.claude', 'box-shadow'), /var\(--cli-soft\)/)
  assert.equal(declOf('.mw-job-dot.claude', 'color'), 'var(--cli)')
  assert.equal(declOf('.badge-cli .badge-dot', 'background'), 'var(--cli)')
})

test('the external dot is the neutral ink, never a residency or Claude colour', () => {
  // A slot whose residency nobody can see gets no residency colour, and no --cli ring either.
  assert.equal(declOf('.rail-chip-dot.external', 'background'), 'var(--text-secondary)')
  const decls = RULES.filter((r) => r.at === null && r.selectors.includes('.rail-chip-dot.external'))
  assert.ok(decls.every((r) => !('box-shadow' in r.decls)), 'a ring would read as the Claude dot')
})

test('the theme resolver sees every palette and both modes, by their real surfaces', () => {
  // Witness for the contrast and distinctness tests below. Each axis has to be live: the
  // palette axis (nord is not tokyo is not the bare :root), the mode axis (light is not dark),
  // and the OS axis (System follows it, an explicit Light or Dark ignores it). A resolver that
  // silently applied only :root would hand every palette the default page and pass everything.
  assert.ok(PALETTES.length >= 22, `only ${PALETTES.length} palettes seen (incl. the bare :root)`)
  assert.equal(THEMES.length, PALETTES.length * 6)
  const at = (palette, theme, prefersDark) => THEMES.find((t) =>
    t.palette === palette && t.theme === theme && t.prefersDark === prefersDark).tokens
  assert.equal(at(null, 'light', false)['--page'], '#f9f9f7')
  assert.equal(at(null, 'dark', false)['--page'], '#0d0d0d')
  assert.equal(at('tokyo', 'light', false)['--page'], '#e1e2e7')
  assert.equal(at('nord', 'light', true)['--page'], '#eceff4', 'explicit Light must ignore a dark OS')
  assert.equal(at('nord', 'dark', false)['--surface-1'], '#343b4a', 'explicit Dark must ignore a light OS')
  assert.equal(at('nord', undefined, true)['--surface-1'], '#343b4a', 'System on a dark OS is dark')
  assert.equal(at('nord', undefined, false)['--surface-1'], '#ffffff', 'System on a light OS is light')
  for (const t of THEMES) {
    const L = luminance(colourOf(t.tokens['--page'], t.tokens))
    assert.ok(t.dark ? L < 0.2 : L > 0.5, `${t.name}: --page luminance ${L.toFixed(3)} is the wrong mode`)
  }
})

test('--cli and the chip tokens come from the :root rules alone, never from a palette', () => {
  // One meaning on every palette is the whole point: a palette that repainted --cli could put it
  // back on blue. Mode may move them (a light value on :root, a dark one in both dark :root
  // blocks); a palette may not. --cli-soft and --good, which do not change with mode either, are
  // still defined exactly once.
  const rootRules = RULES.filter((r) => r.selectors.every((s) => s.startsWith(':root')))
  for (const tok of ['--cli', '--info', '--chip-missing', '--chip-cold', '--chip-warming', '--chip-loaded',
    '--good-text', '--warning-text', '--critical-text']) {
    const defs = RULES.filter((r) => tok in r.decls)
    assert.ok(defs.length >= 1, `${tok} is never defined`)
    for (const r of defs)
      assert.ok(rootRules.includes(r), `${tok} is set by ${r.selectors.join(', ')}, not by a :root rule`)
  }
  for (const tok of ['--cli-soft', '--good']) {
    const defs = RULES.filter((r) => tok in r.decls)
    assert.equal(defs.length, 1, `${tok} defined ${defs.length} times: `
      + defs.map((r) => r.selectors.join(',')).join(' | '))
    assert.equal(defs[0], ROOT, `${tok} is defined outside the top-level :root`)
  }
})

test('every model-chip dot reaches 3:1 against --page and --surface-1, on every palette and mode', () => {
  // WCAG 1.4.11: an 8px dot is a graphic and needs 3:1 against what it sits on. The old fixed
  // status colours missed it on dozens of palette/mode pairs (amber on every light palette, the
  // cold blue likewise, red and --cli on nord dark). No single colour can pass both tokyo's light
  // page and nord's dark surface, which is why the tokens have a light and a dark value. A new
  // palette whose surface lands too close to a dot fails here by name: give the dot a value for
  // that palette rather than lowering the bar.
  const fails = []
  let checked = 0
  for (const t of THEMES) {
    for (const surface of ['--page', '--surface-1']) {
      const bg = colourOf(t.tokens[surface], t.tokens)
      for (const dot of DOTS) {
        const c = dotColour(dot, t)
        const r = contrast(c, bg)
        checked++
        if (r < 3) fails.push(`${t.name}: ${dot} ${c} on ${surface} ${bg} = ${r.toFixed(2)}:1`)
      }
    }
  }
  assert.equal(checked, THEMES.length * 2 * DOTS.length)
  assert.deepEqual(fails, [], `${fails.length} of ${checked} below 3:1:\n  ${fails.slice(0, 25).join('\n  ')}`)
})

// Every other small status graphic in the sheet, with what it actually sits on. A Badge has no
// fill, so its dot sits on whatever card or page holds it; a TagChip dot sits inside a .chip,
// which is filled with --surface-2; the widget's running-job marker (a ▶ glyph, a graphic, not
// words) sits in the .mw-pop popover, which is --surface-1.
const GRAPHICS = [
  ...['good', 'warning', 'critical', 'accent', 'cli'].map((t) =>
    ({ sel: `.badge-${t} .badge-dot`, prop: 'background', on: ['--page', '--surface-1'] })),
  { sel: '.badge-dot', prop: 'background', on: ['--page', '--surface-1'] },
  ...['good', 'warning', 'critical', 'accent', 'neutral'].map((t) =>
    ({ sel: `.tag-${t}`, prop: 'background', on: ['--surface-2'] })),
  { sel: '.mw-job-dot.active', prop: 'color', on: ['--surface-1'] },
  // The borders of the status-text controls below: the boundary is what shows the state.
  { sel: '.btn-danger:hover:not(:disabled)', prop: 'border-color', on: ['--page', '--surface-1'] },
  { sel: '.mw-x:hover:not(:disabled)', prop: 'border-color', on: ['--surface-1'] },
  { sel: '.vc-speak.rec', prop: 'border-color', on: ['--page', '--surface-1'] },
]

// Status colour as TEXT, with what it sits on: admin cards and their error lines on
// --surface-1 (or the page), the widget popover and the voice popover on --surface-1, the
// dictation chip and danger buttons wherever a rail puts them.
const STATUS_TEXT = [
  { sel: '.error-line', on: ['--page', '--surface-1'] },
  { sel: '.rail-slot-saved', on: ['--page', '--surface-1'] },
  { sel: '.rail-slot-warn', on: ['--page', '--surface-1'] },
  { sel: '.btn-danger:hover:not(:disabled)', on: ['--page', '--surface-1'] },
  { sel: '.mw-x:hover:not(:disabled)', on: ['--surface-1'] },
  { sel: '.vc-speak.rec', on: ['--page', '--surface-1'] },
  { sel: '.vc-fine.vc-err', on: ['--surface-1'] },
]

/** `a` mixed into `b` at `p` in sRGB, as `color-mix(in srgb, a p, b)` and a translucent tint
 *  composited over a surface both paint. */
const mixSrgb = (a, b, p) => over(a, b, p)

test('the status text tokens reach 4.5:1 on every surface, bare and under a 10% tint of their hue', () => {
  // The token-level promise rails rely on: status words sit on any of the four surfaces, and on a
  // tint of their own hue (THEMING holds such tints to about 10%: rails paint them with
  // color-mix(in srgb, var(--warning) 10%, transparent) and the like). The tokens used to be held
  // on --page and --surface-1 only, and on tokyo light's --surface-2 under a 10% tint they managed
  // 4.16-4.52:1, which is why iep had nudged its status words toward the ink.
  const HUES = { '--good-text': '--good', '--warning-text': '--warning', '--critical-text': '--critical' }
  const fails = []
  let checked = 0
  for (const t of THEMES) {
    for (const [tok, hueTok] of Object.entries(HUES)) {
      const c = colourOf(t.tokens[tok], t.tokens)
      const hue = colourOf(t.tokens[hueTok], t.tokens)
      for (const surface of SURFACES) {
        const s = colourOf(t.tokens[surface], t.tokens)
        for (const [how, bg] of [['', s], [`a 10% ${hueTok} tint over `, mixSrgb(hue, s, 0.1)]]) {
          const r = contrast(c, bg)
          checked++
          if (r < 4.5) fails.push(`${t.name}: ${tok} ${c} on ${how}${surface} ${bg} = ${r.toFixed(2)}:1`)
        }
      }
    }
  }
  assert.equal(checked, THEMES.length * 3 * SURFACES.length * 2)
  assert.deepEqual(fails, [], `${fails.length} of ${checked} below 4.5:1:\n  ${fails.slice(0, 30).join('\n  ')}`)
})

test('every status text rule reaches 4.5:1 against what it sits on, on every palette and mode', () => {
  // WCAG 1.4.3. These painted --good / --warning / --critical (one colour for both modes), which
  // missed 4.5:1 on 53, 44 and 53 of 88 palette/mode/surface pairs; one read an undefined
  // --danger and was painted by its fallback literal. They read the per-mode --*-text tokens now.
  const fails = []
  let checked = 0
  for (const g of STATUS_TEXT) {
    const value = declOf(g.sel, 'color')
    for (const t of THEMES) {
      const c = colourOf(value, t.tokens)
      for (const surface of g.on) {
        const bg = colourOf(t.tokens[surface], t.tokens)
        const r = contrast(c, bg)
        checked++
        if (r < 4.5) fails.push(`${t.name}: ${g.sel} ${c} on ${surface} ${bg} = ${r.toFixed(2)}:1`)
      }
    }
  }
  assert.equal(checked, THEMES.length * STATUS_TEXT.reduce((n, g) => n + g.on.length, 0))
  assert.deepEqual(fails, [], `${fails.length} of ${checked} below 4.5:1:\n  ${fails.slice(0, 30).join('\n  ')}`)
})

// The palette's own text tokens and every surface level it defines. Surfaces are read from the
// palette blocks by name (--page, --rail, --surface-N), so a palette that grows a --surface-3
// is measured without anybody remembering to add it here; the witness below pins the four that
// exist today. --muted is in because web-core paints words with it (.muted, .empty, .foot,
// .mw-empty, .vc-fine ...), not only rules and icons.
const TEXT_TOKENS = ['--text-primary', '--text-secondary', '--muted']
const SURFACES = [...new Set(RULES.filter((r) => r.selectors.some((s) => s.startsWith('[data-palette=')))
  .flatMap((r) => Object.keys(r.decls)).filter((k) => /^--(page|rail|surface-\d+)$/.test(k)))].sort()

test("every palette's text tokens reach 4.5:1 against every surface it defines, in every mode", () => {
  // WCAG 1.4.3, for the palette itself rather than for one rule. solarized's --text-secondary
  // sat at 3.90:1 on its dark --surface-2 and 4.39:1 on its light one, so every .chip label and
  // .hint on it missed; --muted missed on 34 of the 44 palette/modes, down to 2.14:1 (onedark
  // light, --surface-2). A failing pair is fixed in that palette's TEXT token: the surfaces are
  // what every dot, fill and badge measurement in this file is taken against.
  assert.deepEqual(SURFACES, ['--page', '--rail', '--surface-1', '--surface-2'])
  for (const tok of TEXT_TOKENS)
    assert.ok(RULES.some((r) => (r.decls.color ?? '') === `var(${tok})`), `witness: no rule paints text with ${tok}`)
  const fails = []
  let checked = 0
  for (const t of THEMES) {
    for (const surface of SURFACES) {
      assert.ok(surface in t.tokens, `${t.name}: ${surface} is not defined`)
      const bg = colourOf(t.tokens[surface], t.tokens)
      for (const tok of TEXT_TOKENS) {
        const c = colourOf(t.tokens[tok], t.tokens)
        const r = contrast(c, bg)
        checked++
        if (r < 4.5) fails.push(`${t.name}: ${tok} ${c} on ${surface} ${bg} = ${r.toFixed(2)}:1`)
      }
    }
  }
  assert.equal(checked, THEMES.length * SURFACES.length * TEXT_TOKENS.length)
  assert.deepEqual(fails, [], `${fails.length} of ${checked} below 4.5:1:\n  ${fails.join('\n  ')}`)
})

// The palette accent as WORDS, with what each sits on: the rail's active app on --rail, the
// widget's (i) and the voice popover's permission link on --surface-1 (their popovers), the theme
// menu's chosen-palette label on --surface-1, and the admin tabs, rail-slot kind pill and revert
// link, schedule pickers and the speak button on the page or a card. `fill` names the rule whose
// own background (an --accent-soft wash) lies between the words and that surface.
const ACCENT_TEXT = [
  { sel: '.rail-app.on', on: ['--rail'], fill: '.rail-app.on' },
  { sel: '.mw-i:hover', on: ['--surface-1'] },
  { sel: '.tm-sw.on .tm-sw-l', on: ['--surface-1'], fill: '.tm-sw.on' },
  { sel: '.admin-tab.on', on: ['--page', '--surface-1'] },
  { sel: '.rail-slot-kind', on: ['--page', '--surface-1'], fill: '.rail-slot-kind' },
  { sel: '.rail-slot-revert', on: ['--page', '--surface-1'] },
  { sel: '.sch-freq label.on', on: ['--page', '--surface-1'], fill: '.sch-freq label.on' },
  { sel: '.sch-days button.on', on: ['--page', '--surface-1'], fill: '.sch-days button.on' },
  { sel: '.vc-perm', on: ['--surface-1'] },
  { sel: '.vc-speak.on', on: ['--page', '--surface-1'] },
]

test('every accent text rule reaches 4.5:1 against what it sits on, on every palette and mode', () => {
  // WCAG 1.4.3. These painted --accent, which each palette owns and tunes for fills, dots and
  // gradients, not for words: 1080 of these 2112 pairs missed 4.5:1, down to 2.51:1 (ember's
  // active rail app under its --accent-soft wash). They read --accent-text now.
  const fails = []
  let checked = 0
  for (const g of ACCENT_TEXT) {
    const value = declOf(g.sel, 'color')
    const fill = g.fill ? declOf(g.fill, 'background') : null
    if (g.fill) assert.equal(fill, 'var(--accent-soft)', `${g.fill}: the modelled wash is --accent-soft`)
    for (const t of THEMES) {
      const c = colourOf(value, t.tokens)
      for (const surface of g.on) {
        const s = colourOf(t.tokens[surface], t.tokens)
        const bg = fill ? over(colourOf(fill, t.tokens), s) : s
        const r = contrast(c, bg)
        checked++
        if (r < 4.5) fails.push(`${t.name}: ${g.sel} ${c} on ${fill ? '--accent-soft over ' : ''}${surface} ${bg} = ${r.toFixed(2)}:1`)
      }
    }
  }
  assert.equal(checked, THEMES.length * ACCENT_TEXT.reduce((n, g) => n + g.on.length, 0))
  assert.deepEqual(fails, [], `${fails.length} of ${checked} below 4.5:1:\n  ${fails.join('\n  ')}`)
})

test('--accent-text reaches 4.5:1 on every surface, bare and under --accent-soft, on every palette and mode', () => {
  // The token-level promise rails rely on: they paint words with --accent-text (through their
  // own --ac-text aliases) on whichever surface their layout puts them, and on the standard
  // --accent-soft wash, without a table of their own here. A rail rule with any OTHER backdrop
  // is measured rule by rule in tools/tests/test_status_fill_contrast.py.
  const fails = []
  let checked = 0
  for (const t of THEMES) {
    const c = colourOf(t.tokens['--accent-text'], t.tokens)
    const soft = colourOf(t.tokens['--accent-soft'], t.tokens)
    for (const surface of SURFACES) {
      const s = colourOf(t.tokens[surface], t.tokens)
      for (const [how, bg] of [['', s], ['--accent-soft over ', over(soft, s)]]) {
        const r = contrast(c, bg)
        checked++
        if (r < 4.5) fails.push(`${t.name}: --accent-text ${c} on ${how}${surface} ${bg} = ${r.toFixed(2)}:1`)
      }
    }
  }
  assert.equal(checked, THEMES.length * SURFACES.length * 2)
  assert.deepEqual(fails, [], `${fails.length} of ${checked} below 4.5:1:\n  ${fails.join('\n  ')}`)
})

/** The colour stops of a gradient value as #rrggbb, or the value itself as its only stop. */
function stopsOf(value, tokens) {
  const v = colourOf(value, tokens)
  if (!/gradient\(/.test(v)) return [v]
  const stops = v.match(/#[0-9a-f]{6}\b/gi) ?? []
  assert.ok(stops.length >= 2 && !/rgba?\(|var\(/.test(v), `only #rrggbb gradient stops are modelled: ${v}`)
  return stops
}

test('--on-accent reaches 4.5:1 on every stop of the accent gradient, on every palette and mode', () => {
  // The words on a filled control (a primary button, the selected chip, the chosen theme mode).
  // They were white everywhere, and white missed 4.5:1 on 104 of 132 palette/mode stops (worst
  // 1.51:1, evergreen dark). Each palette now names its ink: white where it reads, or where a
  // gradient shift of at most 0.06 in OKLab makes it read, otherwise the palette's own dark ink.
  const fails = []
  let checked = 0
  for (const t of THEMES) {
    const ink = colourOf(t.tokens['--on-accent'], t.tokens)
    for (const s of stopsOf(t.tokens['--grad-accent'], t.tokens)) {
      const r = contrast(ink, s)
      checked++
      if (r < 4.5) fails.push(`${t.name}: --on-accent ${ink} on --grad-accent stop ${s} = ${r.toFixed(2)}:1`)
    }
  }
  assert.ok(checked >= THEMES.length * 2, `only ${checked} stops measured`)
  assert.deepEqual(fails, [], `${fails.length} of ${checked} below 4.5:1:\n  ${fails.join('\n  ')}`)
})

test('every web-core rule with words on an accent fill reaches 4.5:1 on every stop of it', () => {
  // Found from the sheet, not listed: any rule whose background reads --accent or --grad-accent
  // and that sets a colour. A new filled control that keeps `color: #fff` fails here by name.
  const filled = RULES.filter((r) => r.at === null && 'color' in r.decls
    && /var\(--(grad-accent|accent)\s*[,)]/.test(r.decls.background ?? r.decls['background-color'] ?? ''))
  assert.ok(filled.length >= 6, `only ${filled.length} filled controls found`)
  const fails = []
  for (const r of filled) {
    const fill = r.decls.background ?? r.decls['background-color']
    for (const t of THEMES) {
      const ink = colourOf(r.decls.color, t.tokens)
      for (const s of stopsOf(fill, t.tokens)) {
        const q = contrast(ink, s)
        if (q < 4.5) fails.push(`${t.name}: ${r.selectors.join(', ')} ${ink} on ${s} = ${q.toFixed(2)}:1`)
      }
    }
  }
  assert.deepEqual(fails, [], `${fails.length} below 4.5:1:\n  ${fails.slice(0, 30).join('\n  ')}`)
})

test('every block that sets --accent sets --accent-text and --on-accent beside it', () => {
  // --accent-text is the palette's accent moved in lightness until words in it clear 4.5:1 (or
  // var(--accent) where the accent already does), so it belongs to the same block as the accent.
  // A palette that set --accent and not --accent-text would inherit the :root one: still 4.5:1,
  // and the default palette's blue on every other palette's tabs and links.
  const blocks = RULES.filter((r) => '--accent' in r.decls)
  assert.ok(blocks.length >= PALETTES.length * 3, `only ${blocks.length} --accent blocks seen`)
  // --on-accent likewise: an inherited one is the default palette's ink on another palette's fill.
  const missing = blocks.filter((r) => !('--accent-text' in r.decls) || !('--on-accent' in r.decls))
    .map((r) => r.selectors.join(', '))
  assert.deepEqual(missing, [])
})

/** `top` (#rrggbb, or rgba(r, g, b, a)) painted over an opaque #rrggbb, blended in sRGB as browsers do. */
function over(top, under, alpha = 1) {
  const m = top.match(/^rgba\(\s*(\d+),\s*(\d+),\s*(\d+),\s*([\d.]+)\s*\)$/)
  const bytes = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16))
  const [rgb, a] = m ? [[+m[1], +m[2], +m[3]], +m[4] * alpha] : [bytes(top), alpha]
  const u = bytes(under)
  return '#' + rgb.map((c, i) => Math.round(c * a + u[i] * (1 - a)).toString(16).padStart(2, '0')).join('')
}

/** What the VRAM bar's fill sits on, per theme: --surface-1, and the empty track as painted in
 *  the top bar. The track is --accent-soft (translucent) over .ptopbar, whose background is
 *  --surface-1 at N% over the opaque --grad-accent: the gradient washed at (100 - N)% over the
 *  surface. The wash varies along the bar, so both of its stops are taken; N is read from the
 *  rule, not assumed, and a background of any other shape fails here rather than being measured
 *  as if it were this one. */
function vramBackdrops(t) {
  const bar = declOf('.ptopbar', 'background').replace(/\s+/g, ' ')
  const veil = 'color-mix\\(in srgb, var\\(--surface-1\\) (\\d+(?:\\.\\d+)?)%, transparent\\)'
  const shape = new RegExp(`^linear-gradient\\(${veil}, ${veil}\\) padding-box, var\\(--grad-accent\\) padding-box, var\\(--surface-1\\)$`)
  const m = bar.match(shape)
  assert.ok(m && m[1] === m[2], `the top bar's background is not the modelled wash: ${bar}`)
  const wash = 1 - Number(m[1]) / 100
  assert.ok(wash > 0 && wash < 1, `unexpected wash ${wash}`)
  assert.equal(declOf('.mw .mw-track', 'background'), 'var(--accent-soft)')
  const s1 = colourOf(t.tokens['--surface-1'], t.tokens)
  const soft = colourOf(t.tokens['--accent-soft'], t.tokens)
  const stops = colourOf(t.tokens['--grad-accent'], t.tokens).match(/#[0-9a-f]{6}/gi)
  assert.equal(stops.length, 2, `--grad-accent stops: ${stops}`)
  return [['--surface-1', s1], ...stops.map((s, i) => [`track at wash stop ${i}`, over(soft, over(s, s1, wash))])]
}

test("the VRAM bar's warning and critical fills reach 3:1 against the track and the surface", () => {
  // The fill's length is the reading, so its edge against the empty track is a graphic boundary
  // (WCAG 1.4.11). They painted the fixed --warning / --critical.
  const fails = []
  let checked = 0
  for (const sel of ['.mw .mw-fill.warning', '.mw .mw-fill.critical']) {
    const value = declOf(sel, 'background')
    for (const t of THEMES) {
      const c = colourOf(value, t.tokens)
      for (const [where, bg] of vramBackdrops(t)) {
        const r = contrast(c, bg)
        checked++
        if (r < 3) fails.push(`${t.name}: ${sel} ${c} on ${where} ${bg} = ${r.toFixed(2)}:1`)
      }
    }
  }
  assert.equal(checked, THEMES.length * 2 * 3)
  assert.deepEqual(fails, [], `${fails.length} of ${checked} below 3:1:\n  ${fails.slice(0, 30).join('\n  ')}`)
})

test('no web-core rule paints text with a fixed status token or an undefined one', () => {
  // The table above names the rules that exist today; this catches the next one. A `color:` on
  // --good / --warning / --critical misses 4.5:1 on about half the palettes, and --danger is
  // defined nowhere (a literal fallback would be doing the painting).
  const bad = RULES.filter((r) => /var\(--(good|warning|critical|danger)\s*[,)]/.test(r.decls.color ?? ''))
    .map((r) => `${r.selectors.join(', ')} { color: ${r.decls.color} }`)
  assert.deepEqual(bad, [])
  assert.ok(RULES.some((r) => /var\(--critical-text\)/.test(r.decls.color ?? '')), 'witness: no --critical-text text rule seen')
})

test('every badge dot, tag dot and the widget run marker reach 3:1 against what they sit on', () => {
  // The same WCAG 1.4.11 bar as the chip dots. These painted the fixed --good / --warning /
  // --critical, which missed 3:1 in one mode or the other on many palettes.
  const fails = []
  let checked = 0
  for (const g of GRAPHICS) {
    const value = declOf(g.sel, g.prop)
    for (const t of THEMES) {
      const c = colourOf(value, t.tokens)
      for (const surface of g.on) {
        const bg = colourOf(t.tokens[surface], t.tokens)
        const r = contrast(c, bg)
        checked++
        if (r < 3) fails.push(`${t.name}: ${g.sel} ${c} on ${surface} ${bg} = ${r.toFixed(2)}:1`)
      }
    }
  }
  assert.equal(checked, THEMES.length * GRAPHICS.reduce((n, g) => n + g.on.length, 0))
  const bySel = {}
  for (const f of fails) { const s = f.split(': ')[1].split(' #')[0]; bySel[s] = (bySel[s] ?? 0) + 1 }
  assert.deepEqual(fails, [], `${fails.length} of ${checked} below 3:1, by graphic ${JSON.stringify(bySel)}:\n  `
    + fails.slice(0, 30).join('\n  '))
})

test('the dark dots are the same whether dark comes from the OS or from the theme menu', () => {
  // The dark values live in two blocks (the prefers-color-scheme one and [data-theme='dark']).
  // A value added to only one means "System" on a dark OS and an explicit "Dark" paint different
  // dots, and only one of them is the one somebody checked.
  for (const palette of PALETTES) {
    const sys = THEMES.find((t) => t.palette === palette && t.theme === undefined && t.prefersDark)
    const exp = THEMES.find((t) => t.palette === palette && t.theme === 'dark' && !t.prefersDark)
    for (const dot of DOTS)
      assert.equal(dotColour(dot, exp), dotColour(dot, sys),
        `${palette ?? '(no palette)'}: ${dot} differs between Dark and System-on-a-dark-OS`)
  }
})

test('explicit Dark resolves every token exactly as System does on a dark OS, on every palette', () => {
  // The test above, for the whole theme rather than the dots. Dark is written twice (the
  // prefers-color-scheme blocks and the [data-theme='dark'] ones), and a token added to only one
  // copy forks the theme by how dark was chosen. That is how the bare :root's explicit Dark kept
  // the LIGHT --grad-accent / --grad-brand. Compared resolved, value by value, so a token missing
  // from either copy and a token with two different values both show up.
  let compared = 0
  for (const palette of PALETTES) {
    const sys = THEMES.find((t) => t.palette === palette && t.theme === undefined && t.prefersDark)
    for (const exp of THEMES.filter((t) => t.palette === palette && t.theme === 'dark')) {
      const keys = [...new Set([...Object.keys(sys.tokens), ...Object.keys(exp.tokens)])]
      const diffs = keys.filter((k) => sys.tokens[k] !== exp.tokens[k])
        .map((k) => `${k}: System-dark ${sys.tokens[k] ?? '(unset)'} vs Dark ${exp.tokens[k] ?? '(unset)'}`)
      assert.deepEqual(diffs, [], `${exp.name} differs from ${sys.name}:\n  ${diffs.join('\n  ')}`)
      compared += keys.length
    }
  }
  assert.ok(compared > PALETTES.length * 2 * 20, `only ${compared} token comparisons made`)
})

test('--cli is distinct from every residency colour', () => {
  // Resolved from the rules themselves, on every palette and mode, so a change to EITHER side
  // (repainting --cli, or a residency token drifting toward violet) shows up.
  for (const t of THEMES) {
    const claude = dotColour('claude', t)
    for (const state of STATES) {
      const c = dotColour(state, t)
      const d = distance(claude, c)
      assert.ok(d >= 0.15, `${t.name}: claude ${claude} vs ${state} ${c}: OKLab distance ${d.toFixed(3)} < 0.15`)
    }
  }
})

test('the four residency dots stay distinct from each other, on every palette and mode', () => {
  // The cost of 3:1 in light mode is that all four sit in a narrow lightness band, so hue alone
  // separates them, and red against amber is the pair that runs out first (0.169 here). Same
  // 0.15 bar as --cli: below it two states read as one colour on the real screen.
  for (const t of THEMES) {
    for (let i = 0; i < STATES.length; i++) {
      for (let j = i + 1; j < STATES.length; j++) {
        const a = dotColour(STATES[i], t)
        const b = dotColour(STATES[j], t)
        const d = distance(a, b)
        assert.ok(d >= 0.15, `${t.name}: ${STATES[i]} ${a} vs ${STATES[j]} ${b}: OKLab distance ${d.toFixed(3)} < 0.15`)
      }
    }
  }
})

test('every rail-chip-pulse stops under prefers-reduced-motion', () => {
  // Asserts the CONDITION, not that some rule exists: every selector that animates with the
  // chip pulse must be switched off in a reduced-motion block, so a new pulse added without its
  // off-switch fails here by name.
  const animated = RULES.filter((r) => r.at === null && /\brail-chip-pulse\b/.test(r.decls.animation ?? ''))
  assert.ok(animated.length >= 3, `only ${animated.length} pulsing rules found`)
  const calmed = new Set(RULES
    .filter((r) => r.at && /prefers-reduced-motion:\s*reduce/.test(r.at) && r.decls.animation === 'none')
    .flatMap((r) => r.selectors))
  for (const r of animated)
    for (const s of r.selectors) assert.ok(calmed.has(s), `${s} still pulses under reduced motion`)
})
