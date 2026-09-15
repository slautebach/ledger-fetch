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
        """
        Navigate to login page and wait for manual login.
        
        This method handles:
        1. Navigating to the BMO login page.
        2. Waiting for the user to complete the authentication process.
        3. Detecting successful login by monitoring URL changes (waiting for redirection to accounts page).
        """
        print("Navigating to BMO login page...")
        # Forward console logs to Python stdout for debugging
        if getattr(self.config.ledger_fetch, 'debug', False):
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

        # Base class already fetched accounts into the cache; only re-scrape
        # if the cache is empty (e.g. accounts step failed there).
        accounts = list(self.accounts_cache.values()) or self.fetch_accounts()
        
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
                # Arm the sniffer BEFORE clicking: the app fires its transaction
                # request the instant the details route activates, harvesting
                # valid device-bound headers we cannot synthesize ourselves.
                sniffer = self._arm_api_sniffer()

                # Click on the account to open it
                if not self._click_account(idx - 1):  # 0-indexed
                    print("  Could not open account details page; skipping account.")
                    self._disarm_api_sniffer(sniffer)
                    continue

                exchange = self._await_api_exchange(sniffer, timeout_s=90)
                if exchange is None:
                    print("  Warning: app did not fire its transaction API call; "
                          "cannot build request template. Skipping account.")
                    continue
                template, initial_text = exchange
                print("  Captured app's API request template (device headers included).")

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

                # The captured call already covers the most recent window
                try:
                    initial_json = json.loads(initial_text)
                    initial_txns = self._parse_transaction_response(initial_json, account)
                    print(f"  Initial (app-fetched window): {len(initial_txns)} transactions")
                    all_account_transactions.extend(initial_txns)
                except Exception as e:
                    print(f"  Could not parse app's initial response: {e}")
                
                current_date = datetime.now()
                current_year = current_date.year
                
                bank_config = self.config.ledger_fetch.banks.get(self.get_bank_name())
                days_to_fetch = getattr(bank_config, 'days_to_fetch', 365) if bank_config else 365
                years_to_fetch = (days_to_fetch // 365) + 1
                
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
                        transactions_year = self._replay_api_call(template, from_date_str, to_date_str, account)
                        all_account_transactions.extend(transactions_year)
                    except Exception as e:
                        print(f"  Error fetching {target_year}: {e}")
                    time.sleep(1)
                
                all_account_transactions = self._dedupe(all_account_transactions)
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
        """
        Extract credit card account information from the accounts list page.
        
        Because BMO's page structure is complex and uses shadow DOM/Angular components,
        we inject JavaScript to traverse the DOM and extract account details directly.
        
        Returns:
            List[Dict]: List of dicts with 'name', 'number', and 'balance' keys.
        """
        # We rely on executing JavaScript in the browser to robustly traverse the DOM,
        # as the page structure is complex and dynamic (Angular/React).
        # Reuse the scraping logic
        # Retry up to 5 times (15 seconds total)
        for attempt in range(5):
            try:
                accounts = self.page.evaluate(r"""
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
        """Click on a credit card account by index and wait for the details page.

        The accounts list is an Angular app whose click handlers bind shortly
        after the DOM renders, so a click fired the moment the element appears
        can be silently swallowed. We settle, click, and verify the router
        actually navigated to /account-details/, retrying once if not.

        Args:
            index: 0-based index of the account to click

        Returns:
            True if the details page was reached, False otherwise.
        """
        time.sleep(2)  # let Angular bind handlers after render

        click_js = """
            (index) => {
                const accountItems = document.querySelectorAll('app-accounts-list-group-item');
                const creditCardItems = [];

                accountItems.forEach(item => {
                    const container = item.closest('.account-container');
                    if (!container) return;

                    const heading = container.querySelector('app-accounts-list-category-heading');
                    if (!heading || !heading.textContent.toLowerCase().includes('credit card')) return;

                    creditCardItems.push(item);
                });

                if (creditCardItems[index]) {
                    const clickableRow = creditCardItems[index].querySelector('.account-row');
                    if (clickableRow) {
                        clickableRow.click();
                        return true;
                    }
                    creditCardItems[index].click();
                    return true;
                }
                return false;
            }
        """

        for attempt in (1, 2):
            try:
                clicked = self.page.evaluate(click_js, index)
            except Exception as e:
                print(f"Error clicking account: {e}")
                return False
            if not clicked:
                print("Account row not found for clicking.")
                return False

            # Wait for the SPA router to land on the details page
            deadline = time.time() + 20
            while time.time() < deadline:
                if "account-details" in self.page.url:
                    print(f"  Details page reached: {self.page.url.split('?')[0]}")
                    return True
                time.sleep(1)

            print(f"  Click attempt {attempt}: still at {self.page.url.split('?')[0]}; retrying...")

        return False

    def _arm_api_sniffer(self) -> Dict:
        """
        Arm request/response listeners for the app's own transaction API call.

        Must be called BEFORE clicking into the account: the app fires its
        transient-cache request at the moment the details route activates, so
        listeners armed after detecting the URL change would miss it.
        """
        api_match = "transient-extended-credit-card-data/get"
        state = {"url": None, "headers": None, "body": {}, "response_text": None,
                 "handlers": []}

        def on_request(request):
            if api_match in request.url and request.method == "POST":
                state["url"] = request.url
                # Playwright headers are lower-cased; drop cookie so the browser
                # attaches fresh ones on replay
                state["headers"] = {
                    k: v for k, v in request.headers.items() if k.lower() != "cookie"
                }
                try:
                    state["body"] = json.loads(request.post_data)
                except (TypeError, ValueError):
                    state["body"] = {}

        def on_response(response):
            if api_match in response.url:
                try:
                    state["response_text"] = response.text()
                except Exception:
                    pass

        self.page.on("request", on_request)
        self.page.on("response", on_response)
        state["handlers"] = [("request", on_request), ("response", on_response)]
        return state

    def _disarm_api_sniffer(self, state: Dict):
        for event, handler in state.get("handlers", []):
            try:
                self.page.remove_listener(event, handler)
            except Exception:
                pass

    def _await_api_exchange(self, state: Dict, timeout_s: float = 90):
        """
        Wait for the sniffer to capture the app's transaction API exchange.

        Returns:
            (template, response_text) or None if the app never made the call.
        """
        # The transactions view initializes lazily; scrolling encourages the
        # render. No clicks - they can reset the app's in-flight init.
        nudge_js = "() => { window.scrollTo(0, document.body.scrollHeight); }"

        try:
            deadline = time.time() + timeout_s
            next_nudge = time.time() + 5
            while time.time() < deadline and state["headers"] is None:
                if time.time() >= next_nudge:
                    try:
                        self.page.evaluate(nudge_js)
                    except Exception:
                        pass
                    next_nudge = time.time() + 7
                time.sleep(0.5)
        finally:
            self._disarm_api_sniffer(state)

        if state["headers"] is None:
            return None

        # Response may arrive a beat after the request
        resp_deadline = time.time() + 10
        while time.time() < resp_deadline and state["response_text"] is None:
            time.sleep(0.5)

        template = {
            "url": state["url"],
            "headers": state["headers"],
            "body": state.get("body", {}),
        }
        return template, state.get("response_text") or ""

    def _replay_api_call(self, template: Dict, from_date: str, to_date: str, account: Account) -> List[Transaction]:
        """
        Replay the captured transaction API request with a different date range.

        Keeps device-bound headers verbatim; refreshes per-request IDs
        (x-request-id, x-fapi-interaction-id, x-original-request-time).
        """
        try:
            from .utils import with_retries

            def do_evaluate():
                return self.page.evaluate("""
                    async (params) => {
                        const uuid = () => 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => {
                            const r = Math.random() * 16 | 0;
                            const v = c === 'x' ? r : (r & 0x3 | 0x8);
                            return v.toString(16);
                        });
                        const headers = Object.assign({}, params.headers);
                        if ('x-request-id' in headers) {
                            headers['x-request-id'] = 'REQ_' + Array.from({length: 16},
                                () => Math.floor(Math.random() * 16).toString(16)).join('');
                        }
                        if ('x-fapi-interaction-id' in headers) {
                            headers['x-fapi-interaction-id'] = uuid();
                        }
                        headers['x-original-request-time'] = new Date().toUTCString();

                        const body = Object.assign({}, params.body, {
                            fromDate: params.fromDate,
                            toDate: params.toDate
                        });

                        try {
                            const resp = await fetch(params.url, {
                                method: 'POST',
                                headers: headers,
                                credentials: 'include',
                                body: JSON.stringify(body)
                            });
                            const text = await resp.text();
                            return {ok: resp.ok, status: resp.status, text: text};
                        } catch (e) {
                            return {error: e.message};
                        }
                    }
                """, {
                    "url": template["url"],
                    "headers": template["headers"],
                    "body": template["body"],
                    "fromDate": from_date,
                    "toDate": to_date,
                })

            result = with_retries(
                do_evaluate,
                should_retry=lambda r: (isinstance(r, dict)
                                        and r.get("status") in (429, 500, 502, 503, 504)),
                attempts=3, base_delay=3.0,
                desc=f"BMO replay {from_date}",
            )

            if "error" in result:
                print(f"  API fetch error: {result['error']}")
                return []

            if not result.get("ok"):
                print(f"  API error status: {result.get('status')}")
                preview = result.get("text", "")[:500]
                print(f"  Response preview: {preview}")
                return []

            json_response = json.loads(result.get("text", "{}"))
            return self._parse_transaction_response(json_response, account)

        except Exception as e:
            print(f"  Error fetching transactions: {e}")
            import traceback
            traceback.print_exc()
            return []

    def _dedupe(self, transactions: List[Transaction]) -> List[Transaction]:
        """Remove duplicate transactions by unique ID (keep first occurrence)."""
        seen = {}
        for t in transactions:
            if t.unique_transaction_id not in seen:
                seen[t.unique_transaction_id] = t
        removed = len(transactions) - len(seen)
        if removed:
            print(f"  Removed {removed} duplicate transaction(s) within account")
        return list(seen.values())

    def _parse_transaction_response(self, json_data: Dict[str, Any], account: Account) -> List[Transaction]:
        """Parse BMO API JSON response and normalize to standard format."""
        transactions = []
        
        # Get posted transactions
        posted_txns = json_data.get('postedTransactions', {}).get('transactions', [])
        print(f"Found {len(posted_txns)} posted transactions")
        
        for txn_data in posted_txns:
            txn = self._create_transaction_from_dict(txn_data, account, is_pending=False)
            transactions.append(txn)
        
        # Get pending transactions
        pending_txns = json_data.get('pendingTransactions', {}).get('transactions', [])
        if pending_txns:
            print(f"Found {len(pending_txns)} pending transactions")
            for txn_data in pending_txns:
                txn = self._create_transaction_from_dict(txn_data, account, is_pending=True)
                transactions.append(txn)
        
        print(f"Parsed {len(transactions)} total transactions")
        return transactions

    def _create_transaction_from_dict(self, txn_data: Dict[str, Any], account: Account, is_pending: bool) -> Transaction:
        """Helper to create a Transaction object from a raw BMO dictionary."""
        # Extract fields
        txn_date = txn_data.get('txnDate', '')  # Transaction date (YYYY-MM-DD)
        post_date = txn_data.get('postDate', '')  # Posted date (YYYY-MM-DD)
        description = txn_data.get('descr', '')
        merchant_name = txn_data.get('merchantName', '')
        amount_val = float(txn_data.get('amount', 0))
        txn_indicator = txn_data.get('txnIndicator', 'DR')  # DR = Debit, CR = Credit
        txn_id = txn_data.get('transactionId', '')
        
        # Use posted date as the primary date if available, otherwise transaction date
        date_str = post_date if post_date else txn_date
        # If pending, we might only have txnDate
        if not date_str and is_pending:
             date_str = datetime.now().strftime('%Y-%m-%d') # Fallback if absolutely no date

        date = TransactionNormalizer.normalize_date(date_str)
        
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
        txn.date = date
        txn.description = description
        txn.payee_name = payee_name
        txn.amount = amount
        txn.currency = account.currency
        txn.is_pending = is_pending
        
        # Ensure status is captured in raw data for importer to see
        txn.raw_data['Status'] = 'Pending' if is_pending else 'Posted'
        
        # BMO-specific fields in raw_data
        txn.raw_data['Transaction Date'] = txn_date
        txn.raw_data['Post Date'] = post_date
        txn.raw_data['Merchant Name'] = merchant_name
        txn.raw_data['Transaction Indicator'] = txn_indicator
        
        return txn

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
