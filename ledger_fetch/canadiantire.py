import time
import json
import uuid
import calendar
from datetime import datetime, timedelta
from typing import List, Dict, Any
from .base import BankDownloader
from .base import BankDownloader
from .utils import TransactionNormalizer
from .models import Transaction, Account, AccountType

class CanadianTireDownloader(BankDownloader):
    """
    Canadian Tire Financial Services (CTFS) Transaction Downloader.
    
    This downloader handles the retrieval of transactions from the CTFS website.
    It employs a sophisticated API interaction strategy because CTFS does not have a 
    simple date-range transaction API. Instead, it is statement-based.
    
    Workflow:
    1.  Interactive Login: The user logs in manually.
    2.  Token Extraction: It extracts the `transientReference` (a temporary account handle) 
        and `csrftoken` from `document.cookie` and API responses.
    3.  Statement Extrapolation: Since the API requires exact statement dates, and scraping 
        them is unreliable, the script finds the latest statement date and then mathematically 
        extrapolates previous statement dates (monthly) to cover the requested date range.
    4.  Statement Fetching: It iterates through these calculated dates and calls 
        `/dash/v1/account/retrieveTransactions` for each one.
    """

    def get_bank_name(self) -> str:
        return "canadiantire"

    def login(self):
        """
        Navigate to login page and wait for manual login.
        
        This handles the initial authentication. Note that CTFS often requires
        SMS or Email 2FA for new browser sessions. The script waits indefinitely
        (up to 5 minutes) for the user to complete this challenge and arrive at 
        the account details page.
        """
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
        target_url = "https://www.ctfs.com/content/dash/en/private/Details.html#!/view?tab=account-details"
        if target_url in self.page.url:
            print("Already on account details page. Skipping navigation.")
            return

        print("Navigating to account details page...")
        try:
            self.page.goto(target_url, wait_until="domcontentloaded", timeout=60000)
            time.sleep(5)
        except Exception as e:
            print(f"Warning: Could not auto-navigate (might be already there): {e}")

    def fetch_accounts(self) -> List[Account]:
        """
        Fetch accounts from profile and account API.
        
        CTFS splits account data across two endpoints:
        1. `retrieveProfile`: Returns the list of cards, partial account numbers, and status.
           We use this to discover what accounts exist.
        2. `retrieveAccount`: Returns detailed balances, due dates, and statement dates.
           We call this for each account found in step 1.
           
        Returns:
            List[Account]: List of fully populated Account objects.
        """
        print("Fetching profile to get accounts...")
        profile_url = "https://www.ctfs.com/bank/v1/profile/retrieveProfile"
        accounts = []
        
        try:
            # 1. Get Profile (for card list and references)
            result = self.page.evaluate("""
                async (url) => {
                    try {
                        const cookies = document.cookie.split(';').reduce((acc, cookie) => {
                            const [key, value] = cookie.trim().split('=');
                            acc[key] = value;
                            return acc;
                        }, {});
                        const csrfToken = cookies['csrftoken'] || '';

                        const headers = {
                            'Content-Type': 'application/json',
                            'X-Requested-With': 'XMLHttpRequest'
                        };
                        if (csrfToken) headers['csrftoken'] = csrfToken;
                        
                        const response = await fetch(url, {
                            method: 'POST', fields: headers, credentials: 'include', body: '{}', headers: headers
                        });
                        
                        return { ok: response.ok, status: response.status, text: await response.text(), csrf: csrfToken };
                    } catch (e) { return { error: e.message }; }
                }
            """, profile_url)

            if "error" in result:
                print(f"Profile fetch error: {result['error']}")
                return []
            
            json_response = json.loads(result.get("text", "{}"))
            cards = json_response.get("registeredCards", [])
            csrf_token = result.get("csrf", "")

            for card in cards:
                ref = card.get("transientReference")
                if not ref: continue
                
                # Basic info
                display_name = card.get("displayName", "Canadian Tire Options Mastercard")
                last_4 = card.get("last4Digits", "")
                unique_id = f"CTFS-{last_4}" if last_4 else f"CTFS-{uuid.uuid4()}"
                
                acc = Account(card, unique_id)
                acc.account_name = display_name
                acc.account_number = last_4
                acc.type = AccountType.CREDIT_CARD
                acc.currency = "CAD"
                
                # 2. Fetch Detailed Account Info (for balances and dates)
                # API: https://www.ctfs.com/dash/v1/account/retrieveAccount
                print(f"Fetching details for {unique_id}...")
                details = self._fetch_account_details(ref, csrf_token)
                
                if details:
                    # Update Unique Account ID from API
                    api_account_id = details.get("accountId")
                    if api_account_id:
                        acc.unique_account_id = f"CTFS-{api_account_id}"

                    # Current Balance
                    # Use API currentBalanceAmt
                    acc.current_balance = float(details.get("currentBalanceAmt", 0.0))
                    
                    # Statement Info
                    # statementDueAmt -> Statement Balance (User preferred statementBalanceDueAmt)
                    acc.statement_balance = float(details.get("statementBalanceDueAmt", 0.0))
                    
                    # statementAmtFullPmt -> Remaining Balance Due (Per User Instruction)
                    acc.remaining_balance_due = float(details.get("statementAmtFullPmt", 0.0))
                    
                    # paymentDueDate -> Due Date
                    due_date = details.get("paymentDueDate", "")
                    # paymentDueDate -> Due Date
                    due_date = details.get("paymentDueDate", "")
                    if due_date:
                        acc.payment_due_date = TransactionNormalizer.normalize_date(due_date)

                    # lastStatementDate -> Anchor for transaction fetching
                    last_stmt = details.get("lastStatementDate", "")
                    if last_stmt:
                        # Store as string YYYY-MM-DD
                        acc.last_statement_date = last_stmt
                        
                    print(f"  Found account: {acc.unique_account_id}")
                    print(f"  Balance: ${acc.current_balance}")
                    print(f"  Statement Balance: ${acc.statement_balance}")
                    print(f"  Remaining Due: ${acc.remaining_balance_due}")
                    print(f"  Due Date: {acc.payment_due_date}")
                else:
                     # Fallback to profile balance if API fails
                     bal = card.get("balance") or card.get("currentBalance") or card.get("accountBalance")
                     if bal: acc.current_balance = float(bal)

                accounts.append(acc)

        except Exception as e:
            print(f"Error fetching accounts: {e}")
            import traceback
            traceback.print_exc()

        return accounts

    def _fetch_account_details(self, transient_ref: str, csrf_token: str) -> Dict[str, Any]:
        """Fetch details from retrieveAccount API."""
        api_url = "https://www.ctfs.com/dash/v1/account/retrieveAccount"
        
        try:
            result = self.page.evaluate("""
                async (params) => {
                    try {
                        const headers = {
                            'Content-Type': 'application/json',
                            'X-Requested-With': 'XMLHttpRequest'
                        };
                        if (params.csrf) headers['csrftoken'] = params.csrf;
                        
                        const response = await fetch(params.url, {
                            method: 'POST',
                            headers: headers,
                            credentials: 'include',
                            body: JSON.stringify({ transientReference: params.ref })
                        });
                        return { ok: response.ok, text: await response.text() };
                    } catch (e) { return { error: e.message }; }
                }
            """, {'url': api_url, 'csrf': csrf_token, 'ref': transient_ref})
            
            if result.get("ok"):
                return json.loads(result.get("text", "{}"))
        except:
            pass
        return {}

    def download_transactions(self) -> List[Transaction]:
        """
        Fetch transactions via API.
        
        CTFS requires a `statementDate` to fetch transactions. It does not support arbitrary date ranges.
        
        Strategy:
        1. Discover available statement dates from the UI (drop-down) or API.
        2. If that fails or isn't enough, EXTRAPOLATE past dates. Since statements are monthly,
           we can mathematically calculate likely statement dates based on the 'lastStatementDate'.
        3. Iterate through these dates and call `retrieveTransactions` for each.
        
        Returns:
             List[Transaction]: Aggregated list of transactions from all fetched statements.
        """
        
        # 1. Fetch Accounts
        # 1. Fetch Accounts
        if self.accounts_cache:
            print("Using cached accounts...")
            accounts = list(self.accounts_cache.values())
        else:
            accounts = self.fetch_accounts()
            if accounts:
                self.save_accounts(accounts)
        
        # 2. Get statement dates (Assuming global/current context)
        # Ensure we are on the Details page to find the dropdown
        self.navigate_to_transactions()
        
        # For now, we only process the first account
        target_account = accounts[0]
        
        statement_dates = self._get_statement_dates()
        
        if not statement_dates:
            print("No statement dates found via scraping.")

        # Extrapolate dates based on config
        days = self.config.canadiantire.days_to_fetch
        
        # 1. Find the latest date to start from
        latest_date_str = None
        
        if statement_dates:
            try:
                sorted_dates = sorted(
                    [d for d in statement_dates if d],
                    key=lambda x: datetime.strptime(x, "%Y-%m-%d"),
                    reverse=True
                )
                latest_date_str = sorted_dates[0]
            except:
                pass
        
        # Fallback to account.last_statement_date if scraping failed
        if not latest_date_str and hasattr(target_account, 'last_statement_date') and target_account.last_statement_date:
            print(f"Using API lastStatementDate as anchor: {target_account.last_statement_date}")
            latest_date_str = target_account.last_statement_date

        if latest_date_str:
            try:
                print(f"Latest available statement: {latest_date_str}. Extrapolating {days} days back...")
                # 2. Generate dates
                statement_dates = self._generate_historical_dates(latest_date_str, days)
                print(f"Generated {len(statement_dates)} statement dates.")
            except Exception as e:
                print(f"Error extrapolating dates: {e}.")
        else:
             print("Could not find a valid date to start extrapolation.")
             
        if self.config.debug:
            print(f"DEBUG: latest_date_str: {latest_date_str}")
            print(f"DEBUG: statement_dates: {statement_dates}")

        if not statement_dates:
             return []

        print(f"Fetching transactions for {len(statement_dates)} statement(s)...")
        
        all_transactions = []
        
        print(f"DEBUG: Processing transactions for Account ID: {target_account.unique_account_id}")
        print(f"Processing account: {target_account.account_name} ({target_account.unique_account_id})")
        
        transient_ref = target_account.raw_data.get("transientReference")
        if not transient_ref:
            print("No transient reference for account.")
            return []

        for date in statement_dates:
            txns = self._fetch_transactions_for_statement(date, transient_ref, target_account)
            all_transactions.extend(txns)
            time.sleep(1)
            
        return all_transactions



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

    def _fetch_transactions_for_statement(self, statement_date, transient_ref, account: Account) -> List[Transaction]:
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
            return self._parse_transaction_response(json_response, account)
            
        except Exception as e:
            print(f"Error fetching transactions: {e}")
            return []

    def _parse_transaction_response(self, json_data, account: Account) -> List[Transaction]:
        """Parse API response."""
        transactions = []
        if 'transactions' not in json_data:
            return transactions
            
        for txn_data in json_data['transactions']:
            tran_date = txn_data.get('tranDate', '')
            date = TransactionNormalizer.normalize_date(tran_date)
            
            merchant = txn_data.get('merchant', '')
            description = TransactionNormalizer.clean_description(merchant)
            
            amount_val = float(txn_data.get('amount', 0))
 
            amount = amount_val
                
            # IDs
            ref_num = txn_data.get('referenceNumber', '')
            if ref_num:
                txid_merchant = merchant.replace(' ', '_').replace('#', '').replace('/', '').replace('(', '').replace(')', '')
                # Concatenate reference number and merchant to ensure uniqueness ID for shared refs
                unique_trans_id = f"{ref_num}-{txid_merchant}"
            else:
                unique_trans_id = TransactionNormalizer.generate_transaction_id(date, amount, description, "CTFS")

            trans_type = txn_data.get('type', '')
            
            # Determine if transfer (Payment)
            is_transfer = trans_type == 'PAYMENT'

            payee_name = TransactionNormalizer.normalize_payee(description)

            # Create Transaction
            txn = Transaction(txn_data, account.unique_account_id)
            txn.unique_transaction_id = unique_trans_id
            txn.account_name = account.account_name
            txn.date = date
            txn.description = description

            txn.payee_name = payee_name # Normalized payee
            txn.amount = amount
            txn.currency = 'CAD'
            #txn.is_transfer = is_transfer
            txn.notes = f"Type: {trans_type}, Ref: {ref_num}"
            
            transactions.append(txn)
            
        return transactions

    def _generate_historical_dates(self, start_date_str: str, days_back: int) -> List[str]:
        """
        Generate a list of monthly dates going back in time from a start date.
        
        This is a workaround for the API structure. If we know one valid statement date 
        (e.g., the latest one), we can guess previous ones because they are usually 
        on the same day of the month.
        
        Args:
            start_date_str: The most recent known statement date (YYYY-MM-DD).
            days_back: How far back in time to generate dates for.
            
        Returns:
            List[str]: List of date strings (YYYY-MM-DD).
        """
        try:
            start_date = datetime.strptime(start_date_str, "%Y-%m-%d")
        except ValueError:
            return [start_date_str]
            
        cutoff_date = datetime.now() - timedelta(days=days_back)
        billing_day = start_date.day
        
        dates = []
        current_date = start_date
        
        while current_date >= cutoff_date:
            dates.append(current_date.strftime("%Y-%m-%d"))
            
            # Go back one month
            year = current_date.year
            month = current_date.month - 1
            if month == 0:
                month = 12
                year -= 1
            
            # Clamp day to valid range for the new month (e.g., handle Feb 28/29)
            last_day_of_month = calendar.monthrange(year, month)[1]
            safe_day = min(billing_day, last_day_of_month)
            
            current_date = current_date.replace(year=year, month=month, day=safe_day)
            
        return dates
