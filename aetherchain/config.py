"""TOML configuration for AetherChain developer nodes."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 fallback
    tomllib = None  # type: ignore[assignment]


@dataclass(frozen=True)
class NodeConfig:
    host: str = "127.0.0.1"
    p2p_port: int = 5001
    rpc_port: int = 8545
    data_dir: Path = Path("./data")
    difficulty: int = 1
    block_reward: int = 500_000
    target_block_time: float = 2.0
    adjustment_interval: int = 5
    genesis_allocation: int = 1_000_000
    genesis_address: str = "AETH_GENESIS_RESERVE"
    max_block_transactions: int = 10

    @classmethod
    def from_mapping(cls, raw: dict[str, Any]) -> "NodeConfig":
        node = raw.get("node", {})
        consensus = raw.get("consensus", {})
        values = {**{k: getattr(cls, k) for k in cls.__dataclass_fields__}, **node, **consensus}
        values["data_dir"] = Path(values["data_dir"])
        for key in ("p2p_port", "rpc_port", "difficulty", "block_reward", "adjustment_interval", "genesis_allocation", "max_block_transactions"):
            values[key] = int(values[key])
        values["target_block_time"] = float(values["target_block_time"])
        return cls(**{k: values[k] for k in cls.__dataclass_fields__})

    @classmethod
    def load(cls, path: str | Path) -> "NodeConfig":
        path = Path(path)
        if not path.exists():
            return cls()
        if tomllib is None:
            raise RuntimeError("TOML configuration requires Python 3.11+")
        return cls.from_mapping(tomllib.loads(path.read_text(encoding="utf-8")))

    def blockchain_kwargs(self) -> dict[str, Any]:
        return {"difficulty": self.difficulty, "block_reward": self.block_reward,
                "target_block_time": self.target_block_time, "adjustment_interval": self.adjustment_interval,
                "genesis_allocation": self.genesis_allocation, "genesis_address": self.genesis_address,
                "max_block_transactions": self.max_block_transactions}

    def toml(self) -> str:
        return f'''[node]\nhost = "{self.host}"\np2p_port = {self.p2p_port}\nrpc_port = {self.rpc_port}\ndata_dir = "{self.data_dir}"\n\n[consensus]\ndifficulty = {self.difficulty}\nblock_reward = {self.block_reward}\ntarget_block_time = {self.target_block_time}\nadjustment_interval = {self.adjustment_interval}\ngenesis_allocation = {self.genesis_allocation}\ngenesis_address = "{self.genesis_address}"\nmax_block_transactions = {self.max_block_transactions}\n'''

    def save(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.toml(), encoding="utf-8")
