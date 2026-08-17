"""Scenario package generation — the four-question diagnostic turned into a meeting kit.

Each output is a SEPARATE grounded pass with its own retrieval, rather than one call producing
everything. Three reasons, in order of importance:

1. **Quality.** The rail's model is 3B-class (it has to stay resident beside the embedder). Asking
   it for five distinct artifacts in one response produces five mediocre ones. Asking it for one
   thing, with context retrieved for that one thing, produces something usable.
2. **Honest progress.** The original prototype showed a checklist ticking through while the
   package built. Here each line of that checklist IS a pass, so the UI reports real work rather
   than animating a timer.
3. **Partial failure.** If the Q&A pass fails, the partner still gets the scenario card and the
   directional close instead of an error page.

**No invented numbers.** The ROI pass is deliberately prompted for a qualitative value case and
is forbidden from producing percentages or currency figures. The original prototype's ROI tiles
were 3B-model output and one was visibly broken ("3" under a label reading "$,000 - $5,500").
Any figure a partner repeats to a customer must come from the corpus, where it is sourced and
dated, or from the partner's own inputs.
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Awaitable, Callable

from smb_partner import broker, config, rag, scenarios, store

log = logging.getLogger("smb_partner.generate")

Emit = Callable[[str, dict[str, Any]], Awaitable[None]]

# A 3B model reliably opens by restating its input. These are the shapes it produces; they are
# stripped rather than prompted away, because prompting alone did not hold.
_PREAMBLE = re.compile(
    r"^\s*(#{1,4}\s*)?(diagnostic answers?|scenario summary|current state|customer summary|"
    r"summary|analysis|overview|introduction)\b.*?(?=\n#{1,4}\s|\Z)",
    re.I | re.S)

# Any figure a partner might repeat: percentages, currency amounts, and bare multipliers.
_NUMERIC = re.compile(r"(?<![\w.])(?:\d[\d,]*\.?\d*\s*%|[$£€]\s?\d[\d,]*\.?\d*|"
                      r"\d[\d,]*\.?\d*\s*(?:x|times)\b)", re.I)


def _strip_preamble(text: str) -> str:
    """Drop a leading restatement block. Only removes a *leading* section whose heading is one of
    the known restatement shapes, so genuine content is never touched."""
    cleaned = _PREAMBLE.sub("", text or "", count=1).strip()
    return cleaned or (text or "").strip()


def _scrub_invented_numbers(text: str, context: str) -> tuple[str, list[str]]:
    """Remove sentences containing a figure that does not appear in the retrieved context.

    The rail's promise is that no number reaches a partner unless it is sourced. Prompting the
    model not to invent figures does not hold at 3B — it turned "mostly frontline, a small head
    office" into "90% frontline, 10% office" on the first run. So the guard is mechanical:
    a figure survives only if the grounding context actually contains it.
    """
    removed: list[str] = []
    kept: list[str] = []
    for sentence in re.split(r"(?<=[.!?])\s+", text or ""):
        figures = _NUMERIC.findall(sentence)
        unsupported = [f for f in figures if f.strip() not in context]
        if unsupported:
            removed.append(sentence.strip())
            continue
        kept.append(sentence)
    return " ".join(kept).strip(), removed

# Per-pass generation budgets. The directional close is the headline output and gets room; the
# supporting tabs are deliberately tight, because a partner skims them before a meeting.
_BUDGETS = {"next_move": 320, "scenario_card": 520, "discovery": 420,
            "customer_qa": 460, "roi": 380}

_BASE_RULES = (
    "You are briefing a Microsoft SMB partner before a customer meeting. Use ONLY the provided "
    "context. If the context does not support a claim, leave it out — do not fill gaps from "
    "general knowledge. Never state a price, discount, margin, incentive rate or percentage "
    "unless it appears verbatim in the context. Be concrete and commercial; the reader is "
    "selling, not studying. Write plain markdown with short paragraphs.\n\n"
    "CRITICAL: do NOT restate, summarise or list the customer's situation or the diagnostic "
    "answers back to the reader. They already know them. Produce ONLY what you are asked for, "
    "starting immediately with the requested content and no introduction.\n\n"
    "Never contradict what the partner told you, and never assert a fact about the customer that "
    "the brief does not state. If the brief says something is not yet known, treat it as unknown — "
    "do not infer it from the scenario description."
)

# Low temperature: these are briefing artifacts, not creative writing, and a 3B model drifts
# off-instruction quickly at default sampling.
_OPTIONS_BASE = {"temperature": 0.2, "top_p": 0.9}


#: Hard rules derived deterministically from an answer, keyed by ``(question id, answer label)``.
#:
#: These are constraints, not hints. An A/B run over identical retailers differing only in
#: headcount showed the model surfacing the 300-seat cap in the scenario card while its directional
#: close still recommended "upgrading to Business Premium" for a 300+ seat customer — advice that
#: cannot be executed. A 3B model will not reliably derive a hard limit from retrieved prose, so
#: the limit is computed here and handed over as something it may not contradict.
#:
#: Only rules the knowledge base actually supports belong here. Each maps to sourced material:
#: the pooled Business-family cap, the sub-300-employee scope of the partner-led Copilot trial,
#: and the Microsoft-managed-account requirement for co-sell deal registration.
_HARD_RULES: dict[tuple[str, str], str] = {
    ("headcount", "More than 300"):
        "This customer is PAST the pooled 300-seat Microsoft 365 Business-family cap. Business "
        "Basic, Business Standard and Business Premium are NOT viable as the primary plan — "
        "Enterprise licensing (E3/E5) is required. Do not recommend a Business-family plan as the "
        "answer, and do not offer the partner-led Copilot trial, which is scoped to customers "
        "under 300 employees.",
    ("headcount", "Fewer than 25"):
        "The entire business fits inside a single 25-seat Copilot trial cohort, so a trial can "
        "cover everyone rather than a pilot group.",
    ("relationship", "Yes, but another partner holds the tenant"):
        "You do NOT have tenant visibility and cannot inspect or transact on this tenant. Any "
        "advice that depends on reading Partner Center data is not executable until the partner "
        "relationship changes — lead with that, not with a product recommendation.",
    ("relationship", "No — I'm trying to win them"):
        "This is net-new. You have no tenant visibility and no existing incentive position, so "
        "do not describe this as an upgrade or renewal motion.",
    # Deal registration requires a Microsoft-managed account, and smaller customers usually are
    # not managed. Stated unconditionally at the small end because it is the single most useful
    # correction the tool can make — a partner chasing an unavailable registration wastes the deal.
    ("locations", "2–5 locations"):
        "A customer this small is very unlikely to be a Microsoft-managed account, so Azure IP "
        "co-sell deal registration is probably NOT available. Do not recommend registering the "
        "deal; give the partner-led path instead.",
    ("locations", "6–15 locations"):
        "A customer this size is more likely unmanaged than managed, so co-sell deal registration "
        "may well be unavailable. Tell the partner to confirm managed status before relying on it.",
    ("sites", "2–5 sites"):
        "A customer this small is very unlikely to be a Microsoft-managed account, so Azure IP "
        "co-sell deal registration is probably NOT available. Give the partner-led path instead.",
    ("sites", "More than 50 sites"):
        "A group this size is likely a Microsoft-managed account, so co-sell and deal registration "
        "are plausibly available — but confirm managed status in Partner Center rather than "
        "assuming it.",
    # --- foundation-before-AI rules -------------------------------------------------------------
    # These exist because the corpus is explicit that Microsoft positions security as the
    # prerequisite for AI readiness in SMB, and because selling Copilot onto a broken foundation is
    # how these deals unravel after signature.
    ("mfa", "No"):
        "Multi-factor authentication is NOT deployed. This is a foundational security gap. Lead "
        "with the security foundation — Microsoft positions security as the prerequisite for AI "
        "readiness — and do NOT make Copilot the headline recommendation for this customer.",
    ("mfa", "They think so but nobody has checked"):
        "Security posture is unverified. Recommend an assessment as the first engagement rather "
        "than a product, because every later recommendation depends on what it finds.",
    ("crm", "Paper, whiteboards or memory"):
        "There is NO system of record for customer data. Copilot for Sales works on top of CRM "
        "data, so it cannot deliver value here yet. Do not recommend Copilot for Sales as the "
        "answer — the CRM foundation comes first.",
    ("crm", "A CRM nobody keeps up to date"):
        "The CRM exists but is not maintained, so this is an adoption problem rather than a tooling "
        "problem. AI over stale data will underperform and damage trust; address adoption first.",
    ("files", "Scattered across several of these"):
        "Client files are spread across multiple unmanaged locations. Governance and AI both "
        "require knowing where data is, so discovery and consolidation precede any Copilot or "
        "Purview recommendation.",
    ("itowner", "An outside IT company"):
        "An incumbent IT provider holds this account. Any recommendation has to account for them — "
        "either partner with them or displace them deliberately — and they will likely be in the "
        "room for the technical decision.",
    ("decision", "Nobody has been identified yet"):
        "No buying decision-maker has been identified. This is a qualification gap: the immediate "
        "next move is to find who signs, not to propose a solution.",
}


def _constraints(resolved: list[dict[str, str]], scenario_id: str) -> list[str]:
    """Collect the hard rules triggered by this set of answers."""
    out: list[str] = []
    for item in resolved:
        rule = _HARD_RULES.get((item.get("id", ""), item["answer"]))
        if rule and rule not in out:
            out.append(rule)
    return out


#: Licensing path implied by headcount. Derived, not generated: the mapping is a published rule
#: (the pooled 300-seat Business-family cap) and a model has no business re-deciding it per run.
_LICENCE_PATH: dict[str, str] = {
    "More than 300":
        "**Enterprise licensing (E3 or E5).** This customer is past the pooled 300-seat cap that "
        "applies across Business Basic, Standard and Premium combined, so the Business family "
        "cannot be the primary plan. The partner-led Copilot trial does not apply either — it is "
        "scoped to customers under 300 employees.",
    "100–300":
        "**Business family, but plan for the ceiling.** Business Premium fits today, though the "
        "300-seat cap is pooled across all Business plans and this customer is close enough to it "
        "that growth should be part of the conversation now rather than at renewal.",
    "25–100":
        "**Business Premium is the natural fit** — it carries the security and compliance layer "
        "Microsoft positions as the prerequisite for AI adoption, and it sits inside the "
        "300-seat Business-family cap with room to grow.",
    "Fewer than 25":
        "**Business Premium, and the whole business fits one trial.** The partner-led Copilot "
        "trial covers 25 seats, so at this size a trial can include everyone rather than a "
        "selected pilot group.",
}

#: Frontline signal → licence-mix note. Keyed on the answers that reveal unlicensed staff.
_FRONTLINE_NOTE: dict[str, str] = {
    "Managers and head office only":
        "Store staff have no work account today, so they are frontline seats — materially cheaper "
        "per user than a full knowledge-worker licence. That gap is usually where the commercial "
        "conversation opens.",
    "Head office only":
        "Almost the entire workforce is unlicensed, which is the largest frontline seat "
        "opportunity of any answer here.",
    "Almost nobody — they use personal email":
        "Nobody has a work account. Alongside the licensing opportunity this is an identity and "
        "offboarding risk the customer has probably not considered.",
    "No — they use personal phones and email":
        "Site managers have no work account. Alongside the licensing opportunity this is an "
        "identity and offboarding risk worth naming.",
    "Managers do, floor staff do not":
        "Floor staff are unlicensed and are frontline seats; managers are the knowledge-worker "
        "population.",
    "Only head office has them":
        "Almost the entire workforce is unlicensed — the strongest frontline case available.",
}


def _build_scenario_card(scenario: dict[str, Any], resolved: list[dict[str, str]],
                         constraints: list[str]) -> str:
    """Assemble the Scenario Card in code rather than generating it.

    This was a model pass and it was the weakest output in the package: asked for a card, a 3B
    model bullet-listed the diagnostic answers back at the partner — "16–50 locations", "25–100
    employees", "Upgrade motion" — which is information they had just typed in. It also, on one
    run, restated a customer's licensing position as the opposite of what the answers said.

    Nothing in this card requires judgement. The profile is the answers, the ruled-out list is the
    deterministic constraint table, and the licensing path follows a published rule. Building it
    here makes it correct by construction, removes a generation pass, and leaves the model to do
    only the four jobs that genuinely need reasoning.
    """
    by_id = {r["id"]: r for r in resolved if r.get("id")}
    answered = [r for r in resolved if r["answer"] != scenarios.UNKNOWN_LABEL]
    unknown = [r for r in resolved if r["answer"] == scenarios.UNKNOWN_LABEL]

    lines = [f"## Customer profile", "",
             f"{scenario['title']} — {scenario['fit']}.", ""]
    if answered:
        for item in answered:
            lines.append(f"- **{item['question']}** {item['answer']}")
        lines.append("")

    head = by_id.get("headcount", {}).get("answer", "")
    path = _LICENCE_PATH.get(head)
    if path:
        lines += ["## Licensing path", "", path, ""]
        for qid in ("workforce", "managers"):
            note = _FRONTLINE_NOTE.get(by_id.get(qid, {}).get("answer", ""))
            if note:
                lines += [note, ""]
                break

    if constraints:
        lines += ["## What this rules out", "",
                  "These follow from the answers above and are not negotiable:", ""]
        lines += [f"- {c}" for c in constraints]
        lines.append("")

    if unknown:
        lines += ["## Still to establish", "",
                  "You left these open, so nothing in this package assumes an answer. They lead "
                  "the Discovery Playbook:", ""]
        lines += [f"- {u['question']}" for u in unknown]
        lines.append("")

    lines += ["---", "",
              "*Licensing figures and program mechanics change every Microsoft fiscal year. "
              "Confirm the current price list and eligibility in Partner Center before quoting.*"]
    return "\n".join(lines).strip()


def _split_known(resolved: list[dict[str, str]]) -> tuple[list[dict], list[dict]]:
    """Separate answered questions from the ones the partner flagged as unknown."""
    known = [r for r in resolved if r["answer"] != scenarios.UNKNOWN_LABEL]
    unknown = [r for r in resolved if r["answer"] == scenarios.UNKNOWN_LABEL]
    return known, unknown


def _brief(scenario: dict[str, Any], resolved: list[dict[str, str]],
           constraints: list[str] | None = None) -> str:
    """The customer picture, assembled from the diagnostic answers. This is stage one — it is
    genuine work (it is what every later pass is conditioned on), not a loading message.

    Unknowns are stated as unknowns. If they were merely omitted the model would fill the gap from
    the scenario description and present the guess as fact, which is the exact failure the
    "Not sure yet" option exists to prevent.
    """
    known, unknown = _split_known(resolved)
    lines = [f"Scenario: {scenario['title']} — {scenario['fit']}",
             f"Situation: {scenario['situation']}", ""]
    if known:
        lines.append("What the partner knows:")
        for item in known:
            lines.append(f"- {item['question']} → {item['answer']} ({item['signal']})")
    if unknown:
        lines += ["", "What the partner does NOT yet know — treat as open questions, never assume "
                      "an answer, and do not build a recommendation that depends on them:"]
        for item in unknown:
            lines.append(f"- {item['question']}")
    # Last, and therefore closest to the instruction — a 3B model weights the end of a long
    # prompt more heavily than the middle.
    if constraints:
        lines += ["", "NON-NEGOTIABLE CONSTRAINTS. These are established facts about this "
                      "customer's eligibility. Every recommendation you make must comply with "
                      "them, and you must not suggest anything they rule out:"]
        for rule in constraints:
            lines.append(f"- {rule}")
    return "\n".join(lines)


async def _retrieve(query: str, collections: list[str], k: int) -> list[dict]:
    """Retrieval embeds the query over blocking HTTP, so it runs off the event loop."""
    def _work() -> list[dict]:
        chunks, matrix = store.snapshot()
        if not chunks:
            return []
        return rag.rank(query, chunks, matrix, k=k, collections=set(collections))
    return await asyncio.to_thread(_work)


async def _pass(kind: str, brief: str, instruction: str, query: str, collections: list[str],
                k: int = 6, prefill: str = "", emit: Emit | None = None,
                ) -> tuple[str, list[dict], list[str]]:
    """One grounded generation pass.

    ``prefill`` seeds the assistant turn so the model *continues* a required opening rather than
    reformatting around it — without it, asked to begin with "Your next move:", the model put
    that phrase at the very end after a page of restatement.

    Emits its retrieval and then its tokens, so the UI can show what the pass is standing on and
    what it is producing. Returns the text, the hits, and any sentences dropped by the guard.
    """
    hits = await _retrieve(query, collections, k)
    if emit:
        # The retrieval is the most legible part of the reasoning — it shows which sourced material
        # this pass stands on, and how well each piece matched.
        await emit("retrieval", {"key": kind, "query": query, "hits": [
            {"title": h.get("title", ""), "source": h["source"],
             "collection": h["collection"], "score": round(float(h["score"]), 3)}
            for h in hits
        ]})
    context = rag.build_context(hits) or "(no supporting context was retrieved)"
    messages = [
        {"role": "system", "content": _BASE_RULES},
        {"role": "user", "content":
            f"{brief}\n\nReference material:\n{context}\n\n{instruction}"},
    ]
    if prefill:
        messages.append({"role": "assistant", "content": prefill})
    parts: list[str] = []
    async for delta in broker.chat_stream(
            config.RAG_MODEL, messages,
            options={**_OPTIONS_BASE, "num_predict": _BUDGETS.get(kind, 400)}):
        parts.append(delta)
        if emit:
            await emit("token", {"key": kind, "token": delta})
    text = _strip_preamble(prefill + "".join(parts) if prefill else "".join(parts))
    # Every pass is scrubbed, not just the value summary: an invented seat count in the scenario
    # card is exactly as damaging as an invented saving in the ROI.
    text, removed = _scrub_invented_numbers(text, context)
    if removed:
        log.info("pass %s: dropped %d sentence(s) with unsourced figures", kind, len(removed))
    return text.strip(), hits, removed


# Each entry: (stage key, instruction, retrieval query suffix, extra collections, prefill).
_PASSES: list[tuple[str, str, str, list[str], str]] = [
    ("next_move",
     "Write the single most important next move for this partner, as one short paragraph "
     "beginning 'Your next move:'. Say what to lead with and what to propose concretely.\n\n"
     "MANDATORY: if you mention registering a deal or co-sell at all, you must in the same "
     "sentence tell the partner to first confirm the customer is a Microsoft-managed account, "
     "because deal registration is unavailable on unmanaged accounts and most smaller SMB "
     "customers are unmanaged. If the customer is small enough to be unlikely to be managed, do "
     "not recommend registration at all — give the partner-led alternative instead.",
     "which solution to lead with, deal registration eligibility, and the Partner Center action",
     ["partner-center", "incentives-funding"],
     "Your next move: "),
    ("discovery",
     "Write a Discovery Playbook: five questions this partner should ask on the call. For each, "
     "give the question in bold and one sentence on the signal a good answer gives.\n\n"
     "Two rules on which questions to pick. Anything listed under 'What the partner does NOT yet "
     "know' must appear FIRST, rephrased as a question the partner can put to the customer in "
     "plain language. And do NOT re-ask anything already answered under 'What the partner knows' "
     "— the partner told you that, so asking it back wastes one of the five slots.",
     "discovery and qualification questions for this customer situation",
     ["smb-segment", "mcem"],
     "**"),
    ("customer_qa",
     "Write a Customer Q&A pack: the three objections this customer is most likely to raise, "
     "each as a bold heading in the customer's own words, followed by a grounded response. If "
     "the honest answer is that the customer has a fair point, say so rather than supplying a "
     "weak rebuttal.",
     "objections this customer will raise and the grounded response",
     ["smb-segment", "objection-handling", "csp-licensing"],
     "**\""),
    ("roi",
     "Write a Value Summary with the headings '## Where the value comes from', '## What to "
     "measure', and '## What the customer must supply'. Describe value qualitatively — the "
     "mechanism by which this customer saves time or money. **Do not invent any numbers, "
     "percentages, currency amounts or savings estimates.** Under 'What the customer must "
     "supply', list the specific figures the partner needs to collect from the customer to build "
     "a real business case.",
     "business value, adoption measurement and recurring services for this customer",
     ["managed-services", "smb-segment"],
     "## Where the value comes from\n"),
]


async def generate_package(scenario_id: str, answers: dict[str, str],
                           emit: Emit | None = None) -> dict[str, Any]:
    """Run the full diagnostic → package generation, reporting progress through ``emit``.

    ``emit(event, payload)`` receives ``stage`` events as each pass starts and completes. A pass
    that raises is recorded in the package as an error and the rest continue — a partner with
    four of five outputs is far better served than one with a stack trace.
    """
    scenario = scenarios.SCENARIOS_BY_ID.get(scenario_id)
    if scenario is None:
        raise ValueError(f"unknown scenario {scenario_id!r}")
    resolved = scenarios.resolve_answers(scenario_id, answers)

    async def send(event: str, payload: dict[str, Any]) -> None:
        if emit:
            await emit(event, payload)

    await send("stage", {"key": "analyze", "state": "active"})
    # Constraints are computed deterministically, not generated — this is the step where the rail
    # decides what is and is not executable for this customer.
    constraints = _constraints(resolved, scenario_id)
    brief = _brief(scenario, resolved, constraints)
    known, unknown = _split_known(resolved)
    # Reported individually so the UI can name the rules that fired. A constraint the assistant
    # silently obeyed demonstrates nothing; one the partner can read is the product.
    await send("analysis", {"known": len(known), "unknown": [u["question"] for u in unknown],
                            "constraints": constraints})
    await send("stage", {"key": "analyze", "state": "done", "constraints": len(constraints)})

    # The grounding stage is real: it proves the corpus can serve this scenario at all, and its
    # result is surfaced so an empty knowledge base is visible rather than silently degrading.
    await send("stage", {"key": "ground", "state": "active"})
    probe = await _retrieve(f"{scenario['title']} {scenario['fit']}", scenario["collections"], 4)
    await send("stage", {"key": "ground", "state": "done",
                         "grounded": bool(probe), "sources": len(probe)})

    # The scenario card is assembled, not generated — see _build_scenario_card. It is reported as
    # a stage because it IS a real step; it simply completes immediately.
    await send("stage", {"key": "scenario_card", "state": "active"})
    card = _build_scenario_card(scenario, resolved, constraints)
    await send("stage", {"key": "scenario_card", "state": "done", "deterministic": True})

    package: dict[str, Any] = {
        "scenario": {k: scenario[k] for k in ("id", "title", "icon", "fit", "situation")},
        "answers": resolved,
        # Surfaced so the UI can show the partner what the tool ruled out and why — a constraint
        # the assistant silently obeyed teaches nothing.
        "constraints": constraints,
        "outputs": {"scenario_card": card},
        # No citations: this card restates the partner's own answers and applies published rules,
        # so there is no retrieved passage to attribute it to.
        "citations": {"scenario_card": []},
        "grounded": bool(probe),
    }

    for key, instruction, query_suffix, extra, prefill in _PASSES:
        await send("stage", {"key": key, "state": "active"})
        query = f"{scenario['title']} {scenario['fit']} — {query_suffix}"
        collections = list(dict.fromkeys(scenario["collections"] + extra))
        try:
            text, hits, removed = await _pass(key, brief, instruction, query, collections,
                                              prefill=prefill, emit=emit)
            package["outputs"][key] = text
            package["citations"][key] = [
                {"source": h["source"], "collection": h["collection"], "title": h.get("title", "")}
                for h in hits
            ]
            if removed:
                # Surfaced, not hidden: a partner should know the assistant suppressed a figure
                # rather than wonder why the summary reads thin.
                package.setdefault("suppressed", {})[key] = len(removed)
            await send("stage", {"key": key, "state": "done", "suppressed": len(removed)})
        except broker.BrokerError as exc:
            log.warning("pass %s failed: %s", key, exc)
            package["outputs"][key] = ""
            package["citations"][key] = []
            package.setdefault("errors", {})[key] = str(exc)
            await send("stage", {"key": key, "state": "error", "detail": str(exc)})

    return package
