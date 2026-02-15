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

for i, entry in enumerate(entries):
    url = entry['request']['url']
    print(f"{i+1}: {url}")
    if i >= 50:
        print("... (truncated)")
        break
