import time
import json
from datetime import datetime, timedelta
from typing import List, Dict, Any
from .base import BankDownloader
from .base import BankDownloader
from .utils import TransactionNormalizer
from .models import Transaction, Account, AccountType


class BMODownloader(BankDownloader):
    """
    BMO (Bank of Montreal) Transaction Downloader.
    
    This downloader automates the retrieval of transaction data from BMO's online banking.
    It uses a advanced hybrid approach because BMO's standard CSV export is often limited.
    
    Workflow:
    1.  Interactive Login: Use Playwright to let the user log in.
    2.  Page Scraping: Parse the DOM of the accounts list to discover credit card accounts 
        and their current balances.
    3.  API Interception: Unlike RBC (which uses direct HTTP requests), BMO requires complex 
        headers (XSRF tokens, session IDs). We solve this by executing `fetch` *inside* 
        the browser context via `page.evaluate()`. This ensures all cookies and session 
        headers are automatically attached by the browser.
    
    This allows us to fetch detailed transaction data (including pending transactions)
    from the internal `/api/cdb/utility/cache/transient-extended-credit-card-data/get` endpoint.
    """

    def get_bank_name(self) -> str:
        return "bmo"

    def login(self):
        """Navigate to login page and wait for manual login."""
        print("Navigating to BMO login page...")
        # Forward console logs to Python stdout for debugging
        if self.config.debug:
            self.page.on("console", lambda msg: print(f"BROWSER CONSOLE: {msg.text}"))
        
        self.page.goto("https://www1.bmo.com/banking/digital/login?lang=en")
        
        print("\nWaiting for user to log in to BMO...")
        print("Please complete:")
        print("1. Login process")
        print("2. Two-factor authentication (if required)")
        
        # Wait for successful login - look for accounts page specifically
        # The login page is at /banking/digital/login, so we need to wait
        # until we're redirected away from it
        try:
            # Wait for navigation away from login page to accounts/summary
            self.page.wait_for_url("**/accounts", timeout=300000)
            print("Login detected.")
            time.sleep(3)  # Give page time to fully load
        except Exception:
            print("Warning: Login timeout or URL not matched.")
            print("Checking if we're on an accounts page...")
            current_url = self.page.url
            if "/login" not in current_url.lower():
                print("Appears to be logged in. Proceeding...")
                time.sleep(3)
            else:
                print("Still on login page. Please complete login and press Enter to continue.")
                input()
                time.sleep(3)

    def navigate_to_transactions(self):
        """Navigate to accounts list page."""
        print("Navigating to accounts page...")
        try:
            self.page.goto("https://www1.bmo.com/banking/digital/accounts")
            time.sleep(3)  # Wait for accounts to load
            print("Accounts page loaded.")
        except Exception as e:
            print(f"Could not navigate to accounts page: {e}")

    def fetch_accounts(self) -> List[Account]:
        """Fetch accounts from the accounts list page."""
        print("Finding credit card accounts...")
        accounts = []
        
        # Reuse the scraping logic
        account_dicts = self._get_credit_card_accounts()
        
        for acc_dict in account_dicts:
            name = acc_dict['name']
            number = acc_dict['number']
            
            # Generate ID
            # BMO-{last 4}
            unique_id = f"BMO-{number[-4:]}" if len(number) >= 4 else f"BMO-{number}"
            
            acc = Account(acc_dict, unique_id)
            acc.account_name = name
            acc.account_number = number
            acc.type = AccountType.CREDIT_CARD
            acc.currency = "CAD" # Assumption
            
            # Map Current Balance
            balance_str = acc_dict.get('balance')
            if balance_str:
                # If string contains $, it likely has garbage before it (like "Mastercard8733 , $898.70")
                if '$' in balance_str:
                    balance_str = balance_str.split('$')[-1]
                
                # Clean string (remove $, commas, whitespace)
                import re
                clean_bal = re.sub(r'[^\d.-]', '', balance_str)
                try:
                    acc.current_balance = float(clean_bal)
                except (ValueError, TypeError):
                    pass
            
            accounts.append(acc)
            
        return accounts

    def download_transactions(self) -> List[Transaction]:
        """Fetch transactions for all credit card accounts."""
        
        accounts = self.fetch_accounts()
        
        if not accounts:
            print("No credit card accounts found.")
            return []
        
        self.save_accounts(accounts)
        print(f"Found {len(accounts)} credit card account(s)")
        
        all_transactions = []
        
        # Process each account
        for idx, account in enumerate(accounts, 1):
            print(f"\n[{idx}/{len(accounts)}] Processing: {account.account_name} ({account.account_number})")
            try:
                # Click on the account to open it
                self._click_account(idx - 1)  # 0-indexed
                time.sleep(3)  # Wait for account page to load
                
                current_url = self.page.url
                print(f"  Current URL: {current_url}")
                
                # Scrape accurate balance from details page
                details_balance = self._scrape_details_balance()
                if details_balance is not None:
                    print(f"  Updated balance from details page: {details_balance}")
                    account.current_balance = details_balance
                    # Update accounts.csv immediately
                    self.save_accounts(accounts)
                
                # BMO API doesn't allow date ranges that cross calendar years
                # Fetch transactions by calendar year (looping backwards)
                all_account_transactions = []
                
                current_date = datetime.now()
                current_year = current_date.year
                
                years_to_fetch = (self.config.bmo.days_to_fetch // 365) + 1
                
                for i in range(years_to_fetch):
                    target_year = current_year - i
                    
                    if target_year == current_year:
                        from_date_str = f"{target_year}-01-01"
                        to_date_str = current_date.strftime("%Y-%m-%d")
                    else:
                        from_date_str = f"{target_year}-01-01"
                        to_date_str = f"{target_year}-12-31"
                    
                    print(f"  Fetching {target_year}: {from_date_str} to {to_date_str}...")
                    try:
                        transactions_year = self._fetch_transactions_from_api(from_date_str, to_date_str, account)
                        all_account_transactions.extend(transactions_year)
                    except Exception as e:
                        print(f"  Error fetching {target_year}: {e}")
                    time.sleep(1)
                
                all_transactions.extend(all_account_transactions)
                
                # Navigate back to accounts list for next account
                if idx < len(accounts):
                    print("Returning to accounts list...")
                    self.page.goto("https://www1.bmo.com/banking/digital/accounts", wait_until="networkidle")
                    time.sleep(2)
                    
            except Exception as e:
                print(f"Error processing account {account.account_name}: {e}")
                import traceback
                traceback.print_exc()
                # Try to return to accounts list
                try:
                    self.page.goto("https://www1.bmo.com/banking/digital/accounts", wait_until="networkidle")
                    time.sleep(2)
                except:
                    pass
        
        print(f"\nTotal transactions fetched: {len(all_transactions)}")
        return all_transactions

    def _get_credit_card_accounts(self) -> List[Dict[str, str]]:
        """Extract credit card account information from the accounts list page.
        
        Returns:
            List[Dict]: List of dicts with 'name', 'number', and 'balance' keys.
        """
        # We rely on executing JavaScript in the browser to robustly traverse the DOM,
        # as the page structure is complex and dynamic (Angular/React).
        # Reuse the scraping logic
        # Retry up to 5 times (15 seconds total)
        for attempt in range(5):
            try:
                accounts = self.page.evaluate("""
                    () => {
                        const accounts = [];
                        
                        // Find all credit card account items
                        const accountItems = document.querySelectorAll('app-accounts-list-group-item');
                        
                        accountItems.forEach(item => {
                            // Check if this is in the credit cards section
                            const container = item.closest('.account-container');
                            if (!container) return;
                            
                            const heading = container.querySelector('app-accounts-list-category-heading');
                            if (!heading || !heading.textContent.toLowerCase().includes('credit card')) return;
                            
                            // Extract account name
                            const nameElement = item.querySelector('.account-name');
                            const name = nameElement ? nameElement.textContent.trim() : '';
                            
                            // Extract account number (last 4 digits)
                            const numberElement = item.querySelector('.account-number');
                            const number = numberElement ? numberElement.textContent.trim() : '';
                            
                            // Extract balance
                            // Try multiple selectors as we don't have the exact DOM
                            let balance = null;
                            const balanceSelectors = ['.account-balance', '.balance', '.amount', '[data-test-id="account-balance"]'];
                            
                            for (const selector of balanceSelectors) {
                                const el = item.querySelector(selector);
                                if (el) {
                                    balance = el.textContent.trim();
                                    break;
                                }
                            }
                            
                            // Fallback: look for text containing '$'
                            if (!balance) {
                                const spans = item.querySelectorAll('span, div');
                                for (const span of spans) {
                                    if (span.textContent.includes('$') && span.textContent.replace(/[^\d.]/g, '').length > 0) {
                                        balance = span.textContent.trim();
                                        break;
                                    }
                                }
                            }

                            if (name && number) {
                                accounts.push({ name, number, balance });
                            }
                        });
                        
                        return accounts;
                    }
                """)
                
                if accounts:
                    return accounts
                
                print(f"  Attempt {attempt+1}/5: No accounts found yet, waiting...")
                time.sleep(3)
                
            except Exception as e:
                print(f"Error extracting account information: {e}")
                return []
                
        return []

    def _click_account(self, index: int):
        """Click on a credit card account by index.
        
        Args:
            index: 0-based index of the account to click
        """
        try:
            self.page.evaluate(f"""
                (index) => {{
                    const accountItems = document.querySelectorAll('app-accounts-list-group-item');
                    const creditCardItems = [];
                    
                    accountItems.forEach(item => {{
                        const container = item.closest('.account-container');
                        if (!container) return;
                        
                        const heading = container.querySelector('app-accounts-list-category-heading');
                        if (!heading || !heading.textContent.toLowerCase().includes('credit card')) return;
                        
                        creditCardItems.push(item);
                    }});
                    
                    if (creditCardItems[index]) {{
                        const clickableRow = creditCardItems[index].querySelector('.account-row');
                        if (clickableRow) {{
                            clickableRow.click();
                        }}
                    }}
                }}
            """, index)
            
        except Exception as e:
            print(f"Error clicking account: {e}")

    def _fetch_transactions_from_api(self, from_date: str, to_date: str, account: Account) -> List[Transaction]:
        """
        Fetch transactions from BMO REST API by injecting JS.
        
        This method constructs the complex payload required by BMO's backend and then
        uses `page.evaluate()` to perform the `fetch` call from within the authorized 
        browser session. This bypasses issues with CORS and missing cookies that would 
        occur if we used `self.page.request.post` from the Python side.
        
        Args:
            from_date: Start date in YYYY-MM-DD format
            to_date: End date in YYYY-MM-DD format
            account: The account object
        """
        
        api_url = "https://www1.bmo.com/api/cdb/utility/cache/transient-extended-credit-card-data/get"
        
        try:
            # Build request payload
            post_data = {
                "accountIndex": "0",
                "fromDate": from_date,
                "toDate": to_date,
                "promoOfferToggle": True,
                "promoOfferDetails": {
                    "interactionPoint": "CDB_InstallmentTab_IP",
                    "sessionAttributes": [
                        {"name": "CHANNEL_ID", "value": "CDB_InstallmentTab", "valueDataType": "String"},
                        {"name": "SESSION_CHANNEL_ID", "value": "OLB", "valueDataType": "String"},
                        {"name": "AUDIENCE_LEVEL", "value": "Customer", "valueDataType": "String"},
                        {"name": "CHANNEL_LANGUAGE", "value": "EN", "valueDataType": "String"},
                        {"name": "DIGITAL_CHANNEL_ID", "value": "OLB", "valueDataType": "String"},
                        {"name": "DIGITAL_DEVICE_DETAIL", "value": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36", "valueDataType": "String"}
                    ]
                }
            }
            
            if self.config.debug:
                print(f"DEBUG: API Request Payload for {from_date} to {to_date}:")
                print(json.dumps(post_data, indent=2))


            
            # Make API call using page.evaluate to maintain session
            result = self.page.evaluate("""
                async (params) => {
                    try {
                        // Extract XSRF token from cookies
                        const cookies = document.cookie.split(';').reduce((acc, cookie) => {
                            const [key, value] = cookie.trim().split('=');
                            acc[key] = value;
                            return acc;
                        }, {});
                        
                        const xsrfToken = cookies['XSRF-TOKEN'] || '';
                        
                        // Update User-Agent in payload to match actual browser
                        const payload = params.data;
                        if (payload.promoOfferDetails && payload.promoOfferDetails.sessionAttributes) {
                            const uaAttr = payload.promoOfferDetails.sessionAttributes.find(attr => attr.name === 'DIGITAL_DEVICE_DETAIL');
                            if (uaAttr) {
                                uaAttr.value = navigator.userAgent;
                            }
                        }
                        
                        // Generate required IDs
                        const generateUUID = () => {
                            return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function(c) {
                                const r = Math.random() * 16 | 0;
                                const v = c === 'x' ? r : (r & 0x3 | 0x8);
                                return v.toString(16);
                            });
                        };
                        
                        const currentPath = window.location.pathname;
                        const currentTime = new Date().toUTCString();
                        
                        const headers = {
                            'Content-Type': 'application/json',
                            'Accept': 'application/json, text/plain, */*',
                            'X-XSRF-TOKEN': xsrfToken,
                            'X-ChannelType': 'OLB',
                            'X-App-Current-Path': currentPath,
                            'X-App-Version': 'session-id',
                            'X-Original-Request-Time': currentTime,
                            'X-UI-Session-ID': '0.0.1',
                            'x-api-key': '47c4abcb8fdc34e1a4aacc8b19912c30',
                            'x-app-cat-id': '63623',
                            'x-bmo-session-id': 'session-id',
                            'x-client-id': '63623',
                            'x-fapi-financial-id': '001',
                            'x-fapi-interaction-id': generateUUID(),
                            'x-request-id': 'REQ_' + Array.from({length: 16}, () => Math.floor(Math.random() * 16).toString(16)).join(''),
                            'x_bmo_csg': 'true',
                            'x_bmo_user_lang': 'EN',
                            'x_channeltype': 'OLB'
                        };
                        
                        // Add a debugger statement to pause JS execution if DevTools is open
                        debugger;
                        
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
                "data": post_data
            })
            
            if "error" in result:
                print(f"API fetch error: {result['error']}")
                if self.config.debug:
                    print("!"*60)
                    print("API EXECUTION ERROR")
                    print("The JavaScript code failed to execute properly.")
                    print("Check the BROWSER CONSOLE logs above for details.")
                    print("!"*60)
                    input("Press Enter to continue (and likely fail)...")
                return []
                
            if not result.get("ok"):
                print(f"API error status: {result.get('status')}")
                if self.config.debug:
                    print(f"Response text preview: {result.get('text', '')[:1000]}")
                    print("!"*60)
                    print("API REQUEST FAILED (Non-200 Status)")
                    print("1. Check the Network tab in the browser.")
                    print("2. Look for the failed request.")
                    print("3. Check the recorded HAR file.")
                    print("!"*60)
                    input("Press Enter to continue (and likely fail)...")
                return []
                
            json_response = json.loads(result.get("text", "{}"))
            return self._parse_transaction_response(json_response, account)
            
        except Exception as e:
            print(f"Error fetching transactions: {e}")
            import traceback
            traceback.print_exc()
            return []

    def _parse_transaction_response(self, json_data: Dict[str, Any], account: Account) -> List[Transaction]:
        """Parse BMO API JSON response and normalize to standard format.
        
        Args:
            json_data: Raw JSON response from BMO API
            account: The account object
            
        Returns:
            List of normalized transaction objects
        """
        transactions = []
        
        # Get posted transactions
        posted_txns = json_data.get('postedTransactions', {}).get('transactions', [])
        
        print(f"Found {len(posted_txns)} posted transactions")
        
        for txn_data in posted_txns:
            # Extract fields
            txn_date = txn_data.get('txnDate', '')  # Transaction date (YYYY-MM-DD)
            post_date = txn_data.get('postDate', '')  # Posted date (YYYY-MM-DD)
            description = txn_data.get('descr', '')
            merchant_name = txn_data.get('merchantName', '')
            amount_val = float(txn_data.get('amount', 0))
            txn_indicator = txn_data.get('txnIndicator', 'DR')  # DR = Debit, CR = Credit
            txn_id = txn_data.get('transactionId', '')
            txn_ref = txn_data.get('txnRefNumber', '')
            txn_code = txn_data.get('txnCode', '')
            
            # Use posted date as the primary date (when it cleared)
            date = TransactionNormalizer.normalize_date(post_date if post_date else txn_date)
            
            # Clean description
            description = TransactionNormalizer.clean_description(description)
            
            payee_name = TransactionNormalizer.normalize_payee(description)

            # Determine signed amount
            # DR (Debit) = money spent (negative)
            # CR (Credit) = payment/refund (positive)
            if txn_indicator == 'DR':
                amount = -amount_val
            else:
                amount = amount_val
            
            # Use BMO's transaction ID, or generate one if missing
            unique_id = txn_id if txn_id else TransactionNormalizer.generate_transaction_id(
                date, amount, description, account.unique_account_id
            )
            
            # Create Transaction
            txn = Transaction(txn_data, account.unique_account_id)
            txn.unique_transaction_id = unique_id
            txn.account_name = account.account_name
            txn.date = date
            txn.description = description

            txn.payee_name = payee_name # Normalized payee
            txn.amount = amount
            txn.currency = 'CAD'
            
            # BMO-specific fields in raw_data (already passed in constructor, but we can add more if needed)
            txn.raw_data['Transaction Date'] = txn_date
            txn.raw_data['Post Date'] = post_date
            txn.raw_data['Merchant Name'] = merchant_name
            txn.raw_data['Transaction Indicator'] = txn_indicator
            txn.raw_data['Transaction Code'] = txn_code
            txn.raw_data['Reference Number'] = txn_ref
            
            transactions.append(txn)
        
        # Also get pending transactions if any
        pending_txns = json_data.get('pendingTransactions', {}).get('transactions', [])
        if pending_txns:
            print(f"Found {len(pending_txns)} pending transactions (not included in output)")
            # Note: We're not including pending transactions as they haven't cleared yet
            # If you want to include them, you can parse them similarly
        
        print(f"Parsed {len(transactions)} posted transactions")
        return transactions

    def _scrape_details_balance(self) -> float:
        """Scrape balance from the account details page."""
        try:
            # Use the selector provided by user
            # .current-balance-container-desktop-tablet .fdc-heading1
            balance_str = self.page.evaluate("""
                () => {
                    const el = document.querySelector('.current-balance-container-desktop-tablet .fdc-heading1');
                    return el ? el.textContent.trim() : null;
                }
            """)
            
            if balance_str:
                # Same cleaning logic as before just in case
                if '$' in balance_str:
                    balance_str = balance_str.split('$')[-1]
                
                import re
                clean_bal = re.sub(r'[^\d.-]', '', balance_str)
                try:
                    return float(clean_bal)
                except ValueError:
                    return None
        except Exception as e:
            print(f"Error scraping details balance: {e}")
        return None
