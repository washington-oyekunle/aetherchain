"""Portable JSON snapshots for development nodes."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from .blockchain import Block, Blockchain
from .p2p import P2PNode


def save_chain(blockchain: Blockchain, path: str | os.PathLike[str]) -> None:
    destination = Path(path)
    with blockchain._lock:
        payload = {
            "version": 1,
            "config": {"difficulty": blockchain.initial_difficulty, "block_reward": blockchain.block_reward,
                       "target_block_time": blockchain.target_block_time, "adjustment_interval": blockchain.adjustment_interval,
                       "genesis_allocation": blockchain.genesis_allocation, "genesis_address": blockchain.genesis_address,
                       "max_block_transactions": blockchain.max_block_transactions},
            "chain": [P2PNode.serialize_block(block) for block in blockchain.chain],
        }
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=destination.name + ".", dir=destination.parent, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def load_chain(path: str | os.PathLike[str]) -> Blockchain:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if payload.get("version") != 1:
        raise ValueError("unsupported snapshot version")
    config = payload["config"]
    blockchain = Blockchain(**config)
    candidate = [P2PNode.deserialize_block(raw) for raw in payload["chain"]]
    valid, state, reason = blockchain.validate_chain(candidate)
    if not valid or state is None:
        raise ValueError(f"invalid snapshot: {reason}")
    blockchain.chain = candidate
    blockchain.state = state
    blockchain.difficulty = blockchain.latest_block().difficulty
    return blockchain
