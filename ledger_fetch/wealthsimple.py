import json
import urllib.parse
import re
import time
import json
from datetime import datetime
from typing import List, Dict, Any
from .base import BankDownloader
from .utils import TransactionNormalizer

# Try to import ws_api, handle if missing
try:
    from ws_api import WealthsimpleAPI, WSAPISession
except ImportError:
    WealthsimpleAPI = None
    WSAPISession = None

class PlaywrightResponseAdapter:
    """Adapts a Playwright APIResponse to look like a requests.Response object."""
    def __init__(self, api_response):
        self._response = api_response
        self.status_code = api_response.status
        self.reason = api_response.status_text
        self.headers = api_response.headers
        self._text = api_response.text()
        self.content = api_response.body()

    @property
    def text(self):
        return self._text

    def json(self):
        return json.loads(self._text)

class WealthsimpleDownloader(BankDownloader):
    """
    Wealthsimple Transaction Downloader.
    
    This downloader leverages the `ws-api` library (if available) and the browser's
    authenticated session to fetch transactions directly from Wealthsimple's API.
    
    Workflow:
    1.  Interactive Login: The user logs in manually.
    2.  Session Hijacking: The script extracts the OAuth token, session ID, and 
        device ID from the browser's cookies and local storage.
    3.  API Initialization: It initializes a `WealthsimpleAPI` client using these credentials.
    4.  Data Fetching: It iterates through all accounts and fetches activities (transactions)
        using the API.
    """

    def get_bank_name(self) -> str:
        return "wealthsimple"

    def login(self):
        """Navigate to login page and wait for manual login."""
        if not WealthsimpleAPI:
            raise ImportError("ws-api library is required for Wealthsimple downloader.")

        print("Navigating to Wealthsimple login page...")
        self.page.goto("https://my.wealthsimple.com/app/login")
        
        print("Waiting for user to ensure logged in...")
        # Wait for a specific element that indicates login success
        # The dashboard usually has "Total value" or similar.
        try:
            self.page.wait_for_url("**/app/home**", timeout=300000) # 5 min timeout
            print("Login detected.")
        except Exception:
             print("Warning: Login timeout or URL not matched. Proceeding anyway.")

    def navigate_to_transactions(self):
        """Not needed for Wealthsimple as we use API."""
        pass

    def download_transactions(self) -> List[Dict[str, Any]]:
        """Fetch transactions via API."""
        print("Extracting tokens from browser session...")
        
        # Get OAuth token from cookies
        cookies = self.context.cookies()
        oauth_cookie = next((c for c in cookies if c["name"] == "_oauth2_access_v2"), None)
        
        if not oauth_cookie:
            raise Exception("Could not find '_oauth2_access_v2' cookie. Are you logged in?")

        # Decode and parse the OAuth token
        decoded_value = urllib.parse.unquote(oauth_cookie["value"])
        token_info = json.loads(decoded_value)
        
        # Extract session ID and device ID from localStorage
        local_storage = self.page.evaluate("() => JSON.stringify(localStorage)")
        local_storage_data = json.loads(local_storage)
        
        session_id = None
        wssdi = None
        
        # Find session ID
        session_id_key = next((k for k in local_storage_data.keys() if k.startswith("ab.storage.sessionId")), None)
        if session_id_key:
            try:
                val_json = json.loads(local_storage_data[session_id_key])
                session_id = val_json.get("v")
            except: pass

        # Find device ID
        device_id_key = next((k for k in local_storage_data.keys() if k.startswith("ab.storage.deviceId")), None)
        if device_id_key:
             try:
                val_json = json.loads(local_storage_data[device_id_key])
                wssdi = val_json.get("v")
             except: pass

        # Initialize ws-api session
        session = WSAPISession()
        session.token_info = token_info
        session.access_token = token_info.get("access_token")
        session.refresh_token = token_info.get("refresh_token")
        session.token_type = token_info.get("token_type")
        if session_id: session.session_id = session_id
        if wssdi: session.wssdi = wssdi

        print("Session initialized.")

        # Apply monkey patch
        self._setup_monkey_patch()
        
        # Initialize API
        ws = WealthsimpleAPI(session)
        
        # Fetch accounts
        print("Fetching accounts...")
        accounts = ws.get_accounts()
        print(f"Found {len(accounts)} accounts.")
        
        # Export accounts to CSV
        if accounts:
            import csv
            accounts_file = self.config.output_dir / self.get_bank_name() / "accounts.csv"
            self.config.output_dir.mkdir(parents=True, exist_ok=True)
            (self.config.output_dir / self.get_bank_name()).mkdir(parents=True, exist_ok=True)
            
            # Define columns
            fieldnames = [
                'Unique Account ID', 'Account Name', 'Account Number', 'Currency', 
                'Status', 'Type', 'Unified Type', 'Net Value', 'Net Deposits', 'Created At'
            ]
            
            clean_accounts = []
            for acc in accounts:
                # Extract nested financials safely
                financials = acc.get('financials', {})
                current_combined = financials.get('currentCombined', {})
                net_liquidation = current_combined.get('netLiquidationValue', {})
                net_deposits = current_combined.get('netDeposits', {})
                
                # Extract custodian account number (usually the first one)
                custodian_accounts = acc.get('custodianAccounts', [])
                account_number = ''
                if custodian_accounts and isinstance(custodian_accounts, list) and len(custodian_accounts) > 0:
                    account_number = custodian_accounts[0].get('id', '')

                clean_acc = {
                    'Unique Account ID': acc.get('id'),
                    'Account Name': acc.get('nickname'),
                    'Account Number': account_number,
                    'Currency': acc.get('currency'),
                    'Status': acc.get('status'),
                    'Type': acc.get('type'),
                    'Unified Type': acc.get('unifiedAccountType'),
                    'Net Value': net_liquidation.get('amount'),
                    'Net Deposits': net_deposits.get('amount'),
                    'Created At': acc.get('createdAt')
                }
                clean_accounts.append(clean_acc)
            
            with open(accounts_file, 'w', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(clean_accounts)
            print(f"Exported clean account details to {accounts_file}")
        
        all_transactions = []
        
        for account in accounts:
            account_id = account['id']
            account_name = account.get('nickname') or account.get('account_type') or account_id
            print(f"Processing account: {account_name} ({account_id})")
            
            try:
                activities = ws.get_activities(account_id)
                if isinstance(activities, dict) and 'results' in activities:
                    activities = activities['results']
                
                if not activities:
                    continue
                    
                print(f"  Found {len(activities)} transactions.")
                
                for activity in activities:
                    txn = self._process_activity(activity, account_name, account_id)
                    all_transactions.append(txn)
                    
            except Exception as e:
                print(f"  Error fetching transactions for account {account_id}: {e}")
                
        return all_transactions

    def _process_activity(self, activity, account_name, account_id):
        """
        Process a single activity into a transaction dict.
        
        Raw Activity Fields (Wealthsimple API):
        - id: str (e.g., "order-...")
        - canonicalId: str (e.g., "order-...")
        - occurredAt: str (ISO 8601 date)
        - date: str (ISO 8601 date, alternative)
        - created_at: str (ISO 8601 date, alternative)
        - amount: float or dict (value, currency)
        - amountSign: str ("positive", "negative")
        - currency: str (e.g., "CAD")
        - description: str (Raw description)
        - primary_action: str (Alternative description)
        - type: str (e.g., "DEPOSIT", "WITHDRAWAL", "BUY", "SELL", "DIVIDEND", "E_TRANSFER_FUNDING")
        - category: str (e.g., "transfer", "investment")
        - assetSymbol: str (e.g., "XEQT")
        - p2pMessage: str (e-transfer message)
        - p2pHandle: str
        - status: str (e.g., "posted")
        """
        # Date
        raw_date = activity.get('occurredAt') or activity.get('date') or activity.get('created_at')
        date = TransactionNormalizer.normalize_date(raw_date)
        
        # Amount
        amount_val = activity.get('amount', {}).get('amount') if isinstance(activity.get('amount'), dict) else activity.get('amount')
        amount_sign = activity.get('amountSign')
        amount = 0.0
        if amount_val is not None:
            try:
                float_amount = float(amount_val)
                if amount_sign == 'negative':
                    amount = -abs(float_amount)
                elif amount_sign == 'positive':
                    amount = abs(float_amount)
            except: pass
            
        # Description
        raw_description = activity.get('description') or activity.get('primary_action') or ''
        asset_symbol = activity.get('assetSymbol')
        trans_type = activity.get('type')
        
        # Clean description (simplified logic from original)
        cleaned_description = raw_description
        if asset_symbol:
            cleaned_description = re.sub(r'\[sec-[a-z]-[a-f0-9]+\]', asset_symbol, cleaned_description)
            
        cleaned_description = TransactionNormalizer.clean_description(cleaned_description)
        
        # Unique IDs
        # Wealthsimple provides a canonicalId or id
        ws_id = activity.get('canonicalId') or activity.get('id')
        unique_trans_id = ws_id if ws_id else TransactionNormalizer.generate_transaction_id(date, amount, cleaned_description, account_id)
        
        payee = TransactionNormalizer.normalize_payee(cleaned_description)

        # New Fields Logic
        # Explicitly handling INTERNAL_TRANSFER as per requirements
        is_transfer = trans_type in ['DEPOSIT', 'WITHDRAWAL', 'INTERNAL_TRANSFER', 'E_TRANSFER_FUNDING', 'E_TRANSFER_CASHOUT']
        notes = activity.get('p2pMessage', '')
        
        txn = {
            'Unique Account ID': account_id,
            'Unique Transaction ID': unique_trans_id,
            'Account Name': account_name,
            'Date': date,
            'Description': cleaned_description,
            'Payee': payee,
            'Payee Name': payee,
            'Amount': amount,
            'Currency': activity.get('amount', {}).get('currency') if isinstance(activity.get('amount'), dict) else activity.get('currency'),
            'Category': '', # Can implement categorization logic if needed
            'Is Transfer': is_transfer,
            'Notes': notes,
            'Account': account_name,
            'Asset Symbol': asset_symbol,
            'Asset Quantity': activity.get('assetQuantity'),
            'Status': activity.get('status'),
            'Sub Type': activity.get('subType'),
            'Fees': activity.get('fees'),
            'FX Rate': activity.get('fxRate'),
            'Type': trans_type,
            'ID': ws_id
        }
        
        # Add other fields
        for k, v in activity.items():
            if k not in ['amount', 'description', 'primary_action', 'occurredAt', 'date', 'created_at', 'canonicalId', 'id']:
                if isinstance(v, (str, int, float, bool)) or v is None:
                     txn[k] = v
                     
        return txn

    def _setup_monkey_patch(self):
        """Monkey-patch WealthsimpleAPI to use Playwright."""
        
        def playwright_send_http_request(api_self, url, method='POST', data=None, headers=None, return_headers=False):
            headers = headers or {}
            if method == 'POST':
                headers['Content-Type'] = 'application/json'

            if api_self.session.session_id:
                headers['x-ws-session-id'] = api_self.session.session_id

            if api_self.session.access_token and (not data or data.get('grant_type') != 'refresh_token'):
                headers['Authorization'] = f"Bearer {api_self.session.access_token}"

            if api_self.session.wssdi:
                headers['x-ws-device-id'] = api_self.session.wssdi

            if WealthsimpleAPI.user_agent:
                headers['User-Agent'] = WealthsimpleAPI.user_agent
            
            try:
                if method.upper() == 'GET':
                    response = self.context.request.get(url, headers=headers)
                elif method.upper() == 'POST':
                    response = self.context.request.post(url, headers=headers, data=data)
                else:
                    response = self.context.request.fetch(url, method=method, headers=headers, data=data)

                adapter = PlaywrightResponseAdapter(response)

                if return_headers:
                    headers_str = '\\r\\n'.join(f"{k}: {v}" for k, v in adapter.headers.items())
                    return f"{headers_str}\\r\\n\\r\\n{adapter.text}"

                return adapter.json()

            except Exception as e:
                print(f"Request failed: {e}")
                raise e

        WealthsimpleAPI.send_http_request = playwright_send_http_request
