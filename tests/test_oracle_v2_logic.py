"""Offline tests for OptimisticClaimOracleV2 - the appeal round.

The interesting surface here is not the happy path, it is what happens between
`resolve()` and money moving: who is allowed to reopen a decided claim, for how
long, and what the second round is allowed to know.
"""

from __future__ import annotations

import json

import pytest

from helpers import ALICE, BOB, CAROL, GEN, deploy_oracle_v2

AUTHOR_DOC = "https://example.com/report"
COUNTER_DOC = "https://example.com/retraction"
AUTHOR_BODY = "The report states that protocol X shipped v2 on 2026-10-30."
COUNTER_BODY = "The same outlet retracted the story and says v2 was never shipped."

ROUND_ONE = "You are the adjudicator of an on-chain claim oracle"
ROUND_TWO = "You are the appeal adjudicator"

BOND = GEN // 100


def _decision(verdict="SUPPORTED", confidence=90, reasoning="because the documents say so"):
    return json.dumps({"verdict": verdict, "confidence": confidence, "reasoning": reasoning})


def _mock_documents(mod):
    mod.gl.nondet.web.mock(r"example\.com/report", AUTHOR_BODY)
    mod.gl.nondet.web.mock(r"example\.com/retraction", COUNTER_BODY)


def _contested(mod, contract, bond=BOND):
    """Author posts, challenger matches the bond: the claim is now under dispute."""
    mod.gl.set_context(sender=ALICE, value=bond)
    claim_id = contract.post_claim("Protocol X shipped v2", AUTHOR_DOC)
    mod.gl.set_context(sender=BOB, value=bond)
    contract.challenge(claim_id)
    return claim_id


def _round_one(mod, contract, claim_id, verdict="SUPPORTED", confidence=90):
    mod.gl.llm.mock(ROUND_ONE, _decision(verdict, confidence))
    mod.gl.set_context(sender=CAROL)  # any third party may crank the resolver
    contract.resolve(claim_id)


def _round_two(mod, contract, claim_id, verdict="REFUTED", confidence=90):
    mod.gl.llm.mock(ROUND_TWO, _decision(verdict, confidence))
    mod.gl.set_context(sender=CAROL)
    contract.resolve_appeal(claim_id)


def _travel(mod, seconds):
    """Move the contract's clock forward without waiting in real time."""
    base = mod._now_ts()
    mod._now_ts = lambda: base + seconds


def _credits(contract, *accounts):
    return [int(contract.get_credit(a)) for a in accounts]


# ------------------------------------------------------------------ deployment


def test_appeal_window_defaults_to_the_minimum():
    mod, contract = deploy_oracle_v2(challenge_window=0, appeal_window=0)
    assert int(contract.appeal_window) == int(mod.MIN_APPEAL_WINDOW)


def test_appeal_window_below_minimum_names_both_numbers():
    mod = __import__("helpers").load_contract("contracts/optimistic_claim_oracle_v2.py")
    with pytest.raises(mod.gl.vm.UserError) as err:
        mod.OptimisticClaimOracleV2(mod.u256(0), mod.u256(3600), mod.u256(5))
    message = str(err.value)
    assert "5s" in message
    assert str(int(mod.MIN_APPEAL_WINDOW)) + "s" in message


def test_negative_appeal_window_is_rejected():
    mod = __import__("helpers").load_contract("contracts/optimistic_claim_oracle_v2.py")
    with pytest.raises(mod.gl.vm.UserError, match="must be >= 0"):
        mod.OptimisticClaimOracleV2(mod.u256(0), mod.u256(3600), mod.u256(-1))


# ------------------------------------------------------- deciding != settling


def test_resolve_decides_without_paying():
    """The whole point of v2: a verdict must not move money before the appeal
    window has run out."""
    mod, contract = deploy_oracle_v2()
    _mock_documents(mod)
    claim_id = _contested(mod, contract)
    _round_one(mod, contract, claim_id, verdict="SUPPORTED")

    claim = contract.get_claim(claim_id)
    assert claim.status == mod.STATUS_RESOLVED
    assert claim.verdict == "SUPPORTED"
    assert int(claim.appeal_deadline) > 0
    assert _credits(contract, ALICE, BOB) == [0, 0]
    assert contract.is_appealable(claim_id) is True


def test_finalise_before_the_window_closes_is_rejected():
    mod, contract = deploy_oracle_v2()
    _mock_documents(mod)
    claim_id = _contested(mod, contract)
    _round_one(mod, contract, claim_id)

    mod.gl.set_context(sender=CAROL)
    with pytest.raises(mod.gl.vm.UserError, match="appeal window still open"):
        contract.finalise(claim_id)


def test_finalise_after_the_window_pays_round_one():
    mod, contract = deploy_oracle_v2(appeal_window=600)
    _mock_documents(mod)
    claim_id = _contested(mod, contract)
    _round_one(mod, contract, claim_id, verdict="REFUTED")

    _travel(mod, 601)
    mod.gl.set_context(sender=CAROL)
    contract.finalise(claim_id)

    claim = contract.get_claim(claim_id)
    assert claim.status == mod.STATUS_SETTLED
    assert _credits(contract, ALICE, BOB) == [0, 2 * BOND]


def test_appeal_is_closed_once_finalised():
    mod, contract = deploy_oracle_v2(appeal_window=600)
    _mock_documents(mod)
    claim_id = _contested(mod, contract)
    _round_one(mod, contract, claim_id, verdict="SUPPORTED")
    _travel(mod, 601)
    contract.finalise(claim_id)

    mod.gl.set_context(sender=BOB, value=BOND)
    with pytest.raises(mod.gl.vm.UserError, match="not awaiting an appeal"):
        contract.appeal(claim_id, COUNTER_DOC)


# ------------------------------------------------------------- who may appeal


def test_the_winner_cannot_appeal_its_own_victory():
    mod, contract = deploy_oracle_v2()
    _mock_documents(mod)
    claim_id = _contested(mod, contract)
    _round_one(mod, contract, claim_id, verdict="SUPPORTED")

    mod.gl.set_context(sender=ALICE, value=BOND)  # the author won
    with pytest.raises(mod.gl.vm.UserError, match="only the party round 1 ruled against"):
        contract.appeal(claim_id, COUNTER_DOC)


def test_a_stranger_cannot_pay_to_reopen_a_dispute():
    mod, contract = deploy_oracle_v2()
    _mock_documents(mod)
    claim_id = _contested(mod, contract)
    _round_one(mod, contract, claim_id, verdict="REFUTED")

    mod.gl.set_context(sender=CAROL, value=BOND)
    with pytest.raises(mod.gl.vm.UserError, match="only the party round 1 ruled against"):
        contract.appeal(claim_id, COUNTER_DOC)


def test_the_loser_can_appeal():
    mod, contract = deploy_oracle_v2()
    _mock_documents(mod)
    claim_id = _contested(mod, contract)
    _round_one(mod, contract, claim_id, verdict="REFUTED")

    mod.gl.set_context(sender=ALICE, value=BOND)
    contract.appeal(claim_id, COUNTER_DOC)

    claim = contract.get_claim(claim_id)
    assert claim.status == mod.STATUS_APPEALED
    assert str(claim.appellant) == ALICE
    assert int(claim.appeal_bond) == BOND
    assert contract.pot_of(claim_id) == 3 * BOND


def test_inconclusive_round_one_lets_either_party_appeal():
    """No loser means no privileged appellant, so both parties get the route."""
    mod, contract = deploy_oracle_v2()
    _mock_documents(mod)
    claim_id = _contested(mod, contract)
    _round_one(mod, contract, claim_id, verdict="INCONCLUSIVE", confidence=40)

    mod.gl.set_context(sender=ALICE, value=BOND)
    contract.appeal(claim_id, COUNTER_DOC)
    assert contract.get_claim(claim_id).status == mod.STATUS_APPEALED


def test_inconclusive_round_one_still_bars_a_stranger_from_appealing():
    mod, contract = deploy_oracle_v2()
    _mock_documents(mod)
    claim_id = _contested(mod, contract)
    _round_one(mod, contract, claim_id, verdict="INCONCLUSIVE", confidence=40)

    mod.gl.set_context(sender=CAROL, value=BOND)
    with pytest.raises(mod.gl.vm.UserError, match="only a party"):
        contract.appeal(claim_id, COUNTER_DOC)


# --------------------------------------------------------------- appeal gates


def test_appeal_requires_matching_the_claim_bond():
    mod, contract = deploy_oracle_v2()
    _mock_documents(mod)
    claim_id = _contested(mod, contract)
    _round_one(mod, contract, claim_id, verdict="REFUTED")

    mod.gl.set_context(sender=ALICE, value=BOND - 1)
    with pytest.raises(mod.gl.vm.UserError, match="must match the claim bond"):
        contract.appeal(claim_id, COUNTER_DOC)


def test_appeal_before_round_one_is_rejected():
    mod, contract = deploy_oracle_v2()
    _mock_documents(mod)
    claim_id = _contested(mod, contract)

    mod.gl.set_context(sender=ALICE, value=BOND)
    with pytest.raises(mod.gl.vm.UserError, match="not awaiting an appeal"):
        contract.appeal(claim_id, COUNTER_DOC)


def test_appeal_after_the_window_is_rejected():
    mod, contract = deploy_oracle_v2(appeal_window=600)
    _mock_documents(mod)
    claim_id = _contested(mod, contract)
    _round_one(mod, contract, claim_id, verdict="REFUTED")

    _travel(mod, 601)
    mod.gl.set_context(sender=ALICE, value=BOND)
    with pytest.raises(mod.gl.vm.UserError, match="appeal window closed"):
        contract.appeal(claim_id, COUNTER_DOC)


def test_a_second_appeal_is_rejected():
    mod, contract = deploy_oracle_v2()
    _mock_documents(mod)
    claim_id = _contested(mod, contract)
    _round_one(mod, contract, claim_id, verdict="REFUTED")
    mod.gl.set_context(sender=ALICE, value=BOND)
    contract.appeal(claim_id, COUNTER_DOC)

    mod.gl.set_context(sender=BOB, value=BOND)
    with pytest.raises(mod.gl.vm.UserError, match="not awaiting an appeal"):
        contract.appeal(claim_id, COUNTER_DOC)


def test_finalise_is_rejected_while_an_appeal_is_pending():
    mod, contract = deploy_oracle_v2(appeal_window=600)
    _mock_documents(mod)
    claim_id = _contested(mod, contract)
    _round_one(mod, contract, claim_id, verdict="REFUTED")
    mod.gl.set_context(sender=ALICE, value=BOND)
    contract.appeal(claim_id, COUNTER_DOC)

    _travel(mod, 601)
    with pytest.raises(mod.gl.vm.UserError, match="not awaiting finalisation"):
        contract.finalise(claim_id)


def test_counter_evidence_must_be_http_urls():
    mod, contract = deploy_oracle_v2()
    _mock_documents(mod)
    claim_id = _contested(mod, contract)
    _round_one(mod, contract, claim_id, verdict="REFUTED")

    mod.gl.set_context(sender=ALICE, value=BOND)
    with pytest.raises(mod.gl.vm.UserError, match="http"):
        contract.appeal(claim_id, "not-a-url")


def test_counter_evidence_is_capped():
    mod, contract = deploy_oracle_v2()
    _mock_documents(mod)
    claim_id = _contested(mod, contract)
    _round_one(mod, contract, claim_id, verdict="REFUTED")

    mod.gl.set_context(sender=ALICE, value=BOND)
    many = "\n".join("https://example.com/evidence-" + str(i) for i in range(5))
    with pytest.raises(mod.gl.vm.UserError, match="too many counter evidence"):
        contract.appeal(claim_id, many)


# ------------------------------------------------------------- round two payout


def test_round_two_supported_pays_the_whole_pot_to_the_author():
    mod, contract = deploy_oracle_v2()
    _mock_documents(mod)
    claim_id = _contested(mod, contract)
    _round_one(mod, contract, claim_id, verdict="REFUTED")
    mod.gl.set_context(sender=ALICE, value=BOND)
    contract.appeal(claim_id, COUNTER_DOC)
    _round_two(mod, contract, claim_id, verdict="SUPPORTED")

    claim = contract.get_claim(claim_id)
    assert claim.status == mod.STATUS_SETTLED
    assert _credits(contract, ALICE, BOB) == [3 * BOND, 0]


def test_round_two_refuted_pays_the_whole_pot_to_the_challenger():
    mod, contract = deploy_oracle_v2()
    _mock_documents(mod)
    claim_id = _contested(mod, contract)
    _round_one(mod, contract, claim_id, verdict="SUPPORTED")
    mod.gl.set_context(sender=BOB, value=BOND)
    contract.appeal(claim_id, COUNTER_DOC)
    _round_two(mod, contract, claim_id, verdict="REFUTED")

    assert _credits(contract, ALICE, BOB) == [0, 3 * BOND]


def test_round_two_inconclusive_returns_each_stake():
    mod, contract = deploy_oracle_v2()
    _mock_documents(mod)
    claim_id = _contested(mod, contract)
    _round_one(mod, contract, claim_id, verdict="REFUTED")
    mod.gl.set_context(sender=ALICE, value=BOND)
    contract.appeal(claim_id, COUNTER_DOC)
    _round_two(mod, contract, claim_id, verdict="INCONCLUSIVE", confidence=35)

    # The author put in both the initial bond and the appeal bond.
    assert _credits(contract, ALICE, BOB) == [2 * BOND, BOND]


def test_value_is_conserved_through_an_appeal():
    """No path may create or destroy wei: the ledger must always sum to the
    deposits, whatever the verdicts are."""
    mod, contract = deploy_oracle_v2()
    _mock_documents(mod)
    claim_id = _contested(mod, contract)
    _round_one(mod, contract, claim_id, verdict="REFUTED")
    mod.gl.set_context(sender=ALICE, value=BOND)
    contract.appeal(claim_id, COUNTER_DOC)
    _round_two(mod, contract, claim_id, verdict="SUPPORTED")

    held = sum(int(v) for v in contract.credits.values())
    assert held == 3 * BOND
    assert contract.pot_of(claim_id) == 3 * BOND


def test_round_two_requires_a_pending_appeal():
    mod, contract = deploy_oracle_v2()
    _mock_documents(mod)
    claim_id = _contested(mod, contract)
    _round_one(mod, contract, claim_id, verdict="REFUTED")

    with pytest.raises(mod.gl.vm.UserError, match="not under appeal"):
        contract.resolve_appeal(claim_id)


def test_round_two_consensus_failure_leaves_the_claim_appealed():
    mod, contract = deploy_oracle_v2()
    _mock_documents(mod)
    claim_id = _contested(mod, contract)
    _round_one(mod, contract, claim_id, verdict="REFUTED")
    mod.gl.set_context(sender=ALICE, value=BOND)
    contract.appeal(claim_id, COUNTER_DOC)

    # Leader says SUPPORTED, the validator re-derives REFUTED.
    mod.gl.llm.queue(ROUND_TWO, [_decision("SUPPORTED"), _decision("REFUTED")])
    with pytest.raises(mod.gl.vm.UserError):
        contract.resolve_appeal(claim_id)

    assert contract.get_claim(claim_id).status == mod.STATUS_APPEALED
    assert _credits(contract, ALICE, BOB) == [0, 0]


# ------------------------------------------------------------ round two prompt


def test_round_two_is_blind_to_round_one():
    """If the appeal prompt carries the first verdict, the appeal is a request to
    defend it. It must be judged from documents only, so round one's reasoning
    text must not survive into the round two prompt."""
    mod, contract = deploy_oracle_v2()
    _mock_documents(mod)
    claim_id = _contested(mod, contract)

    marker = "round-one-reasoning-marker"
    mod.gl.llm.mock(ROUND_ONE, _decision("REFUTED", 73, marker))
    mod.gl.set_context(sender=CAROL)
    contract.resolve(claim_id)

    mod.gl.set_context(sender=ALICE, value=BOND)
    contract.appeal(claim_id, COUNTER_DOC)

    mod.gl.llm.calls.clear()
    _round_two(mod, contract, claim_id, verdict="SUPPORTED")

    appeal_prompts = [p for p in mod.gl.llm.calls if ROUND_TWO in p]
    assert appeal_prompts, "round two never ran"
    for prompt in appeal_prompts:
        assert marker not in prompt
        assert "73" not in prompt
        assert "round 1" not in prompt.lower()
        assert "first decision has already been issued" in prompt


def test_round_two_sees_both_document_sets():
    mod, contract = deploy_oracle_v2()
    _mock_documents(mod)
    claim_id = _contested(mod, contract)
    _round_one(mod, contract, claim_id, verdict="REFUTED")
    mod.gl.set_context(sender=ALICE, value=BOND)
    contract.appeal(claim_id, COUNTER_DOC)

    mod.gl.llm.calls.clear()
    _round_two(mod, contract, claim_id, verdict="SUPPORTED")

    prompt = [p for p in mod.gl.llm.calls if ROUND_TWO in p][0]
    assert AUTHOR_BODY in prompt
    assert COUNTER_BODY in prompt
    assert "COUNTERPARTY" in prompt


def test_round_one_never_sees_the_counter_evidence():
    """Counter evidence only exists after round one, but the prompt builder is
    the place a regression would hide the ordering."""
    mod, contract = deploy_oracle_v2()
    _mock_documents(mod)
    claim_id = _contested(mod, contract)

    mod.gl.llm.calls.clear()
    _round_one(mod, contract, claim_id, verdict="SUPPORTED")

    prompt = [p for p in mod.gl.llm.calls if ROUND_ONE in p][0]
    assert COUNTER_BODY not in prompt


# ------------------------------------------------------------------ withdrawal


def test_withdraw_after_an_appeal_clears_the_credit():
    mod, contract = deploy_oracle_v2()
    _mock_documents(mod)
    claim_id = _contested(mod, contract)
    _round_one(mod, contract, claim_id, verdict="REFUTED")
    mod.gl.set_context(sender=ALICE, value=BOND)
    contract.appeal(claim_id, COUNTER_DOC)
    _round_two(mod, contract, claim_id, verdict="SUPPORTED")

    mod.gl.set_context(sender=ALICE)
    contract.withdraw()
    assert int(contract.get_credit(ALICE)) == 0


def test_uncontested_claim_still_refunds_the_bond():
    """The cheap path must stay cheap: no LLM, no appeal window, no fee."""
    mod, contract = deploy_oracle_v2(challenge_window=60)
    mod.gl.set_context(sender=ALICE, value=BOND)
    claim_id = contract.post_claim("Protocol X shipped v2", AUTHOR_DOC)

    _travel(mod, 61)
    contract.claim_uncontested(claim_id)

    assert _credits(contract, ALICE) == [BOND]
    assert mod.gl.llm.calls == []
