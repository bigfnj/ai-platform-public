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

import logging
import re
from typing import Any, Callable

from smb_partner import broker, config, rag, scenarios, store

log = logging.getLogger("smb_partner.generate")

Emit = Callable[[str, dict[str, Any]], None]

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
    "starting immediately with the requested content and no introduction."
)

# Low temperature: these are briefing artifacts, not creative writing, and a 3B model drifts
# off-instruction quickly at default sampling.
_OPTIONS_BASE = {"temperature": 0.2, "top_p": 0.9}


def _brief(scenario: dict[str, Any], resolved: list[dict[str, str]]) -> str:
    """The customer picture, assembled from the diagnostic answers. This is stage one — it is
    genuine work (it is what every later pass is conditioned on), not a loading message."""
    lines = [f"Scenario: {scenario['title']} — {scenario['fit']}",
             f"Situation: {scenario['situation']}", "", "Diagnostic answers:"]
    for item in resolved:
        lines.append(f"- {item['question']} → {item['answer']} ({item['signal']})")
    return "\n".join(lines)


def _retrieve(query: str, collections: list[str], k: int) -> list[dict]:
    chunks, matrix = store.snapshot()
    if not chunks:
        return []
    return rag.rank(query, chunks, matrix, k=k, collections=set(collections))


def _pass(kind: str, brief: str, instruction: str, query: str, collections: list[str],
          k: int = 6, prefill: str = "") -> tuple[str, list[dict], list[str]]:
    """One grounded generation pass.

    ``prefill`` seeds the assistant turn so the model *continues* a required opening rather than
    reformatting around it — without it, asked to begin with "Your next move:", the model put
    that phrase at the very end after a page of restatement.

    Returns the text, the hits it was grounded on, and any sentences dropped by the numeric guard.
    """
    hits = _retrieve(query, collections, k)
    context = rag.build_context(hits) or "(no supporting context was retrieved)"
    messages = [
        {"role": "system", "content": _BASE_RULES},
        {"role": "user", "content":
            f"{brief}\n\nReference material:\n{context}\n\n{instruction}"},
    ]
    if prefill:
        messages.append({"role": "assistant", "content": prefill})
    text = broker.chat(config.RAG_MODEL, messages,
                       options={**_OPTIONS_BASE, "num_predict": _BUDGETS.get(kind, 400)})
    text = _strip_preamble(prefill + text if prefill else text)
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
    ("scenario_card",
     "Write a Scenario Card with these markdown headings and nothing else: '## Customer profile', "
     "'## Primary pain', '## Microsoft solution fit', '## Licensing considerations', "
     "'## Deal registration path'. Under Licensing considerations, name only products and rules "
     "that appear in the context — include any seat-count ceiling that applies.",
     "licensing families, seat limits and solution fit for this customer profile",
     ["csp-licensing", "designations"],
     "## Customer profile\n"),
    ("discovery",
     "Write a Discovery Playbook: five questions this partner should ask on the call. For each, "
     "give the question in bold and one sentence on the signal a good answer gives. Order them "
     "so the first question opens the conversation naturally.",
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


def generate_package(scenario_id: str, answers: dict[str, str],
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
    send = emit or (lambda *_: None)

    send("stage", {"key": "analyze", "state": "active"})
    brief = _brief(scenario, resolved)
    send("stage", {"key": "analyze", "state": "done"})

    # The grounding stage is real: it proves the corpus can serve this scenario at all, and its
    # result is surfaced so an empty knowledge base is visible rather than silently degrading.
    send("stage", {"key": "ground", "state": "active"})
    probe = _retrieve(f"{scenario['title']} {scenario['fit']}", scenario["collections"], 4)
    send("stage", {"key": "ground", "state": "done",
                   "grounded": bool(probe), "sources": len(probe)})

    package: dict[str, Any] = {
        "scenario": {k: scenario[k] for k in ("id", "title", "icon", "fit", "situation")},
        "answers": resolved,
        "outputs": {},
        "citations": {},
        "grounded": bool(probe),
    }

    for key, instruction, query_suffix, extra, prefill in _PASSES:
        send("stage", {"key": key, "state": "active"})
        query = f"{scenario['title']} {scenario['fit']} — {query_suffix}"
        collections = list(dict.fromkeys(scenario["collections"] + extra))
        try:
            text, hits, removed = _pass(key, brief, instruction, query, collections,
                                        prefill=prefill)
            package["outputs"][key] = text
            package["citations"][key] = [
                {"source": h["source"], "collection": h["collection"], "title": h.get("title", "")}
                for h in hits
            ]
            if removed:
                # Surfaced, not hidden: a partner should know the assistant suppressed a figure
                # rather than wonder why the summary reads thin.
                package.setdefault("suppressed", {})[key] = len(removed)
            send("stage", {"key": key, "state": "done", "suppressed": len(removed)})
        except broker.BrokerError as exc:
            log.warning("pass %s failed: %s", key, exc)
            package["outputs"][key] = ""
            package["citations"][key] = []
            package.setdefault("errors", {})[key] = str(exc)
            send("stage", {"key": key, "state": "error", "detail": str(exc)})

    return package
