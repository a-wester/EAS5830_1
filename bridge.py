from web3 import Web3
from web3.providers.rpc import HTTPProvider
from web3.middleware import ExtraDataToPOAMiddleware
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


def retry_rpc_call(func, *args, max_retries=5, **kwargs):
    """Retry RPC calls with exponential backoff"""
    for attempt in range(max_retries):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            if attempt == max_retries - 1:
                raise
            
            if hasattr(e, 'args') and len(e.args) > 0:
                error_msg = str(e.args[0])
                if 'limit exceeded' in error_msg or 'rate limit' in error_msg:
                    wait_time = (2 ** attempt) + random.uniform(0, 1)
                    print(f"Rate limit or limit exceeded. Retrying in {wait_time:.2f} seconds...")
                    time.sleep(wait_time)
                    continue
            
            wait_time = (1.5 ** attempt) + random.uniform(0, 0.5)
            print(f"RPC call failed, retrying in {wait_time:.2f} seconds... Error: {e}")
            time.sleep(wait_time)


def send_transaction_with_retry(w3, contract_func, warden_address, warden_key, gas_limit=100000, max_retries=5):
    """
    Helper function to send a transaction with proper retry logic for common errors
    """
    last_error = None
    initial_nonce = w3.eth.get_transaction_count(warden_address)
    initial_gas_price = w3.eth.gas_price
    
    # Calculate a base gas price based on available balance
    available_balance = w3.eth.get_balance(warden_address)
    base_gas_price = min(initial_gas_price, available_balance // (gas_limit * 2))
    
    for attempt in range(max_retries):
        try:
            # For each retry, get a fresh nonce and increase gas price
            current_nonce = initial_nonce + attempt
            current_gas_price = int(base_gas_price * (1.2 ** attempt))  # Increase by 20% each retry
            
            # Make sure gas price doesn't exceed what we can afford
            max_affordable_gas_price = available_balance // gas_limit
            current_gas_price = min(current_gas_price, max_affordable_gas_price)
            
            tx = contract_func.build_transaction({
                'from': warden_address,
                'gas': gas_limit,
                'gasPrice': current_gas_price,
                'nonce': current_nonce,
            })
            
            signed_tx = w3.eth.account.sign_transaction(tx, warden_key)
            tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
            
            print(f"Transaction sent with gas price {current_gas_price} wei. Hash: {tx_hash.hex()}")
            
            # Wait for receipt
            receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
            
            if receipt.status == 1:
                print("Transaction succeeded!")
                return receipt
            else:
                print("Transaction failed on-chain!")
                last_error = Exception("Transaction failed with status 0")
                continue
                
        except Exception as e:
            error_msg = str(e)
            print(f"Attempt {attempt+1}/{max_retries} failed: {error_msg}")
            
            last_error = e
            
            # Handle specific errors
            if 'nonce too low' in error_msg:
                # Get a fresh nonce for next attempt
                initial_nonce = w3.eth.get_transaction_count(warden_address)
                print(f"Updating nonce to {initial_nonce}")
            elif 'replacement transaction underpriced' in error_msg:
                # Significantly increase gas price for next attempt
                base_gas_price = int(base_gas_price * 1.5)
                print(f"Increasing base gas price to {base_gas_price}")
            elif 'insufficient funds' in error_msg:
                # Reduce gas price even more
                base_gas_price = int(base_gas_price * 0.7)
                print(f"Reducing base gas price to {base_gas_price}")
            
            # Wait before retry with exponential backoff
            wait_time = (2 ** attempt) + random.uniform(0, 1)
            print(f"Waiting {wait_time:.2f} seconds before retry...")
            time.sleep(wait_time)
    
    # If we reach here, all retries failed
    raise last_error or Exception("Transaction failed after all retry attempts")


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

    current_block_source = w3_source.eth.block_number
    current_block_dest = w3_dest.eth.block_number

    start_block_source = max(0, current_block_source - 5)
    start_block_dest = max(0, current_block_dest - 2)

    if chain == 'source':
        print(f"Scanning blocks {start_block_source} to {current_block_source} on source chain")

        try:
            deposit_events = w3_source.eth.get_logs({
                'fromBlock': start_block_source,
                'toBlock': current_block_source,
                'address': source_address
            })

            print(f"Found {len(deposit_events)} Deposit events")

            for event in deposit_events:
                try:
                    parsed_event = source_contract.events.Deposit().process_log(event)
                    token = parsed_event.args.token
                    recipient = parsed_event.args.recipient
                    amount = parsed_event.args.amount

                    print(f"Found Deposit: Token: {token}, Recipient: {recipient}, Amount: {amount}")
                    
                    # Use the improved transaction sending function
                    try:
                        receipt = send_transaction_with_retry(
                            w3_dest,
                            dest_contract.functions.wrap(token, recipient, amount),
                            warden_address,
                            warden_key,
                            gas_limit=100000,
                            max_retries=5
                        )
                        
                        if receipt and receipt.status == 1:
                            print(f"Successfully wrapped tokens. Transaction hash: {receipt.transactionHash.hex()}")
                        else:
                            print("Wrap transaction failed")
                            
                    except Exception as tx_error:
                        print(f"Failed to send wrap transaction after all retries: {tx_error}")
                    
                except Exception as e:
                    print(f"Error processing deposit event: {e}")
                    continue

        except Exception as e:
            print(f"Error processing Deposit events: {e}")

    elif chain == 'destination':
        print(f"Scanning blocks {start_block_dest} to {current_block_dest} on destination chain")
        
        time.sleep(3)  # Brief delay before scanning
        
        # Process blocks one by one to avoid rate limits
        found_any_unwrap = False
        for block_num in range(start_block_dest, current_block_dest + 1):
            try:
                print(f"Scanning unwraps from block {block_num}")
                unwrap_topic = w3_dest.keccak(text="Unwrap(address,address,uint256)").hex()
                
                try:
                    # Scan one block at a time to avoid rate limiting
                    unwrap_events = w3_dest.eth.get_logs({
                        'fromBlock': block_num,
                        'toBlock': block_num,
                        'address': dest_address,
                        'topics': [unwrap_topic]
                    })
                    
                    if unwrap_events:
                        found_any_unwrap = True
                        print(f"Found {len(unwrap_events)} Unwrap events in block {block_num}")
                        
                        for event in unwrap_events:
                            try:
                                parsed_event = dest_contract.events.Unwrap().process_log(event)
                                token = parsed_event.args.underlying_token
                                recipient = parsed_event.args.to
                                amount = parsed_event.args.amount
                                
                                print(f"Found Unwrap: Token: {token}, Recipient: {recipient}, Amount: {amount}")
                                
                                # Use the improved transaction sending function
                                try:
                                    receipt = send_transaction_with_retry(
                                        w3_source,
                                        source_contract.functions.withdraw(token, recipient, amount),
                                        warden_address,
                                        warden_key,
                                        gas_limit=100000,
                                        max_retries=5
                                    )
                                    
                                    if receipt and receipt.status == 1:
                                        print(f"Successfully withdrew tokens. Transaction hash: {receipt.transactionHash.hex()}")
                                    else:
                                        print("Withdraw transaction failed")
                                        
                                except Exception as tx_error:
                                    print(f"Failed to send withdraw transaction after all retries: {tx_error}")
                                
                            except Exception as e:
                                print(f"Error processing unwrap event: {e}")
                                continue
                    
                except Exception as log_e:
                    if 'limit exceeded' in str(log_e):
                        print(f"Rate limit exceeded when scanning block {block_num}, waiting...")
                        time.sleep(5)  # Wait 5 seconds when hitting rate limits
                    else:
                        print(f"Error getting logs for block {block_num}: {log_e}")
                
                # Add a delay between block scans to avoid rate limits
                time.sleep(4)
                    
            except Exception as block_e:
                print(f"Error processing block {block_num}: {block_e}")
                time.sleep(3)  # Wait a bit on errors
        
        # If we didn't find any events, try once more with the most recent block
        if not found_any_unwrap:
            try:
                print("No events found in block range. Trying most recent block...")
                last_block = current_block_dest
                
                time.sleep(5)  # Wait a bit before trying
                
                unwrap_topic = w3_dest.keccak(text="Unwrap(address,address,uint256)").hex()
                
                unwrap_events = w3_dest.eth.get_logs({
                    'fromBlock': last_block,
                    'toBlock': last_block,
                    'address': dest_address,
                    'topics': [unwrap_topic]
                })
                
                print(f"Found {len(unwrap_events)} Unwrap events in final attempt")
                
                # Process events
                for event in unwrap_events:
                    try:
                        parsed_event = dest_contract.events.Unwrap().process_log(event)
                        token = parsed_event.args.underlying_token
                        recipient = parsed_event.args.to
                        amount = parsed_event.args.amount
                        
                        print(f"Found Unwrap: Token: {token}, Recipient: {recipient}, Amount: {amount}")
                        
                        # Use the improved transaction sending function
                        try:
                            receipt = send_transaction_with_retry(
                                w3_source,
                                source_contract.functions.withdraw(token, recipient, amount),
                                warden_address,
                                warden_key,
                                gas_limit=100000,
                                max_retries=5
                            )
                            
                            if receipt and receipt.status == 1:
                                print(f"Successfully withdrew tokens. Transaction hash: {receipt.transactionHash.hex()}")
                            else:
                                print("Withdraw transaction failed")
                                
                        except Exception as tx_error:
                            print(f"Failed to send withdraw transaction after all retries: {tx_error}")
                        
                    except Exception as e:
                        print(f"Error processing unwrap event in fallback: {e}")
                        continue
                
            except Exception as final_e:
                print(f"Final attempt also failed: {final_e}")

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
                # Use our improved transaction sending function
                receipt = send_transaction_with_retry(
                    w3_source,
                    source_contract.functions.registerToken(Web3.to_checksum_address(source_token)),
                    warden_address,
                    warden_key,
                    gas_limit=200000,
                    max_retries=3
                )
                
                if receipt and receipt.status == 1:
                    print(f"Successfully registered token on source chain. Transaction hash: {receipt.transactionHash.hex()}")
                else:
                    print("Registration transaction failed")
                
                print(f"Creating wrapped token for {source_token} on destination chain...")
                # Use our improved transaction sending function
                receipt = send_transaction_with_retry(
                    w3_dest,
                    dest_contract.functions.createToken(Web3.to_checksum_address(source_token), name, symbol),
                    warden_address,
                    warden_key,
                    gas_limit=200000,
                    max_retries=3
                )
                
                if receipt and receipt.status == 1:
                    print(f"Successfully created token on destination chain. Transaction hash: {receipt.transactionHash.hex()}")
                else:
                    print("Create token transaction failed")
                
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
    for i, token in enumerate(tokens_to_create):
        try:
            print(f"Creating token {token}")
            
            # Use our improved transaction sending function
            receipt = send_transaction_with_retry(
                w3_dest,
                dest_contract.functions.createToken(Web3.to_checksum_address(token), name, symbol),
                warden_address,
                warden_key,
                gas_limit=200000,
                max_retries=3
            )
            
            if receipt and receipt.status == 1:
                print(f"Successfully created token {token}. Transaction hash: {receipt.transactionHash.hex()}")
            else:
                print(f"Failed to create token {token}")
                
            # Always wait between transactions
            if i < len(tokens_to_create) - 1:  # Don't wait after the last token
                time.sleep(10)
                
        except Exception as e:
            print(f"Failed to create token {token}: {e}")
            time.sleep(10)  # Wait before trying the next token


if __name__ == "__main__":
    # register_tokens()
    # create_missing_tokens()
    
    # Run the scanning operations
    scan_blocks('source')
    
    # Add delay between chain operations
    time.sleep(10)
    
    scan_blocks('destination')
