"""Proof-of-work blockchain controller with committed state and chain-work fork choice."""
from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import dataclass
from typing import Optional

from .ledger import SYSTEM, TransactionOutput, UTXOState, UTXOTransaction


def merkle_root(transactions: list[UTXOTransaction]) -> str:
    hashes = [tx.tx_id for tx in transactions]
    if not hashes:
        return hashlib.sha256(b"").hexdigest()
    while len(hashes) > 1:
        if len(hashes) % 2:
            hashes.append(hashes[-1])
        hashes = [hashlib.sha256((hashes[i] + hashes[i + 1]).encode()).hexdigest() for i in range(0, len(hashes), 2)]
    return hashes[0]


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

    @property
    def merkle_root(self) -> str:
        return merkle_root(self.transactions)

    def header(self) -> dict:
        return {"index": self.index, "previous_hash": self.previous_hash, "merkle_root": self.merkle_root,
                "state_root": self.state_root, "difficulty": self.difficulty, "timestamp": self.timestamp,
                "nonce": self.nonce}

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


class Blockchain:
    def __init__(self, difficulty: int = 1, block_reward: int = 50_0000, target_block_time: float = 2.0,
                 adjustment_interval: int = 5, genesis_allocation: int = 1_000_000,
                 genesis_address: str = "AETH_GENESIS_RESERVE"):
        if difficulty < 0 or adjustment_interval < 1 or block_reward < 0 or target_block_time <= 0:
            raise ValueError("invalid blockchain parameters")
        self.initial_difficulty = difficulty
        self.difficulty = difficulty
        self.block_reward = block_reward
        self.target_block_time = target_block_time
        self.adjustment_interval = adjustment_interval
        self.genesis_allocation = genesis_allocation
        self.genesis_address = genesis_address
        self.state = UTXOState()
        self.mempool = Mempool()
        self.chain: list[Block] = []
        self._lock = threading.RLock()
        self._create_genesis()

    def _create_genesis(self) -> None:
        tx = UTXOTransaction([], [TransactionOutput(self.genesis_address, self.genesis_allocation)], SYSTEM)
        state = UTXOState()
        state.apply_block([tx], max_coinbase=self.genesis_allocation)
        block = Block(0, "0" * 64, [tx], self.initial_difficulty, 1.0, state.state_root())
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
            candidate = self.mempool.get_batch(max_transactions)
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
            try:
                staged.apply_block([coinbase] + valid, max_coinbase=reward)
            except ValueError:
                return None
            block = Block(len(self.chain), self.latest_block().hash, [coinbase] + valid, difficulty, timestamp, staged.state_root())
            block.mine()
            self.state = staged
            self.chain.append(block)
            self.difficulty = difficulty
            self.mempool.remove_transactions(valid)
            return block

    def validate_chain(self, candidate: list[Block]) -> tuple[bool, Optional[UTXOState], str]:
        if not candidate:
            return False, None, "empty chain"
        replay = UTXOState()
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
            except ValueError as exc:
                return False, None, f"block {index} state transition failed: {exc}"
            if block.state_root != replay.state_root():
                return False, None, f"block {index} state root mismatch"
        return True, replay, ""

    def replace_chain(self, candidate: list[Block]) -> bool:
        with self._lock:
            valid, state, _ = self.validate_chain(candidate)
            if not valid or state is None or self.chain_work(candidate) <= self.chain_work(self.chain):
                return False
            self.chain, self.state = list(candidate), state
            self.difficulty = self.latest_block().difficulty
            self.mempool.clear()
            return True

    def is_chain_valid(self) -> bool:
        with self._lock:
            valid, state, _ = self.validate_chain(self.chain)
            return valid and state is not None and state.utxo_pool == self.state.utxo_pool

    def get_block(self, index: int) -> Optional[Block]:
        with self._lock:
            return self.chain[index] if 0 <= index < len(self.chain) else None
