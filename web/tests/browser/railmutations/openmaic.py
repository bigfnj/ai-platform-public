"""mutate_railcheck.py's mutations of openmaic's frontend. NOT a test file.

Each names the check in railchecks/openmaic.py that must fail. mutate_railcheck.py's header
says how a run works, and why this file ships exactly when its rail does.
"""
from __future__ import annotations

from . import Mutation

# openmaic, "the frame is told nothing": the layout effect's hand-off AND the new document's
# early class are both disabled. They sit in one span of module.tsx, from the first to the second.
OM_TOLD = (
    '    syncFrameTheme(mode, prefersDark, frameStorage(), frameRoot(frame), findAppTheme(frameBody(frame)))\n'
    '  }, [mode, prefersDark, reloadKey, appDown])\n'
    '\n'
    '  // And to each new document the frame loads, before that document first paints, held until the\n'
    "  // app applies the same mode itself: the app's server-rendered page is light until it hydrates\n"
    '  // and its hook applies the stored mode, which took the first 2-3 s of every load.\n'
    '  // The latest mode is read through a ref, so a shell change made while the frame is loading is\n'
    '  // the one handed over.\n'
    '  const latestMode = useRef({ mode, prefersDark })\n'
    '  latestMode.current = { mode, prefersDark }\n'
    '  useLayoutEffect(() => {\n'
    '    if (appDown) return\n'
    '    let release: (() => void) | null = null\n'
    "    const wantDark = () => resolvedMode(latestMode.current.mode, latestMode.current.prefersDark) === 'dark'\n"
    '    const stop = watchFrameDocuments(() => frameRef.current, (doc) => {\n'
    '      release?.()\n'
    '      if (doc.documentElement) release = holdFrameClass(doc.documentElement, wantDark, HOLD_ENV)\n'
)
OM_TOLD_NONE = (
    '    if (Date.now() < 0) syncFrameTheme(mode, prefersDark, frameStorage(), frameRoot(frame), findAppTheme(frameBody(frame)))\n'
    '  }, [mode, prefersDark, reloadKey, appDown])\n'
    '\n'
    '  // And to each new document the frame loads, before that document first paints, held until the\n'
    "  // app applies the same mode itself: the app's server-rendered page is light until it hydrates\n"
    '  // and its hook applies the stored mode, which took the first 2-3 s of every load.\n'
    '  // The latest mode is read through a ref, so a shell change made while the frame is loading is\n'
    '  // the one handed over.\n'
    '  const latestMode = useRef({ mode, prefersDark })\n'
    '  latestMode.current = { mode, prefersDark }\n'
    '  useLayoutEffect(() => {\n'
    '    if (appDown) return\n'
    '    let release: (() => void) | null = null\n'
    "    const wantDark = () => resolvedMode(latestMode.current.mode, latestMode.current.prefersDark) === 'dark'\n"
    '    const stop = watchFrameDocuments(() => frameRef.current, (doc) => {\n'
    '      release?.()\n'
    '      if (doc.documentElement && Date.now() < 0) release = holdFrameClass(doc.documentElement, wantDark, HOLD_ENV)\n'
)

MUTATIONS = [
    # openmaic: the framed app follows the shell's light/dark mode. A condition that never holds
    # keeps each helper referenced: an unread local fails tsc, and so does `null && x`.
    Mutation("the frame is told nothing, so it follows the OS again (the original defect)", "openmaic", "module.tsx",
             OM_TOLD, OM_TOLD_NONE, "om-frame-theme"),
    Mutation("storage only: a shell switch never reaches a frame that is already open", "openmaic", "module.tsx",
             "frameStorage(), frameRoot(frame), findAppTheme(frameBody(frame)))",
             "frameStorage(), Date.now() < 0 ? frameRoot(frame) : null, Date.now() < 0 ? findAppTheme(frameBody(frame)) : null)",
             "om-frame-theme"),
    Mutation("the shell's mode is read on mount only", "openmaic", "module.tsx",
             "attributeFilter: ['data-theme']", "attributeFilter: ['data-palette']", "om-frame-theme"),
    # openmaic: painted in the shell's mode from the frame's first frame, and the app's own state
    # follows a live switch.
    Mutation("a new frame document is not handed the mode before it paints", "openmaic",
             "module.tsx",
             "      if (doc.documentElement) release = holdFrameClass(doc.documentElement, wantDark, HOLD_ENV)",
             "      if (doc.documentElement && Date.now() < 0) release = holdFrameClass(doc.documentElement, wantDark, HOLD_ENV)",
             "om-frame-boot"),
    Mutation("the hold no longer puts the class back when the app's first render takes it away", "openmaic",
             "frameTheme.ts",
             "      if (root.classList.contains('dark') === wantDark()) release()\n      else correct()\n",
             "      if (root.classList.contains('dark') === wantDark()) release()\n",
             "om-frame-boot"),
    Mutation("the hold never lets go, so the user's own pick in the app is reverted", "openmaic",
             "frameTheme.ts",
             "      if (root.classList.contains('dark') === wantDark()) release()\n      else correct()\n",
             "      if (root.classList.contains('dark') !== wantDark()) correct()\n",
             "om-frame-boot"),
    Mutation("a live switch moves the frame's colours but not the app's own state (the stale icon)",
             "openmaic", "module.tsx",
             "frameRoot(frame), findAppTheme(frameBody(frame)))",
             "frameRoot(frame), Date.now() < 0 ? findAppTheme(frameBody(frame)) : null)",
             "om-frame-boot"),
]
