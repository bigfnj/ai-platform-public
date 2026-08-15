# SME knowledge base — authoring contract

This directory is the rail's subject-matter corpus. Everything here is embedded at boot and
is the **only** thing the assistant is allowed to answer from.

## Layout

Every immediate subfolder is one **collection**. The folder name is the collection id; the
UI shows it title-cased, and a partner can scope a question to a subset of collections.

```
knowledge-base/
│  ── the program: how Microsoft's partner ecosystem is structured ──
├── program-structure/    MAICPP overview, history (MPN→MCPP→MAICPP), membership, enrollment
├── designations/         Solutions Partner designations, capability score, specializations
├── partner-center/       Partner Center ops: deal registration, referrals, co-sell, marketplace
├── csp-licensing/        CSP models, New Commerce, the SMB licensing families
├── incentives-funding/   Incentives, co-op funds, payouts
│
│  ── the motion: how a partner actually sells ──
├── smb-segment/          Segment definition, partner-led economics, the SMB buyer
├── mcem/                 Microsoft Customer Engagement Methodology — stages, exit criteria
├── solution-plays/       The SMB plays a partner actually sells
├── discovery/            Qualification + discovery questions, by persona and play
├── objection-handling/   Objections and the grounded response
└── customer-stories/     Proof points, references, win narratives
```

Add a collection by adding a folder. No code change is needed — ingest discovers it.

The split matters for retrieval: **program** collections answer "how does this work / am I
eligible", **motion** collections answer "what do I say / what do I sell". A partner mid-call
is almost always asking the second kind, so keep motion content speakable.

## File rules

- **Markdown only.** `*.md`, any depth inside a collection.
- **Files starting with `_` and any `README.md` are skipped.** That is how `_TEMPLATE.md`
  and these instructions stay out of the corpus.
- **Headings are load-bearing.** Chunks carry their nearest heading as a title, and that
  title is what a partner sees in the citation. `## Objection: "We already have Google
  Workspace"` cites far better than `## Section 4`.
- Keep a section to roughly one idea. Chunks merge to ~220 characters and cap at ~1400, so
  a wall of text gets truncated and a one-line bullet gets merged into its neighbour.
- Put the answer in the prose. The model may not infer from a table's shape, and a bare
  bullet list with no surrounding sentence retrieves poorly.

### Disambiguate near-identical policies in the heading itself

This rule was written after a live failure, and it is the most important one here.

Microsoft has many rules that sound alike but govern different transactions. A heading of
`## Can I get a refund if I renew or purchase by mistake?` retrieved strongly for "how long do
I have to cancel an annual subscription I sold by mistake" — and the assistant confidently
answered **30 days** (the MAICPP membership refund window) when the correct answer was **7
calendar days** (the New Commerce cancellation window). A partner acting on that loses a year
of licence cost.

The chunk was not wrong; it was unscoped. So:

- **Name the subject in the heading.** "…my own MAICPP membership offer" beats "…a purchase".
- **Where two rules are confusable, say so inside both chunks** and state the other rule's
  answer. Retrieval returns a chunk without its neighbours, so the disambiguation has to
  travel with it.
- Assume a small model is reading. It will not infer scope you left implicit, and it fails
  toward the most lexically similar chunk rather than the most correct one.

## Sourcing

State the source and its date inside the file. SMB program mechanics change every fiscal
year, and a confidently wrong incentive rate is worse than no answer — the assistant will
happily repeat whatever is written here, because that is exactly what it was told to do.

Use the front-matter block in `_TEMPLATE.md` for this.

Two rules that have already earned their place:

- **Never state a rate, margin, threshold or fee you cannot cite.** Much of the incentive
  detail is partner-confidential and lives only in the Partner Center rate card. Where that
  is the case, say so — "check the current rate card in Partner Center" is a genuinely useful
  answer; an invented percentage is a lost deal.
- **This repo is public.** Only publicly available material belongs here. The internal
  rebuild sources live in `../../research/` and `../../documents/`, which are gitignored;
  nothing from them should be copied into this directory verbatim.

## Re-ingesting

Ingest is fingerprinted per collection, so a restart only re-embeds what changed. To force
a full rebuild:

```
POST /smb-partner-enablement/api/ingest?force=true      (admin)
```

Editing a file under `rails/smb-partner-enablement/seed/knowledge-base/` requires a
container rebuild to take effect, because the seed tree is baked into the image read-only.
For iterating on content without a rebuild, bind-mount this directory over `/srv/seed/knowledge-base`.
