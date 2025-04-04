import eth_account
import random
import string
import json
from pathlib import Path
from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware  # Necessary for POA chains


def merkle_assignment():
    num_of_primes = 8192
    primes = generate_primes(num_of_primes)
    leaves = convert_leaves(primes)
    tree = build_merkle(leaves)
    random_leaf_index = random.randint(1, len(leaves) - 1)
    proof = prove_merkle(tree, random_leaf_index)
    challenge = ''.join(random.choice(string.ascii_letters) for i in range(32))
    addr, sig = sign_challenge(challenge)
    if sign_challenge_verify(challenge, addr, sig):
        tx_hash = '0x'
        # tx_hash = send_signed_msg(proof, leaves[random_leaf_index])


def generate_primes(num_primes):
    primes_list = []
    num = 2
    while len(primes_list) < num_primes:
        is_prime = True
        for p in primes_list:
            if p * p > num:
                break
            if num % p == 0:
                is_prime = False
                break
        if is_prime:
            primes_list.append(num)
        num += 1
    return primes_list


def convert_leaves(primes_list):
    leaves = []
    for prime in primes_list:
        pb = prime.to_bytes(32, byteorder='big')
        leaves.append(Web3.keccak(pb))
    return leaves


def build_merkle(leaves):
    tree = [leaves]
    level = leaves
    while len(level) > 1:
        next_level = []
        for i in range(0, len(level), 2):
            left = level[i]
            right = level[i + 1] if i + 1 < len(level) else left
            parent = hash_pair(left, right)
            next_level.append(parent)
        tree.append(next_level)
        level = next_level
    return tree


def prove_merkle(merkle_tree, index):
    merkle_proof = []
    for level in merkle_tree[:-1]:
        sibling_index = index ^ 1
        if sibling_index < len(level):
            merkle_proof.append(level[sibling_index])
        else:
            merkle_proof.append(level[index])
        index //= 2
    return merkle_proof


def sign_challenge(challenge):
    acct = get_account()
    addr = acct.address
    eth_sk = acct.key
    eth_encoded_msg = eth_account.messages.encode_defunct(text=challenge)
    eth_sig_obj = eth_account.Account.sign_message(eth_encoded_msg, private_key=eth_sk)
    return addr, eth_sig_obj.signature.hex()


def send_signed_msg(proof, random_leaf):
    chain = 'bsc'
    acct = get_account()
    address, abi = get_contract_info(chain)
    w3 = connect_to(chain)
    tx_hash = 'placeholder'
    contract = w3.eth.contract(address=address, abi=abi)
    tx = contract.functions.submit(proof, random_leaf).build_transaction({
        'nonce': w3.eth.get_transaction_count(acct.address),
        'gas': 250000,
        'gasPrice': w3.to_wei('10', 'gwei'),
        'chainId': 97
    })
    signed_tx = w3.eth.account.sign_transaction(tx, private_key=acct.key)
    tx_hash = w3.eth.send_raw_transaction(signed_tx['rawTransaction'])
    return tx_hash


def connect_to(chain):
    if chain not in ['avax','bsc']:
        print(f"{chain} is not a valid option for 'connect_to()'")
        return None
    if chain == 'avax':
        api_url = f"https://api.avax-test.network/ext/bc/C/rpc"
    else:
        api_url = f"https://data-seed-prebsc-1-s1.binance.org:8545/"
    w3 = Web3(Web3.HTTPProvider(api_url))
    w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
    return w3


def get_account():
    cur_dir = Path(__file__).parent.absolute()
    with open(cur_dir.joinpath('sk.txt'), 'r') as f:
        sk = f.readline().rstrip()
    if sk[0:2] == "0x":
        sk = sk[2:]
    return eth_account.Account.from_key(sk)


def get_contract_info(chain):
    contract_file = Path(__file__).parent.absolute() / "contract_info.json"
    if not contract_file.is_file():
        contract_file = Path(__file__).parent.parent.parent / "tests" / "contract_info.json"
    with open(contract_file, "r") as f:
        d = json.load(f)
        d = d[chain]
    return d['address'], d['abi']


def sign_challenge_verify(challenge, addr, sig):
    eth_encoded_msg = eth_account.messages.encode_defunct(text=challenge)
    if eth_account.Account.recover_message(eth_encoded_msg, signature=sig) == addr:
        print(f"Success: signed the challenge {challenge} using address {addr}!")
        return True
    else:
        print(f"Failure: The signature does not verify!")
        print(f"signature = {sig}\naddress = {addr}\nchallenge = {challenge}")
        return False


def hash_pair(a, b):
    if a < b:
        return Web3.solidity_keccak(['bytes32', 'bytes32'], [a, b])
    else:
        return Web3.solidity_keccak(['bytes32', 'bytes32'], [b, a])


if __name__ == "__main__":
    merkle_assignment()

