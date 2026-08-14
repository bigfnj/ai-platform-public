# SME knowledge base — authoring contract

This directory is the rail's subject-matter corpus. Everything here is embedded at boot and
is the **only** thing the assistant is allowed to answer from.

## Layout

Every immediate subfolder is one **collection**. The folder name is the collection id; the
UI shows it title-cased, and a partner can scope a question to a subset of collections.

```
knowledge-base/
├── mcem/                 Microsoft Customer Engagement Methodology — stages, exit criteria
├── partner-programs/     Partner Center, incentives, designations, co-sell mechanics
├── solution-plays/       The SMB plays a partner actually sells
├── discovery/            Qualification + discovery questions, by persona and play
├── objection-handling/   Objections and the grounded response
└── customer-stories/     Proof points, references, win narratives
```

Add a collection by adding a folder. No code change is needed — ingest discovers it.

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

## Sourcing

State the source and its date inside the file. SMB program mechanics change every fiscal
year, and a confidently wrong incentive rate is worse than no answer — the assistant will
happily repeat whatever is written here, because that is exactly what it was told to do.

Use the front-matter block in `_TEMPLATE.md` for this.

## Re-ingesting

Ingest is fingerprinted per collection, so a restart only re-embeds what changed. To force
a full rebuild:

```
POST /smb-partner-enablement/api/ingest?force=true      (admin)
```

Editing a file under `rails/smb-partner-enablement/seed/knowledge-base/` requires a
container rebuild to take effect, because the seed tree is baked into the image read-only.
For iterating on content without a rebuild, bind-mount this directory over `/srv/seed/knowledge-base`.
