"""ai-playground's checks: the bundle restyling nothing outside the rail, the RAG controls row at
the shell's width, and the ask bar's Dictate pill.
"""
from __future__ import annotations

from .harness import Api, NoFixture, css_leaks, mount, rail, serve


def ai_playground_api() -> Api:
    def api(method, path, q, body):
        if path == "/api/demos":
            return {"demos": [{"id": "rag", "title": "RAG over documents", "icon": "R", "blurb": "",
                               "status": "ready"}],
                    "nim": {"available": False, "endpoint": "", "chat_model": ""},
                    # The deployed box's values, as the walk read them off the controls row.
                    "gen": {"role": "@chat", "model": "nemotron-3-nano:4b", "label": "NVIDIA Nemotron",
                            "is_nvidia": True, "gpu": "NVIDIA GeForce RTX 4090"}}
        if path == "/api/whoami":
            return {"user": "owner-one", "is_admin": True}
        if path == "/api/capabilities":
            return {"broker": "ok", "models": []}
        if path == "/api/rag/corpora":
            return {"corpora": [{"id": 1, "slug": "nvidia-stack", "name": "Nvidia Stack", "kind": "seed",
                                 "owner": None, "chunks": 58},
                                {"id": 2, "slug": "embedding-concepts", "name": "Embedding Concepts",
                                 "kind": "seed", "owner": None, "chunks": 16}], "default": "nvidia-stack"}
        raise NoFixture(path)

    return api


def check_css_ai_playground(page) -> list[str]:
    """ai-playground carried the same standalone-dev sheet (18px)."""
    return css_leaks(page, rail("ai-playground", ai_playground_api()))


def check_ap_controls_row(page) -> list[str]:
    """The RAG controls row stays one row at the width the shell gives a rail at 1440px
    (1440 - the 76px nav - 48px of padding - the scrollbar gutter, about 1300): it wrapped
    there and left the GPU pill alone on a second line."""
    page.set_viewport_size({"width": 1300, "height": 900})
    r = rail("ai-playground", ai_playground_api())
    serve(page, r)
    mount(page, r)
    page.wait_for_selector(".ai-playground .rag-controls .rag-pill", timeout=8000)
    page.wait_for_selector(".ai-playground .rag-corpus option >> text=Nvidia Stack", state="attached",
                           timeout=8000)
    page.wait_for_timeout(300)
    mids = page.evaluate("""() => Object.fromEntries(['.rag-corpus select', '.rag-upload-btn', '.rag-nim', '.rag-pill']
      .map((s) => { const r = document.querySelector('.ai-playground .rag-controls ' + s).getBoundingClientRect();
                    return [s, Math.round(r.top + r.height / 2)]; }))""")
    first = mids[".rag-corpus select"]
    off = {s: y - first for s, y in mids.items() if abs(y - first) > 10}
    return [f"the controls row broke onto more than one line: {off} px below the corpus picker"] if off else []


def check_ap_dictate_pill(page) -> list[str]:
    """The RAG ask bar's Dictate control keeps web-core's pill (.vc-speak.sm). The bar's own
    `button` rule, meant for Ask, outranked it and squashed Dictate into a 14px gradient strip
    beside a 47px input."""
    page.set_viewport_size({"width": 1440, "height": 900})
    r = rail("ai-playground", ai_playground_api())
    serve(page, r)
    mount(page, r)
    dictate = page.locator(".ai-playground .askbar button[aria-label='Dictate']")
    try:
        dictate.wait_for(timeout=8000)
    except Exception:
        return ["no Dictate control in the ask bar (is the harness origin secure?)"]
    page.wait_for_timeout(300)
    got = dictate.evaluate("""(b) => {
      const shared = [...document.styleSheets].flatMap((s) => { try { return [...s.cssRules] } catch { return [] } })
        .find((r) => r.selectorText === '.vc-speak.sm');
      const cs = getComputedStyle(b);
      return { height: b.getBoundingClientRect().height, pad: cs.paddingTop, want: shared ? shared.style.paddingTop : null,
               radius: cs.borderTopLeftRadius, image: cs.backgroundImage, minWidth: cs.minWidth };
    }""")
    ask = page.locator(".ai-playground .askbar button", has_text="Ask").evaluate(
        "(b) => getComputedStyle(b).minWidth")
    out = []
    if got["want"] is None:
        out.append("web-core's .vc-speak.sm rule was not found, so the pill could not be compared")
    elif got["pad"] != got["want"]:
        out.append(f"Dictate's padding-top is {got['pad']}, not web-core's {got['want']}")
    if got["height"] < 18:
        out.append(f"Dictate is {got['height']:.0f}px tall")
    if got["image"] != "none" or got["minWidth"] == "84px" or got["radius"] == "12px":
        out.append(f"Dictate wears the Ask button's style: {got}")
    if ask != "84px":
        out.append(f"the Ask button lost its own rule (min-width {ask})")
    return out


#: This module's checks: name -> function. railcheck.py's RAILS names each one, fixes its
#: place in --list and says which frontends it mounts.
CHECKS = {
    "css-ai-playground": check_css_ai_playground,
    "ap-dictate-pill": check_ap_dictate_pill,
    "ap-controls-row": check_ap_controls_row,
}
