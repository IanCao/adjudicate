# OptimisticClaimOracleV3 — the exit for states that trap funds

v1 settled a claim the moment it was decided. v2 made a decided claim
appealable. Both still assume the adjudication eventually produces *some*
answer. This version is about the third case: it never produces one.

## The failure v2 leaves open

Every settlement path in v2 — `finalise()`, `resolve_appeal()`,
`claim_uncontested()` — requires `resolve()` to have succeeded at least once.
`resolve()` runs a leader/validator consensus over fetched web documents. It can
fail, and its failures are not exotic:

- a document that renders differently for different fetches (JS, A/B, geo)
- a model family that keeps splitting three ways on a hedged claim
- a source that 502s for part of the validator set
- a page that is simply long enough to be truncated differently

When that happens the call reverts and the claim stays `CHALLENGED`. The author
cannot withdraw. The challenger cannot withdraw. There is no timeout, no owner,
no admin, no upgrade path. Both bonds are locked indefinitely, and the contract
is insolvent-by-deadlock one unlucky claim at a time.

An oracle is bought to price uncertainty. "The uncertainty turned out to be
unpriceable" has to be a state it can leave.

## What v3 adds

### 1. `release_stuck()` — a deadlock exit that pays nobody

```
CHALLENGED --(stuck_window elapses)--> release_stuck() --> REFUNDED
                                          author gets own bond back
                                          challenger gets own bond back
```

Three properties make this safe rather than a loophole:

- **No one profits.** Each party recovers exactly its own bond. There is no
  incentive to *engineer* a deadlock, which is what a "challenger wins by
  default" rule would create: post a claim, challenge it yourself, wait, keep
  the pot.
- **It is measured from the challenge, not from the last failed attempt.**
  Measuring from the last attempt would let a persistent resolver keep a claim
  alive forever by calling and reverting — the exact liveness bug being fixed.
- **It ends `REFUNDED`, not `SETTLED`.** No verdict was issued, so an indexer
  that counts settled claims does not count this one. `claim.verdict` stays
  empty and the reasoning field says why.

`stuck_window` has a floor of 3600 seconds and a default of 86400 (pass `0` to
get the default). The floor exists for the same reason `min_bond` has one: a
two-second exit would let a griefer release every dispute before an honest
resolver ran, turning the verdict machinery into decoration.

### 2. `get_record()` — what an address's claims actually did

```
Record { posted, upheld, refuted, inconclusive, uncontested }
```

Integrators that want to price a counterparty's assertions had nothing to read
before this. The history of an address's claims is the only signal the contract
can produce without trusting an outside indexer.

It is counters, not a score, deliberately. A score invites gaming and hides the
sample size; five counters let a caller decide whether "3 refuted of 4" is noise
or a pattern.

Records are written on **decisions only** — `finalise()`, `resolve_appeal()`,
`claim_uncontested()`, and `release_stuck()`. The last one writes
`inconclusive` for both sides, because that is what happened: nobody was shown
to be right and nobody was shown to be wrong.

### 3. The read side of the same rule

`is_stuck(claim_id)` and `stuck_after()` let a client show "this dispute can be
released in 4h" instead of an indefinite spinner.

## What did not change

Consensus, both prompts, the appeal round and the payout table are byte-for-byte
the same as v2. The prompt-injection guard, the blind second round, the
`INCONCLUSIVE` refund path and the confidence tolerance are untouched.

v3 is additive on purpose: it changes *when* money can move, never *who deserves
it*.

## Cost of the exit

The exit is not free. A well-funded attacker can force a refund on any claim by
making adjudication fail — spam the evidence URLs, or pick documents that render
unreliably. But that attack already existed in v2 and was strictly worse: the
attacker also got the victim's bond locked forever, at no cost to itself. v3
converts "permanent loss" into "no decision", which is the best available
outcome when the evidence cannot be judged.

## Tests

23 new tests in `tests/test_oracle_v3_logic.py`, 80 in the repository:

| Area | What is asserted |
|---|---|
| Constructor | `0` maps to the default, the 3600s floor is enforced, negative rejected |
| The exit | reverts one second early, succeeds at the boundary, permissionless, one-shot |
| Money | both bonds returned exactly, value conserved, no credit for the caller |
| Ordering | cannot release an `OPEN` or a decided claim; a released claim cannot be resolved or appealed |
| The scenario | a failing `resolve()` leaves the claim releasable and both parties whole |
| Ledger | `posted` / `upheld` / `refuted` / `inconclusive` / `uncontested` across all four settlement paths |
| Isolation | two parties never share a record; each side of a decided claim is written |

```
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests -q   # 80 passed
```

## Deploy

```bash
min_bond          10000000000000000   # 0.01 GEN
challenge_window  86400               # 1 day
appeal_window     3600                # 1 hour
stuck_window      259200              # 3 days, or 0 for the 1 day default
```

The web client detects v3 by the presence of `release_stuck` in the schema and
shows a **Release · refund both** button on a dispute that has passed the
window. Paste a v3 address into the contract field; v1 and v2 addresses keep
working unchanged.
