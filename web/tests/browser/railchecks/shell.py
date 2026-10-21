"""The platform shell's checks. Each mounts the shell's own built bundle (the shell harness in
railchecks/harness.py) with recipe-book among its rails; shell-topbar-layers mounts terminal-fun
as well and opens the menus over each, which is why terminal_fun_api is here: terminal-fun has no
check of its own.
"""
from __future__ import annotations

import copy
import json

from .harness import (ADMIN_RAILS, CLI_NO_STREAM, ORIGIN, Api, NoFixture, Shell, broker_streams, rail,
                      shell_up)
from .recipe_book import recipe_api


def check_shell_session_lost(page) -> list[str]:
    """An expired session sends the user back to the login form, rather than reading as
    "Broker offline" with the status poll running for ever."""
    shell = Shell()
    shell_up(page, shell, {"recipe-book": rail("recipe-book", recipe_api("ok", admin=False))})
    page.wait_for_selector(".mw-trigger", timeout=10000)
    shell.status = 401
    try:
        page.wait_for_selector("input[type=password]", timeout=13000)
    except Exception:
        offline = page.locator("text=Broker offline").count()
        return [f"no login form 13 s after the session expired (Broker offline shown: {bool(offline)})"]
    before = sum(1 for c in shell.calls if c.endswith("/status"))
    page.wait_for_timeout(6000)
    after = sum(1 for c in shell.calls if c.endswith("/status"))
    return [] if after == before else [f"the status poll kept running behind the login form ({after - before} more)"]


def check_shell_hidden(page) -> list[str]:
    """No status poll while the tab is hidden, and one as soon as it is visible again."""
    shell = Shell()
    shell_up(page, shell, {"recipe-book": rail("recipe-book", recipe_api("ok", admin=False))})
    page.wait_for_selector(".mw-trigger", timeout=10000)
    page.evaluate("window.__setVis('hidden')")
    n0 = sum(1 for c in shell.calls if c.endswith("/status"))
    page.wait_for_timeout(11000)
    n1 = sum(1 for c in shell.calls if c.endswith("/status"))
    page.evaluate("window.__setVis('visible')")
    page.wait_for_timeout(800)
    n2 = sum(1 for c in shell.calls if c.endswith("/status"))
    out = []
    if n1 != n0:
        out.append(f"{n1 - n0} status polls ran in 11 s with the tab hidden")
    if n2 == n1:
        out.append("no status poll when the tab became visible again")
    return out


def check_shell_rail_rerender(page) -> list[str]:
    """The shell's status poll must not re-render the active rail. A parent re-render hands the
    rail's root component a NEW props object; the rail re-rendering itself (its own 6 s chips
    poll) keeps the old one. So count the distinct props objects its fiber has held."""
    shell = Shell()
    shell_up(page, shell, {"recipe-book": rail("recipe-book", recipe_api("ok", admin=False))})
    page.wait_for_selector(".recipe-book", timeout=10000)
    page.evaluate("""() => {
      window.__props = new Set();
      const sample = () => {
        const el = document.querySelector('.recipe-book');
        if (!el) return;
        const key = Object.keys(el).find((k) => k.startsWith('__reactFiber$'));
        const f = el[key] && el[key].return;      // the host div's parent: RecipeBookModule
        if (!f) return;
        window.__props.add(f.memoizedProps);
        if (f.alternate) window.__props.add(f.alternate.memoizedProps);
      };
      sample();
      window.__sampler = setInterval(sample, 250);
    }""")
    start = sum(1 for c in shell.calls if c.endswith("/status"))
    page.wait_for_timeout(11500)
    polls = sum(1 for c in shell.calls if c.endswith("/status")) - start
    seen = page.evaluate("window.__props.size")
    out = []
    if polls < 2:
        out.append(f"only {polls} status polls in the window, so nothing was tested")
    if seen != 1:
        out.append(f"the rail's root component got {seen} different props objects across {polls} status polls")
    return out
# The gateway's (and broker's) own sentences, copied from scheduler.py, main.py and the broker's
# main.py, so the check asserts the admin reads them unchanged.
RUN_REFUSAL = "recipe-book/t1 is already running"
USER_REFUSAL = ("a username is 1-64 printable ASCII characters (letters, digits, punctuation, "
                "inner spaces): it is sent to every app in an HTTP header")
TOKEN_REFUSAL = ("refused: this broker has no BROKER_AUTH_TOKEN, and creating its first named token "
                 "turns token checks on, so the gateway, this console and every rail (which hold no "
                 "token) would get 401 on their next call. Set BROKER_AUTH_TOKEN for the broker and "
                 "the gateway, restart both, then create named tokens.")


def check_shell_admin_rails(page) -> list[str]:
    """A slot on a shared role says which other rails a repoint moves; a rail-own slot does not.
    And the Claude switch (see _check_claude_switch)."""
    shell = Shell(admin=True)
    shell_up(page, shell, {"recipe-book": rail("recipe-book", recipe_api("ok", admin=True))})
    page.wait_for_selector(".mw-trigger", timeout=10000)
    page.goto(ORIGIN + "/#/admin")
    page.wait_for_selector(".rail-slot", timeout=10000)
    notes = page.eval_on_selector_all(".rail-slot", """els => els.map((e) => [
      e.closest('.card').querySelector('.rail-title').textContent.trim(),
      e.querySelector('.rail-slot-label').textContent.trim(),
      (e.querySelector('.rail-slot-shared') || {}).textContent || null])""")
    want = {
        ("EDU-Suite", "Content"): "Shared role: a change here also repoints IEP Goals, IEP Present Levels.",
        ("IEP Goals", "Draft"): None,
        ("IEP Goals", "Translate"): "Shared role: a change here also repoints EDU-Suite.",
        ("Finance", "Chat"): None,
    }
    got = {(t.split(" ", 1)[-1], label): note for t, label, note in notes}
    out = [f"{k}: {got.get(k)!r}, expected {v!r}" for k, v in want.items() if got.get(k, "MISSING") != v]
    if page.locator("text=repoints only that rail").count():
        out.append("the tab still promises a repoint moves only one rail")
    return out + _check_claude_switch(page, shell)


# The gateway's (rails_view.ts) sentences, restated so the check asserts what the admin reads.
CLI_NOT_WIRED = "this rail does not pass who is asking, so every call stays local"
CLI_SUPERADMIN_ONLY = "super-admin only"


def _switches(page) -> dict:
    """(rail, slot) -> None when the slot has no Claude switch, else its state and both notes."""
    rows = page.eval_on_selector_all(".rail-slot", """els => els.map((e) => {
      const sw = e.querySelector('.rail-slot-cli input[role=switch]');
      return [e.closest('.card').querySelector('.rail-title').textContent.trim(),
              e.querySelector('.rail-slot-label').textContent.trim(),
              sw ? [sw.checked, sw.disabled] : null,
              (e.querySelector('.rail-cli-why') || {}).textContent || null,
              (e.querySelector('.rail-cli-shared') || {}).textContent || null]})""")
    return {(t.split(" ", 1)[-1], label): (None if sw is None else (sw[0], sw[1], why, shared))
            for t, label, sw, why, shared in rows}


def _check_claude_switch(page, shell: Shell) -> list[str]:
    """The Claude switch: one per TEXT or VISION slot (none on an image slot), keyed by role,
    effective on click, and disabled with the reason a super-admin (or a plain admin) can act
    on."""
    out: list[str] = []
    got = _switches(page)
    want = {
        ("EDU-Suite", "Content"): (True, False, None,
                                   "Shared role: the Claude switch and model here also apply to IEP Goals, IEP Present Levels."),
        ("IEP Goals", "Draft"): (True, False, None, None),
        ("IEP Goals", "Translate"): (True, False, None,
                                     "Shared role: the Claude switch and model here also apply to EDU-Suite."),
        ("Finance", "Chat"): (False, True, CLI_NOT_WIRED, None),
        # A vision slot has a switch under the same rules: unwired, it is disabled and says why;
        # wired, it can be clicked.
        ("Finance", "Receipts"): (False, True, CLI_NOT_WIRED, None),
        ("Photo Lab", "Photo reader"): (False, False, None, None),
        ("Photo Lab", "Icons"): None,           # an image slot: no switch at all
    }
    out += [f"switch {k}: {got.get(k, 'MISSING')!r}, expected {v!r}"
            for k, v in want.items() if got.get(k, "MISSING") != v]
    # The Claude model select beside each switch: the role's model (Default when it has none),
    # Default then the broker's list with plain labels, and the switch's rule (the unwired Chat
    # slot's is disabled, its reason already shown by the switch, so not repeated).
    got_m = _model_selects(page)
    want_m = {
        ("EDU-Suite", "Content"): ("", False, None),
        ("IEP Goals", "Draft"): (OPUS, False, None),
        ("IEP Goals", "Translate"): ("", False, None),
        ("Finance", "Chat"): ("", True, None),
        ("Finance", "Receipts"): ("", True, None),
        ("Photo Lab", "Photo reader"): ("", False, None),
        ("Photo Lab", "Icons"): None,
    }
    out += [f"model select {k}: {got_m.get(k, 'MISSING')!r}, expected {v!r}"
            for k, v in want_m.items() if got_m.get(k, "MISSING") != v]
    labels = page.eval_on_selector(".rail-slot .rail-cli-model select",
                                   "s => [...s.options].map((o) => o.textContent)")
    if labels != MODEL_LABELS:
        out.append(f"the model select offers {labels!r}, expected {MODEL_LABELS!r}")
    if out:
        return out
    # Click the EDU-Suite switch off. One PUT for the ROLE, and BOTH slots on @edu follow the
    # gateway's answer; @iep, which only looks similar, does not move.
    page.locator(".rail-slot", has=page.locator(".rail-slot-label", has_text="Content")) \
        .locator("input[role=switch]").click()
    for _ in range(50):
        now = _switches(page)
        if now[("EDU-Suite", "Content")][0] is False and now[("IEP Goals", "Translate")][0] is False:
            break
        page.wait_for_timeout(100)
    if shell.cli_puts != [("edu", {"enabled": False})]:
        out.append(f"the click sent {shell.cli_puts!r}, expected one PUT for edu with enabled false")
    now = _switches(page)
    flipped = (now[("EDU-Suite", "Content")][0], now[("IEP Goals", "Translate")][0],
               now[("IEP Goals", "Draft")][0])
    if flipped != (False, False, True):
        out.append(f"after switching @edu off: Content/Translate/Draft checked = {flipped}, "
                   "expected (False, False, True)")
    out += _choose_models(page, shell)
    out += _switch_vision(page, shell)
    out += _vision_without_images(page, shell)
    # A plain admin: every switch disabled, and the reason is the one they can act on.
    shell.rails["cli_editable"] = False
    page.reload()
    page.wait_for_selector(".rail-slot-cli", timeout=10000)
    plain = {k: v for k, v in _switches(page).items() if v is not None}
    wrong = {k: v for k, v in plain.items() if v[1] is not True or v[2] != CLI_SUPERADMIN_ONLY}
    if wrong:
        out.append(f"for a plain admin these switches are not disabled as super-admin only: {wrong}")
    open_m = {k: v for k, v in _model_selects(page).items() if v is not None and v[1] is not True}
    if open_m:
        out.append(f"for a plain admin these model selects are not disabled: {open_m}")
    # A broker that is not ready: a switch that is off is disabled, naming the blocker. One that
    # is on (Draft; @edu's Content was switched off above) stays clickable, still naming it,
    # because OFF is the kill switch the gateway and the broker take whatever the blockers say;
    # it used to be greyed out too (2026-10-14). Every model select is disabled.
    not_ready = "Claude is not ready on the broker: disabled"
    shell.rails["cli_editable"] = True
    shell.rails["cli"]["switch_blockers"] = ["disabled"]
    for r in shell.rails["rails"]:
        for s in r["slots"]:
            s["cli_ready"] = False
    page.reload()
    page.wait_for_selector(".rail-slot-cli", timeout=10000)
    now = _switches(page)
    draft, content = now[("IEP Goals", "Draft")], now[("EDU-Suite", "Content")]
    if draft[:3] != (True, False, not_ready):
        out.append(f"with the broker not ready the Draft switch (on) reads {draft!r}, expected "
                   f"clickable to switch it off and naming the blocker")
    if content[:3] != (False, True, not_ready):
        out.append(f"with the broker not ready the Content switch (off) reads {content!r}")
    for key in (("IEP Goals", "Draft"), ("EDU-Suite", "Content")):
        sel = _model_selects(page)[key]
        if sel is None or sel[1] is not True:
            out.append(f"with the broker not ready the {key[1]} model select reads {sel!r}")
    if draft[:2] == (True, False):
        # The OFF goes through: one PUT for @iep with enabled false, and then it cannot be
        # switched back on.
        before = len(shell.cli_puts)
        page.locator(".rail-slot", has=page.locator(".rail-slot-label", has_text="Draft")) \
            .locator("input[role=switch]").click()
        for _ in range(50):
            if _switches(page)[("IEP Goals", "Draft")][0] is False:
                break
            page.wait_for_timeout(100)
        if shell.cli_puts[before:] != [("iep", {"enabled": False})]:
            out.append(f"switching Draft off sent {shell.cli_puts[before:]!r}, expected one PUT "
                       "for iep with enabled false")
        after = _switches(page)[("IEP Goals", "Draft")]
        if after[:3] != (False, True, not_ready):
            out.append(f"after switching Draft off on a broker that is not ready it reads {after!r}")
    return out


OPUS, HAIKU = "claude-opus-5-5", "claude-haiku-5-5"
# rails_view.ts cliModelOptions, for ADMIN_RAILS (no BROKER_CLI_MODEL, so the CLI's own default).
MODEL_LABELS = ["Default (the CLI's own)", "Haiku 5.5", "Sonnet 5.5", "Opus 5.5"]


def _model_selects(page) -> dict:
    """(rail, slot) -> None when the slot has no Claude model select, else (value, disabled,
    the select's own reason line or None)."""
    rows = page.eval_on_selector_all(".rail-slot", """els => els.map((e) => {
      const sel = e.querySelector('.rail-slot-cli .rail-cli-model select');
      return [e.closest('.card').querySelector('.rail-title').textContent.trim(),
              e.querySelector('.rail-slot-label').textContent.trim(),
              sel ? [sel.value, sel.disabled] : null,
              (e.querySelector('.rail-cli-model-why') || {}).textContent || null]})""")
    return {(t.split(" ", 1)[-1], label): (None if sel is None else (sel[0], sel[1], why))
            for t, label, sel, why in rows}


def _choose_models(page, shell: Shell) -> list[str]:
    """Choose Haiku for @edu on the Content slot, then Default for @iep on Draft. One PUT each,
    with the model alone (the switch is not touched), and every slot on the role follows the
    gateway's answer."""
    out: list[str] = []
    before = len(shell.cli_puts)

    def pick(label: str, value: str) -> None:
        page.locator(".rail-slot", has=page.locator(".rail-slot-label", has_text=label)) \
            .locator(".rail-cli-model select").select_option(value)

    def wait_for(key, value) -> None:
        for _ in range(50):
            if (_model_selects(page).get(key) or ("?",))[0] == value:
                return
            page.wait_for_timeout(100)

    pick("Content", HAIKU)
    wait_for(("IEP Goals", "Translate"), HAIKU)
    pick("Draft", "")
    wait_for(("IEP Goals", "Draft"), "")
    puts = shell.cli_puts[before:]
    if puts != [("edu", {"model": HAIKU}), ("iep", {"model": None})]:
        out.append(f"the model choices sent {puts!r}, expected edu Haiku then iep null")
    now = _model_selects(page)
    chosen = (now[("EDU-Suite", "Content")][0], now[("IEP Goals", "Translate")][0],
              now[("IEP Goals", "Draft")][0])
    if chosen != (HAIKU, HAIKU, ""):
        out.append(f"after the choices Content/Translate/Draft read {chosen}, expected "
                   f"({HAIKU!r}, {HAIKU!r}, '')")
    switches = _switches(page)
    if (switches[("EDU-Suite", "Content")][0], switches[("IEP Goals", "Draft")][0]) != (False, True):
        out.append("a model choice moved a Claude switch")
    return out


def _switch_vision(page, shell: Shell) -> list[str]:
    """The wired vision slot's switch and model select work like a text slot's: a click is one
    PUT for its role with `enabled`, a choice one PUT with `model`, and the slot shows the
    gateway's answer."""
    out: list[str] = []
    before = len(shell.cli_puts)
    row = page.locator(".rail-slot", has=page.locator(".rail-slot-label", has_text="Photo reader"))
    row.locator("input[role=switch]").click()
    for _ in range(50):
        if (_switches(page).get(("Photo Lab", "Photo reader")) or (None,))[0] is True:
            break
        page.wait_for_timeout(100)
    row.locator(".rail-cli-model select").select_option(HAIKU)
    for _ in range(50):
        if (_model_selects(page).get(("Photo Lab", "Photo reader")) or ("?",))[0] == HAIKU:
            break
        page.wait_for_timeout(100)
    puts = shell.cli_puts[before:]
    if puts != [("photo-vision", {"enabled": True}), ("photo-vision", {"model": HAIKU})]:
        out.append(f"the vision slot's switch and select sent {puts!r}, expected photo-vision "
                   f"enabled true then model Haiku")
    sw = _switches(page).get(("Photo Lab", "Photo reader"))
    sel = _model_selects(page).get(("Photo Lab", "Photo reader"))
    if (sw or (None,))[0] is not True or (sel or (None,))[0] != HAIKU:
        out.append(f"after the vision slot's switch and choice it reads {sw!r} / {sel!r}")
    return out


# main.py's CLI_NO_VISION, restated so the check asserts what the admin reads.
CLI_NO_VISION = ("the broker does not say it can send images to Claude, so every image call "
                 "on this role stays local")


def _set_on(shell: Shell, role: str, on: bool) -> None:
    for r in shell.rails["rails"]:
        for s in r["slots"]:
            if s["role"] == role:
                s["cli_on"] = on
    live = set(shell.rails["cli"]["enabled_roles"])
    (live.add if on else live.discard)(role)
    shell.rails["cli"]["enabled_roles"] = sorted(live)


def _mark_vision(shell: Shell, sends_images: bool) -> None:
    """The gateway's payload for a broker that does (or does not) say it sends images to
    Claude: `vision` in the cli block, and CLI_NO_VISION on every slot of a vision role."""
    shell.rails["cli"]["vision"] = sends_images
    for r in shell.rails["rails"]:
        for s in r["slots"]:
            if s["kind"] == "vision":
                s["cli_unavailable"] = None if sends_images else CLI_NO_VISION


def _vision_without_images(page, shell: Shell) -> list[str]:
    """A broker that does not say it sends images to Claude. The wired vision slot, off, is
    disabled with the gateway's reason, and so is its model select; the unwired one still
    gives the wiring reason first. On (BROKER_CLI_ROLES, say), it can be switched OFF and
    still says why; one PUT, and then it cannot be switched back on."""
    out: list[str] = []
    key = ("Photo Lab", "Photo reader")
    _mark_vision(shell, False)
    _set_on(shell, "photo-vision", False)
    page.reload()
    page.wait_for_selector(".rail-slot-cli", timeout=10000)
    sw, sel = _switches(page).get(key), _model_selects(page).get(key)
    if sw != (False, True, CLI_NO_VISION, None):
        out.append(f"off, on a broker that sends no images, the vision switch reads {sw!r}, "
                   f"expected disabled with the gateway's reason")
    if sel is None or sel[1] is not True:
        out.append(f"off, on a broker that sends no images, the vision model select reads {sel!r}")
    receipts = _switches(page).get(("Finance", "Receipts"))
    if receipts != (False, True, CLI_NOT_WIRED, None):
        out.append(f"the unwired vision switch reads {receipts!r}, expected the wiring reason first")
    _set_on(shell, "photo-vision", True)
    page.reload()
    page.wait_for_selector(".rail-slot-cli", timeout=10000)
    sw, sel = _switches(page).get(key), _model_selects(page).get(key)
    if sw != (True, False, CLI_NO_VISION, None):
        out.append(f"on, on a broker that sends no images, the vision switch reads {sw!r}, "
                   f"expected clickable (OFF is always allowed) with the reason")
    if sel is None or sel[1] is not False:
        out.append(f"on, the vision model select reads {sel!r}, expected usable")
    before = len(shell.cli_puts)
    page.locator(".rail-slot", has=page.locator(".rail-slot-label", has_text="Photo reader")) \
        .locator("input[role=switch]").click()
    for _ in range(50):
        if (_switches(page).get(key) or (None,))[0] is False:
            break
        page.wait_for_timeout(100)
    if shell.cli_puts[before:] != [("photo-vision", {"enabled": False})]:
        out.append(f"switching the vision slot off sent {shell.cli_puts[before:]!r}")
    after = _switches(page).get(key)
    if after != (False, True, CLI_NO_VISION, None):
        out.append(f"after switching it off the vision switch reads {after!r}, expected disabled")
    _mark_vision(shell, True)
    return out


# rails_view.ts's sentences, restated so the check asserts what the admin reads.
CLI_SOURCE_DEFAULTS = ("Claude switches: the broker has no switch file right now, so its service "
                       "defaults (BROKER_CLI_ROLES) decide which roles are on. The first switch "
                       "writes the file. A deleted file reads the same way, and the switches it "
                       "held are gone.")
CLI_SOURCE_FILE = "Claude switches: the broker's switch file decides which roles are on."
RELOAD_ERROR = ("The switch was applied, but the Rails tab could not be re-read: broker "
                "unreachable: connection reset")
FILE_PROBLEM = "BROKER_CLI_ROLES_FILE is invalid: not valid JSON (Expecting value at line 1)"


def check_shell_admin_rails_cli(page) -> list[str]:
    """Where the Claude switches come from, what the broker said about its switch file, and a
    switch that landed while the tab could not be re-read.

    A switch file that is set but absent (the documented reset, deleting it) used to look exactly
    like one that agreed with BROKER_CLI_ROLES. And when the broker took a switch but the
    gateway's re-read failed, the tab got a 502 and kept the old checkbox while the switch was
    live; the gateway now answers with the switch's own state, which the tab draws."""
    shell = Shell(admin=True, cli_reload_error=RELOAD_ERROR)
    shell.rails = copy.deepcopy(ADMIN_RAILS)
    shell.rails["cli"]["roles_source"] = "env"
    shell_up(page, shell, {"recipe-book": rail("recipe-book", recipe_api("ok", admin=True))})
    page.wait_for_selector(".mw-trigger", timeout=10000)
    page.goto(ORIGIN + "/#/admin")
    page.wait_for_selector(".rail-slot-cli", timeout=10000)
    out: list[str] = []
    source = page.locator(".rail-cli-source").all_inner_texts()
    if source != [CLI_SOURCE_DEFAULTS]:
        out.append(f"with the file set but absent the tab says {source!r}, "
                   f"expected the service-defaults line")
    if page.locator(".rail-cli-problem").count():
        out.append("a switch-file problem line is shown with no problem")
    # Switch @edu off. The fake gateway answers with the switch's own state and no tab.
    page.locator(".rail-slot", has=page.locator(".rail-slot-label", has_text="Content")) \
        .locator("input[role=switch]").click()
    for _ in range(50):
        now = _switches(page)
        if now[("EDU-Suite", "Content")][0] is False and now[("IEP Goals", "Translate")][0] is False:
            break
        page.wait_for_timeout(100)
    now = _switches(page)
    flipped = (now[("EDU-Suite", "Content")][0], now[("IEP Goals", "Translate")][0],
               now[("IEP Goals", "Draft")][0])
    if flipped != (False, False, True):
        out.append(f"after a switch-only reply turning @edu off: Content/Translate/Draft checked = "
                   f"{flipped}, expected (False, False, True)")
    shown = page.locator(".error-line").all_inner_texts()
    if RELOAD_ERROR not in shown:
        out.append(f"the tab did not say it could not be re-read (shown: {shown!r})")
    if not page.locator(".rail-slot").count():
        out.append("the tab lost its slots on a switch-only reply")
    # A model chosen while the tab cannot be re-read: the select shows the live choice on every
    # slot of the role, drawn from the switch's own reply.
    page.locator(".rail-slot", has=page.locator(".rail-slot-label", has_text="Content")) \
        .locator(".rail-cli-model select").select_option(HAIKU)
    for _ in range(50):
        if (_model_selects(page)[("IEP Goals", "Translate")] or ("?",))[0] == HAIKU:
            break
        page.wait_for_timeout(100)
    now_m = _model_selects(page)
    chosen = (now_m[("EDU-Suite", "Content")][0], now_m[("IEP Goals", "Translate")][0],
              now_m[("IEP Goals", "Draft")][0])
    if chosen != (HAIKU, HAIKU, OPUS):
        out.append(f"after a switch-only reply choosing Haiku for @edu: Content/Translate/Draft "
                   f"read {chosen}, expected ({HAIKU!r}, {HAIKU!r}, {OPUS!r})")
    # An invalid switch file: the problem and the way back, and the file named as the source.
    shell.cli_reload_error = None
    shell.rails["cli"].update(roles_source="file", roles_problem=FILE_PROBLEM,
                              blockers=["cli_roles_file_invalid"],
                              switch_blockers=["cli_roles_file_invalid"])
    page.reload()
    page.wait_for_selector(".rail-slot-cli", timeout=10000)
    problem = page.locator(".rail-cli-problem").all_inner_texts()
    want = (f"{FILE_PROBLEM} No role is on until it is fixed. Deleting the file resets every "
            "role to the service defaults (BROKER_CLI_ROLES), and the switches it held are lost.")
    if problem != [want]:
        out.append(f"an invalid switch file reads {problem!r}, expected {want!r}")
    source = page.locator(".rail-cli-source").all_inner_texts()
    if source != [CLI_SOURCE_FILE]:
        out.append(f"with the file deciding the tab says {source!r}")
    return out


def check_shell_admin_rails_stream(page) -> list[str]:
    """A streaming slot (its rail streams the answer as it is written) on a broker that does not
    say it streams to Claude: _vision_without_images for the other reason. On a broker that
    streams, its switch is like any other. On one that does not, the slot, off, is disabled with
    the gateway's CLI_NO_STREAM beside it, and so is its model select. On (BROKER_CLI_ROLES, say),
    it can be switched OFF and still says why; one PUT, and then it cannot be switched back on."""
    shell = Shell(admin=True)
    shell.rails = copy.deepcopy(ADMIN_RAILS)
    shell_up(page, shell, {"recipe-book": rail("recipe-book", recipe_api("ok", admin=True))})
    page.wait_for_selector(".mw-trigger", timeout=10000)
    page.goto(ORIGIN + "/#/admin")
    page.wait_for_selector(".rail-slot-cli", timeout=10000)
    out: list[str] = []
    key = ("Study Desk", "Answers")
    sw, sel = _switches(page).get(key), _model_selects(page).get(key)
    if sw != (False, False, None, None):
        out.append(f"on a broker that streams, the streaming switch reads {sw!r}, "
                   f"expected clickable with no reason")
    if sel is None or sel[1] is not False:
        out.append(f"on a broker that streams, the streaming model select reads {sel!r}")
    broker_streams(shell.rails, False)
    page.reload()
    page.wait_for_selector(".rail-slot-cli", timeout=10000)
    sw, sel = _switches(page).get(key), _model_selects(page).get(key)
    if sw != (False, True, CLI_NO_STREAM, None):
        out.append(f"off, on a broker that does not stream, the streaming switch reads {sw!r}, "
                   f"expected disabled with the gateway's reason")
    if sel is None or sel[1] is not True:
        out.append(f"off, on a broker that does not stream, the streaming model select reads {sel!r}")
    _set_on(shell, "desk-answers", True)
    page.reload()
    page.wait_for_selector(".rail-slot-cli", timeout=10000)
    sw, sel = _switches(page).get(key), _model_selects(page).get(key)
    if sw != (True, False, CLI_NO_STREAM, None):
        out.append(f"on, on a broker that does not stream, the streaming switch reads {sw!r}, "
                   f"expected clickable (OFF is always allowed) with the reason")
    if sel is None or sel[1] is not False:
        out.append(f"on, the streaming model select reads {sel!r}, expected usable")
    before = len(shell.cli_puts)
    page.locator(".rail-slot", has=page.locator(".rail-slot-label", has_text="Answers")) \
        .locator("input[role=switch]").click()
    for _ in range(50):
        if (_switches(page).get(key) or (None,))[0] is False:
            break
        page.wait_for_timeout(100)
    if shell.cli_puts[before:] != [("desk-answers", {"enabled": False})]:
        out.append(f"switching the streaming slot off sent {shell.cli_puts[before:]!r}")
    after = _switches(page).get(key)
    if after != (False, True, CLI_NO_STREAM, None):
        out.append(f"after switching it off the streaming switch reads {after!r}, expected disabled")
    return out


def check_shell_admin_refusals(page) -> list[str]:
    """The gateway's new refusals reach the admin in its own words: Run now on a task the
    scheduler still holds (409), a username that is not printable ASCII (400), and the first
    named broker token on a box with no BROKER_AUTH_TOKEN (409)."""
    shell = Shell(admin=True, refuse={
        "POST admin/schedules/recipe-book/t1/run": (409, RUN_REFUSAL),
        "POST admin/users": (400, USER_REFUSAL),
        "POST admin/broker/tokens": (409, TOKEN_REFUSAL),
    })
    shell_up(page, shell, {"recipe-book": rail("recipe-book", recipe_api("ok", admin=True))})
    page.wait_for_selector(".mw-trigger", timeout=10000)
    page.goto(ORIGIN + "/#/admin")
    out = []

    def said(where: str, want: str) -> None:
        try:
            page.get_by_text(want, exact=True).wait_for(timeout=4000)
        except Exception:
            shown = page.locator(".error-line, .error-banner").all_inner_texts()
            out.append(f"{where}: the refusal was not shown in the gateway's words (shown: {shown!r})")

    page.get_by_role("tab", name="Schedule").click()
    page.locator(".sch-task button", has_text="Run now").click()
    said("Run now", RUN_REFUSAL)
    page.get_by_role("tab", name="Users").click()
    page.locator("input[name=new-user]").fill("Zo\u00eb")
    page.locator("input[name=new-user-password]").fill("pw-for-the-check")
    page.get_by_role("button", name="Create", exact=True).click()
    said("Add user", USER_REFUSAL)
    page.get_by_role("tab", name="Broker").click()
    page.get_by_label("Token label").fill("studio-desktop")
    page.get_by_role("button", name="Generate", exact=True).click()
    said("Generate token", TOKEN_REFUSAL)
    return out


WS_REFUSAL = "only a super-admin can change a workspace that contains a super-admin"


def check_shell_workspace_lock(page) -> list[str]:
    """A plain admin sees a room that holds a super-admin locked, and told why, and since
    2026-10-15 a room that holds a member beyond their reach (an app in the catalog they cannot
    grant) locked too, the note naming the member and the app; a name they may not add is greyed
    out with the app that puts it beyond reach. A room with neither stays editable, and a 403 that
    arrives anyway (the room changed since the load) is shown in the gateway's words. Both name
    boxes stop at 64."""
    shell = Shell(admin=True, users=[
        {"id": 1, "username": "owner-one", "is_admin": True, "is_superadmin": False,
         "apps": ["recipe-book"]},
        {"id": 2, "username": "root", "is_admin": True, "is_superadmin": True, "apps": []},
        {"id": 3, "username": "bob", "is_admin": False, "is_superadmin": False, "apps": []},
        {"id": 4, "username": "carol", "is_admin": False, "is_superadmin": False,
         "apps": ["terminal-fun"]},
    ], catalog=[{"id": "recipe-book", "label": "Recipe Book"},
                {"id": "terminal-fun", "label": "Terminal"}],
        grantable=["recipe-book"], workspaces={"workspaces": [
            {"id": 1, "name": "Leads", "members": ["owner-one", "root"]},
            {"id": 2, "name": "Third Grade", "members": ["bob", "owner-one"]},
            {"id": 3, "name": "Room 12", "members": ["carol", "owner-one"]},
        ], "manageable": ["bob", "owner-one"]}, refuse={"PATCH admin/workspaces/2": (403, WS_REFUSAL)})
    shell_up(page, shell, {"recipe-book": rail("recipe-book", recipe_api("ok", admin=True))})
    page.wait_for_selector(".mw-trigger", timeout=10000)
    page.goto(ORIGIN + "/#/admin")
    page.get_by_role("tab", name="Workspaces").click()
    page.wait_for_selector(".admin-table tbody tr", timeout=8000)
    out = []
    rows = page.locator(".admin-table tbody tr")
    state = page.evaluate("""() => [...document.querySelectorAll('.admin-table tbody tr')].map((tr) => ({
      name: tr.querySelector('input[type=text]').value,
      nameBox: tr.querySelector('input[type=text]').disabled,
      members: [...tr.querySelectorAll('.app-checks input')].map((i) => i.disabled),
      del: [...tr.querySelectorAll('button')].find((x) => x.textContent.trim() === 'delete').disabled,
      note: (tr.querySelector('.ws-locked') || {}).textContent || null,
      titles: Object.fromEntries([...tr.querySelectorAll('.app-checks label')].map((l) =>
        [l.textContent.trim(), l.title || null])),
      max: tr.querySelector('input[type=text]').maxLength }))""")
    leads, grade, room12 = state[0], state[1], state[2]
    if not (leads["nameBox"] and all(leads["members"]) and leads["del"]):
        out.append(f"the room holding a super-admin is not locked for a plain admin: {leads}")
    if leads["note"] != "Only a super-admin can change or delete a workspace that contains a super-admin.":
        out.append(f"the locked room does not say why: {leads['note']!r}")
    if grade["nameBox"] or grade["del"] or grade["note"]:
        out.append(f"a room with no super-admin or member beyond reach is locked too: {grade}")
    if not (room12["nameBox"] and all(room12["members"]) and room12["del"]):
        out.append(f"the room holding a member beyond reach is not locked for a plain admin: {room12}")
    if room12["note"] != ("Only a super-admin can change or delete this workspace: carol holds apps "
                          "you cannot grant (terminal-fun)."):
        out.append(f"the room beyond reach does not say why: {room12['note']!r}")
    if grade["titles"].get("carol") != ("Holds apps you cannot grant (terminal-fun): only a "
                                        "super-admin can place this user in a workspace."):
        out.append(f"a name beyond reach is not told why it is greyed out: {grade['titles'].get('carol')!r}")
    if leads["max"] != 64 or page.locator("input[name=new-workspace]").evaluate("(i) => i.maxLength") != 64:
        out.append("a workspace name box takes more than 64 characters")
    rows.nth(1).locator("input[type=text]").fill("Third Grade Team")
    rows.nth(1).get_by_role("button", name="rename").click()
    try:
        page.get_by_text(WS_REFUSAL, exact=True).wait_for(timeout=4000)
    except Exception:
        out.append(f"a 403 on rename was not shown in the gateway's words: {page.locator('.error-line').all_inner_texts()!r}")
    return out


def check_shell_run_now(page) -> list[str]:
    """Run now says it is running and cannot be fired twice while it is."""
    page.add_init_script("""(() => { const f = window.fetch; window.fetch = (u, i) => String(u).endsWith('/run')
      ? new Promise((r) => setTimeout(() => r(f(u, i)), 1500)) : f(u, i) })()""")
    shell = Shell(admin=True)
    shell_up(page, shell, {"recipe-book": rail("recipe-book", recipe_api("ok", admin=True))})
    page.wait_for_selector(".mw-trigger", timeout=10000)
    page.goto(ORIGIN + "/#/admin")
    page.get_by_role("tab", name="Schedule").click()
    btn = page.locator(".sch-task button", has_text="Run now")
    btn.click()
    page.wait_for_timeout(150)
    out = []
    running = page.locator(".sch-task button", has_text="Running")
    if not running.count():
        out.append("no 'Running…' while the run was in flight")
    elif not running.first.is_disabled():
        out.append("the button stayed enabled while the run was in flight")
    page.locator(".sch-task button").first.dispatch_event("click")
    page.wait_for_timeout(2500)
    runs = sum(1 for c in shell.calls if c.endswith("/run"))
    if runs != 1:
        out.append(f"{runs} run requests from two clicks during one run")
    return out


def check_shell_unload_error(page) -> list[str]:
    """A failed Unload says why, in the popover the admin clicked in."""
    shell = Shell(admin=True)
    shell_up(page, shell, {"recipe-book": rail("recipe-book", recipe_api("ok", admin=True))})
    page.wait_for_selector(".mw-trigger", timeout=10000)
    shell.unload = 500
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.click(".mw-trigger")
    page.click(".mw-pop .mw-x")
    try:
        page.wait_for_selector(".mw-pop .mw-err", timeout=5000)
    except Exception:
        return ["no error shown after the unload failed"] + [f"page error: {e}" for e in errors]
    text = page.inner_text(".mw-pop .mw-err")
    return [] if "refused" in text else [f"the error shown does not carry the reason: {text!r}"]


# --- the top bar over rail content ---------------------------------------------------------------
#
# The bar's popovers carry their own z-index for the whole page (.tm-pop 80, .mw-pop 60). While
# .ptopbar was a stacking context (z-index: 0, for its gradient wash) they ranked only inside it,
# and positioned rail content later in the DOM painted over the open theme menu and took its
# clicks: Terminal Fun's tiles and Recipe Book's cards are `position: relative`.

def terminal_fun_api(n: int = 30) -> Api:
    cats = [{"id": "games", "label": "Games"}, {"id": "toys", "label": "Toys"}]
    items = [{"id": f"t{i}", "label": f"Toy {i}", "icon": "x", "category": cats[i % 2]["id"],
              "watch": False, "info": "i", "tunable": False, "saveable": False, "web": False}
             for i in range(n)]

    def api(method, path, q, body):
        if path == "/api/catalog":
            return {"categories": cats, "items": items}
        if path == "/api/capabilities":
            return {"broker": "ok", "models": []}
        if path == "/api/saves":
            return {"saves": []}
        raise NoFixture(path)

    return api


# Every point of a popover's on-screen box, every 10 px, and the centre of each of its buttons:
# what document.elementFromPoint finds there, which is what a click lands on. With `hide`, the
# popover is hidden first (a hidden element is not hit) and the result counts the points that land
# on POSITIONED rail content: the witness that there is something under it to lose to.
HIT_POPOVER = """([sel, hide]) => {
  const pop = document.querySelector(sel)
  if (!pop) return null
  const r = pop.getBoundingClientRect()
  const name = (el) => el ? (el.tagName + (el.className && typeof el.className === 'string'
    ? '.' + el.className.trim().split(/\\s+/).join('.') : '')) : 'nothing'
  const pts = []
  for (let y = Math.max(r.top, 0) + 3; y < Math.min(r.bottom, innerHeight) - 3; y += 10)
    for (let x = Math.max(r.left, 0) + 3; x < Math.min(r.right, innerWidth) - 3; x += 10) pts.push([x, y])
  if (hide) {
    pop.style.visibility = 'hidden'
    let positioned = 0
    const content = document.querySelector('.pcontent')
    for (const [x, y] of pts) {
      for (let el = document.elementFromPoint(x, y); el && el !== content && content.contains(el); el = el.parentElement)
        if (getComputedStyle(el).position !== 'static') { positioned++; break }
    }
    pop.style.visibility = ''
    return { points: pts.length, positioned }
  }
  const out = []
  for (const [x, y] of pts) {
    const el = document.elementFromPoint(x, y)
    if (!pop.contains(el)) out.push(`(${Math.round(x)}, ${Math.round(y)}) ${name(el)}`)
  }
  const buttons = []
  const all = [...pop.querySelectorAll('button')]
  for (const b of all) {
    const br = b.getBoundingClientRect()
    const el = document.elementFromPoint(br.left + br.width / 2, br.top + br.height / 2)
    if (!b.contains(el)) buttons.push(`${b.textContent.trim().slice(0, 30) || b.title} -> ${name(el)}`)
  }
  const last = all[all.length - 1].getBoundingClientRect()
  return { points: pts.length, out, buttons, n: all.length, bottom: r.bottom, lastBottom: last.bottom,
           scroll: pop.scrollHeight - pop.clientHeight }
}"""


def _covered(page, sel: str, where: str) -> list[str]:
    got = page.evaluate(HIT_POPOVER, [sel, False])
    if got is None:
        return [f"{where}: {sel} is not open"]
    out = []
    if got["buttons"]:
        out.append(f"{where}: {len(got['buttons'])} of {got['n']} {sel} buttons take no click, e.g. "
                   + "; ".join(got["buttons"][:4]))
    if got["out"]:
        out.append(f"{where}: rail content paints over {len(got['out'])} of {got['points']} points "
                   f"of {sel}, e.g. " + "; ".join(got["out"][:3]))
    return out


def check_shell_topbar_layers(page) -> list[str]:
    """The theme menu and the model widget's popover are above rail content on two rails, all
    of the theme menu fits a 900 px window, and a rail modal still covers the whole bar."""
    page.set_viewport_size({"width": 1440, "height": 900})
    shell = Shell(admin=True)
    rails = {"terminal-fun": rail("terminal-fun", terminal_fun_api()),
             "recipe-book": rail("recipe-book", recipe_api("ok", admin=True, n=40))}
    shell_up(page, shell, rails)
    page.wait_for_selector(".mw-trigger", timeout=10000)
    # The roaming desk pet sits above everything on purpose (z-index 2000) and wanders under the
    # menu at random, so it is taken out of the hit test rather than left to make the check flaky.
    page.add_style_tag(content=PET + " { visibility: hidden !important; }")
    out: list[str] = []
    for name, ready in (("terminal-fun", ".ft .tile"), ("recipe-book", ".rb-card")):
        page.goto(ORIGIN + "/#/" + name)
        page.wait_for_selector(ready, timeout=10000)
        page.wait_for_timeout(300)
        page.click(".theme-btn")
        page.wait_for_selector(".tm-pop", timeout=3000)
        under = page.evaluate(HIT_POPOVER, [".tm-pop", True])
        if under["positioned"] < 20:
            out.append(f"{name}: only {under['positioned']} of {under['points']} points under the theme "
                       "menu are positioned rail content, so the layering was not tested")
        out += _covered(page, ".tm-pop", name)
        if name == "recipe-book":
            # The whole menu, admin action included, fits a 900 px window with no inner scroll.
            got = page.evaluate(HIT_POPOVER, [".tm-pop", False])
            if got["scroll"] > 1 or got["lastBottom"] > got["bottom"] + 0.5:
                out.append(f"the theme menu scrolls {got['scroll']} px inside itself and its last action "
                           f"ends at {got['lastBottom']:.0f} px, past its own bottom ({got['bottom']:.0f})")
            # Above the highest non-modal layer a rail uses today (smb's floating Mobile button,
            # finance's dropdown, both z-index 40), not only above z-index auto.
            page.evaluate("""() => { const d = document.createElement('div'); d.id = '__z40';
              Object.assign(d.style, { position: 'fixed', inset: '0', zIndex: '40' });
              document.querySelector('.pcontent').appendChild(d) }""")
            out += [f"{p} (over a z-index 40 rail layer)" for p in _covered(page, ".tm-pop", name)]
            page.evaluate("document.getElementById('__z40').remove()")
        page.click(".theme-btn")
        page.wait_for_selector(".tm-pop", state="detached", timeout=3000)
        page.click(".mw-trigger")
        page.wait_for_selector(".mw-pop", timeout=3000)
        out += _covered(page, ".mw-pop", name)
        page.keyboard.press("Escape")
        page.wait_for_selector(".mw-pop", state="detached", timeout=3000)
    # A rail modal still covers the bar, the lowest one (recipe-book's and job-aid's, z-index 50)
    # included, and one whose rail wrapper is its own stacking context too. Lifting the bar with a
    # z-index of its own would fix the menu and break this.
    for label, html in (("a z-index 50 modal", "<div class='__modal'></div>"),
                        ("a modal inside a rail wrapper that is a stacking context",
                         "<div style='isolation:isolate'><div class='__modal'></div></div>")):
        hit = page.evaluate("""(html) => {
          const w = document.createElement('div'); w.id = '__wrap'; w.innerHTML = html
          Object.assign(w.querySelector('.__modal').style, { position: 'fixed', inset: '0', zIndex: '50' })
          document.querySelector('.pcontent').appendChild(w)
          const b = document.querySelector('.theme-btn').getBoundingClientRect()
          const el = document.elementFromPoint(b.left + b.width / 2, b.top + b.height / 2)
          const ok = !!el && el.classList.contains('__modal')
          w.remove()
          return ok ? null : (el ? el.tagName + '.' + el.className : 'nothing')
        }""", html)
        if hit:
            out.append(f"{label} does not cover the top bar: the theme button's centre hits {hit}")
    return out + [f"unanswered: {u}" for r in rails.values() for u in r.unanswered]


BROKER_403 = "only a super-admin may manage broker tokens"
BROKER_502 = "broker unreachable: connection refused"


def check_shell_broker_refused(page) -> list[str]:
    """A token list the gateway refuses (a plain admin's 403) or cannot fetch (502) is shown as an
    error, with no token form and no claim about the broker; a list that loads still shows both."""
    shell = Shell(admin=True, refuse={"GET admin/broker/tokens": (403, BROKER_403)})
    shell_up(page, shell, {"recipe-book": rail("recipe-book", recipe_api("ok", admin=True))})
    page.wait_for_selector(".mw-trigger", timeout=10000)
    page.goto(ORIGIN + "/#/admin")
    out: list[str] = []

    def state() -> dict:
        return page.evaluate("""() => {
          const probe = document.createElement('span'); probe.style.color = 'var(--critical-text)'
          document.body.appendChild(probe); const red = getComputedStyle(probe).color; probe.remove()
          const pane = document.querySelector('.admin-pane')
          const errs = [...document.querySelectorAll('.admin-pane .error-line')]
            .map((e) => [e.textContent.trim(), getComputedStyle(e).color === red])
          return { text: pane ? pane.innerText : '', errs,
                   form: !!document.querySelector('input[aria-label="Token label"]'),
                   generate: [...document.querySelectorAll('button')].some((b) => b.textContent.trim() === 'Generate') }
        }""")

    for status, words in ((403, BROKER_403), (502, BROKER_502)):
        shell.refuse["GET admin/broker/tokens"] = (status, words)
        page.get_by_role("tab", name="Users").click()
        page.get_by_role("tab", name="Broker").click()
        try:
            page.get_by_text(words, exact=True).wait_for(timeout=4000)
        except Exception:
            out.append(f"{status}: the gateway's words were not shown")
            continue
        s = state()
        if [words, True] not in s["errs"]:
            out.append(f"{status}: the refusal is not an error line in the error colour: {s['errs']!r}")
        if s["form"] or s["generate"]:
            out.append(f"{status}: the New token form is still offered after the list was refused")
        for claim in ("No named tokens", "currently OPEN", "BROKER_AUTH_TOKEN is in use"):
            if claim in s["text"]:
                out.append(f"{status}: the page still says {claim!r} with no list behind it")
    # The witness: a list that loads (no tokens, no shared token) still renders the form and the
    # sentence, so the absences above are the failed read's doing.
    del shell.refuse["GET admin/broker/tokens"]
    page.get_by_role("tab", name="Users").click()
    page.get_by_role("tab", name="Broker").click()
    try:
        page.get_by_text("No named tokens.", exact=False).wait_for(timeout=4000)
    except Exception:
        out.append("a list that loads no longer shows the Tokens card")
        return out
    s = state()
    if not (s["form"] and s["generate"] and "currently OPEN" in s["text"]) or s["errs"]:
        out.append(f"a list that loads lost its form or its sentence, or shows an error: {s}")
    return out


def check_shell_schedule_inputs(page) -> list[str]:
    """The Schedule tab's "at" time box is styled like the number and time-zone boxes beside it."""
    shell = Shell(admin=True)
    shell_up(page, shell, {"recipe-book": rail("recipe-book", recipe_api("ok", admin=True))})
    page.wait_for_selector(".mw-trigger", timeout=10000)
    page.goto(ORIGIN + "/#/admin")
    page.get_by_role("tab", name="Schedule").click()
    page.wait_for_selector(".sch-row input[type=time]", timeout=8000)
    props = ["border-top-left-radius", "background-color", "font-family", "font-size", "border-top-style",
             "border-top-color", "padding-left", "color"]
    got = page.evaluate("""(props) => Object.fromEntries(['time', 'number'].map((t) => {
      const cs = getComputedStyle(document.querySelector(`.sch-row input[type=${t}]`))
      return [t, Object.fromEntries(props.map((p) => [p, cs.getPropertyValue(p)]))] }))""", props)
    return [f"the time box's {p} is {got['time'][p]!r}, the number box's {got['number'][p]!r}"
            for p in props if got["time"][p] != got["number"][p]]


PET = '[aria-label="Animated desktop pet; press Enter for information"]'
BUBBLE = '[role=status][style*="z-index: 2001"]'


def check_shell_deskpet_bubble(page) -> list[str]:
    """The pet's speech bubble stays inside the window, at its own width, with the pet dragged to
    the right edge, the left edge and the top."""
    page.set_viewport_size({"width": 1440, "height": 900})
    shell = Shell()
    shell_up(page, shell, {"recipe-book": rail("recipe-book", recipe_api("ok", admin=False))})
    page.wait_for_selector(PET, timeout=15000)
    # The welcome line, 10 s after the pet loads; it stays up for 3.5 to 9 s.
    page.wait_for_selector(BUBBLE, timeout=15000)
    vw, vh = 1440, 900
    out: list[str] = []

    def drag_to(x: int, y: int) -> dict | None:
        box = page.locator(PET).first.bounding_box()     # the pet itself; a child it spawns comes later
        # Grab the part of the sprite that is on screen: after the drag to the right edge, half of
        # it is past the window, and a press there would land on nothing and move nothing.
        x0, x1 = max(box["x"], 0), min(box["x"] + box["width"], vw)
        y0, y1 = max(box["y"], 0), min(box["y"] + box["height"], vh)
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        page.mouse.move(cx, cy)
        page.mouse.down()
        page.mouse.move(cx + 3, cy + 3)       # the engine starts a drag on a held move over the pet
        page.mouse.move(x, y, steps=6)        # then follows the pointer anywhere on the page
        page.wait_for_timeout(250)
        got = page.evaluate("""([b, p]) => { const e = document.querySelector(b); if (!e) return null
          const r = e.getBoundingClientRect(), q = document.querySelector(p).getBoundingClientRect()
          return { l: r.left, r: r.right, t: r.top, b: r.bottom, w: r.width, petTop: q.top, petBottom: q.bottom,
                   petX: q.left + q.width / 2, petY: q.top + q.height / 2 } }""",
                            [BUBBLE, PET])
        page.mouse.up()
        # The engine centres the sprite on the pointer while it drags: the witness that the pet
        # really is at the edge being tested, not still where the last drag left it.
        if got and (abs(got["petX"] - x) > 2 or abs(got["petY"] - y) > 2):
            out.append(f"the pet did not follow the drag to ({x}, {y}); it is at ({got['petX']:.0f}, {got['petY']:.0f})")
        return got

    mid = drag_to(700, 450)
    if mid is None:
        return ["the bubble was gone before the pet could be moved"]
    for where, (x, y) in (("right edge", (vw - 2, 450)), ("left edge", (2, 450)), ("top", (700, 6))):
        g = drag_to(x, y)
        if g is None:
            out.append(f"{where}: the bubble was gone before it could be measured")
            continue
        if g["l"] < 0 or g["r"] > vw or g["t"] < 0 or g["b"] > vh:
            out.append(f"{where}: the bubble ({g['l']:.0f}, {g['t']:.0f})-({g['r']:.0f}, {g['b']:.0f}) "
                       f"runs outside the {vw}x{vh} window")
        if abs(g["w"] - mid["w"]) > 1:
            out.append(f"{where}: the bubble is {g['w']:.0f} px wide, {mid['w']:.0f} px mid-window "
                       "(squeezed by its position)")
        if g["b"] > g["petTop"] + 1 and g["t"] < g["petBottom"] - 1:
            out.append(f"{where}: the bubble covers the pet")
    return out


def _pool(name: str, cat: str, cls: str | None, blurb: str, in_use: bool = False, loaded: bool = False,
          enabled: bool = True, roles: list[str] | None = None, size: str | None = "8B") -> dict:
    return {"name": name, "class": cls, "parameter_size": size, "size": None, "vision": cat == "vision",
            "loaded": loaded, "in_use": in_use, "roles": roles or [], "enabled": enabled,
            "category": cat, "blurb": blurb, "generated": False}


# Names, blurbs and statuses of very different lengths per section: what made each table size
# its columns differently.
MODEL_POOL = {
    "categories": [{"id": "chat", "label": "Chat & instruction-following", "order": 1},
                   {"id": "vision", "label": "Vision (multimodal)", "order": 2},
                   {"id": "embed", "label": "Embedding (retrieval)", "order": 3}],
    "models": [
        _pool("mistral-small3.2:24b-instruct-2506-q4_K_M", "chat", "heavy",
              "Long-form drafting and translation; the default writer for most rails, strong at "
              "following a structured prompt to the letter.",
              in_use=True, loaded=True, roles=["iep", "edu", "openmaic", "finance-chat"], size="24B"),
        _pool("qwen3:8b", "chat", "heavy", "Fast general chat.", roles=[]),
        _pool("llama3.1:70b", "chat", None, "", enabled=False, size="70B"),
        _pool("qwen2.5vl:7b", "vision", "heavy", "Reads photos.", size="7B"),
        _pool("moondream:latest", "vision", "light", "Tiny captioner.", size="1.8B"),
        _pool("qwen3-embedding:0.6b", "embed", "embed", "Retrieval vectors.", in_use=True,
              roles=["embed"], size="0.6B"),
        _pool("nomic-embed-text", "embed", "embed", "", size=None),
        # A name longer than the Model column is wide: it wraps at its hyphens and slashes, and
        # the columns after it stay put.
        _pool("hf.co/someone/Very-Long-Retrieval-Model-Name-Embedding-Instruct-GGUF:Q8_0-extra-tag",
              "embed", "embed", "", size=None),
    ],
}


def check_shell_models_columns(page) -> list[str]:
    """Admin > Models: every category's table puts its columns at the same x, and each row's
    Disable and delete sit side by side."""
    page.set_viewport_size({"width": 1440, "height": 900})
    shell = Shell(admin=True)
    shell_up(page, shell, {"recipe-book": rail("recipe-book", recipe_api("ok", admin=True))})
    page.wait_for_selector(".mw-trigger", timeout=10000)
    page.route("**/api/platform/admin/models", lambda route: route.fulfill(
        status=200, content_type="application/json", body=json.dumps(MODEL_POOL)))
    page.goto(ORIGIN + "/#/admin")
    page.get_by_role("tab", name="Models").click()
    page.wait_for_selector(".admin-table tbody tr", timeout=8000)
    got = page.evaluate("""() => [...document.querySelectorAll('.admin-table')].map((t) => ({
      title: t.closest('.card').querySelector('h4').textContent.trim(),
      cols: [...t.querySelectorAll('thead th')].map((th) => Math.round(th.getBoundingClientRect().left)),
      rows: [...t.querySelectorAll('tbody tr')].map((tr) => {
        const b = [...tr.querySelectorAll('td:last-child button')].map((x) => Math.round(x.getBoundingClientRect().top))
        return { name: tr.querySelector('code').textContent, tops: b } }) }))""")
    out: list[str] = []
    if len(got) != 3:
        return [f"expected three category tables, saw {len(got)}: {[g['title'] for g in got]}"]
    first = got[0]["cols"]
    for g in got[1:]:
        if any(abs(a - b) > 1 for a, b in zip(g["cols"], first)) or len(g["cols"]) != len(first):
            out.append(f"{g['title']!r} puts its columns at {g['cols']}, {got[0]['title']!r} at {first}")
    for g in got:
        for r in g["rows"]:
            if len(r["tops"]) != 2 or abs(r["tops"][0] - r["tops"][1]) > 1:
                out.append(f"{g['title']!r} / {r['name']}: the row's buttons are stacked (tops {r['tops']})")
    return out


#: This module's checks: name -> function. railcheck.py's RAILS names each one, fixes its
#: place in --list and says which frontends it mounts.
CHECKS = {
    "shell-session-lost": check_shell_session_lost,
    "shell-hidden": check_shell_hidden,
    "shell-rail-rerender": check_shell_rail_rerender,
    "shell-unload-error": check_shell_unload_error,
    "shell-admin-rails": check_shell_admin_rails,
    "shell-admin-rails-cli": check_shell_admin_rails_cli,
    "shell-admin-rails-stream": check_shell_admin_rails_stream,
    "shell-admin-refusals": check_shell_admin_refusals,
    "shell-workspace-lock": check_shell_workspace_lock,
    "shell-run-now": check_shell_run_now,
    "shell-broker-refused": check_shell_broker_refused,
    "shell-schedule-inputs": check_shell_schedule_inputs,
    "shell-deskpet-bubble": check_shell_deskpet_bubble,
    "shell-models-columns": check_shell_models_columns,
    "shell-topbar-layers": check_shell_topbar_layers,
}
