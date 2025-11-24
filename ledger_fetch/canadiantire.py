import time
import json
import uuid
from datetime import datetime
from typing import List, Dict, Any
from .base import BankDownloader
from .utils import TransactionNormalizer

class CanadianTireDownloader(BankDownloader):
    """Canadian Tire Transaction Downloader."""

    def get_bank_name(self) -> str:
        return "canadiantire"

    def login(self):
        """Navigate to login page and wait for manual login."""
        print("Navigating to Canadian Tire Financial Services login page...")
        self.page.goto("https://www.ctfs.com/content/dash/en/private/Details.html#!/view?tab=account-details")
        
        print("\nWaiting for user to log in to Canadian Tire Financial Services...")
        print("Please complete:")
        print("1. Login process (including any 2FA if required)")
        
        # Wait for account details page
        try:
            self.page.wait_for_url("**/Details.html**", timeout=300000)
            print("Login detected.")
        except Exception:
            print("Warning: Login timeout or URL not matched. Proceeding anyway.")

    def navigate_to_transactions(self):
        """Navigate to account details page."""
        print("Navigating to account details page...")
        try:
            self.page.goto("https://www.ctfs.com/content/dash/en/private/Details.html#!/view?tab=account-details", wait_until="networkidle")
            time.sleep(3)
        except Exception as e:
            print(f"Could not auto-navigate: {e}")

    def download_transactions(self) -> List[Dict[str, Any]]:
        """Fetch transactions via API."""
        
        # 1. Capture transient reference
        transient_ref = self._get_transient_reference()
        
        # 2. Get statement dates
        statement_dates = self._get_statement_dates()
        if not statement_dates:
            print("No statement dates found.")
            return []
            
        print(f"Fetching transactions for {len(statement_dates)} statement(s)...")
        
        all_transactions = []
        for date in statement_dates:
            txns = self._fetch_transactions_for_statement(date, transient_ref)
            all_transactions.extend(txns)
            time.sleep(1)
            
        return all_transactions

    def _get_transient_reference(self):
        """Get transient reference from page or generate one."""
        # Try to get from window object
        try:
            auth_info = self.page.evaluate("""
                () => {
                    let transientRef = '';
                    if (window.transientReference) {
                        transientRef = window.transientReference;
                    }
                    return { transientReference: transientRef };
                }
            """)
            ref = auth_info.get("transientReference")
            if ref:
                print(f"Found transient reference in window: {ref[:20]}...")
                return ref
        except: pass
        
        print("Warning: No transient reference found, using generated UUID")
        return str(uuid.uuid4())

    def _get_statement_dates(self):
        """Extract available statement dates."""
        try:
            self.page.wait_for_selector("#selectBillingDates", timeout=10000)
            options = self.page.evaluate("""
                () => {
                    const select = document.getElementById('selectBillingDates');
                    const options = Array.from(select.options);
                    return options
                        .filter(opt => opt.value !== 'current' && opt.value !== '')
                        .map(opt => opt.value);
                }
            """)
            return options
        except Exception as e:
            print(f"Could not extract statement dates: {e}")
            return []

    def _fetch_transactions_for_statement(self, statement_date, transient_ref):
        """Fetch transactions for a specific date."""
        api_url = "https://www.ctfs.com/dash/v1/account/retrieveTransactions"
        print(f"Fetching transactions for {statement_date}")
        
        try:
            # Get CSRF token
            csrf_info = self.page.evaluate("""
                () => {
                    const cookies = document.cookie.split(';').reduce((acc, cookie) => {
                        const [key, value] = cookie.trim().split('=');
                        acc[key] = value;
                        return acc;
                    }, {});
                    return { csrftoken: cookies['csrftoken'] || '' };
                }
            """)
            csrf_token = csrf_info.get("csrftoken", "")
            
            post_data = {
                "category": "STATEMENTED",
                "statementDate": statement_date,
                "transientReference": transient_ref
            }
            
            result = self.page.evaluate("""
                async (params) => {
                    try {
                        const headers = {
                            'Content-Type': 'application/json',
                            'X-Requested-With': 'XMLHttpRequest'
                        };
                        if (params.csrftoken) {
                            headers['csrftoken'] = params.csrftoken;
                        }
                        const response = await fetch(params.url, {
                            method: 'POST',
                            headers: headers,
                            credentials: 'include',
                            body: JSON.stringify(params.data)
                        });
                        const text = await response.text();
                        return {
                            ok: response.ok,
                            status: response.status,
                            text: text
                        };
                    } catch (error) {
                        return { error: error.message };
                    }
                }
            """, {
                "url": api_url,
                "data": post_data,
                "csrftoken": csrf_token
            })
            
            if "error" in result:
                print(f"Fetch error: {result['error']}")
                return []
                
            if not result.get("ok"):
                print(f"API error: {result.get('status')}")
                return []
                
            json_response = json.loads(result.get("text", "{}"))
            return self._parse_transaction_response(json_response)
            
        except Exception as e:
            print(f"Error fetching transactions: {e}")
            return []

    def _parse_transaction_response(self, json_data):
        """Parse API response."""
        transactions = []
        if 'transactions' not in json_data:
            return transactions
            
        for txn in json_data['transactions']:
            tran_date = txn.get('tranDate', '')
            date = TransactionNormalizer.normalize_date(tran_date)
            
            merchant = txn.get('merchant', '')
            description = TransactionNormalizer.clean_description(merchant)
            
            amount_val = float(txn.get('amount', 0))
            trans_type = txn.get('type', '')
            
            # Signed amount
            if trans_type == 'PURCHASE':
                amount = -amount_val
            else:
                amount = amount_val
                
            # IDs
            ref_num = txn.get('referenceNumber', '')
            unique_trans_id = ref_num if ref_num else TransactionNormalizer.generate_transaction_id(date, amount, description, "CTFS")
            
            transaction = {
                'Unique Account ID': "CTFS",
                'Unique Transaction ID': unique_trans_id,
                'Date': date,
                'Description': description,
                'Amount': amount,
                'Currency': 'CAD',
                'Category': '',
                'Type': trans_type,
                'Merchant': merchant,
                'Reference Number': ref_num
            }
            
            # Add other fields
            for k, v in txn.items():
                if k not in ['tranDate', 'merchant', 'amount', 'type', 'referenceNumber']:
                    transaction[k] = v
                    
            transactions.append(transaction)
            
        return transactions
