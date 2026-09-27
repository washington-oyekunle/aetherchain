"""Canonical UTXO transactions and an atomic, cryptographically committed ledger."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Optional

from .crypto import Point, Wallet, address_from_public_key, sign_hash, verify_signature

SYSTEM = "SYSTEM"


def _amount(value: object) -> bool:
    return type(value) is int and value > 0


@dataclass(frozen=True, slots=True)
class TransactionInput:
    tx_id: str
    output_index: int

    @property
    def utxo_id(self) -> str:
        return f"{self.tx_id}:{self.output_index}"


@dataclass(frozen=True, slots=True)
class TransactionOutput:
    recipient: str
    amount: int


@dataclass(frozen=True, slots=True)
class UTXO:
    tx_id: str
    output_index: int
    recipient: str
    amount: int

    @property
    def id(self) -> str:
        return f"{self.tx_id}:{self.output_index}"


class UTXOTransaction:
    def __init__(self, inputs: list[TransactionInput], outputs: list[TransactionOutput], sender: str):
        self.sender = sender
        self.inputs = list(inputs)
        self.outputs = list(outputs)
        self.sender_pubkey: Optional[Point] = None
        self.signature: Optional[tuple[int, int]] = None
        self.tx_id = self.calculate_hash_hex()

    def _payload(self) -> dict:
        return {
            "sender": self.sender,
            "inputs": [{"tx_id": i.tx_id, "output_index": i.output_index} for i in self.inputs],
            "outputs": [{"recipient": o.recipient, "amount": o.amount} for o in self.outputs],
        }

    def calculate_hash(self) -> bytes:
        encoded = json.dumps(self._payload(), sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).digest()

    def calculate_hash_hex(self) -> str:
        return self.calculate_hash().hex()

    def sign(self, wallet: Wallet) -> None:
        if wallet.address != self.sender:
            raise ValueError("wallet address does not match transaction sender")
        self.sender_pubkey = wallet.public_key
        self.signature = sign_hash(wallet.private_key, self.calculate_hash())

    def is_coinbase(self) -> bool:
        return self.sender == SYSTEM

    def validate(self, pool: dict[str, UTXO], max_coinbase: Optional[int] = None) -> tuple[bool, int, str]:
        if self.tx_id != self.calculate_hash_hex():
            return False, 0, "transaction id does not match canonical payload"
        if not self.outputs or any(type(o.recipient) is not str or not o.recipient or not _amount(o.amount) for o in self.outputs):
            return False, 0, "outputs must have non-empty recipients and positive integer amounts"
        if self.is_coinbase():
            total = sum(o.amount for o in self.outputs)
            if self.inputs or (max_coinbase is not None and total > max_coinbase):
                return False, 0, "invalid coinbase transaction"
            return True, 0, ""
        if not self.sender or not self.inputs or self.sender_pubkey is None or self.signature is None:
            return False, 0, "signed transaction is missing sender, inputs, or signature"
        if address_from_public_key(self.sender_pubkey) != self.sender:
            return False, 0, "sender does not match public key"
        if not verify_signature(self.sender_pubkey, self.calculate_hash(), self.signature):
            return False, 0, "invalid signature"
        seen: set[str] = set()
        input_sum = 0
        for txin in self.inputs:
            if type(txin.output_index) is not int or txin.output_index < 0 or txin.utxo_id in seen:
                return False, 0, "duplicate or invalid input"
            seen.add(txin.utxo_id)
            utxo = pool.get(txin.utxo_id)
            if utxo is None:
                return False, 0, "input is missing or already spent"
            if utxo.recipient != self.sender:
                return False, 0, "input is not owned by sender"
            input_sum += utxo.amount
        output_sum = sum(o.amount for o in self.outputs)
        if output_sum > input_sum:
            return False, 0, "outputs exceed inputs"
        return True, input_sum - output_sum, ""


class UTXOState:
    def __init__(self, pool: Optional[dict[str, UTXO]] = None):
        self.utxo_pool = dict(pool or {})

    def clone(self) -> "UTXOState":
        return UTXOState(self.utxo_pool)

    def get_balance(self, address: str) -> int:
        return sum(u.amount for u in self.utxo_pool.values() if u.recipient == address)

    def get_user_utxos(self, address: str) -> list[UTXO]:
        return sorted((u for u in self.utxo_pool.values() if u.recipient == address), key=lambda u: u.id)

    def state_root(self) -> str:
        """Deterministic commitment to every live UTXO and its ownership/value."""
        leaves = [f"{u.id}|{u.recipient}|{u.amount}" for u in sorted(self.utxo_pool.values(), key=lambda x: x.id)]
        return hashlib.sha256("\n".join(leaves).encode()).hexdigest()

    def apply(self, tx: UTXOTransaction, max_coinbase: Optional[int] = None) -> int:
        valid, fee, reason = tx.validate(self.utxo_pool, max_coinbase=max_coinbase)
        if not valid:
            raise ValueError(reason)
        if any(f"{tx.tx_id}:{index}" in self.utxo_pool for index in range(len(tx.outputs))):
            raise ValueError("transaction id already exists in state")
        if not tx.is_coinbase():
            for txin in tx.inputs:
                del self.utxo_pool[txin.utxo_id]
        for index, output in enumerate(tx.outputs):
            self.utxo_pool[f"{tx.tx_id}:{index}"] = UTXO(tx.tx_id, index, output.recipient, output.amount)
        return fee

    def apply_block(self, transactions: list[UTXOTransaction], max_coinbase: int) -> int:
        if not transactions or not transactions[0].is_coinbase() or sum(tx.is_coinbase() for tx in transactions) != 1:
            raise ValueError("block must contain exactly one first-position coinbase")
        staged = self.clone()
        total_fees = 0
        for tx in transactions:
            total_fees += staged.apply(tx, max_coinbase=max_coinbase if tx.is_coinbase() else None)
        self.utxo_pool = staged.utxo_pool
        return total_fees


class UTXOWallet(Wallet):
    def create_transaction(self, recipient: str, amount: int, fee: int, state: UTXOState) -> UTXOTransaction:
        if type(amount) is not int or amount <= 0 or type(fee) is not int or fee < 0:
            raise ValueError("amount must be a positive integer and fee a non-negative integer")
        selected: list[UTXO] = []
        total = 0
        for utxo in state.get_user_utxos(self.address):
            selected.append(utxo)
            total += utxo.amount
            if total >= amount + fee:
                break
        if total < amount + fee:
            raise ValueError("insufficient funds")
        outputs = [TransactionOutput(recipient, amount)]
        if total - amount - fee:
            outputs.append(TransactionOutput(self.address, total - amount - fee))
        tx = UTXOTransaction([TransactionInput(u.tx_id, u.output_index) for u in selected], outputs, self.address)
        tx.sign(self)
        return tx
