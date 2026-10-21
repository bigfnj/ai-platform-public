"""smb-partner-enablement's checks: the Scenario Builder's "Why this matters" aside, the mobile
preview's drawn phone hardware over the app, the header's icon-only device pickers in light
palettes, and where their menus open.
"""
from __future__ import annotations

import mimetypes
from urllib.parse import urlsplit

from .harness import NoFixture, Rail, each_theme, frontend_dir, glyph_contrast, mount, rail, serve

SCENARIOS = {"scenarios": [{
    "id": "retail-chain", "icon": "🛍️", "title": "Retail Chain", "fit": "Business Premium",
    "situation": "Multi-location retailer with frontline staff.",
    "stages": [{"key": "scenario_card", "label": "Scenario card"}],
    "tabs": [{"key": "scenario_card", "label": "Scenario card"}],
    "questions": [{"id": "size", "prompt": "How many employees?",
                   "why": "Seat count decides the licensing path and which offers apply.",
                   "options": ["Fewer than 25", "25-100", "Not sure yet"]}]}], "stages": []}
CAPS = {"broker": "ok", "models": [], "user": "owner-one", "is_admin": False,
        "voice": {"configured": "browser", "effective": "browser", "broker_media": False,
                  "stt": "browser", "note": ""},
        "corpus": {"chunks": 3, "dims": 8, "collections": 2}}


def smb_api(method, path, q, body):
    if path == "/api/capabilities":
        return CAPS
    if path == "/api/scenarios":
        return SCENARIOS
    raise NoFixture(path)


def smb_up(page) -> Rail:
    r = rail("smb-partner-enablement", smb_api)
    serve(page, r)
    mount(page, r)
    page.get_by_role("tab", name="📋 Scenario Builder").wait_for(timeout=8000)
    return r


def check_smb_why_aside(page) -> list[str]:
    """The card's paragraph rule outranked the .why rule, so 'Why this matters' sat flush
    against the first answer at body size. It keeps its gap above the answers and its smaller
    aside size."""
    r = smb_up(page)
    page.get_by_role("tab", name="📋 Scenario Builder").click()
    page.locator(".card.scenario").first.click()
    page.wait_for_selector(".why", timeout=8000)
    got = page.evaluate("""() => {
      const why = document.querySelector('.why'), opt = document.querySelector('.opts .opt');
      return { gap: opt.getBoundingClientRect().top - why.getBoundingClientRect().bottom,
               size: parseFloat(getComputedStyle(why).fontSize),
               body: parseFloat(getComputedStyle(opt).fontSize) };
    }""")
    out = []
    if got["gap"] < 12:
        out.append(f"'Why this matters' sits {got['gap']:.0f}px above the first answer")
    if got["size"] >= got["body"]:
        out.append(f"'Why this matters' is {got['size']}px, no smaller than the {got['body']}px answers")
    return out + [f"unanswered: {u}" for u in r.unanswered]


def serve_mobile_build(page) -> None:
    """The real mobile bundle (dist/m) at the path the preview's iframe loads. Registered after
    serve(), so it answers first; anything it does not hold falls back to the harness."""
    dist = frontend_dir("smb-partner-enablement") / "dist" / "m"
    prefix = "/smb-partner-enablement/m/"

    def handler(route):
        path = urlsplit(route.request.url).path
        f = dist / (path[len(prefix):] or "index.html")
        if not f.is_file():
            return route.fallback()
        ctype = "text/javascript" if f.suffix == ".js" else (mimetypes.guess_type(f.name)[0] or "text/html")
        return route.fulfill(status=200, content_type=ctype, body=f.read_bytes())

    page.route(f"**{prefix}**", handler)


def check_smb_mobile_safe_area(page) -> list[str]:
    """In the Mobile preview the drawn Dynamic Island sat on the app header and the home
    bar ran through 'Tap to speak'. The real mobile build is loaded into the frame, and neither
    piece of drawn hardware may overlap the frame it renders in."""
    page.set_viewport_size({"width": 1440, "height": 900})
    r = smb_up(page)
    serve_mobile_build(page)
    page.get_by_role("button", name="📱 Mobile").click()
    frame = page.frame_locator(".mscreen iframe")
    try:
        frame.locator(".mtop .brand").wait_for(timeout=8000)
        frame.locator(".hint").wait_for(timeout=8000)
    except Exception:
        return ["the mobile build did not render in the preview, so nothing was checked"]
    rects = page.evaluate("""() => Object.fromEntries(['.mscreen iframe', '.mnotch', '.mhome'].map((s) => {
      const q = document.querySelector(s).getBoundingClientRect();
      return [s, [q.left, q.top, q.right, q.bottom]]; }))""")
    app = rects[".mscreen iframe"]
    out = []
    for sel, what in ((".mnotch", "the Dynamic Island"), (".mhome", "the home indicator")):
        x0, y0, x1, y1 = rects[sel]
        if x0 < app[2] and app[0] < x1 and y0 < app[3] and app[1] < y1:
            out.append(f"{what} is drawn over the app ({rects[sel]} inside the frame {app})")
    return out + [f"unanswered: {u}" for u in r.unanswered]


def check_smb_device_icons(page) -> list[str]:
    """The icon-only Microphone and Speaker buttons were colour emoji, grey whatever the
    ink, and washed out on light surfaces. Each icon must reach 3:1 against the header, measured
    from its pixels, in every palette and mode. The strongest QUARTER of the ink (p75) is held to
    3:1, not the strongest tenth: the mic emoji's dark grille put its p90 at 3.1:1 or more on
    every light palette, while its body, which is what the owner saw, measured as little as
    1.9:1 at p75 (the speaker: 1.6:1)."""
    r = smb_up(page)
    buttons = page.locator(".audio-pickers .icon-btn")
    if buttons.count() != 2:
        return [f"{buttons.count()} device-picker buttons rendered, expected the microphone and speaker"]
    names = [buttons.nth(i).get_attribute("title") for i in range(2)]

    def measure(pal: str, mode: str) -> list[str]:
        probs = []
        for i, name in enumerate(names):
            got = glyph_contrast(page, buttons.nth(i))
            if got["ink"] < 20 or got["p75"] < 3:
                probs.append(f"{name} icon: {got['p75']:.2f}:1 (p75) over {got['ink']} ink px")
        return probs

    return each_theme(page, measure) + [f"unanswered: {u}" for u in r.unanswered]


def check_smb_device_menus(page) -> list[str]:
    """The same pickers' menus: each picker's menu was anchored by its RIGHT edge while the
    buttons sit at the LEFT end of the header, so it opened off the rail and under the shell's app
    rail (Microphone spanned x -82 to 148 at 1440x900). Each open menu must lie inside the rail,
    and its first item must hit-test as itself at 5%, 50% and 95% of its width."""
    r = smb_up(page)
    buttons = page.locator(".audio-pickers .icon-btn")
    if buttons.count() != 2:
        return [f"{buttons.count()} device-picker buttons rendered, expected the microphone and speaker"]
    out = []
    for i in range(2):
        name = buttons.nth(i).get_attribute("title")
        buttons.nth(i).click()
        page.wait_for_selector(".audio-menu", timeout=4000)
        got = page.evaluate("""() => {
          const root = document.querySelector('.smbp').getBoundingClientRect();
          const menu = document.querySelector('.audio-menu').getBoundingClientRect();
          const item = document.querySelector('.audio-menu .audio-item');
          const ir = item.getBoundingClientRect(), y = ir.top + ir.height / 2;
          const hits = [0.05, 0.5, 0.95].map(f => {
            const el = document.elementFromPoint(ir.left + ir.width * f, y);
            return !!el && (el === item || item.contains(el));
          });
          return { root: [root.left, root.right], menu: [menu.left, menu.right], hits };
        }""")
        (rl, rr), (ml, mr) = got["root"], got["menu"]
        if ml < rl or mr > rr:
            out.append(f"{name} menu spans x {ml:.0f} to {mr:.0f}, outside the rail's {rl:.0f} to {rr:.0f}")
        if not all(got["hits"]):
            out.append(f"{name} menu's first item is covered at {got['hits'].count(False)} of 3 points")
        buttons.nth(i).click()  # close it again (the buttons toggle their own menu)
    return out + [f"unanswered: {u}" for u in r.unanswered]


#: This module's checks: name -> function. railcheck.py's RAILS names each one, fixes its
#: place in --list and says which frontends it mounts.
CHECKS = {
    "smb-why-aside": check_smb_why_aside,
    "smb-mobile-safe-area": check_smb_mobile_safe_area,
    "smb-device-icons": check_smb_device_icons,
    "smb-device-menus": check_smb_device_menus,
}
