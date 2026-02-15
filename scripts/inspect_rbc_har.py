import json
import urllib.parse

har_path = r"p:\dev\LifeOS\family_budget\ledger-fetch\debug\www1.royalbank.com.har"

try:
    with open(har_path, 'r', encoding='utf-8') as f:
        har_data = json.load(f)
except FileNotFoundError:
    print(f"File not found: {har_path}")
    exit(1)

print(f"Loaded HAR file. Log version: {har_data['log']['version']}")

entries = har_data['log']['entries']
print(f"Total entries: {len(entries)}")

transaction_entries = []

for entry in entries:
    url = entry['request']['url']
    if 'transaction-presentation-service' in url and 'transactions' in url:
        transaction_entries.append(entry)

print(f"Found {len(transaction_entries)} transaction related entries.")

for i, entry in enumerate(transaction_entries):
    url = entry['request']['url']
    print(f"\n--- Entry {i+1} ---")
    print(f"URL: {url}")
    
    response = entry['response']
    content = response['content']
    mime_type = content.get('mimeType')
    print(f"Mime Type: {mime_type}")
    
    text = content.get('text')
    if not text:
        print("No content text found.")
        continue
        
    try:
        data = json.loads(text)
        
        # Check for transaction keys as per rbc.py
        txns = data.get('transactionList') or data.get('transactions')
        
        if txns:
            print(f"Found {len(txns)} transactions in this response.")
            if len(txns) > 0:
                print("First transaction keys:", list(txns[0].keys()))
                print("Sample first transaction:", json.dumps(txns[0], indent=2))
        else:
            print("No 'transactionList' or 'transactions' key found in JSON.")
            print("Top level keys:", list(data.keys()))
            
    except json.JSONDecodeError:
        print("Could not decode JSON response.")
