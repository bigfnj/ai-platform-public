"""Scenario package generation — the deterministic half, the guards, and partial failure.

Most of what this module promises is not "the model wrote something". It is that the scenario
card is assembled in code rather than generated, that the hard constraints fire from the
answers alone, that a figure or an entitlement the retrieved context does not support never
reaches a partner, and that one broken pass costs one tab rather than the whole package. All
four are testable with a scripted model, which is what happens here.
"""
from __future__ import annotations

import asyncio

import pytest

from smb_partner import broker, generate, scenarios

RETAIL = "retail-chain"
PRACTICE = "grow-your-practice"

#: The corpus every pass retrieves over. Collections are chosen to be in scope for both the
#: retail and the practice scenario. Deliberately free of the words "defender", "contact" and
#: "center" so the entitlement guard has something it genuinely cannot support.
CORPUS = {
    "csp-licensing": [
        ("caps.md", "Seat caps",
         "The Business family caps at 300 seats pooled across Basic, Standard and Premium, "
         "and margin on those seats runs to 40% in some programs."),
    ],
    "smb-segment": [
        ("segment.md", "SMB segment",
         "Small and midsize customers are mostly unmanaged accounts, served partner-led."),
    ],
    "designations": [
        ("solutions-partner.md", "Solutions Partner",
         "A Solutions Partner designation is earned against a capability score, renewed yearly."),
    ],
    "program-structure": [
        ("membership.md", "Membership",
         "Partner membership is annual and gates the benefits package a partner can draw on."),
    ],
}

ANSWERS_SMALL = {
    "headcount": "25–100",
    "relationship": "Yes — I'm their partner of record",
    "locations": "6–15 locations",
    "workforce": "Head office only",
}

ANSWERS_BIG = {
    "headcount": "More than 300",
    "relationship": "No — I'm trying to win them",
}


def run(coro):
    """House style across this repo: drive a coroutine with asyncio.run rather than pull in a
    plugin and a marker for four tests."""
    return asyncio.run(coro)


class Recorder:
    """Collects the ``emit`` stream the websocket surface forwards to the UI."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    async def __call__(self, event: str, payload: dict) -> None:
        self.events.append((event, payload))

    def of(self, event: str) -> list[dict]:
        return [p for e, p in self.events if e == event]

    def stages(self, state: str) -> list[str]:
        return [p["key"] for p in self.of("stage") if p["state"] == state]


@pytest.fixture()
def corpus(seed_corpus):
    from conftest import chunk
    for name, rows in CORPUS.items():
        seed_corpus(name, [chunk(text, source=src, title=title, collection=name)
                           for src, title, text in rows])


def user_message(call: dict) -> str:
    return call["messages"][1]["content"]


# --- the deterministic half -------------------------------------------------------------

def test_an_unknown_scenario_is_a_value_error(fake_broker):
    with pytest.raises(ValueError, match="unknown scenario"):
        run(generate.generate_package("no-such-scenario", {}))


def test_the_scenario_card_is_assembled_not_generated(corpus, fake_broker):
    """It is a real stage but it costs no model call, and it carries no citations because it
    restates the partner's own answers and applies a published rule."""
    package = run(generate.generate_package(RETAIL, ANSWERS_SMALL))
    card = package["outputs"]["scenario_card"]
    assert card.startswith("## Customer profile")
    assert "Retail Chain — Teams Frontline + Copilot for Store Ops." in card
    # The answers, verbatim, under the profile.
    assert "**Roughly how many people work at this business?** 25–100" in card
    assert "6–15 locations" in card
    assert package["citations"]["scenario_card"] == []
    # Four generated passes, and none of them produced the card.
    assert len(fake_broker.streams) == 4
    assert card not in [user_message(c) for c in fake_broker.streams]


def test_the_licensing_path_follows_headcount_rather_than_the_model(corpus, fake_broker):
    card = run(generate.generate_package(RETAIL, ANSWERS_SMALL))["outputs"]["scenario_card"]
    assert "## Licensing path" in card
    assert "Business Premium is the natural fit" in card
    # The frontline note rides with it, keyed off the licence-mix answer.
    assert "Almost the entire workforce is unlicensed" in card


def test_past_the_seat_cap_the_business_family_is_ruled_out(corpus, fake_broker):
    """The A/B run that motivated the hard-rule table: identical retailers differing only in
    headcount, and the close still recommended Business Premium to the 300+ seat one."""
    package = run(generate.generate_package(RETAIL, ANSWERS_BIG))
    card = package["outputs"]["scenario_card"]
    assert "Enterprise licensing (E3 or E5)" in card
    assert "## What this rules out" in card
    assert "are not negotiable" in card
    assert package["constraints"]
    assert any("PAST the pooled 300-seat" in c for c in package["constraints"])
    assert "Business Premium is the natural fit" not in card


def test_constraints_reach_the_model_as_non_negotiable(corpus, fake_broker):
    """A 3B model will not derive a hard limit from retrieved prose, so it is computed and
    handed over last — closest to the instruction, where the model weights it most."""
    run(generate.generate_package(RETAIL, ANSWERS_BIG))
    brief = user_message(fake_broker.streams[0])
    assert "NON-NEGOTIABLE CONSTRAINTS" in brief
    assert "Enterprise licensing (E3/E5) is required" in brief


def test_an_unknown_answer_is_stated_as_unknown_never_filled_in(corpus, fake_broker):
    """If unknowns were merely omitted the model would fill the gap from the scenario
    description and present the guess as fact — the exact failure the option exists to stop."""
    package = run(generate.generate_package(RETAIL, {
        "headcount": "25–100",
        "relationship": scenarios.UNKNOWN_LABEL,
    }))
    card = package["outputs"]["scenario_card"]
    assert "## Still to establish" in card
    assert "Is this already your customer?" in card
    brief = user_message(fake_broker.streams[0])
    assert "What the partner does NOT yet know" in brief
    assert "never assume an answer" in brief


def test_an_unanswered_question_is_simply_absent(corpus, fake_broker):
    """Skipping is not the same as flagging unknown: a partner in a hurry gets a thinner
    package, not a "still to establish" list of everything they did not click."""
    package = run(generate.generate_package(RETAIL, {"headcount": "25–100"}))
    assert "## Still to establish" not in package["outputs"]["scenario_card"]
    assert [a["id"] for a in package["answers"]] == ["headcount"]


def test_the_card_always_dates_itself(corpus, fake_broker):
    card = run(generate.generate_package(RETAIL, ANSWERS_SMALL))["outputs"]["scenario_card"]
    assert "Confirm the current price list and eligibility in Partner Center" in card


def test_the_practice_scenario_profiles_the_partner_not_a_customer(corpus, fake_broker):
    package = run(generate.generate_package(PRACTICE, {
        "designation": "No, and we have not started",
        "goal": "Attain or add a designation",
    }))
    card = package["outputs"]["scenario_card"]
    assert card.startswith("## Practice profile")
    assert "## Licensing path" not in card       # no headcount question in this scenario
    assert set(package["outputs"]) == {"scenario_card", "next_move", "gap_analysis",
                                       "partner_center", "business_case"}
    assert "advising a Microsoft partner about their OWN business" in \
        fake_broker.streams[0]["messages"][0]["content"]


def test_the_customer_scenarios_produce_the_customer_artifacts(corpus, fake_broker):
    package = run(generate.generate_package(RETAIL, ANSWERS_SMALL))
    assert set(package["outputs"]) == {"scenario_card", "next_move", "discovery",
                                       "customer_qa", "roi"}
    assert set(package["citations"]) == set(package["outputs"])
    assert package["scenario"] == {"id": "retail-chain", "title": "Retail Chain",
                                   "icon": "🛍️",
                                   "fit": "Teams Frontline + Copilot for Store Ops",
                                   "situation": scenarios.SCENARIOS_BY_ID[RETAIL]["situation"]}


# --- passes -----------------------------------------------------------------------------

def test_each_pass_gets_its_own_retrieval_and_its_own_budget(corpus, fake_broker):
    run(generate.generate_package(RETAIL, ANSWERS_SMALL))
    budgets = [c["options"]["num_predict"] for c in fake_broker.streams]
    assert budgets == [generate._BUDGETS[k] for k in
                       ("next_move", "discovery", "customer_qa", "roi")]
    assert all(c["options"]["temperature"] == 0.2 for c in fake_broker.streams)
    # Five distinct retrieval queries: the grounding probe plus one per pass.
    assert len({user_message(c) for c in fake_broker.streams}) == 4


def test_a_pass_prefill_is_sent_as_an_assistant_turn_and_kept(corpus, fake_broker):
    """Without the prefill the model put the required opening at the very end, after a page of
    restatement — so it seeds the turn and has to survive into the output."""
    package = run(generate.generate_package(RETAIL, ANSWERS_SMALL))
    first = fake_broker.streams[0]["messages"]
    assert first[-1] == {"role": "assistant", "content": "Your next move: "}
    assert package["outputs"]["next_move"].startswith("Your next move:")


def test_the_grounding_probe_reports_whether_the_corpus_can_serve_the_scenario(corpus,
                                                                               fake_broker):
    rec = Recorder()
    package = run(generate.generate_package(RETAIL, ANSWERS_SMALL, rec))
    assert package["grounded"] is True
    done = [p for p in rec.of("stage") if p["key"] == "ground" and p["state"] == "done"][0]
    assert done["grounded"] is True
    assert done["sources"] > 0


def test_an_empty_corpus_degrades_to_an_ungrounded_package(fake_broker):
    """First boot, or an ingest that failed. The partner still gets a package; it is simply
    marked ungrounded and cites nothing."""
    package = run(generate.generate_package(RETAIL, ANSWERS_SMALL))
    assert package["grounded"] is False
    assert all(v == [] for v in package["citations"].values())
    assert package["outputs"]["next_move"]
    assert "(no supporting context was retrieved)" in user_message(fake_broker.streams[0])


def test_citations_name_the_material_each_pass_stood_on(corpus, fake_broker):
    package = run(generate.generate_package(RETAIL, ANSWERS_SMALL))
    cited = package["citations"]["next_move"]
    assert cited
    assert {c["source"] for c in cited} <= {src for rows in CORPUS.values()
                                            for src, _, _ in rows}
    assert all(set(c) == {"source", "collection", "title"} for c in cited)


# --- the guards -------------------------------------------------------------------------

def test_a_figure_the_context_does_not_support_never_reaches_the_partner(corpus, fake_broker):
    """Prompting a 3B model not to invent figures does not hold — it turned "mostly frontline,
    a small head office" into "90% frontline, 10% office" on the first run. So it is
    mechanical: a figure survives only if the grounding context contains it."""
    fake_broker.tokens = ["Lead with Business Premium. ",
                          "Expect a 91% drop in support tickets and $4,500 saved."]
    package = run(generate.generate_package(RETAIL, ANSWERS_SMALL))
    out = package["outputs"]["next_move"]
    assert "91%" not in out
    assert "$4,500" not in out
    assert "Lead with Business Premium." in out
    # Surfaced rather than hidden, so the summary reading thin is explained.
    assert package["suppressed"]["next_move"] == 1


def test_a_figure_that_is_in_the_retrieved_context_survives(corpus, fake_broker):
    """The rule is "sourced", not "no numbers at all" — a partner quoting the corpus is fine."""
    fake_broker.tokens = ["Margin on those seats runs to 40% in some programs."]
    package = run(generate.generate_package(RETAIL, ANSWERS_SMALL))
    assert "40%" in package["outputs"]["next_move"]
    assert "next_move" not in package.get("suppressed", {})


def test_an_entitlement_asserted_about_an_absent_product_is_dropped(corpus, fake_broker):
    """The failure the numeric guard could not see: "a free trial of Defender" carries no
    figure, reads plausibly, and commits the partner on Microsoft's behalf."""
    fake_broker.tokens = ["Lead with the security story. ",
                          "They are eligible for a free trial of Defender."]
    package = run(generate.generate_package(RETAIL, ANSWERS_SMALL))
    out = package["outputs"]["next_move"]
    assert "Defender" not in out
    assert "Lead with the security story." in out
    assert package["suppressed"]["next_move"] == 1


def test_the_entitlement_guard_works_on_a_product_sharing_no_words_with_the_context():
    context = "[1] (csp/plans.md) Business Premium is the SMB security plan."
    text = "They are eligible for a free trial of Viva Engage."
    kept, removed = generate._scrub_unsupported_entitlements(text, context)
    assert removed == [text]
    assert kept == ""


# Regression: the guard accepted a product as 'supported' when ANY of the last two
# tokens of its name appeared anywhere in the context. In a Microsoft corpus 'for',
# 'business' and '365' are always present, so the guard's own motivating example --
# a free trial of Microsoft 365 Defender for Business with nothing supporting it --
# sailed through the check written to catch it. Fixed 2026-09-10: leading qualifiers
# are dropped one at a time and each candidate matches as a contiguous PHRASE.
def test_the_entitlement_guard_is_not_defeated_by_one_shared_token():
    context = ("[1] (csp/plans.md) Microsoft 365 Business Premium is the SMB security plan. "
               "The 300-seat cap is pooled across the Business family.")
    text = "Offer them a free trial of Microsoft 365 Defender for Business."
    kept, removed = generate._scrub_unsupported_entitlements(text, context)
    assert removed == [text]
    assert kept == ""


def test_a_product_mentioned_descriptively_is_untouched(corpus, fake_broker):
    """The guard is deliberately narrow: it fires only when a sentence BOTH asserts an
    entitlement and names a product the context never mentions."""
    fake_broker.tokens = ["Defender is the product this conversation eventually reaches."]
    package = run(generate.generate_package(RETAIL, ANSWERS_SMALL))
    assert "Defender" in package["outputs"]["next_move"]


@pytest.mark.parametrize("opener", ["## Summary", "Diagnostic answers", "#### Overview",
                                    "Customer summary", "Current state"])
def test_strip_preamble_drops_a_leading_restatement(opener):
    """A 3B model reliably opens by restating its input. These are the shapes it produces;
    prompting them away did not hold, so they are stripped."""
    text = f"{opener}\n\nA multi-site retailer with paper rotas.\n\n## Real content\n\nLead here."
    assert generate._strip_preamble(text) == "## Real content\n\nLead here."


def test_strip_preamble_leaves_genuine_content_alone():
    """Only a *leading* section with a known restatement heading goes."""
    text = "## What to measure\n\nAdoption, week over week.\n\n## Summary\n\nA closing recap."
    assert generate._strip_preamble(text) == text


def test_strip_preamble_never_empties_the_answer():
    """If the whole response was a restatement, a thin answer still beats a blank tab."""
    text = "## Summary\n\nThe customer is a multi-site retailer."
    assert generate._strip_preamble(text) == text


def test_a_leading_restatement_is_stripped_end_to_end(corpus, fake_broker):
    """Through a pass with no prefill, since a prefill puts a legitimate heading first."""
    fake_broker.tokens = ["## Summary\n\nYou resell without a managed service.\n\n",
                          "## What to verify\n\nCheck the capability score in Partner Center."]
    package = run(generate.generate_package(PRACTICE, {"transact": "Both, depending on the "
                                                                   "customer"}))
    verify = package["outputs"]["partner_center"]
    assert verify.startswith("## What to verify")
    assert "You resell without a managed service" not in verify


# Regression: both guards rejoined with ' '.join() after splitting on (?<=[.!?])\s+,
# and \s+ matches newlines -- so every line break after a sentence-ending character
# collapsed to a space, on every pass, even when nothing was removed. Both passes are
# prompted for markdown and both tabs render it, so a Discovery Playbook came back as
# one run-on paragraph. Fixed 2026-09-10 by keeping each sentence's trailing gap.
def test_the_guards_preserve_markdown_structure(corpus, fake_broker):
    fake_broker.tokens = ["**Do they have a work account?** Signals the frontline gap.\n\n",
                          "**Who signs off?** Signals the buyer."]
    package = run(generate.generate_package(RETAIL, ANSWERS_SMALL))
    assert "\n\n**Who signs off?**" in package["outputs"]["discovery"]


# --- progress and partial failure --------------------------------------------------------

def test_every_stage_is_reported_in_execution_order(corpus, fake_broker):
    """The checklist in the UI must never show a later line finishing first."""
    rec = Recorder()
    run(generate.generate_package(RETAIL, ANSWERS_SMALL, rec))
    expected = [s["key"] for s in scenarios.STAGE_SETS["customer"]]
    assert rec.stages("active") == expected
    assert rec.stages("done") == expected
    order = [(p["key"], p["state"]) for p in rec.of("stage")]
    for key in expected:
        assert order.index((key, "active")) < order.index((key, "done"))


def test_the_analysis_event_names_the_open_questions_and_the_rules_that_fired(corpus,
                                                                              fake_broker):
    """A constraint the assistant silently obeyed demonstrates nothing; one the partner can
    read is the product."""
    rec = Recorder()
    run(generate.generate_package(RETAIL, dict(ANSWERS_BIG,
                                               locations=scenarios.UNKNOWN_LABEL), rec))
    analysis = rec.of("analysis")[0]
    assert analysis["known"] == 2
    assert analysis["unknown"] == ["How many store locations does this retailer operate?"]
    assert any("300-seat" in c for c in analysis["constraints"])


def test_the_retrieval_event_shows_what_each_pass_is_standing_on(corpus, fake_broker):
    rec = Recorder()
    run(generate.generate_package(RETAIL, ANSWERS_SMALL, rec))
    retrievals = rec.of("retrieval")
    assert [r["key"] for r in retrievals] == ["next_move", "discovery", "customer_qa", "roi"]
    hit = retrievals[0]["hits"][0]
    assert set(hit) == {"title", "source", "collection", "score"}
    assert isinstance(hit["score"], float)
    assert retrievals[0]["query"].startswith("Retail Chain")


def test_tokens_are_emitted_against_the_pass_that_produced_them(corpus, fake_broker):
    rec = Recorder()
    run(generate.generate_package(RETAIL, ANSWERS_SMALL, rec))
    tokens = rec.of("token")
    assert {t["key"] for t in tokens} == {"next_move", "discovery", "customer_qa", "roi"}
    assert len(tokens) == 4 * len(fake_broker.tokens)


def test_one_failing_pass_costs_one_tab_not_the_package(corpus, monkeypatch, fake_broker):
    """A partner with four of five outputs is far better served than one with a stack trace."""
    healthy = fake_broker.chat_stream

    def flaky(model, messages, **kw):
        if "Discovery Playbook" in messages[1]["content"]:
            fake_broker.stream_error = broker.BrokerError("model evicted mid-pass")
        try:
            return healthy(model, messages, **kw)
        finally:
            fake_broker.stream_error = None

    monkeypatch.setattr(broker, "chat_stream", flaky)
    rec = Recorder()
    package = run(generate.generate_package(RETAIL, ANSWERS_SMALL, rec))

    assert package["errors"] == {"discovery": "model evicted mid-pass"}
    assert package["outputs"]["discovery"] == ""
    assert package["citations"]["discovery"] == []
    assert package["outputs"]["next_move"] and package["outputs"]["roi"]
    errored = [p for p in rec.of("stage") if p["state"] == "error"]
    assert [p["key"] for p in errored] == ["discovery"]
    # The rest of the checklist still completes.
    assert rec.stages("done") == ["analyze", "ground", "scenario_card", "next_move",
                                  "customer_qa", "roi"]


def test_a_healthy_package_carries_no_error_key(corpus, fake_broker):
    package = run(generate.generate_package(RETAIL, ANSWERS_SMALL))
    assert "errors" not in package


def test_generation_works_without_an_emit_callback(corpus, fake_broker):
    """The buffered HTTP route passes none — the events must be optional, not assumed."""
    package = run(generate.generate_package(RETAIL, ANSWERS_SMALL))
    assert package["outputs"]["next_move"]
