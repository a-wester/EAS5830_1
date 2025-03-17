import json
from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware
from web3.providers.rpc import HTTPProvider

'''
If you use one of the suggested infrastructure providers, the url will be of the form
now_url  = f"https://eth.nownodes.io/{now_token}"
alchemy_url = f"https://eth-mainnet.alchemyapi.io/v2/{alchemy_token}"
infura_url = f"https://mainnet.infura.io/v3/{infura_token}"
'''

def connect_to_eth():
	url = "https://mainnet.infura.io/v3/83a171a395a14884b404624b177c433c"  # FILL THIS IN
	w3 = Web3(HTTPProvider(url))
	assert w3.is_connected(), f"Failed to connect to provider at {url}"
	return w3


def connect_with_middleware(contract_json):
	with open(contract_json, "r") as f:
		d = json.load(f)
		d = d['bsc']
		address = d['address']
		abi = d['abi']

	# TODO complete this method
	# The first section will be the same as "connect_to_eth()" but with a BNB url
	w3 = 0
	url = "wss://bsc-testnet.drpc.org"  # FILL THIS IN
	w3 = Web3(WebsocketProvider(url))
	assert w3.is_connected(), f"Failed to connect to provider at {url}"
	#return w3

	# The second section requires you to inject middleware into your w3 object and
	# create a contract object. Read more on the docs pages at https://web3py.readthedocs.io/en/stable/middleware.html
	# and https://web3py.readthedocs.io/en/stable/web3.contract.html
	contract = 0
	w3.middleware_onion.inject(geth_poa_middleware, layer=0)
	contract = w3.eth.contract(address=address, abi=abi)

	return w3, contract


if __name__ == "__main__":
	connect_to_eth()
