# GECX knowledge base — authoring contract

This directory is the rail's subject-matter corpus on **Gemini Enterprise for Customer
Experience (GECX)**. Everything here is embedded at boot and is the **only** thing the
assistant is allowed to answer from.

## Layout

Every immediate subfolder is one **collection**. The folder name is the collection id; the UI
shows it title-cased, and a user can scope a question to a subset of collections.

```
knowledge-base/
│  ── the platform: what GECX is and how it works ──
├── gecx-overview/            What GECX is, the four components, launch, lineage from CCAI/CES
├── cx-agent-studio/          The builder: agents, instructions, tools, variables, guardrails
├── agent-assist/             Real-time assistance for human agents
├── cx-insights/              Conversation analytics, topic modeling, Quality AI
├── commerce-agents/          Shopping / Food Ordering agents — and their real status
├── models-and-languages/     Which Gemini models, and text vs audio language coverage
├── evaluation-and-testing/   Test cases, metrics, simulator, personas, hill climbing
├── deployment-and-channels/  CCaaS, Twilio, Five9, AudioCodes, web widget, API
├── security-and-governance/  IAM, audit logs, VPC-SC, CMEK, residency, training-data stance
├── pricing-and-licensing/    What is public, and what is deliberately not stated
│
│  ── the motion: how a practitioner scopes, sells, and delivers ──
├── discovery/                Qualification and discovery questions by persona
├── objection-handling/       Objections and the grounded response
├── solution-plays/           The CX use cases worth leading with
├── migration-and-adoption/   Dialogflow CX / ES → CX Agent Studio, and the adoption path
├── competitive-landscape/    Honest positioning against the other contact-centre AI stacks
├── customer-stories/         Kroger, Lowe's, Woolworths, Papa Johns — what is actually claimed
└── training-and-certification/  Google Skills paths for practitioners and partners
```

Add a collection by adding a folder. No code change is needed — ingest discovers it.

The split matters for retrieval: **platform** collections answer "how does this work / can it
do X", **motion** collections answer "what do I say / what do I scope". Someone mid-call is
almost always asking the second kind, so keep motion content speakable.

## File rules

- **Markdown only.** `*.md`, any depth inside a collection.
- **Files starting with `_` and any `README.md` are skipped.** That is how `_TEMPLATE.md` and
  these instructions stay out of the corpus.
- **Headings are load-bearing.** Chunks carry their nearest heading as a title, and that title
  is what the user sees in the citation. `## Which languages does audio-to-audio actually
  support?` cites far better than `## Section 4`.
- Keep a section to roughly one idea. Chunks merge to ~220 characters and cap at ~1400, so a
  wall of text gets truncated and a one-line bullet gets merged into its neighbour.
- Put the answer in the prose. The model may not infer from a table's shape, and a bare bullet
  list with no surrounding sentence retrieves poorly.

### Disambiguate near-identical facts in the heading itself

This rule is inherited from the SMB Partner rail, where it was written after a live failure,
and GECX has its own version of the same trap. Three pairs that WILL be confused:

- **40+ languages (text) vs 10 languages (audio-to-audio).** These are different numbers for
  different modalities. A heading that just says "language support" will answer the wrong one.
- **Announced vs documented.** The NRF press release announces Commerce Agents as shipping;
  the product documentation says "Commerce agents coming soon". Both statements are real and
  they contradict each other in practice.
- **Gemini Enterprise vs Gemini Enterprise for CX.** The former is the company-wide agent
  platform; the latter is the CX-specific solution built on it. Questions about "Gemini
  Enterprise" often mean the parent, not this.

So: **name the subject and the modality in the heading**, and where two facts are confusable,
**state the other one inside both chunks**. Retrieval returns a chunk without its neighbours,
so the disambiguation has to travel with it. Assume a small model is reading — it will not
infer scope you left implicit, and it fails toward the most lexically similar chunk rather
than the most correct one.

## Sourcing

State the source and its date inside the file, using the front-matter block in `_TEMPLATE.md`.
GECX launched in January 2026 and is moving fast: models are being added, components are still
landing, and a confidently stale answer is worse than no answer, because the assistant will
repeat whatever is written here.

Two rules that already have teeth:

- **Never state a price, quota, SLA, or model limit you cannot cite.** Google has published
  almost no GECX pricing. Where that is the case, say so — "GECX pricing is not published;
  price it through your Google Cloud account team" is a genuinely useful answer, and an
  invented per-seat figure is a lost deal.
- **Mark status explicitly on every capability.** GA, Preview, Coming soon, and
  Announced-only are four different things to anyone planning a build. The `Status:` field in
  the front matter is not decorative.

## Re-ingesting

Ingest is fingerprinted per collection, so a restart only re-embeds what changed. To force a
full rebuild:

```
POST /gemini-cx/api/ingest?force=true      (admin)
```

Editing a file here requires a container rebuild to take effect, because the seed tree is
baked into the image read-only. To iterate on content without a rebuild, bind-mount this
directory over `/srv/seed/knowledge-base`.
