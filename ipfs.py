import requests
import json

PINATA_API_KEY = "61a02e213c1dec4cc7a8"
PINATA_SECRET_API_KEY = "1602171c441108ca06003f2369014294c0ebdc90a6f34aa849aa13614e215ff0"

def pin_to_ipfs(data):
	assert isinstance(data,dict), f"Error pin_to_ipfs expects a dictionary"
	#YOUR CODE HERE

	url = "https://api.pinata.cloud/pinning/pinJSONToIPFS"
	headers = {"Content-Type": "application/json","pinata_api_key": PINATA_API_KEY,"pinata_secret_api_key": PINATA_SECRET_API_KEY}
	payload = {"pinataContent": data}
	response = requests.post(url, headers=headers, json=payload)
	response.raise_for_status()
	result = response.json()
	cid = result.get("IpfsHash")
	return cid

def get_from_ipfs(cid,content_type="json"):
	assert isinstance(cid,str), f"get_from_ipfs accepts a cid in the form of a string"
	#YOUR CODE HERE	

	url = f"https://gateway.pinata.cloud/ipfs/{cid}"
	response = requests.get(url)
	response.raise_for_status()
	data = response.json()

	assert isinstance(data,dict), f"get_from_ipfs should return a dict"
	return data
