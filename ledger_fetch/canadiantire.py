import time
import json
import uuid
from datetime import datetime
from typing import List, Dict, Any
from .base import BankDownloader
from .base import BankDownloader
from .utils import TransactionNormalizer
from .models import Transaction, Account

class CanadianTireDownloader(BankDownloader):
    """
    Canadian Tire Financial Services (CTFS) Transaction Downloader.
    
    This downloader handles the retrieval of transactions from the CTFS website.
    It employs a sophisticated API interaction strategy:
    1.  Interactive Login: The user logs in manually.
    2.  Token Extraction: It extracts the `transientReference` and `csrftoken` from 
        the browser's state (cookies and profile API).
    3.  API Calls: It uses these tokens to query the internal API (`/dash/v1/account/retrieveTransactions`)
        for each available statement date.
    """

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
        """Fetch accounts from profile API."""
        print("Fetching profile to get accounts...")
        api_url = "https://www.ctfs.com/bank/v1/profile/retrieveProfile"
        accounts = []
        
        try:
            result = self.page.evaluate("""
                async (url) => {
                    try {
                        // Get CSRF token from cookies
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
                        if (csrfToken) {
                            headers['csrftoken'] = csrfToken;
                        }
                        
                        const response = await fetch(url, {
                            method: 'POST',
                            headers: headers,
                            credentials: 'include',
                            body: '{}'
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
            """, api_url)

            if "error" in result:
                print(f"Profile fetch error: {result['error']}")
                return []
            elif not result.get("ok"):
                print(f"Profile API error: {result.get('status')}")
                return []
            else:
                text = result.get("text", "{}")
                try:
                    json_response = json.loads(text)
                    cards = json_response.get("registeredCards", [])
                    for card in cards:
                        # Extract info
                        ref = card.get("transientReference")
                        display_name = card.get("displayName", "Canadian Tire Options Mastercard")
                        last_4 = card.get("last4Digits", "")
                        
                        if not ref: continue
                        
                        unique_id = f"CTFS-{last_4}" if last_4 else f"CTFS-{uuid.uuid4()}"
                        
                        acc = Account(card, unique_id)
                        acc.account_name = display_name
                        acc.account_number = last_4
                        acc.type = "Credit Card"
                        acc.currency = "CAD"
                        
                        # Map Current Balance
                        # Check various potential keys
                        balance = card.get("balance") or card.get("currentBalance") or card.get("accountBalance")
                        if balance is not None:
                            try:
                                acc.current_balance = float(balance)
                            except (ValueError, TypeError):
                                pass
                        
                        accounts.append(acc)
                        
                except json.JSONDecodeError as e:
                    print(f"Error parsing profile JSON: {e}")
                    
        except Exception as e:
            print(f"Error fetching accounts: {e}")

        # Scrape Current Balance from Summary Page
        if accounts:
            print("Navigating to Summary page to scrape balance...")
            try:
                self.page.goto("https://www.ctfs.com/content/dash/en/private/Summary.html", wait_until="domcontentloaded")
                self.page.wait_for_selector(".balance-section .current .amount", timeout=15000)
                
                balance_text = self.page.inner_text(".balance-section .current .amount")
                # Expected format: "$1,584.55"
                # Remove '$', ',' and whitespace
                clean_balance = balance_text.replace('$', '').replace(',', '').strip()
                # Handle potential newlines if any
                clean_balance = clean_balance.split('\n')[0].strip()
                
                current_balance = float(clean_balance)
                print(f"Scraped current balance: {current_balance}")
                
                # Assign to the first account
                accounts[0].current_balance = current_balance
                
            except Exception as e:
                print(f"Warning: Could not scrape balance from Summary page: {e}")
            finally:
                # Ensure we return to the details page for transaction fetching
                self.navigate_to_transactions()
            
        return accounts

    def download_transactions(self) -> List[Transaction]:
        """Fetch transactions via API."""
        
        # 1. Fetch Accounts
        accounts = self.fetch_accounts()
        if not accounts:
            print("No accounts found.")
            # Fallback to legacy single-ref method if needed? 
            # But fetch_accounts implements the same logic.
            return []
            
        self.save_accounts(accounts)
        
        # 2. Get statement dates (Assuming global/current context)
        statement_dates = self._get_statement_dates()
        if not statement_dates:
            print("No statement dates found.")
            return []
            
        print(f"Fetching transactions for {len(statement_dates)} statement(s)...")
        
        all_transactions = []
        
        # For now, we only process the first account because statement_dates might be tied to UI context
        # and we don't know how to switch accounts yet.
        # But we pass the account object to _fetch_transactions_for_statement
        target_account = accounts[0]
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
            trans_type = txn_data.get('type', '')
            
            # Signed amount
            if trans_type == 'PURCHASE':
                amount = -amount_val
            else:
                amount = amount_val
                
            # IDs
            ref_num = txn_data.get('referenceNumber', '')
            unique_trans_id = ref_num if ref_num else TransactionNormalizer.generate_transaction_id(date, amount, description, "CTFS")
            
            # Determine if transfer (Payment)
            is_transfer = trans_type == 'PAYMENT'

            payee_name = TransactionNormalizer.normalize_payee(description)

            # Create Transaction
            txn = Transaction(txn_data, account.unique_account_id)
            txn.unique_transaction_id = unique_trans_id
            txn.account_name = account.account_name
            txn.date = date
            txn.description = description
            txn.payee = description # Original (cleaned) description
            txn.payee_name = payee_name # Normalized payee
            txn.amount = amount
            txn.currency = 'CAD'
            txn.is_transfer = is_transfer
            txn.notes = f"Type: {trans_type}, Ref: {ref_num}"
            
            transactions.append(txn)
            
        return transactions
