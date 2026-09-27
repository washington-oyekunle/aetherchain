from .blockchain import Blockchain
from .ledger import UTXOWallet


def main() -> None:
    chain = Blockchain(difficulty=1, block_reward=5_000, adjustment_interval=5)
    miner, alice, bob = UTXOWallet(), UTXOWallet(), UTXOWallet()
    chain.mine_pending_transactions(miner.address)
    funding = miner.create_transaction(alice.address, amount=1_000, fee=25, state=chain.state)
    assert chain.mempool.add_transaction(funding, chain.state)
    chain.mine_pending_transactions(miner.address)
    payment = alice.create_transaction(bob.address, amount=400, fee=10, state=chain.state)
    assert chain.mempool.add_transaction(payment, chain.state)
    chain.mine_pending_transactions(miner.address)
    print(f"Bob balance: {chain.state.get_balance(bob.address)} base units")
    print(f"Chain height: {len(chain.chain) - 1}; valid: {chain.is_chain_valid()}")


if __name__ == "__main__":
    main()
