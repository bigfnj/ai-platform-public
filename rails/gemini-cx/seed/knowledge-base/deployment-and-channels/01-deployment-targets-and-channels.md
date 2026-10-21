# Where a CX Agent Studio agent can be deployed

> Source: docs.cloud.google.com/gemini-enterprise-cx/cx-agent-studio (deploy section)
> As of: 2026-08 · Verified: 2026-08-18 · Status: GA

## What channels can I deploy a CX Agent Studio agent to?

The documented platform connections are **AudioCodes**, **Five9**, **Google Cloud CCaaS**,
**Google Telephony Platform**, and **Twilio**. Beyond those, an agent can be deployed as a **web
widget**, or driven directly through **API access** for a custom front end.

Note the shape of that list: two Google-native paths (Google Cloud CCaaS, Google Telephony
Platform), three third-party voice/CCaaS partners, plus web and API. If a prospect runs Genesys,
Amazon Connect, NICE, or Cisco, there is no first-party connection listed — that traffic reaches
CX Agent Studio through a voice-infrastructure partner such as AudioCodes. See the telephony file
in this collection.

## Traffic splitting — A/B testing agents in production

**Traffic splitting** is a documented deployment capability, which means you can run two agent
versions concurrently against real traffic and compare. Combined with the versioning, changelog,
and rollback features in CX Agent Studio and the evaluation metrics, this is what allows an agent
to be improved against production behaviour rather than against a test set alone. It is also the
safe way to ship a prompt change to a high-volume line.

## Networking controls for deployment

The deploy documentation covers **inbound and outbound networking**, so an agent's egress to your
internal APIs and its ingress from a channel can both be constrained. Pair this with **VPC
Service Controls** from the security section when an agent needs to reach systems that are not
internet-facing — a tool calling an internal order API is the normal case, not the exception, and
it is the first place a deployment meets the network team.

## The web widget is the fastest path to a demo

For a proof of concept, the **web widget** removes the telephony and CCaaS integration work
entirely: the agent is embedded in a page and is talking to real users in the shortest possible
time. Use it to validate the conversation design and the tool integrations, then take the same
agent application to a voice channel once the behaviour is right. Do not use widget performance to
estimate voice latency, though — the voice path has different models and different infrastructure.
