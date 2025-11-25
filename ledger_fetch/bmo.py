import time
import json
from datetime import datetime, timedelta
from typing import List, Dict, Any
from .base import BankDownloader
from .utils import TransactionNormalizer


class BMODownloader(BankDownloader):
    """
    BMO (Bank of Montreal) Transaction Downloader.
    
    This downloader automates the retrieval of transaction data from BMO's online banking.
    It uses a hybrid approach:
    1.  Interactive Login: The user logs in manually.
    2.  API Interception: The script executes JavaScript within the browser context to 
        call BMO's internal REST API (`/api/cdb/utility/cache/transient-extended-credit-card-data/get`).
    
    This allows for fetching detailed transaction data, including pending transactions
    and extended metadata, which might not be available in standard CSV exports.
    """

    def get_bank_name(self) -> str:
        return "bmo"

    def login(self):
        """Navigate to login page and wait for manual login."""
        print("Navigating to BMO login page...")
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

    def download_transactions(self) -> List[Dict[str, Any]]:
        """Fetch transactions for all credit card accounts."""
        
        print("Finding credit card accounts...")
        
        # Get all credit card account links
        account_links = self._get_credit_card_accounts()
        
        if not account_links:
            print("No credit card accounts found.")
            return []
        
        print(f"Found {len(account_links)} credit card account(s)")
        
        all_transactions = []
        
        # Process each account
        for idx, account_info in enumerate(account_links, 1):
            account_name = account_info['name']
            account_number = account_info['number']
            
            print(f"\n[{idx}/{len(account_links)}] Processing: {account_name} ({account_number})")
            
            try:
                # Click on the account to open it
                self._click_account(idx - 1)  # 0-indexed
                time.sleep(3)  # Wait for account page to load
                
                current_url = self.page.url
                print(f"  Current URL: {current_url}")
                
                # BMO API doesn't allow date ranges that cross calendar years
                # Fetch transactions by calendar year
                all_account_transactions = []
                
                current_date = datetime.now()
                current_year = current_date.year
                
                print("\n" + "="*60)
                print("PAUSED: Ready to fetch transactions")
                print("1. Open browser DevTools (F12)")
                print("2. Go to Network tab")
                print("3. Ensure 'Preserve log' is checked")
                print("4. Press Enter here to start the API calls")
                print("="*60)
                input("Press Enter to continue...")
                
                # Fetch current year (from Jan 1 to today)
                from_date_str = f"{current_year}-01-01"
                to_date_str = current_date.strftime("%Y-%m-%d")
                
                print(f"  Fetching {current_year}: {from_date_str} to {to_date_str}...")
                transactions_current = self._fetch_transactions_from_api(from_date_str, to_date_str)
                all_account_transactions.extend(transactions_current)
                time.sleep(1)
                
                # Fetch previous year (full year)
                prev_year = current_year - 1
                from_date_str = f"{prev_year}-01-01"
                to_date_str = f"{prev_year}-12-31"
                
                print(f"  Fetching {prev_year}: {from_date_str} to {to_date_str}...")
                transactions_prev = self._fetch_transactions_from_api(from_date_str, to_date_str)
                all_account_transactions.extend(transactions_prev)
                
                print(f"  Total transactions for this account: {len(all_account_transactions)}")
                
                print("\n" + "="*60)
                print("PAUSED: API calls completed")
                print("You can now:")
                print("1. Open browser DevTools (F12)")
                print("2. Go to Network tab")
                print("3. Look for the API calls to see request/response")
                print("4. Check what went wrong (if anything)")
                print("="*60)
                input("Press Enter when ready to continue...")
                
                # Add account info to each transaction
                for txn in all_account_transactions:
                    txn['Account Name'] = account_name
                    if 'Unique Account ID' not in txn or txn['Unique Account ID'] == 'BMO':
                        txn['Unique Account ID'] = f"BMO-{account_number}"
                
                all_transactions.extend(all_account_transactions)
                
                # Navigate back to accounts list for next account
                if idx < len(account_links):
                    print("Returning to accounts list...")
                    self.page.goto("https://www1.bmo.com/banking/digital/accounts", wait_until="networkidle")
                    time.sleep(2)
                    
            except Exception as e:
                print(f"Error processing account {account_name}: {e}")
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
            List of dicts with 'name' and 'number' keys
        """
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
                            
                            if (name && number) {
                                accounts.push({ name, number });
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

    def _fetch_transactions_from_api(self, from_date: str, to_date: str) -> List[Dict[str, Any]]:
        """Fetch transactions from BMO REST API.
        
        Args:
            from_date: Start date in YYYY-MM-DD format
            to_date: End date in YYYY-MM-DD format
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
                return []
                
            if not result.get("ok"):
                print(f"API error: {result.get('status')}")
                print(f"Response: {result.get('text', '')[:500]}")
                return []
                
            json_response = json.loads(result.get("text", "{}"))
            return self._parse_transaction_response(json_response)
            
        except Exception as e:
            print(f"Error fetching transactions: {e}")
            import traceback
            traceback.print_exc()
            return []

    def _parse_transaction_response(self, json_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Parse BMO API JSON response and normalize to standard format.
        
        Args:
            json_data: Raw JSON response from BMO API
            
        Returns:
            List of normalized transaction dictionaries
        """
        transactions = []
        
        # Extract account number for unique account ID
        account_number = "BMO"  # Default
        try:
            account_detail = json_data.get('accountDetails', {}).get('accountDetail', {})
            account_number = account_detail.get('accountNumber', 'BMO')
            # Use last 4 digits for privacy
            if len(account_number) > 4:
                account_number = f"BMO-{account_number[-4:]}"
        except Exception:
            pass
        
        # Get posted transactions
        posted_txns = json_data.get('postedTransactions', {}).get('transactions', [])
        
        print(f"Found {len(posted_txns)} posted transactions")
        
        for txn in posted_txns:
            # Extract fields
            txn_date = txn.get('txnDate', '')  # Transaction date (YYYY-MM-DD)
            post_date = txn.get('postDate', '')  # Posted date (YYYY-MM-DD)
            description = txn.get('descr', '')
            merchant_name = txn.get('merchantName', '')
            amount_val = float(txn.get('amount', 0))
            txn_indicator = txn.get('txnIndicator', 'DR')  # DR = Debit, CR = Credit
            txn_id = txn.get('transactionId', '')
            txn_ref = txn.get('txnRefNumber', '')
            txn_code = txn.get('txnCode', '')
            
            # Use posted date as the primary date (when it cleared)
            date = TransactionNormalizer.normalize_date(post_date if post_date else txn_date)
            
            # Clean description
            description = TransactionNormalizer.clean_description(description)
            
            # Determine signed amount
            # DR (Debit) = money spent (negative)
            # CR (Credit) = payment/refund (positive)
            if txn_indicator == 'DR':
                amount = -amount_val
            else:
                amount = amount_val
            
            # Use BMO's transaction ID, or generate one if missing
            unique_id = txn_id if txn_id else TransactionNormalizer.generate_transaction_id(
                date, amount, description, account_number
            )
            
            # Build standardized transaction
            transaction = {
                'Unique Account ID': account_number,
                'Unique Transaction ID': unique_id,
                'Date': date,
                'Description': description,
                'Amount': amount,
                'Currency': 'CAD',
                'Category': '',
                # BMO-specific fields
                'Transaction Date': txn_date,
                'Post Date': post_date,
                'Merchant Name': merchant_name,
                'Transaction Indicator': txn_indicator,
                'Transaction Code': txn_code,
                'Reference Number': txn_ref,
            }
            
            transactions.append(transaction)
        
        # Also get pending transactions if any
        pending_txns = json_data.get('pendingTransactions', {}).get('transactions', [])
        if pending_txns:
            print(f"Found {len(pending_txns)} pending transactions (not included in output)")
            # Note: We're not including pending transactions as they haven't cleared yet
            # If you want to include them, you can parse them similarly
        
        print(f"Parsed {len(transactions)} posted transactions")
        return transactions
