# Adjudicate

A claim oracle where being wrong costs money. Post a statement with a bond and
the URLs backing it; anyone who disagrees matches the bond; GenLayer validators
fetch the documents themselves and decide. An undisputed claim never reaches a
model - it ages out and the bond comes back.

**Live client:** https://iancao.github.io/genlayer-primitives/ - reads claims
straight from Studionet with no wallet, and signs through one when you want to
post or challenge.

## What is here

```
contracts/   the Intelligent Contracts
app/         the web client (no framework, vendored genlayer-js)
docs/        design documents, one per contract
tests/       57 offline logic tests, no network, no Docker
scripts/     read-only on-chain verification of the deployments
tools/       an offline GenVM shim the tests run against
```

## The contracts

| Contract | File | The question it answers |
|---|---|---|
| OptimisticClaimOracle | `contracts/optimistic_claim_oracle.py` | "Is this factual claim true?" - bond, challenge, consensus |
| OptimisticClaimOracleV2 | `contracts/optimistic_claim_oracle_v2.py` | Same, plus a remedy: the loser may appeal, and round two is blind |

Deployed on GenLayer Studionet:

| Contract | Address | Constructor args |
|---|---|---|
| OptimisticClaimOracle | [`0x3a3A9c681cD547ccb293a6329839cc50D529fAd9`](https://explorer-studio.genlayer.com/address/0x3a3A9c681cD547ccb293a6329839cc50D529fAd9) | `min_bond=1e16`, `challenge_window=86400` |

## Design in one paragraph

The failure that makes most AI oracles unusable in production is a model that is
wrong for free. Here both sides have money at stake and the model is only the
tie-breaker. Consensus is re-derivation, not voting: every validator re-fetches
the evidence and produces its own decision, and the leader is accepted only on
verdict equality plus a 25-point confidence tolerance. INCONCLUSIVE is a
first-class outcome, because an oracle forced to pick a side can be farmed.

v2 separates deciding from settling: `resolve()` writes the verdict and pays
nobody; `finalise()` pays round one once the appeal window closes; `appeal()`
is callable only by the loser, only by matching the bond, and pins up to two
counter-evidence URLs; `resolve_appeal()` reads both document sets and never
sees the first verdict, confidence or reasoning. Full rationale in
[`docs/optimistic_claim_oracle_v2.md`](docs/optimistic_claim_oracle_v2.md).

## Try it

Open the live client, press Connect wallet, post a falsifiable statement with
one evidence URL at the 0.01 GEN default bond, then switch accounts and send
the matching Challenge. The transaction panel reports each consensus stage -
proposing, committing, revealing - instead of a spinner, because on GenLayer a
broadcast transaction is not yet an accepted one.

## Run the tests

```bash
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/Scripts/python -m pytest tests -q
```

57 tests in about a second. They cover the state machine, permission edges,
both appeal windows, the 3x appeal payout, stake refunds, consensus failure,
prompt-injection attempts, and end-to-end value conservation. The tests run
against `tools/genshim/genlayer.py`, an offline GenVM shim; the consensus layer
itself is exercised on-chain by the deployments.

## Verify the deployment

```bash
npm install genlayer-js
node scripts/verify_deployment.mjs
```

Read-only, no private key. Per contract it checks that the on-chain code is
byte-identical to the repo source, that the schema compiled from the repo
matches the on-chain schema, that a view call actually executes, and that the
constructor values appear in the live storage slots. Add
`ORACLE_V2_ADDRESS=0x...` once v2 is deployed and it joins the same battery.

## Related

The escrow primitive (DeliverableEscrow - acceptance criteria in words settled
in basis points) is its own product at
[IanCao/settle](https://github.com/IanCao/settle), with its own client, tests and
deployment. The publishing pipeline for this repo's web client lives in
[IanCao/genlayer-primitives](https://github.com/IanCao/genlayer-primitives).
