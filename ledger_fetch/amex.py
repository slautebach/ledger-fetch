import os
import time
import re
import shutil
import urllib.parse
import pandas as pd
from typing import List, Dict, Any
from .base import BankDownloader
from .utils import TransactionNormalizer
from .models import Transaction, Account, AccountType

class AmexDownloader(BankDownloader):
    """
    American Express Transaction Downloader.
    
    This downloader automates the process of fetching transactions from American Express.
    It prefers using the internal JSON API (`searchTransaction.json`) over CSV downloads
    for cleaner data and better reliability.
    
    Workflow:
    1.  Interactive Login: User logs in manually.
    2.  Account Discovery: Scrapes account ID from "Recent Activity" and balances from "Dashboard".
    3.  API Fetch: Uses `page.request.get` to call the internal API with the proper session headers.
    4.  Parsing: Converts the JSON response into standard Transaction objects.
    """
    
    from datetime import datetime, timedelta

    def get_bank_name(self) -> str:
        return "amex"

    def login(self):
        """
        Navigate to login page and wait for manual login.
        
        This method directs the browser to the 'Recent Activity' page, which redirects to the login
        screen if the user is not authenticated. It then waits for the URL to change back to a 
        statement/activity page, indicating successful login.
        """
        print("Navigating to American Express Statements page (will redirect to login)...")
        # Use the direct link that redirects back to statements after login
        self.page.goto("https://global.americanexpress.com/activity/recent")
        
        print("\nWaiting for user to log in...")
        print("Please complete the login process.")
        print("You should be automatically redirected to the Statements page.")
        
        # Wait for statements page
        try:
            # Wait for URL to indicate we are on statements/activity
            self.page.wait_for_url(re.compile(r".*(statement).*"), timeout=300000)
            print("Login and redirect detected.")
        except Exception:
            print("Warning: Login timeout or URL not matched. Proceeding anyway.")

    def navigate_to_transactions(self):
        """Navigate to Statements & Activity."""
        print("Navigating to Statements page...")
        
        try:
            self.page.goto("https://global.americanexpress.com/activity/recent")
        except:
            pass
                
        # Wait for the page to settle
        try:
            self.page.wait_for_url(re.compile(r".*(statement).*"), timeout=2000)
        except:
            pass

    def fetch_accounts(self) -> List[Account]:
        """
        Fetch account details by scraping both Recent Activity (for ID) and Dashboard (for balances).
        
        Amex doesn't provide a single clean "API" response for all account details that is easily 
        accessible without complex session tokens. Thus, we scrape:
        1.  Account ID (last 5 digits) from the "Recent Activity" page selector.
        2.  Current Balance and Payment Due info from the "Dashboard" page.
        
        Returns:
            List[Account]: A list containing the single primary active account (multi-card support is limited).
        """
        print("Fetching account details...")
        
        # --- Step 1: Get Account ID from Activity Page ---
        if "activity/recent" not in self.page.url:
            print("Navigating to Recent Activity for Account ID...")
            try:
                self.page.goto("https://global.americanexpress.com/activity/recent")
                self.page.wait_for_selector("span[data-ng-bind*='acctNumberlast5Digits']", timeout=15000)
            except:
                print("Warning: Timeout waiting for Activity page load.")

        last_digits = "00000"
        unique_id = "AMEX-DEFAULT"
        
        try:
            # Selector based on: <span class="card-member-cell ..."> - 91001</span>
            acct_el = self.page.locator("span[data-ng-bind*='acctNumberlast5Digits']").first
            if acct_el.count() > 0:
                 text = acct_el.text_content() # " - 91001"
                 match = re.search(r'(\d{4,5})', text)
                 if match:
                     last_digits = match.group(1)
                     unique_id = f"AMEX-{last_digits}"
        except Exception as e:
             print(f"Warning: could not parse account digits from Activity page: {e}")
        
        print(f"  Found account: {unique_id}")

        # --- Step 2: Get Balances from Dashboard ---
        print("Navigating to Dashboard for balances...")
        try:
            self.page.goto("https://global.americanexpress.com/dashboard")
            self.page.wait_for_selector("[data-locator-id='total_balance_title_value']", timeout=15000)
        except: 
             print("Warning: Timeout waiting for dashboard load.")

        current_balance = 0.0
        remaining_balance_due = 0.0
        statement_balance = 0.0
        payment_due_date = ""

        try:
            # Extract Balance (Current Balance / Total Balance)
            # User provided: <span ... data-locator-id="total_balance_title_value">...</span>
            balance_el = self.page.locator("[data-locator-id='total_balance_title_value']").first
            if balance_el.count() > 0:
                balance_text = balance_el.text_content()
                clean_balance = balance_text.replace('$', '').replace(',', '').strip()
                current_balance = float(clean_balance)
            
            # Extract Remaining Statement Balance
            # User provided: <span ... data-locator-id="remaining_statement_balance_title_value">...</span>
            rem_bal_el = self.page.locator("[data-locator-id='remaining_statement_balance_title_value']").first
            if rem_bal_el.count() > 0:
                txt = rem_bal_el.text_content().replace('$', '').replace(',', '').strip()
                if txt:
                    remaining_balance_due = float(txt)

            # Payment Due Date
            # Trying to find on Dashboard
            due_date_el = self.page.locator("[data-locator-id*='payment_due_date']").first
            if due_date_el.count() > 0:
                due_txt = due_date_el.text_content().strip()
                if due_txt:
                    from .utils import TransactionNormalizer
                    payment_due_date = TransactionNormalizer.normalize_date(due_txt)

        except Exception as e:
            print(f"Warning: could not parse dashboard details: {e}")

        print(f"  Balance: ${current_balance}")
        print(f"  Remaining Balance: ${remaining_balance_due}")
        print(f"  Payment Due: {payment_due_date}")

        account = Account({}, unique_id)
        account.current_balance = current_balance
        account.account_name = "American Express"
        account.currency = "CAD" # Assumption
        account.type = AccountType.CREDIT_CARD
        
        account.statement_balance = statement_balance # Not extracted yet
        account.remaining_balance_due = remaining_balance_due
        account.payment_due_date = payment_due_date
        
        return [account]



    def download_transactions(self) -> List[Transaction]:
        """
        Download transactions using the internal JSON API.
        
        This method uses the `searchTransaction.json` endpoint which powers the
        "Recent Activity" view on the website. This provides a more robust data source
        than scraping HTML or downloading CSVs, as it includes unique reference numbers
        and detailed description fields.
        
        Returns:
            List[Transaction]: List of parsed transactions.
        """
        print("Fetching transactions via API...")
        
        # Calculate date range
        days = self.config.amex.days_to_fetch
        print(f"Fetch configuration: days_to_fetch={days}")
        
        end_date = self.datetime.now()
        start_date = end_date - self.timedelta(days=days)
        
        start_str = start_date.strftime("%Y%m%d")
        end_str = end_date.strftime("%Y%m%d")
        
        print(f"Requesting transactions from {start_str} to {end_str}...")
        
        try:
            json_data = self._fetch_transactions_api(start_str, end_str)
            transactions = self._parse_amex_json(json_data)
            print(f"Successfully fetched {len(transactions)} transactions.")
            return transactions
        except Exception as e:
            print(f"Error fetching transactions: {e}")
            if self.config.debug:
                self.page.screenshot(path=self.config.transactions_path / "amex_error.png")
            return []

    def _fetch_transactions_api(self, start_date: str, end_date: str) -> Dict[str, Any]:
        """
        Execute the internal API call using page.request to bypass 'eval disabled' restrictions.
        
        Using `self.page.request` is crucial here because it automatically includes
        all the cookies from the browser session (authentication, session ID), which
        are required to authorize the API call.
        
        Args:
            start_date (str): Start date in YYYYMMDD format.
            end_date (str): End date in YYYYMMDD format.
            
        Returns:
            Dict[str, Any]: The raw JSON response from the API.
        """
        url = (
            f"https://global.americanexpress.com/myca/intl/istatement/canlac/searchTransaction.json"
            f"?method=searchTransaction&clearSearchParticipant=true&Face=en_CA&sorted_index=0"
            f"&BPIndex=-1&requestType=searchDateRange"
            f"&currentStartDate={start_date}&currentEndDate={end_date}"
        )
        
        # We need a Referer header, possibly with the account key if we can find it
        account_key = self._extract_account_key() or "AMEX-DEFAULT"
        referer = f"https://global.americanexpress.com/myca/intl/istatement/canlac/statement.do?request_type=&Face=en_CA&BPIndex=0&account_key={account_key}"
        
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Referer": referer
        }

        try:
            print(f"DEBUG: Making API request to {url}")
            response = self.page.request.get(url, headers=headers)
            
            if not response.ok:
                print(f"API Request Failed: {response.status} {response.status_text}")
                # Try to print body for debugging
                try:
                    print(response.text())
                except: pass
                raise Exception(f"HTTP error! status: {response.status}")
            
            return response.json()
        except Exception as e:
            print(f"API Request failed: {e}")
            raise e

    def _parse_amex_json(self, data: Dict[str, Any]) -> List[Transaction]:
        """
        Parse the JSON response from searchTransaction.json.
        
        Args:
            data (Dict[str, Any]): The raw JSON data from the API.
            
        Returns:
            List[Transaction]: A list of Transaction objects.
        """
        transactions = []
        
        try:
            # Navigate to transactions list
            stmt = data.get("statement", {})
            txns_list = stmt.get("transactionsList", [])
            
            if not txns_list:
                print("No transactions found in API response.")
                return []
                
            for item in txns_list:
                try:
                    # Extract fields
                    timestamp = item.get("chargeDate")
                    if timestamp:
                        date_obj = self.datetime.fromtimestamp(timestamp / 1000)
                        date_str = date_obj.strftime("%Y-%m-%d")
                    else:
                        continue
                        
                    description = item.get("descriptionLine", "").strip()
                    amount = float(item.get("transactionAmount", 0.0))
                    
                    unique_trans_id = item.get("uniqueReferenceNumber")
                    if not unique_trans_id:
                         unique_trans_id = item.get("transactionId")
                         
                    account_id = "AMEX"
                    bal_info = stmt.get("balanceInfo", {})
                    last_digits = bal_info.get("acctNumberlast5Digits")
                    if last_digits:
                        account_id = f"AMEX-{last_digits}"
                    
                    clean_desc = TransactionNormalizer.clean_description(description)
                    payee_name = TransactionNormalizer.normalize_payee(clean_desc)
                    
                    txn = Transaction(item, account_id)
                    txn.unique_transaction_id = unique_trans_id
                    txn.date = date_str
                    txn.description = clean_desc

                    txn.payee_name = payee_name
                    txn.amount = amount
                    txn.currency = "CAD" # Default
                    
                    transactions.append(txn)
                    
                except Exception as e:
                    print(f"Error parsing transaction item: {e}")
                    continue
                    
        except Exception as e:
            print(f"Error parsing JSON response: {e}")
            
        return transactions

    def _expand_sections(self):
        """Deprecated: No longer needed for API approach."""
        pass

    def _extract_account_key(self):
        """Extract account key from URL or page content."""
        account_key = None
        try:
            # Try URL
            for i in range(5):
                current_url = self.page.url
                parsed_url = urllib.parse.urlparse(current_url)
                query_params = urllib.parse.parse_qs(parsed_url.query)
                keys = query_params.get('account_key')
                if keys:
                    return keys[0]
                time.sleep(1)
                
            # Try Page Content
            content = self.page.content()
            match = re.search(r'account_key=["\']?([a-zA-Z0-9-]+)["\']?', content)
            if match:
                return match.group(1)
        except:
            pass
            
        return None 

    def _find_download_buttons(self):
        pass

    def _extract_date(self, btn):
        pass

    def _download_statement(self, account_key, date_part, is_latest, download_dir):
        pass

    def _parse_amex_csv(self, csv_path: str, account_id: str = "AMEX") -> List[Transaction]:
        pass

