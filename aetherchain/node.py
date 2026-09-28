"""Convenience runtime for operating an AetherChain node as one process."""
from __future__ import annotations

from pathlib import Path

from .blockchain import Block, Blockchain
from .config import NodeConfig
from .database import load_chain_sqlite, save_chain_sqlite
from .p2p import P2PNode
from .rpc import RPCNode
from .storage import save_chain
from .vm import ContractOperation


class Node:
    """Owns the chain and optional network services with explicit lifecycle control."""

    def __init__(self, blockchain: Blockchain | None = None, host: str = "127.0.0.1",
                 p2p_port: int = 5001, rpc_port: int = 8545, data_dir: str | Path | None = None):
        self.data_dir = Path(data_dir) if data_dir is not None else None
        database = self.data_dir / "chain.sqlite3" if self.data_dir is not None else None
        if blockchain is not None:
            self.blockchain = blockchain
        elif database is not None and database.exists():
            self.blockchain = load_chain_sqlite(database)
        else:
            self.blockchain = Blockchain()
        self.p2p = P2PNode(host, p2p_port, self.blockchain)
        self.rpc = RPCNode(self.blockchain, host, rpc_port)
        self._running = False

    @property
    def running(self) -> bool:
        return self._running

    @classmethod
    def from_config(cls, config: NodeConfig) -> "Node":
        database = config.data_dir / "chain.sqlite3"
        blockchain = None
        if not database.exists():
            blockchain = Blockchain(**config.blockchain_kwargs())
        return cls(blockchain, config.host, config.p2p_port, config.rpc_port, config.data_dir)

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
        self.persist()
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
            self.persist()
        return block

    def submit_contract_operation(self, operation: ContractOperation) -> bool:
        accepted = self.blockchain.contract_mempool.add_operation(operation, self.blockchain.contracts)
        if accepted:
            self.p2p.broadcast_contract_operation(operation)
        return accepted

    def snapshot(self, path: str | Path) -> None:
        save_chain(self.blockchain, path)

    def persist(self) -> None:
        if self.data_dir is not None:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            save_chain_sqlite(self.blockchain, self.data_dir / "chain.sqlite3")
