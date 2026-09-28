"""SQLite persistence for restart-safe development nodes."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .blockchain import Blockchain
from .p2p import P2PNode


def save_chain_sqlite(blockchain: Blockchain, path: str | Path) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with blockchain._lock, sqlite3.connect(destination) as db:
        db.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS blocks (height INTEGER PRIMARY KEY, payload TEXT NOT NULL)")
        config = {"difficulty": blockchain.initial_difficulty, "block_reward": blockchain.block_reward,
                  "target_block_time": blockchain.target_block_time, "adjustment_interval": blockchain.adjustment_interval,
                  "genesis_allocation": blockchain.genesis_allocation, "genesis_address": blockchain.genesis_address,
                  "max_block_transactions": blockchain.max_block_transactions}
        db.execute("INSERT OR REPLACE INTO metadata(key, value) VALUES('config', ?)", (json.dumps(config, sort_keys=True),))
        db.execute("DELETE FROM blocks")
        db.executemany("INSERT INTO blocks(height, payload) VALUES(?, ?)",
                       [(block.index, json.dumps(P2PNode.serialize_block(block), sort_keys=True)) for block in blockchain.chain])
        db.commit()


def load_chain_sqlite(path: str | Path) -> Blockchain:
    with sqlite3.connect(path) as db:
        row = db.execute("SELECT value FROM metadata WHERE key = 'config'").fetchone()
        if row is None:
            raise ValueError("database has no chain configuration")
        config = json.loads(row[0])
        rows = db.execute("SELECT payload FROM blocks ORDER BY height").fetchall()
    chain = Blockchain(**config)
    candidate = [P2PNode.deserialize_block(json.loads(payload)) for (payload,) in rows]
    valid, state, reason = chain.validate_chain(candidate)
    if not valid or state is None:
        raise ValueError(f"invalid chain database: {reason}")
    chain.chain, chain.state = candidate, state
    for block in candidate:
        chain.contracts.apply_operations(block.contract_operations)
    chain.difficulty = chain.latest_block().difficulty
    return chain
