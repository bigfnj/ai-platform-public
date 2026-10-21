"""mutate_railcheck.py's mutations of co-worker's frontend. NOT a test file.

Each names the check in railchecks/co_worker.py that must fail. mutate_railcheck.py's header
says how a run works, and why this file ships exactly when its rail does.
"""
from __future__ import annotations

from . import Mutation

MUTATIONS = [
    Mutation("changing the Brief's lens stops the synthesis poll again", "co-worker", "briefsync.ts",
             "    this.set({ lens })\n    this.load()\n",
             "    this.set({ lens })\n    this.stopPolling()\n    this.load()\n",
             "cw-brief-lens-switch"),
    # co-worker: the Co-Work Prompts panel.
    Mutation("an open prompt card stretches its row again", "co-worker", "theme.css",
             "  align-items: start;\n}", "}", "cw-prompt-rows"),
    Mutation("the template note is not rendered", "co-worker", "BriefView.tsx",
             "      {PROMPTS_ARE_TEMPLATES && (\n        <p className=\"cw-prompts-note\">",
             "      {!PROMPTS_ARE_TEMPLATES && (\n        <p className=\"cw-prompts-note\">",
             "cw-prompt-templates"),
    Mutation("an open card names no field to fill in", "co-worker", "BriefView.tsx",
             "{fillIns(job.prompt).length > 0 && (", "{fillIns(job.prompt).length < 0 && (",
             "cw-prompt-templates"),
    Mutation("the button calls the examples the exact prompts again", "co-worker", "BriefView.tsx",
             "'Example prompts for scheduled co-work jobs, as templates to fill in'",
             "'View the exact prompts for each scheduled co-work job'", "cw-prompt-templates"),
    # web-core's read-aloud chip, which the client pulse renders (added 2026-10-14; since
    # 2026-10-15 a stopped read is aborted, web/src/read_aloud.ts, not outrun by a generation).
    Mutation("Stop leaves the read running, so its reply plays again", "co-worker",
             "web/src/SpeakButton.tsx",
             "{ reader.current.stop(); stopSpeaking(); setState('idle'); return }",
             "{ stopSpeaking(); setState('idle'); return }",
             "cw-speak-stop-loading"),
    Mutation("a stopped read whose request fails falls back to the on-device voice again",
             "co-worker", "web/src/read_aloud.ts",
             "        if (!mine.signal.aborted) fallback()\n", "        fallback()\n",
             "cw-speak-stop-loading"),
    Mutation("the reader's Stop aborts nothing, so the pending read is still current",
             "co-worker", "web/src/read_aloud.ts",
             "    stop(): void {\n      live?.abort()\n      live = null\n",
             "    stop(): void {\n      live = null\n",
             "cw-speak-stop-loading"),
    Mutation("ttsLight drops the signal, so Stop cannot end the request", "co-worker",
             "web/src/platformApi.ts",
             "'/api/platform/tts_light', { method: 'POST', body: JSON.stringify(body), signal }),",
             # `signal && undefined` keeps the parameter read: an unread one fails tsc, and the build.
             "'/api/platform/tts_light', { method: 'POST', body: JSON.stringify(body), signal: signal && undefined }),",
             "cw-speak-stop-loading"),
    Mutation("an unmount leaves the read in flight running", "co-worker", "web/src/SpeakButton.tsx",
             "    return () => { alive.current = false; reader.current.stop(); stopSpeakingFor(owner.current) }\n",
             "    return () => { alive.current = false; stopSpeakingFor(owner.current) }\n",
             "cw-speak-stop-loading"),
]
