import json

har_path = r"p:\dev\LifeOS\family_budget\ledger-fetch\debug\www1.royalbank.com.har"

try:
    with open(har_path, 'r', encoding='utf-8') as f:
        har_data = json.load(f)
except FileNotFoundError:
    print(f"File not found: {har_path}")
    exit(1)

entries = har_data['log']['entries']
largest_entry = None
max_size = 0

for entry in entries:
    url = entry['request']['url']
    text = entry['response']['content'].get('text', '')
    if len(text) > max_size:
        max_size = len(text)
        largest_entry = entry

if largest_entry:
    print(f"Analyzing largest response from: {largest_entry['request']['url']}")
    text = largest_entry['response']['content'].get('text', '')
    
    try:
        data = json.loads(text)
        
        print(f"Total Matches: {data.get('totalMatches')}")
        print(f"Total Results Returned: {data.get('totalResultsReturned')}")
        print(f"Offset Key: {data.get('offsetKey')}")
        
        txns = data.get('transactionList') or data.get('transactions', [])
        print(f"Transaction Count in List: {len(txns)}")
        
        # Check request query params
        req_url = largest_entry['request']['url']
        if '?' in req_url:
            query = req_url.split('?')[1]
            print(f"Request Query Params: {query}")
            
    except json.JSONDecodeError:
        print("Could not decode JSON.")
else:
    print("No content found.")
