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
               "What is still running on the customer's own servers?",
               "Separates a genuine Azure migration from a light cloud tidy-up, and tells you "
               "whether there is Azure consumed revenue in this deal at all.",
               [("The dealer management system itself", "significant migration; possible ISV dependency"),
                ("File, print and Active Directory", "classic infrastructure lift; identity modernisation"),
                ("Line-of-business apps nobody wants to touch", "app modernisation or rehost assessment"),
                ("Very little — mostly cloud already", "no migration motion; lead with Copilot instead")]),
            _q("trigger",
               "What is forcing the change now?",
               "A migration without a trigger event slips indefinitely. The trigger also dictates "
               "your timeline and which funding or promotion applies.",
               [("Hardware refresh or end-of-life", "hard deadline; capex-to-opex argument"),
                ("Windows Server or SQL end of support", "ESU cost as the forcing function"),
                ("Opening or acquiring another site", "scale event; consolidation opportunity"),
                ("No specific trigger", "no urgency; qualify hard before investing effort")]),
            _q("sellers",
               "How many people would use AI assistance for selling?",
               "Sizes the Copilot motion and checks it against the trial and licensing thresholds "
               "that actually exist for a business of this size.",
               [("Fewer than 25", "fits a single Copilot trial cohort"),
                ("25–75", "trial then phased rollout; clear pilot boundary"),
                ("75–300", "still inside the SMB Business-family ceiling"),
                ("300 or more", "past the pooled Business cap; Enterprise licensing required")]),
            _q("decision",
               "Who actually signs this off?",
               "SMB decisions are made by very few people, and the pitch changes completely "
               "depending on whether you are talking to an owner or an incumbent IT provider.",
               [("The owner or general manager", "business-outcome pitch; avoid technical framing"),
                ("An internal IT manager", "technical credibility and migration risk are the sale"),
                ("A group CFO or finance lead", "cost, predictability and ROI framing"),
                ("An external MSP already in place", "incumbent displacement or partnering decision")]),
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
               "How many sites does the group operate?",
               "Drives both the deal size and the consolidation argument — the pain of "
               "disconnected systems scales with site count.",
               [("2–5 sites", "small group; owner-operator dynamics"),
                ("6–15 sites", "consolidation pain becoming acute"),
                ("16–50 sites", "central visibility is now a board-level problem"),
                ("50+ sites", "approaching corporate; likely managed account")]),
            _q("data",
               "How is point-of-sale and back-office data handled across sites?",
               "This is the consolidation thesis. No central visibility is the problem statement "
               "an owner will recognise instantly.",
               [("Each site standalone, no central view", "greenfield consolidation; strongest case"),
                ("Some central reporting, heavily manual", "partial; automation and reliability argument"),
                ("Central system that nobody trusts", "data quality problem, not a platform problem"),
                ("Already centralised and working", "no consolidation motion; lead elsewhere")]),
            _q("scheduling",
               "How are shifts scheduled and changes communicated?",
               "Scheduling chaos and last-minute no-shows are the operational pain that makes "
               "the frontline conversation concrete rather than abstract.",
               [("Paper or a spreadsheet", "greenfield frontline deployment"),
                ("Manager posts in a group chat", "shadow IT; no audit trail; offboarding risk"),
                ("A third-party scheduling app", "displacement or integration decision"),
                ("Integrated with the POS system", "mature; focus on the AI layer instead")]),
            _q("pain",
               "What does the owner complain about most?",
               "Lead with the owner's own words. In SMB the pitch that wins is the one that names "
               "the problem they already talk about.",
               [("Scheduling chaos and no-shows", "frontline communication and shift management"),
                ("No visibility on food or labour cost", "data consolidation and reporting"),
                ("Staff turnover and onboarding time", "onboarding, training and knowledge retention"),
                ("Compliance and record-keeping", "governance, retention and audit")]),
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
        "questions": [
            _q("size",
               "How many fee-earners and knowledge workers are there?",
               "Every seat here is a full knowledge-worker licence, so seat count drives both the "
               "deal value and whether the Business family still fits.",
               [("Fewer than 25", "small practice; Business Premium sweet spot"),
                ("25–100", "core SMB; Business Premium plus security attach"),
                ("100–300", "upper SMB; approaching the pooled Business cap"),
                ("300 or more", "past the Business-family cap; Enterprise licensing")]),
            _q("sensitivity",
               "What kind of client data does the firm hold?",
               "Sets how hard the security and governance argument lands — and whether Purview "
               "is a nice-to-have or the reason the deal closes.",
               [("General client-confidential material", "baseline confidentiality; standard protection"),
                ("Regulated financial or audit data", "regulatory retention and audit requirements"),
                ("Legally privileged material", "privilege protection; access control is critical"),
                ("Health or personal special-category data", "highest sensitivity; strict governance")]),
            _q("posture",
               "What does the firm's security posture look like today?",
               "Determines whether you are selling the security foundation or the AI layer on top "
               "of one that already exists. Selling AI onto a weak foundation is how deals unravel.",
               [("Basic email and files, no advanced security", "foundation sale; Business Premium"),
                ("Business Premium bought but not deployed", "deployment and adoption services"),
                ("Enterprise plans with some add-ons", "gap analysis; targeted add-on attach"),
                ("Comprehensive, actively managed", "mature; lead with AI governance")]),
            _q("ai_driver",
               "What is driving the AI conversation?",
               "AI interest in professional services is usually externally triggered, and the "
               "trigger tells you whether this is a real project or curiosity.",
               [("Partners are asking about Copilot", "internal curiosity; needs a structured trial"),
                ("A client asked how the firm uses AI", "client-driven; governance question first"),
                ("Competitors are marketing AI capability", "competitive pressure; time-sensitive"),
                ("Pressure to do more without more headcount", "efficiency case; measurable outcome")]),
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
STAGES: list[dict[str, str]] = [
    {"key": "analyze", "label": "Analyzing diagnostic answers…"},
    {"key": "ground", "label": "Grounding in Microsoft product and program guidance…"},
    {"key": "next_move", "label": "Determining the directional close…"},
    {"key": "scenario_card", "label": "Building the scenario card…"},
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
                out.append({"question": q["prompt"], "answer": chosen, "signal": opt["signal"]})
                break
    return out
