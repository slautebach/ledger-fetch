import json
import urllib.parse
import re
import time
import json
from datetime import datetime
from typing import List, Dict, Any
from .base import BankDownloader
from .utils import TransactionNormalizer
from .models import Transaction, Account, AccountType
from .config import settings

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

    def __init__(self, config=settings):
        super().__init__(config)
        self.ws = None

    def _initialize_api(self):
        """Initialize the Wealthsimple API client."""
        if self.ws:
            return

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
        self.ws = WealthsimpleAPI(session)

    def fetch_accounts(self) -> List[Account]:
        """Fetch accounts from Wealthsimple API."""
        self._initialize_api()
        
        print("Fetching accounts...")
        ws_accounts = self.ws.get_accounts()
        print(f"Found {len(ws_accounts)} accounts.")
        
        accounts = []
        for acc_data in ws_accounts:
            # Extract custodian account number (usually the first one)
            custodian_accounts = acc_data.get('custodianAccounts', [])
            account_number = ''
            if custodian_accounts and isinstance(custodian_accounts, list) and len(custodian_accounts) > 0:
                account_number = custodian_accounts[0].get('id', '')
                
            unique_id = acc_data.get('id')
            
            acc = Account(acc_data, unique_id)
            acc.account_name = acc_data.get('nickname') or acc_data.get('account_type') or unique_id
            acc.account_number = account_number
            acc.currency = acc_data.get('currency')
            
            # Map Type
            ws_type = acc_data.get('type')
            if ws_type == 'ca_credit_card':
                acc.type = AccountType.CREDIT_CARD
            elif ws_type == 'ca_cash_msb':
                acc.type = AccountType.CHEQUING
            else:
                acc.type = AccountType.INVESTMENT
            
            # Extra fields
            acc.raw_data['Status'] = acc_data.get('status')
            acc.raw_data['Unified Type'] = acc_data.get('unifiedAccountType')
            net_val = acc_data.get('financials', {}).get('currentCombined', {}).get('netLiquidationValue', {}).get('amount')
            acc.raw_data['Net Value'] = net_val
            
            # Map Current Balance
            if net_val is not None:
                try:
                    acc.current_balance = float(net_val)
                except (ValueError, TypeError):
                    pass
            acc.raw_data['Net Deposits'] = acc_data.get('financials', {}).get('currentCombined', {}).get('netDeposits', {}).get('amount')
            acc.raw_data['Created At'] = acc_data.get('createdAt')
            
            accounts.append(acc)
            
        return accounts

    def download_transactions(self) -> List[Transaction]:
        """Fetch transactions via API."""
        
        accounts = self.fetch_accounts()
        if not accounts:
            return []
            
        self.save_accounts(accounts)
        
        all_transactions = []
        
        for account in accounts:
            print(f"Processing account: {account.account_name} ({account.unique_account_id})")
            
            try:
                activities = self.ws.get_activities(account.unique_account_id, load_all=True)
                if isinstance(activities, dict) and 'results' in activities:
                    activities = activities['results']
                
                if not activities:
                    continue
                    
                print(f"  Found {len(activities)} transactions.")
                
                for activity in activities:
                    txn = self._process_activity(activity, account)
                    all_transactions.append(txn)
                    
            except Exception as e:
                print(f"  Error fetching transactions for account {account.unique_account_id}: {e}")
                
        return all_transactions
        
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



    def _process_activity(self, activity, account: Account) -> Transaction:
        """
        Process a single activity into a transaction object.
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
        unique_trans_id = ws_id if ws_id else TransactionNormalizer.generate_transaction_id(date, amount, cleaned_description, account.unique_account_id)
        
        payee_name = TransactionNormalizer.normalize_payee(cleaned_description)

        # New Fields Logic
        # Explicitly handling INTERNAL_TRANSFER as per requirements
        is_transfer = trans_type in ['DEPOSIT', 'WITHDRAWAL', 'INTERNAL_TRANSFER', 'E_TRANSFER_FUNDING', 'E_TRANSFER_CASHOUT']
        notes = activity.get('p2pMessage', '')
        
        # Create Transaction
        txn = Transaction(activity, account.unique_account_id)
        txn.unique_transaction_id = unique_trans_id
        txn.account_name = account.account_name
        txn.date = date
        txn.description = cleaned_description

        txn.payee_name = payee_name # Normalized payee
        txn.amount = amount
        txn.currency = activity.get('amount', {}).get('currency') if isinstance(activity.get('amount'), dict) else activity.get('currency')
        txn.is_transfer = is_transfer
        txn.notes = notes
        
        # Extra fields
        txn.raw_data['Asset Symbol'] = asset_symbol
        txn.raw_data['Asset Quantity'] = activity.get('assetQuantity')
        txn.raw_data['Status'] = activity.get('status')
        txn.raw_data['Sub Type'] = activity.get('subType')
        txn.raw_data['Fees'] = activity.get('fees')
        txn.raw_data['FX Rate'] = activity.get('fxRate')
        txn.raw_data['Type'] = trans_type
        txn.raw_data['ID'] = ws_id
        
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
