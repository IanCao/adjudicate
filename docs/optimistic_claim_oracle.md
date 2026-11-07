# Optimistic Claim Oracle

**One line:** a bonded assertion primitive - anyone can post a claim with money
behind it, anyone can dispute it by matching the bond, and only a *contested*
claim ever reaches AI consensus.

`contracts/optimistic_claim_oracle.py` · GenVM `py-genlayer:1jb45aa8ynh...`

## The problem it solves

Oracles that ask a model "is X true?" and store the answer are cheap to write and
trivial to manipulate: the cost of a wrong answer is zero, so a wrong answer is
rational. The missing ingredient is not a better model, it is **falsifiability
with a price**.

This contract inverts the default. Truth is assumed optimistically; disputing it
costs as much as asserting it. The AI is not consulted unless someone has paid to
disagree, and it never decides who wins by itself - it decides *whether the
evidence supports the claim*, and the settlement rule maps that decision to money
mechanically.

## State

| Field | Meaning |
|---|---|
| `claims: TreeMap[u256, Claim]` | append-only registry, id never reused |
| `credits: TreeMap[Address, u256]` | pull-payment ledger for winnings |
| `next_id`, `min_bond`, `challenge_window` | protocol parameters fixed at deploy |

`Claim` is a flat `@allow_storage @dataclass` with primitive fields only
(`Address`, `str`, `u256`, `u8`) so it pickles deterministically.

## Lifecycle

```
post_claim(statement, evidence)  [payable, >= min_bond]
        |
        v
      OPEN  --challenge() during window, exact bond match--> CHALLENGED --resolve()--> SETTLED
        |
        +-- window closes with no challenger --> claim_uncontested() --> SETTLED, bond returned
```

`resolve()` may be called by anyone. The caller cannot influence the outcome:
statement and evidence are already pinned in storage, and the evidence set is
re-fetched by every validator.

## How consensus is used

`_adjudicate()` builds a leader/validator pair with `gl.vm.run_nondet_unsafe`:

- **leader** fetches every evidence URL (`gl.nondet.web.get`, capped at 6000 chars
  each), wraps each document in `<<<EVIDENCE i url>>> ... <<<END EVIDENCE i>>>`,
  and asks for a JSON decision.
- **validator** does the *same* fetch-and-judge pipeline independently. It never
  trusts the leader's reasoning. It rejects when:
  - the leader did not return a `gl.vm.Return`,
  - the payload is not a JSON string,
  - `verdict` differs from its own verdict,
  - `confidence` differs by more than 25 points.

Only the decision fields are compared; the prose is free to vary. That is the
difference between "consensus on a decision" and "consensus on a phrasing", and
it is what makes multi-model validator sets stable instead of permanently
undetermined.

Three verdicts, because two are not enough:

| Verdict | Settlement |
|---|---|
| `SUPPORTED` | author takes both bonds |
| `REFUTED` | challenger takes both bonds |
| `INCONCLUSIVE` | both bonds refunded, no slashing |

`INCONCLUSIVE` is deliberate. Evidence that merely fails to mention a claim is not
evidence against it, and an oracle that must pick a side on thin evidence is an
oracle that can be farmed by posting unfalsifiable claims.

## Prompt-injection posture

Evidence is attacker-controlled by definition - the author chooses the URLs. The
prompt therefore (a) states that everything between the delimiters is data, never
instruction, (b) names verdict-flipping and prompt-reveal attempts inside a
document as evidence of tampering that should *lower* confidence, and (c) forces
`response_format="json"` plus server-side coercion in `_coerce_decision()`, which
rejects any verdict outside the three allowed values before it can reach
settlement logic.

## Why it is reusable

The economics are generic: any registry where an assertion should be cheap to
make, expensive to make falsely, and free when nobody cares. Grant milestone
truthfulness, "did the project actually ship", prediction-market resolution with
a dispute bond, moderation appeals, insurance triggers with a challenge phase.

## Tests

`tests/test_optimistic_claim_oracle.py` covers bond rejection, challenge-window
enforcement, exact-bond matching, all three verdict paths including the
inconclusive refund, validator agreement and validator dissent, prompt-injection
tolerance (a document that tries to force `SUPPORTED`), and uncontested
expiry.

## Deploy

Constructor: `OptimisticClaimOracle(min_bond: u256, challenge_window: u256)`

```
min_bond          = 10000000000000000   # 1e16 wei = 0.01 GEN, also the floor
challenge_window  = 3600                # 1h on testnet; 86400 is sane for prod
```

Studio deploy payload: `{"args":[10000000000000000,3600]}`

Both arguments treat `0` as "use the protocol minimum" (1e16 wei / 60s). That
sentinel exists because deploy arguments are the one place where a units
mistake is unrecoverable: `1000000000000000` (one zero short) is 0.001 GEN,
sits below the floor, and reverts the whole deployment. When a value really is
too low the rollback message names both numbers so the fix is obvious from the
transaction history.

`withdraw()` pushes GEN through the ghost contract, so it needs a live network;
direct-mode tests assert on the `credits` ledger instead.
