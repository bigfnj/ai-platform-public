"""Regenerate the 6 llama tray icons from ollama-icon.svg.

Needs Playwright (renders the SVG in headless Chromium). Run with the DevToolbox
venv python (it has playwright) — see the reference-playwright-toolbox memo:
  C:\\Users\\Admin\\AppData\\Local\\DevToolbox\\python\\.venv\\Scripts\\python.exe render_icons.py

The source SVG is a single black llama path whose muzzle is a ring with a
transparent hole; we place a colored ellipse behind that hole (the 'nose' status
light) and recolor the linework black (light taskbar) or white (dark taskbar).
"""
from pathlib import Path
from playwright.sync_api import sync_playwright

HERE = Path(__file__).parent
SVG = (HERE / "ollama-icon.svg").read_text(encoding="utf-8")
ICONS = HERE / "icons"
ICONS.mkdir(exist_ok=True)

CX, CY, RX, RY = 256, 268, 52, 31            # nose ellipse (viewBox units), fits the muzzle hole
OUTLINES = {"light": "#000000", "dark": "#ffffff"}
NOSE = {"green": "#3fb950", "yellow": "#f4be3f", "red": "#ec4d4d"}
SIZE = 32

def variant(outline, nose):
    s = SVG.replace('fill="#000"', 'fill="%s"' % outline)
    ell = '<ellipse cx="%d" cy="%d" rx="%d" ry="%d" fill="%s"/>' % (CX, CY, RX, RY, nose)
    i = s.index('>') + 1
    return s[:i] + ell + s[i:]

def render(browser, svg, size, out):
    pg = browser.new_page(viewport={"width": size, "height": size})
    pg.set_content('<!doctype html><style>*{margin:0;padding:0}'
                   'svg{width:%dpx;height:%dpx;display:block}</style>%s' % (size, size, svg))
    pg.wait_for_timeout(120)
    pg.screenshot(path=out, omit_background=True, clip={"x": 0, "y": 0, "width": size, "height": size})
    pg.close()

with sync_playwright() as p:
    b = p.chromium.launch()
    for tname, ohex in OUTLINES.items():
        for sname, nhex in NOSE.items():
            render(b, variant(ohex, nhex), SIZE, str(ICONS / ("broker_%s_%s.png" % (tname, sname))))
    b.close()
print("wrote 6 icons to", ICONS)
