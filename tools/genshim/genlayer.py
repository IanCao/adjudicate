"""Minimal GenVM stand-in used to exercise contract logic offline.

Why this exists
---------------
The official `genlayer-test` direct runner downloads the GenVM runtime from
GitHub releases on first use. That is the right tool for consensus tests, but it
cannot run on a machine without access to GitHub release assets, and it is slow
for the pure state-machine logic.

This shim implements only the surface the two primitives touch - storage
containers, value types, decorators, the message context, and a leader/validator
runner - so the deterministic logic (bonding, permissions, state transitions,
settlement arithmetic, output coercion) can be tested in milliseconds anywhere.

It is NOT a consensus simulator: `run_nondet_unsafe` calls the leader once and
asks the validator to accept or reject that result. Multi-node rotation,
pickling and WASI behaviour are out of scope by design. Consensus behaviour is
covered by the direct-mode suite in the same folder.
"""

from __future__ import annotations

import json
import re
import types


# --------------------------------------------------------------------- errors


class UserError(Exception):
    pass


class VMError(Exception):
    pass


class Return:
    """Mirror of gl.vm.Return - only `.calldata` is used by the contracts."""

    def __init__(self, calldata=None):
        self.calldata = calldata

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Return({self.calldata!r})"


# ---------------------------------------------------------------------- types


class u256(int):
    pass


class u32(int):
    pass


class u16(int):
    pass


class u8(int):
    pass


class i32(int):
    pass


class Address(str):
    """Addresses behave like checksummed strings; comparison is by value."""

    def __new__(cls, value: str):
        text = str(value).strip()
        return super().__new__(cls, text)


class TreeMap(dict):
    def get(self, key, default=None):
        return dict.get(self, key, default)


class DynArray(list):
    pass


def allow_storage(cls):
    """No-op marker; real GenVM uses it to pick the storage codec."""
    return cls


# ------------------------------------------------------------------- contexts


class MessageContext:
    def __init__(self) -> None:
        self.sender_address = Address("0x" + "0" * 40)
        self.origin_address = Address("0x" + "0" * 40)
        self.contract_address = Address("0x" + "0" * 40)
        self.value = u256(0)
        self.chain_id = u256(61999)


class _Response:
    def __init__(self, body: bytes, status: int = 200):
        self.body = body
        self.status = status


class _Web:
    def __init__(self) -> None:
        self.mocks: list[tuple[str, object]] = []

    def mock(self, pattern: str, body: str, status: int = 200) -> None:
        self.mocks.append((pattern, _Response(body.encode("utf-8"), status)))

    def clear(self) -> None:
        self.mocks.clear()

    def get(self, url: str, *args, **kwargs) -> _Response:
        for pattern, response in self.mocks:
            if re.search(pattern, url):
                return response
        raise RuntimeError(f"no web mock for {url}")


class _LLM:
    """Prompt matcher. A list response is consumed in order, which is how tests
    simulate a validator that disagrees with the leader."""

    def __init__(self) -> None:
        self.mocks: list[tuple[str, object]] = []
        self.calls: list[str] = []

    def mock(self, pattern: str, response) -> None:
        if isinstance(response, (list, tuple)):
            self.mocks.append((pattern, list(response)))
        else:
            self.mocks.append((pattern, response))

    def clear(self) -> None:
        self.mocks.clear()
        self.calls.clear()

    def queue(self, pattern: str, responses: list[str]) -> None:
        self.mocks.append((pattern, list(responses)))

    def exec_prompt(self, prompt: str, response_format=None, **kwargs) -> str:
        self.calls.append(prompt)
        for pattern, response in self.mocks:
            if not re.search(pattern, prompt):
                continue
            if isinstance(response, list):
                if len(response) == 0:
                    raise RuntimeError("llm response sequence exhausted")
                return response.pop(0)
            return response
        raise RuntimeError("no llm mock matched the prompt")


class _VM:
    UserError = UserError
    VMError = VMError
    Return = Return

    def run_nondet_unsafe(self, leader_fn, validator_fn):
        result = leader_fn()
        accepted = validator_fn(Return(result))
        if not accepted:
            raise UserError("no consensus among validators")
        return result


class _EqPrinciple:
    """Only the shape matters here; the contracts use run_nondet_unsafe."""

    def strict_eq(self, fn):
        return fn()

    def prompt_comparative(self, fn, principle=""):
        return fn()

    def prompt_non_comparative(self, fn, task="", criteria=""):
        return fn()


class _Nondet:
    def __init__(self) -> None:
        self.web = _Web()
        self.exec_prompt = _LLM().exec_prompt

    def bind_llm(self, llm: _LLM) -> None:
        self.exec_prompt = llm.exec_prompt


class _EVM:
    @staticmethod
    def contract_interface(cls):
        """Real GenVM synthesises an interface with emit_transfer/emit; mirror it."""

        def _init(self, address=None):
            self.address = address
            self.sent = []

        def emit_transfer(self, value=None, on=None):
            self.sent.append(int(value or 0))

        cls.__init__ = _init
        cls.emit_transfer = emit_transfer
        return cls


class _Marker:
    """Decorator that is also callable-with-attribute, e.g. gl.public.write.payable."""

    def __init__(self, name: str):
        self.name = name

    def __call__(self, fn):
        return fn

    @property
    def payable(self):
        return self


class _Public:
    def __init__(self) -> None:
        self.view = _Marker("view")
        self.write = _Marker("write")


class Contract:
    """Base class: bootstraps annotated storage fields and records transfers."""

    def __new__(cls, *args, **kwargs):
        instance = super().__new__(cls)
        # Real GenVM materialises annotated storage slots before __init__ runs.
        for klass in reversed(cls.__mro__):
            for name, annotation in getattr(klass, "__annotations__", {}).items():
                if name.startswith("_"):
                    continue
                text = str(annotation)
                if "TreeMap" in text:
                    object.__setattr__(instance, name, TreeMap())
                elif "DynArray" in text:
                    object.__setattr__(instance, name, DynArray())
        return instance

    def emit_transfer(self, value=None, on=None):
        self.__dict__.setdefault("_emitted", []).append(int(value or 0))
        return None


class GL:
    """The `gl` namespace object the contracts import."""

    Contract = Contract

    def __init__(self) -> None:
        self.public = _Public()
        self.vm = _VM()
        self.message = MessageContext()
        self.nondet = _Nondet()
        self.eq_principle = _EqPrinciple()
        self.evm = _EVM()
        self.llm = _LLM()
        self.nondet.bind_llm(self.llm)

    def set_context(self, sender=None, value=0, contract=None) -> None:
        if sender is not None:
            self.message.sender_address = Address(sender)
            self.message.origin_address = Address(sender)
        if contract is not None:
            self.message.contract_address = Address(contract)
        self.message.value = u256(value)

    def get_contract_at(self, address):  # pragma: no cover - not used yet
        raise NotImplementedError("cross-contract calls are out of scope for the shim")

    def reset(self) -> None:
        self.message = MessageContext()
        self.nondet.web.clear()
        self.llm.clear()


gl = GL()


def build_module() -> types.ModuleType:
    """Build a module object that mimics `genlayer` for `from genlayer import *`."""
    module = types.ModuleType("genlayer")
    exported = {
        "gl": gl,
        "Contract": Contract,
        "u256": u256,
        "u32": u32,
        "u16": u16,
        "u8": u8,
        "i32": i32,
        "Address": Address,
        "TreeMap": TreeMap,
        "DynArray": DynArray,
        "allow_storage": allow_storage,
        "json": json,
    }
    module.__dict__.update(exported)
    module.__all__ = list(exported.keys())
    return module
