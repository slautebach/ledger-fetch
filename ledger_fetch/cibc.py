import time
import json
from typing import List, Dict, Any
from datetime import datetime, timedelta
from playwright.sync_api import Request
from .base import BankDownloader
from .utils import TransactionNormalizer

class CIBCDownloader(BankDownloader):
    """
    Downloader for CIBC (Canadian Imperial Bank of Commerce).
    
    This downloader uses a hybrid approach:
    1.  Interactive Login: The user logs in manually via a browser window.
    2.  Token Interception: The script listens for API requests to capture session tokens.
    3.  API Direct Access: Once tokens are captured, it uses the internal CIBC JSON API 
        to fetch transactions directly, bypassing the UI for data retrieval.
    """
    
    def get_bank_name(self) -> str:
        """Return the unique identifier for this bank."""
        return "cibc"

    def login(self):
        """
        Navigate to the login page and wait for the user to log in.
        
        The script waits for the dashboard element (.card-container) to appear
        before proceeding.
        """
        print("Navigating to CIBC login page...")
        # User requested specific login URL
        self.page.goto("https://www.cibc.com/en/personal-banking.html?loggedOut=true")
        
        print("\n" + "="*50)
        print("PLEASE LOG IN MANUALLY")
        print("="*50)
        print("Waiting for dashboard...")
        
        # Wait for the dashboard to load (look for the account container)
        try:
            self.page.wait_for_selector(".card-container", timeout=300000) # 5 min timeout for login
            print("Dashboard detected!")
        except Exception:
            print("Timed out waiting for dashboard. Please ensure you are logged in.")

    def navigate_to_transactions(self):
        """
        No-op. 
        
        Navigation is handled dynamically in `download_transactions` by iterating 
        through discovered accounts.
        """
        pass

    def download_transactions(self) -> List[Dict[str, Any]]:
        """
        Orchestrate the transaction download process.
        
        1.  Captures session tokens (x-auth-token) from background API requests.
        2.  Scrapes the dashboard for available accounts (IDs and Names).
        3.  Iterates through each account.
        4.  For each account, iterates through the past 12 months.
        5.  Fetches transactions for each month using the internal JSON API.
        6.  Normalizes and returns the aggregated list of transactions.
        """
        # 1. Capture tokens from any API request on the dashboard
        print("Capturing session tokens...")
        captured_auth = {}
        
        # We need to wait for at least one API request to capture headers
        # Usually the dashboard makes many. We can reload if needed, but likely they are already there.
        # Let's set up a future listener or check past requests if possible? 
        # Playwright listener only catches future requests.
        # So we might need to reload the dashboard or wait for a background poll.
        
        def handle_request(request: Request):
            if "api/v1/json" in request.url and not captured_auth:
                headers = request.headers
                if 'x-auth-token' in headers:
                    captured_auth['headers'] = headers
                    print("Captured session tokens.")

        self.page.on("request", handle_request)
        
        # Wait a bit for a request to happen (e.g. balance check)
        self.page.wait_for_timeout(5000)
        
        if not captured_auth:
            print("No tokens captured yet. Reloading dashboard to trigger requests...")
            self.page.reload()
            self.page.wait_for_timeout(5000)
            
        if not captured_auth:
            raise Exception("Could not capture x-auth-token. Cannot proceed.")

        # 2. Scrape Account IDs
        print("Scanning for accounts...")
        # Selector based on user provided HTML: a[data-test-id^="account-card-account-name-link-"]
        account_links = self.page.query_selector_all('a[data-test-id^="account-card-account-name-link-"]')
        
        accounts = []
        for link in account_links:
            href = link.get_attribute('href')
            name = link.inner_text()
            # href example: /ebm-resources/public/banking/cibc/client/web/index.html#/accounts/credit-cards/cea725ba9cbe6428d4ea642a49f013d8a03349bc1a584166b1dfe7f9ec93ecd3
            if href:
                parts = href.split('/')
                acc_id = parts[-1]
                accounts.append({'id': acc_id, 'name': name, 'href': href})
                print(f"Found account: {name} (ID: {acc_id})")

        if not accounts:
            print("No accounts found on dashboard.")
            return []

        all_transactions = []
        
        # 3. Iterate and Fetch
        for acc in accounts:
            print(f"\nProcessing account: {acc['name']}")
            
            # Navigate to account page as requested
            # The href is relative or absolute? User provided relative in HTML but likely works with base.
            # We can just construct the full URL or click.
            # Clicking is safer to ensure SPA state updates.
            
            # Find the element again to avoid stale handle
            try:
                # We can just goto the URL to be faster and reliable
                target_url = f"https://www.cibconline.cibc.com{acc['href']}"
                print(f"Navigating to {target_url}")
                self.page.goto(target_url)
                self.page.wait_for_timeout(3000) # Wait for load
            except Exception as e:
                print(f"Error navigating to account: {e}")
                continue

            # Fetch transactions via API
            print("Fetching transaction history (past 12 months)...")
            
            today = datetime.now()
            
            # Iterate over the past 12 months
            for i in range(12):
                # Calculate start and end of the month
                # Logic: Go back i months. 
                # Start date: 1st of that month
                # End date: Last day of that month
                
                # Careful with month calculation
                month_target = today.month - i
                year_target = today.year
                
                while month_target <= 0:
                    month_target += 12
                    year_target -= 1
                
                start_date_dt = datetime(year_target, month_target, 1)
                
                # End date is start of next month minus 1 day
                if month_target == 12:
                    next_month = datetime(year_target + 1, 1, 1)
                else:
                    next_month = datetime(year_target, month_target + 1, 1)
                
                end_date_dt = next_month - timedelta(days=1)
                
                # Don't fetch future dates if we are in the current month
                if end_date_dt > today:
                    end_date_dt = today

                start_date_str = start_date_dt.strftime('%Y-%m-%d')
                end_date_str = end_date_dt.strftime('%Y-%m-%d')
                
                print(f"  Fetching {start_date_str} to {end_date_str}...")

                api_url = "https://www.cibconline.cibc.com/ebm-ai/api/v1/json/transactions"
                params = {
                    "accountId": acc['id'],
                    "ccTransactionState": "both",
                    "filterBy": "range",
                    "fromDate": start_date_str,
                    "toDate": end_date_str,
                    "limit": "1000",
                    "offset": "0",
                    "sortAsc": "true",
                    "sortByField": "date"
                }
                
                try:
                    response = self.context.request.get(
                        api_url,
                        params=params,
                        headers=captured_auth['headers']
                    )
                    
                    if response.ok:
                        data = response.json()
                        tx_list = data.get('transactions', [])
                        if not tx_list and isinstance(data, list):
                            tx_list = data
                            
                        print(f"    Retrieved {len(tx_list)} transactions.")
                        
                        for tx in tx_list:
                            # Normalize
                            date_str = tx.get('date') or tx.get('transactionDate') or tx.get('postedDate')
                            
                            # Amount logic
                            credit = tx.get('credit')
                            debit = tx.get('debit')
                            
                            if credit is not None:
                                amount = float(credit)
                            elif debit is not None:
                                amount = -abs(float(debit))
                            else:
                                amount = float(tx.get('amount') or tx.get('transactionAmount') or 0)

                            desc = tx.get('description') or tx.get('transactionDescription') or tx.get('merchantName')
                            
                            normalized_date = TransactionNormalizer.normalize_date(date_str)
                            clean_desc = TransactionNormalizer.clean_description(desc)
                            
                            t = {
                                'Date': normalized_date,
                                'Description': clean_desc,
                                'Amount': amount,
                                'Currency': 'CAD',
                                'Category': '',
                                'Unique Account ID': acc['id'],
                                'Unique Transaction ID': tx.get('id') or TransactionNormalizer.generate_transaction_id(normalized_date, amount, clean_desc, acc['id']),
                                # Extra details
                                'Transaction Type': tx.get('transactionType'),
                                'Transaction Location': tx.get('transactionLocation'),
                                'Merchant Category ID': tx.get('merchantCategoryId'),
                                'FIT ID': tx.get('fitId'),
                                'Pending': tx.get('pendingIndicator'),
                                'Description Line 1': tx.get('descriptionLine1'),
                                'Description Line 2': tx.get('descriptionLine2'),
                                'Merchant Class Code': tx.get('merchantClassCode'),
                                'Country Code': tx.get('countryCode')
                            }
                            all_transactions.append(t)
                    else:
                        print(f"    API Error: {response.status} {response.status_text}")
                        
                except Exception as e:
                    print(f"    Error fetching transactions: {e}")
                
                # Small pause between months to be nice
                time.sleep(0.5)
                
            # Pause briefly between accounts
            time.sleep(2)
            
        return all_transactions
