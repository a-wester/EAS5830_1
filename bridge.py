from web3 import Web3
from web3.providers.rpc import HTTPProvider
from web3.middleware import ExtraDataToPOAMiddleware #Necessary for POA chains
from datetime import datetime
import json
import pandas as pd


def connect_to(chain):
    if chain == 'source':  # The source contract chain is avax
        api_url = f"https://api.avax-test.network/ext/bc/C/rpc" #AVAX C-chain testnet

    if chain == 'destination':  # The destination contract chain is bsc
        api_url = f"https://data-seed-prebsc-1-s1.binance.org:8545/" #BSC testnet

    if chain in ['source','destination']:
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
        with open(contract_info, 'r')  as f:
            contracts = json.load(f)
    except Exception as e:
        print( f"Failed to read contract info\nPlease contact your instructor\n{e}" )
        return 0
    return contracts[chain]



def scan_blocks(chain, contract_info="contract_info.json"):
    """
        chain - (string) should be either "source" or "destination"
        Scan the last 5 blocks of the source and destination chains
        Look for 'Deposit' events on the source chain and 'Unwrap' events on the destination chain
        When Deposit events are found on the source chain, call the 'wrap' function the destination chain
        When Unwrap events are found on the destination chain, call the 'withdraw' function on the source chain
    """

    # This is different from Bridge IV where chain was "avax" or "bsc"
    if chain not in ['source','destination']:
        print( f"Invalid chain: {chain}" )
        return 0
    
        #YOUR CODE HERE
    
    # Load contract information
    with open(contract_info, 'r') as f:
        info = json.load(f)
    
    # Connect to both chains
    w3_source = connect_to('source')
    w3_dest = connect_to('destination')
    
    # Get contract addresses
    source_address = Web3.to_checksum_address(info["source"]["address"])
    dest_address = Web3.to_checksum_address(info["destination"]["address"])
    
    # Get warden info
    warden_address = Web3.to_checksum_address(info["warden_address"])
    warden_key = info["warden_key"]
    
    # Initialize contracts
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
    
    # Calculate starting blocks (last 5 blocks)
    start_block_source = max(0, current_block_source - 5)
    start_block_dest = max(0, current_block_dest - 5)
    
    if chain == 'source':
        # Scan for Deposit events on the source chain
        print(f"Scanning blocks {start_block_source} to {current_block_source} on source chain")
        
        try:
            # Get all Deposit events from the last 5 blocks
            deposit_events = source_contract.events.Deposit.get_logs(
                fromBlock=start_block_source,
                toBlock=current_block_source
            )
            
            print(f"Found {len(deposit_events)} Deposit events")
            
            # Process each Deposit event
            for event in deposit_events:
                token = event.args.token
                recipient = event.args.recipient
                amount = event.args.amount
                
                print(f"Found Deposit: Token: {token}, Recipient: {recipient}, Amount: {amount}")
                
                # Call the wrap function on the destination chain
                nonce = w3_dest.eth.get_transaction_count(warden_address)
                
                # Build the transaction
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
                
                # Sign and send the transaction
                signed_tx = w3_dest.eth.account.sign_transaction(wrap_tx, warden_key)
                tx_hash = w3_dest.eth.send_raw_transaction(signed_tx.rawTransaction)
                
                print(f"Sent wrap transaction: {tx_hash.hex()}")
                
                # Wait for the transaction to be mined
                receipt = w3_dest.eth.wait_for_transaction_receipt(tx_hash)
                if receipt.status == 1:
                    print("Wrap transaction succeeded")
                else:
                    print("Wrap transaction failed")
                
        except Exception as e:
            print(f"Error processing Deposit events: {e}")
    
    elif chain == 'destination':
        # Scan for Unwrap events on the destination chain
        print(f"Scanning blocks {start_block_dest} to {current_block_dest} on destination chain")
        
        try:
            # Get all Unwrap events from the last 5 blocks
            unwrap_events = dest_contract.events.Unwrap.get_logs(
                fromBlock=start_block_dest,
                toBlock=current_block_dest
            )
            
            print(f"Found {len(unwrap_events)} Unwrap events")
            
            # Process each Unwrap event
            for event in unwrap_events:
                token = event.args.underlying_token
                recipient = event.args.to  # The recipient is in the 'to' field
                amount = event.args.amount
                
                print(f"Found Unwrap: Token: {token}, Recipient: {recipient}, Amount: {amount}")
                
                # Call the withdraw function on the source chain
                nonce = w3_source.eth.get_transaction_count(warden_address)
                
                # Build the transaction
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
                
                # Sign and send the transaction
                signed_tx = w3_source.eth.account.sign_transaction(withdraw_tx, warden_key)
                tx_hash = w3_source.eth.send_raw_transaction(signed_tx.rawTransaction)
                
                print(f"Sent withdraw transaction: {tx_hash.hex()}")
                
                # Wait for the transaction to be mined
                receipt = w3_source.eth.wait_for_transaction_receipt(tx_hash)
                if receipt.status == 1:
                    print("Withdraw transaction succeeded")
                else:
                    print("Withdraw transaction failed")
                
        except Exception as e:
            print(f"Error processing Unwrap events: {e}")
    
    return 1


def register_tokens(contract_info="contract_info.json", token_csv="erc20s.csv"):
    """
    Register tokens on both chains using the token addresses from the CSV file
    """
    # Load contract information from contract_info.json
    with open(contract_info, 'r') as f:
        info = json.load(f)
    
    # Connect to both chains
    w3_source = connect_to('source')
    w3_dest = connect_to('destination')
    
    # Get contract addresses
    source_address = Web3.to_checksum_address(info["source"]["address"])
    dest_address = Web3.to_checksum_address(info["destination"]["address"])
    
    # Get warden info
    warden_address = Web3.to_checksum_address(info["warden_address"])
    warden_key = info["warden_key"]
    
    # Initialize contracts
    source_contract = w3_source.eth.contract(
        address=source_address,
        abi=info["source"]["abi"]  # Use the ABI from contract_info.json
    )
    
    dest_contract = w3_dest.eth.contract(
        address=dest_address,
        abi=info["destination"]["abi"]  # Use the ABI from contract_info.json
    )
    
    # Read the token CSV file
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
        
        # Process tokens in pairs
        for i in range(min(len(source_tokens), len(dest_tokens))):
            source_token = source_tokens[i]
            dest_token = dest_tokens[i]
            
            # Create simple name and symbol for the token
            name = "Wrapped Token"
            symbol = "WTKN"
            
            try:
                # Register token on source chain
                print(f"Registering token {source_token} on source chain...")
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
                tx_hash = w3_source.eth.send_raw_transaction(signed_tx.rawTransaction)
                
                print(f"Sent register token transaction: {tx_hash.hex()}")
                receipt = w3_source.eth.wait_for_transaction_receipt(tx_hash)
                
                # Create token on destination chain
                print(f"Creating wrapped token for {source_token} on destination chain...")
                nonce = w3_dest.eth.get_transaction_count(warden_address)
                
                create_tx = dest_contract.functions.createToken(
                    Web3.to_checksum_address(source_token),
                    name,
                    symbol
                ).build_transaction({
                    'from': warden_address,
                    'gas': 300000,
                    'gasPrice': w3_dest.eth.gas_price,
                    'nonce': nonce,
                })
                
                signed_tx = w3_dest.eth.account.sign_transaction(create_tx, warden_key)
                tx_hash = w3_dest.eth.send_raw_transaction(signed_tx.rawTransaction)
                
                print(f"Sent create token transaction: {tx_hash.hex()}")
                receipt = w3_dest.eth.wait_for_transaction_receipt(tx_hash)
                
            except Exception as e:
                print(f"Error registering/creating token {source_token}: {e}")
                # Continue with the next token
    
    except Exception as e:
        print(f"Error reading token CSV file: {e}")
