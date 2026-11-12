"""Offline tests for OptimisticClaimOracleV3 - the deadlock exit and the ledger.

The interesting surface is not the happy path. It is what happens when the
adjudication machinery cannot produce an answer at all: who may release the
funds, after how long, in what proportion, and what the contract is allowed to
claim afterwards about who was right.
"""

from __future__ import annotations

import json

import pytest

from helpers import ALICE, BOB, CAROL, GEN, deploy_oracle_v3

AUTHOR_DOC = "https://example.com/report"
COUNTER_DOC = "https://example.com/retraction"
AUTHOR_BODY = "The report states that protocol X shipped v2 on 2026-10-30."
COUNTER_BODY = "The same outlet retracted the story and says v2 was never shipped."

ROUND_ONE = "You are the adjudicator of an on-chain claim oracle"
ROUND_TWO = "You are the appeal adjudicator"

BOND = GEN // 100
STUCK = 7200


def _decision(verdict="SUPPORTED", confidence=90, reasoning="because the documents say so"):
    return json.dumps({"verdict": verdict, "confidence": confidence, "reasoning": reasoning})


def _mock_documents(mod):
    mod.gl.nondet.web.mock(r"example\.com/report", AUTHOR_BODY)
    mod.gl.nondet.web.mock(r"example\.com/retraction", COUNTER_BODY)


def _travel(mod, seconds):
    """Move the contract's clock forward without waiting in real time."""
    base = mod._now_ts()
    mod._now_ts = lambda: base + seconds


def _contested(mod, contract, bond=BOND):
    mod.gl.set_context(sender=ALICE, value=bond)
    claim_id = contract.post_claim("Protocol X shipped v2", AUTHOR_DOC)
    mod.gl.set_context(sender=BOB, value=bond)
    contract.challenge(claim_id)
    return claim_id


def _round_one(mod, contract, claim_id, verdict="SUPPORTED", confidence=90):
    # Clear first: mocks are matched in registration order, so a second claim in
    # the same test would otherwise be judged by the first claim's verdict.
    mod.gl.llm.clear()
    mod.gl.llm.mock(ROUND_ONE, _decision(verdict, confidence))
    mod.gl.set_context(sender=CAROL)
    contract.resolve(claim_id)


def _round_two(mod, contract, claim_id, verdict="REFUTED", confidence=90):
    mod.gl.llm.clear()
    mod.gl.llm.mock(ROUND_TWO, _decision(verdict, confidence))
    mod.gl.set_context(sender=CAROL)
    contract.resolve_appeal(claim_id)


# --- constructor -------------------------------------------------------------


def test_stuck_window_zero_falls_back_to_the_default():
    mod, contract = deploy_oracle_v3(stuck_window=0)
    assert int(contract.stuck_after()) == int(mod.DEFAULT_STUCK_WINDOW)


def test_stuck_window_below_the_floor_is_rejected():
    mod = deploy_oracle_v3()[0]
    with pytest.raises(mod.gl.vm.UserError, match="below the minimum"):
        mod.OptimisticClaimOracleV3(
            mod.u256(BOND), mod.u256(3600), mod.u256(3600), mod.u256(60)
        )


def test_stuck_window_negative_is_rejected():
    mod = deploy_oracle_v3()[0]
    with pytest.raises(mod.gl.vm.UserError, match="must be >= 0"):
        mod.OptimisticClaimOracleV3(
            mod.u256(BOND), mod.u256(3600), mod.u256(3600), mod.u256(-1)
        )


# --- the deadlock exit -------------------------------------------------------


def test_challenge_records_when_it_landed():
    mod, contract = deploy_oracle_v3()
    claim_id = _contested(mod, contract)
    claim = contract.get_claim(claim_id)
    assert claim.status == "CHALLENGED"
    assert int(claim.challenged_at) > 0


def test_release_before_the_window_reverts():
    mod, contract = deploy_oracle_v3()
    claim_id = _contested(mod, contract)
    _travel(mod, STUCK - 1)
    mod.gl.set_context(sender=CAROL)
    with pytest.raises(mod.gl.vm.UserError, match="less than stuck_window"):
        contract.release_stuck(claim_id)


def test_release_after_the_window_returns_both_bonds():
    mod, contract = deploy_oracle_v3()
    claim_id = _contested(mod, contract)
    _travel(mod, STUCK)
    mod.gl.set_context(sender=CAROL)
    contract.release_stuck(claim_id)

    claim = contract.get_claim(claim_id)
    assert claim.status == "REFUNDED"
    assert claim.verdict == ""  # no verdict was ever issued
    assert int(contract.get_credit(ALICE)) == BOND
    assert int(contract.get_credit(BOB)) == BOND
    assert int(contract.get_credit(CAROL)) == 0


def test_release_conserves_value_exactly():
    mod, contract = deploy_oracle_v3()
    claim_id = _contested(mod, contract)
    _travel(mod, STUCK * 3)
    contract.release_stuck(claim_id)
    paid = int(contract.get_credit(ALICE)) + int(contract.get_credit(BOB))
    assert paid == 2 * BOND


def test_release_is_permissionless_and_idempotent_once():
    mod, contract = deploy_oracle_v3()
    claim_id = _contested(mod, contract)
    _travel(mod, STUCK)
    mod.gl.set_context(sender=CAROL)
    contract.release_stuck(claim_id)
    with pytest.raises(mod.gl.vm.UserError, match="not under challenge"):
        contract.release_stuck(claim_id)


def test_release_does_not_apply_to_an_open_claim():
    mod, contract = deploy_oracle_v3()
    mod.gl.set_context(sender=ALICE, value=BOND)
    claim_id = contract.post_claim("Protocol X shipped v2", AUTHOR_DOC)
    _travel(mod, STUCK * 2)
    with pytest.raises(mod.gl.vm.UserError, match="not under challenge"):
        contract.release_stuck(claim_id)


def test_release_does_not_apply_to_a_decided_claim():
    mod, contract = deploy_oracle_v3()
    _mock_documents(mod)
    claim_id = _contested(mod, contract)
    _round_one(mod, contract, claim_id)
    _travel(mod, STUCK * 2)
    with pytest.raises(mod.gl.vm.UserError, match="not under challenge"):
        contract.release_stuck(claim_id)


def test_is_stuck_flips_exactly_at_the_boundary():
    mod, contract = deploy_oracle_v3()
    claim_id = _contested(mod, contract)
    _travel(mod, STUCK - 1)
    assert contract.is_stuck(claim_id) is False
    _travel(mod, STUCK)
    assert contract.is_stuck(claim_id) is True


def test_released_claim_can_no_longer_be_resolved_or_appealed():
    mod, contract = deploy_oracle_v3()
    _mock_documents(mod)
    claim_id = _contested(mod, contract)
    _travel(mod, STUCK)
    contract.release_stuck(claim_id)

    with pytest.raises(mod.gl.vm.UserError, match="not under challenge"):
        _round_one(mod, contract, claim_id)
    mod.gl.set_context(sender=ALICE, value=BOND)
    with pytest.raises(mod.gl.vm.UserError, match="not awaiting an appeal"):
        contract.appeal(claim_id, COUNTER_DOC)


def test_failed_consensus_leaves_a_claim_that_can_still_be_released():
    """The scenario the exit exists for: resolve() cannot agree, repeatedly."""
    mod, contract = deploy_oracle_v3()
    claim_id = _contested(mod, contract)

    # No LLM mock is registered, so the shim raises inside the nondet block -
    # the same shape as a validator set that never agrees.
    mod.gl.set_context(sender=CAROL)
    with pytest.raises(Exception):
        contract.resolve(claim_id)
    assert contract.get_claim(claim_id).status == "CHALLENGED"

    _travel(mod, STUCK)
    contract.release_stuck(claim_id)
    assert contract.get_claim(claim_id).status == "REFUNDED"
    assert int(contract.get_credit(ALICE)) == BOND
    assert int(contract.get_credit(BOB)) == BOND


# --- the observation ledger --------------------------------------------------


def test_posting_increments_the_posted_counter():
    mod, contract = deploy_oracle_v3()
    mod.gl.set_context(sender=ALICE, value=BOND)
    contract.post_claim("Protocol X shipped v2", AUTHOR_DOC)
    record = contract.get_record(ALICE)
    assert int(record.posted) == 1
    assert int(record.upheld) == 0


def test_unknown_address_has_a_zeroed_record():
    mod, contract = deploy_oracle_v3()
    record = contract.get_record(CAROL)
    assert int(record.posted) == 0
    assert int(record.refuted) == 0


def test_uncontested_claim_is_recorded_as_uncontested_not_upheld():
    mod, contract = deploy_oracle_v3()
    mod.gl.set_context(sender=ALICE, value=BOND)
    claim_id = contract.post_claim("Protocol X shipped v2", AUTHOR_DOC)
    _travel(mod, 3600)
    contract.claim_uncontested(claim_id)

    record = contract.get_record(ALICE)
    assert int(record.uncontested) == 1
    assert int(record.upheld) == 0


def test_finalised_supported_credits_both_sides_of_the_record():
    mod, contract = deploy_oracle_v3()
    _mock_documents(mod)
    claim_id = _contested(mod, contract)
    _round_one(mod, contract, claim_id, verdict="SUPPORTED")
    _travel(mod, 3600)
    contract.finalise(claim_id)

    assert int(contract.get_record(ALICE).upheld) == 1
    assert int(contract.get_record(BOB).refuted) == 1


def test_finalised_refuted_flips_the_record():
    mod, contract = deploy_oracle_v3()
    _mock_documents(mod)
    claim_id = _contested(mod, contract)
    _round_one(mod, contract, claim_id, verdict="REFUTED")
    _travel(mod, 3600)
    contract.finalise(claim_id)

    assert int(contract.get_record(ALICE).refuted) == 1
    assert int(contract.get_record(BOB).upheld) == 1


def test_finalised_inconclusive_charges_nobody():
    mod, contract = deploy_oracle_v3()
    _mock_documents(mod)
    claim_id = _contested(mod, contract)
    _round_one(mod, contract, claim_id, verdict="INCONCLUSIVE")
    _travel(mod, 3600)
    contract.finalise(claim_id)

    assert int(contract.get_record(ALICE).inconclusive) == 1
    assert int(contract.get_record(BOB).inconclusive) == 1
    assert int(contract.get_credit(ALICE)) == BOND
    assert int(contract.get_credit(BOB)) == BOND


def test_appeal_outcome_updates_the_record_from_round_two():
    mod, contract = deploy_oracle_v3()
    _mock_documents(mod)
    claim_id = _contested(mod, contract)
    _round_one(mod, contract, claim_id, verdict="REFUTED")

    # Alice lost round one, so she is the one who may appeal.
    mod.gl.set_context(sender=ALICE, value=BOND)
    contract.appeal(claim_id, COUNTER_DOC)
    _round_two(mod, contract, claim_id, verdict="SUPPORTED")

    assert int(contract.get_record(ALICE).upheld) == 1
    assert int(contract.get_record(BOB).refuted) == 1


def test_release_records_inconclusive_for_both_parties():
    mod, contract = deploy_oracle_v3()
    claim_id = _contested(mod, contract)
    _travel(mod, STUCK)
    contract.release_stuck(claim_id)

    assert int(contract.get_record(ALICE).inconclusive) == 1
    assert int(contract.get_record(BOB).inconclusive) == 1
    # A release is not a win for anyone.
    assert int(contract.get_record(ALICE).upheld) == 0
    assert int(contract.get_record(BOB).upheld) == 0


def test_record_accumulates_across_several_claims():
    mod, contract = deploy_oracle_v3()
    _mock_documents(mod)

    first = _contested(mod, contract)
    _round_one(mod, contract, first, verdict="SUPPORTED")
    _travel(mod, 3600)
    contract.finalise(first)

    second = _contested(mod, contract)
    _round_one(mod, contract, second, verdict="REFUTED")
    _travel(mod, 3600)
    contract.finalise(second)

    record = contract.get_record(ALICE)
    assert int(record.posted) == 2
    assert int(record.upheld) == 1
    assert int(record.refuted) == 1


def test_two_parties_do_not_share_a_record():
    mod, contract = deploy_oracle_v3()
    _mock_documents(mod)
    claim_id = _contested(mod, contract)
    _round_one(mod, contract, claim_id, verdict="SUPPORTED")
    _travel(mod, 3600)
    contract.finalise(claim_id)

    alice = contract.get_record(ALICE)
    bob = contract.get_record(BOB)
    assert int(alice.posted) == 1 and int(bob.posted) == 0
    assert int(alice.upheld) == 1 and int(bob.upheld) == 0
    assert int(bob.refuted) == 1 and int(alice.refuted) == 0
