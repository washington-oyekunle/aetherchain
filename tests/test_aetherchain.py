import tempfile
import unittest
from pathlib import Path

from aetherchain.blockchain import Block, Blockchain
from aetherchain.crypto import G, generate_keypair, point_mul, sign_hash, verify_signature
from aetherchain.ledger import SYSTEM, TransactionOutput, UTXOWallet, UTXOTransaction
from aetherchain.node import Node
from aetherchain.p2p import P2PNode
from aetherchain.rpc import RPCNode
from aetherchain.storage import load_chain, save_chain


class AetherChainTests(unittest.TestCase):
    def test_ecdsa_round_trip_and_tamper(self):
        private, public = generate_keypair()
        digest = b"aetherchain-test-digest"
        signature = sign_hash(private, digest)
        self.assertTrue(verify_signature(public, digest, signature))
        self.assertFalse(verify_signature(public, b"tampered", signature))
        self.assertEqual(point_mul(private, G), public)

    def test_transfer_fee_double_spend_and_state_root(self):
        chain = Blockchain(difficulty=0, block_reward=100, adjustment_interval=5)
        miner, alice, bob = UTXOWallet(), UTXOWallet(), UTXOWallet()
        chain.mine_pending_transactions(miner.address)
        tx = miner.create_transaction(alice.address, 40, 3, chain.state)
        self.assertTrue(chain.mempool.add_transaction(tx, chain.state))
        block = chain.mine_pending_transactions(miner.address)
        self.assertIsNotNone(block)
        self.assertEqual(block.state_root, chain.state.state_root())
        self.assertEqual(chain.state.get_balance(alice.address), 40)
        self.assertEqual(chain.state.get_balance(miner.address), 160)
        spend = alice.create_transaction(bob.address, 10, 1, chain.state)
        self.assertTrue(chain.mempool.add_transaction(spend, chain.state))
        self.assertFalse(chain.mempool.add_transaction(spend, chain.state))
        self.assertIsNotNone(chain.mine_pending_transactions(miner.address))
        self.assertEqual(chain.state.get_balance(bob.address), 10)
        self.assertTrue(chain.is_chain_valid())

    def test_tampered_state_root_and_reward_are_rejected(self):
        chain = Blockchain(difficulty=0, block_reward=100)
        miner = UTXOWallet()
        chain.mine_pending_transactions(miner.address)
        candidate = list(chain.chain)
        candidate[-1].state_root = "f" * 64
        candidate[-1].hash = candidate[-1].calculate_hash()
        self.assertFalse(chain.validate_chain(candidate)[0])

        bad_tx = UTXOTransaction([], [TransactionOutput(miner.address, 101)], SYSTEM)
        state = chain.state.clone()
        with self.assertRaises(ValueError):
            state.apply_block([bad_tx], max_coinbase=100)

    def test_atomic_invalid_transaction_and_snapshot_round_trip(self):
        chain = Blockchain(difficulty=0, block_reward=100)
        before = dict(chain.state.utxo_pool)
        invalid = UTXOTransaction([], [], SYSTEM)
        self.assertFalse(chain.mempool.add_transaction(invalid, chain.state))
        self.assertEqual(chain.state.utxo_pool, before)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "chain.json"
            save_chain(chain, path)
            restored = load_chain(path)
            self.assertTrue(restored.is_chain_valid())
            self.assertEqual(restored.state.state_root(), chain.state.state_root())

    def test_rpc_and_network_serialization_round_trip(self):
        wallet = UTXOWallet()
        chain = Blockchain(difficulty=0)
        chain.mine_pending_transactions(wallet.address)
        tx = wallet.create_transaction("AETHrecipient", 1, 0, chain.state)
        restored = P2PNode.deserialize_tx(P2PNode.serialize_tx(tx))
        self.assertEqual(restored.tx_id, tx.tx_id)
        self.assertEqual(restored.signature, tx.signature)
        rpc = RPCNode(chain)
        result, error = rpc.handle({"id": 1, "method": "eth_blockNumber", "params": []})
        self.assertEqual(result, "0x1")
        self.assertIsNone(error)
        result, error = rpc.handle({"id": 2, "method": "aether_getStateRoot", "params": []})
        self.assertEqual(result, chain.state.state_root())
        self.assertIsNone(error)
        stats, error = rpc.handle({"jsonrpc": "2.0", "id": 3, "method": "aether_getChainStats", "params": []})
        self.assertEqual(stats["height"], 1)
        self.assertIsNone(error)
        utxos, error = rpc.handle({"id": 4, "method": "aether_getUtxos", "params": [wallet.address]})
        self.assertEqual(sum(item["amount"] for item in utxos), chain.state.get_balance(wallet.address))
        self.assertIsNone(error)
        self.assertTrue(chain.mempool.add_transaction(tx, chain.state))
        tx_info, error = rpc.handle({"id": 5, "method": "aether_getTransactionByHash", "params": [tx.tx_id]})
        self.assertEqual(tx_info["tx_id"], tx.tx_id)
        self.assertIsNone(error)
        _, error = rpc.handle({"jsonrpc": "1.0", "id": 6, "method": "eth_blockNumber", "params": []})
        self.assertEqual(error["code"], -32600)

    def test_node_lifecycle_and_wire_hash_integrity(self):
        node = Node(Blockchain(difficulty=0), p2p_port=0, rpc_port=0)
        self.assertFalse(node.running)
        node.start()
        self.assertTrue(node.running)
        node.stop()
        self.assertFalse(node.running)

        block = Blockchain(difficulty=0).latest_block()
        encoded = P2PNode.serialize_block(block)
        encoded["hash"] = "0" * 64
        with self.assertRaises(ValueError):
            P2PNode.deserialize_block(encoded)


if __name__ == "__main__":
    unittest.main()
