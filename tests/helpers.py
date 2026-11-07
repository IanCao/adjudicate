"""Helpers that load a contract file against the offline GenVM shim."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHIM_DIR = ROOT / "tools" / "genshim"

ALICE = "0x" + "a1" * 20
BOB = "0x" + "b2" * 20
CAROL = "0x" + "c3" * 20

GEN = 10 ** 18
ZERO_ADDRESS = "0x" + "0" * 40

_load_counter = 0


def load_contract(relative_path: str):
    """Import a contract file with `genlayer` resolving to the local shim.

    Each call gets a fresh shim instance, so mocks and message context never
    leak between tests.
    """
    global _load_counter
    _load_counter += 1
    module_name = f"contract_under_test_{_load_counter}"

    sys.path.insert(0, str(SHIM_DIR))
    try:
        spec = importlib.util.spec_from_file_location(module_name, ROOT / relative_path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(SHIM_DIR))
        sys.modules.pop("genlayer", None)


def deploy_oracle(min_bond: int = GEN // 100, challenge_window: int = 3600):
    module = load_contract("contracts/optimistic_claim_oracle.py")
    contract = module.OptimisticClaimOracle(module.u256(min_bond), module.u256(challenge_window))
    return module, contract


def deploy_escrow(escalation_window: int = 3600):
    module = load_contract("contracts/deliverable_escrow.py")
    contract = module.DeliverableEscrow(module.u256(escalation_window))
    return module, contract


def deploy_oracle_v2(
    min_bond: int = GEN // 100,
    challenge_window: int = 3600,
    appeal_window: int = 3600,
):
    module = load_contract("contracts/optimistic_claim_oracle_v2.py")
    contract = module.OptimisticClaimOracleV2(
        module.u256(min_bond),
        module.u256(challenge_window),
        module.u256(appeal_window),
    )
    return module, contract
