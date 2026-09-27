import unittest

from aetherchain.blockchain import Blockchain
from aetherchain.crypto import generate_keypair, point_mul, G, sign_hash, verify_signature
from aetherchain.ledger import UTXOWallet, UTXOTransaction
from aetherchain.p2p import P2PNode


class AetherChainTests(unittest.TestCase):
    def test_ecdsa_round_trip_and_tamper(self):
        private, public = generate_keypair()
        digest = b"aetherchain-test-digest"
        signature = sign_hash(private, digest)
        self.assertTrue(verify_signature(public, digest, signature))
        self.assertFalse(verify_signature(public, b"tampered", signature))
        self.assertEqual(point_mul(private, G), public)

    def test_transfer_fee_and_double_spend(self):
        chain = Blockchain(difficulty=0, block_reward=100, adjustment_interval=5)
        miner, alice, bob = UTXOWallet(), UTXOWallet(), UTXOWallet()
        chain.mine_pending_transactions(miner.address)
        tx = miner.create_transaction(alice.address, 40, 3, chain.state)
        self.assertTrue(chain.mempool.add_transaction(tx, chain.state))
        chain.mine_pending_transactions(miner.address)
        self.assertEqual(chain.state.get_balance(alice.address), 40)
        self.assertEqual(chain.state.get_balance(miner.address), 160)
        spend = alice.create_transaction(bob.address, 10, 1, chain.state)
        self.assertTrue(chain.mempool.add_transaction(spend, chain.state))
        self.assertFalse(chain.mempool.add_transaction(spend, chain.state))
        self.assertIsNotNone(chain.mine_pending_transactions(miner.address))
        self.assertEqual(chain.state.get_balance(bob.address), 10)
        self.assertTrue(chain.is_chain_valid())

    def test_atomic_block_rejects_invalid_transaction(self):
        chain = Blockchain(difficulty=0, block_reward=100)
        miner = UTXOWallet()
        before = dict(chain.state.utxo_pool)
        invalid = UTXOTransaction([], [], "SYSTEM")
        self.assertFalse(chain.mempool.add_transaction(invalid, chain.state))
        self.assertEqual(chain.state.utxo_pool, before)

    def test_network_serialization_round_trip(self):
        wallet = UTXOWallet()
        chain = Blockchain(difficulty=0)
        chain.mine_pending_transactions(wallet.address)
        tx = wallet.create_transaction("AETHrecipient", 1, 0, chain.state)
        restored = P2PNode.deserialize_tx(P2PNode.serialize_tx(tx))
        self.assertEqual(restored.tx_id, tx.tx_id)
        self.assertEqual(restored.signature, tx.signature)


if __name__ == "__main__":
    unittest.main()
