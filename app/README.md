# Adjudicate - the web client

A single page, no build step to run it, that talks to the oracle through
`genlayer-js`. It is the part of the project that makes the contract usable by
someone who is not going to write a script.

```
index.html      structure
styles.css      the whole stylesheet, ~300 lines, no framework
app.js          client, state machine mapping, transaction lifecycle
vendor/         genlayer-js 1.1.8 bundled with esbuild (~600 KB)
```

## The live URL

**https://iancao.github.io/genlayer-primitives/**

The site is served by GitHub Pages from the `gh-pages` branch of the
[genlayer-primitives](https://github.com/IanCao/genlayer-primitives)
repository, where the publishing pipeline lives. This copy of `app/` is the
source of truth for the client; that repo's `scripts/publish_app.sh` is what
rebuilds the branch from it.

## Run it locally

Any static server works; ES modules need `http://`, not `file://`:

```bash
cd app
python -m http.server 8080
# open http://localhost:8080
```

## Rebuild the vendored client

Only needed when bumping `genlayer-js`:

```bash
npx esbuild app/vendor-entry.js --bundle --format=esm --minify \
  --outfile=app/vendor/genlayer.js
```

The bundle is committed on purpose: a reviewer opening the page should not depend
on a CDN being reachable or on a build succeeding on their machine.

## What it actually does

**Reads never need a wallet.** `claim_count()` and `get_claim(id)` are `gen_call`
simulations against Studionet, so the page renders the full claim list for any
visitor, including the Steward reviewing it.

**Writes go through the full lifecycle.** `post_claim`, `challenge`, `resolve`,
`appeal`, `resolve_appeal`, `finalise`, `claim_uncontested` and `withdraw` are all
signed through the injected wallet. The panel in the bottom right reports each
stage instead of showing a spinner:

```
waiting for your signature  ->  submitted  ->  in consensus · proposing
                            ->  in consensus · committing
                            ->  in consensus · revealing
                            ->  accepted in Ns   (or: rejected: <revert text>)
```

That distinction matters on GenLayer: a transaction that has been broadcast is not
a transaction that has been accepted, and a contract can still revert after
consensus. The panel polls `getTransaction` until the status leaves the in-flight
states, then reads the verdict out of the receipt.

**The UI is derived from the contract, not hardcoded.** On load the page calls
`getContractSchema(address)` and looks for `resolve_appeal`. A v1 contract without
an appeal round shows the v1 flow; a v2 contract shows `Finalise` and `Appeal`.
The same page therefore works before and after the upgrade, and against any
address typed into the Contract field.

## Where the money logic lives

The client never computes a payout. It reads `bond`, `appeal_bond` and the
deadlines from the contract and displays them; the button that says
`Challenge · 0.01 GEN` sends exactly `claim.bond` as `value`, so the transaction
either matches the bond or reverts on chain. There is no client-side arithmetic
that could disagree with the contract.
