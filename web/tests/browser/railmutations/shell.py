"""mutate_railcheck.py's mutations of the shell's frontend (and web-core, which it builds from source). NOT a test file.

Each names the check in railchecks/shell.py that must fail. mutate_railcheck.py's header
says how a run works, and why this file ships exactly when its rail does.
"""
from __future__ import annotations

from . import Mutation

MUTATIONS = [
    # The shell. Each check mounts the built shell with recipe-book as the active rail.
    Mutation("the shell no longer hands a lost session back to App", "shell", "ShellModelWidget.tsx",
             "    if (step.sessionLost) {\n      onSessionLost()\n", "    if (step.sessionLost) {\n",
             "shell-session-lost"),
    Mutation("web-core's poller polls a hidden tab", "shell", "web/src/poll.ts",
             "    if (env.hidden()) {\n      owed = true\n      return\n    }\n", "", "shell-hidden"),
    Mutation("App re-renders every 5 s again (as when it held the status)", "shell", "App.tsx",
             "  const [active, setActive] = useState<string>(currentApp)\n",
             "  const [active, setActive] = useState<string>(currentApp)\n"
             "  const [, bump] = useState(0)\n"
             "  useEffect(() => { const id = setInterval(() => bump((n) => n + 1), 5000); return () => clearInterval(id) }, [])\n",
             "shell-rail-rerender"),
    Mutation("a failed Unload is swallowed without a word", "shell", "ShellModelWidget.tsx",
             "      setError((e as Error).message || 'The request failed.')\n", "", "shell-unload-error"),
    Mutation("the shared-role note is not rendered", "shell", "AdminPage.tsx",
             # `shared && null`, not deleted: an unread local fails tsc, and the build with it.
             "{shared && <p className=\"rail-slot-desc rail-slot-shared\">{shared}</p>}", "{shared && null}",
             "shell-admin-rails"),
    Mutation("Run now is enabled while its run is in flight", "shell", "AdminPage.tsx",
             "disabled={b || running} onClick={() => runNow(", "disabled={b} onClick={() => runNow(",
             "shell-run-now"),
    # The top bar over rail content (web-core's stylesheet, built into the shell).
    Mutation("the top bar is a stacking context again (the theme menu under the tiles)", "shell",
             "web/src/styles.css", ".ptopbar {\n  grid-column: 2;\n  position: relative;\n",
             ".ptopbar {\n  grid-column: 2;\n  position: relative;\n  z-index: 0;\n", "shell-topbar-layers"),
    Mutation("the bar is lifted with a z-index of its own (rail modals stop covering it)", "shell",
             "web/src/styles.css", ".ptopbar {\n  grid-column: 2;\n  position: relative;\n",
             ".ptopbar {\n  grid-column: 2;\n  position: relative;\n  z-index: 45;\n", "shell-topbar-layers"),
    Mutation("the theme menu is capped at 640 px again", "shell", "web/src/styles.css",
             "max-height: calc(100vh - 64px);", "max-height: min(80vh, 640px);", "shell-topbar-layers"),
    Mutation("the time box loses the shared input style", "shell", "web/src/styles.css",
             "input[type='date'], input[type='time'], input[type='number']",
             "input[type='date'], input[type='number']", "shell-schedule-inputs"),
    # The broker tab after a refused or failed token list.
    Mutation("a refused list still renders the token form and the OPEN sentence", "shell", "AdminPage.tsx",
             "      {!loadErr && (<>\n", "      {(!loadErr || loadErr.length >= 0) && (<>\n", "shell-broker-refused"),
    Mutation("the refusal is in an unstyled card again", "shell", "AdminPage.tsx",
             '{loadErr && <div className="card" style={{ marginBottom: 16 }}><p className="error-line" style={{ margin: 0 }}>{loadErr}</p></div>}',
             '{loadErr && <div className="card error-banner">{loadErr}</div>}', "shell-broker-refused"),
    Mutation("a failed list read is filed as a failed action again", "shell", "AdminPage.tsx",
             "    catch (ex) { setLoadErr((ex as Error).message) }\n",
             "    catch (ex) { setErr((ex as Error).message) }\n", "shell-broker-refused"),
    # Admin > Models: one column grid for every category's table.
    Mutation("the Models tables lose their shared column grid", "shell", "AdminPage.tsx",
             '<table className="admin-table models-table">', '<table className="admin-table">',
             "shell-models-columns"),
    # The desk pet's bubble at the window's edge.
    Mutation("the bubble is not held inside the right edge", "shell", "deskpet/bubble.ts",
             "  const left = clamp(pet.x - box.w / 2, m, view.w - m - box.w)\n",
             "  const left = Math.max(m, pet.x - box.w / 2)\n", "shell-deskpet-bubble"),
    Mutation("the shell shows the whole error body, not the gateway's sentence", "shell", "web/src/platformApi.ts",
             "detail = typeof body?.detail === 'string' ? body.detail : JSON.stringify(body)",
             "detail = JSON.stringify(body)", "shell-admin-refusals"),
    Mutation("a refused Run now reads as a generic failure", "shell", "AdminPage.tsx",
             "    try { await platformApi.adminRunSchedule(rail, tid); await load() }\n"
             "    catch (ex) { setErr((ex as Error).message) }",
             "    try { await platformApi.adminRunSchedule(rail, tid); await load() }\n"
             "    catch (ex) { setErr(ex ? 'Run now failed.' : '') }", "shell-admin-refusals"),
    Mutation("a room holding a super-admin stays editable for a plain admin", "shell", "workspace_rule.ts",
             "  return members.some((m) => superadmins.has(m))\n",
             "  return members.some((m) => superadmins.has(m)) && actorIsSuper\n", "shell-workspace-lock"),
    Mutation("the tab never learns who the super-admins are", "shell", "AdminPage.tsx",
             "setSuperadmins(new Set(u.users.filter((x) => x.is_superadmin).map((x) => x.username)))",
             "setSuperadmins(new Set(u.users.filter((x) => x.is_superadmin && !x).map((x) => x.username)))",
             "shell-workspace-lock"),
    # The reach lock (2026-10-15): a room holding a member beyond the admin's reach.
    Mutation("a room holding a member beyond reach stays editable", "shell", "workspace_rule.ts",
             "  if (!beyond.length) return null\n", "  if (beyond.length >= 0) return null\n",
             "shell-workspace-lock"),
    Mutation("a locked room's note is not rendered", "shell", "AdminPage.tsx",
             "{lockNote && <p className=\"muted ws-locked\"",
             "{lockNote && false && <p className=\"muted ws-locked\"", "shell-workspace-lock"),
    Mutation("the tab keeps no member's apps, so nobody is beyond reach", "shell", "AdminPage.tsx",
             "      setAppsOf(new Map(u.users.map((x) => [x.username, x.apps])))\n",
             "      setAppsOf(new Map(u.users.map((x) => [x.username, x.apps.slice(0, 0)])))\n",
             "shell-workspace-lock"),
    Mutation("the tab takes every catalog app as one the admin may grant", "shell", "AdminPage.tsx",
             "      setGrantable(u.grantable ?? [])\n      setCatalogIds(",
             "      setGrantable(u.catalog.map((a) => a.id))\n      setCatalogIds(",
             "shell-workspace-lock"),
    Mutation("the tab works the shared list out from the roles again", "shell", "AdminPage.tsx",
             "              const shared = sharedRoleNote(s)\n",
             "              const shared = sharedRoleNote({ shared_with: rails.filter((x) => x.label !== rail.label"
             " && x.slots.some((y) => y.role === s.role)).map((x) => x.label) })\n", "shell-admin-rails"),
    # The Rails tab's Claude switch (one per text or vision slot, keyed by role, super-admin only).
    # Re-anchored 2026-10-09 when the row's guard became hasCliSwitch (vision slots got the switch).
    Mutation("the Claude switch row is never rendered", "shell", "AdminPage.tsx",
             "                  {hasCliSwitch(s) && (\n                    <div className=\"rail-slot-cli\">",
             "                  {false && hasCliSwitch(s) && (\n                    <div className=\"rail-slot-cli\">",
             "shell-admin-rails"),
    # The case that planted a switch on vision slots went the day that became the rule; these
    # are its two halves now. In rails_view.ts, where the plant still builds (the text-only guard
    # in AdminPage.tsx would leave hasCliSwitch imported and unread, which fails tsc).
    # Re-anchored 2026-10-09 when hasCliSwitch started reading the gateway's cli_switchable.
    Mutation("a vision slot loses its Claude switch again (the text-only rule)", "shell", "rails_view.ts",
             "  return slot.cli_switchable === true\n",
             "  return (slot as { kind?: string }).kind === 'chat'\n", "shell-admin-rails"),
    Mutation("an image slot gets a Claude switch", "shell", "rails_view.ts",
             "  return slot.cli_switchable === true\n",
             "  return slot.cli_switchable === true || (slot as { kind?: string }).kind === 'image'\n",
             "shell-admin-rails"),
    # A vision role on a broker that does not say it sends images to Claude (2026-10-09).
    Mutation("a vision slot whose broker sends no images is offered its switch", "shell", "rails_view.ts",
             "  if (unavailable && slot.cli_on !== true) return { disabled: true, reason: unavailable }\n",
             "", "shell-admin-rails"),
    Mutation("a vision slot on such a broker can no longer be switched off", "shell", "rails_view.ts",
             "  if (unavailable && slot.cli_on !== true) return",
             "  if (unavailable) return", "shell-admin-rails"),
    Mutation("a vision switch disabled for the image path gives no reason", "shell", "rails_view.ts",
             "  if (unavailable && slot.cli_on !== true) return { disabled: true, reason: unavailable }\n",
             "  if (unavailable && slot.cli_on !== true) return { disabled: true, reason: null }\n",
             "shell-admin-rails"),
    # A streaming role on a broker that does not say it streams to Claude (2026-10-14). The tab's
    # rule is the vision one, generic on `cli_unavailable`, so each plant reaches the streaming
    # reason alone: the vision check above cannot see it, and only shell-admin-rails-stream can.
    Mutation("a switch disabled because its broker does not stream gives no reason", "shell", "rails_view.ts",
             "  if (unavailable && slot.cli_on !== true) return { disabled: true, reason: unavailable }\n",
             "  if (unavailable && slot.cli_on !== true) return { disabled: true, reason: "
             "unavailable.includes('stream') ? null : unavailable }\n",
             "shell-admin-rails-stream"),
    Mutation("a streaming slot whose broker does not stream is offered its switch", "shell", "rails_view.ts",
             "  if (unavailable && slot.cli_on !== true) return",
             "  if (unavailable && slot.cli_on !== true && !unavailable.includes('stream')) return",
             "shell-admin-rails-stream"),
    Mutation("a vision slot's switch row has no model select", "shell", "AdminPage.tsx",
             "                      <label className=\"rail-cli-model\">\n",
             "                      <label className={s.kind === 'vision' ? 'rail-cli-model-off' : 'rail-cli-model'}>\n",
             "shell-admin-rails"),
    # Re-anchored 2026-10-09 when setRoleCli started taking the change ({ enabled } or { model }).
    Mutation("a click on the Claude switch never reaches the gateway", "shell", "AdminPage.tsx",
             "onChange={(e) => void setRoleCli(s.role, { enabled: e.target.checked })} />",
             "onChange={() => void setRoleCli} />", "shell-admin-rails"),
    Mutation("the Claude switch ignores its rule (unwired, plain admin, not ready)", "shell", "AdminPage.tsx",
             "disabled={sw.disabled || cliBusy === s.role}", "disabled={cliBusy === s.role}",
             "shell-admin-rails"),
    Mutation("the tab trusts itself to be a super-admin", "shell", "AdminPage.tsx",
             "setCliEditable(view.cli_editable === true)",
             "setCliEditable(view.cli_editable === true || view.rails.length > 0)",
             "shell-admin-rails"),
    Mutation("the Claude shared-role note is not rendered", "shell", "AdminPage.tsx",
             '{sharedCli && <p className="rail-slot-desc rail-cli-shared">{sharedCli}</p>}', "{sharedCli && null}",
             "shell-admin-rails"),
    # Re-anchored 2026-10-14, when a switch that is on stayed clickable on such a backend.
    Mutation("a backend that is not ready gives no reason", "shell", "rails_view.ts",
             "  if (slot.cli_ready !== true && slot.cli_on !== true) return { disabled: true, reason: notReady(cli) }\n",
             "  if (slot.cli_ready !== true && slot.cli_on !== true) return { disabled: true, reason: null }\n",
             "shell-admin-rails"),
    # OFF is the kill switch: a switch that is on stays clickable on such a backend (2026-10-14).
    Mutation("a switch that is on is greyed out again while the broker is not ready", "shell", "rails_view.ts",
             "  if (slot.cli_ready !== true && slot.cli_on !== true) return { disabled: true, reason: notReady(cli) }\n",
             "  if (slot.cli_ready !== true) return { disabled: true, reason: notReady(cli) }\n",
             "shell-admin-rails"),
    Mutation("the model select opens on a role that is on while the broker is not ready", "shell",
             "rails_view.ts",
             "  if (slot.cli_ready !== true) return { disabled: true, reason: notReady(cli) }\n"
             "  if (slot.cli_wired !== true) return { disabled: true, reason: CLI_NOT_WIRED }\n",
             "  if (slot.cli_wired !== true) return { disabled: true, reason: CLI_NOT_WIRED }\n",
             "shell-admin-rails"),
    # Where the switches come from, and a switch that landed while the tab could not be re-read.
    Mutation("the tab never says the service defaults decide", "shell", "AdminPage.tsx",
             '{cliSource && <p className="muted rail-cli-source"',
             '{cliSource && null && <p className="muted rail-cli-source"', "shell-admin-rails-cli"),
    Mutation("an invalid switch file is shown without the way back", "shell", "rails_view.ts",
             "  return invalid\n    ? ", "  return invalid && false\n    ? ", "shell-admin-rails-cli"),
    # In rails_view.ts, not AdminPage.tsx: there `if (false)` does not type-check (the union
    # reaches apply), which is a guard of its own, but this harness needs a bundle that builds.
    Mutation("a switch-only reply is handed to apply as if it were the tab", "shell", "rails_view.ts",
             "  return !Array.isArray((view as Partial<RailsSettings>).rails)\n",
             "  return Array.isArray((view as Partial<RailsSettings>).rails) && false\n",
             "shell-admin-rails-cli"),
    # Re-anchored 2026-10-09 when the redraw started carrying each slot's Claude model.
    Mutation("a switch-only reply leaves the checkbox from before the click", "shell", "rails_view.ts",
             "cli_on: on.has(s.role), cli_ready: ready,",
             "cli_on: on.has(s.role) ? s.cli_on : s.cli_on, cli_ready: ready,",
             "shell-admin-rails-cli"),
    # The Claude model select beside each switch (2026-10-09).
    Mutation("a model choice never reaches the gateway", "shell", "AdminPage.tsx",
             "onChange={(e) => void setRoleCli(s.role, { model: e.target.value || null })}>",
             "onChange={() => void setRoleCli}>", "shell-admin-rails"),
    Mutation("a model choice is sent as a switch", "shell", "AdminPage.tsx",
             "{ model: e.target.value || null }", "{ enabled: e.target.value !== '' }",
             "shell-admin-rails"),
    Mutation("the model select ignores its rule (plain admin, not ready)", "shell", "AdminPage.tsx",
             "disabled={msel.disabled || cliBusy === s.role}", "disabled={cliBusy === s.role}",
             "shell-admin-rails"),
    Mutation("the model select shows Default whatever the role's model", "shell", "AdminPage.tsx",
             "<select value={s.cli_model ?? ''}", "<select value={s.cli_model ? '' : ''}",
             "shell-admin-rails"),
    Mutation("the model select offers the raw ids", "shell", "rails_view.ts",
             "...list.map((m) => ({ value: m, text: cliModelLabel(m) }))]",
             "...list.map((m) => ({ value: m, text: m || cliModelLabel(m) }))]", "shell-admin-rails"),
    Mutation("the Claude shared-role note stops covering the model", "shell", "rails_view.ts",
             "`Shared role: the Claude switch and model here also apply to ${names}.`",
             "`Shared role: switching Claude here also switches ${names}.`", "shell-admin-rails"),
    Mutation("a switch-only reply leaves the model select from before the change", "shell", "rails_view.ts",
             "cli_model: modelOf(s.role) })),", "cli_model: modelOf(s.role) ? s.cli_model : s.cli_model })),",
             "shell-admin-rails-cli"),
    Mutation("a switch-only reply does not say the tab could not be re-read", "shell", "AdminPage.tsx",
             "        setErr(view.reload_error)\n", "", "shell-admin-rails-cli"),
]
