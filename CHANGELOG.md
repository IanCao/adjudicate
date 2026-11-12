# Changelog

Everything that changed between deployed versions of this project, in the order
a reviewer would check it: constructor, storage, methods, payouts.

## v3 — the deadlock exit and the ledger

`contracts/optimistic_claim_oracle_v3.py`

**Problem it answers.** Every v2 settlement path needs `resolve()` to succeed
once. When consensus cannot be reached — a page that renders differently per
fetch, a model set that splits three ways — the call reverts and the claim stays
`CHALLENGED` forever. Both bonds are locked with no owner, no timeout and no
upgrade path.

| Surface | v2 | v3 |
|---|---|---|
| Constructor | `(min_bond, challenge_window, appeal_window)` | `+ stuck_window` (floor 3600s, `0` → 86400s) |
| `Claim` struct | 14 fields | `+ challenged_at: u256` |
| New storage | — | `records: TreeMap[Address, Record]` |
| New status | — | `REFUNDED` — released without a decision |
| New writes | — | `release_stuck(claim_id)` |
| New views | — | `get_record(account)`, `is_stuck(id)`, `stuck_after()` |
| Payout on deadlock | none — funds frozen | both parties recover their own bond |
| Reputation | none | five counters per address, written on decisions only |

Unchanged on purpose: both prompts, the consensus rule, the confidence
tolerance, the appeal round and every existing payout split. v3 changes *when*
money can move, never *who deserves it*.

Tests: 23 new (`tests/test_oracle_v3_logic.py`), 80 total.

## v2 — the appeal round

`contracts/optimistic_claim_oracle_v2.py`

**Problem it answers.** v1 paid out the moment `resolve()` returned. One
adjudication, final: a validator set that misread a page moved money
permanently, and nobody can appeal a verdict that has already paid.

| Surface | v1 | v2 |
|---|---|---|
| Constructor | `(min_bond, challenge_window)` | `+ appeal_window` (floor 300s) |
| New status | — | `RESOLVED` (decided, unpaid, appealable), `APPEALED` |
| `resolve()` | decides **and pays** | decides, **pays nobody**, opens the window |
| New writes | — | `appeal(claim_id, counter_evidence)`, `resolve_appeal(claim_id)`, `finalise(claim_id)` |
| New views | — | `is_appealable`, `pot_of` |
| Payout | winner takes 2× bond | round two pays 3× bond to the winner; `finalise()` pays round one |

Two design rules that carry the whole version:

- **Round two never sees round one.** The first verdict, its confidence and its
  reasoning are absent from the appeal prompt. An appeal that can read the first
  answer becomes a request to defend it.
- **`INCONCLUSIVE` refunds everyone, in both rounds.** A forced verdict is
  farmable; a fabricated answer and a considered one look identical from outside.

Tests: 30 new, 57 total at that point.

## v1 — the bonded claim oracle

`contracts/optimistic_claim_oracle.py`

Assertion staked with a bond, challenge by matching the bond, adjudication by
leader/validator consensus over fetched documents, pull-payment ledger for
withdrawals. An uncontested claim never reaches a model: it ages out of the
challenge window and the bond is returned.
