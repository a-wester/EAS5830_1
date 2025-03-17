import requests
import json

def pin_to_ipfs(data):
	assert isinstance(data,dict), f"Error pin_to_ipfs expects a dictionary"
	#YOUR CODE HERE

	url = "https://ipfs.infura.io:5001/api/v0/add"

	files = {
			"file": ("data.json", json.dumps(data))
	}
	
	response = requests.post(url, files=files)
	response.raise_for_status()
	result = response.json()
	cid = result.get("Hash")
	
	return cid

def get_from_ipfs(cid,content_type="json"):
	assert isinstance(cid,str), f"get_from_ipfs accepts a cid in the form of a string"
	#YOUR CODE HERE	

	url = f"https://ipfs.infura.io:5001/api/v0/cat?arg={cid}"
	
	response = requests.post(url)
	response.raise_for_status()
	data = json.loads(response.text)

	assert isinstance(data,dict), f"get_from_ipfs should return a dict"
	return data
