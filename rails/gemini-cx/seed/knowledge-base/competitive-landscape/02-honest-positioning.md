# Where GECX genuinely wins, and where it does not

> Source: synthesised from GECX documentation, Forrester 2026-01-20, and AudioCodes partner material
> As of: 2026-08 · Verified: 2026-08-18 · Status: practitioner judgement

## Where does GECX genuinely win?

**Cross-journey context between commerce and service.** This is the one differentiator conceded even
by a sceptical Forrester assessment: "the connection between e-commerce, customer service, and
insights." A CCaaS vendor does not own the shopping journey; a commerce platform does not own the
contact centre. GECX is designed so support interactions are "informed by historical context" from
the shopping journey.

**Voice naturalness in ten languages.** The audio-to-audio path built with Google DeepMind, with
mid-conversation language switching demonstrated between English and Mandarin without the user
signalling the change. This is a real capability gap versus transcribe-generate-synthesise
pipelines, and it demos exceptionally well.

**Evaluation as a first-class product surface.** Six defined metrics, Scenario and Golden test case
types, personas, tool faking, stable replay, and AI-generated loss reports. Many competing stacks
treat testing as an afterthought; here it is a documented phase with a composite task-completion
metric.

**Overlay adoption without replacement.** Agent Assist works over Genesys Cloud, LivePerson,
Salesforce, Twilio Flex, and custom desktops. The ability to adopt the AI layer without a platform
migration is a commercial advantage as much as a technical one.

## Where GECX does not win today

**Feature-by-feature contact-centre comparison.** Forrester: most customer-service features are
"practically table stakes for contact-center-as-a-service vendors". Do not build an evaluation
around them.

**Autonomy.** The components that execute end-to-end consented commerce actions are documented as
"coming soon". The current honest claim is assistive and task-executing, not autonomous.

**Enterprise telephony breadth.** No first-party connection is documented for Genesys, Amazon
Connect, NICE, or Cisco; those require a voice partner such as AudioCodes, which adds licensing,
SBC infrastructure, and integration work.

**Non-US, non-EU regional deployment via Google Cloud CCaaS.** Only `us` and `eu` multi-regions are
supported for that channel.

**Deep model choice.** All three supported models are Flash-class. There is no premium
reasoning-tier option, which is correct for latency-critical contact-centre work but is a genuine
limitation if a use case needs heavier reasoning.

## The positioning sentence that holds up under scrutiny

"GECX is the strongest option when the customer journey crosses commerce and service, when voice
quality in a supported language matters, and when you want to add AI over the contact centre you
already run. It is not the strongest option if you are comparing contact-centre features
line-by-line, if you need autonomous transaction completion today, or if your telephony estate is
Genesys or Cisco and you are unwilling to add a voice partner."

Saying the second half is what makes the first half believable, and it is also what stops a deal
becoming a delivery problem six months later.

## A caution on named competitors

Forrester's assessment names **no** direct contact-centre competitors. It references broader
dynamics — exclusive partnerships between commerce or payments vendors and answer engines such as
ChatGPT, Google Gemini, and Perplexity — and frames GECX as possibly "the first major moment for
customer service finding its way into answer engine shopping." Do not attribute competitive rankings
to Forrester that it did not publish; this was a blog assessment, not a Wave.
