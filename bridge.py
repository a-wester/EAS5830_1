from web3 import Web3
from web3.providers.rpc import HTTPProvider
from web3.middleware import ExtraDataToPOAMiddleware  # Necessary for POA chains
from datetime import datetime
import json
import time
import random

# Warden credentials - make sure these match your deployed contracts
warden_address = "0x3b178a0a54730C2AAe0b327C77aF2d78F3Dca55B"
warden_key = "0xc72d903f58f9b5aecfc15ca6916720a88cc8b090e27ce9bb0db52bb0cd05c1d3"

def connect_to(chain):
    """
    Establishes connection to the specified blockchain
    """
    if chain == 'source':  # The source contract chain is avax
        api_url = "https://api.avax-test.network/ext/bc/C/rpc"  # AVAX C-chain testnet

    if chain == 'destination':  # The destination contract chain is bsc
        api_url = "https://data-seed-prebsc-1-s1.binance.org:8545/"  # BSC testnet

    if chain in ['source', 'destination']:
        w3 = Web3(Web3.HTTPProvider(api_url))
        # inject the poa compatibility middleware to the innermost layer
        w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
        
        # Add a retry middleware to handle rate limits
        from web3.middleware import geth_poa_middleware
        w3.middleware_onion.inject(geth_poa_middleware, layer=0)
        
        return w3
    return None

def get_contract_info(chain, contract_info="contract_info.json"):
    """
    Load the contract_info file into a dictionary
    """
    try:
        with open(contract_info, 'r') as f:
            contracts = json.load(f)
    except Exception as e:
        print(f"Failed to read contract info\nPlease contact your instructor\n{e}")
        return None
    return contracts[chain]

def scan_blocks(chain, contract_info="contract_info.json"):
    """
    Scan for events on the specified chain and trigger appropriate actions
    on the other chain
    """
    if chain not in ['source', 'destination']:
        print(f"Invalid chain: {chain}")
        return 0

    # Load contract information
    with open(contract_info, 'r') as f:
        info = json.load(f)

    # Connect to both chains
    w3_source = connect_to('source')
    w3_dest = connect_to('destination')

    # Set up contract addresses and ABIs
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

    # Get current block numbers
    current_block_source = w3_source.eth.block_number
    current_block_dest = w3_dest.eth.block_number

    # Define the blocks to scan
    start_block_source = max(0, current_block_source - 5)
    start_block_dest = max(0, current_block_dest - 5)

    if chain == 'source':
        print(f"Scanning blocks {start_block_source} to {current_block_source} on source chain")
        
        try:
            # Use create_filter approach instead of get_logs
            deposit_filter = source_contract.events.Deposit.create_filter(
                fromBlock=start_block_source,
                toBlock=current_block_source
            )
            deposit_events = deposit_filter.get_all_entries()
            
            print(f"Found {len(deposit_events)} Deposit events")

            for event in deposit_events:
                try:
                    # Extract event data
                    token = event.args.token
                    recipient = event.args.recipient
                    amount = event.args.amount

                    print(f"Found Deposit: Token: {token}, Recipient: {recipient}, Amount: {amount}")

                    # Check destination chain balance before attempting to send transaction
                    balance = w3_dest.eth.get_balance(warden_address)
                    gas_price = w3_dest.eth.gas_price
                    estimated_gas = 200000  # Reasonable estimate
                    estimated_cost = gas_price * estimated_gas
                    
                    if balance < estimated_cost:
                        print(f"WARNING: Insufficient balance on destination chain. Have: {balance}, Need: {estimated_cost}")
                        print("Skipping this event to avoid transaction failure.")
                        continue

                    # Get a fresh nonce for the transaction
                    nonce = w3_dest.eth.get_transaction_count(warden_address)
                    
                    # Build and send the transaction
                    wrap_tx = dest_contract.functions.wrap(
                        token,
                        recipient,
                        amount
                    ).build_transaction({
                        'from': warden_address,
                        'gas': 200000,
                        'gasPrice': w3_dest.eth.gas_price,
                        'nonce': nonce,
                    })

                    signed_tx = w3_dest.eth.account.sign_transaction(wrap_tx, warden_key)
                    tx_hash = w3_dest.eth.send_raw_transaction(signed_tx.raw_transaction)

                    print(f"Sent wrap transaction: {tx_hash.hex()}")

                    # Wait for transaction receipt with timeout
                    try:
                        receipt = w3_dest.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
                        if receipt.status == 1:
                            print("Wrap transaction succeeded")
                        else:
                            print("Wrap transaction failed")
                    except Exception as timeout_e:
                        print(f"Transaction may be pending: {timeout_e}")
                            
                except Exception as e:
                    print(f"Error processing deposit event: {e}")
                    # Add a delay to avoid rate limits
                    time.sleep(3)
                    continue

        except Exception as e:
            print(f"Error processing Deposit events: {e}")
            # Add a delay to avoid rate limits
            time.sleep(5)

    elif chain == 'destination':
        print(f"Scanning blocks {start_block_dest} to {current_block_dest} on destination chain")
        
        try:
            # Use create_filter approach instead of get_logs
            unwrap_filter = dest_contract.events.Unwrap.create_filter(
                fromBlock=start_block_dest,
                toBlock=current_block_dest
            )
            unwrap_events = unwrap_filter.get_all_entries()
            
            print(f"Found {len(unwrap_events)} Unwrap events")
            
            for event in unwrap_events:
                try:
                    # Extract event data
                    token = event.args.underlying_token
                    recipient = event.args.to
                    amount = event.args.amount
                    
                    print(f"Found Unwrap: Token: {token}, Recipient: {recipient}, Amount: {amount}")
                    
                    # Check source chain balance before attempting to send transaction
                    balance = w3_source.eth.get_balance(warden_address)
                    gas_price = w3_source.eth.gas_price
                    estimated_gas = 200000  # Reasonable estimate
                    estimated_cost = gas_price * estimated_gas
                    
                    if balance < estimated_cost:
                        print(f"WARNING: Insufficient balance on source chain. Have: {balance}, Need: {estimated_cost}")
                        print("Skipping this event to avoid transaction failure.")
                        continue
                    
                    # Get a fresh nonce
                    nonce = w3_source.eth.get_transaction_count(warden_address)
                    
                    # Build and send the transaction
                    withdraw_tx = source_contract.functions.withdraw(
                        token,
                        recipient,
                        amount
                    ).build_transaction({
                        'from': warden_address,
                        'gas': 200000,
                        'gasPrice': w3_source.eth.gas_price,
                        'nonce': nonce,
                    })
                    
                    signed_tx = w3_source.eth.account.sign_transaction(withdraw_tx, warden_key)
                    tx_hash = w3_source.eth.send_raw_transaction(signed_tx.raw_transaction)
                    
                    print(f"Sent withdraw transaction: {tx_hash.hex()}")
                    
                    # Wait for transaction receipt with timeout
                    try:
                        receipt = w3_source.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
                        if receipt.status == 1:
                            print("Withdraw transaction succeeded")
                        else:
                            print("Withdraw transaction failed")
                    except Exception as timeout_e:
                        print(f"Transaction may be pending: {timeout_e}")
                            
                except Exception as e:
                    print(f"Error processing unwrap event: {e}")
                    # Add a delay to avoid rate limits
                    time.sleep(3)
                    continue
                    
        except Exception as e:
            print(f"Error scanning destination chain: {e}")
            # Add a delay to avoid rate limits
            time.sleep(5)

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
            
            try:
                print(f"Registering token {source_token} on source chain...")
                # Check balance before registering
                balance = w3_source.eth.get_balance(warden_address)
                gas_price = w3_source.eth.gas_price
                estimated_gas = 200000
                estimated_cost = gas_price * estimated_gas
                
                if balance < estimated_cost:
                    print(f"WARNING: Insufficient balance on source chain. Have: {balance}, Need: {estimated_cost}")
                    print("Skipping token registration.")
                    continue
                
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
                w3_source.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
                
                # Check balance before creating token
                balance = w3_dest.eth.get_balance(warden_address)
                gas_price = w3_dest.eth.gas_price
                estimated_gas = 200000
                estimated_cost = gas_price * estimated_gas
                
                if balance < estimated_cost:
                    print(f"WARNING: Insufficient balance on destination chain. Have: {balance}, Need: {estimated_cost}")
                    print("Skipping token creation.")
                    continue
                
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
                w3_dest.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
                
                # Add a delay between token registrations to avoid rate limiting
                time.sleep(10)
                
            except Exception as e:
                print(f"Error registering/creating token {source_token}: {e}")
                time.sleep(5)
    
    except Exception as e:
        print(f"Error reading token CSV file: {e}")

def create_missing_tokens(contract_info="contract_info.json"):
    """
    Create any missing tokens on the destination chain
    """
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

    # Check balance before attempting to create tokens
    balance = w3_dest.eth.get_balance(warden_address)
    gas_price = w3_dest.eth.gas_price
    estimated_gas = 3000000
    estimated_cost = gas_price * estimated_gas
    
    if balance < estimated_cost:
        print(f"WARNING: Insufficient balance on destination chain. Have: {balance}, Need: {estimated_cost}")
        print("Cannot create tokens due to insufficient funds.")
        return

    # Create tokens one at a time with proper error handling
    for i, token in enumerate(tokens_to_create):
        try:
            # Get the current nonce
            nonce = w3_dest.eth.get_transaction_count(warden_address)
            print(f"Creating token {token} with nonce {nonce}")
            
            tx = dest_contract.functions.createToken(
                Web3.to_checksum_address(token),
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
            receipt = w3_dest.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
            print(f"Created token {token}: {tx_hash.hex()}")
        except Exception as e:
            print(f"Failed to create token {token}: {e}")
        
        # Always wait between transactions
        time.sleep(10)

if __name__ == "__main__":
    # Uncomment these if you need to register tokens or create missing tokens
    # register_tokens()
    # create_missing_tokens()
    
    # Run the scanning operations
    scan_blocks('source')
    
    # Add delay between chain operations
    time.sleep(10)
    
    scan_blocks('destination')
