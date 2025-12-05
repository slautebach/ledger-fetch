import time
import json
from typing import List, Dict, Any
from datetime import datetime, timedelta
from playwright.sync_api import Request
from .base import BankDownloader
from .base import BankDownloader
from .utils import TransactionNormalizer
from .models import Transaction, Account, AccountType

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

    def download_transactions(self) -> List[Transaction]:
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

    def fetch_accounts(self) -> List[Account]:
        """Scrape accounts from the dashboard."""
        print("Scanning for accounts...")
        # Selector based on user provided HTML: a[data-test-id^="account-card-account-name-link-"]
        # We'll look for card containers to get both name and balance
        # Assuming .card-container or similar wrapper exists as per login check
        
        # Fallback to links if containers not found easily, but try to find containers first
        # We can iterate over links and find parent
        account_links = self.page.query_selector_all('a[data-test-id^="account-card-account-name-link-"]')
        
        accounts = []
        for link in account_links:
            href = link.get_attribute('href')
            name = link.inner_text()
            # href example: /ebm-resources/public/banking/cibc/client/web/index.html#/accounts/credit-cards/cea725ba9cbe6428d4ea642a49f013d8a03349bc1a584166b1dfe7f9ec93ecd3
            if href:
                parts = href.split('/')
                acc_id = parts[-1]
                
                # Create Account object
                # We don't have account number, just internal ID and name
                acc = Account({'href': href}, acc_id)
                acc.account_name = name
                acc.type = AccountType.CREDIT_CARD if "credit-cards" in href else AccountType.CHEQUING # Default to Chequing for bank accounts for now
                acc.currency = "CAD" # Assumption
                
                # Try to find balance
                # Navigate up to find the card container
                try:
                    # This is a bit hacky without exact DOM, but we can try to find a sibling with currency
                    # or use JS to find the parent card
                    balance = self.page.evaluate("""
                        (link) => {
                            // Find closest card container
                            const card = link.closest('div[class*="card"]'); 
                            if (!card) return null;
                            
                            // Look for balance
                            // Common selectors: .balance, .amount, or just text with $
                            const balanceEl = card.querySelector('.balance-amount, .account-balance, [class*="balance"]');
                            if (balanceEl) return balanceEl.textContent.trim();
                            
                            // Fallback: scan all elements
                            const all = card.querySelectorAll('*');
                            for (const el of all) {
                                if (el.textContent.includes('$') && el.textContent.length < 20) {
                                    return el.textContent.trim();
                                }
                            }
                            return null;
                        }
                    """, link)
                    
                    if balance:
                        import re
                        clean_bal = re.sub(r'[^\d.-]', '', balance)
                        acc.current_balance = float(clean_bal)
                except Exception as e:
                    print(f"Warning: Could not scrape balance for {name}: {e}")
                
                accounts.append(acc)
                print(f"Found account: {name} (ID: {acc_id})")

        if not accounts:
            print("No accounts found on dashboard.")
            
        return accounts

    def download_transactions(self) -> List[Transaction]:
        """
        Orchestrate the transaction download process.
        """
        # 1. Capture tokens from any API request on the dashboard
        print("Capturing session tokens...")
        captured_auth = {}
        
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

        # 2. Fetch Accounts
        accounts = self.fetch_accounts()
        if not accounts:
            return []
            
        self.save_accounts(accounts)

        all_transactions = []
        
        # 3. Iterate and Fetch
        for acc in accounts:
            print(f"\nProcessing account: {acc.account_name}")
            
            # Navigate to account page as requested
            try:
                # We can just goto the URL to be faster and reliable
                href = acc.raw_data.get('href', '')
                target_url = f"https://www.cibconline.cibc.com{href}"
                print(f"Navigating to {target_url}")
                self.page.goto(target_url)
                self.page.wait_for_timeout(3000) # Wait for load
                
                # Scrape balance from details page
                try:
                    # Selector based on user provided HTML:
                    # <li class="current-balance first"> ... <span class="align-right"><span>$999.50</span></span> ... </li>
                    balance_el = self.page.query_selector("li.current-balance .align-right span")
                    if balance_el:
                        balance_text = balance_el.inner_text()
                        import re
                        clean_bal = re.sub(r'[^\d.-]', '', balance_text)
                        if clean_bal:
                            acc.current_balance = float(clean_bal)
                            print(f"  Scraped balance: {acc.current_balance}")
                except Exception as e:
                    print(f"  Warning: Could not scrape balance from details page: {e}")

            except Exception as e:
                print(f"Error navigating to account: {e}")
                continue

            # Fetch transactions via API
            txns = self._fetch_transactions_for_account(acc, captured_auth)
            all_transactions.extend(txns)
            
            # Pause briefly between accounts
            time.sleep(2)
            
        # Save accounts again to update with scraped balances
        self.save_accounts(accounts)

        return all_transactions

    def _fetch_transactions_for_account(self, account: Account, captured_auth: Dict[str, Any]) -> List[Transaction]:
        """Fetch transactions for a specific account using internal API."""
        print("Fetching transaction history (past 12 months)...")
        
        today = datetime.now()
        transactions = []
        
        # Calculate months to fetch based on config
        months_to_fetch = (self.config.cibc.days_to_fetch // 30) + 1 # Approximate
        
        # Iterate over the past months
        for i in range(months_to_fetch):
            # Calculate start and end of the month
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
                "accountId": account.unique_account_id,
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
                        
                        payee_name = TransactionNormalizer.normalize_payee(clean_desc)

                        # Create Transaction
                        txn = Transaction(tx, account.unique_account_id)
                        txn.unique_transaction_id = tx.get('id') or TransactionNormalizer.generate_transaction_id(normalized_date, amount, clean_desc, account.unique_account_id)
                        txn.account_name = account.account_name
                        txn.date = normalized_date
                        txn.description = clean_desc
                        txn.payee = clean_desc # Original (cleaned) description
                        txn.payee_name = payee_name # Normalized payee
                        txn.amount = amount
                        txn.currency = 'CAD'
                        txn.is_transfer = 'Transfer' in clean_desc or 'Transfer' in (tx.get('transactionType') or '')
                        
                        # Extra details
                        txn.raw_data['Transaction Type'] = tx.get('transactionType')
                        txn.raw_data['Transaction Location'] = tx.get('transactionLocation')
                        txn.raw_data['Merchant Category ID'] = tx.get('merchantCategoryId')
                        txn.raw_data['FIT ID'] = tx.get('fitId')
                        txn.raw_data['Pending'] = tx.get('pendingIndicator')
                        txn.raw_data['Description Line 1'] = tx.get('descriptionLine1')
                        txn.raw_data['Description Line 2'] = tx.get('descriptionLine2')
                        txn.raw_data['Merchant Class Code'] = tx.get('merchantClassCode')
                        txn.raw_data['Country Code'] = tx.get('countryCode')
                        
                        transactions.append(txn)
                else:
                    print(f"    API Error: {response.status} {response.status_text}")
                    
            except Exception as e:
                print(f"    Error fetching transactions: {e}")
            
            # Small pause between months to be nice
            time.sleep(0.5)
            
        return transactions
