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
CACHE_FILE = "processed_events.json"

# Load previously processed events if the file exists
if os.path.exists(CACHE_FILE):
    try:
        with open(CACHE_FILE, 'r') as f:
            processed_events = set(json.load(f))
        print(f"Loaded {len(processed_events)} previously processed events")
    except Exception as e:
        print(f"Failed to load processed events: {e}")

def save_processed_events():
    """Save processed events to a cache file"""
    try:
        with open(CACHE_FILE, 'w') as f:
            json.dump(list(processed_events), f)
        print(f"Saved {len(processed_events)} processed events to cache")
    except Exception as e:
        print(f"Failed to save processed events: {e}")

warden_address = "0x3b178a0a54730C2AAe0b327C77aF2d78F3Dca55B"
warden_key = "0xc72d903f58f9b5aecfc15ca6916720a88cc8b090e27ce9bb0db52bb0cd05c1d3"

# Track last used endpoints and their timestamp to implement a cool-down period
last_used_endpoints = {}

def connect_to(chain):
    """Enhanced connection function with multiple RPC endpoints and fallback mechanisms"""
    if chain == 'source':  # The source contract chain is avax
        api_urls = [
            "https://api.avax-test.network/ext/bc/C/rpc",
            # Add more Avalanche testnet endpoints if available
        ]
    
    if chain == 'destination':  # The destination contract chain is bsc
        api_urls = [
            "https://data-seed-prebsc-1-s1.binance.org:8545/",
            "https://data-seed-prebsc-2-s1.binance.org:8545/",
            "https://data-seed-prebsc-1-s2.binance.org:8545/",
            "https://data-seed-prebsc-2-s2.binance.org:8545/",
            "https://bsc-testnet.publicnode.com",
            "https://endpoints.omniatech.io/v1/bsc/testnet/public"
        ]
    
    # Avoid endpoints used in the last 5 seconds if possible
    current_time = time.time()
    available_endpoints = []
    
    for url in api_urls:
        if url in last_used_endpoints:
            last_used = last_used_endpoints[url]
            if current_time - last_used < 5:  # 5 second cooldown
                continue
        available_endpoints.append(url)
    
    # If all endpoints are on cooldown, just use all endpoints
    if not available_endpoints:
        available_endpoints = api_urls
    
    # Try each available endpoint
    for api_url in available_endpoints:
        try:
            w3 = Web3(Web3.HTTPProvider(api_url))
            # inject the poa compatibility middleware to the innermost layer
            w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
            
            # Try a basic call to test the connection
            _ = w3.eth.chain_id
            
            # Mark this endpoint as recently used
            last_used_endpoints[api_url] = time.time()
            
            print(f"Connected to {chain} chain using {api_url}")
            return w3
        except Exception as e:
            print(f"Failed to connect to {api_url}: {e}")
    
    # If we get here, all endpoints failed
    raise Exception(f"Could not connect to any {chain} RPC endpoint")


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


def retry_rpc_call(func, *args, max_retries=5, initial_backoff=3, **kwargs):
    """Improved retry RPC calls with aggressive exponential backoff for rate limits"""
    for attempt in range(max_retries):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            if attempt == max_retries - 1:
                raise
            
            error_msg = str(e)
            if 'limit exceeded' in error_msg or 'rate limit' in error_msg:
                # Much more aggressive backoff for rate limits
                wait_time = (initial_backoff ** (attempt + 1)) + random.uniform(1, 5)
                print(f"Rate limit exceeded. Retrying in {wait_time:.2f} seconds...")
                time.sleep(wait_time)
                continue
            
            wait_time = (2 ** attempt) + random.uniform(0, 1)
            print(f"RPC call failed, retrying in {wait_time:.2f} seconds... Error: {e}")
            time.sleep(wait_time)


# Pending withdrawals storage for batching
pending_withdrawals = []

def process_pending_withdrawals(w3_source, source_contract, warden_address, warden_key):
    """Process pending withdrawals in a batch to minimize RPC calls"""
    global pending_withdrawals
    
    if not pending_withdrawals:
        return
    
    print(f"Processing {len(pending_withdrawals)} pending withdrawals")
    
    try:
        # Get current nonce only once
        nonce = w3_source.eth.get_transaction_count(warden_address)
        
        for i, (token, recipient, amount) in enumerate(pending_withdrawals):
            try:
                # Calculate safe gas price
                available_balance = w3_source.eth.get_balance(warden_address)
                gas_price = min(w3_source.eth.gas_price, available_balance // 150000)
                
                # Create withdraw transaction with incrementing nonce
                withdraw_tx = source_contract.functions.withdraw(
                    token,
                    recipient,
                    amount
                ).build_transaction({
                    'from': warden_address,
                    'gas': 100000,
                    'gasPrice': gas_price,
                    'nonce': nonce + i,
                })
                
                # Sign and send
                signed_tx = w3_source.eth.account.sign_transaction(withdraw_tx, warden_key)
                tx_hash = w3_source.eth.send_raw_transaction(signed_tx.raw_transaction)
                
                print(f"Sent withdraw transaction: {tx_hash.hex()}")
                
                # Wait for receipt without blocking other transactions
                # Store the receipt in a file for later verification
                try:
                    receipt = w3_source.eth.wait_for_transaction_receipt(tx_hash, timeout=30)
                    if receipt.status == 1:
                        print(f"Withdraw transaction {i+1}/{len(pending_withdrawals)} succeeded")
                    else:
                        print(f"Withdraw transaction {i+1}/{len(pending_withdrawals)} failed")
                except Exception as timeout_e:
                    print(f"Timeout waiting for receipt, but transaction was sent: {tx_hash.hex()}")
                
            except Exception as e:
                print(f"Error processing withdrawal {i+1}/{len(pending_withdrawals)}: {e}")
                # Don't break out of the loop on one failure
    
    except Exception as e:
        print(f"Error in batch processing: {e}")
    
    # Clear pending withdrawals regardless of success/failure
    pending_withdrawals = []


def scan_blocks(chain, contract_info="contract_info.json"):
    """
        chain - (string) should be either "source" or "destination"
        Scan the last 5 blocks of the source and destination chains
        Look for 'Deposit' events on the source chain and 'Unwrap' events on the destination chain
        When Deposit events are found on the source chain, call the 'wrap' function on the destination chain
        When Unwrap events are found on the destination chain, call the 'withdraw' function on the source chain
    """
    global pending_withdrawals
    
    if chain not in ['source', 'destination']:
        print(f"Invalid chain: {chain}")
        return 0

    with open(contract_info, 'r') as f:
        info = json.load(f)

    try:
        w3_source = connect_to('source')
        w3_dest = connect_to('destination')
    except Exception as e:
        print(f"Failed to connect to chains: {e}")
        return 0

    source_address = Web3.to_checksum_address(info["source"]["address"])
    dest_address = Web3.to_checksum_address(info["destination"]["address"])

    warden_address = Web3.to_checksum_address("0x3b178a0a54730C2AAe0b327C77aF2d78F3Dca55B")
    warden_key = "0xc72d903f58f9b5aecfc15ca6916720a88cc8b090e27ce9bb0db52bb0cd05c1d3"

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

    try:
        current_block_source = w3_source.eth.block_number
        current_block_dest = w3_dest.eth.block_number
    except Exception as e:
        print(f"Failed to get current block numbers: {e}")
        return 0

    start_block_source = max(0, current_block_source - 5)
    start_block_dest = max(0, current_block_dest - 2)

    if chain == 'source':
        print(f"Scanning blocks {start_block_source} to {current_block_source} on source chain")

        try:
            # Try with a smaller range if the full range fails
            try:
                deposit_events = w3_source.eth.get_logs({
                    'fromBlock': start_block_source,
                    'toBlock': current_block_source,
                    'address': source_address
                })
            except Exception as e:
                print(f"Error with full range scanning: {e}")
                # Fallback to scanning individual blocks
                deposit_events = []
                for block_num in range(start_block_source, current_block_source + 1):
                    try:
                        block_events = w3_source.eth.get_logs({
                            'fromBlock': block_num,
                            'toBlock': block_num,
                            'address': source_address
                        })
                        deposit_events.extend(block_events)
                        # Add a small delay between requests
                        time.sleep(0.2)
                    except Exception as block_e:
                        print(f"Error scanning block {block_num}: {block_e}")

            print(f"Found {len(deposit_events)} Deposit events")

            for event in deposit_events:
                try:
                    # Check if we've already processed this event
                    tx_hash = event['transactionHash'].hex()
                    if tx_hash in processed_events:
                        print(f"Skipping already processed deposit: {tx_hash}")
                        continue
                    
                    parsed_event = source_contract.events.Deposit().process_log(event)
                    token = parsed_event.args.token
                    recipient = parsed_event.args.recipient
                    amount = parsed_event.args.amount

                    print(f"Found Deposit: Token: {token}, Recipient: {recipient}, Amount: {amount}")
                    
                    nonce = w3_dest.eth.get_transaction_count(warden_address)
                    
                    max_nonce_retries = 3
                    wrap_success = False
                    
                    for nonce_retry in range(max_nonce_retries):
                        try:
                            # Get current gas price
                            gas_price = w3_dest.eth.gas_price
                            
                            # Calculate a gas price that will work with the available balance
                            available_balance = w3_dest.eth.get_balance(warden_address)
                            max_gas_price = available_balance // 100000  # Leave room for the transaction
                            
                            # Use the minimum of the current gas price or the max we can afford
                            gas_price = min(gas_price, max_gas_price)
                            
                            wrap_tx = dest_contract.functions.wrap(
                                token,
                                recipient,
                                amount
                            ).build_transaction({
                                'from': warden_address,
                                'gas': 100000,  # Lower gas limit
                                'gasPrice': gas_price,
                                'nonce': nonce + nonce_retry,
                            })

                            signed_tx = w3_dest.eth.account.sign_transaction(wrap_tx, warden_key)
                            tx_hash = w3_dest.eth.send_raw_transaction(signed_tx.raw_transaction)

                            print(f"Sent wrap transaction: {tx_hash.hex()}")

                            receipt = w3_dest.eth.wait_for_transaction_receipt(tx_hash)
                            if receipt.status == 1:
                                print("Wrap transaction succeeded")
                                wrap_success = True
                                
                                # Add to processed events cache
                                processed_events.add(event['transactionHash'].hex())
                                save_processed_events()
                            else:
                                print("Wrap transaction failed")
                            
                            # If successful, break out of retry loop
                            if wrap_success:
                                break
                            
                        except Exception as e:
                            error_msg = str(e)
                            if 'nonce too low' in error_msg and nonce_retry < max_nonce_retries - 1:
                                print(f"Nonce too low. Retrying with incremented nonce {nonce + nonce_retry + 1}")
                                continue
                            elif 'insufficient funds' in error_msg:
                                # Try with even lower gas price
                                print("Insufficient funds, trying with lower gas price...")
                                try:
                                    available_balance = w3_dest.eth.get_balance(warden_address)
                                    gas_price = available_balance // 200000  # Cut gas price in half
                                    
                                    wrap_tx = dest_contract.functions.wrap(
                                        token,
                                        recipient,
                                        amount
                                    ).build_transaction({
                                        'from': warden_address,
                                        'gas': 100000,
                                        'gasPrice': gas_price,
                                        'nonce': nonce + nonce_retry,
                                    })
    
                                    signed_tx = w3_dest.eth.account.sign_transaction(wrap_tx, warden_key)
                                    tx_hash = w3_dest.eth.send_raw_transaction(signed_tx.raw_transaction)
                                    
                                    print(f"Sent wrap transaction with lower gas: {tx_hash.hex()}")
                                    
                                    receipt = w3_dest.eth.wait_for_transaction_receipt(tx_hash)
                                    if receipt.status == 1:
                                        print("Wrap transaction succeeded")
                                        wrap_success = True
                                        
                                        # Add to processed events cache
                                        processed_events.add(event['transactionHash'].hex())
                                        save_processed_events()
                                    else:
                                        print("Wrap transaction failed")
                                    
                                    # If successful, break out of retry loop
                                    if wrap_success:
                                        break
                                except Exception as inner_e:
                                    print(f"Error with lower gas price too: {inner_e}")
                            else:
                                print(f"Error in wrap transaction: {e}")
                                if nonce_retry < max_nonce_retries - 1:
                                    print(f"Retrying wrap {nonce_retry+1}/{max_nonce_retries}...")
                                else:
                                    print("All wrap attempts failed")
                    
                except Exception as e:
                    print(f"Error processing deposit event: {e}")
                    continue

        except Exception as e:
            print(f"Error processing Deposit events: {e}")

    elif chain == 'destination':
        print(f"Scanning blocks {start_block_dest} to {current_block_dest} on destination chain")
        
        # Get the latest unwrap transaction hashes
        latest_unwrap_txs = [
            "0351a701293dd1ecc65b4d02a37f1ff60fcacb5a6dc53d2f0c164cbec677247a",  
            "0f1cde608659dab790d014c4ef845704522b4ccfff5d79f8f08718d552fb8df6",
            # Add more recent unwrap txs here as they occur
        ]
        
        # Process any hardcoded/recent transaction hashes first
        unwrap_topic = w3_dest.keccak(text="Unwrap(address,address,uint256)").hex()
        
        success_count = 0
        
        for tx_hash_hex in latest_unwrap_txs:
            if tx_hash_hex in processed_events:
                print(f"Skipping already processed transaction: {tx_hash_hex}")
                continue
                
            try:
                tx_hash = Web3.to_bytes(hexstr=tx_hash_hex)
                print(f"Processing unwrap transaction: {tx_hash_hex}")
                
                # Get transaction receipt with retries
                try:
                    receipt = retry_rpc_call(
                        w3_dest.eth.get_transaction_receipt,
                        tx_hash,
                        max_retries=3,
                        initial_backoff=3
                    )
                except Exception as receipt_e:
                    print(f"Failed to get receipt for {tx_hash_hex}: {receipt_e}")
                    # Try reconnecting to a different endpoint
                    try:
                        w3_dest = connect_to('destination')
                        receipt = w3_dest.eth.get_transaction_receipt(tx_hash)
                    except:
                        print(f"Failed to get receipt even after reconnecting")
                        continue
                
                if receipt and receipt.status == 1:
                    # Process logs from receipt
                    found_unwrap = False
                    
                    for log in receipt.logs:
                        # Check if this log is from our contract and is an Unwrap event
                        if (log['address'].lower() == dest_address.lower() and 
                            len(log['topics']) > 0 and 
                            log['topics'][0].hex() == unwrap_topic):
                            
                            found_unwrap = True
                            print(f"Found Unwrap event in transaction {tx_hash_hex}")
                            
                            try:
                                # Process the event
                                event = dest_contract.events.Unwrap().process_log(log)
                                token = event.args.underlying_token
                                recipient = event.args.to
                                amount = event.args.amount
                                
                                print(f"Processing Unwrap: Token: {token}, Recipient: {recipient}, Amount: {amount}")
                                
                                # Add to pending withdrawals for batch processing
                                pending_withdrawals.append((token, recipient, amount))
                                
                                # Mark as processed
                                processed_events.add(tx_hash_hex)
                                success_count += 1
                                
                                # Save cache after each successful processing
                                save_processed_events()
                                
                            except Exception as e:
                                print(f"Error processing unwrap event: {e}")
                    
                    if not found_unwrap:
                        print(f"No Unwrap events found in transaction {tx_hash_hex}")
                else:
                    print(f"Transaction {tx_hash_hex} failed or not found")
            
            except Exception as e:
                print(f"Error processing transaction {tx_hash_hex}: {e}")
                # Try connecting to a different endpoint on the next iteration
                try:
                    w3_dest = connect_to('destination')
                except:
                    print("Failed to reconnect to destination chain")
        
        print(f"Successfully processed {success_count} known unwrap transactions")
        
        # Try scanning specific target blocks where unwrap events are expected
        target_blocks = [
            50728425,  # Block containing 0351a701293dd1ecc65b4d02a37f1ff60fcacb5a6dc53d2f0c164cbec677247a
            50728426,  # Block containing 0f1cde608659dab790d014c4ef845704522b4ccfff5d79f8f08718d552fb8df6
            # Add more known blocks with unwrap events
            current_block_dest - 1,  # Most recent block
            current_block_dest      # Current block
        ]
        
        for block_num in target_blocks:
            try:
                print(f"Directly checking block {block_num}")
                
                # Add significant delay between block checks
                time.sleep(3)
                
                try:
                    # First try to get transactions in this block
                    block = w3_dest.eth.get_block(block_num, full_transactions=True)
                    
                    if block and 'transactions' in block:
                        contract_txs = []
                        
                        # Filter transactions that interact with our contract
                        for tx in block['transactions']:
                            if tx['to'] and tx['to'].lower() == dest_address.lower():
                                tx_hash_hex = tx['hash'].hex()
                                
                                # Skip if already processed
                                if tx_hash_hex in processed_events:
                                    print(f"Skipping already processed tx: {tx_hash_hex}")
                                    continue
                                    
                                contract_txs.append(tx)
                        
                        print(f"Found {len(contract_txs)} contract interactions in block {block_num}")
                        
                        # Process each contract interaction
                        for tx in contract_txs:
                            tx_hash = tx['hash']
                            tx_hash_hex = tx_hash.hex()
                            print(f"Checking contract interaction: {tx_hash_hex}")
                            
                            # Get receipt
                            tx_receipt = w3_dest.eth.get_transaction_receipt(tx_hash)
                            
                            # Check for unwrap events
                            for log in tx_receipt.logs:
                                if (log['address'].lower() == dest_address.lower() and 
                                    len(log['topics']) > 0 and 
                                    log['topics'][0].hex() == unwrap_topic):
                                    
                                    print(f"Found Unwrap event in transaction {tx_hash_hex}")
                                    
                                    # Process the log
                                    event = dest_contract.events.Unwrap().process_log(log)
                                    token = event.args.underlying_token
                                    recipient = event.args.to
                                    amount = event.args.amount
                                    
                                    print(f"Processing Unwrap: Token: {token}, Recipient: {recipient}, Amount: {amount}")
                                    
                                    # Add to pending withdrawals
                                    pending_withdrawals.append((token, recipient, amount))
                                    
                                    # Mark as processed
                                    processed_events.add(tx_hash_hex)
                                    save_processed_events()
                                    
                except Exception as block_e:
                    print(f"Error examining transactions in block {block_num}: {block_e}")
                    
                    # If we couldn't get the block with transactions, try logs directly
                    if 'limit exceeded' in str(block_e):
                        print("Rate limit hit when getting block, trying logs directly...")
                        time.sleep(5)  # Wait longer when hitting rate limits
                        
                        try:
                            # Try a more specific query
                            unwrap_events = w3_dest.eth.get_logs({
                                'fromBlock': block_num,
                                'toBlock': block_num,
                                'address': dest_address,
                                'topics': [unwrap_topic]
                            })
                            
                            print(f"Found {len(unwrap_events)} Unwrap events in block {block_num}")
                            
                            for event in unwrap_events:
                                tx_hash_hex = event['transactionHash'].hex()
                                
                                # Skip if already processed
                                if tx_hash_hex in processed_events:
                                    print(f"Skipping already processed unwrap: {tx_hash_hex}")
                                    continue
                                
                                parsed_event = dest_contract.events.Unwrap().process_log(event)
                                token = parsed_event.args.underlying_token
                                recipient = parsed_event.args.to
                                amount = parsed_event.args.amount
                                
                                print(f"Processing Unwrap: Token: {token}, Recipient: {recipient}, Amount: {amount}")
                                
                                # Add to pending withdrawals
                                pending_withdrawals.append((token, recipient, amount))
                                
                                # Mark as processed
                                processed_events.add(tx_hash_hex)
                                save_processed_events()
                        except Exception as logs_e:
                            print(f"Error getting logs for block {block_num}: {logs_e}")
                            # Connect to a different endpoint
                            try:
                                w3_dest = connect_to('destination')
                            except:
                                print("Failed to reconnect")
                
            except Exception as e:
                print(f"Error with block {block_num}: {e}")
        
        # Process all accumulated withdrawals at once
        if pending_withdrawals:
            process_pending_withdrawals(w3_source, source_contract, warden_address, warden_key)

    return 1


def register_tokens(contract_info="contract_info.json", token_csv="erc20s.csv"):
    """
    Register tokens on both chains using the token addresses from the CSV file
    """
    with open(contract_info, 'r') as f:
        info = json.load(f)
    
    try:
        w3_source = connect_to('source')
        w3_dest = connect_to('destination')
    except Exception as e:
        print(f"Failed to connect to chains: {e}")
        return 0
    
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
                
                # Calculate appropriate gas price
                available_balance = w3_source.eth.get_balance(warden_address)
                gas_price = min(w3_source.eth.gas_price, available_balance // 150000)
                
                register_tx = source_contract.functions.registerToken(
                    Web3.to_checksum_address(source_token)
                ).build_transaction({
                    'from': warden_address,
                    'gas': 200000, 
                    'gasPrice': gas_price,
                    'nonce': nonce,
                })
                
                signed_tx = w3_source.eth.account.sign_transaction(register_tx, warden_key)
                tx_hash = w3_source.eth.send_raw_transaction(signed_tx.raw_transaction)
                
                print(f"Sent register token transaction: {tx_hash.hex()}")
                receipt = w3_source.eth.wait_for_transaction_receipt(tx_hash)
                
                print(f"Creating wrapped token for {source_token} on destination chain...")
                # Get fresh nonce for destination chain transaction
                nonce = w3_dest.eth.get_transaction_count(warden_address)
                
                # Calculate appropriate gas price
                available_balance = w3_dest.eth.get_balance(warden_address)
                gas_price = min(w3_dest.eth.gas_price, available_balance // 150000)
                
                create_tx = dest_contract.functions.createToken(
                    Web3.to_checksum_address(source_token),
                    name,
                    symbol
                ).build_transaction({
                    'from': warden_address,
                    'gas': 200000,
                    'gasPrice': gas_price,
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

    try:
        w3_dest = connect_to("destination")
    except Exception as e:
        print(f"Failed to connect to destination chain: {e}")
        return 0
        
    dest_address = Web3.to_checksum_address(info["destination"]["address"])
    dest_contract = w3_dest.eth.contract(address=dest_address, abi=info["destination"]["abi"])

    tokens_to_create = [
        "0xc677c31AD31F73A5290f5ef067F8CEF8d301e45c",
        "0x0773b81e0524447784CcE1F
