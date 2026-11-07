# OptimisticClaimOracleV2 - the right of appeal

v1 answered one question: *is this claim supported by public evidence?* It
answered it with a bonded challenge and a consensus-checked adjudication. What it
could not answer is the question that follows immediately: **what if the
adjudication was wrong?**

v2 adds one thing and only one thing - a loser's appeal - because that is the
gap that makes the difference between a primitive you can point at and a
primitive you can settle money with.

## The failure v1 left open

```
post_claim ──▶ challenge ──▶ resolve ──▶ paid. Final.
```

A single probabilistic decision moved money. Three ways that goes wrong, none of
them hypothetical:

1. **A page renders differently on two fetches.** A JS-heavy changelog returns
   "v2 shipped" to one worker and an empty shell to another. The validators
   disagree, consensus fails, and the claim sits unresolved forever - or worse,
   they happen to agree on the wrong reading.
2. **A document is misread consistently.** Two models from the same family make
   the same systematic error on the same document. Validator independence does
   not help when the error is correlated.
3. **Evidence is thin and the model guesses.** Confidence 55, verdict REFUTED,
   and the author's bond is gone with no route back.

v1's only remedy for a bad adjudication is "post a new claim", which is not a
remedy: it costs another bond and the first payout has already happened.

## What v2 changes

```
post_claim ──▶ challenge ──▶ resolve        (verdict written, NO payment)
                                 │
                 ┌───────────────┴────────────────┐
      appeal() by the loser                 finalise() after the window
      bond matched + counter evidence        pays round one
                 │                                  │
        resolve_appeal()  ──▶ paid, final      paid, final
```

Four properties, each of which is a deliberate choice:

**Deciding is not settling.** `resolve()` writes a verdict and opens an appeal
window. It pays nobody. `finalise()` and `resolve_appeal()` are the only
functions that credit an account. This is what makes an appeal possible at all -
in v1 the money was already gone by the time anyone could object.

**Only the loser may appeal, and only by matching the bond.** The side that won
has nothing to appeal, and a third party cannot pay to reopen a dispute it has no
stake in. When round one is `INCONCLUSIVE` there is no loser, so either party may
appeal - both have equally good grounds to say the evidence was misread.

**Round two is blind.** The round one verdict, confidence and reasoning are
never placed in the round two prompt. An appeal that can see the first answer
degrades into a request to justify it, and the expensive part of adjudication -
actually reading the documents - gets skipped. The appellant instead submits up
to two counter-evidence URLs, which are fetched and rendered alongside the
author's documents in a separate, clearly labelled block.

**INCONCLUSIVE stays a first-class outcome in both rounds.** Both stakes go back.
An oracle forced to pick a side is farmable, because a fabricated verdict and a
considered one are indistinguishable from outside the contract.

## State machine

| Status | Reachable by | Meaning |
|---|---|---|
| `OPEN` | `post_claim` | bond locked, challenge window running |
| `CHALLENGED` | `challenge` | bond matched, awaiting round one |
| `RESOLVED` | `resolve` | decided, **unpaid**, appeal window running |
| `APPEALED` | `appeal` | loser matched the bond, counter evidence pinned |
| `SETTLED` | `finalise` / `resolve_appeal` / `claim_uncontested` | paid, terminal |

Illegal transitions revert with a message naming the state, for example
`claim is not awaiting an appeal` - the same discipline as v1, because a revert
string is the only debug channel a caller has.

## Payout table

| Path | `SUPPORTED` | `REFUTED` | `INCONCLUSIVE` |
|---|---|---|---|
| `finalise` (no appeal) | author gets 2×bond | challenger gets 2×bond | each gets 1×bond |
| `resolve_appeal` | author gets 3×bond | challenger gets 3×bond | author 1×, challenger 1×, appellant 1× |

The pot is `2×bond` before an appeal, `3×bond` after. Every path sums to the pot
exactly - there is a test for that (`test_value_is_conserved_through_an_appeal`),
because a payout table that leaks wei is the kind of bug that only shows up in
production.

Credits are still a pull ledger (`credits` + `withdraw()`), so a recipient that
reverts cannot revert the adjudication.

## Consensus

Unchanged from v1 on purpose, in both rounds:

- the leader fetches the documents, wraps each in explicit delimiters, and
  returns canonical JSON;
- every validator **independently re-fetches and re-derives** its own decision;
- the leader is accepted only if `verdict` matches **and** `confidence` is within
  25 points of the validator's own read;
- reasoning text is allowed to differ - only decision fields are compared;
- a document that tries to dictate the verdict is treated as tampering and
  lowers confidence.

The two rounds differ only in inputs: round one sees the author's documents,
round two sees both sides' documents and no memory of round one.

## Deploy

```python
# Studio, chain 61999
OptimisticClaimOracleV2(
    min_bond        = 10000000000000000,   # 1e16 wei = 0.01 GEN, the protocol floor
    challenge_window = 86400,              # 1 day to challenge
    appeal_window    = 3600,               # 1 hour to appeal a decided claim
)
```

Both windows accept `0` as "use the protocol minimum" (60s challenge, 300s
appeal), and both refuse anything below their floor with a message naming the
value you passed and the value required.

## Verify a deployment

```bash
npm install genlayer-js
ORACLE_V2_ADDRESS=0x... node scripts/verify_deployment.mjs
```

With that variable set the script appends v2 to the contracts it checks: on-chain
source diff, schema diff against the repo file, a live `claim_count()` call, and
the three constructor values located inside the contract's own storage slots.

## Tests

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests -q
```

30 tests for v2 alone, 86 for the repo. The v2 suite targets the parts that are
easy to get subtly wrong:

- `resolve()` credits nobody, and `finalise()` is refused while the window is open
- the winner, a stranger, and a second appellant are all refused
- a claim that is already `APPEALED` cannot be finalised, and a `SETTLED` claim
  cannot be appealed
- round two pays 3×bond to whichever side prevails, and refunds each stake on
  `INCONCLUSIVE`
- round two's prompt contains no trace of round one's reasoning, and round one's
  prompt contains no counter evidence
- consensus failure in round two leaves the claim `APPEALED` and pays nobody
- value is conserved end to end

## Known limits, stated on purpose

- **One appeal, not a court.** Round two is final. A third round needs juror
  selection, which needs randomness, which is a separate primitive.
- **The appeal bond equals the claim bond.** That keeps the arithmetic symmetric
  and cheap to verify, but it means a claim is never worth more than its own
  bond to defend.
- **Round two can flip on the same evidence.** That is the point of an
  independent second reading, but it also means an appellant with genuinely bad
  evidence still gets one coin-flip on a lenient validator set. The cost of that
  coin-flip is a bond.
