// Adjudicate - a client for the bonded claim oracle.
//
// Everything here goes through genlayer-js (bundled into ./vendor/genlayer.js),
// so the page talks to the same RPC the contracts do: reads are `gen_call`
// simulations, writes are signed transactions that go through GenLayer
// consensus before they are accepted.
//
// The interesting part is not the form, it is the transaction lifecycle: a write
// is not "sent and done", it moves through proposing / committing / revealing
// before it is ACCEPTED. The bottom-right panel reports those states, and it
// reports the revert text when consensus rejects a call.

import { createClient, chains } from "./vendor/genlayer.js";

const ENDPOINT = "https://studio.genlayer.com/api";
const CHAIN_ID_HEX = "0xf22f"; // 61999, Studionet
const ZERO = "0x0000000000000000000000000000000000000000";

const $ = (id) => document.getElementById(id);

// The field order of the Claim struct, so a decoded array can be mapped even
// when the RPC hands back positional values. Kept per contract version because
// v2 appended four fields rather than reshuffling the old ones.
const CLAIM_FIELDS_V1 = [
  "author", "statement", "evidence", "bond", "challenge_deadline",
  "status", "challenger", "verdict", "confidence", "reasoning", "settled_at",
];
const CLAIM_FIELDS_V2 = [
  ...CLAIM_FIELDS_V1,
  "appeal_deadline", "appellant", "appeal_bond", "counter_evidence",
];
const CLAIM_FIELDS_V3 = [...CLAIM_FIELDS_V2, "challenged_at"];

const state = {
  address: new URLSearchParams(location.search).get("address") || $("addressInput").value.trim(),
  account: null,
  capabilities: new Set(),
  claims: [],
  busy: false,
};

const reader = createClient({ chain: chains.studionet, endpoint: ENDPOINT });
let wallet = null;

/* ------------------------------------------------------------------ helpers */

const short = (a) => (a && a.length > 12 ? a.slice(0, 6) + "…" + a.slice(-4) : a || "");
const isZero = (a) => !a || String(a).toLowerCase() === ZERO;

/** wei -> "0.01 GEN", without floating point drift. */
function fmtGen(wei) {
  const v = BigInt(wei ?? 0);
  const whole = v / 10n ** 18n;
  const frac = (v % 10n ** 18n).toString().padStart(18, "0").replace(/0+$/, "");
  return (frac ? `${whole}.${frac.slice(0, 4)}` : `${whole}`) + " GEN";
}

/** "0.01" -> 10000000000000000n, digit-exact. */
function parseGen(text) {
  const clean = String(text).trim();
  if (!/^\d*(\.\d*)?$/.test(clean) || clean === "" || clean === ".") {
    throw new Error("bond must be a decimal number of GEN");
  }
  const [w, f = ""] = clean.split(".");
  return BigInt(w || "0") * 10n ** 18n + BigInt((f + "000000000000000000").slice(0, 18));
}

function normaliseClaim(raw, version) {
  const fields =
    version >= 3 ? CLAIM_FIELDS_V3 : version >= 2 ? CLAIM_FIELDS_V2 : CLAIM_FIELDS_V1;
  let obj = raw;
  if (Array.isArray(raw)) {
    obj = {};
    fields.forEach((name, i) => (obj[name] = raw[i]));
  }
  // Not every decode path preserves key names; fall back to positional order.
  if (obj && typeof obj === "object" && obj.statement === undefined && Array.isArray(obj.__values)) {
    obj = {};
    fields.forEach((name, i) => (obj[name] = raw.__values[i]));
  }
  return {
    author: String(obj.author ?? ""),
    statement: String(obj.statement ?? ""),
    evidence: String(obj.evidence ?? ""),
    bond: BigInt(obj.bond ?? 0),
    challengeDeadline: Number(obj.challenge_deadline ?? 0),
    status: String(obj.status ?? ""),
    challenger: String(obj.challenger ?? ""),
    verdict: String(obj.verdict ?? ""),
    confidence: Number(obj.confidence ?? 0),
    reasoning: String(obj.reasoning ?? ""),
    appealDeadline: Number(obj.appeal_deadline ?? 0),
    appellant: String(obj.appellant ?? ""),
    appealBond: BigInt(obj.appeal_bond ?? 0),
    counterEvidence: String(obj.counter_evidence ?? ""),
    challengedAt: Number(obj.challenged_at ?? 0),
  };
}

function countdown(deadlineSeconds) {
  if (!deadlineSeconds) return "—";
  const left = deadlineSeconds - Math.floor(Date.now() / 1000);
  if (left <= 0) return "closed";
  const h = Math.floor(left / 3600);
  const m = Math.floor((left % 3600) / 60);
  const s = left % 60;
  return h > 0 ? `${h}h ${m}m` : m > 0 ? `${m}m ${s}s` : `${s}s`;
}

/* --------------------------------------------------------------- tx panel */

function txReset(title) {
  $("txlogBody").innerHTML = "";
  $("txlog").classList.add("show");
  txStep(title, "active");
}

function txStep(text, kind = "active", hash) {
  const row = document.createElement("div");
  row.className = "tx-step " + kind;
  row.innerHTML = `<span class="dot"></span><span>${text}${
    hash ? ` <a class="tx-hash" href="https://explorer-studio.genlayer.com/transaction/${hash}" target="_blank" rel="noreferrer">${short(hash)}</a>` : ""
  }</span>`;
  $("txlogBody").appendChild(row);
  $("txlogBody").scrollTop = $("txlogBody").scrollHeight;
  return row;
}

function txDone(previous, text, kind) {
  if (previous) previous.className = "tx-step " + kind;
  txStep(text, kind);
}

/* ------------------------------------------------------------------ wallet */

async function ensureChain() {
  try {
    await window.ethereum.request({
      method: "wallet_switchEthereumChain",
      params: [{ chainId: CHAIN_ID_HEX }],
    });
  } catch (err) {
    if (err?.code !== 4902 && !/Unrecognized chain/i.test(err?.message || "")) throw err;
    await window.ethereum.request({
      method: "wallet_addEthereumChain",
      params: [
        {
          chainId: CHAIN_ID_HEX,
          chainName: "GenLayer Studionet",
          nativeCurrency: { name: "GEN", symbol: "GEN", decimals: 18 },
          rpcUrls: ["https://studio.genlayer.com/api"],
          blockExplorerUrls: ["https://explorer-studio.genlayer.com"],
        },
      ],
    });
  }
}

async function connect() {
  if (!window.ethereum) {
    alert("No wallet found. Install MetaMask (or any EIP-1193 wallet) to post or challenge.");
    return;
  }
  await ensureChain();
  const [account] = await window.ethereum.request({ method: "eth_requestAccounts" });
  state.account = account;
  wallet = createClient({
    chain: chains.studionet,
    endpoint: ENDPOINT,
    provider: window.ethereum,
    account,
  });
  $("connectBtn").textContent = short(account);
  $("connectBtn").classList.add("btn-accent");
  render();
}

/* ------------------------------------------------------------------- reads */

async function loadContract(address) {
  state.address = address;
  $("addressInput").value = address;
  $("explorerLink").href = `https://explorer-studio.genlayer.com/address/${address}`;
  $("versionTag").textContent = "loading…";

  let schema;
  try {
    schema = await reader.getContractSchema(address);
  } catch (err) {
    $("claimsList").innerHTML = `<p class="empty">Could not read a contract at that address: ${err.message}</p>`;
    $("versionTag").textContent = "unknown";
    return;
  }

  const methods = Object.keys(schema.methods || {});
  state.capabilities = new Set(methods);
  // Three shapes so far, and they are nested: v3 has every v2 method plus the
  // deadlock exit, so test for the newest capability first.
  const version = methods.includes("release_stuck")
    ? 3
    : methods.includes("resolve_appeal")
      ? 2
      : 1;
  state.version = version;
  $("versionTag").textContent = `v${version}`;
  $("versionTag").title = methods.join(", ");

  state.stuckWindow = 0;
  if (version >= 3) {
    try {
      state.stuckWindow = Number(
        (await reader.readContract({
          address,
          functionName: "stuck_after",
          args: [],
          jsonSafeReturn: true,
        })) ?? 0,
      );
    } catch {
      state.stuckWindow = 0;
    }
  }

  await refresh();
}

async function refresh() {
  try {
    const countRaw = await reader.readContract({
      address: state.address,
      functionName: "claim_count",
      args: [],
      jsonSafeReturn: true,
    });
    const count = Number(countRaw ?? 0);
    const claims = [];
    for (let id = 0; id < count; id++) {
      try {
        const raw = await reader.readContract({
          address: state.address,
          functionName: "get_claim",
          args: [BigInt(id)],
          jsonSafeReturn: true,
        });
        claims.push({ id, ...normaliseClaim(raw, state.version) });
      } catch {
        /* a claim that cannot be read should not break the list */
      }
    }
    state.claims = claims.reverse();

    if (state.account) {
      try {
        const c = await reader.readContract({
          address: state.address,
          functionName: "get_credit",
          args: [state.account],
          jsonSafeReturn: true,
        });
        state.credit = BigInt(c ?? 0);
      } catch {
        state.credit = 0n;
      }
    }
    render();
  } catch (err) {
    $("claimsList").innerHTML = `<p class="empty">Read failed: ${err.message}</p>`;
  }
}

/* ------------------------------------------------------------------ writes */

async function send({ fn, args, value = 0n, label }) {
  if (!wallet || !state.account) {
    alert("Connect a wallet first.");
    return;
  }
  state.busy = true;
  render();
  txReset(label);
  const signing = txStep("waiting for your signature", "active");

  let hash;
  try {
    hash = await wallet.writeContract({
      address: state.address,
      functionName: fn,
      args,
      value,
    });
  } catch (err) {
    txDone(signing, "rejected before sending: " + (err?.shortMessage || err?.message || err), "fail");
    state.busy = false;
    render();
    return;
  }

  txDone(signing, "submitted", "done");
  const pending = txStep("in consensus · submitted", "active", hash);
  const started = Date.now();

  // Poll until the transaction leaves the in-flight states. GenLayer writes are
  // not instant: they are proposed, committed, revealed, then accepted.
  let tx = null;
  let lastStatus = "";
  for (let i = 0; i < 240; i++) {
    try {
      tx = await reader.getTransaction({ hash });
      const status = String(tx?.status ?? "");
      if (status && status !== lastStatus) {
        lastStatus = status;
        if (status !== "ACCEPTED") {
          pending.querySelector("span:last-child").textContent =
            `in consensus · ${status.toLowerCase()} · ${Math.round((Date.now() - started) / 1000)}s`;
        }
      }
      if (["ACCEPTED", "FINALIZED", "UNDETERMINED", "CANCELED", "VALIDATORS_TIMEOUT", "LEADER_TIMEOUT"].includes(status)) {
        break;
      }
    } catch {
      /* transient RPC errors during polling are expected */
    }
    await new Promise((r) => setTimeout(r, 3000));
  }

  const ok = ["ACCEPTED", "FINALIZED"].includes(String(tx?.status ?? ""));
  if (ok) {
    txDone(pending, `accepted in ${Math.round((Date.now() - started) / 1000)}s`, "done");
  } else {
    const detail =
      tx?.consensus_data?.leader_receipt?.[0]?.error ||
      tx?.result_name ||
      tx?.status ||
      "no receipt";
    txDone(pending, "rejected: " + String(detail).slice(0, 220), "fail");
  }

  state.busy = false;
  await refresh();
}

/* ----------------------------------------------------------------- rendering */

function actionButtons(claim) {
  const now = Math.floor(Date.now() / 1000);
  const mine = (a) => state.account && String(a).toLowerCase() === String(state.account).toLowerCase();
  const buttons = [];

  if (claim.status === "OPEN") {
    if (now < claim.challengeDeadline) {
      buttons.push({
        label: `Challenge · ${fmtGen(claim.bond)}`,
        fn: "challenge",
        args: [BigInt(claim.id)],
        value: claim.bond,
      });
    } else {
      // The window is over and nobody disputed it: anyone may close it out and
      // return the bond. This path never touches a model.
      buttons.push({ label: "Close window · return bond", fn: "claim_uncontested", args: [BigInt(claim.id)] });
    }
  }
  if (claim.status === "CHALLENGED") {
    buttons.push({ label: "Resolve (round 1)", fn: "resolve", args: [BigInt(claim.id)] });
    // v3 only: a dispute that never produced a decision is releasable, and the
    // money goes back to both sides. Shown only once the window has passed, so
    // the button cannot be used to dodge a verdict.
    if (state.stuckWindow > 0 && claim.challengedAt > 0) {
      const releasableAt = claim.challengedAt + state.stuckWindow;
      if (now >= releasableAt) {
        buttons.push({
          label: "Release · refund both",
          fn: "release_stuck",
          args: [BigInt(claim.id)],
        });
      }
    }
  }
  if (claim.status === "RESOLVED") {
    const open = now < claim.appealDeadline;
    const loser =
      claim.verdict === "REFUTED" ? claim.author : claim.verdict === "SUPPORTED" ? claim.challenger : null;
    if (open && (loser === null || mine(loser))) {
      buttons.push({
        label: `Appeal · ${fmtGen(claim.bond)}`,
        fn: "appeal",
        args: [BigInt(claim.id)],
        value: claim.bond,
        needsCounterEvidence: true,
      });
    }
    if (!open) {
      buttons.push({ label: "Finalise", fn: "finalise", args: [BigInt(claim.id)] });
    }
  }
  if (claim.status === "APPEALED") {
    buttons.push({ label: "Resolve appeal", fn: "resolve_appeal", args: [BigInt(claim.id)] });
  }
  return buttons;
}

function claimCard(claim) {
  const el = document.createElement("article");
  el.className = "claim";

  const evidence = claim.evidence.split("\n").filter(Boolean);
  const counter = claim.counterEvidence.split("\n").filter(Boolean);
  const open = claim.status === "OPEN";
  const deadline = open ? claim.challengeDeadline : claim.appealDeadline;

  const verdictBlock =
    claim.verdict && claim.status !== "OPEN"
      ? `<div class="verdict">
           <b>${claim.verdict}</b> · confidence ${claim.confidence}/100 · round ${claim.status === "APPEALED" ? 2 : 1}
           ${claim.reasoning ? `<div style="margin-top:4px;color:#5a5a62">${escapeHtml(claim.reasoning)}</div>` : ""}
         </div>`
      : "";

  el.innerHTML = `
    <div class="claim-top">
      <span class="claim-id">#${claim.id}</span>
      <span class="status ${claim.status}">${claim.status || "UNKNOWN"}</span>
      <span class="claim-meta">
        <span>bond <code>${fmtGen(claim.bond)}</code></span>
        ${claim.status !== "SETTLED" ? `<span>pot <code>${fmtGen(claim.bond * 2n + claim.appealBond)}</code></span>` : ""}
        ${deadline ? `<span>${claim.status === "OPEN" ? "challenge closes in" : "appeal closes in"} <code>${countdown(deadline)}</code></span>` : ""}
      </span>
    </div>
    <p class="claim-statement">${escapeHtml(claim.statement)}</p>
    <div class="claim-meta">
      <span>author <code>${short(claim.author)}</code></span>
      ${isZero(claim.challenger) ? "" : `<span>challenger <code>${short(claim.challenger)}</code></span>`}
      ${isZero(claim.appellant) ? "" : `<span>appellant <code>${short(claim.appellant)}</code></span>`}
    </div>
    <p class="claim-evidence">
      ${evidence.map((u) => `<a href="${escapeAttr(u)}" target="_blank" rel="noreferrer">${hostOf(u)}</a>`).join("")}
      ${counter.map((u) => `<a href="${escapeAttr(u)}" target="_blank" rel="noreferrer">counter: ${hostOf(u)}</a>`).join("")}
    </p>
    ${verdictBlock}
    <div class="claim-actions"></div>
  `;

  const actions = el.querySelector(".claim-actions");
  for (const b of actionButtons(claim)) {
    if (b.when === false) continue;
    const btn = document.createElement("button");
    btn.className = "btn btn-sm" + (b.value ? " btn-accent" : "");
    btn.textContent = b.label;
    btn.disabled = state.busy;
    btn.onclick = async () => {
      let args = b.args;
      if (b.needsCounterEvidence) {
        const url = prompt("Counter evidence URL (what the first round did not see):");
        if (!url) return;
        args = [...args, url.trim()];
      }
      await send({ fn: b.fn, args, value: b.value ?? 0n, label: `${b.fn} #${claim.id}` });
    };
    actions.appendChild(btn);
  }
  if (!actions.children.length && claim.status !== "SETTLED") {
    actions.innerHTML = `<span class="claim-meta">no action available to you in this state</span>`;
  }
  return el;
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
const escapeAttr = escapeHtml;
function hostOf(u) {
  try {
    return new URL(u).host;
  } catch {
    return u.slice(0, 24);
  }
}

function render() {
  const list = $("claimsList");
  list.innerHTML = "";
  if (!state.claims.length) {
    list.innerHTML = `<p class="empty">No claims yet. Post the first one and watch it age out of its window.</p>`;
  }
  for (const claim of state.claims) list.appendChild(claimCard(claim));

  $("statClaims").textContent = String(state.claims.length);
  $("statOpen").textContent = String(state.claims.filter((c) => c.status === "OPEN").length);
  const pot = state.claims
    .filter((c) => c.status !== "SETTLED")
    .reduce((acc, c) => acc + c.bond * 2n + c.appealBond, 0n);
  $("statPot").textContent = fmtGen(pot);

  $("postBtn").disabled = state.busy;

  const withdraw = $("withdrawBtn");
  if (withdraw) {
    const credit = state.credit ?? 0n;
    withdraw.hidden = credit === 0n;
    withdraw.textContent = `Withdraw ${fmtGen(credit)}`;
    withdraw.disabled = state.busy;
  }
}

/* -------------------------------------------------------------------- wiring */

$("connectBtn").onclick = () => connect().catch((e) => alert(e.message));
$("reloadBtn").onclick = () => loadContract($("addressInput").value.trim());
$("refreshBtn").onclick = () => refresh();
$("withdrawBtn").onclick = () => send({ fn: "withdraw", args: [], value: 0n, label: "withdraw" });
$("txlogClose").onclick = () => $("txlog").classList.remove("show");

$("composer").onsubmit = async (event) => {
  event.preventDefault();
  const statement = $("statement").value.trim();
  const evidence = $("evidence").value.trim();
  if (!statement || !evidence) return alert("A claim needs a statement and at least one evidence URL.");
  let value;
  try {
    value = parseGen($("bond").value);
  } catch (err) {
    return alert(err.message);
  }
  await send({ fn: "post_claim", args: [statement, evidence], value, label: "post_claim" });
  $("statement").value = "";
  $("evidence").value = "";
};

if (window.ethereum) {
  window.ethereum.on?.("accountsChanged", (accounts) => {
    state.account = accounts[0] || null;
    wallet = null;
    $("connectBtn").textContent = state.account ? short(state.account) : "Connect wallet";
    render();
  });
}

setInterval(() => {
  // Countdowns are cheap; re-render only the deadline strings by refreshing the
  // whole list no more than once every 15 seconds.
  if (Date.now() - (render.last || 0) > 15000) {
    render.last = Date.now();
    render();
  }
}, 5000);

loadContract(state.address);
