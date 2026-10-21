"""The Scenario Builder's definitions and the answer resolution the generator runs on.

Two kinds of test live here. The first are the authoring rules the module docstring commits to
— six to eight questions, an unknown option on every one, a distinct signal behind every answer
— which are exactly the rules a hurried edit breaks, and which nothing else would catch until a
partner saw a question whose answers all produced the same package. The second is
``resolve_answers``, the only real logic in the module: it is what turns a client's labels into
the signals the generator reasons over, and it has to survive a client running a stale bundle.
"""
from __future__ import annotations

import pytest

from smb_partner import scenarios

CUSTOMER_IDS = ["retail-chain", "auto-dealership", "restaurant-group",
                "professional-services", "manufacturing"]
ALL_IDS = CUSTOMER_IDS + ["grow-your-practice"]

ALL_QUESTIONS = [(s["id"], q) for s in scenarios.SCENARIOS for q in s["questions"]]
QUESTION_IDS = [f"{sid}.{q['id']}" for sid, q in ALL_QUESTIONS]


def test_the_scenario_set_is_what_the_surfaces_expect():
    """The ids are baked into both frontends and the mobile build, so they are a contract."""
    assert [s["id"] for s in scenarios.SCENARIOS] == ALL_IDS
    assert set(scenarios.SCENARIOS_BY_ID) == set(ALL_IDS)
    assert all(scenarios.SCENARIOS_BY_ID[i]["id"] == i for i in ALL_IDS)


@pytest.mark.parametrize("scenario", scenarios.SCENARIOS, ids=ALL_IDS)
def test_every_scenario_is_renderable(scenario):
    for field in ("id", "icon", "title", "fit", "situation"):
        assert scenario[field], f"{scenario['id']} is missing {field}"
    assert scenario["collections"], "a scenario with no collections can never be grounded"
    assert scenario["pass_set"] in scenarios.STAGE_SETS
    assert scenario["pass_set"] in scenarios.TAB_SETS


@pytest.mark.parametrize("scenario", scenarios.SCENARIOS, ids=ALL_IDS)
def test_question_count_stays_inside_the_authoring_window(scenario):
    """Six to eight. Past eight the "two minutes between meetings" promise breaks; below six
    the depth the module was rewritten to add is gone again."""
    assert 6 <= len(scenario["questions"]) <= 8


@pytest.mark.parametrize("sid,question", ALL_QUESTIONS, ids=QUESTION_IDS)
def test_every_question_offers_an_out(sid, question):
    """"Not sure yet" is appended by ``_q`` so a question cannot be authored without it, and it
    has to be last — a partner scanning options should meet the real answers first."""
    last = question["options"][-1]
    assert last["label"] == scenarios.UNKNOWN_LABEL
    assert "DOES NOT KNOW" in last["signal"]
    assert [o["label"] for o in question["options"][:-1]].count(scenarios.UNKNOWN_LABEL) == 0


@pytest.mark.parametrize("sid,question", ALL_QUESTIONS, ids=QUESTION_IDS)
def test_every_answer_changes_the_recommendation(sid, question):
    """A question whose options carry the same signal is theatre: the generator consumes the
    signal, so identical signals mean the answer had no effect on the package."""
    signals = [o["signal"] for o in question["options"]]
    labels = [o["label"] for o in question["options"]]
    assert len(set(signals)) == len(signals)
    assert len(set(labels)) == len(labels)
    assert question["prompt"] and question["why"]


@pytest.mark.parametrize("sid", CUSTOMER_IDS)
def test_the_spine_leads_every_customer_scenario(sid):
    """Headcount and partner-of-record gate Microsoft mechanics regardless of industry, and
    they are prepended once centrally so they cannot drift per scenario."""
    questions = scenarios.SCENARIOS_BY_ID[sid]["questions"]
    assert [q["id"] for q in questions[:2]] == ["headcount", "relationship"]


def test_the_practice_scenario_opts_out_of_the_spine():
    """"Is this already your customer" is meaningless when the subject IS the partner."""
    practice = scenarios.SCENARIOS_BY_ID["grow-your-practice"]
    assert practice["pass_set"] == "practice"
    assert [q["id"] for q in practice["questions"]][:2] == ["designation", "transact"]
    assert not {"headcount", "relationship"} & {q["id"] for q in practice["questions"]}


def test_the_spine_is_not_a_shared_mutable():
    """``_spine()`` is called per scenario. If it were ever hoisted to a module constant, an
    edit to one scenario's headcount question would silently rewrite it in all five."""
    a = scenarios.SCENARIOS_BY_ID["retail-chain"]["questions"][0]
    b = scenarios.SCENARIOS_BY_ID["manufacturing"]["questions"][0]
    assert a == b
    assert a is not b
    assert a["options"][0] is not b["options"][0]


@pytest.mark.parametrize("sid", CUSTOMER_IDS)
def test_customer_scenarios_draw_on_the_base_collections(sid):
    assert {"smb-segment", "csp-licensing", "solution-plays"} <= set(
        scenarios.SCENARIOS_BY_ID[sid]["collections"])


def test_stage_and_tab_sets_agree_on_the_artifacts_they_describe():
    """A stage that produces an output nobody has a tab for is invisible work; a tab with no
    stage behind it renders empty. ``analyze`` and ``ground`` are the two legitimate
    exceptions — they are reasoning steps, not artifacts."""
    for pass_set, stages in scenarios.STAGE_SETS.items():
        produced = {s["key"] for s in stages} - {"analyze", "ground"}
        tabs = {t["key"] for t in scenarios.TAB_SETS[pass_set]}
        assert tabs <= produced, f"{pass_set}: tab with no stage behind it"
        assert produced - tabs == {"next_move"}, f"{pass_set}: unexpected untabbed output"


def test_stages_alias_still_points_at_the_customer_set():
    """``STAGES`` is what ``/api/scenarios`` returns for clients that predate per-scenario
    stages, so it has to stay the customer list rather than becoming a union."""
    assert scenarios.STAGES == scenarios.STAGE_SETS["customer"]
    assert [s["key"] for s in scenarios.STAGES][:2] == ["analyze", "ground"]


# --- public view ------------------------------------------------------------------------

def test_public_view_strips_the_generator_signals():
    """Signals are retrieval instructions, not prose. Shipping them would put "competitive
    displacement; new-to-Microsoft" on screen next to a radio button."""
    view = scenarios.public_view()
    assert [s["id"] for s in view] == ALL_IDS
    for scenario in view:
        # No ``collections`` either: which corpus a scenario retrieves over is generator
        # plumbing, and publishing it invites a client to start passing its own.
        assert set(scenario) == {"id", "icon", "title", "fit", "situation",
                                 "stages", "tabs", "questions"}
        for question in scenario["questions"]:
            assert all(isinstance(o, str) for o in question["options"])
            assert set(question) == {"id", "prompt", "why", "options"}


def test_public_view_carries_per_scenario_stages_and_tabs():
    """The UI must not hardcode one output set: a practice self-assessment produces a gap
    analysis where a customer brief produces a discovery playbook."""
    view = {s["id"]: s for s in scenarios.public_view()}
    assert [t["key"] for t in view["retail-chain"]["tabs"]][0] == "scenario_card"
    assert {t["key"] for t in view["grow-your-practice"]["tabs"]} == {
        "scenario_card", "gap_analysis", "partner_center", "business_case"}
    assert view["grow-your-practice"]["stages"] == scenarios.STAGE_SETS["practice"]
    assert view["retail-chain"]["stages"] == scenarios.STAGE_SETS["customer"]


def test_public_view_keeps_the_option_order_of_the_definition():
    definition = scenarios.SCENARIOS_BY_ID["retail-chain"]["questions"][0]
    published = scenarios.public_view()[0]["questions"][0]
    assert published["options"] == [o["label"] for o in definition["options"]]


# --- answer resolution ------------------------------------------------------------------

def test_resolve_answers_pairs_each_answer_with_its_signal():
    resolved = scenarios.resolve_answers("retail-chain", {
        "headcount": "More than 300",
        "relationship": "No — I'm trying to win them",
    })
    assert [r["id"] for r in resolved] == ["headcount", "relationship"]
    assert resolved[0]["question"].startswith("Roughly how many people")
    assert resolved[0]["answer"] == "More than 300"
    assert "past the pooled Business cap" in resolved[0]["signal"]
    assert "net-new acquisition" in resolved[1]["signal"]


def test_resolve_answers_follows_question_order_not_submission_order():
    """The brief is assembled in this order and a 3B model weights position, so the order is
    load bearing rather than cosmetic."""
    resolved = scenarios.resolve_answers("retail-chain", {
        "comms": "Paper schedules and verbal handover",
        "headcount": "25–100",
    })
    assert [r["id"] for r in resolved] == ["headcount", "comms"]


def test_resolve_answers_keeps_an_explicit_unknown():
    """The unknown has to survive resolution — it is what routes the question into the
    Discovery Playbook instead of being guessed."""
    resolved = scenarios.resolve_answers("retail-chain",
                                         {"headcount": scenarios.UNKNOWN_LABEL})
    assert resolved[0]["answer"] == scenarios.UNKNOWN_LABEL
    assert resolved[0]["signal"].startswith("PARTNER DOES NOT KNOW")


def test_resolve_answers_drops_what_a_stale_client_sends():
    """A client on an old bundle should get a thinner package, not a 500."""
    resolved = scenarios.resolve_answers("retail-chain", {
        "headcount": "25–100",
        "retired_question": "whatever",          # question removed since that bundle shipped
        "relationship": "an option label that no longer exists",
        "comms": "",                             # rendered but never answered
    })
    assert [r["id"] for r in resolved] == ["headcount"]


def test_resolve_answers_for_an_unknown_scenario_is_empty_not_an_error():
    assert scenarios.resolve_answers("no-such-scenario", {"headcount": "25–100"}) == []


def test_resolve_answers_with_no_answers_is_empty():
    assert scenarios.resolve_answers("retail-chain", {}) == []


@pytest.mark.parametrize("sid", ALL_IDS)
def test_every_option_of_every_scenario_resolves(sid):
    """Round-trip the whole definition: every published label must map back to a signal, or a
    partner picks an answer the generator silently ignores."""
    scenario = scenarios.SCENARIOS_BY_ID[sid]
    for question in scenario["questions"]:
        for option in question["options"]:
            resolved = scenarios.resolve_answers(sid, {question["id"]: option["label"]})
            assert len(resolved) == 1
            assert resolved[0]["signal"] == option["signal"]
