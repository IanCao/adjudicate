// Verify that the contracts deployed on GenLayer Studionet are exactly the
// source in this repo, with exactly the constructor arguments we intended.
//
// Read-only: no private key, no signing, no gas. Every check reads chain state.
//
//   npm install genlayer-js
//   node scripts/verify_deployment.mjs
//
// Checks per contract
//   1. code      getContractCode(address) is byte-identical to the repo file
//                (line endings normalised: Studio stores CRLF)
//   2. schema    getContractSchemaForCode(repo source) == getContractSchema(address)
//   3. exec      a view call actually returns, proving the contract runs
//   4. storage   the constructor values appear in the contract's storage slots
// For the oracle there is a fifth, independent check: post_claim() is payable
// and its guard reverts with both the sent value and the stored min_bond, so a
// value-0 simulation prints the live min_bond in plain text.
import {createClient, chains} from "genlayer-js";
import {readFileSync} from "node:fs";
import {createHash} from "node:crypto";
import {fileURLToPath} from "node:url";
import {dirname, join} from "node:path";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");

const CONTRACTS = [
  {
    name: "OptimisticClaimOracle",
    address: "0x3a3A9c681cD547ccb293a6329839cc50D529fAd9",
    source: "contracts/optimistic_claim_oracle.py",
    ctor: [
      ["min_bond", 10000000000000000n],
      ["challenge_window", 86400n],
    ],
    view: {fn: "claim_count", args: []},
    guard: {fn: "post_claim", args: ["probe", "https://example.com"]},
  },
];

// v2 is optional: pass its address and it joins the same battery of checks.
// The constructor values below are the reference deployment; deploying with
// different arguments is fine, the storage check just needs them updated here.
if (process.env.ORACLE_V2_ADDRESS) {
  CONTRACTS.push({
    name: "OptimisticClaimOracleV2",
    address: process.env.ORACLE_V2_ADDRESS,
    source: "contracts/optimistic_claim_oracle_v2.py",
    ctor: [
      ["min_bond", BigInt(process.env.ORACLE_V2_MIN_BOND ?? "10000000000000000")],
      ["challenge_window", BigInt(process.env.ORACLE_V2_CHALLENGE_WINDOW ?? "86400")],
      ["appeal_window", BigInt(process.env.ORACLE_V2_APPEAL_WINDOW ?? "3600")],
    ],
    view: {fn: "claim_count", args: []},
    guard: {fn: "post_claim", args: ["probe", "https://example.com"]},
  });
}

// Studio returns the code with CRLF endings; the repo uses LF.
const normalise = (s) => s.replace(/\r\n/g, "\n").replace(/\r/g, "\n");
const sha = (s) => createHash("sha256").update(s).digest("hex").slice(0, 16);
const canonical = (o) => JSON.stringify(o, Object.keys(o).sort());

const client = createClient({
  chain: chains.studionet,
  endpoint: "https://studio.genlayer.com/api",
});

// viem rewrites RPC errors into "execution failed". Wrap fetch so the raw
// JSON-RPC body survives: it carries the revert message in base64.
let lastRawError = null;
const realFetch = globalThis.fetch;
globalThis.fetch = async (...args) => {
  const res = await realFetch(...args);
  const text = await res.clone().text();
  if (text.includes('"error"')) lastRawError = text;
  return new Response(text, {status: res.status, headers: res.headers});
};

/** Scan a storage slot for an integer's minimal little-endian byte form. */
function findInSlots(contractState, value) {
  const bytes = [];
  let x = value;
  while (x > 0n) {
    bytes.push(Number(x & 0xffn));
    x >>= 8n;
  }
  const needle = Buffer.from(bytes).toString("hex");
  const hits = [];
  for (const [key, encoded] of Object.entries(contractState ?? {})) {
    const buf = Buffer.from(encoded, "base64");
    if (buf.length > 500) continue; // skip the code slot
    const at = buf.toString("hex").indexOf(needle);
    if (at >= 0) hits.push(`slot ${key.slice(0, 10)}… byte ${at / 2}`);
  }
  return hits;
}

const results = [];
const chainId = parseInt(await client.request({method: "eth_chainId", params: []}), 16);
const head = await client.request({method: "eth_blockNumber", params: []});
console.log(`Studionet  chainId=${chainId}  head=${parseInt(head, 16)}\n`);

for (const c of CONTRACTS) {
  console.log("=".repeat(72));
  console.log(`${c.name}  ${c.address}`);

  const repoSource = readFileSync(join(ROOT, c.source), "utf8");
  const onchainSource = normalise(await client.getContractCode(c.address));
  const codeOk = normalise(repoSource) === onchainSource;
  console.log(`  code    ${codeOk ? "identical" : "DIFFERS"}  sha256 ${sha(normalise(repoSource))} vs ${sha(onchainSource)}`);

  const fromSource = await client.getContractSchemaForCode(repoSource);
  const fromChain = await client.getContractSchema(c.address);
  const schemaOk = canonical(fromSource) === canonical(fromChain);
  console.log(`  schema  ${schemaOk ? "identical" : "DIFFERS"}  ${Object.keys(fromChain.methods).length} methods, ctor [${fromChain.ctor.params.map((p) => p[0]).join(", ")}]`);

  let execOk = false;
  try {
    const value = await client.readContract({
      address: c.address,
      functionName: c.view.fn,
      args: c.view.args,
      jsonSafeReturn: false,
    });
    const shown = typeof value === "bigint" ? value.toString() : JSON.stringify(value);
    console.log(`  exec    ${c.view.fn}() -> ${shown}`);
    execOk = true;
  } catch (err) {
    console.log(`  exec    ${c.view.fn}() FAILED ${String(err?.message ?? err).split("\n")[0]}`);
  }

  // Drive the payable guard to obtain a receipt carrying contract_state.
  lastRawError = null;
  try {
    await client.simulateWriteContract({
      address: c.address,
      functionName: c.guard.fn,
      args: c.guard.args,
    });
  } catch {
    /* a revert is what we want here */
  }

  let stateOk = false;
  if (lastRawError) {
    const receipt = JSON.parse(lastRawError).error.data.receipt;
    const message = Buffer.from(receipt.result, "base64").subarray(1).toString("utf8");
    console.log(`  guard   ${message}`);

    const allFound = c.ctor.every(([, value]) => findInSlots(receipt.contract_state, value).length > 0);
    for (const [label, value] of c.ctor) {
      const hits = findInSlots(receipt.contract_state, value);
      console.log(`  slot    ${label.padEnd(18)} ${value.toString().padEnd(20)} ${hits.length ? hits.join(", ") : "NOT FOUND"}`);
    }
    stateOk = allFound;
  } else {
    console.log("  guard   no revert captured, cannot inspect storage");
  }

  results.push({name: c.name, codeOk, schemaOk, execOk, stateOk});
  console.log("");
}

console.log("=".repeat(72));
let failed = 0;
for (const r of results) {
  const passed = r.codeOk && r.schemaOk && r.execOk && r.stateOk;
  if (!passed) failed++;
  console.log(
    `${passed ? "PASS" : "FAIL"}  ${r.name.padEnd(22)} code=${r.codeOk} schema=${r.schemaOk} exec=${r.execOk} storage=${r.stateOk}`,
  );
}
process.exit(failed === 0 ? 0 : 1);
