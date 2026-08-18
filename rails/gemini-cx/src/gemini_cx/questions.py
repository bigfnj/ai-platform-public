"""The curated question deck — the rail's front door.

A blank prompt box is a bad interface for a corpus nobody has read yet: the user does not know
what GECX *is*, so they cannot know what to ask, and their first question is usually one the
corpus cannot answer. The deck fixes that by making the corpus's own strengths clickable.

Three deliberate design rules, because this deck is content, not chrome:

1. **Every question here must be answerable from the corpus.** A deck entry that returns "the
   context does not cover this" is worse than no deck at all — it teaches the user the tool is
   broken on their very first click. ``validate()`` is the guard, and the API exposes it.
2. **Lead with the questions where GECX's own marketing is misleading.** The disambiguation
   traps (40+ vs 10 languages, announced vs documented, agentic vs assistive) are where this
   corpus earns its keep, so they are the first group rather than buried under basics.
3. **Scope each question to the collections that answer it.** Retrieval over the whole corpus
   for "how is it priced" will pull in adjacent chunks about residency; a scoped ask is sharper.
   Scoping is a hint, not a cage — a free-typed question is never scoped.
"""
from __future__ import annotations

from pathlib import Path

# Each group: id, label, icon, blurb, and its ordered questions.
# Each question: id, text, and the collections it should retrieve against.
QUESTION_GROUPS: list[dict] = [
    {
        "id": "traps",
        "label": "Get it right",
        "icon": "⚠️",
        "blurb": "Where GECX's own marketing and its documentation disagree. Start here.",
        "questions": [
            {"id": "commerce-status",
             "text": "Can I deploy the Shopping agent or Food Ordering agent today?",
             "collections": ["commerce-agents", "gecx-overview"]},
            {"id": "languages",
             "text": "How many languages does GECX support for voice versus text?",
             "collections": ["models-and-languages"]},
            {"id": "agentic",
             "text": "Is GECX actually agentic, or is it a chatbot with better marketing?",
             "collections": ["objection-handling", "competitive-landscape", "commerce-agents"]},
            {"id": "vs-parent",
             "text": "What is the difference between Gemini Enterprise and Gemini Enterprise for CX?",
             "collections": ["gecx-overview", "pricing-and-licensing"]},
            {"id": "residency-trap",
             "text": "Which GECX regions and endpoints break data residency?",
             "collections": ["security-and-governance", "deployment-and-channels"]},
        ],
    },
    {
        "id": "basics",
        "label": "What it is",
        "icon": "\U0001f9ed",
        "blurb": "The product, its four components, and how they fit together.",
        "questions": [
            {"id": "what-is-gecx",
             "text": "What is Gemini Enterprise for Customer Experience?",
             "collections": ["gecx-overview"]},
            {"id": "components",
             "text": "What are the four components of GECX and what is each one for?",
             "collections": ["gecx-overview", "cx-agent-studio", "agent-assist", "cx-insights"]},
            {"id": "launch",
             "text": "When did GECX launch and what was announced?",
             "collections": ["gecx-overview", "customer-stories"]},
            {"id": "lineage",
             "text": "How does GECX relate to Dialogflow, CCAI and the Customer Engagement Suite?",
             "collections": ["gecx-overview", "migration-and-adoption"]},
            {"id": "glossary",
             "text": "What do GECX, CES, ADK, A2A and CCaaS mean in this product?",
             "collections": ["gecx-overview"]},
        ],
    },
    {
        "id": "build",
        "label": "Building agents",
        "icon": "\U0001f6e0️",
        "blurb": "CX Agent Studio: agents, instructions, tools, guardrails.",
        "questions": [
            {"id": "root-vs-sub",
             "text": "What is the difference between a root agent and a sub-agent?",
             "collections": ["cx-agent-studio"]},
            {"id": "instructions",
             "text": "How should I write agent instructions, and what is the recommended XML structure?",
             "collections": ["cx-agent-studio"]},
            {"id": "tool-types",
             "text": "What tool types can a CX Agent Studio agent use?",
             "collections": ["cx-agent-studio"]},
            {"id": "sync-async",
             "text": "When should a tool be synchronous versus asynchronous?",
             "collections": ["cx-agent-studio"]},
            {"id": "guardrails",
             "text": "What guardrails are available and what happens when one is triggered?",
             "collections": ["cx-agent-studio", "security-and-governance"]},
            {"id": "handoff",
             "text": "How do I transfer a conversation to a human agent?",
             "collections": ["cx-agent-studio", "deployment-and-channels"]},
            {"id": "models",
             "text": "Which Gemini models can CX Agent Studio use?",
             "collections": ["models-and-languages"]},
        ],
    },
    {
        "id": "test",
        "label": "Testing & evaluation",
        "icon": "\U0001f9ea",
        "blurb": "The part teams skip, and the reason their pilots stall.",
        "questions": [
            {"id": "test-types",
             "text": "What is the difference between a Scenario and a Golden test case?",
             "collections": ["evaluation-and-testing"]},
            {"id": "metrics",
             "text": "What evaluation metrics does CX Agent Studio produce and how are they scored?",
             "collections": ["evaluation-and-testing"]},
            {"id": "replay",
             "text": "What is the difference between stable replay and naive replay?",
             "collections": ["evaluation-and-testing"]},
            {"id": "tool-fake",
             "text": "How do I run a regression suite without hitting real APIs?",
             "collections": ["evaluation-and-testing"]},
        ],
    },
    {
        "id": "deploy",
        "label": "Deploy & integrate",
        "icon": "\U0001f50c",
        "blurb": "Channels, CCaaS, telephony, and the regional limits.",
        "questions": [
            {"id": "channels",
             "text": "What channels can I deploy a CX Agent Studio agent to?",
             "collections": ["deployment-and-channels"]},
            {"id": "ccaas",
             "text": "How do I deploy an agent to Google Cloud CCaaS, and what are the prerequisites?",
             "collections": ["deployment-and-channels"]},
            {"id": "genesys",
             "text": "We run Genesys and Cisco. How does GECX connect to our telephony?",
             "collections": ["deployment-and-channels", "agent-assist"]},
            {"id": "keep-ccaas",
             "text": "Can we adopt GECX without replacing our existing contact centre?",
             "collections": ["agent-assist", "solution-plays"]},
            {"id": "voice-readiness",
             "text": "What do I need to check before promising a voice agent?",
             "collections": ["discovery", "deployment-and-channels", "models-and-languages"]},
        ],
    },
    {
        "id": "commercial",
        "label": "Money & risk",
        "icon": "\U0001f4b0",
        "blurb": "Pricing shape, what is unpublished, and the security review.",
        "questions": [
            {"id": "pricing",
             "text": "How is GECX priced?",
             "collections": ["pricing-and-licensing"]},
            {"id": "never-quote",
             "text": "Which GECX figures should I refuse to quote?",
             "collections": ["pricing-and-licensing"]},
            {"id": "training-data",
             "text": "Is our customer data used to train Google's models?",
             "collections": ["security-and-governance", "objection-handling"]},
            {"id": "security-controls",
             "text": "What security and governance controls does GECX provide?",
             "collections": ["security-and-governance"]},
        ],
    },
    {
        "id": "motion",
        "label": "Scope & sell",
        "icon": "\U0001f3af",
        "blurb": "Which play to lead with, and the objections you will meet.",
        "questions": [
            {"id": "which-play",
             "text": "Which GECX play should I lead with, and why not the exciting one?",
             "collections": ["solution-plays"]},
            {"id": "discovery",
             "text": "What discovery questions actually qualify a GECX opportunity?",
             "collections": ["discovery"]},
            {"id": "table-stakes",
             "text": "Our CCaaS vendor already does all of this. Why GECX?",
             "collections": ["objection-handling", "competitive-landscape"]},
            {"id": "where-it-wins",
             "text": "Where does GECX genuinely win, and where does it not?",
             "collections": ["competitive-landscape"]},
            {"id": "migration",
             "text": "How do we migrate from Dialogflow CX to CX Agent Studio?",
             "collections": ["migration-and-adoption"]},
            {"id": "customers",
             "text": "Who is already using GECX and what did they actually announce?",
             "collections": ["customer-stories"]},
            {"id": "training",
             "text": "How do I train a delivery team on GECX?",
             "collections": ["training-and-certification"]},
        ],
    },
]


def groups() -> list[dict]:
    """The deck as the UI consumes it."""
    return QUESTION_GROUPS


def all_questions() -> list[dict]:
    """Flat list of every question, each carrying its group id and label."""
    out: list[dict] = []
    for g in QUESTION_GROUPS:
        for q in g["questions"]:
            out.append({**q, "group": g["id"], "group_label": g["label"]})
    return out


def find(question_id: str) -> dict | None:
    """Look up one deck question by id, so the client can send an id instead of prose."""
    for q in all_questions():
        if q["id"] == question_id:
            return q
    return None


def validate(seed_dir: Path) -> list[dict]:
    """Check every question's declared collections exist on disk.

    This is design rule 1 with teeth. A deck question scoped to a collection that was renamed
    or removed retrieves NOTHING and answers "the context does not cover this" — the exact
    failure the deck exists to prevent, and one that is invisible until a user clicks it. The
    health endpoint surfaces the result so a bad deck fails loudly at boot instead of silently
    in front of someone.
    """
    present = {p.name for p in seed_dir.iterdir() if p.is_dir()} if seed_dir.is_dir() else set()
    problems: list[dict] = []
    for q in all_questions():
        missing = [c for c in q.get("collections", []) if c not in present]
        if missing:
            problems.append({"question": q["id"], "missing_collections": missing})
    return problems
