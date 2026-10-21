// Mutation harness for admin-rails.test.js. NOT a test file: it rewrites the sources.
//
//     node apps/platform/frontend/tests/mutate_admin_rails.mjs
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import { runMutations } from '../../../../web/tests/mutation_suite.mjs'

const HERE = dirname(fileURLToPath(import.meta.url))
const REPO = join(HERE, '..', '..', '..', '..')
const VIEW = join(HERE, '..', 'src', 'rails_view.ts')
const ADMIN = join(HERE, '..', 'src', 'AdminPage.tsx')
const RULE = join(HERE, '..', 'src', 'workspace_rule.ts')
const USERS = join(HERE, '..', 'src', 'user_rule.ts')
const GATEWAY = join(HERE, '..', '..', 'backend', 'platform_gateway_app', 'main.py')
const FRAME = join(HERE, '..', 'src', 'RailFrame.tsx')
const TYPES = join(REPO, 'web', 'src', 'types.ts')

runMutations({
  repo: REPO,
  suite: join(HERE, 'admin-rails.test.js'),
  mutations: [
    {
      label: 'an empty shared_with still reads as shared',
      file: VIEW,
      old: '  if (!others.length) return null\n',
      new: '',
      expect: 'a shared slot names the rails in shared_with; an unshared slot says nothing',
    },
    {
      label: 'the note drops the second rail',
      file: VIEW,
      old: '${others.join(\', \')}',
      new: '${others[0]}',
      expect: 'a shared slot names the rails in shared_with; an unshared slot says nothing',
    },
    {
      label: 'the old promise comes back',
      file: ADMIN,
      old: 'A change repoints the ROLE, and so every rail that',
      new: 'Each rail has its own model role, so changing one repoints only that rail. A change repoints the ROLE, and so every rail that',
      expect: 'the tab renders the gateway\'s list and works out none of its own',
    },
    {
      // Added 2026-10-14: web-core's slot type said it as well.
      label: 'the slot type\'s comment promises a one-rail repoint again',
      file: TYPES,
      old: '// a concrete installed model; changing it repoints the ROLE, so every rail with a slot on that\n',
      new: '// a concrete installed model; changing it repoints only that rail (per-rail roles), so every rail with a slot on that\n',
      expect: 'the tab renders the gateway\'s list and works out none of its own',
    },
    {
      label: 'the slot stops rendering the note',
      file: ADMIN,
      old: '                  {shared && <p className="rail-slot-desc rail-slot-shared">{shared}</p>}\n',
      new: '',
      expect: 'the tab renders the gateway\'s list and works out none of its own',
    },
    {
      label: 'the tab works the list out from the roles again',
      file: ADMIN,
      old: '              const shared = sharedRoleNote(s)\n',
      new: '              const byRole = rails.filter((x) => x.slots.some((y) => y.role === s.role)).map((x) => x.label)\n'
        + '              const shared = sharedRoleNote({ shared_with: byRole.filter((l) => l !== rail.label) })\n',
      expect: 'the tab renders the gateway\'s list and works out none of its own',
    },
    // --- the Claude switch ---------------------------------------------------------------------
    {
      label: 'the Claude shared-role note drops the second rail',
      file: VIEW,
      old: "slot.shared_with.join(', ') : ''",
      new: "String(slot.shared_with[0] ?? '') : ''",
      expect: 'a shared role says which other rails its Claude switch moves; an unshared one says nothing',
    },
    {
      // Re-anchored 2026-10-09 when the note started covering the model select too.
      label: 'an unshared role still gets a Claude shared-role note',
      file: VIEW,
      old: 'return names ? `Shared role: the Claude switch and model here also apply to ${names}.` : null',
      new: 'return `Shared role: the Claude switch and model here also apply to ${names}.`',
      expect: 'a shared role says which other rails its Claude switch moves; an unshared one says nothing',
    },
    {
      label: 'the Claude shared-role note stops covering the model',
      file: VIEW,
      old: '`Shared role: the Claude switch and model here also apply to ${names}.`',
      new: '`Shared role: switching Claude here also switches ${names}.`',
      expect: 'a shared role says which other rails its Claude switch moves; an unshared one says nothing',
    },
    {
      label: 'a plain admin is offered the switch',
      file: VIEW,
      old: '  if (editable !== true) return { disabled: true, reason: CLI_SUPERADMIN_ONLY }\n',
      new: '',
      expect: 'the switch is a super-admin\'s, and only on a wired, ready slot with a switch file',
    },
    {
      label: 'an unwired slot can be switched ON',
      file: VIEW,
      old: '  if (slot.cli_wired !== true && slot.cli_on !== true) return { disabled: true, reason: CLI_NOT_WIRED }\n',
      new: '',
      expect: 'the switch is a super-admin\'s, and only on a wired, ready slot with a switch file',
    },
    {
      label: 'an unwired slot that is on can no longer be switched off',
      file: VIEW,
      old: '  if (slot.cli_wired !== true && slot.cli_on !== true) return',
      new: '  if (slot.cli_wired !== true) return',
      expect: 'the switch is a super-admin\'s, and only on a wired, ready slot with a switch file',
    },
    {
      // Re-anchored 2026-10-14 when a switch that is on stayed clickable on such a backend.
      label: 'a backend that is not ready still takes a switch',
      file: VIEW,
      old: '  if (slot.cli_ready !== true && slot.cli_on !== true) return { disabled: true, reason: notReady(cli) }\n',
      new: '',
      expect: 'the switch is a super-admin\'s, and only on a wired, ready slot with a switch file',
    },
    // --- a switch that is on, on a broker that is not ready (added 2026-10-14) ----------------
    {
      // The rule as it was: every switch greyed out while the broker reports a blocker.
      label: 'a switch that is on is greyed out again while the broker is not ready',
      file: VIEW,
      old: '  if (slot.cli_ready !== true && slot.cli_on !== true) return { disabled: true, reason: notReady(cli) }\n',
      new: '  if (slot.cli_ready !== true) return { disabled: true, reason: notReady(cli) }\n',
      expect: 'a switch that is on can be switched OFF while the broker reports a blocker; ON stays disabled',
    },
    {
      label: 'a switch that is on, on a broker that is not ready, no longer says why',
      file: VIEW,
      old: '  if (slot.cli_ready !== true) return { disabled: false, reason: notReady(cli) }\n',
      new: '',
      expect: 'a switch that is on can be switched OFF while the broker reports a blocker; ON stays disabled',
    },
    {
      label: 'an OFF on a broker with no switch file is offered, though the broker refuses it',
      file: VIEW,
      old: '  if (!cli || cli.roles_file_set !== true) return { disabled: true, reason: CLI_NO_FILE }\n'
        + '  if (slot.cli_ready !== true) return { disabled: false, reason: notReady(cli) }\n',
      new: '  if (slot.cli_ready !== true) return { disabled: false, reason: notReady(cli) }\n'
        + '  if (!cli || cli.roles_file_set !== true) return { disabled: true, reason: CLI_NO_FILE }\n',
      expect: 'a switch that is on can be switched OFF while the broker reports a blocker; ON stays disabled',
    },
    {
      label: 'the model select opens on a role that is on while the broker is not ready',
      file: VIEW,
      old: '  if (slot.cli_ready !== true) return { disabled: true, reason: notReady(cli) }\n',
      new: '',
      expect: 'a switch that is on can be switched OFF while the broker reports a blocker; ON stays disabled',
    },
    {
      label: 'a broker with no switch file is offered a switch it will refuse',
      file: VIEW,
      old: '  if (!cli || cli.roles_file_set !== true) return { disabled: true, reason: CLI_NO_FILE }\n',
      new: '',
      expect: 'the switch is a super-admin\'s, and only on a wired, ready slot with a switch file',
    },
    // Which slots have a switch (vision added 2026-10-09). The case that planted a switch on
    // every slot is now the image half below; a vision slot having one is the rule. The three
    // cases that rewrote hasCliSwitch's kind rule ('a vision slot has no switch', 'an image
    // slot has a switch', 'any kind the tab does not know has a switch') went when the tab
    // stopped keeping the kinds (2026-10-09): the gateway's cli_switchable decides, and its
    // kinds are held by apps/platform/backend/tests/mutate_rails_cli_switch.py. The cases after
    // them hold the field.
    {
      // The guard as it was before vision slots had a switch.
      label: 'the tab goes back to the text-only rule, dropping the vision switch',
      file: ADMIN,
      old: "                  {hasCliSwitch(s) && (\n                    <div className=\"rail-slot-cli\">",
      new: "                  {s.kind === 'chat' && (\n                    <div className=\"rail-slot-cli\">",
      expect: 'the tab draws the switch on text and vision slots only and sends the click as it is',
    },
    {
      label: 'the tab draws the switch on every slot, image ones too',
      file: ADMIN,
      old: "                  {hasCliSwitch(s) && (\n                    <div className=\"rail-slot-cli\">",
      new: "                  {(hasCliSwitch(s) || s.kind === 'image') && (\n                    <div className=\"rail-slot-cli\">",
      expect: 'the tab draws the switch on text and vision slots only and sends the click as it is',
    },
    {
      // The review's second source (2026-10-09): the tab decides the kinds itself again.
      label: 'the tab keeps its own copy of the kinds (text or vision) instead of the gateway\'s word',
      file: VIEW,
      old: '  return slot.cli_switchable === true\n',
      new: "  return (slot as { kind?: string }).kind === 'chat' || (slot as { kind?: string }).kind === 'vision'\n",
      expect: 'a slot has the Claude switch where the gateway says it does, and nowhere else',
    },
    {
      label: 'a slot the gateway says nothing about has a switch',
      file: VIEW,
      old: '  return slot.cli_switchable === true\n',
      new: '  return slot.cli_switchable !== false\n',
      expect: 'a slot has the Claude switch where the gateway says it does, and nowhere else',
    },
    {
      label: 'any truthy cli_switchable draws a switch',
      file: VIEW,
      old: '  return slot.cli_switchable === true\n',
      new: '  return Boolean(slot.cli_switchable)\n',
      expect: 'a slot has the Claude switch where the gateway says it does, and nowhere else',
    },
    {
      label: 'the header no longer says a vision switch waits for a broker that sends images',
      file: ADMIN,
      old: " A vision slot's switch turns on only when\n          the broker says it can send images to Claude.",
      new: '',
      expect: 'the tab draws the switch on text and vision slots only and sends the click as it is',
    },
    // --- a streaming role on a broker that does not stream to Claude (added 2026-10-14) -------
    {
      label: 'the header no longer says a streaming switch waits for a broker that streams',
      file: ADMIN,
      old: " A streaming slot's switch (its rail\n          streams the answer as it is written) turns on only when the broker says it streams to\n          Claude.",
      new: '',
      expect: 'the header says a streaming slot\'s switch waits for a broker that streams to Claude',
    },
    {
      label: 'the gateway rewords its streaming reason and the tab is tested with the old words',
      file: GATEWAY,
      old: 'CLI_NO_STREAM = ("the broker does not say it streams to Claude, so every streamed answer on this "',
      new: 'CLI_NO_STREAM = ("this broker cannot stream to Claude, so every streamed answer on this "',
      expect: 'a streaming slot whose broker does not stream cannot be switched ON, says why, and can be switched OFF',
    },
    // --- a vision role on a broker that does not send images to Claude (added 2026-10-09) -----
    {
      label: 'a vision slot whose broker sends no images can be switched ON',
      file: VIEW,
      old: '  if (unavailable && slot.cli_on !== true) return { disabled: true, reason: unavailable }\n',
      new: '',
      expect: 'a vision slot whose broker sends no images cannot be switched ON, says why, and can be switched OFF',
    },
    {
      label: 'a vision slot on such a broker can no longer be switched OFF',
      file: VIEW,
      old: '  if (unavailable && slot.cli_on !== true) return',
      new: '  if (unavailable) return',
      expect: 'a vision slot whose broker sends no images cannot be switched ON, says why, and can be switched OFF',
    },
    {
      label: 'the gateway\'s reason is replaced by the tab\'s own words',
      file: VIEW,
      old: '  if (unavailable && slot.cli_on !== true) return { disabled: true, reason: unavailable }\n',
      new: "  if (unavailable && slot.cli_on !== true) return { disabled: true, reason: 'not available' }\n",
      expect: 'a vision slot whose broker sends no images cannot be switched ON, says why, and can be switched OFF',
    },
    {
      label: 'a vision slot that is on no longer says its photos stay local',
      file: VIEW,
      old: '  if (unavailable) return { disabled: false, reason: unavailable }\n',
      new: '',
      expect: 'a vision slot whose broker sends no images cannot be switched ON, says why, and can be switched OFF',
    },
    {
      // Re-anchored 2026-10-14 (the not-ready line below it changed).
      label: 'the image check comes after the broker\'s blockers',
      file: VIEW,
      old: '  const unavailable = slot.cli_unavailable || null\n'
        + '  if (unavailable && slot.cli_on !== true) return { disabled: true, reason: unavailable }\n'
        + '  if (slot.cli_ready !== true && slot.cli_on !== true) return',
      new: '  const unavailable = slot.cli_unavailable || null\n'
        + '  if (slot.cli_ready !== true && slot.cli_on !== true) return',
      expect: 'a vision slot whose broker sends no images cannot be switched ON, says why, and can be switched OFF',
    },
    // --- the vision paths the review found no test for (2026-10-09) ---------------------------
    {
      // Its plant, as the review wrote it (rails_view.ts:109).
      label: 'an unwired vision slot that is on can be given a model',
      file: VIEW,
      old: '  if (slot.cli_wired !== true) return { disabled: true, reason: CLI_NOT_WIRED }\n  if (!Array.isArray(models)',
      new: "  if (slot.cli_wired !== true && (slot as { kind?: string }).kind !== 'vision') return { disabled: true, reason: CLI_NOT_WIRED }\n  if (!Array.isArray(models)",
      expect: 'an unwired vision slot that is on can be switched off but not given a model',
    },
    {
      label: 'an unwired vision slot that is on reads as wired',
      file: VIEW,
      old: '  if (slot.cli_wired !== true) return { disabled: false, reason: CLI_NOT_WIRED }\n',
      new: "  if (slot.cli_wired !== true && (slot as { kind?: string }).kind !== 'vision') return { disabled: false, reason: CLI_NOT_WIRED }\n",
      expect: 'an unwired vision slot that is on can be switched off but not given a model',
    },
    {
      // Its plant, as the review wrote it (rails_view.ts:162).
      label: 'a switch-only reply leaves a vision slot as it was',
      file: VIEW,
      old: '    slots: r.slots.map((s) => ({ ...s, cli_on: on.has(s.role), cli_ready: ready, cli_model: modelOf(s.role) })),\n',
      new: "    slots: r.slots.map((s) => (s.kind === 'vision' ? s : { ...s, cli_on: on.has(s.role), cli_ready: ready, cli_model: modelOf(s.role) })),\n",
      expect: 'the switch\'s own reply redraws a vision slot like a text slot and keeps what a switch cannot change',
    },
    {
      label: 'a switch-only reply drops the image-path reason',
      file: VIEW,
      old: 'cli_model: modelOf(s.role) })),',
      new: 'cli_model: modelOf(s.role), cli_unavailable: null })),',
      expect: 'the switch\'s own reply redraws a vision slot like a text slot and keeps what a switch cannot change',
    },
    {
      label: 'the header still says only a text slot has the switch',
      file: ADMIN,
      old: "A text or vision slot's <b>Claude</b> switch",
      new: "A text slot's <b>Claude</b> switch",
      expect: 'the tab draws the switch on text and vision slots only and sends the click as it is',
    },
    {
      label: 'the switch ignores the rule and is always clickable',
      file: ADMIN,
      old: 'disabled={sw.disabled || cliBusy === s.role}',
      new: 'disabled={cliBusy === s.role}',
      expect: 'the tab draws the switch on text and vision slots only and sends the click as it is',
    },
    {
      label: 'cli_editable is trusted when merely truthy',
      file: ADMIN,
      old: 'setCliEditable(view.cli_editable === true)',
      new: 'setCliEditable(Boolean(view.cli_editable ?? true))',
      expect: 'the tab draws the switch on text and vision slots only and sends the click as it is',
    },
    {
      label: 'the Claude shared-role note is not rendered',
      file: ADMIN,
      old: '{sharedCli && <p className="rail-slot-desc rail-cli-shared">{sharedCli}</p>}',
      new: '{sharedCli && null}',
      expect: 'the tab draws the switch on text and vision slots only and sends the click as it is',
    },
    // --- where the switches come from; a switch that landed when the tab could not be re-read --
    {
      label: 'the header calls the service defaults the switch file',
      file: VIEW,
      old: "  return cli.roles_source === 'file' ? CLI_SOURCE_FILE : CLI_SOURCE_DEFAULTS\n",
      new: '  return CLI_SOURCE_FILE\n',
      expect: 'the tab says whether the switch file or the service defaults decide',
    },
    {
      label: 'an invalid switch file is shown without the way back',
      file: VIEW,
      old: '  return invalid\n    ? ',
      new: '  return invalid && false\n    ? ',
      expect: 'an invalid switch file names the way back; an ignored-key note is shown as it is',
    },
    {
      label: 'the header stops rendering where the switches come from',
      file: ADMIN,
      old: '{cliSource && <p className="muted rail-cli-source"',
      new: '{cliSource && null && <p className="muted rail-cli-source"',
      expect: 'the tab renders both lines in the header card',
    },
    {
      label: 'a full tab is mistaken for a switch-only reply',
      file: VIEW,
      old: '  return !Array.isArray((view as Partial<RailsSettings>).rails)\n',
      new: '  return true\n',
      expect: 'a switch-only reply is told apart from the full tab',
    },
    {
      // Re-anchored 2026-10-09 when the redraw started carrying each slot's Claude model.
      label: 'the redraw keeps the checkbox from before the click',
      file: VIEW,
      old: 'cli_on: on.has(s.role), cli_ready: ready,',
      new: 'cli_on: on.has(s.role) ? s.cli_on : s.cli_on, cli_ready: ready,',
      expect: "the switch's own reply redraws cli_on and cli_ready the way the gateway does",
    },
    {
      label: 'a blocked backend reads ready after a redraw',
      file: VIEW,
      old: '  const ready = !(Array.isArray(cli.switch_blockers) && cli.switch_blockers.length)\n',
      new: '  const ready = true\n',
      expect: "the switch's own reply redraws cli_on and cli_ready the way the gateway does",
    },
    {
      label: 'a switch-only reply is handed to apply as if it were the tab',
      file: ADMIN,
      old: '      if (isSwitchOnly(view)) {\n',
      new: '      if (false) {\n',
      expect: 'the tab applies a switch-only reply to the slots it has and says why',
    },
    // --- the Claude model select (added 2026-10-09) ---------------------------------------------
    {
      label: 'the select ignores the switch\'s rule',
      file: VIEW,
      old: '  const sw = cliSwitch(slot, cli, editable)\n  if (sw.disabled) return sw\n',
      new: '  const sw = cliSwitch(slot, cli, editable)\n',
      expect: 'the select is disabled for the switch\'s reasons, in its words, and for three of its own',
    },
    {
      label: 'an unwired slot that is on can be given a model',
      file: VIEW,
      old: '  if (slot.cli_wired !== true) return { disabled: true, reason: CLI_NOT_WIRED }\n  if (!Array.isArray(models)',
      new: '  if (!Array.isArray(models)',
      expect: 'the select is disabled for the switch\'s reasons, in its words, and for three of its own',
    },
    {
      label: 'a broker with nothing to choose is offered the select',
      file: VIEW,
      old: '  if (!Array.isArray(models) || models.length === 0) return { disabled: true, reason: CLI_NO_MODELS }\n',
      new: '',
      expect: 'the select is disabled for the switch\'s reasons, in its words, and for three of its own',
    },
    {
      label: 'Default does not say what it means',
      file: VIEW,
      old: 'text: fallback ? `Default (${cliModelLabel(fallback)})` : "Default (the CLI\'s own)" }',
      new: "text: 'Default' }",
      expect: 'the choices are Default, saying what it means, then the allowlist in its order',
    },
    {
      label: 'the allowlist is offered out of the broker\'s order',
      file: VIEW,
      old: '...list.map((m) => ({ value: m, text: cliModelLabel(m) }))]',
      new: '...list.slice().sort().map((m) => ({ value: m, text: cliModelLabel(m) }))]',
      expect: 'the choices are Default, saying what it means, then the allowlist in its order',
    },
    {
      label: 'a model id is shown raw',
      file: VIEW,
      old: '  return m ? `${m[1].charAt(0).toUpperCase()}${m[1].slice(1)} ${m[2]}.${m[3]}` : id\n',
      new: '  return id\n',
      expect: 'a Claude model id reads as a plain name, and any other id as it is',
    },
    {
      label: 'the redraw keeps the model from before the click',
      file: VIEW,
      old: 'cli_model: modelOf(s.role) })),',
      new: 'cli_model: s.cli_model })),',
      expect: "the switch's own reply redraws each slot's Claude model too",
    },
    {
      label: 'a role model that is not a string is drawn',
      file: VIEW,
      old: " && typeof chosen[role] === 'string' ? chosen[role] as string : null",
      new: ' ? chosen[role] as string : null',
      expect: "the switch's own reply redraws each slot's Claude model too",
    },
    {
      label: 'the select ignores its rule',
      file: ADMIN,
      old: 'disabled={msel.disabled || cliBusy === s.role}',
      new: 'disabled={cliBusy === s.role}',
      expect: 'the tab draws the select beside the switch and sends the choice as a model',
    },
    {
      label: 'Default is sent as an empty model id',
      file: ADMIN,
      old: '{ model: e.target.value || null }',
      new: '{ model: e.target.value }',
      expect: 'the tab draws the select beside the switch and sends the choice as a model',
    },
    {
      label: 'the tab never learns the allowlist',
      file: ADMIN,
      old: 'setCliModels(Array.isArray(view.cli_models) ? view.cli_models : [])',
      new: 'setCliModels([])',
      expect: 'the tab draws the select beside the switch and sends the choice as a model',
    },
    {
      label: 'a select reason the switch already gives is shown twice',
      file: ADMIN,
      old: '{msel.reason && msel.reason !== sw.reason && cliBusy !== s.role && (',
      new: '{msel.reason && cliBusy !== s.role && (',
      expect: 'the tab draws the select beside the switch and sends the choice as a model',
    },
    // --- Apply: a box change clears the model, and the button waits for one (added 2026-10-14) -
    {
      label: 'Apply lights after a box change alone again',
      file: VIEW,
      old: '  if (!model) return false\n',
      new: '',
      expect: 'Apply lights only for a chosen model that differs from the live one, or on another box',
    },
    {
      label: 'Apply ignores a change of box',
      file: VIEW,
      old: "  return model !== slot.pattern || upstream !== (slot.upstream || 'local')\n",
      new: '  return model !== slot.pattern\n',
      expect: 'Apply lights only for a chosen model that differs from the live one, or on another box',
    },
    {
      // The rule as it was, in the tab.
      label: 'the button goes back to "something changed"',
      file: ADMIN,
      old: 'disabled={!applyable || busy}',
      new: "disabled={(current === s.pattern && curUp === (s.upstream || 'local')) || busy}",
      expect: 'the button and its click both go by canApply',
    },
    {
      label: 'the click stops asking canApply',
      file: ADMIN,
      old: '    if (!canApply(slot, model, upstream)) return\n',
      new: '',
      expect: 'the button and its click both go by canApply',
    },
    // --- Rail Manager: which setting keeps a rail off (added 2026-10-14) ----------------------
    {
      label: 'a rail PLATFORM_DISABLED_APPS removed is sent to the other list again',
      file: VIEW,
      old: "  if (r.blocked_by === 'disabled_apps') return { badge: 'off for this box', how: RAIL_DISABLED_HERE }\n",
      new: '',
      expect: 'a rail this box does not run names the setting that keeps it off',
    },
    {
      label: 'a gateway that does not say gets a guess',
      file: VIEW,
      old: "  return { badge: 'not installed', how: RAIL_EITHER }\n",
      new: "  return { badge: 'not installed', how: RAIL_NOT_ENABLED }\n",
      expect: 'a rail this box does not run names the setting that keeps it off',
    },
    {
      label: 'a rail an admin switched off gets an instruction in place of Turn on',
      file: VIEW,
      old: "  if (r.state !== 'unavailable') return null\n",
      new: "  if (r.state === 'enabled') return null\n",
      expect: 'a rail this box does not run names the setting that keeps it off',
    },
    {
      label: 'the card prints the one instruction again',
      file: ADMIN,
      old: '<span className="muted admin-rail-how" style={{ fontSize: 12 }}>{block.how}</span>',
      new: '<span className="muted admin-rail-how" style={{ fontSize: 12 }}>add to PLATFORM_ENABLED_APPS and restart</span>',
      expect: 'the card renders the gateway\'s cause, and the gateway sends the two the card reads',
    },
    {
      label: 'the card keeps its own badge',
      file: ADMIN,
      old: '{block && <Badge tone="neutral">{block.badge}</Badge>}',
      new: '{block && <Badge tone="neutral">not installed</Badge>}',
      expect: 'the card renders the gateway\'s cause, and the gateway sends the two the card reads',
    },
    {
      label: 'the gateway stops sending the cause',
      file: GATEWAY,
      old: '            "blocked_by": _rail_blocked_by(rid),\n',
      new: '',
      expect: 'the card renders the gateway\'s cause, and the gateway sends the two the card reads',
    },
    // --- the rail failure card (added 2026-10-14) ---------------------------------------------
    {
      label: 'the failure card points at Admin → Rails again',
      file: FRAME,
      old: 'Admin → Rail Manager for a',
      new: 'Admin → Rails for a',
      expect: 'the rail failure card points at the tab that draws the no bundle badge',
    },
    {
      label: 'the Rail Manager tab stops rendering the badge\'s card and the pointer stays',
      file: ADMIN,
      old: "        : tab === 'manager' ? <RailManagerTab />\n",
      new: "        : tab === 'manager' ? <ModelsTab />\n",
      expect: 'the rail failure card points at the tab that draws the no bundle badge',
    },
    {
      label: 'a super-admin actor is locked out of their own rooms',
      file: RULE,
      old: '  if (actorIsSuper) return false\n',
      new: '',
      expect: 'a plain admin is locked out of a room that holds a super-admin, and only then',
    },
    {
      label: 'no room is ever locked',
      file: RULE,
      old: '  return members.some((m) => superadmins.has(m))\n',
      new: '  return members.some((m) => superadmins.has(m)) && false\n',
      expect: 'a plain admin is locked out of a room that holds a super-admin, and only then',
    },
    {
      label: 'delete stays offered on a locked room',
      file: ADMIN,
      old: '<Button variant="danger" size="sm" disabled={b || locked}\n',
      new: '<Button variant="danger" size="sm" disabled={b}\n',
      expect: 'every control on a locked room is disabled and says why; names stop at 64',
    },
    {
      label: 'member checkboxes stay live on a locked room',
      file: ADMIN,
      old: 'const lock = b || locked || !canManage(u)',
      new: 'const lock = b || !canManage(u)',
      expect: 'every control on a locked room is disabled and says why; names stop at 64',
    },
    {
      label: 'the rename box takes any length again',
      file: ADMIN,
      old: '                               maxLength={WORKSPACE_NAME_MAX}\n',
      new: '',
      expect: 'every control on a locked room is disabled and says why; names stop at 64',
    },
    // --- Users: the gateway's reach rule (main.py _out_of_reach) -------------------------------
    {
      label: 'a row for a rail no longer in the catalog puts a user out of reach',
      file: USERS,
      old: 'apps.filter((a) => known.has(a) && !can.has(a))',
      new: 'apps.filter((a) => !can.has(a))',
      expect: 'a row is out of reach when it holds a catalog app the actor cannot grant, and only then',
    },
    {
      label: 'the apps beyond reach are named in the order the row lists them',
      file: USERS,
      old: '!can.has(a)).sort()\n',
      new: '!can.has(a))\n',
      expect: 'a row is out of reach when it holds a catalog app the actor cannot grant, and only then',
    },
    {
      label: 'reset pw is offered on a row beyond reach again',
      file: ADMIN,
      old: '<Button variant="ghost" size="sm" disabled={locked(u) || !!far(u)}',
      new: '<Button variant="ghost" size="sm" disabled={locked(u)}',
      expect: 'reset, delete and an admin row\'s role are greyed out with the reason; grants stay live',
    },
    {
      label: 'delete is offered on a row beyond reach again',
      file: ADMIN,
      old: '<Button variant="danger" size="sm" disabled={locked(u) || !!far(u)}',
      new: '<Button variant="danger" size="sm" disabled={locked(u)}',
      expect: 'reset, delete and an admin row\'s role are greyed out with the reason; grants stay live',
    },
    {
      label: 'an admin row beyond reach can be demoted from the role select again',
      file: ADMIN,
      old: ' || (u.is_admin && !!far(u))}',
      new: '}',
      expect: 'reset, delete and an admin row\'s role are greyed out with the reason; grants stay live',
    },
    // --- Workspaces: the reach rule (main.py _manageable, _refuse_room_out_of_reach; 2026-10-15)
    {
      label: 'a room holding a member beyond reach stays editable',
      file: RULE,
      old: '    return apps.length ? [{ user, apps }] : []\n',
      new: '    return []\n',
      expect: 'a room is locked when it holds a member beyond reach, and the note names each one',
    },
    {
      label: 'the lock note names only the first member beyond reach',
      file: RULE,
      old: 'const who = beyond.map(',
      new: 'const who = beyond.slice(0, 1).map(',
      expect: 'a room is locked when it holds a member beyond reach, and the note names each one',
    },
    {
      label: 'the members beyond reach are named in the order the room lists them',
      file: RULE,
      old: 'return [...members].sort().flatMap(',
      new: 'return [...members].flatMap(',
      expect: 'a room is locked when it holds a member beyond reach, and the note names each one',
    },
    {
      label: 'the reach note is given ahead of the super-admin one',
      file: RULE,
      old: '  if (roomLocked(members, superadmins, actorIsSuper)) return SUPERADMIN_ROOM_NOTE\n'
        + '  const beyond = roomBeyondReach(members, appsOf, grantable, catalogIds)\n'
        + '  if (!beyond.length) return null\n',
      new: '  const beyond = roomBeyondReach(members, appsOf, grantable, catalogIds)\n'
        + '  if (!beyond.length) return roomLocked(members, superadmins, actorIsSuper) ? SUPERADMIN_ROOM_NOTE : null\n',
      expect: 'a room is locked when it holds a member beyond reach, and the note names each one',
    },
    {
      label: 'a name beyond reach is told only the general rule',
      file: RULE,
      old: '  if (!beyond.length) return MANAGE_NOTE\n',
      new: '  return MANAGE_NOTE\n',
      expect: 'a name the actor may not add says which apps put it beyond them',
    },
    {
      label: 'the tab never learns the users\' apps, so no room is locked on reach',
      file: ADMIN,
      old: 'setAppsOf(new Map(u.users.map((x) => [x.username, x.apps])))',
      new: 'setAppsOf(new Map())',
      expect: 'the Workspaces tab judges reach from the users route\'s lists and locks the room on it',
    },
    {
      label: 'the tab locks a room on the super-admin rule alone again',
      file: ADMIN,
      old: '                const lockNote = roomLockNote(w.members, superadmins, iAmSuper, appsOf, grantable, catalogIds)\n',
      new: '                const lockNote = roomLockNote(w.members, superadmins, iAmSuper, new Map(), grantable, catalogIds)\n',
      expect: 'every control on a locked room is disabled and says why; names stop at 64',
    },
    {
      label: 'a greyed-out name in the new-room picker no longer says why',
      file: ADMIN,
      old: 'style={canManage(u) ? undefined : { opacity: 0.45 }}\n                       title={whyNot(u)}>',
      new: 'style={canManage(u) ? undefined : { opacity: 0.45 }}\n                       title={undefined}>',
      expect: 'the Workspaces tab judges reach from the users route\'s lists and locks the room on it',
    },
  ],
})
