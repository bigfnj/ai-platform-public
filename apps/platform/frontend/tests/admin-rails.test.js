// The admin Rails tab's shared-role notes, Apply rule and Claude switch rule, and the Rail
// Manager's line on a rail this box does not run (src/rails_view.ts); the rail failure card's
// pointer to that tab (src/RailFrame.tsx); the Workspaces tab's lock on a room that holds a
// super-admin or a member beyond the actor's reach (src/workspace_rule.ts).
//
//   node apps/platform/frontend/tests/admin-rails.test.js
//
// The tab promised that a repoint "repoints only that rail" while iep-goals, EDU-Suite and IEP
// Present Levels share roles, and course-builder runs on @openmaic. The gateway now sends
// `shared_with` on every slot (rails_models.py, test_rails_shared_roles.py) and the tab renders
// that, instead of working the list out a second time. The browser half (the note renders on
// the slot) is web/tests/browser/railcheck.py shell-admin-rails. Mutation-tested by
// mutate_admin_rails.mjs.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { applyCliReply, canApply, cliModelLabel, cliModelOptions, cliModelSelect, cliProblemNote, cliSourceNote, cliSwitch, hasCliSwitch, isSwitchOnly, railBlock, sharedCliNote, sharedRoleNote, CLI_NO_FILE, CLI_NO_MODELS, CLI_NOT_WIRED, CLI_SOURCE_DEFAULTS, CLI_SOURCE_FILE, CLI_SUPERADMIN_ONLY, RAIL_DISABLED_HERE, RAIL_EITHER, RAIL_NOT_ENABLED } from '../src/rails_view.ts'
import { memberNote, roomBeyondReach, roomLocked, roomLockNote, MANAGE_NOTE, SUPERADMIN_ROOM_NOTE, WORKSPACE_NAME_MAX } from '../src/workspace_rule.ts'
import { outOfReach, reachNote } from '../src/user_rule.ts'

test('a shared slot names the rails in shared_with; an unshared slot says nothing', () => {
  assert.equal(sharedRoleNote({ shared_with: ['EDU-Suite'] }), 'Shared role: a change here also repoints EDU-Suite.')
  assert.equal(sharedRoleNote({ shared_with: ['IEP Goals', 'IEP Present Levels'] }),
    'Shared role: a change here also repoints IEP Goals, IEP Present Levels.')
  assert.equal(sharedRoleNote({ shared_with: [] }), null)
  assert.equal(sharedRoleNote({}), null, 'a slot with no list is not called shared')
})

test('the tab renders the gateway\'s list and works out none of its own', () => {
  const src = readFileSync(new URL('../src/AdminPage.tsx', import.meta.url), 'utf8')
  const view = readFileSync(new URL('../src/rails_view.ts', import.meta.url), 'utf8')
  // And the slot type's own comment (web-core's types.ts), which said it too until 2026-10-14.
  const types = readFileSync(new URL('../../../../web/src/types.ts', import.meta.url), 'utf8')
  assert.doesNotMatch(src, /repoints only that rail/)
  assert.doesNotMatch(types, /repoints only that rail/, 'types.ts promises a one-rail repoint again')
  assert.match(src, /const shared = sharedRoleNote\(s\)/)
  assert.match(src, /\{shared && <p className="rail-slot-desc rail-slot-shared">\{shared\}<\/p>\}/)
  assert.doesNotMatch(src + view, /railsByRole|byRole/, 'a second computation of the same list is back')
})

// --- the Claude switch (rails_view.ts cliSwitch / sharedCliNote) -------------------------------
// The broker routes by ROLE, the gateway allows the switch to a super-admin only, and switching
// an unwired role ON is refused (409): every call on it stays local today, and it would start
// sending data off the box the day the rail is wired. The tab must say each of those before the
// click rather than after it.

const WIRED = { cli_on: false, cli_wired: true, cli_ready: true }
const CLI = { switch_blockers: [], roles_file_set: true }

test('a shared role says which other rails its Claude switch moves; an unshared one says nothing', () => {
  // The switch and the model select beside it, both keyed by role.
  assert.equal(sharedCliNote({ shared_with: ['IEP Goals', 'IEP Present Levels'] }),
    'Shared role: the Claude switch and model here also apply to IEP Goals, IEP Present Levels.')
  assert.equal(sharedCliNote({ shared_with: ['EDU-Suite'] }),
    'Shared role: the Claude switch and model here also apply to EDU-Suite.')
  assert.equal(sharedCliNote({ shared_with: [] }), null)
  assert.equal(sharedCliNote({}), null)
})

test('the switch is a super-admin\'s, and only on a wired, ready slot with a switch file', () => {
  assert.deepEqual(cliSwitch(WIRED, CLI, true), { disabled: false, reason: null })
  assert.deepEqual(cliSwitch(WIRED, CLI, false), { disabled: true, reason: CLI_SUPERADMIN_ONLY })
  assert.deepEqual(cliSwitch({ ...WIRED, cli_wired: false }, CLI, true),
    { disabled: true, reason: CLI_NOT_WIRED }, 'an unwired slot cannot be switched ON')
  assert.deepEqual(cliSwitch({ ...WIRED, cli_wired: false, cli_on: true }, CLI, true),
    { disabled: false, reason: CLI_NOT_WIRED }, 'but one already on can be switched OFF')
  assert.deepEqual(cliSwitch({ ...WIRED, cli_ready: false }, { ...CLI, switch_blockers: ['disabled', 'token_unreadable'] }, true),
    { disabled: true, reason: 'Claude is not ready on the broker: disabled, token_unreadable' })
  assert.deepEqual(cliSwitch(WIRED, { ...CLI, roles_file_set: false }, true), { disabled: true, reason: CLI_NO_FILE })
  assert.deepEqual(cliSwitch(WIRED, null, true), { disabled: true, reason: CLI_NO_FILE })
  // The order: a plain admin hears "super-admin only", not a broker detail they cannot act on.
  assert.equal(cliSwitch({ cli_on: false, cli_wired: false, cli_ready: false }, null, false).reason,
    CLI_SUPERADMIN_ONLY)
  assert.equal(CLI_NOT_WIRED, 'this rail does not pass who is asking, so every call stays local')
})

test('a switch that is on can be switched OFF while the broker reports a blocker; ON stays disabled', () => {
  // OFF is the kill switch: the gateway's route and the broker's write both take it whatever
  // the blockers say. The tab greyed out the box of a role that was ON, so it could not be
  // switched off until the broker was fixed.
  const blocked = { ...CLI, switch_blockers: ['disabled', 'token_unreadable'] }
  const why = 'Claude is not ready on the broker: disabled, token_unreadable'
  const on = { ...WIRED, cli_on: true, cli_ready: false }
  assert.deepEqual(cliSwitch(on, blocked, true), { disabled: false, reason: why },
    'an ON switch is clickable, and still says why nothing goes to Claude')
  assert.deepEqual(cliSwitch({ ...on, cli_on: false }, blocked, true), { disabled: true, reason: why },
    'an OFF switch cannot be turned ON on a broker that is not ready')
  // The rules before it still come first, and a broker with no switch file refuses an OFF too.
  assert.deepEqual(cliSwitch(on, blocked, false), { disabled: true, reason: CLI_SUPERADMIN_ONLY })
  assert.deepEqual(cliSwitch(on, { ...blocked, roles_file_set: false }, true), { disabled: true, reason: CLI_NO_FILE })
  assert.deepEqual(cliSwitch({ ...on, cli_wired: false }, blocked, true), { disabled: false, reason: why },
    'an unwired role that is on can be switched off on a broker that is not ready')
  // The model select stays disabled while the broker is not ready, on or off, in the same words.
  assert.deepEqual(cliModelSelect(on, blocked, true, ['claude-haiku-5-5']), { disabled: true, reason: why })
  assert.deepEqual(cliModelSelect({ ...on, cli_on: false }, blocked, true, ['claude-haiku-5-5']), { disabled: true, reason: why })
})

// --- Apply (rails_view.ts canApply) -------------------------------------------------------------
// Choosing another box clears the model (the old one is almost certainly not installed there).
// Apply was enabled by "the box changed" alone, and its click then returned on the empty model:
// a button that lit up and did nothing.

test('Apply lights only for a chosen model that differs from the live one, or on another box', () => {
  const slot = { pattern: 'qwen3:8b', upstream: 'local' }
  assert.equal(canApply(slot, 'qwen3:8b', 'local'), false, 'nothing changed')
  assert.equal(canApply(slot, 'mistral:7b', 'local'), true, 'another model')
  assert.equal(canApply(slot, '', 'offsite'), false, 'another box, its model not chosen yet')
  assert.equal(canApply(slot, 'qwen3:8b', 'offsite'), true, 'another box with a model chosen there')
  assert.equal(canApply(slot, '', 'local'), false, 'a box changed and changed back leaves no model')
  assert.equal(canApply({ pattern: 'qwen3:8b' }, 'qwen3:8b', 'local'), false, 'no upstream reads local')
  assert.equal(canApply({ pattern: 'x', upstream: 'offsite' }, 'x', 'local'), true, 'back to this box')
})

test('the button and its click both go by canApply', () => {
  const src = readFileSync(new URL('../src/AdminPage.tsx', import.meta.url), 'utf8')
  assert.match(src, /const applyable = canApply\(s, current, curUp\)/)
  assert.match(src, /<Button size="sm" disabled=\{!applyable \|\| busy\} onClick=\{\(\) => onApply\(s\)\}>/)
  assert.match(src, /if \(!canApply\(slot, model, upstream\)\) return\r?\n\s+void setRoleModel\(slot\.role, model, upstream\)/)
  assert.doesNotMatch(src, /disabled=\{!changed \|\| busy\}/, 'the box-change-alone rule is back')
})

test('a slot has the Claude switch where the gateway says it does, and nowhere else', () => {
  // The gateway's `cli_switchable`, from its CLI_SLOT_KINDS: a text or a vision slot has one,
  // an image slot none (the route's 400). The tab reads that word and keeps no copy of the
  // kinds, so a gateway that changes the kinds changes the tab with them.
  assert.equal(hasCliSwitch({ kind: 'chat', cli_switchable: true }), true)
  assert.equal(hasCliSwitch({ kind: 'vision', cli_switchable: true }), true)
  assert.equal(hasCliSwitch({ kind: 'image', cli_switchable: false }), false)
  assert.equal(hasCliSwitch({ kind: 'vision', cli_switchable: false }), false,
    'the field decides, not the kind: a gateway that takes vision out takes the row out')
  assert.equal(hasCliSwitch({ kind: 'image', cli_switchable: true }), true, 'nor does the tab second-guess it')
  assert.equal(hasCliSwitch({ kind: 'chat' }), false, 'a gateway that does not say draws no switch')
  assert.equal(hasCliSwitch({ kind: 'chat', cli_switchable: 'yes' }), false, 'only a JSON true')
  assert.equal(hasCliSwitch({}), false)
  // The same rule, and the same reasons, for a vision slot as for a text one.
  const vision = { kind: 'vision', ...WIRED }
  assert.deepEqual(cliSwitch(vision, CLI, true), cliSwitch(WIRED, CLI, true))
  assert.deepEqual(cliSwitch({ ...vision, cli_wired: false }, CLI, true), { disabled: true, reason: CLI_NOT_WIRED })
})

test('the tab draws the switch on text and vision slots only and sends the click as it is', () => {
  const src = readFileSync(new URL('../src/AdminPage.tsx', import.meta.url), 'utf8')
  // The row is guarded by hasCliSwitch: an image slot has no switch, and nor does the tab
  // decide the kinds a second time.
  assert.match(src, /\{hasCliSwitch\(s\) && \(\s*<div className="rail-slot-cli">/)
  assert.doesNotMatch(src, /s\.kind === 'chat' && \(\s*<div className="rail-slot-cli">/)
  assert.match(src, /A text or vision slot's <b>Claude<\/b> switch/)
  assert.match(src, /A vision slot's switch turns on only when\s+the broker says it can send images to Claude\./,
    'the header no longer says a vision switch waits for a broker that sends images')
  // The checkbox shows the gateway's answer and is disabled by the rule, and while its ROLE is busy.
  assert.match(src, /checked=\{s\.cli_on === true\}\s+disabled=\{sw\.disabled \|\| cliBusy === s\.role\}/)
  assert.match(src, /const sw = cliSwitch\(s, cli, cliEditable\)/)
  assert.match(src, /onChange=\{\(e\) => void setRoleCli\(s\.role, \{ enabled: e\.target\.checked \}\)\}/)
  assert.match(src, /const view = await platformApi\.adminSetRailCli\(role, change\)/)
  assert.match(src, /setCliEditable\(view\.cli_editable === true\)/)
  assert.match(src, /\{sharedCli && <p className="rail-slot-desc rail-cli-shared">\{sharedCli\}<\/p>\}/)
})

// --- the Claude model select (rails_view.ts cliModelSelect / cliModelOptions) -----------------
// Beside each text and vision slot's switch: Default, then the broker's allowlist with plain labels,
// disabled for the switch's own reasons (in the same words) and three of its own (a broker that
// is not ready, an unwired slot that is on, nothing to choose). Effective on change: one PUT with
// { model }, null for Default.

const MODELS = ['claude-haiku-5-5', 'claude-sonnet-5-5', 'claude-opus-5-5']

test('a Claude model id reads as a plain name, and any other id as it is', () => {
  assert.equal(cliModelLabel('claude-haiku-5-5'), 'Haiku 5.5')
  assert.equal(cliModelLabel('claude-sonnet-5-5'), 'Sonnet 5.5')
  assert.equal(cliModelLabel('claude-opus-5-5'), 'Opus 5.5')
  assert.equal(cliModelLabel('claude-opus-4-10'), 'Opus 4.10')
  assert.equal(cliModelLabel('some-other-model'), 'some-other-model')
})

test('the choices are Default, saying what it means, then the allowlist in its order', () => {
  assert.deepEqual(cliModelOptions(MODELS, { default_model: '' }), [
    { value: '', text: "Default (the CLI's own)" },
    { value: 'claude-haiku-5-5', text: 'Haiku 5.5' },
    { value: 'claude-sonnet-5-5', text: 'Sonnet 5.5' },
    { value: 'claude-opus-5-5', text: 'Opus 5.5' },
  ])
  assert.equal(cliModelOptions(MODELS, { default_model: 'claude-sonnet-5-5' })[0].text, 'Default (Sonnet 5.5)')
  assert.deepEqual(cliModelOptions([], null), [{ value: '', text: "Default (the CLI's own)" }])
  assert.deepEqual(cliModelOptions(undefined, undefined).map((o) => o.value), [''])
})

test('the select is disabled for the switch\'s reasons, in its words, and for three of its own', () => {
  assert.deepEqual(cliModelSelect(WIRED, CLI, true, MODELS), { disabled: false, reason: null })
  assert.deepEqual(cliModelSelect({ ...WIRED, cli_on: true }, CLI, true, MODELS), { disabled: false, reason: null },
    'a role that is on can have its model chosen')
  assert.deepEqual(cliModelSelect(WIRED, CLI, false, MODELS), { disabled: true, reason: CLI_SUPERADMIN_ONLY })
  assert.deepEqual(cliModelSelect({ ...WIRED, cli_wired: false }, CLI, true, MODELS),
    { disabled: true, reason: CLI_NOT_WIRED })
  assert.deepEqual(cliModelSelect({ ...WIRED, cli_wired: false, cli_on: true }, CLI, true, MODELS),
    { disabled: true, reason: CLI_NOT_WIRED }, 'an unwired slot that is on can be switched off, not given a model')
  assert.deepEqual(cliModelSelect({ ...WIRED, cli_ready: false }, { ...CLI, switch_blockers: ['disabled'] }, true, MODELS),
    { disabled: true, reason: 'Claude is not ready on the broker: disabled' })
  assert.deepEqual(cliModelSelect(WIRED, { ...CLI, roles_file_set: false }, true, MODELS), { disabled: true, reason: CLI_NO_FILE })
  assert.deepEqual(cliModelSelect(WIRED, CLI, true, []), { disabled: true, reason: CLI_NO_MODELS })
  assert.deepEqual(cliModelSelect(WIRED, CLI, true, undefined), { disabled: true, reason: CLI_NO_MODELS })
  // Every reason the switch gives is the select's too.
  for (const [slot, cli, editable] of [[WIRED, CLI, false], [{ ...WIRED, cli_ready: false }, { ...CLI, switch_blockers: ['x'] }, true],
    [WIRED, { ...CLI, roles_file_set: false }, true], [{ ...WIRED, cli_wired: false }, CLI, true]]) {
    assert.equal(cliModelSelect(slot, cli, editable, MODELS).reason, cliSwitch(slot, cli, editable).reason)
  }
})

// A vision role's switch needs a broker that says it sends images to Claude. The gateway marks
// every slot on such a role with its reason (`cli_unavailable`, the sentence its 409 gives)
// while the broker does not, and the tab shows it before the click.
const NO_IMAGES = 'the broker does not say it can send images to Claude, so every image call on this role stays local'

test('a vision slot whose broker sends no images cannot be switched ON, says why, and can be switched OFF', () => {
  const slot = { kind: 'vision', ...WIRED, cli_unavailable: NO_IMAGES }
  assert.deepEqual(cliSwitch(slot, CLI, true), { disabled: true, reason: NO_IMAGES })
  assert.deepEqual(cliSwitch({ ...slot, cli_on: true }, CLI, true), { disabled: false, reason: NO_IMAGES },
    'one already on can be switched OFF, and still says why its photos stay local')
  // The order: super-admin first, then the wiring, then this, then the broker's blockers.
  assert.deepEqual(cliSwitch(slot, CLI, false), { disabled: true, reason: CLI_SUPERADMIN_ONLY })
  assert.deepEqual(cliSwitch({ ...slot, cli_wired: false }, CLI, true), { disabled: true, reason: CLI_NOT_WIRED })
  assert.equal(cliSwitch({ ...slot, cli_ready: false }, { ...CLI, switch_blockers: ['disabled'] }, true).reason, NO_IMAGES)
  // No reason (a text slot, or a broker that sends images) leaves the switch as it was.
  assert.deepEqual(cliSwitch({ ...slot, cli_unavailable: null }, CLI, true), { disabled: false, reason: null })
  assert.deepEqual(cliSwitch({ ...WIRED, cli_on: true }, CLI, true), { disabled: false, reason: null })
  // The select follows the switch: off, disabled in the same words; on, a model may still be
  // chosen, which the gateway takes (a model sends nothing off the box by itself).
  assert.deepEqual(cliModelSelect(slot, CLI, true, MODELS), { disabled: true, reason: NO_IMAGES })
  assert.deepEqual(cliModelSelect({ ...slot, cli_on: true }, CLI, true, MODELS), { disabled: false, reason: null })
})

test('an unwired vision slot that is on can be switched off but not given a model', () => {
  // The fixture's unwired vision slots are all off, which cliSwitch disables first, so the
  // select's own unwired branch is reached for a vision slot only here.
  const slot = { kind: 'vision', cli_on: true, cli_wired: false, cli_ready: true, cli_unavailable: null }
  assert.deepEqual(cliSwitch(slot, CLI, true), { disabled: false, reason: CLI_NOT_WIRED })
  assert.deepEqual(cliModelSelect(slot, CLI, true, MODELS), { disabled: true, reason: CLI_NOT_WIRED })
})

// A streaming role's switch needs a broker that says it streams to Claude: the vision rule's
// shape, for a slot whose rail streams its answers (rails_models.py `stream`). The sentence is
// read from the gateway itself, so the tab is tested with the words the admin reads beside the
// switch, and a gateway that rewords it changes this test with it.
function gatewaySentence(name) {
  const gw = readFileSync(new URL('../../backend/platform_gateway_app/main.py', import.meta.url), 'utf8')
  const m = new RegExp(`^${name} = \\(((?:\\s*"[^"\\n]*")+)\\)`, 'm').exec(gw)
  assert.ok(m, `${name} is not a parenthesised string in main.py`)
  return [...m[1].matchAll(/"([^"\n]*)"/g)].map((p) => p[1]).join('')
}
const NO_STREAM = gatewaySentence('CLI_NO_STREAM')

test('a streaming slot whose broker does not stream cannot be switched ON, says why, and can be switched OFF', () => {
  assert.equal(NO_STREAM, 'the broker does not say it streams to Claude, so every streamed answer on this role stays local')
  const slot = { kind: 'chat', ...WIRED, cli_unavailable: NO_STREAM }
  assert.deepEqual(cliSwitch(slot, CLI, true), { disabled: true, reason: NO_STREAM })
  assert.deepEqual(cliSwitch({ ...slot, cli_on: true }, CLI, true), { disabled: false, reason: NO_STREAM },
    'one already on can be switched OFF, and still says why its streams stay local')
  assert.deepEqual(cliSwitch(slot, CLI, false), { disabled: true, reason: CLI_SUPERADMIN_ONLY })
  assert.deepEqual(cliModelSelect(slot, CLI, true, MODELS), { disabled: true, reason: NO_STREAM })
  // A switch's own reply keeps the reason: a switch does not change whether the broker streams.
  const tab = [{ id: 'syn', label: 'Syn', icon: 'x', slots: [{ ...slot, role: 'syn-stream', cli_on: true,
                                                                 cli_model: null, cli_switchable: true }] }]
  const after = applyCliReply(tab, { enabled_roles: [], switch_blockers: [] })[0].slots[0]
  assert.deepEqual(cliSwitch(after, CLI, true), { disabled: true, reason: NO_STREAM })
})

test('the header says a streaming slot\'s switch waits for a broker that streams to Claude', () => {
  const src = readFileSync(new URL('../src/AdminPage.tsx', import.meta.url), 'utf8')
  assert.match(src, /A streaming slot's switch \(its rail\s+streams the answer as it is written\) turns on only when the broker says it streams to\s+Claude\./)
})

test('the tab draws the select beside the switch and sends the choice as a model', () => {
  const src = readFileSync(new URL('../src/AdminPage.tsx', import.meta.url), 'utf8')
  assert.match(src, /const msel = cliModelSelect\(s, cli, cliEditable, cliModels\)/)
  // Inside the switch row, so a vision slot has one too and an image slot has none either.
  assert.match(src, /<div className="rail-slot-cli">[\s\S]*?<label className="rail-cli-model">[\s\S]*?<\/div>/)
  assert.match(src, /<select value=\{s\.cli_model \?\? ''\}\s+disabled=\{msel\.disabled \|\| cliBusy === s\.role\}/)
  assert.match(src, /onChange=\{\(e\) => void setRoleCli\(s\.role, \{ model: e\.target\.value \|\| null \}\)\}/)
  assert.match(src, /\{cliModelOptions\(cliModels, cli\)\.map\(\(o\) => \(/)
  assert.match(src, /setCliModels\(Array\.isArray\(view\.cli_models\) \? view\.cli_models : \[\]\)/)
  assert.match(src, /\{msel\.reason && msel\.reason !== sw\.reason && cliBusy !== s\.role && \(\s*<span className="muted rail-cli-model-why">\{msel\.reason\}<\/span>/)
})

// --- where the switches come from, and what the broker said about the file -------------------
// A switch file that is set but absent is the documented reset: every role goes back to
// BROKER_CLI_ROLES. It used to look exactly like a file that agreed with the env, so a role
// switched off came back on with nothing on the tab saying why.

test('the tab says whether the switch file or the service defaults decide', () => {
  assert.equal(cliSourceNote({ roles_file_set: true, roles_source: 'env' }), CLI_SOURCE_DEFAULTS)
  assert.equal(cliSourceNote({ roles_file_set: true, roles_source: 'file' }), CLI_SOURCE_FILE)
  // No file configured: every slot already says so (CLI_NO_FILE), so the header adds nothing.
  assert.equal(cliSourceNote({ roles_file_set: false, roles_source: 'env' }), null)
  assert.equal(cliSourceNote(null), null)
  assert.match(CLI_SOURCE_DEFAULTS, /service defaults \(BROKER_CLI_ROLES\)/)
  assert.match(CLI_SOURCE_DEFAULTS, /deleted file reads the same way/)
})

test('an invalid switch file names the way back; an ignored-key note is shown as it is', () => {
  const invalid = cliProblemNote({ roles_problem: 'BROKER_CLI_ROLES_FILE is invalid: x.',
                                   blockers: ['cli_roles_file_invalid'] })
  assert.match(invalid, /^BROKER_CLI_ROLES_FILE is invalid: x\. No role is on until it is fixed\./)
  assert.match(invalid, /resets every role to the service defaults \(BROKER_CLI_ROLES\)/)
  const note = "BROKER_CLI_ROLES_FILE names 'gone', which this broker has no role for."
  assert.equal(cliProblemNote({ roles_problem: note, blockers: [] }), note)
  assert.equal(cliProblemNote({ roles_problem: '', blockers: ['cli_roles_file_invalid'] }), null)
  assert.equal(cliProblemNote(null), null)
})

test('the tab renders both lines in the header card', () => {
  const src = readFileSync(new URL('../src/AdminPage.tsx', import.meta.url), 'utf8')
  assert.match(src, /const cliSource = cliSourceNote\(cli\)/)
  assert.match(src, /const cliProblem = cliProblemNote\(cli\)/)
  assert.match(src, /\{cliSource && <p className="muted rail-cli-source"[^>]*>\{cliSource\}<\/p>\}/)
  assert.match(src, /\{cliProblem && <p className="rail-slot-warn rail-cli-problem"[^>]*>\{cliProblem\}<\/p>\}/)
})

// --- a switch that landed when the tab could not be re-read -----------------------------------
// The broker took the switch, then the gateway's re-read failed. The gateway answers with the
// switch's own `cli` block and no `rails`; the slots on screen are redrawn from it, so the
// checkbox shows the switch that is live instead of the one before the click.

const TAB = [{ id: 'iep', label: 'IEP', icon: 'i', slots: [
  { role: 'iep', cli_on: false, cli_ready: true, cli_model: null, label: 'Writer' },
  { role: 'edu', cli_on: true, cli_ready: true, cli_model: 'claude-opus-5-5', label: 'Translate' },
] }]

test('a switch-only reply is told apart from the full tab', () => {
  assert.equal(isSwitchOnly({ cli: {}, cli_editable: true, reload_error: 'x' }), true)
  assert.equal(isSwitchOnly({ rails: [], cli: {}, cli_editable: true }), false)
})

test('the switch\'s own reply redraws cli_on and cli_ready the way the gateway does', () => {
  const out = applyCliReply(TAB, { enabled_roles: ['iep'], switch_blockers: [] })
  assert.deepEqual(out[0].slots.map((s) => [s.role, s.cli_on, s.cli_ready]),
    [['iep', true, true], ['edu', false, true]])
  assert.equal(out[0].slots[0].label, 'Writer', 'the rest of a slot is kept')
  assert.equal(TAB[0].slots[0].cli_on, false, 'the tab it was given is not edited in place')
  const blocked = applyCliReply(TAB, { enabled_roles: ['iep'], switch_blockers: ['disabled'] })
  assert.deepEqual(blocked[0].slots.map((s) => s.cli_ready), [false, false])
})

test('the switch\'s own reply redraws each slot\'s Claude model too', () => {
  const out = applyCliReply(TAB, { enabled_roles: ['edu'], switch_blockers: [],
                                   role_models: { iep: 'claude-haiku-5-5', other: 'claude-sonnet-5-5' } })
  assert.deepEqual(out[0].slots.map((s) => [s.role, s.cli_model]),
    [['iep', 'claude-haiku-5-5'], ['edu', null]], 'a role the reply gives no model reads Default')
  // A reply with no role_models (a broker older than the choice) reads Default everywhere, and
  // only a string is a model.
  assert.deepEqual(applyCliReply(TAB, { enabled_roles: [], switch_blockers: [] })[0].slots.map((s) => s.cli_model), [null, null])
  assert.deepEqual(applyCliReply(TAB, { enabled_roles: [], switch_blockers: [], role_models: { iep: 5, constructor: 'x' } })[0]
    .slots.map((s) => s.cli_model), [null, null])
})

const VISION_TAB = [{ id: 'photo-lab', label: 'Photo Lab', icon: 'x', slots: [
  { role: 'photo-vision', kind: 'vision', label: 'Reader', cli_on: false, cli_ready: true, cli_model: null,
    cli_switchable: true, cli_unavailable: null },
  { role: 'photo-icon', kind: 'image', label: 'Icons', cli_on: false, cli_ready: true, cli_model: null,
    cli_switchable: false, cli_unavailable: null },
] }]

test('the switch\'s own reply redraws a vision slot like a text slot and keeps what a switch cannot change', () => {
  const out = applyCliReply(VISION_TAB, { enabled_roles: ['photo-vision'], switch_blockers: [],
                                         role_models: { 'photo-vision': 'claude-haiku-5-5' } })
  const [reader, icons] = out[0].slots
  assert.deepEqual([reader.cli_on, reader.cli_ready, reader.cli_model], [true, true, 'claude-haiku-5-5'])
  assert.deepEqual([reader.kind, reader.label, reader.cli_switchable], ['vision', 'Reader', true])
  assert.equal(icons.cli_switchable, false)
  const off = applyCliReply(out, { enabled_roles: [], switch_blockers: ['disabled'] })[0].slots[0]
  assert.deepEqual([off.cli_on, off.cli_ready, off.cli_model], [false, false, null])
  // The image-path reason is the gateway's word from the last full read, and a switch does not
  // change what the broker can send: it is kept, so a vision role switched off on such a
  // broker cannot be switched straight back on.
  const marked = [{ ...VISION_TAB[0], slots: [{ ...VISION_TAB[0].slots[0], cli_on: true, cli_wired: true,
                                               cli_unavailable: NO_IMAGES }] }]
  const after = applyCliReply(marked, { enabled_roles: [], switch_blockers: [] })[0].slots[0]
  assert.deepEqual([after.cli_on, after.cli_unavailable, after.cli_switchable], [false, NO_IMAGES, true])
  assert.deepEqual(cliSwitch(after, CLI, true), { disabled: true, reason: NO_IMAGES })
})

test('the tab applies a switch-only reply to the slots it has and says why', () => {
  const src = readFileSync(new URL('../src/AdminPage.tsx', import.meta.url), 'utf8')
  assert.match(src, /if \(isSwitchOnly\(view\)\) \{\s+setCli\(view\.cli \?\? null\)\s+setRails\(\(current\) => applyCliReply\(current, view\.cli\)\)\s+setErr\(view\.reload_error\)\s+\} else \{\s+apply\(view\)\s+\}/)
})

// --- Rail Manager: a rail this box does not run (rails_view.ts railBlock) ----------------------
// Two settings keep a rail off a box. The card told the admin to add every such rail to
// PLATFORM_ENABLED_APPS, including one PLATFORM_DISABLED_APPS had removed, which is usually on
// that list already. The gateway sends which now (`blocked_by`, main.py _rail_blocked_by, held by
// apps/platform/backend/tests/test_rail_toggle_message.py) and the card renders it.

test('a rail this box does not run names the setting that keeps it off', () => {
  assert.deepEqual(railBlock({ state: 'unavailable', blocked_by: 'disabled_apps' }),
    { badge: 'off for this box', how: RAIL_DISABLED_HERE })
  assert.deepEqual(railBlock({ state: 'unavailable', blocked_by: 'not_enabled' }),
    { badge: 'not installed', how: RAIL_NOT_ENABLED })
  assert.deepEqual(railBlock({ state: 'unavailable' }), { badge: 'not installed', how: RAIL_EITHER },
    'a gateway that does not say gets both settings, never a guess')
  assert.equal(railBlock({ state: 'enabled', blocked_by: null }), null)
  assert.equal(railBlock({ state: 'disabled', blocked_by: null }), null, 'Turn on fixes an admin\'s switch')
  assert.equal(RAIL_DISABLED_HERE, 'remove from PLATFORM_DISABLED_APPS and restart')
  assert.equal(RAIL_NOT_ENABLED, 'add to PLATFORM_ENABLED_APPS and restart')
  assert.match(RAIL_EITHER, /PLATFORM_ENABLED_APPS.*PLATFORM_DISABLED_APPS/)
})

test('the card renders the gateway\'s cause, and the gateway sends the two the card reads', () => {
  const src = readFileSync(new URL('../src/AdminPage.tsx', import.meta.url), 'utf8')
  const gw = readFileSync(new URL('../../backend/platform_gateway_app/main.py', import.meta.url), 'utf8')
  assert.match(src, /const block = railBlock\(r\)/)
  assert.match(src, /\{block && <Badge tone="neutral">\{block\.badge\}<\/Badge>\}/)
  assert.match(src, /\{block \? \(\s*<span className="muted admin-rail-how"[^>]*>\{block\.how\}<\/span>/)
  assert.doesNotMatch(src, /add to PLATFORM_ENABLED_APPS and restart/, 'the one hard-coded instruction is back')
  // The gateway's rule, in the two words the card reads.
  assert.match(gw, /"blocked_by": _rail_blocked_by\(rid\)/)
  assert.match(gw, /return "disabled_apps" if rid in set\(settings\.disabled_apps\) else "not_enabled"/)
})

// --- the rail failure card (RailFrame.tsx) ------------------------------------------------------
// It sent an admin to "Admin → Rails" for the "no bundle" badge, which the Rail Manager tab draws.

test('the rail failure card points at the tab that draws the no bundle badge', () => {
  const frame = readFileSync(new URL('../src/RailFrame.tsx', import.meta.url), 'utf8')
  const admin = readFileSync(new URL('../src/AdminPage.tsx', import.meta.url), 'utf8')
  // The function that draws the badge, the tab that renders that function, and the tab's label.
  const fns = [...admin.matchAll(/^function (\w+)\(/gm)].map((m) => ({ name: m[1], at: m.index }))
  const badge = admin.indexOf('>no bundle</Badge>')
  assert.ok(badge > 0, 'the no bundle badge is gone')
  const host = fns.filter((f) => f.at < badge).at(-1).name
  const tab = new RegExp(`tab === '(\\w+)' \\? <${host} `).exec(admin)?.[1]
  assert.ok(tab, `no tab renders ${host}`)
  const label = new RegExp(`onClick=\\{\\(\\) => go\\('${tab}'\\)\\}>([^<]+)</button>`).exec(admin)?.[1]
  assert.ok(label, `no tab button for '${tab}'`)
  const pointer = /Admin → (.+?) for a “no bundle” badge/.exec(frame)?.[1]
  assert.equal(pointer, label, `the card says Admin → ${pointer}; the badge is on ${label}`)
})

// --- Workspaces: the gateway's super-admin room rule (main.py _refuse_superadmin_room) ----------

test('a plain admin is locked out of a room that holds a super-admin, and only then', () => {
  const supers = new Set(['root'])
  assert.equal(roomLocked(['bob', 'root'], supers, false), true, 'a super-admin is in the room')
  assert.equal(roomLocked(['bob', 'owner-one'], supers, false), false, 'no super-admin in the room')
  assert.equal(roomLocked([], supers, false), false, 'an empty room')
  assert.equal(roomLocked(['bob', 'root'], supers, true), false, 'a super-admin actor is never locked')
})

test('every control on a locked room is disabled and says why; names stop at 64', () => {
  const src = readFileSync(new URL('../src/AdminPage.tsx', import.meta.url), 'utf8')
  const gw = readFileSync(new URL('../../backend/platform_gateway_app/main.py', import.meta.url), 'utf8')
  // Since 2026-10-15 the lock and its note come from roomLockNote (both of the gateway's room
  // refusals); the super-admin one is tested through it below.
  assert.match(src, /const lockNote = roomLockNote\(w\.members, superadmins, iAmSuper, appsOf, grantable, catalogIds\)\r?\n\s+const locked = lockNote !== null\r?\n/)
  assert.match(src, /value=\{pending\} disabled=\{b \|\| locked\}/, 'the rename box')
  assert.match(src, /disabled=\{!renamed \|\| b \|\| locked\} onClick=\{\(\) => rename\(w\)\}/, 'rename')
  assert.match(src, /const lock = b \|\| locked \|\| !canManage\(u\)/, 'the member checkboxes')
  assert.match(src, /disabled=\{b \|\| locked\}\s+title=\{lockNote \?\? 'Removes the sharing only/, 'delete')
  assert.match(src, /\{lockNote && <p className="muted ws-locked"[^>]*>\{lockNote\}<\/p>\}/, 'the note')
  assert.equal((src.match(/maxLength=\{WORKSPACE_NAME_MAX\}/g) || []).length, 2, 'both name boxes')
  assert.equal(WORKSPACE_NAME_MAX, 64)
  assert.match(gw, /_WORKSPACE_NAME_MAX = 64\b/, 'the gateway\'s limit moved')
  assert.match(gw, /only a super-admin can \{action\} a workspace that contains a super-admin/,
    'the gateway\'s refusal changed; SUPERADMIN_ROOM_NOTE restates it')
  assert.match(SUPERADMIN_ROOM_NOTE, /^Only a super-admin can change or delete a workspace that contains a super-admin\.$/)
})

// --- Users: the gateway's reach rule (main.py _out_of_reach) ------------------------------------
// A plain admin gets 403 on a reset, a demotion or a deletion of a user holding an app the admin
// could not grant, because a reset hands over that user's login and every app with it.

const CATALOG = ['recipe-book', 'terminal-fun', 'workstation']

test('a row is out of reach when it holds a catalog app the actor cannot grant, and only then', () => {
  assert.deepEqual(outOfReach(['workstation', 'terminal-fun'], ['recipe-book'], CATALOG), ['terminal-fun', 'workstation'])
  assert.deepEqual(outOfReach(['recipe-book'], ['recipe-book'], CATALOG), [], 'within reach')
  assert.deepEqual(outOfReach([], ['recipe-book'], CATALOG), [], 'a user with no apps')
  assert.deepEqual(outOfReach(['workstation', 'terminal-fun'], CATALOG, CATALOG), [],
    'a super-admin is sent the whole catalog as grantable, so nothing is beyond them')
  assert.deepEqual(outOfReach(['recipe-book', 'a-rail-since-removed'], ['recipe-book'], CATALOG), [],
    'a row for a rail no longer in the catalog grants nothing, so it does not count')
  assert.equal(reachNote([]), null)
  assert.equal(reachNote(['terminal-fun', 'workstation']),
    'Holds apps you cannot grant (terminal-fun, workstation): only a super-admin can reset, demote or delete this user.')
})

test('reset, delete and an admin row\'s role are greyed out with the reason; grants stay live', () => {
  const src = readFileSync(new URL('../src/AdminPage.tsx', import.meta.url), 'utf8')
  const gw = readFileSync(new URL('../../backend/platform_gateway_app/main.py', import.meta.url), 'utf8')
  assert.match(src, /const far = \(u: AdminUser\) => reachNote\(outOfReach\(u\.apps, grantable, catalogIds\)\)/)
  assert.match(src, /disabled=\{locked\(u\) \|\| !!far\(u\)\}\s+title=\{far\(u\) \?\? undefined\} onClick=\{\(\) => resetPw\(u\)\}/,
    'reset pw')
  assert.match(src, /disabled=\{locked\(u\) \|\| !!far\(u\)\}\s+title=\{far\(u\) \?\? undefined\} onClick=\{\(\) => remove\(u\)\}/,
    'delete')
  assert.match(src, /disabled=\{locked\(u\) \|\| u\.username === meUsername \|\| \(u\.is_admin && !!far\(u\)\)\}/,
    'the role select of an admin row (a demotion); a plain user\'s may still be promoted')
  assert.match(src, /const lock = !canGrant\(a\.id\) \|\| locked\(u\)\r?\n/, 'an app checkbox is not this rule')
  // The two lists the shell judges by are the users route's own.
  assert.match(gw, /"grantable": sorted\(_grantable\(db, admin\)\)/)
  // The actor's grantable may be handed in since 2026-10-15 (a check over many users works it out
  // once); the rule is the same subtraction.
  assert.match(gw, /if grantable is None:\r?\n\s+grantable = _grantable\(db, actor\)\r?\n\s+return sorted\(entitled_app_ids\(db, target, APP_IDS\) - grantable\)/,
    'the gateway\'s rule changed; outOfReach restates it')
})

// --- Workspaces: the reach rule (main.py _manageable, _refuse_room_out_of_reach; 2026-10-15) ---
// A room decides whose records its members read, so a plain admin may place in one, and change
// or delete one holding, only users they could reset. The gateway answers anything else with a
// 403; the tab greys the same rooms and names out, from the users route's own lists.

const APPS = new Map([
  ['teacher-t', ['terminal-fun', 'workstation']], ['aide-c', ['terminal-fun']],
  ['user-d', ['recipe-book']], ['user-n', []], ['user-stale', ['recipe-book', 'a-rail-since-removed']],
])
const MINE = ['recipe-book']

test('a room is locked when it holds a member beyond reach, and the note names each one', () => {
  assert.deepEqual(roomBeyondReach(['user-d', 'teacher-t', 'aide-c'], APPS, MINE, CATALOG), [
    { user: 'aide-c', apps: ['terminal-fun'] }, { user: 'teacher-t', apps: ['terminal-fun', 'workstation'] }])
  assert.deepEqual(roomBeyondReach(['user-d', 'user-n', 'user-stale'], APPS, MINE, CATALOG), [],
    'within reach, a row for a rail since removed included')
  assert.deepEqual(roomBeyondReach(['teacher-t'], APPS, CATALOG, CATALOG), [],
    'a super-admin is sent the whole catalog as grantable, so nothing is beyond them')
  const none = new Set()
  assert.equal(roomLockNote(['teacher-t', 'user-d'], none, false, APPS, MINE, CATALOG),
    'Only a super-admin can change or delete this workspace: teacher-t holds apps you cannot grant (terminal-fun, workstation).')
  assert.equal(roomLockNote(['teacher-t', 'aide-c', 'user-d'], none, false, APPS, MINE, CATALOG),
    'Only a super-admin can change or delete this workspace: aide-c holds apps you cannot grant (terminal-fun); '
    + 'teacher-t holds apps you cannot grant (terminal-fun, workstation).', 'every member, by name')
  assert.equal(roomLockNote(['user-d', 'user-n'], none, false, APPS, MINE, CATALOG), null, 'a room within reach')
  assert.equal(roomLockNote(['root', 'teacher-t'], new Set(['root']), false, APPS, MINE, CATALOG),
    SUPERADMIN_ROOM_NOTE, 'the super-admin refusal first, as the gateway checks it')
  assert.equal(roomLockNote(['root', 'teacher-t'], new Set(['root']), true, APPS, CATALOG, CATALOG), null,
    'a super-admin actor is never locked')
})

test('a name the actor may not add says which apps put it beyond them', () => {
  assert.equal(memberNote(['workstation', 'terminal-fun'], MINE, CATALOG),
    'Holds apps you cannot grant (terminal-fun, workstation): only a super-admin can place this user in a workspace.')
  assert.equal(memberNote([], MINE, CATALOG), MANAGE_NOTE,
    'a name greyed out for another reason (a super-admin holds no app rows) gets the general rule')
  assert.equal(memberNote(['recipe-book', 'a-rail-since-removed'], MINE, CATALOG), MANAGE_NOTE)
})

test('the Workspaces tab judges reach from the users route\'s lists and locks the room on it', () => {
  const src = readFileSync(new URL('../src/AdminPage.tsx', import.meta.url), 'utf8')
  const gw = readFileSync(new URL('../../backend/platform_gateway_app/main.py', import.meta.url), 'utf8')
  const tab = src.slice(src.indexOf('function WorkspacesTab('))
  assert.match(tab, /setAppsOf\(new Map\(u\.users\.map\(\(x\) => \[x\.username, x\.apps\]\)\)\)/)
  assert.match(tab, /setGrantable\(u\.grantable \?\? \[\]\)/)
  assert.match(tab, /setCatalogIds\(u\.catalog\.map\(\(a\) => a\.id\)\)/)
  assert.equal((tab.match(/title=\{whyNot\(u\)\}/g) || []).length, 2, 'both member pickers say why a name is greyed out')
  assert.match(tab, /const whyNot = \(u: string\) => canManage\(u\) \? undefined : memberNote\(appsOf\.get\(u\) \?\? \[\], grantable, catalogIds\)/)
  // The gateway's two halves, which the tab restates.
  // The grantable handed in since 2026-10-15: worked out once, not once a user.
  assert.match(gw, /if not u\.is_superadmin and not _out_of_reach\(db, actor, u, grantable\)\}/,
    'the gateway\'s _manageable changed; the tab reads it as the manageable list')
  assert.match(gw, /_refuse_room_out_of_reach\(db, w, admin, "change"\)/)
  assert.match(gw, /_refuse_room_out_of_reach\(db, w, admin, "delete"\)/)
  assert.match(gw, /f"you cannot \{action\} the workspace '\{w\.name\}': \{who\}, so only a super-admin can"/,
    'the gateway\'s refusal changed; roomLockNote restates it')
})
