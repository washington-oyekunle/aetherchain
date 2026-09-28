"""Proof-of-work blockchain controller with committed state and chain-work fork choice."""
from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

from .ledger import SYSTEM, TransactionOutput, UTXOState, UTXOTransaction
from .vm import ContractOperation, ContractStore


def merkle_root(transactions: list[UTXOTransaction]) -> str:
    hashes = [tx.tx_id for tx in transactions]
    if not hashes:
        return hashlib.sha256(b"").hexdigest()
    while len(hashes) > 1:
        if len(hashes) % 2:
            hashes.append(hashes[-1])
        hashes = [hashlib.sha256((hashes[i] + hashes[i + 1]).encode()).hexdigest() for i in range(0, len(hashes), 2)]
    return hashes[0]


def contract_operations_root(operations: list[ContractOperation]) -> str:
    return hashlib.sha256("\n".join(operation.tx_id for operation in operations).encode()).hexdigest()


@dataclass
class Block:
    index: int
    previous_hash: str
    transactions: list[UTXOTransaction]
    difficulty: int
    timestamp: float
    state_root: str = ""
    nonce: int = 0
    hash: str = ""
    contract_root: str = ""
    contract_operations: list[ContractOperation] = field(default_factory=list)

    @property
    def merkle_root(self) -> str:
        return merkle_root(self.transactions)

    def header(self) -> dict:
        return {"index": self.index, "previous_hash": self.previous_hash, "merkle_root": self.merkle_root,
                "state_root": self.state_root, "contract_root": self.contract_root,
                "contract_operations_root": contract_operations_root(self.contract_operations),
                "difficulty": self.difficulty, "timestamp": self.timestamp, "nonce": self.nonce}

    def calculate_hash(self) -> str:
        return hashlib.sha256(json.dumps(self.header(), sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def mine(self) -> None:
        target = "0" * self.difficulty
        while True:
            self.hash = self.calculate_hash()
            if self.hash.startswith(target):
                return
            self.nonce += 1


class Mempool:
    def __init__(self, max_size: int = 10_000) -> None:
        self.pending_transactions: dict[str, UTXOTransaction] = {}
        self.max_size = max_size
        self._lock = threading.RLock()

    def add_transaction(self, tx: UTXOTransaction, state: UTXOState) -> bool:
        with self._lock:
            if len(self.pending_transactions) >= self.max_size or tx.tx_id in self.pending_transactions:
                return False
            valid, _, _ = tx.validate(state.utxo_pool)
            if not valid:
                return False
            reserved = {i.utxo_id for p in self.pending_transactions.values() for i in p.inputs}
            if any(i.utxo_id in reserved for i in tx.inputs):
                return False
            self.pending_transactions[tx.tx_id] = tx
            return True

    def get_batch(self, limit: int = 10) -> list[UTXOTransaction]:
        with self._lock:
            return list(self.pending_transactions.values())[:max(0, limit)]

    def remove_transactions(self, transactions: list[UTXOTransaction]) -> None:
        with self._lock:
            for tx in transactions:
                self.pending_transactions.pop(tx.tx_id, None)

    def clear(self) -> None:
        with self._lock:
            self.pending_transactions.clear()


class ContractMempool:
    def __init__(self, max_size: int = 10_000):
        self.pending_operations: dict[str, ContractOperation] = {}
        self.max_size = max_size
        self._lock = threading.RLock()

    def add_operation(self, operation: ContractOperation, store: ContractStore) -> bool:
        with self._lock:
            if len(self.pending_operations) >= self.max_size or operation.tx_id in self.pending_operations:
                return False
            valid, _ = operation.validate()
            if not valid or operation.nonce != store._nonces.get(operation.sender, 0):
                return False
            if any(p.sender == operation.sender and p.nonce == operation.nonce for p in self.pending_operations.values()):
                return False
            self.pending_operations[operation.tx_id] = operation
            return True

    def get_batch(self, limit: int = 10) -> list[ContractOperation]:
        with self._lock:
            return list(self.pending_operations.values())[:max(0, limit)]

    def remove_operations(self, operations: list[ContractOperation]) -> None:
        with self._lock:
            for operation in operations:
                self.pending_operations.pop(operation.tx_id, None)

    def clear(self) -> None:
        with self._lock:
            self.pending_operations.clear()


class Blockchain:
    def __init__(self, difficulty: int = 1, block_reward: int = 50_0000, target_block_time: float = 2.0,
                 adjustment_interval: int = 5, genesis_allocation: int = 1_000_000,
                 genesis_address: str = "AETH_GENESIS_RESERVE", max_block_transactions: int = 10):
        if difficulty < 0 or adjustment_interval < 1 or block_reward < 0 or target_block_time <= 0 or max_block_transactions < 1:
            raise ValueError("invalid blockchain parameters")
        self.initial_difficulty = difficulty
        self.difficulty = difficulty
        self.block_reward = block_reward
        self.target_block_time = target_block_time
        self.adjustment_interval = adjustment_interval
        self.genesis_allocation = genesis_allocation
        self.genesis_address = genesis_address
        self.max_block_transactions = max_block_transactions
        self.state = UTXOState()
        self.contracts = ContractStore()
        self.mempool = Mempool()
        self.contract_mempool = ContractMempool()
        self.chain: list[Block] = []
        self._lock = threading.RLock()
        self._create_genesis()

    def _create_genesis(self) -> None:
        tx = UTXOTransaction([], [TransactionOutput(self.genesis_address, self.genesis_allocation)], SYSTEM)
        state = UTXOState()
        state.apply_block([tx], max_coinbase=self.genesis_allocation)
        block = Block(0, "0" * 64, [tx], self.initial_difficulty, 1.0, state.state_root(), contract_root=self.contracts.state_root())
        block.mine()
        self.state = state
        self.chain.append(block)

    def latest_block(self) -> Block:
        return self.chain[-1]

    @staticmethod
    def block_work(difficulty: int) -> int:
        return 16 ** difficulty

    def chain_work(self, chain: Optional[list[Block]] = None) -> int:
        return sum(self.block_work(block.difficulty) for block in (chain or self.chain))

    def expected_difficulty(self, next_index: int, previous_chain: Optional[list[Block]] = None) -> int:
        chain = previous_chain or self.chain
        if next_index == 0:
            return self.initial_difficulty
        previous = chain[next_index - 1]
        if next_index % self.adjustment_interval:
            return previous.difficulty
        start = chain[max(0, next_index - self.adjustment_interval)]
        elapsed = max(0.001, previous.timestamp - start.timestamp)
        target = self.target_block_time * self.adjustment_interval
        ratio = max(0.25, min(4.0, target / elapsed))
        return max(0, round(previous.difficulty + (ratio - 1.0)))

    def next_difficulty(self) -> int:
        return self.expected_difficulty(len(self.chain))

    def mine_pending_transactions(self, miner_address: str, max_transactions: int = 10) -> Optional[Block]:
        with self._lock:
            candidate = self.mempool.get_batch(min(max_transactions, self.max_block_transactions))
            contract_candidate = self.contract_mempool.get_batch(min(max_transactions, self.max_block_transactions))
            selection_state = self.state.clone()
            valid: list[UTXOTransaction] = []
            fees = 0
            for tx in candidate:
                try:
                    valid_fee = selection_state.apply(tx)
                except ValueError:
                    continue
                valid.append(tx)
                fees += valid_fee
            reward = self.block_reward + fees
            coinbase = UTXOTransaction([], [TransactionOutput(miner_address, reward)], SYSTEM)
            difficulty = self.next_difficulty()
            timestamp = max(time.time(), self.latest_block().timestamp + 0.001)
            # Apply first so the block commits the exact post-state root.
            staged = self.state.clone()
            staged_contracts = self.contracts.clone()
            try:
                staged.apply_block([coinbase] + valid, max_coinbase=reward)
                staged_contracts.apply_operations(contract_candidate)
            except ValueError:
                return None
            block = Block(len(self.chain), self.latest_block().hash, [coinbase] + valid, difficulty, timestamp,
                          staged.state_root(), contract_root=staged_contracts.state_root(),
                          contract_operations=contract_candidate)
            block.mine()
            self.state = staged
            self.contracts = staged_contracts
            self.chain.append(block)
            self.difficulty = difficulty
            self.mempool.remove_transactions(valid)
            self.contract_mempool.remove_operations(contract_candidate)
            return block

    def validate_chain(self, candidate: list[Block]) -> tuple[bool, Optional[UTXOState], str]:
        if not candidate:
            return False, None, "empty chain"
        replay = UTXOState()
        replay_contracts = ContractStore()
        for index, block in enumerate(candidate):
            if block.index != index or block.hash != block.calculate_hash():
                return False, None, f"block {index} hash mismatch"
            if block.difficulty != self.expected_difficulty(index, candidate):
                return False, None, f"block {index} has unexpected difficulty"
            if not block.hash.startswith("0" * block.difficulty):
                return False, None, f"block {index} fails proof of work"
            if index == 0:
                if block.previous_hash != "0" * 64:
                    return False, None, "invalid genesis link"
            elif block.previous_hash != candidate[index - 1].hash:
                return False, None, f"block {index} has invalid previous hash"
            try:
                if index > 0:
                    fee_state = replay.clone()
                    fees = sum(fee_state.apply(tx) for tx in block.transactions[1:])
                    coinbase_total = sum(output.amount for output in block.transactions[0].outputs)
                    if coinbase_total > self.block_reward + fees:
                        return False, None, f"block {index} overpays coinbase"
                replay.apply_block(block.transactions, max_coinbase=self.genesis_allocation if index == 0 else self.block_reward + 10**18)
                replay_contracts.apply_operations(block.contract_operations)
            except ValueError as exc:
                return False, None, f"block {index} state transition failed: {exc}"
            if block.state_root != replay.state_root():
                return False, None, f"block {index} state root mismatch"
            if block.contract_operations and contract_operations_root(block.contract_operations) != block.header()["contract_operations_root"]:
                return False, None, f"block {index} contract operation root mismatch"
            if block.contract_root != replay_contracts.state_root():
                return False, None, f"block {index} contract state root mismatch"
        return True, replay, ""

    def replace_chain(self, candidate: list[Block]) -> bool:
        with self._lock:
            valid, state, _ = self.validate_chain(candidate)
            if not valid or state is None or self.chain_work(candidate) <= self.chain_work(self.chain):
                return False
            self.chain, self.state = list(candidate), state
            rebuilt_contracts = ContractStore()
            for block in candidate:
                rebuilt_contracts.apply_operations(block.contract_operations)
            self.contracts = rebuilt_contracts
            self.difficulty = self.latest_block().difficulty
            self.mempool.clear()
            self.contract_mempool.clear()
            return True

    def is_chain_valid(self) -> bool:
        with self._lock:
            valid, state, _ = self.validate_chain(self.chain)
            return valid and state is not None and state.utxo_pool == self.state.utxo_pool \
                and self.contracts.state_root() == self.latest_block().contract_root

    def get_block(self, index: int) -> Optional[Block]:
        with self._lock:
            return self.chain[index] if 0 <= index < len(self.chain) else None

    def get_transaction(self, tx_id: str) -> Optional[UTXOTransaction]:
        with self._lock:
            pending = self.mempool.pending_transactions.get(tx_id)
            if pending is not None:
                return pending
            for block in reversed(self.chain):
                for tx in block.transactions:
                    if tx.tx_id == tx_id:
                        return tx
            return None

    def stats(self) -> dict[str, int | float | str]:
        with self._lock:
            return {"height": len(self.chain) - 1, "difficulty": self.latest_block().difficulty,
                    "work": self.chain_work(), "state_root": self.state.state_root(),
                    "contract_root": self.contracts.state_root(), "contract_count": len(self.contracts.contracts),
                    "utxo_count": len(self.state.utxo_pool), "mempool_size": len(self.mempool.pending_transactions),
                    "contract_mempool_size": len(self.contract_mempool.pending_operations)}
