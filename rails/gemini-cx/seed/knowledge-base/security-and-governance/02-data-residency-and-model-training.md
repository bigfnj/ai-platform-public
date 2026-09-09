# Data residency and the model-training question

> Source: NRF launch release 2026-01-11; docs.cloud.google.com/gemini-enterprise-agent-platform/resources/data-residency; CCaaS deploy docs
> As of: 2026-08 · Verified: 2026-08-18 · Status: GA · verify contractually before quoting

## Is my customer data used to train Google's models?

Google stated at the January 2026 launch that **customer data is not used for model training**, and
that GECX includes **built-in brand policy and legal compliance mechanisms**. Both are launch
communications rather than contract language. Repeat them as Google's stated position, and point
the customer at their Google Cloud agreement and the current **Service Specific Terms** for the
binding version. Presenting a press-release sentence as a contractual guarantee is how a security
review goes badly later.

## Where does the data physically live?

On the Gemini Enterprise Agent Platform, data you store — such as custom model weights or metadata
— "remains physically stored in the specific Google Cloud location you chose", and that residency
holds regardless of which endpoint you use. **Jurisdictional endpoints** keep ML processing within a
specific geography, such as the United States or the European Union.

## The global endpoint does not support data residency — this is the trap

Models and generative AI features are exposed as **regional endpoints and a global endpoint**. The
global endpoint covers the entire world and offers higher availability and reliability than a
single region. But **global endpoints have a separate set of quotas from regional endpoints and do
not support data residency requirements.**

That is the sentence to carry into an architecture review. The endpoint that gives you the best
availability is the one that voids your residency position. A team optimising for uptime can
silently undo the compliance commitment the deal was won on, because both choices look like
sensible engineering in isolation.

## Two different regional constraints — do not merge them

These are separate limits and they are often confused. **Google Cloud CCaaS deployment of a CX
Agent Studio agent supports only the `us` and `eu` multi-regions** — that is a channel
availability limit. **Model endpoint residency** is a separate question about where inference
happens and where data rests, governed by regional versus global endpoints and jurisdictional
endpoints. An organisation can satisfy one and fail the other, so check both against the
requirement rather than treating "it's available in the EU" as a single answer.

## Practical residency checklist for a GECX deployment

Confirm the channel is available in the required region — for Google Cloud CCaaS that means `us` or
`eu` only. Confirm whether the deployment uses a regional or jurisdictional endpoint, and
explicitly rule out the global endpoint if residency is required. Confirm the CX Insights storage
location and its retention policy, since that is where transcripts accumulate. Confirm CMEK key
location and ownership. Then get the model-training position in writing from the contract rather
than from marketing.
