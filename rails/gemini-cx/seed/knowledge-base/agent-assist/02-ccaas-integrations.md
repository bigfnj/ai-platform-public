# Which contact-centre platforms Agent Assist integrates with

> Source: docs.cloud.google.com/gemini-enterprise-cx/agent-assist
> As of: 2026-08 · Verified: 2026-08-18 · Status: GA

## Does Agent Assist work with my existing contact centre?

Very likely, and this is the part of GECX that does not require replacing your CCaaS. Agent Assist
has documented integrations with **Genesys Cloud** (voice via **AudioHook**, plus chat),
**LivePerson** (application and proxy-server deployment), **Salesforce** (a native integration
plus open-source variants), **Twilio** (**Flex** and **SIPREC**), **Dialogflow CX and ES**
(conversation handoff), and **CX Agent Studio**.

Both **voice/telephony** and **chat** channels are supported, with platform-specific integration
patterns rather than one universal connector. Genesys voice goes through AudioHook; Twilio voice
can arrive via SIPREC. Confirm the pattern for your specific platform and channel rather than
assuming parity.

## The commercial significance of the integration list

Agent Assist is an overlay, not a migration. An organisation can keep Genesys, LivePerson, Twilio
Flex, or Salesforce as the agent desktop and telephony platform and still adopt Google's AI layer
on top. That makes Agent Assist a far shorter sales cycle and a far lower-risk first project than
replacing containment infrastructure, and it is usually the right entry point into GECX for an
established contact centre.

## Integrating with a custom or in-house agent desktop

If the desktop is home-grown, Agent Assist still fits. The documentation covers **backend module
integration** for custom agent desktop systems, plus **custom events** and **UI module
connectors** for extensibility, and **bidirectional and extended streaming APIs** for custom
integrations. Client libraries are published for Python, Node.js, Java, Go, C++, C#, PHP, and
Ruby.

## Operational plumbing worth knowing early

**Cloud Pub/Sub notifications** stream real-time events, which is how you get suggestions and
transcription into your own systems. **CMEK** is supported for encryption with customer-managed
keys. **Speech-to-Text model adaptation** improves accuracy for domain-specific vocabulary — worth
budgeting for in any deployment with product names, drug names, or part numbers, because generic
recognition will mangle them. A **simulator** is available for testing configurations before they
go live.
