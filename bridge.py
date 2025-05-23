from web3 import Web3
from web3.providers.rpc import HTTPProvider
from web3.middleware import ExtraDataToPOAMiddleware  # Necessary for POA chains
from datetime import datetime
import json
import pandas as pd
import time
import random

warden_address = "0x3b178a0a54730C2AAe0b327C77aF2d78F3Dca55B"
warden_key = "0xc72d903f58f9b5aecfc15ca6916720a88cc8b090e27ce9bb0db52bb0cd05c1d3"

def connect_to(chain):
    if chain == 'source':  # The source contract chain is avax
        api_url = f"https://api.avax-test.network/ext/bc/C/rpc"  # AVAX C-chain testnet

    if chain == 'destination':  # The destination contract chain is bsc
        api_url = f"https://data-seed-prebsc-1-s1.binance.org:8545/"  # BSC testnet

    if chain in ['source', 'destination']:
        w3 = Web3(Web3.HTTPProvider(api_url))
        # inject the poa compatibility middleware to the innermost layer
        w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
    return w3


def get_contract_info(chain, contract_info):
    """
        Load the contract_info file into a dictionary
        This function is used by the autograder and will likely be useful to you
    """
    try:
        with open(contract_info, 'r') as f:
            contracts = json.load(f)
    except Exception as e:
        print(f"Failed to read contract info\nPlease contact your instructor\n{e}")
        return 0
    return contracts[chain]


# Helper function for retry logic
def retry_rpc_call(func, *args, max_retries=5, **kwargs):
    """Retry RPC calls with exponential backoff"""
    for attempt in range(max_retries):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            if attempt == max_retries - 1:
                raise
            
            # Check if it's a limit exceeded error
            if hasattr(e, 'args') and len(e.args) > 0:
                error_msg = str(e.args[0])
                if 'limit exceeded' in error_msg or 'rate limit' in error_msg:
                    wait_time = (2 ** attempt) + random.uniform(0, 1)
                    print(f"Rate limit or limit exceeded. Retrying in {wait_time:.2f} seconds...")
                    time.sleep(wait_time)
                    continue
            
            # For other errors, retry with shorter wait
            wait_time = (1.5 ** attempt) + random.uniform(0, 0.5)
            print(f"RPC call failed, retrying in {wait_time:.2f} seconds... Error: {e}")
            time.sleep(wait_time)


def scan_blocks(chain, contract_info="contract_info.json"):
    """
        chain - (string) should be either "source" or "destination"
        Scan the last 5 blocks of the source and destination chains
        Look for 'Deposit' events on the source chain and 'Unwrap' events on the destination chain
        When Deposit events are found on the source chain, call the 'wrap' function on the destination chain
        When Unwrap events are found on the destination chain, call the 'withdraw' function on the source chain
    """
    if chain not in ['source', 'destination']:
        print(f"Invalid chain: {chain}")
        return 0

    with open(contract_info, 'r') as f:
        info = json.load(f)

    w3_source = connect_to('source')
    w3_dest = connect_to('destination')

    source_address = Web3.to_checksum_address(info["source"]["address"])
    dest_address = Web3.to_checksum_address(info["destination"]["address"])

    warden_address = Web3.to_checksum_address("0x3b178a0a54730C2AAe0b327C77aF2d78F3Dca55B")
    warden_key = "0xc72d903f58f9b5aecfc15ca6916720a88cc8b090e27ce9bb0db52bb0cd05c1d3"

    source_contract = w3_source.eth.contract(
        address=source_address,
        abi=info["source"]["abi"]
    )

    dest_contract = w3_dest.eth.contract(
        address=dest_address,
        abi=info["destination"]["abi"]
    )

    # Get current block numbers
    current_block_source = w3_source.eth.block_number
    current_block_dest = w3_dest.eth.block_number

    # Define the blocks to scan - increase the range to catch more events
    start_block_source = max(0, current_block_source - 10)  # Increased from 5 to 10
    start_block_dest = max(0, current_block_dest - 15)  # Increased from 10 to 15

    if chain == 'source':
        print(f"Scanning blocks {start_block_source} to {current_block_source} on source chain")
        print(f"Scanning blocks {start_block_source} - {current_block_source} on source chain")

        try:
            # Use create_filter approach exactly as in Bridge IV
            arg_filter = {}
            event_filter = source_contract.events.Deposit.create_filter(from_block=start_block_source, to_block=current_block_source, argument_filters=arg_filter)
            deposit_events = event_filter.get_all_entries()
            
            print(f"Found {len(deposit_events)} Deposit events")

            for event in deposit_events:
                try:
                    # Extract event args directly
                    token = event.args.token
                    recipient = event.args.recipient
                    amount = event.args.amount

                    print(f"Found Deposit: Token: {token}, Recipient: {recipient}, Amount: {amount}")

                    # Get a fresh nonce for each transaction
                    nonce = w3_dest.eth.get_transaction_count(warden_address)
                    
                    # Try to build and send the transaction with retry logic for nonce issues
                    max_nonce_retries = 3
                    for nonce_retry in range(max_nonce_retries):
                        try:
                            wrap_tx = dest_contract.functions.wrap(
                                token,
                                recipient,
                                amount
                            ).build_transaction({
                                'from': warden_address,
                                'gas': 200000,
                                'gasPrice': w3_dest.eth.gas_price,
                                'nonce': nonce + nonce_retry,
                            })

                            signed_tx = w3_dest.eth.account.sign_transaction(wrap_tx, warden_key)
                            tx_hash = w3_dest.eth.send_raw_transaction(signed_tx.raw_transaction)

                            print(f"Sent wrap transaction: {tx_hash.hex()}")

                            receipt = w3_dest.eth.wait_for_transaction_receipt(tx_hash)
                            if receipt.status == 1:
                                print("Wrap transaction succeeded")
                            else:
                                print("Wrap transaction failed")
                            
                            # If successful, break out of retry loop
                            break
                            
                        except Exception as e:
                            error_msg = str(e)
                            if 'nonce too low' in error_msg and nonce_retry < max_nonce_retries - 1:
                                print(f"Nonce too low. Retrying with incremented nonce {nonce + nonce_retry + 1}")
                                continue
                            else:
                                raise  # Re-raise other exceptions or if we've exhausted retries
                    
                except Exception as e:
                    print(f"Error processing deposit event: {e}")
                    continue

        except Exception as e:
            print(f"Error processing Deposit events: {e}")

    elif chain == 'destination':
        print(f"Scanning blocks {start_block_dest} to {current_block_dest} on destination chain")
        print(f"Scanning blocks {start_block_dest} - {current_block_dest} on destination chain")
        
        try:
            # Use create_filter approach exactly as in Bridge IV
            arg_filter = {}
            event_filter = dest_contract.events.Unwrap.create_filter(from_block=start_block_dest, to_block=current_block_dest, argument_filters=arg_filter)
            unwrap_events = event_filter.get_all_entries()
            
            print(f"Found {len(unwrap_events)} Unwrap events")
            
            for event in unwrap_events:
                try:
                    # Extract event args directly
                    token = event.args.underlying_token
                    recipient = event.args.to
                    amount = event.args.amount
                    
                    print(f"Found Unwrap: Token: {token}, Recipient: {recipient}, Amount: {amount}")
                    
                    # Get a fresh nonce with retry logic
                    max_nonce_retries = 3
                    for nonce_retry in range(max_nonce_retries):
                        try:
                            nonce = w3_source.eth.get_transaction_count(warden_address)
                            
                            withdraw_tx = source_contract.functions.withdraw(
                                token,
                                recipient,
                                amount
                            ).build_transaction({
                                'from': warden_address,
                                'gas': 200000,
                                'gasPrice': w3_source.eth.gas_price,
                                'nonce': nonce + nonce_retry,  # Increment nonce on retries
                            })
                            
                            signed_tx = w3_source.eth.account.sign_transaction(withdraw_tx, warden_key)
                            tx_hash = w3_source.eth.send_raw_transaction(signed_tx.raw_transaction)
                            
                            print(f"Sent withdraw transaction: {tx_hash.hex()}")
                            
                            receipt = w3_source.eth.wait_for_transaction_receipt(tx_hash)
                            if receipt.status == 1:
                                print("Withdraw transaction succeeded")
                            else:
                                print("Withdraw transaction failed")
                            
                            # If successful, break out of retry loop
                            break
                                
                        except Exception as e:
                            error_msg = str(e)
                            if 'nonce too low' in error_msg and nonce_retry < max_nonce_retries - 1:
                                print(f"Nonce too low. Retrying with incremented nonce {nonce + nonce_retry + 1}")
                                continue
                            else:
                                raise  # Re-raise other exceptions or if we've exhausted retries
                            
                except Exception as e:
                    print(f"Error processing unwrap event: {e}")
                    continue
        
        except Exception as e:
            print(f"Error scanning destination chain: {e}")

    return 1


def register_tokens(contract_info="contract_info.json", token_csv="erc20s.csv"):
    """
    Register tokens on both chains using the token addresses from the CSV file
    """
    with open(contract_info, 'r') as f:
        info = json.load(f)
    
    w3_source = connect_to('source')
    w3_dest = connect_to('destination')
    
    source_address = Web3.to_checksum_address(info["source"]["address"])
    dest_address = Web3.to_checksum_address(info["destination"]["address"])
    
    
    source_contract = w3_source.eth.contract(
        address=source_address,
        abi=info["source"]["abi"]
    )
    
    dest_contract = w3_dest.eth.contract(
        address=dest_address,
        abi=info["destination"]["abi"]
    )
    
    try:
        source_tokens = []
        dest_tokens = []
        
        with open(token_csv, 'r') as f:
            next(f)
            for line in f:
                parts = line.strip().split(',')
                if len(parts) == 2:
                    chain, token_address = parts
                    if chain.lower() == 'avax':
                        source_tokens.append(token_address)
                    elif chain.lower() == 'bsc':
                        dest_tokens.append(token_address)
        
        for i in range(min(len(source_tokens), len(dest_tokens))):
            source_token = source_tokens[i]
            dest_token = dest_tokens[i]
            
            name = "Wrapped Token"
            symbol = "WTKN"
            
            try:
                print(f"Registering token {source_token} on source chain...")
                # Get fresh nonce for source chain transaction
                nonce = w3_source.eth.get_transaction_count(warden_address)
                
                register_tx = source_contract.functions.registerToken(
                    Web3.to_checksum_address(source_token)
                ).build_transaction({
                    'from': warden_address,
                    'gas': 200000, 
                    'gasPrice': w3_source.eth.gas_price,
                    'nonce': nonce,
                })
                
                signed_tx = w3_source.eth.account.sign_transaction(register_tx, warden_key)
                tx_hash = w3_source.eth.send_raw_transaction(signed_tx.raw_transaction)
                
                print(f"Sent register token transaction: {tx_hash.hex()}")
                receipt = w3_source.eth.wait_for_transaction_receipt(tx_hash)
                
                print(f"Creating wrapped token for {source_token} on destination chain...")
                # Get fresh nonce for destination chain transaction
                nonce = w3_dest.eth.get_transaction_count(warden_address)
                
                create_tx = dest_contract.functions.createToken(
                    Web3.to_checksum_address(source_token),
                    name,
                    symbol
                ).build_transaction({
                    'from': warden_address,
                    'gas': 200000,
                    'gasPrice': w3_dest.eth.gas_price,
                    'nonce': nonce,
                })
                
                signed_tx = w3_dest.eth.account.sign_transaction(create_tx, warden_key)
                tx_hash = w3_dest.eth.send_raw_transaction(signed_tx.raw_transaction)
                
                print(f"Sent create token transaction: {tx_hash.hex()}")
                receipt = w3_dest.eth.wait_for_transaction_receipt(tx_hash)
                
                # Add a delay between token registrations to avoid rate limiting
                time.sleep(10)
                
            except Exception as e:
                print(f"Error registering/creating token {source_token}: {e}")
    
    except Exception as e:
        print(f"Error reading token CSV file: {e}")


def create_missing_tokens():
    contract_info = "contract_info.json"
    with open(contract_info, 'r') as f:
        info = json.load(f)

    w3_dest = connect_to("destination")
    dest_address = Web3.to_checksum_address(info["destination"]["address"])
    dest_contract = w3_dest.eth.contract(address=dest_address, abi=info["destination"]["abi"])

    tokens_to_create = [
        "0xc677c31AD31F73A5290f5ef067F8CEF8d301e45c",
        "0x0773b81e0524447784CcE1F3808fed6AaA156eC8"
    ]

    name = "Wrapped Token"
    symbol = "WTKN"

    # Create tokens one at a time with proper error handling
    # First token
    try:
        # Get the current nonce
        nonce = w3_dest.eth.get_transaction_count(warden_address)
        print(f"Creating token {tokens_to_create[0]} with nonce {nonce}")
        
        tx = dest_contract.functions.createToken(
            Web3.to_checksum_address(tokens_to_create[0]),
            name,
            symbol
        ).build_transaction({
            'from': warden_address,
            'gas': 3000000,
            'gasPrice': w3_dest.eth.gas_price,
            'nonce': nonce,
        })

        signed_tx = w3_dest.eth.account.sign_transaction(tx, warden_key)
        tx_hash = w3_dest.eth.send_raw_transaction(signed_tx.raw_transaction)
        receipt = w3_dest.eth.wait_for_transaction_receipt(tx_hash)
        print(f"Created token {tokens_to_create[0]}: {tx_hash.hex()}")
    except Exception as e:
        print(f"Failed to create token {tokens_to_create[0]}: {e}")
    
    # Always wait between transactions
    time.sleep(10)
    
    # Second token (with fresh nonce)
    try:
        # Get a new nonce - this is crucial!
        nonce = w3_dest.eth.get_transaction_count(warden_address)
        print(f"Creating token {tokens_to_create[1]} with nonce {nonce}")
        
        tx = dest_contract.functions.createToken(
            Web3.to_checksum_address(tokens_to_create[1]),
            name,
            symbol
        ).build_transaction({
            'from': warden_address,
            'gas': 3000000,
            'gasPrice': w3_dest.eth.gas_price,
            'nonce': nonce,
        })

        signed_tx = w3_dest.eth.account.sign_transaction(tx, warden_key)
        tx_hash = w3_dest.eth.send_raw_transaction(signed_tx.raw_transaction)
        receipt = w3_dest.eth.wait_for_transaction_receipt(tx_hash)
        print(f"Created token {tokens_to_create[1]}: {tx_hash.hex()}")
    except Exception as e:
        print(f"Failed to create token {tokens_to_create[1]}: {e}")


if __name__ == "__main__":
    # register_tokens()
    # create_missing_tokens()
    
    # Run the scanning operations
    scan_blocks('source')
    
    # Add delay between chain operations
    time.sleep(10)
    
    scan_blocks('destination')
