import json

har_path = r"p:\dev\LifeOS\family_budget\ledger-fetch\debug\www1.royalbank.com.har"

try:
    with open(har_path, 'r', encoding='utf-8') as f:
        har_data = json.load(f)
except FileNotFoundError:
    print(f"File not found: {har_path}")
    exit(1)

entries = har_data['log']['entries']
print(f"Total entries: {len(entries)}")

largest_entry = None
max_size = 0

for entry in entries:
    url = entry['request']['url']
    size = entry['response']['content'].get('size', 0)
    text = entry['response']['content'].get('text', '')
    
    # approximate size from text length if size is 0
    if size == 0 and text:
        size = len(text)
        
    print(f"URL: {url}, Size: {size}")
    
    if size > max_size:
        max_size = size
        largest_entry = entry

if largest_entry:
    print("\n--- Largest Response ---")
    print(f"URL: {largest_entry['request']['url']}")
    print(f"Size: {max_size}")
    text = largest_entry['response']['content'].get('text', '')
    print(f"Preview: {text[:500]}")
    
    try:
        data = json.loads(text)
        print("JSON Keys:", list(data.keys()))
        if 'transactionList' in data:
            print(f"Found {len(data['transactionList'])} transactions in transactionList.")
        if 'transactions' in data:
            print(f"Found {len(data['transactions'])} transactions in transactions.")
    except:
        print("Not valid JSON.")
else:
    print("No content found.")
