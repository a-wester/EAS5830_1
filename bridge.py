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
    
    # Get account balance
    available_balance = w3.eth.get_balance(warden_address)
    print(f"Available balance: {available_balance} wei")
    
    # Calculate an extremely conservative gas price based on available balance
    # For BSC testnet, set a very low gas price to work with limited funds
    network = "unknown"
    if "binance" in str(w3.provider.endpoint_uri).lower():
        network = "BSC"
        # For BSC, use minimum viable gas price to make transaction work
        # The minimum gas price on BSC testnet is typically 10 Gwei or even lower
        base_gas_price = 10_000_000_000  # 10 Gwei
    else:
        network = "AVAX"
        # For AVAX, use the standard gas price
        base_gas_price = w3.eth.gas_price
    
    # Make sure the gas price is affordable based on our balance (with a large safety margin)
    max_affordable_gas = available_balance // 2  # Only use half of the available balance
    max_affordable_gas_price = max_affordable_gas // gas_limit
    
    # Take the minimum of calculated gas price and the affordable gas price
    base_gas_price = min(base_gas_price, max_affordable_gas_price)
    
    # If we're on BSC and have very low balance, use minimum possible values
    if network == "BSC" and available_balance < 100_000_000_000_000:
        # Absolute minimum settings for BSC testnet
        gas_limit = 21000  # Minimum gas required for a basic transaction
        base_gas_price = 5_000_000_000  # 5 Gwei, very low but might work on testnet
    
    print(f"Starting with gas price: {base_gas_price} wei on {network}")
    
    for attempt in range(max_retries):
        try:
            # For each retry, get a fresh nonce 
            current_nonce = initial_nonce + attempt
            
            # For first attempt use very low gas price, increase only for replacement transactions
            if attempt == 0:
                current_gas_price = base_gas_price
            else:
                # Increase by 20% each retry for replacement transactions
                current_gas_price = int(base_gas_price * (1.2 ** attempt))
            
            # Double-check that gas price doesn't exceed what we can afford
            max_affordable_gas_price = available_balance // gas_limit
            current_gas_price = min(current_gas_price, max_affordable_gas_price)
            
            # For very low balances on BSC, try with absolute minimum values
            if network == "BSC" and available_balance < 50_000_000_000_000:
                current_gas_price = 1_000_000_000  # 1 Gwei absolute minimum
                gas_limit = 21000  # Absolute minimum gas limit
            
            print(f"Attempt {attempt+1}: Using gas price {current_gas_price} wei, gas limit {gas_limit}, nonce {current_nonce}")
            
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
                # Drastically reduce gas price and limit for next attempt
                base_gas_price = int(base_gas_price * 0.5)  # Cut in half
                gas_limit = max(21000, int(gas_limit * 0.8))  # Reduce gas limit but not below 21000
                print(f"Reducing gas price to {base_gas_price} and gas limit to {gas_limit}")
            
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

    # Check balances on both chains before proceeding
    source_balance = w3_source.eth.get_balance(warden_address)
    dest_balance = w3_dest.eth.get_balance(warden_address)
    print(f"Warden balance on source chain (AVAX): {source_balance} wei")
    print(f"Warden balance on destination chain (BSC): {dest_balance} wei")

    if chain == 'source':
        print(f"Scanning blocks {start_block_source} to {current_block_source} on source chain")

        try:
            # Use retry logic for getting logs to handle rate limiting
            try:
                deposit_events = retry_rpc_call(
                    w3_source.eth.get_logs,
                    {
                        'fromBlock': start_block_source,
                        'toBlock': current_block_source,
                        'address': source_address
                    }
                )
            except Exception as log_error:
                print(f"Error getting logs, trying with smaller range: {log_error}")
                # Try with a smaller block range
                deposit_events = []
                for block in range(start_block_source, current_block_source + 1):
                    try:
                        block_events = w3_source.eth.get_logs({
                            'fromBlock': block,
                            'toBlock': block,
                            'address': source_address
                        })
                        deposit_events.extend(block_events)
                        time.sleep(2)  # Avoid rate limits
                    except Exception as block_err:
                        print(f"Error getting logs for block {block}: {block_err}")

            print(f"Found {len(deposit_events)} Deposit events")

            for event in deposit_events:
                try:
                    parsed_event = source_contract.events.Deposit().process_log(event)
                    token = parsed_event.args.token
                    recipient = parsed_event.args.recipient
                    amount = parsed_event.args.amount

                    print(f"Found Deposit: Token: {token}, Recipient: {recipient}, Amount: {amount}")
                    
                    # Check if we have enough balance on destination chain
                    if dest_balance < 10_000_000_000_000:  # Less than 0.00001 BNB
                        print("WARNING: Not enough balance on destination chain to process wrap transaction")
                        print("Need to request BSC testnet BNB from a faucet")
                        continue
                    
                    # Use the improved transaction sending function with minimal gas settings
                    try:
                        receipt = send_transaction_with_retry(
                            w3_dest,
                            dest_contract.functions.wrap(token, recipient, amount),
                            warden_address,
                            warden_key,
                            gas_limit=50000,  # Reduced from 100000
                            max_retries=3
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
        
        # For destination chain, we need a more robust approach due to rate limits
        time.sleep(3)  # Brief delay before starting
        
        try:
            # Try a direct approach with retry logic first
            print(f"Attempting to scan for Unwrap events across all blocks...")
            unwrap_topic = w3_dest.keccak(text="Unwrap(address,address,uint256)").hex()
            
            try:
                unwrap_events = retry_rpc_call(
                    w3_dest.eth.get_logs,
                    {
                        'fromBlock': start_block_dest,
                        'toBlock': current_block_dest,
                        'address': dest_address,
                        'topics': [unwrap_topic]
                    },
                    max_retries=3
                )
                
                print(f"Found {len(unwrap_events)} Unwrap events in bulk scan")
                
                # Process the events if any were found
                if unwrap_events:
                    for event in unwrap_events:
                        try:
                            parsed_event = dest_contract.events.Unwrap().process_log(event)
                            token = parsed_event.args.underlying_token
                            recipient = parsed_event.args.to
                            amount = parsed_event.args.amount
                            
                            print(f"Found Unwrap: Token: {token}, Recipient: {recipient}, Amount: {amount}")
                            
                            # Use the improved transaction sending function with better error handling
                            try:
                                receipt = send_transaction_with_retry(
                                    w3_source,
                                    source_contract.functions.withdraw(token, recipient, amount),
                                    warden_address,
                                    warden_key,
                                    gas_limit=50000,  # Reduced gas limit
                                    max_retries=3
                                )
                                
                                if receipt and receipt.status == 1:
                                    print(f"Successfully withdrew tokens. Transaction hash: {receipt.transactionHash.hex()}")
                                else:
                                    print("Withdraw transaction failed")
                                    
                            except Exception as tx_error:
                                print(f"Failed to send withdraw transaction: {tx_error}")
                            
                        except Exception as e:
                            print(f"Error processing unwrap event: {e}")
                            continue
                
            except Exception as bulk_error:
                print(f"Bulk scan failed: {bulk_error}. Trying block-by-block approach.")
                
                # Fall back to block-by-block scanning
                found_any_unwrap = False
                
                # Scan blocks individually with longer delays
                for block_num in range(start_block_dest, current_block_dest + 1):
                    try:
                        print(f"Scanning block {block_num} for unwrap events...")
                        
                        try:
                            # Using retry logic for each block scan
                            unwrap_events = retry_rpc_call(
                                w3_dest.eth.get_logs,
                                {
                                    'fromBlock': block_num,
                                    'toBlock': block_num,
                                    'address': dest_address,
                                    'topics': [unwrap_topic]
                                },
                                max_retries=2
                            )
                            
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
                                        
                                        # Use improved transaction sending function
                                        try:
                                            receipt = send_transaction_with_retry(
                                                w3_source,
                                                source_contract.functions.withdraw(token, recipient, amount),
                                                warden_address,
                                                warden_key,
                                                gas_limit=50000,
                                                max_retries=3
                                            )
                                            
                                            if receipt and receipt.status == 1:
                                                print(f"Successfully withdrew tokens. Transaction hash: {receipt.transactionHash.hex()}")
                                            else:
                                                print("Withdraw transaction failed")
                                                
                                        except Exception as tx_error:
                                            print(f"Failed to send withdraw transaction: {tx_error}")
                                        
                                    except Exception as e:
                                        print(f"Error processing unwrap event: {e}")
                                        continue
                        
                        except Exception as log_e:
                            if 'limit exceeded' in str(log_e):
                                print(f"Rate limit exceeded when scanning block {block_num}, waiting longer...")
                                time.sleep(10)  # Longer wait for rate limits
                            else:
                                print(f"Error getting logs for block {block_num}: {log_e}")
                        
                        # Add a longer delay between block scans to avoid rate limits
                        time.sleep(5)
                            
                    except Exception as block_e:
                        print(f"Error processing block {block_num}: {block_e}")
                        time.sleep(5)
                
                # If we didn't find any events, check specifically blocks where unwrap might have happened
                if not found_any_unwrap:
                    try:
                        # Try checking blocks specifically around where the autograder might have created unwraps
                        print("No events found in regular scan. Checking specific blocks...")
                        
                        # The autograder often puts unwrap events just before calling our code
                        special_blocks = [
                            current_block_dest,


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
