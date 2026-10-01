# Objection: "We cannot send customer conversations to Google"

> Source: NRF launch release 2026-01-11; data residency and CCaaS deployment documentation
> As of: 2026-08 · Verified: 2026-08-18 · Status: practitioner guidance — verify contractually

## Objection: "We cannot send customer conversations to Google"

Separate the objection into its three real components, because they have three different answers
and treating it as one blocker loses a deal that was winnable.

**"You will train on our data."** Google's stated position at launch is that **customer data is not
used for model training**, alongside built-in brand policy and legal compliance mechanisms. Offer
this as Google's stated position and then point at the contract — the Google Cloud agreement and
current Service Specific Terms are the binding version. Do not present a press-release sentence as
a contractual guarantee; the customer's legal team will check, and being the person who overstated
it is expensive.

**"The data will leave our jurisdiction."** Data you store remains physically in the Google Cloud
location you chose, and **jurisdictional endpoints** keep ML processing inside a named geography
such as the US or the EU. But be straight about two limits: **global endpoints do not support data
residency requirements**, and **Google Cloud CCaaS deployment supports only the `us` and `eu`
multi-regions**. If they need in-country handling outside the US or EU, the CCaaS channel is not
available today as documented and you need a different channel or an exception.

**"We do not control the keys."** **CMEK** is supported across CX Agent Studio, Agent Assist, and CX
Insights, so encryption keys can be customer-managed. Combined with **VPC Service Controls**, **IAM**,
**audit logging**, **fine-grained access control** and **authorized views** in CX Insights, the
control surface is the standard Google Cloud enterprise set rather than something they have to
evaluate from scratch.

## The reframe that usually works

Most of this objection is a Google Cloud objection, not a GECX objection. If the organisation
already runs approved workloads on Google Cloud, the incremental question is narrow: conversation
transcripts are a more sensitive data class than most, and they concentrate in CX Insights. Scope
the review there and the conversation gets much shorter.

If they run nothing on Google Cloud, accept that this is a platform decision above the CX project
and do not try to win it on CX merits alone.

## Where to be genuinely cautious

Transcripts contain whatever the customer said out loud — card numbers read aloud, health
details, complaints about named staff — none of which a structured form would have captured. That is
a real risk and the customer is right to probe it. Bring **data retention policies**, **authorized
views**, and redaction requirements into the design rather than treating the objection as friction
to be overcome. A customer who sees you take this seriously stops fighting you on it.
