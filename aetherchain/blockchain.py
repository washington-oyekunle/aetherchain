"""Proof-of-work blockchain controller built on the AetherChain UTXO ledger."""
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
    nonce: int = 0
    hash: str = ""

    @property
    def merkle_root(self) -> str:
        return merkle_root(self.transactions)

    def header(self) -> dict:
        return {"index": self.index, "previous_hash": self.previous_hash, "merkle_root": self.merkle_root,
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
    def __init__(self) -> None:
        self.pending_transactions: dict[str, UTXOTransaction] = {}
        self._lock = threading.RLock()

    def add_transaction(self, tx: UTXOTransaction, state: UTXOState) -> bool:
        with self._lock:
            if tx.tx_id in self.pending_transactions:
                return False
            valid, _, _ = tx.validate(state.utxo_pool)
            if not valid:
                return False
            # Reject conflicts already reserved by another pending transaction.
            reserved = {i.utxo_id for p in self.pending_transactions.values() for i in p.inputs}
            if any(i.utxo_id in reserved for i in tx.inputs):
                return False
            self.pending_transactions[tx.tx_id] = tx
            return True

    def get_batch(self, limit: int = 10) -> list[UTXOTransaction]:
        with self._lock:
            return list(self.pending_transactions.values())[:limit]

    def remove_transactions(self, transactions: list[UTXOTransaction]) -> None:
        with self._lock:
            for tx in transactions:
                self.pending_transactions.pop(tx.tx_id, None)


class Blockchain:
    def __init__(self, difficulty: int = 1, block_reward: int = 50_0000, target_block_time: float = 2.0,
                 adjustment_interval: int = 5, genesis_allocation: int = 1_000_000):
        if difficulty < 0 or adjustment_interval < 1 or block_reward < 0:
            raise ValueError("invalid blockchain parameters")
        self.difficulty = difficulty
        self.block_reward = block_reward
        self.target_block_time = target_block_time
        self.adjustment_interval = adjustment_interval
        self.state = UTXOState()
        self.mempool = Mempool()
        self.chain: list[Block] = []
        self._lock = threading.RLock()
        self._create_genesis(genesis_allocation)

    def _create_genesis(self, allocation: int) -> None:
        tx = UTXOTransaction([], [TransactionOutput("AETH_GENESIS_RESERVE", allocation)], SYSTEM)
        block = Block(0, "0" * 64, [tx], self.difficulty, 0.0)
        block.mine()
        self.state.apply_block(block.transactions, max_coinbase=allocation)
        self.chain.append(block)

    def latest_block(self) -> Block:
        return self.chain[-1]

    def next_difficulty(self) -> int:
        if len(self.chain) < self.adjustment_interval or len(self.chain) % self.adjustment_interval:
            return self.difficulty
        start = self.chain[-self.adjustment_interval]
        elapsed = max(0.001, self.latest_block().timestamp - start.timestamp)
        target = self.target_block_time * self.adjustment_interval
        ratio = max(0.25, min(4.0, target / elapsed))
        return max(0, round(self.difficulty + (ratio - 1.0)))

    def mine_pending_transactions(self, miner_address: str, max_transactions: int = 10) -> Optional[Block]:
        with self._lock:
            candidate = self.mempool.get_batch(max_transactions)
            staged = self.state.clone()
            valid: list[UTXOTransaction] = []
            fees = 0
            for tx in candidate:
                try:
                    valid_fee = staged.apply(tx)
                except ValueError:
                    continue
                valid.append(tx)
                fees += valid_fee
            reward = self.block_reward + fees
            coinbase = UTXOTransaction([], [TransactionOutput(miner_address, reward)], SYSTEM)
            difficulty = self.next_difficulty()
            block = Block(len(self.chain), self.latest_block().hash, [coinbase] + valid, difficulty, time.time())
            block.mine()
            # Apply all state changes atomically, including reward.
            try:
                self.state.apply_block(block.transactions, max_coinbase=reward)
            except ValueError:
                return None
            self.chain.append(block)
            self.difficulty = difficulty
            self.mempool.remove_transactions(valid)
            return block

    def is_chain_valid(self) -> bool:
        with self._lock:
            if not self.chain:
                return False
            replay = UTXOState()
            for index, block in enumerate(self.chain):
                if block.index != index or block.hash != block.calculate_hash():
                    return False
                if not block.hash.startswith("0" * block.difficulty):
                    return False
                if index == 0:
                    if block.previous_hash != "0" * 64:
                        return False
                elif block.previous_hash != self.chain[index - 1].hash:
                    return False
                try:
                    replay.apply_block(block.transactions, max_coinbase=(self.block_reward + 10**18))
                except ValueError:
                    return False
            return replay.utxo_pool == self.state.utxo_pool
