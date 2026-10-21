"""co-worker's checks: switching the Brief's lens mid-synthesis leaves the Re-synthesize button
usable, in the Co-Work Prompts panel an open card grows alone and the example prompts say they
are templates, and the client pulse's read-aloud chip (web-core's SpeakButton) stops when Stop is
pressed while it is still fetching.
"""
from __future__ import annotations

import json
import re

from .harness import NoFixture, mount, rail, serve


def check_cw_brief_lens_switch(page) -> list[str]:
    """Switching the Brief's lens mid-synthesis no longer strands the Re-synthesize button: the
    status poll was cleared by the lens effect's cleanup with `busy` still true."""
    state = {"running": False}
    brief = {"exists": True, "attention": [], "age_hours": 1, "passes": {
        "email": {"items_considered": 1, "items_eligible": 1, "items_read": 1,
                  "items_filtered": 0, "attention": 0, "truncated": 0}}}

    def api(method, path, q, body):
        if path == "/api/models":
            return {"broker": "ok", "models": [], "items": 0}
        if path in ("/api/inbox", "/api/archive"):
            return {"items": [], "skipped": [], "periods": []}
        if path == "/api/brief":
            return brief
        if path == "/api/brief/status":
            return {"running": state["running"], "last_error": None}
        if path == "/api/brief/refresh" and method == "POST":
            state["running"] = True
            return {"started": True, "running": True}
        raise NoFixture(path)

    r = rail("co-worker", api)
    serve(page, r)
    mount(page, r)
    page.get_by_role("button", name="⟳ Re-synthesize all").click()
    page.get_by_role("button", name="Synthesizing…").wait_for(timeout=5000)
    page.locator(".cw-brief-lens .cw-tab", has_text="Email").click()
    page.wait_for_timeout(500)
    state["running"] = False                   # the run lands while the Email lens is shown
    btn = page.get_by_role("button", name="⟳ Re-synthesize Email")
    try:
        btn.wait_for(timeout=8000)
    except Exception:
        return ["the Re-synthesize button never came back after the run finished"]
    out = [] if btn.is_enabled() else ["the Re-synthesize button is still disabled"]
    return out + [f"unanswered: {u}" for u in r.unanswered]


def quiet_brief_api(method, path, q, body):
    """A brief with nothing in it: enough for the Brief tab and its Co-Work Prompts panel."""
    if path == "/api/models":
        return {"broker": "ok", "models": [], "items": 0}
    if path in ("/api/inbox", "/api/archive"):
        return {"items": [], "skipped": [], "periods": []}
    if path == "/api/brief":
        return {"exists": True, "attention": [], "age_hours": 1, "passes": {}}
    if path == "/api/brief/status":
        return {"running": False, "last_error": None}
    raise NoFixture(path)


def open_prompts(page):
    page.set_viewport_size({"width": 1440, "height": 900})
    r = rail("co-worker", quiet_brief_api)
    serve(page, r)
    mount(page, r)
    page.get_by_role("button", name="📋 Co-Work Prompts").click()
    page.wait_for_selector(".cw-prompt-job", timeout=8000)
    return r


def check_cw_prompt_rows(page) -> list[str]:
    """Expanding one prompt card stretched every card in its grid row to the open card's
    height. The open card grows; the rest of its row keeps the height it had."""
    r = open_prompts(page)
    heights = "() => [...document.querySelectorAll('.cw-prompt-job')].map((e) => { const q = e.getBoundingClientRect(); return [Math.round(q.top), Math.round(q.height)]; })"
    before = page.evaluate(heights)
    page.locator(".cw-prompt-hd").first.click()
    page.wait_for_selector(".cw-prompt-body", timeout=4000)
    after = page.evaluate(heights)
    out = []
    row = [i for i, (top, _) in enumerate(before) if top == before[0][0]]
    if len(row) < 2:
        out.append(f"only {len(row)} card in the first row at 1440px, so a stretch could not show")
    if after[0][1] <= before[0][1]:
        out.append("the opened card did not grow, so the panel never opened")
    grown = [f"card {i + 1}: {before[i][1]} -> {after[i][1]}px" for i in row[1:] if after[i][1] > before[i][1] + 1]
    if grown:
        out.append(f"opening the first card stretched its row: {grown}")
    return out + [f"unanswered: {u}" for u in r.unanswered]


def check_cw_prompt_templates(page) -> list[str]:
    """The prompt text shows '[YOUR NAME]', '[YOUR ROLE]', '[YOUR COMPANY]' and nothing said
    they were for the reader to fill in. The panel says the prompts are templates, an open card
    names exactly the bracketed fields its prompt holds, and the button no longer calls them
    the exact prompts the jobs run."""
    r = open_prompts(page)
    out = []
    note = page.locator(".cw-prompts-note")
    text = note.inner_text() if note.count() else ""
    if "template" not in text.lower() or "bracketed" not in text.lower():
        out.append(f"the panel does not say these are templates to fill in: {text!r}")
    title = page.get_by_role("button", name="📋 Co-Work Prompts").get_attribute("title") or ""
    if "exact" in title.lower() or "template" not in title.lower():
        out.append(f"the Co-Work Prompts button is titled {title!r}")
    page.locator(".cw-prompt-hd").first.click()
    page.wait_for_selector(".cw-prompt-body", timeout=4000)
    # The prompt as rendered, without the fill-in note itself.
    body = page.eval_on_selector(".cw-prompt-body", "(b) => [...b.children].filter((c) => "
                                 "!c.classList.contains('cw-prompt-fill')).map((c) => c.innerText).join(' ')")
    held = sorted(set(re.findall(r"\[[A-Z][A-Z ]*\]", body)))
    named = sorted(page.eval_on_selector_all(".cw-prompt-fill code", "els => els.map((e) => e.textContent)"))
    if not held:
        out.append("the open prompt holds no bracketed field, so the fill-in note was not exercised")
    elif named != held:
        out.append(f"the open card names {named} for filling in; its prompt holds {held}")
    return out + [f"unanswered: {u}" for u in r.unanswered]


# Every playback the page starts, recorded instead of heard: a wav (web-core's playWav makes an
# Audio with its data) and an on-device read (its fallback, speechSynthesis.speak). Installed
# before the rail loads, so the bundle's own voice.ts finds these. An Audio made with no source
# is not a playback: VoiceControls.tsx makes one when it loads, to ask whether setSinkId exists.
SPEAK_STUBS = """
window.__played = [];
window.__spoken = [];
window.Audio = class {
  constructor(src) { this.src = src; this.paused = true; if (src) window.__played.push(src); }
  async play() { this.paused = false; }
  pause() { this.paused = true; }
};
const synth = { speak(u) { window.__spoken.push(u.text); }, cancel() {}, getVoices() { return []; } };
try { Object.defineProperty(window, 'speechSynthesis', { configurable: true, value: synth }); }
catch (e) { window.SpeechSynthesis.prototype.speak = synth.speak; }
"""

PULSE = "The client asked twice about the renewal date."
WAV = {"audio_b64": "UklGRgAAAABXQVZF", "sample_rate": 24000, "voice": "af_heart", "lang": "en"}


def check_cw_speak_stop_loading(page) -> list[str]:
    """Stop pressed while the read-aloud chip is still fetching its audio stops it, request and
    all. The chip went idle and returned, and the reply still in flight then played anyway
    (web/src/SpeakButton.tsx: nothing told the pending read it had been stopped); then the reply
    was dropped, but its request ran on. So: Stop aborts the request (the page reports it
    failed), a reply that lands after Stop all the same plays nothing and the chip stays on Read
    aloud, and a failed fetch after Stop does not fall back to the on-device voice either; an
    unmount mid-fetch aborts the request too. A read nobody stopped still plays, which is the
    witness that the stubs see a playback at all."""
    def api(method, path, q, body):
        if path == "/api/brief":
            return {"exists": True, "attention": [], "age_hours": 1, "passes": {},
                    "client_pulse": PULSE}
        return quiet_brief_api(method, path, q, body)

    held: list = []
    aborted: list = []
    page.add_init_script(SPEAK_STUBS)
    r = rail("co-worker", api)
    serve(page, r)
    # Registered after serve()'s catch-all, so it is matched first: the audio request is held
    # until the check answers it, or the page aborts it.
    page.route("**/api/platform/tts_light", lambda route: held.append(route))
    page.on("requestfailed",
            lambda req: aborted.append(req.url) if req.url.endswith("/api/platform/tts_light") else None)
    mount(page, r)
    chip = page.locator(".cw-pulse .vc-speak")
    chip.wait_for(timeout=8000)

    def label() -> str | None:
        return chip.get_attribute("aria-label")

    def played() -> tuple[int, int]:
        return tuple(page.evaluate("[window.__played.length, window.__spoken.length]"))

    def ask() -> str | None:
        """Click Read aloud and wait for the chip's audio request; why it did not ask, or None."""
        n = len(held)
        chip.click()
        for _ in range(50):
            if len(held) > n:
                break
            page.wait_for_timeout(100)
        if len(held) == n:
            return "the chip never asked for audio"
        if label() != "Stop reading":
            return f"while it fetches the chip reads {label()!r}, not 'Stop reading'"
        return None

    def ends(n: int) -> bool:
        """Whether the page aborts an audio request beyond the first n, within 2 s."""
        for _ in range(20):
            if len(aborted) > n:
                return True
            page.wait_for_timeout(100)
        return False

    out: list[str] = []
    for case, answer in (
            ("a reply that lands after Stop",
             lambda route: route.fulfill(status=200, content_type="application/json",
                                         body=json.dumps(WAV))),
            ("a failed fetch after Stop",
             lambda route: route.fulfill(status=503, content_type="application/json",
                                         body='{"detail":"no media worker"}'))):
        if label() != "Read aloud":                     # a case before left it speaking
            chip.click()
            page.evaluate("window.__played.length = 0; window.__spoken.length = 0")
        why = ask()
        if why:
            return out + [f"{case}: {why}"]
        n = len(aborted)
        chip.click()                                    # Stop, the reply still in flight
        if label() != "Read aloud":
            out.append(f"{case}: Stop left the chip reading {label()!r}")
        if not ends(n):
            out.append(f"{case}: Stop left the audio request running")
        try:
            answer(held[-1])        # lands only on a request Stop left running
        except Exception:
            pass                    # the page aborted it: nothing is waiting for an answer
        page.wait_for_timeout(600)
        if played() != (0, 0):
            out.append(f"{case}: it played anyway (wav, on-device) = {played()}")
        if label() != "Read aloud":
            out.append(f"{case}: the chip went back to {label()!r}")
        page.evaluate("window.__played.length = 0; window.__spoken.length = 0")
    # The witness: an unstopped read plays its wav and the chip offers Stop.
    if label() != "Read aloud":
        chip.click()
        page.evaluate("window.__played.length = 0; window.__spoken.length = 0")
    why = ask()
    if why:
        return out + [f"an unstopped read: {why}"]
    held[-1].fulfill(status=200, content_type="application/json", body=json.dumps(WAV))
    for _ in range(30):
        if played() != (0, 0):
            break
        page.wait_for_timeout(100)
    if played() != (1, 0):
        out.append(f"an unstopped read played (wav, on-device) = {played()}, expected (1, 0): "
                   "the stubs see no playback, so the cases above prove nothing")
    if label() != "Stop reading":
        out.append(f"an unstopped read leaves the chip reading {label()!r}")
    # An unmount mid-fetch ends the request as well.
    if label() != "Read aloud":
        chip.click()
    why = ask()
    if why:
        return out + [f"an unmount mid-fetch: {why}"]
    n = len(aborted)
    page.evaluate("window.__unmount()")
    if not ends(n):
        out.append("an unmount mid-fetch left the audio request running")
    try:
        # Handled either way, so the page closes on no route still waiting for the check.
        held[-1].fulfill(status=200, content_type="application/json", body=json.dumps(WAV))
    except Exception:
        pass
    return out + [f"unanswered: {u}" for u in r.unanswered]


#: This module's checks: name -> function. railcheck.py's RAILS names each one, fixes its
#: place in --list and says which frontends it mounts.
CHECKS = {
    "cw-brief-lens-switch": check_cw_brief_lens_switch,
    "cw-prompt-rows": check_cw_prompt_rows,
    "cw-prompt-templates": check_cw_prompt_templates,
    "cw-speak-stop-loading": check_cw_speak_stop_loading,
}
