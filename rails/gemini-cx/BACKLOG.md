# Gemini Enterprise CX rail — backlog

Ordered by value. Items 1 and 2 are the ones that make the corpus trustworthy over time.

## 1. Fill the research gaps the corpus explicitly refuses to guess

Three facts could not be verified during the build and are marked as unverifiable in the corpus.
Each is a small, bounded task with a clear source:

- **Base per-session prices for CX Agent Studio (chat and voice).** The pricing page exists at
  `cloud.google.com/products/gemini-enterprise-for-customer-experience/cx-agent-studio/pricing`
  but resisted automated extraction (heavy client-side rendering). Read it manually and fill
  `pricing-and-licensing/01-what-is-published.md`. Only the **$0.0025/second voice overage after
  300 seconds** is currently sourced.
- **The specific 40+ language list and the 10 audio-to-audio "core languages".** Counts are
  published; enumerations are not, in the material surveyed. Google publishes a languages
  reference page for CX Agent Studio — capture both lists into
  `models-and-languages/02-language-coverage-text-versus-audio.md`.
- **"Personal Intelligence".** Forrester cites it as a beta GECX feature; it did not appear in
  any Google product or documentation page found. Either confirm and document it, or leave the
  corpus's "analyst-sourced and unverified" caveat in place.

## 2. A staleness signal on the corpus

Every knowledge-base file carries `As of:` and `Verified:` dates, but nothing surfaces them. GECX
launched in January 2026 and is moving fast enough that a six-month-old answer is a liability.

Parse the front matter at ingest, store the date per chunk, and show the oldest `Verified:` date
behind each answer — plus a banner when any cited chunk is older than a threshold. This is the
cheapest possible defence against the corpus quietly rotting, and it matters more here than on a
slower-moving subject.

## 3. Re-verify against the docs on a schedule

The platform has a central scheduler (`scheduler_tasks.py`) with per-rail tasks. A monthly task
could re-fetch the tracked Google Cloud doc pages, diff them against a stored hash, and flag
which corpus files reference a page that changed. That turns "the corpus is stale" from a
discovery into a notification.

Watch list, highest churn first: the Commerce Agents page (currently "coming soon" — its flip to
GA is the single most consequential change), the CX Agent Studio agent page (model list, where
`gemini-3-flash` is in Preview), the CCaaS deploy page (the `us`/`eu` region restriction), and
the pricing pages.

## 4. Answer quality work

- **Record a baseline.** The sibling SMB Partner rail has `tools/ab_synthesis.py` for scoring
  answers across model choices. An equivalent here would settle the `gemma3:4b` versus
  `llama3.2:3b` question in MODELS.md with data instead of reasoning.
- **Test retrieval against the traps deliberately.** The SMB rail learned the hard way that a
  chunk can be accurate but unscoped and still lose to a lexically closer neighbour. The
  specific risks here are the language pair (40+ vs 10) and the status pair (announced vs
  documented). Ask both in several phrasings and confirm the right chunk wins.
- **Consider a reranker.** A CPU cross-encoder may be provisioned at
  `%LOCALAPPDATA%\DevToolbox\scripts\rerank.py`. Reranking the top ~20 cosine hits down to 6
  would sharpen exactly the near-identical-chunk case this corpus is full of, at no VRAM cost.

## 5. Deck improvements

- **Track which deck questions get clicked** and reorder by real use rather than by guess.
- **Let a deck question carry a follow-up set** so an answer can suggest the next two questions.
  The corpus is heavily cross-referenced already; the deck currently is not.
- **Thicken the single-file collections — this is measurable today, not theoretical.** Five
  collections have exactly one file: `commerce-agents`, `cx-insights`, `solution-plays`,
  `customer-stories`, `training-and-certification`. Because a deck question is scoped to its
  collections, a scoped ask against a thin collection returns near-duplicate chunks from the same
  file. Observed live: the `commerce-status` question retrieved 6 chunks, **5 of them from
  `commerce-agents/01-the-announced-versus-documented-gap.md`**. The answer was correct, but the
  citation list implies six independent sources when it is effectively one. Either split those
  files along their headings or widen the affected questions' scope. Also surface per-collection
  coverage in the UI so thinness is visible rather than inferred.

## 6. Not doing, and why

- **No voice INPUT.** Read aloud (output) shipped — Kokoro-82M via `tts_light`, see MODELS.md.
  There is no speech-to-text model in the broker, so a spoken *question* would be the browser's
  `SpeechRecognition` or nothing. Not worth it for a desk tool where the questions are one click.
- **No mobile build.** The SMB Partner rail needs one because a partner uses it between meetings.
  This rail is a desk tool for scoping and delivery work; the shell's fixed 76px rail is fine.
- **No upload UI.** `POST /api/upload` exists and works, but the corpus is curated on purpose —
  an easy upload button invites diluting a carefully sourced, status-marked corpus with
  unsourced material. Keep it API-only until there is a real need.
