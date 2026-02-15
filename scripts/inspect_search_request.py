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
    if 'search/cc/posted' in url:
        largest_entry = entry
        break # Found the search request

if largest_entry:
    req = largest_entry['request']
    print(f"URL: {req['url']}")
    print(f"Method: {req['method']}")
    
    print("\n--- Headers ---")
    for h in req['headers']:
        if h['name'].lower() in ['content-type', 'accept', 'cookie']:
            print(f"{h['name']}: {h['value'][:50]}...")

    print("\n--- Query String ---")
    for q in req['queryString']:
        print(f"{q['name']}: {q['value']}")

    print("\n--- Post Data ---")
    if 'postData' in req:
        print(f"MimeType: {req['postData'].get('mimeType')}")
        print(f"Text: {req['postData'].get('text')}")
    else:
        print("No Post Data")
        
    print("\n--- Response --")
    text = largest_entry['response']['content'].get('text', '')
    try:
         data = json.loads(text)
         print(f"Additions: {json.dumps(data.get('additions'), indent=2)}")
         print(f"Offset Key in Response: {data.get('offsetKey')}")
    except:
         pass

else:
    print("No search requests found.")
