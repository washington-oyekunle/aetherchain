"""Convenience runtime for operating an AetherChain node as one process."""
from __future__ import annotations

from pathlib import Path

from .blockchain import Block, Blockchain
from .p2p import P2PNode
from .rpc import RPCNode
from .storage import save_chain


class Node:
    """Owns the chain and optional network services with explicit lifecycle control."""

    def __init__(self, blockchain: Blockchain | None = None, host: str = "127.0.0.1",
                 p2p_port: int = 5001, rpc_port: int = 8545):
        self.blockchain = blockchain or Blockchain()
        self.p2p = P2PNode(host, p2p_port, self.blockchain)
        self.rpc = RPCNode(self.blockchain, host, rpc_port)
        self._running = False

    @property
    def running(self) -> bool:
        return self._running

    def start(self) -> None:
        if self._running:
            return
        self.p2p.start()
        try:
            self.rpc.start()
        except Exception:
            self.p2p.stop()
            raise
        self._running = True

    def stop(self) -> None:
        self.rpc.stop()
        self.p2p.stop()
        self._running = False

    def connect_peer(self, host: str, port: int) -> bool:
        return self.p2p.connect(host, port)

    def mine_once(self, miner_address: str, max_transactions: int | None = None) -> Block | None:
        limit = max_transactions if max_transactions is not None else self.blockchain.max_block_transactions
        block = self.blockchain.mine_pending_transactions(miner_address, limit)
        if block is not None:
            self.p2p.broadcast_block(block)
        return block

    def snapshot(self, path: str | Path) -> None:
        save_chain(self.blockchain, path)
