"""Deterministic, gas-metered smart-contract VM for AetherChain.

This module is an execution layer and contract sandbox. Contract state is not yet
part of PoW block transactions or the consensus state root; callers must not treat
this version as a production smart-contract network.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Optional

# Small, explicit instruction set. Bytecode is represented as raw bytes or hex.
STOP, ADD, SUB, MUL, DIV = 0x00, 0x01, 0x02, 0x03, 0x04
LT, GT, EQ, ISZERO = 0x10, 0x11, 0x12, 0x13
SLOAD, SSTORE = 0x54, 0x55
MLOAD, MSTORE = 0x51, 0x52
JUMP, JUMPI, JUMPDEST = 0x56, 0x57, 0x5B
PUSH1, DUP1, SWAP1 = 0x60, 0x80, 0x90
CALLER, ORIGIN, CALLVALUE = 0x33, 0x32, 0x34
RETURN, REVERT = 0xF3, 0xFD
LOG0 = 0xA0

UINT256 = 1 << 256
MAX_STACK = 1024
MAX_MEMORY = 64 * 1024
GAS_COSTS = {STOP: 0, ADD: 3, SUB: 3, MUL: 5, DIV: 5, LT: 3, GT: 3, EQ: 3,
             ISZERO: 3, SLOAD: 100, SSTORE: 100, MLOAD: 3, MSTORE: 3, JUMP: 8,
             JUMPI: 10, JUMPDEST: 1, CALLER: 2, ORIGIN: 2, CALLVALUE: 2,
             LOG0: 375, RETURN: 0, REVERT: 0}


class VMError(ValueError):
    """Raised when bytecode execution fails or exceeds a resource limit."""


@dataclass(frozen=True)
class ExecutionContext:
    caller: str = "AETH_CALLER"
    origin: str = "AETH_CALLER"
    value: int = 0
    address: str = "AETH_CONTRACT"


@dataclass
class ExecutionResult:
    success: bool
    gas_used: int
    return_data: bytes = b""
    logs: list[bytes] = field(default_factory=list)
    error: Optional[str] = None
    storage: dict[int, int] = field(default_factory=dict)


class VM:
    """Execute bounded bytecode deterministically with copy-on-write storage."""

    def __init__(self, gas_limit: int = 100_000):
        if gas_limit < 1:
            raise ValueError("gas limit must be positive")
        self.gas_limit = gas_limit

    @staticmethod
    def _code(code: bytes | str) -> bytes:
        if isinstance(code, str):
            try:
                return bytes.fromhex(code.removeprefix("0x"))
            except ValueError as exc:
                raise VMError("bytecode must be valid hex") from exc
        return bytes(code)

    def execute(self, code: bytes | str, calldata: bytes = b"", *, context: ExecutionContext | None = None,
                storage: dict[int, int] | None = None, gas_limit: int | None = None) -> ExecutionResult:
        code = self._code(code)
        ctx = context or ExecutionContext()
        working_storage = dict(storage or {})
        gas_limit = self.gas_limit if gas_limit is None else gas_limit
        stack: list[int] = []
        memory = bytearray()
        logs: list[bytes] = []
        pc, gas = 0, 0

        def charge(op: int) -> None:
            nonlocal gas
            gas += GAS_COSTS.get(op, 0)
            if gas > gas_limit:
                raise VMError("out of gas")

        def pop() -> int:
            if not stack:
                raise VMError("stack underflow")
            return stack.pop()

        def push(value: int) -> None:
            if len(stack) >= MAX_STACK:
                raise VMError("stack overflow")
            stack.append(value % UINT256)

        def memory_slice(offset: int, size: int) -> bytes:
            if offset < 0 or size < 0 or offset + size > MAX_MEMORY:
                raise VMError("memory limit exceeded")
            return bytes(memory[offset:offset + size]).ljust(size, b"\0")

        try:
            while pc < len(code):
                op = code[pc]
                pc += 1
                charge(op)
                if op == STOP:
                    return ExecutionResult(True, gas, storage=working_storage)
                if op == PUSH1:
                    if pc >= len(code):
                        raise VMError("truncated PUSH1")
                    push(code[pc]); pc += 1
                elif op == ADD:
                    push(pop() + pop())
                elif op == SUB:
                    a, b = pop(), pop(); push(a - b)
                elif op == MUL:
                    push(pop() * pop())
                elif op == DIV:
                    a, b = pop(), pop(); push(0 if b == 0 else a // b)
                elif op == LT:
                    a, b = pop(), pop(); push(int(a < b))
                elif op == GT:
                    a, b = pop(), pop(); push(int(a > b))
                elif op == EQ:
                    push(int(pop() == pop()))
                elif op == ISZERO:
                    push(int(pop() == 0))
                elif op == SLOAD:
                    push(working_storage.get(pop(), 0))
                elif op == SSTORE:
                    key, value = pop(), pop(); working_storage[key] = value % UINT256
                elif op == MLOAD:
                    offset = pop(); push(int.from_bytes(memory_slice(offset, 32), "big"))
                elif op == MSTORE:
                    offset, value = pop(), pop()
                    if offset < 0 or offset + 32 > MAX_MEMORY:
                        raise VMError("memory limit exceeded")
                    if len(memory) < offset + 32:
                        memory.extend(b"\0" * (offset + 32 - len(memory)))
                    memory[offset:offset + 32] = value.to_bytes(32, "big")
                elif op == JUMP:
                    destination = pop()
                    if destination >= len(code) or code[destination] != JUMPDEST:
                        raise VMError("invalid jump destination")
                    pc = destination
                elif op == JUMPI:
                    destination, condition = pop(), pop()
                    if condition:
                        if destination >= len(code) or code[destination] != JUMPDEST:
                            raise VMError("invalid jump destination")
                        pc = destination
                elif op == JUMPDEST:
                    pass
                elif op == DUP1:
                    if not stack:
                        raise VMError("stack underflow")
                    push(stack[-1])
                elif op == SWAP1:
                    if len(stack) < 2:
                        raise VMError("stack underflow")
                    stack[-1], stack[-2] = stack[-2], stack[-1]
                elif op == CALLER:
                    push(int.from_bytes(hashlib.sha256(ctx.caller.encode()).digest(), "big"))
                elif op == ORIGIN:
                    push(int.from_bytes(hashlib.sha256(ctx.origin.encode()).digest(), "big"))
                elif op == CALLVALUE:
                    push(ctx.value)
                elif op == LOG0:
                    offset, size = pop(), pop(); logs.append(memory_slice(offset, size))
                elif op in (RETURN, REVERT):
                    offset, size = pop(), pop()
                    data = memory_slice(offset, size)
                    if op == REVERT:
                        raise VMError(data.decode(errors="replace") or "execution reverted")
                    return ExecutionResult(True, gas, data, logs, storage=working_storage)
                else:
                    raise VMError(f"unsupported opcode 0x{op:02x}")
            return ExecutionResult(True, gas, stack[-1].to_bytes(32, "big") if stack else b"", logs, storage=working_storage)
        except VMError as exc:
            return ExecutionResult(False, gas, logs=logs, error=str(exc), storage=dict(storage or {}))


@dataclass
class Contract:
    address: str
    code: bytes
    storage: dict[int, int] = field(default_factory=dict)
    nonce: int = 0


class ContractStore:
    """Deterministic addressable contract registry for VM execution."""

    def __init__(self, vm: VM | None = None):
        self.vm = vm or VM()
        self.contracts: dict[str, Contract] = {}
        self._nonces: dict[str, int] = {}

    def deploy(self, creator: str, code: bytes | str, gas_limit: int | None = None) -> tuple[Contract | None, ExecutionResult]:
        raw = self.vm._code(code)
        nonce = self._nonces.get(creator, 0)
        address = "AETHC" + hashlib.sha256(creator.encode() + nonce.to_bytes(8, "big") + raw).hexdigest()[:35]
        result = self.vm.execute(raw, context=ExecutionContext(caller=creator, origin=creator, address=address), gas_limit=gas_limit)
        if not result.success:
            return None, result
        contract = Contract(address, raw, dict(result.storage), nonce)
        self.contracts[address] = contract
        self._nonces[creator] = nonce + 1
        return contract, result

    def call(self, address: str, caller: str = "AETH_CALLER", calldata: bytes = b"", value: int = 0,
             gas_limit: int | None = None) -> ExecutionResult:
        contract = self.contracts.get(address)
        if contract is None:
            return ExecutionResult(False, 0, error="contract not found")
        result = self.vm.execute(contract.code, calldata, context=ExecutionContext(caller, caller, value, address),
                                 storage=contract.storage, gas_limit=gas_limit)
        if result.success:
            contract.storage = result.storage
        return result

    def get(self, address: str) -> Contract | None:
        return self.contracts.get(address)

    def state_root(self) -> str:
        leaves = [json.dumps({"address": c.address, "code": c.code.hex(), "storage": sorted(c.storage.items())}, sort_keys=True)
                  for c in self.contracts.values()]
        return hashlib.sha256("\n".join(sorted(leaves)).encode()).hexdigest()
