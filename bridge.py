from web3 import Web3
from web3.providers.rpc import HTTPProvider
from web3.middleware import ExtraDataToPOAMiddleware
from datetime import datetime
import json
import pandas as pd
import time
import random
import os

# Store processed events to avoid duplicates
processed_events = set()

# Create a file to store processed events if the script restarts
EVENTS_LOG_FILE = "processed_events.json"
if os.path.exists(EVENTS_LOG_FILE):
    try:
        with open(EVENTS_LOG_FILE, "r") as f:
            processed_events = set(json.load(f))
    except Exception as e:
        print(f"Failed to load processed events: {e}")

def save_processed_events():
    try:
        with open(EVENTS_LOG_FILE, "w") as f:
            json.dump(list(processed_events), f)
    except Exception as e:
        print(f"Failed to save processed events: {e}")

warden_address = "0x3b178a0a54730C2AAe0b327C77aF2d78F3Dca55B"
warden_key = "0xc72d903f58f9b5aecfc15ca6916720a88cc8b090e27ce9bb0db52bb0cd05c1d3"

def connect_to(chain):
    """Enhanced connection function with multiple RPC endpoints"""
    if chain == 'source':  # The source contract chain is avax
        api_urls = [
            "https://api.avax-test.network/ext/bc/C/rpc",
            # Add backup Avalanche testnet RPC endpoints if available
        ]
    
    if chain == 'destination':  # The destination contract chain is bsc
        api_urls = [
            "https://data-seed-prebsc-1-s1.binance.org:8545/",
            "https://data-seed-prebsc-2-s1.binance.org:8545/",
            "https://data-seed-prebsc-1-s2.binance.org:8545/",
            "https://data-seed-prebsc-2-s2.binance.org:8545/"
        ]
    
    # Try endpoints until one works
    for api_url in api_urls:
        try:
            w3 = Web3(Web3.HTTPProvider(api_url))
            w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
            
            # Test connection
            if w3.is_connected():
                print(f"Connected to {chain} chain using {api_url}")
                return w3
        except Exception as e:
            print(f"Failed to connect to {api_url}: {e}")
    
    # If we get here, all connections failed
    raise Exception(f"Could not connect to any RPC endpoint for {chain}")

def get_contract_info(chain, contract_info):
    """Load the contract_info file into a dictionary"""
    try:
        with open(contract_info, 'r') as f:
            contracts = json.load(f)
    except Exception as e:
        print(f"Failed to read contract info\nPlease contact your instructor\n{e}")
        return 0
    return contracts[chain]

def retry_rpc_call(func, *args, max_retries=5, initial_wait=3, **kwargs):
    """Enhanced retry RPC calls with more aggressive exponential backoff"""
    last_exception = None
    
    for attempt in range(max_retries):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            last_exception = e
            
            if attempt == max_retries - 1:
                # On last attempt, re-raise
                raise
            
            # Check if this is a rate limit error
            error_msg = str(e)
            if isinstance(e, dict) and 'message' in e:
                error_msg = e['message']
            elif hasattr(e, 'args') and len(e.args) > 0:
                error_msg = str(e.args[0])
            
            if 'limit exceeded' in error_msg or 'rate limit' in error_msg:
                # Rate limit hit - use longer backoff
                wait_time = (initial_wait ** (attempt + 1)) + random.uniform(3, 8)
                print(f"Rate limit exceeded. Waiting {wait_time:.2f} seconds before retry {attempt+1}/{max_retries}...")
            else:
                # Regular error - use standard backoff
                wait_time = (2 ** attempt) + random.uniform(1, 3)
                print(f"RPC call failed: {error_msg}. Retrying in {wait_time:.2f} seconds ({attempt+1}/{max_retries})...")
            
            time.sleep(wait_time)
    
    # Should never get here due to re-raise on last attempt
    return None

def scan_blocks(chain, contract_info="contract_info.json"):
    """
    Enhanced scan_blocks function with improved rate limit handling and robustness
    """
    if chain not in ['source', 'destination']:
        print(f"Invalid chain: {chain}")
        return 0

    # Load contract info
    try:
        with open(contract_info, 'r') as f:
            info = json.load(f)
    except Exception as e:
        print(f"Failed to load contract info: {e}")
        return 0

    # Connect to both chains with robust connection handling
    try:
        w3_source = connect_to('source')
        w3_dest = connect_to('destination')
    except Exception as e:
        print(f"Failed to connect to chains: {e}")
        return 0

    # Set up contract addresses and interfaces
    source_address = Web3.to_checksum_address(info["source"]["address"])
    dest_address = Web3.to_checksum_address(info["destination"]["address"])
    warden_address = Web3.to_checksum_address("0x3b178a0a54730C2AAe0b327C77aF2d78F3Dca55B")
    warden_key = "0xc72d903f58f9b5aecfc15ca6916720a88cc8b090e27ce9bb0db52bb0cd05c1d3"

    # Create contract instances
    try:
        source_contract = w3_source.eth.contract(
            address=source_address,
            abi=info["source"]["abi"]
        )

        dest_contract = w3_dest.eth.contract(
            address=dest_address,
            abi=info["destination"]["abi"]
        )
    except Exception as e:
        print(f"Failed to create contract instances: {e}")
        return 0

    # Get current block numbers with retry
    try:
        current_block_source = retry_rpc_call(w3_source.eth.block_number)
        current_block_dest = retry_rpc_call(w3_dest.eth.block_number)
    except Exception as e:
        print(f"Failed to get current block numbers: {e}")
        return 0

    # Set scan ranges - more conservative for destination chain
    start_block_source = max(0, current_block_source - 5)
    # Only scan 2 blocks for destination to avoid rate limits
    start_block_dest = max(0, current_block_dest - 2)

    # Process source chain (AVAX)
    if chain == 'source':
        print(f"Scanning blocks {start_block_source} to {current_block_source} on source chain")

        try:
            # Get deposit events with retry mechanism
            deposit_events = retry_rpc_call(
                w3_source.eth.get_logs,
                {
                    'fromBlock': start_block_source,
                    'toBlock': current_block_source,
                    'address': source_address
                },
                max_retries=4,
                initial_wait=2
            )

            print(f"Found {len(deposit_events)} Deposit events")

            for event in deposit_events:
                try:
                    # Check if we've already processed this event
                    tx_hash = event['transactionHash'].hex()
                    if tx_hash in processed_events:
                        print(f"Skipping already processed deposit: {tx_hash}")
                        continue
                    
                    # Parse the event
                    parsed_event = source_contract.events.Deposit().process_log(event)
                    token = parsed_event.args.token
                    recipient = parsed_event.args.recipient
                    amount = parsed_event.args.amount

                    print(f"Found Deposit: Token: {token}, Recipient: {recipient}, Amount: {amount}")
                    
                    # Process with enhanced nonce handling
                    wrapped_token_tx_hash = handle_wrap_transaction(
                        w3_dest, dest_contract, token, recipient, amount, warden_address, warden_key
                    )
                    
                    if wrapped_token_tx_hash:
                        # Add to processed events if successful
                        processed_events.add(tx_hash)
                        save_processed_events()
                    
                except Exception as e:
                    print(f"Error processing deposit event: {e}")
                    continue
                    
        except Exception as e:
            print(f"Error scanning source chain: {e}")
            return 0

    # Process destination chain (BSC) - with block-by-block scanning to avoid rate limits
    elif chain == 'destination':
        print(f"Scanning blocks {start_block_dest} to {current_block_dest} on destination chain")
        unwrap_topic = w3_dest.keccak(text="Unwrap(address,address,uint256)").hex()
        
        # Scan blocks one by one to avoid overwhelming the RPC
        found_events = 0
        for block_num in range(start_block_dest, current_block_dest + 1):
            try:
                print(f"Scanning block {block_num} for unwrap events")
                
                # Wait between block scans to avoid rate limits
                if block_num > start_block_dest:
                    time.sleep(3)
                
                # Get logs with a specific topic filter for the Unwrap event
                unwrap_events = retry_rpc_call(
                    w3_dest.eth.get_logs,
                    {
                        'fromBlock': block_num,
                        'toBlock': block_num,
                        'address': dest_address,
                        'topics': [unwrap_topic]
                    },
                    max_retries=3,
                    initial_wait=4
                )
                
                if unwrap_events:
                    found_events += len(unwrap_events)
                    print(f"Found {len(unwrap_events)} Unwrap event(s) in block {block_num}")
                    
                    for event in unwrap_events:
                        try:
                            # Check if we've already processed this event
                            tx_hash = event['transactionHash'].hex()
                            if tx_hash in processed_events:
                                print(f"Skipping already processed unwrap: {tx_hash}")
                                continue
                            
                            # Parse the event
                            parsed_event = dest_contract.events.Unwrap().process_log(event)
                            token = parsed_event.args.underlying_token
                            recipient = parsed_event.args.to
                            amount = parsed_event.args.amount
                            
                            print(f"Processing Unwrap: Token: {token}, Recipient: {recipient}, Amount: {amount}")
                            
                            # Process the withdraw with enhanced error handling
                            withdraw_tx_hash = handle_withdraw_transaction(
                                w3_source, source_contract, token, recipient, amount, warden_address, warden_key
                            )
                            
                            if withdraw_tx_hash:
                                # Mark as processed if successful
                                processed_events.add(tx_hash)
                                save_processed_events()
                                
                        except Exception as e:
                            print(f"Error processing unwrap event: {e}")
                            continue
                
            except Exception as block_e:
                print(f"Error scanning block {block_num}: {block_e}")
                # Continue to next block rather than completely failing
                continue
        
        print(f"Completed destination chain scan, found {found_events} total unwrap events")
        
        # If no events found through normal scanning, try direct approach
        if found_events == 0:
            try:
                print("No events found. Trying direct approach for most recent block...")
                # Try the most recent confirmed block
                last_block = current_block_dest - 1
                
                # Add significant delay to avoid rate limits
                time.sleep(5)
                
                unwrap_events = retry_rpc_call(
                    w3_dest.eth.get_logs,
                    {
                        'fromBlock': last_block,
                        'toBlock': last_block,
                        'address': dest_address,
                        'topics': [unwrap_topic]
                    },
                    max_retries=2,
                    initial_wait=5
                )
                
                print(f"Found {len(unwrap_events)} Unwrap events in block {last_block}")
                
                for event in unwrap_events:
                    try:
                        # Check if already processed
                        tx_hash = event['transactionHash'].hex()
                        if tx_hash in processed_events:
                            print(f"Skipping already processed unwrap: {tx_hash}")
                            continue
                        
                        # Parse the event
                        parsed_event = dest_contract.events.Unwrap().process_log(event)
                        token = parsed_event.args.underlying_token
                        recipient = parsed_event.args.to
                        amount = parsed_event.args.amount
                        
                        print(f"Processing Unwrap: Token: {token}, Recipient: {recipient}, Amount: {amount}")
                        
                        # Process the withdraw
                        withdraw_tx_hash = handle_withdraw_transaction(
                            w3_source, source_contract, token, recipient, amount, warden_address, warden_key
                        )
                        
                        if withdraw_tx_hash:
                            processed_events.add(tx_hash)
                            save_processed_events()
                        
                    except Exception as e:
                        print(f"Error processing unwrap event in fallback approach: {e}")
                        continue
                
            except Exception as fallback_e:
                print(f"Fallback approach also failed: {fallback_e}")

    return 1

def handle_wrap_transaction(w3, contract, token, recipient, amount, sender_address, sender_key):
    """Handle wrap transaction with improved error handling and nonce management"""
    max_attempts = 3
    for attempt in range(max_attempts):
        try:
            # Get fresh nonce for each attempt with retry
            nonce = retry_rpc_call(w3.eth.get_transaction_count, sender_address)
            
            # Add the attempt number to the nonce to avoid "nonce too low" errors on retries
            adjusted_nonce = nonce + attempt
            
            print(f"Building wrap transaction (attempt {attempt+1}/{max_attempts}, nonce: {adjusted_nonce})")
            
            # Get current gas price with retry 
            gas_price = retry_rpc_call(w3.eth.gas_price)
            
            # Increase gas price slightly to prioritize transaction
            gas_price = int(gas_price * 1.1)
            
            # Build transaction
            wrap_tx = contract.functions.wrap(
                token,
                recipient,
                amount
            ).build_transaction({
                'from': sender_address,
                'gas': 300000,  # Increased gas limit for more safety
                'gasPrice': gas_price,
                'nonce': adjusted_nonce,
            })
            
            # Sign and send
            signed_tx = w3.eth.account.sign_transaction(wrap_tx, sender_key)
            tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
            tx_hash_hex = tx_hash.hex()
            
            print(f"Sent wrap transaction: {tx_hash_hex}")
            
            # Wait for receipt with timeout
            receipt = wait_for_transaction_with_timeout(w3, tx_hash, timeout=60)
            
            if receipt and receipt.status == 1:
                print(f"Wrap transaction succeeded: {tx_hash_hex}")
                return tx_hash_hex
            else:
                print(f"Wrap transaction failed or timed out: {tx_hash_hex}")
                # Continue to next attempt
                time.sleep(5)  # Wait before retry
        
        except Exception as e:
            error_msg = str(e)
            if 'nonce too low' in error_msg and attempt < max_attempts - 1:
                print(f"Nonce too low. Will retry with incremented nonce")
                time.sleep(2)  # Short wait before retrying with new nonce
                continue
            elif 'already known' in error_msg:
                # Transaction is already submitted, wait for it
                print(f"Transaction already submitted. Waiting for confirmation...")
                try:
                    receipt = wait_for_transaction_with_timeout(w3, Web3.to_bytes(hexstr=wrap_tx['hash']), timeout=60)
                    if receipt and receipt.status == 1:
                        print(f"Previously submitted wrap transaction succeeded")
                        return wrap_tx['hash']
                except:
                    pass
            else:
                print(f"Error in wrap transaction (attempt {attempt+1}/{max_attempts}): {e}")
                if attempt < max_attempts - 1:
                    # Wait longer between retries
                    wait_time = 5 * (attempt + 1)
                    print(f"Retrying in {wait_time} seconds...")
                    time.sleep(wait_time)
    
    print("All wrap transaction attempts failed")
    return None

def handle_withdraw_transaction(w3, contract, token, recipient, amount, sender_address, sender_key):
    """Handle withdraw transaction with improved error handling and nonce management"""
    max_attempts = 3
    for attempt in range(max_attempts):
        try:
            # Get fresh nonce for each attempt with retry
            nonce = retry_rpc_call(w3.eth.get_transaction_count, sender_address)
            
            # Add the attempt number to the nonce to avoid "nonce too low" errors on retries
            adjusted_nonce = nonce + attempt
            
            print(f"Building withdraw transaction (attempt {attempt+1}/{max_attempts}, nonce: {adjusted_nonce})")
            
            # Get current gas price with retry and increase by 20% to prioritize transaction
            gas_price = int(retry_rpc_call(w3.eth.gas_price) * 1.2)
            
            # Build transaction with higher gas limit for safety
            withdraw_tx = contract.functions.withdraw(
                token,
                recipient,
                amount
            ).build_transaction({
                'from': sender_address,
                'gas': 300000,  # Increased gas limit
                'gasPrice': gas_price,
                'nonce': adjusted_nonce,
            })
            
            # Sign and send transaction
            signed_tx = w3.eth.account.sign_transaction(withdraw_tx, sender_key)
            tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
            tx_hash_hex = tx_hash.hex()
            
            print(f"Sent withdraw transaction: {tx_hash_hex}")
            
            # Wait for receipt with timeout
            receipt = wait_for_transaction_with_timeout(w3, tx_hash, timeout=60)
            
            if receipt and receipt.status == 1:
                print(f"Withdraw transaction succeeded: {tx_hash_hex}")
                return tx_hash_hex
            else:
                print(f"Withdraw transaction failed or timed out: {tx_hash_hex}")
                # Continue to next attempt
                time.sleep(5)  # Wait before retry
        
        except Exception as e:
            error_msg = str(e)
            if 'nonce too low' in error_msg and attempt < max_attempts - 1:
                print(f"Nonce too low. Will retry with incremented nonce")
                time.sleep(2)
                continue
            elif 'already known' in error_msg:
                # Transaction is already submitted, wait for it
                print(f"Transaction already submitted. Waiting for confirmation...")
                try:
                    receipt = wait_for_transaction_with_timeout(w3, Web3.to_bytes(hexstr=withdraw_tx['hash']), timeout=60)
                    if receipt and receipt.status == 1:
                        print(f"Previously submitted withdraw transaction succeeded")
                        return withdraw_tx['hash']
                except:
                    pass
            else:
                print(f"Error in withdraw transaction (attempt {attempt+1}/{max_attempts}): {e}")
                if attempt < max_attempts - 1:
                    # Wait longer between retries
                    wait_time = 5 * (attempt + 1)
                    print(f"Retrying in {wait_time} seconds...")
                    time.sleep(wait_time)
    
    print("All withdraw transaction attempts failed")
    return None

def wait_for_transaction_with_timeout(w3, tx_hash, timeout=60, poll_interval=3):
    """Wait for a transaction receipt with timeout"""
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            receipt = w3.eth.get_transaction_receipt(tx_hash)
            if receipt is not None:
                return receipt
        except Exception as e:
            print(f"Error getting receipt: {e}")
        
        # Wait before polling again
        time.sleep(poll_interval)
    
    print(f"Transaction {tx_hash.hex()} timed out after {timeout} seconds")
    return None

def register_tokens(contract_info="contract_info.json", token_csv="erc20s.csv"):
    """Enhanced token registration with improved error handling"""
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
            next(f)  # Skip header
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
            
            # Register on source chain
            register_source_token(w3_source, source_contract, source_token, warden_address, warden_key)
            
            # Wait between transactions
            time.sleep(10)
            
            # Create token on destination chain
            create_dest_token(w3_dest, dest_contract, source_token, name, symbol, warden_address, warden_key)
            
            # Wait longer between pairs
            time.sleep(15)
                
    except Exception as e:
        print(f"Error in token registration: {e}")

def register_source_token(w3, contract, token, sender_address, sender_key):
    """Register token on source chain with retries"""
    max_attempts = 3
    for attempt in range(max_attempts):
        try:
            print(f"Registering token {token} on source chain (attempt {attempt+1}/{max_attempts})...")
            
            # Get fresh nonce 
            nonce = w3.eth.get_transaction_count(sender_address)
            adjusted_nonce = nonce + attempt
            
            # Build transaction
            register_tx = contract.functions.registerToken(
                Web3.to_checksum_address(token)
            ).build_transaction({
                'from': sender_address,
                'gas': 200000,
                'gasPrice': w3.eth.gas_price,
                'nonce': adjusted_nonce,
            })
            
            # Sign and send
            signed_tx = w3.eth.account.sign_transaction(register_tx, sender_key)
            tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
            
            print(f"Sent register token transaction: {tx_hash.hex()}")
            
            # Wait for receipt
            receipt = wait_for_transaction_with_timeout(w3, tx_hash, timeout=60)
            
            if receipt and receipt.status == 1:
                print(f"Token {token} registered successfully")
                return True
            else:
                print(f"Token registration failed or timed out")
                if attempt < max_attempts - 1:
                    time.sleep(5)
        except Exception as e:
            error_msg = str(e)
            if 'nonce too low' in error_msg and attempt < max_attempts - 1:
                print(f"Nonce too low. Retrying with incremented nonce...")
                continue
            else:
                print(f"Error registering token {token}: {e}")
                if attempt < max_attempts - 1:
                    wait_time = 5 * (attempt + 1)
                    print(f"Retrying in {wait_time} seconds...")
                    time.sleep(wait_time)
    
    print(f"Failed to register token {token} after {max_attempts} attempts")
    return False

def create_dest_token(w3, contract, source_token, name, symbol, sender_address, sender_key):
    """Create token on destination chain with retries"""
    max_attempts = 3
    for attempt in range(max_attempts):
        try:
            print(f"Creating wrapped token for {source_token} on destination chain (attempt {attempt+1}/{max_attempts})...")
            
            # Get fresh nonce
            nonce = w3.eth.get_transaction_count(sender_address)
            adjusted_nonce = nonce + attempt
            
            # Build transaction
            create_tx = contract.functions.createToken(
                Web3.to_checksum_address(source_token),
                name,
                symbol
            ).build_transaction({
                'from': sender_address,
                'gas': 300000,  # Increased gas limit
                'gasPrice': w3.eth.gas_price,
                'nonce': adjusted_nonce,
            })
            
            # Sign and send
            signed_tx = w3.eth.account.sign_transaction(create_tx, sender_key)
            tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
            
            print(f"Sent create token transaction: {tx_hash.hex()}")
            
            # Wait for receipt
            receipt = wait_for_transaction_with_timeout(w3, tx_hash, timeout=60)
            
            if receipt and receipt.status == 1:
                print(f"Wrapped token for {source_token} created successfully")
                return True
            else:
                print(f"Token creation failed or timed out")
                if attempt < max_attempts - 1:
                    time.sleep(5)
        except Exception as e:
            error_msg = str(e)
            if 'nonce too low' in error_msg and attempt < max_attempts - 1:
                print(f"Nonce too low. Retrying with incremented nonce...")
                continue
            else:
                print(f"Error creating wrapped token for {source_token}: {e}")
                if attempt < max_attempts - 1:
                    wait_time = 5 * (attempt + 1)
                    print(f"Retrying in {wait_time} seconds...")
                    time.sleep(wait_time)
    
    print(f"Failed to create wrapped token for {source_token} after {max_attempts} attempts")
    return False

def create_missing_tokens():
    """Create missing tokens with enhanced error handling"""
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

    # Process tokens one by one with proper spacing
    for i, token in enumerate(tokens_to_create):
        # Add delay before processing tokens after the first one
        if i > 0:
            time.sleep(15)
        
        create_dest_token(w3_dest, dest_contract, token, name, symbol, warden_address, warden_key)

if __name__ == "__main__":
    try:
        # Uncomment these if needed
        # register_tokens()
        # create_missing_tokens()
        
        # Run the scanning operations with delay between them
        print("\n=== SCANNING SOURCE CHAIN ===")
        scan_blocks('source')
        
        # Add delay between chain operations
        print("\nWaiting 15 seconds before scanning destination chain...")
        time.sleep(15)
        
        print("\n=== SCANNING DESTINATION CHAIN ===")
        scan_blocks('destination')
        
    except Exception as e:
        print(f"Critical error in main execution: {e}")
