"""mutate_railcheck.py's mutations of watch-builder's frontend. NOT a test file.

Each names the check in railchecks/watch_builder.py that must fail. mutate_railcheck.py's header
says how a run works, and why this file ships exactly when its rail does.
"""
from __future__ import annotations

from . import Mutation

MUTATIONS = [
    # watch-builder: the canvas inside its pane, the footer captions, the live tag and the preset
    # button. Each is one declaration of theme.css put back.
    Mutation("the canvas frame no longer scrolls a screen wider than it", "watch-builder", "theme.css",
             "  overflow-x: auto;\n", "", "wb-canvas-fits"),
    Mutation("the canvas is centred unsafely again", "watch-builder", "theme.css",
             "  margin-inline: auto;\n", "", "wb-canvas-fits"),
    Mutation("the canvas pane keeps its automatic minimum", "watch-builder", "theme.css",
             ".watch-builder .wb-canvas-pane { position: relative; min-width: 0; }",
             ".watch-builder .wb-canvas-pane { position: relative; }", "wb-canvas-fits"),
    Mutation("a later column rule overrides the stacking query again", "watch-builder", "theme.css",
             ".watch-builder .wb-canvas-pane { position: relative; min-width: 0; }",
             ".watch-builder .wb-cols { grid-template-columns: minmax(230px, 0.9fr) minmax(260px, 1fr) "
             "minmax(320px, 1.4fr); }\n.watch-builder .wb-canvas-pane { position: relative; min-width: 0; }",
             "wb-canvas-fits"),
    Mutation("the canvas caption reads the palette's muted ink again", "watch-builder", "theme.css",
             ".watch-builder .wb-canvas-meta .wb-sub { color: #a1a1ad; }", "", "wb-caption-contrast"),
    Mutation("the on-watch toggle reads the palette's muted ink again", "watch-builder", "theme.css",
             "  color: #a1a1ad;   /* on the #2a2a33 frame", "  color: var(--mut);   /* on the #2a2a33 frame",
             "wb-caption-contrast"),
    Mutation("the live tag keeps its dark literals again", "watch-builder", "theme.css",
             "  border-color: var(--ok-text);\n  color: var(--ok-text);\n"
             "  background: color-mix(in srgb, var(--ok) 9%, transparent);\n",
             "  border-color: #3a7d52;\n  color: #8fd6a8;\n  background: #14251b;\n", "wb-live-tag-palette"),
    Mutation("the preset button may shrink again", "watch-builder", "theme.css",
             ".watch-builder .wb-preset-foot .wb-btn { flex: none; }", "", "wb-use-this-one-line"),
    # watch-builder's AI jobs (2026-10-09): progress and partial variants while the job runs, a
    # poll that stops on a final state, Cancel, a 404 that is final, a reload that picks the
    # job up again, and an estimate that says whose resources it spends.
    Mutation("variants show only once the job is done", "watch-builder", "module.tsx",
             "    if (j.kind === 'variations' && j.state !== 'done') setVariants(j.partial || [])\n",
             "", "wb-job-progress"),
    Mutation("progress is no longer counted in variants", "watch-builder", "module.tsx",
             "  return `Exploring variations: ${job.progress.done} of ${job.progress.total} done.`",
             "  return 'Exploring variations.'", "wb-job-progress"),
    Mutation("a finished job is still polled (and still offers Cancel)", "watch-builder", "module.tsx",
             "const ACTIVE_JOB_STATES: readonly string[] = ['queued', 'running']",
             "const ACTIVE_JOB_STATES: readonly string[] = ['queued', 'running', 'done']",
             "wb-job-progress"),
    Mutation("Cancel job sends nothing to the rail", "watch-builder", "module.tsx",
             "      applyJob(await deleteJSON<AiJob>(`/api/ai/jobs/${job.job_id}`))",
             "      applyJob({ ...job, state: 'cancelled' })", "wb-job-cancel"),
    Mutation("the poll follows any job the page holds, so a cancelled one is polled on", "watch-builder",
             "module.tsx",
             "  const pollingId = job && isActiveJob(job.state) && !outOfTouch ? job.job_id : null",
             "  const pollingId = job && !outOfTouch ? job.job_id : null", "wb-job-cancel"),
    Mutation("a 404 is taken for a blip, so a lost job is asked about for ever", "watch-builder",
             "module.tsx",
             "    if (status === 404) return { gone: true }",
             "    if (status === 999) return { gone: true }",
             "wb-job-lost"),
    Mutation("a reloaded page does not look for the viewer's job", "watch-builder", "module.tsx",
             "          if (pick) attach(pick.job_id, stop.signal)",
             "          if (pick && Date.now() < 0) attach(pick.job_id, stop.signal)", "wb-job-reload"),
    Mutation("the cost says GPU whatever the estimate says", "watch-builder", "module.tsx",
             "      {estimate.holds_gpu ? ' of GPU' : ' on Claude, with no GPU unless a call falls back'}.",
             "      {estimate.holds_gpu ? ' of GPU' : ' of GPU'}.",
             "wb-cost-says-who-pays"),
    # The 2026-10-09 review: what a cancel stops, said exactly; a 409 naming another tab's job.
    Mutation("the cancelled note claims no model call starts after a cancel again", "watch-builder",
             "module.tsx",
             "No new call starts after a cancel; a call already sent finishes and its answer is dropped.`",
             "No model call starts after a cancel.`", "wb-job-cancel"),
    Mutation("a 409's job shows nothing: the page attaches to no job", "watch-builder", "module.tsx",
             "        attach(detail.job_id)\n", "        if (Date.now() < 0) attach(detail.job_id)\n",
             "wb-job-busy-attach"),
    Mutation("a 409's finished design replaces the editor, unsaved edits and all", "watch-builder",
             "module.tsx",
             "      if (j.kind === 'design' && j.result?.design && foreign) {",
             "      if (j.kind === 'design' && j.result?.design && foreign && Date.now() < 0) {",
             "wb-job-busy-attach"),
    Mutation("the 409's line stays after the job it names is final", "watch-builder", "module.tsx",
             "    if (foreign && !isActiveJob(j.state)) setBusyLine('')",
             "    if (foreign && !isActiveJob(j.state) && Date.now() < 0) setBusyLine('')",
             "wb-job-busy-attach"),
    Mutation("the offered design is never shown, so it cannot be opened", "watch-builder", "module.tsx",
             "      {offer && (\n", "      {offer && Date.now() < 0 && (\n", "wb-job-busy-attach"),
    Mutation("a 409 naming only a cancelled job is attached to like a running one", "watch-builder",
             "module.tsx",
             "      if (err.status === 409 && detail?.error === 'busy' && detail.job_id) {",
             "      if (err.status === 409 && detail?.job_id) {", "wb-job-busy-finishing"),
    # The lease (2026-10-09): a hidden tab keeps reading its job, and a job the rail stopped
    # says why, with what finished.
    Mutation("a hidden tab sends no keep-alive, so the rail stops its job", "watch-builder",
             "module.tsx",
             "    if (!page?.polling(id)) {",
             "    if (!page?.polling(id) && Date.now() < 0) {",
             "wb-job-hidden-keepalive"),
    Mutation("the running job no longer says that closing the tab stops it", "watch-builder",
             "module.tsx",
             '<span className="wb-sub wb-job-lease">{leaseLine(job)}</span>',
             '<span className="wb-sub wb-job-lease">{Date.now() < 0 ? leaseLine(job) : \'\'}</span>',
             "wb-job-hidden-keepalive"),
    Mutation("a job the rail stopped never says why", "watch-builder", "module.tsx",
             "      {job?.stopped && job.state !== 'cancelled' && (",
             "      {job?.stopped && job.state !== 'cancelled' && Date.now() < 0 && (",
             "wb-job-stopped"),
    Mutation("every stop is blamed on the ceiling", "watch-builder", "module.tsx",
             "  const why = job.stopped === 'unwatched'",
             "  const why = job.stopped === 'unwatched' && Date.now() < 0", "wb-job-stopped"),
    Mutation("the stopped note no longer says what finished", "watch-builder", "module.tsx",
             "    ? ` ${keptVariants(job)} of ${job.progress.total} variations finished first and are kept.`",
             "    ? ''", "wb-job-stopped"),
    Mutation("a design the rail stopped is shown as a model failure", "watch-builder", "module.tsx",
             "    } else if (j.state === 'failed' && !j.stopped) {",
             "    } else if (j.state === 'failed') {", "wb-job-stopped"),
    # The lease review (2026-10-09): what a stopped pass keeps is counted in variants that
    # finished as a design; the running line names its exception; the keep-alive outlives the
    # component, asks on through an outage, gives each read a deadline, and ends with its job.
    Mutation("the stopped note counts a failed variant as kept (progress.done)", "watch-builder",
             "module.tsx",
             "    ? ` ${keptVariants(job)} of ${job.progress.total} variations finished first and are kept.`",
             "    ? ` ${job.progress.done} of ${job.progress.total} variations finished first and are kept.`",
             "wb-job-stopped"),
    Mutation("the running line promises a background tab without naming the exception", "watch-builder",
             "module.tsx",
             "on another rail or in the background, unless the browser puts that tab to sleep; with none open",
             "on another rail or even in the background; with none open",
             "wb-job-hidden-keepalive"),
    Mutation("the page stops asking once it has lost touch, so a long gateway restart stops a watched job",
             "watch-builder", "module.tsx",
             "    // Out of touch. The poll stands down, and the keep-alive asks on at its own pace.\n"
             "    setOutOfTouch(true)\n",
             "    // Out of touch. The poll stands down, and the keep-alive asks on at its own pace.\n"
             "    stopKeepAlive()\n    setOutOfTouch(true)\n",
             "wb-job-outage"),
    Mutation("the keep-alive dies with the component, so moving to another rail stops the job",
             "watch-builder", "module.tsx",
             "      if (kept.page === page) kept.page = null\n",
             "      if (kept.page === page) kept.page = null\n      stopKeepAlive()\n",
             "wb-job-other-rail"),
    Mutation("a read has no deadline, so one that never settles holds up every later keep-alive",
             "watch-builder", "module.tsx",
             "  const deadline = window.setTimeout(abort, KEEPALIVE_MS)\n",
             "  const deadline = window.setTimeout(() => undefined, KEEPALIVE_MS)\n", "wb-job-hung-read"),
    Mutation("a 401 is taken for a blip, so a signed-out page asks for ever", "watch-builder",
             "module.tsx",
             "    if (status === 401 || status === 403) return { refused: true }",
             "    if (status === 999) return { refused: true }", "wb-job-keepalive-ends"),
    Mutation("a pass that finishes on another rail is lost to the page that comes back",
             "watch-builder", "module.tsx",
             "    if (!ours) {\n      pickUp()\n",
             "    if (!ours) {\n      if (Date.now() < 0) pickUp()\n",
             "wb-job-keepalive-ends"),
    Mutation("a pass that finishes on another rail is read on every 30 s", "watch-builder",
             "module.tsx",
             "  if ('job' in read && isActiveJob(read.job.state)) return\n  stopKeepAlive()\n",
             "  if ('job' in read) return\n  stopKeepAlive()\n", "wb-job-keepalive-ends"),
    Mutation("a job that finishes in view leaves its keep-alive running", "watch-builder",
             "module.tsx",
             "    else if (kept.id === j.job_id) stopKeepAlive()\n", "", "wb-job-keepalive-ends"),
    # The second lease review (2026-10-09). The keep-alive holds only its job's id, so a viewer
    # who signs in after a sign-out on the same tab is shown nothing of the last one's job.
    Mutation("the keep-alive holds a finished pass again and hands it to the next page to mount",
             "watch-builder", "module.tsx",
             "  kept.failures = 0\n  if ('job' in read && isActiveJob(read.job.state)) return\n",
             "  kept.failures = 0\n  if ('job' in read && isActiveJob(read.job.state)) return\n"
             "  if ('job' in read) heldJob = read.job\n",
             "wb-job-signout",
             also=(("function attachPage(page: JobPage): { id: string; foreign: boolean } | null {\n"
                    "  kept.page = page\n",
                    "let heldJob: AiJob | null = null\n"
                    "function attachPage(page: JobPage): { id: string; foreign: boolean } | null {\n"
                    "  kept.page = page\n"
                    "  if (heldJob) { const j = heldJob; heldJob = null; page.heard(j.job_id, { job: j }) }\n"),)),
    Mutation("a keep-alive read is shown by whichever page is mounted when it is answered",
             "watch-builder", "module.tsx",
             "        if (page && kept.page === page) page.heard(id, read)",
             "        if (kept.page) kept.page.heard(id, read)", "wb-job-signout"),
    Mutation("a 404 for the job a page mounted with leaves the keep-alive reading it",
             "watch-builder", "module.tsx",
             "          if (kept.id === id) stopKeepAlive()\n          setUnreached(null)\n",
             "          setUnreached(null)\n", "wb-job-signout"),
    # A keep-alive read leaves a finished pass on offer to the next page's listing.
    Mutation("a keep-alive read marks a finished pass delivered, so no reload or new tab offers it",
             "watch-builder", "module.tsx",
             "      const read = await readJobOnce(id, stop.signal, !page)",
             "      const read = await readJobOnce(id, stop.signal, false)", "wb-job-offered-later"),
    # A browser without AbortSignal.timeout, and a keep-alive tick that throws.
    Mutation("each read's deadline is AbortSignal.timeout again, which Safari < 16 and Chrome < 103 lack",
             "watch-builder", "module.tsx",
             "  const deadline = window.setTimeout(abort, KEEPALIVE_MS)\n",
             "  const deadline = window.setTimeout(() => undefined, KEEPALIVE_MS)\n"
             "  AbortSignal.timeout(KEEPALIVE_MS).addEventListener('abort', abort)\n",
             "wb-job-old-browser"),
    Mutation("a keep-alive tick that throws ends the keep-alive", "watch-builder", "module.tsx",
             "  } catch (e) {\n"
             "    console.error('watch-builder: a keep-alive tick threw; the next one is scheduled all the same', e)\n"
             "  } finally {\n"
             "    if (kept.stop === stop) kept.timer = window.setTimeout(() => void keepTick(stop), KEEPALIVE_MS)\n"
             "  }\n",
             "  } catch (e) {\n"
             "    throw e\n"
             "  }\n"
             "  if (kept.stop === stop) kept.timer = window.setTimeout(() => void keepTick(stop), KEEPALIVE_MS)\n",
             "wb-job-old-browser"),
    # The review's three surviving plants: with no page mounted, a 404 or a refusal that does not
    # stop the keep-alive, an outage it gives up on, and a 403 taken for a blip.
    Mutation("with no page mounted, a 404 or a refusal leaves the keep-alive asking",
             "watch-builder", "module.tsx",
             "  if ('job' in read && isActiveJob(read.job.state)) return\n  stopKeepAlive()\n",
             "  if ('job' in read && isActiveJob(read.job.state)) return\n"
             "  if (!('job' in read)) return\n  stopKeepAlive()\n", "wb-job-unmounted-ends"),
    Mutation("with no page mounted, the keep-alive gives up after three failed reads",
             "watch-builder", "module.tsx",
             "    kept.failures += 1\n    return\n  }\n  kept.failures = 0\n",
             "    kept.failures += 1\n    if (kept.failures >= 3) stopKeepAlive()\n    return\n  }\n"
             "  kept.failures = 0\n", "wb-job-unmounted-ends"),
    Mutation("a 403 is taken for a blip, so a viewer no longer let in is asked about for ever",
             "watch-builder", "module.tsx",
             "    if (status === 401 || status === 403) return { refused: true }",
             "    if (status === 401) return { refused: true }", "wb-job-unmounted-ends"),
    # The third lease review (2026-10-09). A page says how a job ended only for a job a read has
    # shown it, so the next viewer is not told the last one's is gone when their own read of it
    # fails or is slow and the keep-alive's 404 reaches their page first.
    Mutation("the keep-alive's reads reach a page as if it had shown their job (the reviewed bug)",
             "watch-builder", "module.tsx",
             "    let pickedUp = false\n",
             "    let pickedUp = false\n    let own = false\n", "wb-job-signout",
             also=(("        if ('job' in read || shownJob.current === id) {",
                    "        if ('job' in read || shownJob.current === id || !own) {"),
                   ("        if (read !== null && !stop.signal.aborted) page.heard(ours.id, read)",
                    "        if (read !== null && !stop.signal.aborted) { own = true; page.heard(ours.id, read); own = false }"))),
    Mutation("a 404 for a job this page was never shown says 'This AI job is gone' again",
             "watch-builder", "module.tsx",
             "        if ('job' in read || shownJob.current === id) {",
             "        if ('job' in read || shownJob.current === id || 'gone' in read) {", "wb-job-signout"),
    # A page that comes back while the rail is down says it has lost contact, naming no job.
    Mutation("the lost-contact note needs a shown job again, so a return while the rail is down says nothing",
             "watch-builder", "module.tsx",
             "      {unreached && unreached === kept.id && (\n",
             "      {unreached && unreached === kept.id && job && (\n", "wb-job-unmounted-ends"),
    Mutation("a failed read of the kept id sets no lost-contact note", "watch-builder", "module.tsx",
             "          if (kept.id === id) setUnreached(id)\n", "", "wb-job-unmounted-ends"),
    Mutation("an answered read leaves the lost-contact note up", "watch-builder", "module.tsx",
             "    shownJob.current = j.job_id\n    setUnreached(null)\n",
             "    shownJob.current = j.job_id\n", "wb-job-unmounted-ends"),
    # Only a read no page shows carries the marker; every read a page shows delivers a pass.
    Mutation("a keep-alive read a mounted page shows carries the marker again (the reviewed bug)",
             "watch-builder", "module.tsx",
             "      const read = await readJobOnce(id, stop.signal, !page)",
             "      const read = await readJobOnce(id, stop.signal, true)", "wb-job-offered-later"),
    Mutation("the poll carries the marker, so a pass watched to the end is offered again (poll-marked)",
             "watch-builder", "module.tsx",
             "    const read = await readJobOnce(pollingId, signal)\n",
             "    const read = await readJobOnce(pollingId, signal, true)\n", "wb-job-keepalive-ends"),
    Mutation("the read as a page mounts carries the marker, so a pass shown on return is offered "
             "again (mount-read-marked)", "watch-builder", "module.tsx",
             "      void readJobOnce(ours.id, stop.signal).then((read) => {\n",
             "      void readJobOnce(ours.id, stop.signal, true).then((read) => {\n", "wb-job-keepalive-ends"),
]
