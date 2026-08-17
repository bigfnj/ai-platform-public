"""The four SMB scenarios and their diagnostic questions.

Server-side so both surfaces and the generation pass read the same definitions — the desktop
rail, the mobile app and the package generator would otherwise drift.

Authoring rules, learned from the original prototype and the knowledge base:

* **Six to eight questions, sized to the industry.** The original prototype asked four, but four
  was a hackathon constraint rather than a finding. Depth should follow where the Microsoft surface
  area actually is: a professional-services firm has privileged data, regulatory retention and a
  real Business-Premium-versus-E3-plus-add-ons decision to surface, while a retail frontline
  rollout is comparatively clean. Forcing both to the same length either pads one or truncates the
  other. Two questions are a fixed spine (below); each scenario then adds four to six of its own.
  The ceiling is real, though — past eight the "two minutes between meetings" promise breaks, and
  Dan's framing of a partner on a phone is the whole point of the surface.
* **Every answer must change the recommendation.** A question whose answers all produce the
  same output is theatre. Each option below carries ``signal`` — the retrieval terms and
  commercial consequence it implies — which is what the generator actually consumes.
* **Ask what a partner can actually observe.** A question is only valid if a rep could answer it
  from a website, a first conversation, or Partner Center. An early draft asked "what is the split
  between frontline staff and office or knowledge workers?" — a rep does not know a customer's org
  chart before the call, and "knowledge worker" is Microsoft's vocabulary, not the customer's.
  Worse, it pushed the licensing analysis back onto the partner, which is the job this tool exists
  to do. It became "who has a work email account today?": observable, phrased as the customer
  would say it, and mapping onto the same licence-mix signal.
* **Every question offers "Not sure yet."** See ``UNKNOWN_LABEL``. An unknown is routed into the
  Discovery Playbook rather than guessed, because a package built on a wrong premise is worse than
  one that admits a gap.
* **Only ask what the corpus can act on.** Seat counts, current footprint and trigger events
  map onto real licensing and eligibility rules (the 300-seat pooled cap, the SMB designation
  track, deal-registration account management). Questions the corpus cannot ground produce
  confident invention, which is the failure mode this rail exists to avoid.
"""
from __future__ import annotations

from typing import Any

# Retrieval collections every scenario draws on, before its own additions.
_BASE_COLLECTIONS = ["smb-segment", "csp-licensing", "solution-plays"]


#: Appended to every question. A partner prepping between meetings will not know everything, and
#: a forced guess is worse than an admission — it produces a confident package built on a wrong
#: premise. Answering "not sure" routes the question into the Discovery Playbook instead, which
#: is the honest behaviour: the tool turns what you don't know into what to ask.
UNKNOWN_LABEL = "Not sure yet"
_UNKNOWN_SIGNAL = "PARTNER DOES NOT KNOW — must be established on the call, do not assume"


def _q(qid: str, prompt: str, why: str, options: list[tuple[str, str]]) -> dict[str, Any]:
    """A diagnostic question. ``options`` pairs the partner-facing label with the signal the
    generator uses — the label is for a human, the signal is for retrieval and reasoning.

    ``UNKNOWN_LABEL`` is appended automatically so no question can be authored without an out.
    """
    return {
        "id": qid,
        "prompt": prompt,
        "why": why,
        "options": [{"label": label, "signal": signal} for label, signal in options]
        + [{"label": UNKNOWN_LABEL, "signal": _UNKNOWN_SIGNAL}],
    }


def _spine() -> list[dict[str, Any]]:
    """The two questions every scenario asks, because they gate Microsoft mechanics regardless of
    industry. Both are deliberately mechanical rather than situational — situational questions
    belong to the scenario, where the options can be phrased in that industry's language.

    Headcount is here because the rail could not previously answer the most consequential SMB
    licensing question at all: the Business family caps at 300 seats *pooled*, Copilot in 30 is
    scoped to customers under 300 employees, and the trial cohort is 25 seats. Asking only about
    store locations left all of that unreachable.

    Partner-of-record is here because it redirects the entire close. If the partner already holds
    the tenant, "check Partner Center" is actionable and this is an upgrade motion; if another
    partner holds it, it is a displacement with none of the same visibility or incentives.
    """
    return [
        _q("headcount",
           "Roughly how many people work at this business?",
           "The single most consequential number in SMB licensing. The Business family caps at "
           "300 seats pooled across Basic, Standard and Premium, and the partner-led Copilot "
           "trial is scoped to customers under 300 employees — cross that line and the whole "
           "recommendation changes.",
           [("Fewer than 25", "very small; single trial cohort covers the whole business"),
            ("25–100", "core SMB; Business family fits comfortably"),
            ("100–300", "upper SMB; approaching the pooled 300-seat Business cap"),
            ("More than 300", "past the pooled Business cap; Enterprise licensing required")]),
        _q("relationship",
           "Is this already your customer?",
           "Decides whether this is an upgrade you can see in Partner Center or a win you have to "
           "take from someone else — which changes the licensing path, the incentives available, "
           "and whether you can inspect the tenant at all.",
           [("Yes — I'm their partner of record", "existing tenant visibility; upgrade motion; incentives available"),
            ("Yes, but another partner holds the tenant", "shared account; no tenant visibility; transfer question"),
            ("No — I'm trying to win them", "net-new acquisition; competitive displacement"),
            ("No — they buy direct from Microsoft today", "direct-to-CSP transition motion")]),
    ]


SCENARIOS: list[dict[str, Any]] = [
    {
        "id": "retail-chain",
        "icon": "🛍️",
        "title": "Retail Chain",
        "fit": "Teams Frontline + Copilot for Store Ops",
        "situation": (
            "Multi-location SMB retailer with frontline staff, paper schedules, and no shared "
            "communication layer across stores."
        ),
        "collections": _BASE_COLLECTIONS + ["partner-center", "designations"],
        "questions": [
            # Recovered verbatim from the original demo capture.
            _q("locations",
               "How many store locations does this retailer operate?",
               "Sets the deal size and tells you whether this is worth flagging for co-sell — "
               "and whether the customer is likely to be a Microsoft-managed account at all.",
               [("2–5 locations", "very small multi-site; unmanaged account; partner-led only"),
                ("6–15 locations", "small multi-site; still likely unmanaged"),
                ("16–50 locations", "upper SMB; possible co-sell candidate; check account management"),
                ("50+ locations", "approaching corporate segment; likely managed account; co-sell viable")]),
            _q("comms",
               "How do store staff get their schedules and communicate today?",
               "This is the pain that opens the Teams Frontline conversation. Paper and personal "
               "group chats are both an operational problem and a data-governance problem.",
               [("Paper schedules and verbal handover", "no digital layer; greenfield frontline deployment"),
                ("Personal group chat (WhatsApp, SMS)", "shadow IT; data governance and offboarding risk"),
                ("Spreadsheets emailed out", "partial digital; scheduling chaos; no real-time change"),
                ("An existing scheduling product", "displacement sale; integration and switching cost")]),
            _q("workforce",
               "Who at this business has a work email account today?",
               "This is the licence-mix question asked in a way the customer can actually answer. "
               "Staff with no work account are frontline seats, which cost far less per user — and "
               "that gap is usually where the conversation opens.",
               [("Everyone, including store staff", "all staff already licensed; upgrade not net-new seats"),
                ("Managers and head office only", "store staff unlicensed; frontline seat opportunity"),
                ("Head office only", "large unlicensed frontline population; strongest frontline case"),
                ("Almost nobody — they use personal email", "greenfield; identity and governance risk")]),
            _q("footprint",
               "What Microsoft licensing does the customer have today?",
               "Determines whether this is a new-customer acquisition, an upgrade motion, or a "
               "seat-cap problem — and which promotions the customer is actually eligible for.",
               [("Nothing — Google Workspace or nothing", "competitive displacement; new-to-Microsoft"),
                ("Office 365 only", "upgrade motion to Microsoft 365; security attach opportunity"),
                ("Microsoft 365 Business plans", "check the pooled 300-seat Business-family cap"),
                ("Microsoft 365 Enterprise plans", "already Enterprise; add-on and Copilot motion")]),
        ],
    },
    {
        "id": "auto-dealership",
        "icon": "🚗",
        "title": "Auto Dealership",
        "fit": "Azure Migration + Copilot for Sales",
        "situation": (
            "SMB dealership group moving off on-prem infrastructure, wants sellers using "
            "AI-generated cold call scripts."
        ),
        "collections": _BASE_COLLECTIONS + ["partner-center", "incentives-funding"],
        "questions": [
            _q("onprem",
               "What are they still running on their own hardware?",
               "Separates a real Azure migration from a light tidy-up, and tells you whether there "
               "is any Azure consumption in this deal at all.",
               [("The dealer management system", "significant migration; likely ISV dependency"),
                ("Email or file shares on a server in the office", "infrastructure lift; identity modernisation"),
                ("Old software nobody wants to touch", "app modernisation or rehost assessment"),
                ("Not much — they are mostly cloud already", "no migration motion; lead with Copilot instead")]),
            _q("trigger",
               "What is forcing the change now?",
               "A migration with no trigger slips forever. The trigger also sets your timeline and "
               "decides which promotion or funding window is even open.",
               [("Hardware is failing or out of warranty", "hard deadline; capex-to-opex argument"),
                ("Windows Server or SQL support is ending", "support-cost pressure as the forcing function"),
                ("They are opening or buying another site", "scale event; consolidation opportunity"),
                ("Nothing specific yet", "no urgency; qualify hard before investing effort")]),
            # Replaced "how many people would use AI assistance for selling?" — the spine now asks
            # headcount, and a rep cannot split a customer's staff by future licence intent anyway.
            # What they CAN observe is whether a CRM exists, which is what Copilot for Sales needs.
            _q("crm",
               "How do the salespeople track their customers today?",
               "Copilot for Sales lands on top of a CRM. If there isn't one — or nobody updates it "
               "— that is a different, longer conversation, and promising AI on absent data is how "
               "these deals unravel after signature.",
               [("Paper, whiteboards or memory", "no system of record; CRM foundation needed first"),
                ("Only inside the dealer management system", "DMS-bound data; integration question"),
                ("A CRM nobody keeps up to date", "adoption problem, not a tooling problem"),
                ("A CRM they genuinely use", "clean ground for Copilot for Sales")]),
            _q("itowner",
               "Who looks after their IT?",
               "Decides who you are actually selling to, and whether there is an incumbent in the "
               "room whose position you are threatening.",
               [("Nobody really — the manager sorts it out", "no technical buyer; lead on business outcome"),
                ("One person internally", "single technical gatekeeper; credibility matters"),
                ("An outside IT company", "incumbent MSP; displacement or partnering decision"),
                ("A group IT team", "process-driven; expect standards and security review")]),
            _q("decision",
               "Who signs off spending like this?",
               "SMB decisions are made by very few people, and the pitch changes completely "
               "depending on which of them you are in front of.",
               [("The owner", "business-outcome pitch; avoid technical framing"),
                ("The general manager", "operational efficiency framing"),
                ("A group finance lead", "cost, predictability and payback framing"),
                ("Nobody has been identified yet", "no buyer; qualification gap to close on the call")]),
        ],
    },
    {
        "id": "restaurant-group",
        "icon": "🍽️",
        "title": "Restaurant Group",
        "fit": "Azure Consolidation + Copilot for Frontline Managers",
        "situation": (
            "Multi-location SMB restaurant group with disconnected POS systems, paper "
            "scheduling, and no centralized data visibility."
        ),
        "collections": _BASE_COLLECTIONS + ["partner-center"],
        "questions": [
            _q("sites",
               "How many sites does the group run?",
               "Drives the deal size and the consolidation argument — the pain of disconnected "
               "systems scales with every site added.",
               [("2–5 sites", "small group; owner-operator dynamics"),
                ("6–15 sites", "consolidation pain becoming acute"),
                ("16–50 sites", "central visibility is now a leadership problem"),
                ("More than 50 sites", "approaching corporate; likely managed account")]),
            _q("pain",
               "What does the owner complain about most?",
               "Lead with the owner's own words. In SMB the pitch that wins is the one that names "
               "the problem they already talk about unprompted.",
               [("Scheduling chaos and no-shows", "frontline communication and shift management"),
                ("No idea what food or labour actually costs", "data consolidation and reporting"),
                ("Staff turnover and time lost training", "onboarding and knowledge retention"),
                ("Compliance and paperwork", "governance, retention and audit")]),
            # Replaced "how is point-of-sale and back-office data handled across sites?" — that is
            # an architecture question. This asks the same thing as an outcome the owner can answer
            # instantly, which is the whole consolidation thesis in one sentence.
            _q("visibility",
               "Can head office see last night's numbers this morning?",
               "This is the consolidation question asked the way an owner will actually answer it. "
               "A 'no' is the entire business case, and you will hear the frustration in the reply.",
               [("No — someone compiles it weekly", "greenfield consolidation; strongest case"),
                ("Only site by site, never combined", "partial; aggregation is the gap"),
                ("Yes, but nobody trusts the numbers", "data quality problem, not a platform problem"),
                ("Yes, reliably", "no consolidation motion; lead elsewhere")]),
            _q("scheduling",
               "How do staff find out when they are working?",
               "Scheduling chaos and last-minute no-shows are the operational pain that makes the "
               "frontline conversation concrete instead of abstract.",
               [("A printed rota on the wall", "no digital layer; greenfield frontline deployment"),
                ("The manager texts or messages them", "shadow IT; no audit trail; offboarding risk"),
                ("A scheduling app", "displacement or integration decision"),
                ("Through the till or POS system", "mature; focus on the AI layer instead")]),
            _q("managers",
               "Do the site managers have a work email account and a computer?",
               "Decides the licence mix. Managers with no work account are frontline seats at a far "
               "lower cost per user, and if nobody has one you are also solving an identity and "
               "offboarding problem the owner has not thought about yet.",
               [("No — they use personal phones and email", "greenfield; identity and governance risk"),
                ("Managers do, floor staff do not", "mixed estate; frontline seats for staff"),
                ("Everyone has a work account", "all licensed; upgrade rather than net-new seats"),
                ("Only head office has them", "large unlicensed population; strongest frontline case")]),
        ],
    },
    {
        "id": "professional-services",
        "icon": "💼",
        "title": "Professional Services",
        "fit": "M365 Security + Copilot for Knowledge Work",
        "situation": (
            "Accounting, legal, or consulting firm handling sensitive client data with security "
            "gaps and partners asking about AI."
        ),
        "collections": _BASE_COLLECTIONS + ["designations", "program-updates"],
        # Eight questions — the most of any scenario, and deliberately so. This is where the real
        # Microsoft depth lives: regulated data, privilege, the Purview and Defender attach, and a
        # genuine Business-Premium-versus-Enterprise-plus-add-ons decision. Truncating it to match
        # the retail flow would waste the part of the corpus that differentiates hardest.
        "questions": [
            # "How many fee-earners?" was removed — the spine now asks headcount, and duplicating
            # it spent a slot for nothing.
            _q("sensitivity",
               "What kind of client information do they hold?",
               "Sets how hard the governance argument lands — and whether data protection is a "
               "nice-to-have or the actual reason the deal closes.",
               [("General commercial and contact data", "baseline confidentiality; standard protection"),
                ("Regulated financial or audit records", "regulatory retention and audit requirements"),
                ("Legally privileged material", "privilege protection; access control is critical"),
                ("Health or other special-category personal data", "highest sensitivity; strict governance")]),
            _q("client_pressure",
               "Has a client asked them how they handle data or AI?",
               "The most useful question on this list. An external ask is what turns governance "
               "from an IT preference into a budget line with a date on it.",
               [("Yes — they were sent a security questionnaire", "client-driven; compliance evidence needed"),
                ("Yes — a client asked about AI specifically", "AI governance is the opening, not productivity"),
                ("Not yet, but they expect it", "pre-emptive; positioning opportunity"),
                ("No", "no external forcing function; build the case yourself")]),
            _q("files",
               "Where do client files live today?",
               "Tells you whether there is a single place to protect or a sprawl to consolidate "
               "first. You cannot govern or apply AI to what you cannot see.",
               [("A server in the office", "on-premises; migration precedes governance"),
                ("SharePoint or OneDrive", "already in tenant; governance and labelling motion"),
                ("Dropbox, Google Drive or similar", "third-party sprawl; consolidation case"),
                ("Scattered across several of these", "sprawl; discovery before anything else")]),
            _q("mfa",
               "Is multi-factor authentication switched on for everyone?",
               "The single most telling security signal a partner can actually ask about, and one "
               "the customer can answer immediately. A 'no' or 'not sure' reframes the whole "
               "conversation — it is a foundation problem, not an AI opportunity.",
               [("Yes, for everyone", "baseline in place; build upward"),
                ("Only for partners or leadership", "partial coverage; the gap is the risk"),
                ("No", "foundational gap; lead here, not with AI"),
                ("They think so but nobody has checked", "unverified; assessment is the first engagement")]),
            _q("billing",
               "How do they track time and bill clients?",
               "In a fee-earning business this is where AI time-savings become money. It is also "
               "the number you will need later to build any credible business case.",
               [("Spreadsheets", "manual; clear automation opportunity"),
                ("A practice management system", "system of record exists; integration question"),
                ("A dedicated time-tracking tool", "measurable baseline available"),
                ("Written up at the end of the month", "recovery leakage; strong efficiency argument")]),
            _q("ai_driver",
               "What is driving the AI conversation?",
               "AI interest in professional services is usually triggered from outside, and the "
               "trigger tells you whether this is a real project or a curiosity.",
               [("Partners are asking about Copilot", "internal curiosity; needs a structured trial"),
                ("A client asked how the firm uses AI", "client-driven; governance question first"),
                ("Competitors are marketing AI capability", "competitive pressure; time-sensitive"),
                ("Pressure to do more without hiring", "efficiency case; measurable outcome")]),
        ],
    },
]

# Prepended once here rather than repeated in every literal, so the spine cannot drift between
# scenarios and a change to it lands everywhere at the same time. Asked first because headcount and
# the existing relationship are what a partner reaches for before industry detail.
for _scenario in SCENARIOS:
    _scenario["questions"] = _spine() + _scenario["questions"]

SCENARIOS_BY_ID: dict[str, dict[str, Any]] = {s["id"]: s for s in SCENARIOS}

# The generation stages. Each is a REAL grounded pass, not decoration — the UI shows these
# ticking through, and the original prototype's checklist did the same. The label is what a
# partner sees; ``key`` names the output it produces.
#: Order matches execution order, so the checklist never shows a later line finishing first. The
#: scenario card sits early because it is assembled deterministically rather than generated — it
#: completes almost instantly, which is itself worth seeing.
STAGES: list[dict[str, str]] = [
    {"key": "analyze", "label": "Analyzing diagnostic answers…"},
    {"key": "ground", "label": "Grounding in Microsoft product and program guidance…"},
    {"key": "scenario_card", "label": "Assembling the scenario card…"},
    {"key": "next_move", "label": "Determining the directional close…"},
    {"key": "discovery", "label": "Generating discovery playbook…"},
    {"key": "customer_qa", "label": "Building customer Q&A pack…"},
    {"key": "roi", "label": "Drafting the value summary…"},
]


def public_view() -> list[dict[str, Any]]:
    """Scenarios as the clients need them — option signals stripped, since they are generator
    input rather than something a partner should read on screen."""
    return [
        {
            "id": s["id"], "icon": s["icon"], "title": s["title"],
            "fit": s["fit"], "situation": s["situation"],
            "questions": [
                {"id": q["id"], "prompt": q["prompt"], "why": q["why"],
                 "options": [o["label"] for o in q["options"]]}
                for q in s["questions"]
            ],
        }
        for s in SCENARIOS
    ]


def resolve_answers(scenario_id: str, answers: dict[str, str]) -> list[dict[str, str]]:
    """Pair each answered question with the signal its chosen option carries.

    Unknown question ids and unrecognised labels are dropped rather than raised: a client on a
    stale bundle should get a slightly thinner package, not a 500.
    """
    scenario = SCENARIOS_BY_ID.get(scenario_id)
    if scenario is None:
        return []
    out: list[dict[str, str]] = []
    for q in scenario["questions"]:
        chosen = answers.get(q["id"])
        if not chosen:
            continue
        for opt in q["options"]:
            if opt["label"] == chosen:
                # ``id`` rides along so downstream code can key off the question rather than
                # matching its prompt text, which changes whenever the wording is improved.
                out.append({"id": q["id"], "question": q["prompt"],
                            "answer": chosen, "signal": opt["signal"]})
                break
    return out
