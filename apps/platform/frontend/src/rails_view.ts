// The Rails tab's shared-role notes, when a slot's Apply does anything, which slots have a Claude
// switch, its rule and its Claude model select; and the Rail Manager's line on a rail this box
// does not run. Pure, so node can test them (apps/platform/frontend/tests/admin-rails.test.js).
//
// The tab used to promise that "each rail has its own model role, so changing one repoints only
// that rail". Several rails share a role (iep-goals drafts on @iep and translates on @edu, so
// repointing either moves IEP Present Levels or EDU-Suite too; course-builder runs on
// @openmaic's model), and a repoint moves every rail on it. The slot now says so.
//
// One source: the gateway puts `shared_with` on every slot (rails_models.py, held by
// apps/platform/backend/tests/test_rails_shared_roles.py). This file used to work the same list
// out again from the payload, which made two answers to one question.
import type { RailManagerEntry, RailModels, RailModelSlot, RailsCli, RailsCliSwitched, RailsSettings } from '@web-core/types'

/** The note a slot carries when its role also feeds other rails, or null when it is its own. */
export function sharedRoleNote(slot: Pick<RailModelSlot, 'shared_with'>): string | null {
  const others = Array.isArray(slot.shared_with) ? slot.shared_with : []
  if (!others.length) return null
  return `Shared role: a change here also repoints ${others.join(', ')}.`
}

/** The same truth for the Claude switch and its model. The broker routes by ROLE, so switching
 *  Claude on a shared role, or choosing its Claude model, does it for every rail on that role,
 *  and the slot says which. */
export function sharedCliNote(slot: Pick<RailModelSlot, 'shared_with'>): string | null {
  const names = Array.isArray(slot.shared_with) ? slot.shared_with.join(', ') : ''
  return names ? `Shared role: the Claude switch and model here also apply to ${names}.` : null
}

/** Whether a slot has the Claude switch and the model select beside it. The gateway says so on
 *  every slot (`cli_switchable`, from rails_models.py CLI_SLOT_KINDS: a text or a vision slot;
 *  an image slot renders pictures through the media worker, which no Claude model stands in
 *  for, and its switch is the gateway's 400). One source, like `shared_with`: the tab keeps no
 *  copy of the kinds to drift from the route's. Only a JSON true draws the row, so a gateway
 *  that does not say draws none. */
export function hasCliSwitch(slot: Pick<RailModelSlot, 'cli_switchable'>): boolean {
  return slot.cli_switchable === true
}

export const CLI_SUPERADMIN_ONLY = 'super-admin only'
export const CLI_NOT_WIRED = 'this rail does not pass who is asking, so every call stays local'
export const CLI_NO_FILE = 'the broker has no BROKER_CLI_ROLES_FILE, so its roles come from '
  + 'BROKER_CLI_ROLES; set it on the broker to switch them here'

export interface CliSwitch {
  /** The checkbox cannot be clicked. */
  disabled: boolean
  /** Why, or a caveat on a switch that can still be clicked; null when there is nothing to say. */
  reason: string | null
}

/** The reason a broker that is not ready gives beside a switch, naming its blockers. */
function notReady(cli: Pick<RailsCli, 'switch_blockers'> | null | undefined): string {
  const why = cli && Array.isArray(cli.switch_blockers) && cli.switch_blockers.length
    ? cli.switch_blockers.join(', ') : 'unknown'
  return `Claude is not ready on the broker: ${why}`
}

/** Whether a slot's Claude switch can be clicked, and the one reason shown beside it.
 *
 *  Checked in this order, because each later answer assumes the earlier ones: only a super-admin
 *  may switch; an unwired slot cannot be switched ON (every call on it stays local, and it would
 *  start sending data off the box the day the rail is wired) but one that is already on can be
 *  switched OFF; a slot the gateway marks unavailable (`cli_unavailable`: a vision role on a
 *  broker that does not say it sends images to Claude, or a streaming role on one that does not
 *  say it streams to Claude) cannot be switched ON either, in the
 *  gateway's own words, and one already on can be switched OFF; a backend that is not ready
 *  cannot take a switch ON (its blockers are shown), and one already on can be switched OFF,
 *  still naming them; and a broker with no switch file refuses every write.
 *
 *  OFF is the kill switch. The gateway's route and the broker's write both take an OFF whatever
 *  the broker's blockers say (the broker's set_cli_role), and the tab used to grey out every
 *  switch, ON and OFF alike, while the broker reported one: a role that was on could not be
 *  switched off from here until the broker was fixed. */
export function cliSwitch(slot: Pick<RailModelSlot, 'cli_on' | 'cli_wired' | 'cli_ready'>
                            & Partial<Pick<RailModelSlot, 'cli_unavailable'>>,
                          cli: Pick<RailsCli, 'switch_blockers' | 'roles_file_set'> | null | undefined,
                          editable: boolean): CliSwitch {
  if (editable !== true) return { disabled: true, reason: CLI_SUPERADMIN_ONLY }
  if (slot.cli_wired !== true && slot.cli_on !== true) return { disabled: true, reason: CLI_NOT_WIRED }
  const unavailable = slot.cli_unavailable || null
  if (unavailable && slot.cli_on !== true) return { disabled: true, reason: unavailable }
  if (slot.cli_ready !== true && slot.cli_on !== true) return { disabled: true, reason: notReady(cli) }
  if (!cli || cli.roles_file_set !== true) return { disabled: true, reason: CLI_NO_FILE }
  if (slot.cli_ready !== true) return { disabled: false, reason: notReady(cli) }
  if (slot.cli_wired !== true) return { disabled: false, reason: CLI_NOT_WIRED }
  if (unavailable) return { disabled: false, reason: unavailable }
  return { disabled: false, reason: null }
}

/** Whether a slot's Apply would change anything, and so whether the button is enabled: a model
 *  is chosen, and it or the box differs from what is live. Changing only the BOX is a change, but
 *  choosing a box clears the model (the old one is almost certainly not installed there), and an
 *  empty model cannot be applied: the button used to light up after a box change and then do
 *  nothing when clicked. Apply sends the box and the model together, so it waits for both. */
export function canApply(slot: Pick<RailModelSlot, 'pattern'> & Partial<Pick<RailModelSlot, 'upstream'>>,
                         model: string, upstream: string): boolean {
  if (!model) return false
  return model !== slot.pattern || upstream !== (slot.upstream || 'local')
}

// --- the Claude model select, beside the switch ------------------------------------------------

export const CLI_NO_MODELS = 'the broker offers no Claude model to choose (its BROKER_CLI_MODELS '
  + 'is empty, or it is older than this tab)'

/** A Claude model id as the tab names it: "claude-haiku-5-5" reads "Haiku 5.5". An id of any
 *  other shape is shown as it is. */
export function cliModelLabel(id: string): string {
  const m = /^claude-([a-z]+)-(\d+)-(\d+)$/.exec(id)
  return m ? `${m[1].charAt(0).toUpperCase()}${m[1].slice(1)} ${m[2]}.${m[3]}` : id
}

export interface CliModelOption {
  /** '' for Default, which the tab sends as null (clear the role's own model). */
  value: string
  text: string
}

/** The select's choices: Default first, saying what it means (the broker's BROKER_CLI_MODEL, or
 *  the CLI's own default when it has none), then the broker's allowlist in the broker's order. */
export function cliModelOptions(models: string[] | null | undefined,
                                cli: Pick<RailsCli, 'default_model'> | null | undefined): CliModelOption[] {
  const fallback = cli && typeof cli.default_model === 'string' ? cli.default_model.trim() : ''
  const list = Array.isArray(models) ? models.filter((m) => typeof m === 'string' && m) : []
  return [{ value: '', text: fallback ? `Default (${cliModelLabel(fallback)})` : "Default (the CLI's own)" },
    ...list.map((m) => ({ value: m, text: cliModelLabel(m) }))]
}

/** Whether a slot's Claude model select can be used, and the one reason shown beside it. The
 *  switch's own rule first (cliSwitch: super-admin only, not ready, no switch file), so a reason
 *  that disables the switch disables the select in the same words. Then three of its own: a
 *  broker that is not ready keeps the select disabled on a role that is on too (its switch stays
 *  clickable, to switch it OFF), an unwired slot's select stays disabled even when its switch is
 *  on (a model there changes nothing, and the gateway refuses one), and so does a broker with
 *  nothing to choose. */
export function cliModelSelect(slot: Pick<RailModelSlot, 'cli_on' | 'cli_wired' | 'cli_ready'>
                                 & Partial<Pick<RailModelSlot, 'cli_unavailable'>>,
                               cli: Pick<RailsCli, 'switch_blockers' | 'roles_file_set'> | null | undefined,
                               editable: boolean,
                               models: string[] | null | undefined): CliSwitch {
  const sw = cliSwitch(slot, cli, editable)
  if (sw.disabled) return sw
  if (slot.cli_ready !== true) return { disabled: true, reason: notReady(cli) }
  if (slot.cli_wired !== true) return { disabled: true, reason: CLI_NOT_WIRED }
  if (!Array.isArray(models) || models.length === 0) return { disabled: true, reason: CLI_NO_MODELS }
  return { disabled: false, reason: null }
}

// Where the broker's eligible roles come from, once it has a switch file configured. The file
// being set but ABSENT is the documented reset (deleting it hands every role back to
// BROKER_CLI_ROLES), and it used to look exactly like a file that had always agreed with it:
// a role switched off came back on with nothing on the tab saying so.
export const CLI_SOURCE_DEFAULTS = 'Claude switches: the broker has no switch file right now, so '
  + 'its service defaults (BROKER_CLI_ROLES) decide which roles are on. The first switch writes '
  + 'the file. A deleted file reads the same way, and the switches it held are gone.'
export const CLI_SOURCE_FILE = 'Claude switches: the broker\'s switch file decides which roles are on.'

/** The line the tab shows about where the switches come from, or null when the broker has no
 *  switch file configured (every slot already says so, CLI_NO_FILE). */
export function cliSourceNote(cli: Pick<RailsCli, 'roles_file_set' | 'roles_source'> | null | undefined): string | null {
  if (!cli || cli.roles_file_set !== true) return null
  return cli.roles_source === 'file' ? CLI_SOURCE_FILE : CLI_SOURCE_DEFAULTS
}

/** What the broker said about its switch file, or null. With the `cli_roles_file_invalid`
 *  blocker it is a refusal (no role is on), and the way back is named; without it, a note about
 *  keys the file holds that no role answers to, which blocks nothing. */
export function cliProblemNote(cli: Pick<RailsCli, 'roles_problem' | 'blockers'> | null | undefined): string | null {
  const problem = cli && typeof cli.roles_problem === 'string' ? cli.roles_problem.trim() : ''
  if (!problem) return null
  const invalid = Array.isArray(cli?.blockers) && cli.blockers.includes('cli_roles_file_invalid')
  return invalid
    ? `${problem} No role is on until it is fixed. Deleting the file resets every role to the `
      + 'service defaults (BROKER_CLI_ROLES), and the switches it held are lost.'
    : problem
}

/** Whether a switch reply is the full tab, or only the switch's own state (the broker took the
 *  switch and the gateway could not re-read the tab: RailsCliSwitched). */
export function isSwitchOnly(view: RailsSettings | RailsCliSwitched): view is RailsCliSwitched {
  return !Array.isArray((view as Partial<RailsSettings>).rails)
}

/** The slots the tab already has, redrawn from a switch's own reply: `cli_on`, `cli_ready` and
 *  `cli_model` worked out exactly as the gateway's _rails_payload does, so the checkbox and the
 *  model select show what is live even when the tab could not be re-read. Everything else on a
 *  slot is kept, `cli_switchable` and `cli_unavailable` too: a switch changes neither which
 *  kinds have one nor whether the broker sends images or streams. Every kind of slot is redrawn
 *  alike. */
export function applyCliReply(rails: RailModels[],
                              cli: Pick<RailsCli, 'enabled_roles' | 'switch_blockers'> & Partial<Pick<RailsCli, 'role_models'>>): RailModels[] {
  const on = new Set(Array.isArray(cli.enabled_roles) ? cli.enabled_roles : [])
  const ready = !(Array.isArray(cli.switch_blockers) && cli.switch_blockers.length)
  const chosen: Record<string, unknown> = cli.role_models && typeof cli.role_models === 'object' ? cli.role_models : {}
  const modelOf = (role: string): string | null =>
    Object.prototype.hasOwnProperty.call(chosen, role) && typeof chosen[role] === 'string' ? chosen[role] as string : null
  return rails.map((r) => ({
    ...r,
    slots: r.slots.map((s) => ({ ...s, cli_on: on.has(s.role), cli_ready: ready, cli_model: modelOf(s.role) })),
  }))
}

// --- the Rail Manager: a rail this box does not run -------------------------------------------
// Two settings keep a rail off a box, and each has its own fix. The card used to tell the admin
// to add every such rail to PLATFORM_ENABLED_APPS, including one PLATFORM_DISABLED_APPS had
// removed, which is usually on that list already (its default is every rail): the admin edited
// a list it was on, restarted, and it stayed off. The gateway now says which (`blocked_by`, the
// rule its toggle refusal names the setting by), and the card renders that.

export const RAIL_NOT_ENABLED = 'add to PLATFORM_ENABLED_APPS and restart'
export const RAIL_DISABLED_HERE = 'remove from PLATFORM_DISABLED_APPS and restart'
/** A gateway that does not say which (none ships without it): both settings, never a guess. */
export const RAIL_EITHER = 'add to PLATFORM_ENABLED_APPS, or remove from PLATFORM_DISABLED_APPS, '
  + 'and restart'

export interface RailBlock {
  /** The card's badge. */
  badge: string
  /** What to change, in place of the Turn on button. */
  how: string
}

/** The badge and the instruction on a rail this box does not run, or null for one it does. */
export function railBlock(r: Pick<RailManagerEntry, 'state'> & Partial<Pick<RailManagerEntry, 'blocked_by'>>): RailBlock | null {
  if (r.state !== 'unavailable') return null
  if (r.blocked_by === 'disabled_apps') return { badge: 'off for this box', how: RAIL_DISABLED_HERE }
  if (r.blocked_by === 'not_enabled') return { badge: 'not installed', how: RAIL_NOT_ENABLED }
  return { badge: 'not installed', how: RAIL_EITHER }
}
