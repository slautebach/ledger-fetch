import time
import json
from typing import List, Dict, Any
from datetime import datetime, timedelta
from .base import BankDownloader
from .utils import TransactionNormalizer
from .models import Transaction, Account

class RBCDownloader(BankDownloader):
    """
    RBC (Royal Bank of Canada) Transaction Downloader.
    
    This downloader uses the internal API to fetch transactions directly,
    bypassing the CSV download workflow for better reliability and data quality.
    
    Workflow:
    1.  Interactive Login: The user logs in manually.
    2.  Account Discovery: Fetches list of accounts from `accountListSummary` endpoint.
    3.  Transaction Fetching: Iterates through accounts and fetches transactions via `arrangements/pda` endpoint.
    4.  Normalization: Normalizes JSON data to standard schema.
    """

    def get_bank_name(self) -> str:
        return "rbc"

    def login(self):
        """Navigate to login page and wait for manual login."""
        print("Navigating to RBC online banking login page...")
        self.page.goto("https://www.rbcroyalbank.com/ways-to-bank/online-banking.html")
        
        print("\nWaiting for user to log in to RBC online banking...")
        print("Please complete:")
        print("1. Login process (including any 2FA if required)")
        print("2. Wait until the Dashboard / Accounts Summary page loads")
        
        try:
            # Wait up to 5 minutes for login
            # We wait for the URL to indicate we are inside the app
            self.page.wait_for_url("**/olb/index-en/#/**", timeout=300000) 
            print("Login detected (URL match).")
            # Give it a moment to fully load initial data
            time.sleep(5)
        except Exception:
            print("Warning: Login timeout or URL not matched. Proceeding anyway in case we are already there.")

    def navigate_to_transactions(self):
        """
        No specific navigation needed for API approach, 
        but we ensure we are on a page where API calls are authorized.
        """
        pass

    def fetch_accounts(self) -> List[Account]:
        """Fetch list of accounts from the API."""
        print("Fetching account list...")
        
        url = "https://www1.royalbank.com/sgw5/digital/product-summary-presentation-service-v3/v3/accountListSummary"
        
        try:
            response = self.page.request.get(url)
            if response.status != 200:
                print(f"Error fetching accounts: {response.status} {response.status_text}")
                return []
                
            data = response.json()
            
            accounts = []
            
            # Helper to extract name
            def get_name(acc):
                if not acc: return 'Unknown Account'
                nick = acc.get('nickName')
                if nick: return nick
                prod = acc.get('product') or {}
                return prod.get('productName', 'Unknown Account')

            # Helper to extract currency
            def get_currency(acc):
                if not acc: return 'CAD'
                curr = acc.get('accountCurrency') or {}
                return curr.get('currencyCode', 'CAD')

            # Process Deposit Accounts
            if 'depositAccounts' in data and isinstance(data['depositAccounts'], dict):
                da_list = data['depositAccounts'].get('accounts', [])
                for acc in da_list:
                    if not acc: continue
                    # Use Account Number as Unique ID
                    acc_num = acc.get('accountNumber')
                    if acc_num:
                        unique_id = f"RBC-{acc_num}"
                    else:
                         unique_id = acc.get('encryptedAccountNumber')
                    
                    account = Account(acc, unique_id)
                    
                    # Map Current Balance
                    current_balance = acc.get('currentBalance')
                    if current_balance is not None:
                        try:
                            account.current_balance = float(current_balance)
                        except (ValueError, TypeError):
                            pass
                    account.account_name = get_name(acc)
                    account.account_number = acc.get('accountNumber', '')
                    account.type = 'Deposit'
                    account.currency = get_currency(acc)
                    accounts.append(account)

            # Process Credit Card Accounts
            if 'creditCards' in data and isinstance(data['creditCards'], dict):
                cc_list = data['creditCards'].get('accounts', [])
                for acc in cc_list:
                    if not acc: continue
                    # Use Account Number as Unique ID
                    acc_num = acc.get('accountNumber')
                    if acc_num:
                        unique_id = f"RBC-{acc_num}"
                    else:
                         unique_id = acc.get('encryptedAccountNumber')

                    account = Account(acc, unique_id)

                    # Map Current Balance
                    current_balance = acc.get('currentBalance')
                    if current_balance is not None:
                        try:
                            account.current_balance = float(current_balance)
                        except (ValueError, TypeError):
                            pass
                    account.account_name = get_name(acc)
                    account.account_number = acc.get('accountNumber', '')
                    account.type = 'CreditCard'
                    account.currency = get_currency(acc)
                    accounts.append(account)

            # Process Lines and Loans (Home Line Plan)
            if 'linesLoans' in data and isinstance(data['linesLoans'], dict):
                ll_list = data['linesLoans'].get('accounts', [])
                for acc in ll_list:
                    if not acc: continue
                    acc_num = acc.get('accountNumber')
                    if acc_num:
                        unique_id = f"RBC-{acc_num}"
                    else:
                         unique_id = acc.get('encryptedAccountNumber')

                    account = Account(acc, unique_id)
                    
                    current_balance = acc.get('currentBalance')
                    if current_balance is not None:
                        try:
                            account.current_balance = float(current_balance)
                        except (ValueError, TypeError):
                            pass
                    account.account_name = get_name(acc)
                    account.account_number = acc.get('accountNumber', '')
                    account.type = 'LineLoan'
                    account.currency = get_currency(acc)
                    accounts.append(account)

            # Process Mortgages
            if 'mortgages' in data and isinstance(data['mortgages'], dict):
                mtg_list = data['mortgages'].get('accounts', [])
                for acc in mtg_list:
                    if not acc: continue
                    acc_num = acc.get('accountNumber')
                    if acc_num:
                        unique_id = f"RBC-{acc_num}"
                    else:
                         unique_id = acc.get('encryptedAccountNumber')

                    account = Account(acc, unique_id)
                    
                    current_balance = acc.get('currentBalance')
                    if current_balance is not None:
                        try:
                            account.current_balance = float(current_balance)
                        except (ValueError, TypeError):
                            pass
                    account.account_name = get_name(acc)
                    account.account_number = acc.get('accountNumber', '')
                    account.type = 'Mortgage'
                    account.currency = get_currency(acc)
                    accounts.append(account)

            # Process Investments
            if 'investments' in data and isinstance(data['investments'], dict):
                inv_list = data['investments'].get('accounts', [])
                for acc in inv_list:
                    if not acc: continue
                    acc_num = acc.get('accountNumber')
                    if acc_num:
                        unique_id = f"RBC-{acc_num}"
                    else:
                         unique_id = acc.get('encryptedAccountNumber')

                    account = Account(acc, unique_id)
                    
                    # Investments might be closed or have null balance
                    current_balance = acc.get('currentBalance')
                    if current_balance is not None:
                        try:
                            account.current_balance = float(current_balance)
                        except (ValueError, TypeError):
                            pass
                    
                    account.account_name = get_name(acc)
                    account.account_number = acc.get('accountNumber', '')
                    account.type = 'Investment'
                    account.currency = get_currency(acc)
                    accounts.append(account)
            
            print(f"Found {len(accounts)} accounts.")
            return accounts
            
        except Exception as e:
            print(f"Exception fetching accounts: {e}")
            return []

    def fetch_transactions_for_account(self, account: Account, days: int = 365) -> List[Transaction]:
        """Fetch transactions for a specific account."""
        # Use encrypted account number for API calls if available
        encrypted_id = account.get('encryptedAccountNumber')
            
        if not encrypted_id:
            print(f"Skipping account {account.account_name} (No encrypted account ID)")
            return []
            
        print(f"Fetching transactions for {account.account_name} ({account.account_number})...")
        
        # Determine endpoint based on account type
        service_path = "transactions/pda/account"
        if account.type == 'CreditCard':
            service_path = "transactions/cc/posted/account"
        elif account.type == 'Deposit':
            service_path = "transactions/pda/account"
        else:
            print(f"  Skipping API fetch for {account.type} (relying on CSV fallback)")
            return []
            
        # Base URL (try -dbb first as per HAR)
        base_url = "https://www1.royalbank.com/sgw5/digital/transaction-presentation-service-v3-dbb/v3"
        
        # Query params
        if account.type == 'CreditCard':
             # Use parameters observed in HAR for Credit Cards
             # Note: We might want to fetch both posted and pending, but for now let's stick to posted as per user example
             params = f"billingStatus=posted&txType=postedCreditCard&timestamp={int(time.time()*1000)}"
        elif account.type == 'Deposit':
            params = f"intervalType=DAY&intervalValue={days}&type=ALL&txType=pda&useColtOnly=response"
        else:
             # Should not happen due to check above
             return []
        
        url = f"{base_url}/{service_path}/{encrypted_id}?{params}"
        
        try:
            response = self.page.request.get(url)
            if response.status != 200:
                print(f"  Error fetching transactions: {response.status} {response.status_text}")
                # Try fallback without '-dbb' if 404 or 400
                if response.status in [400, 404]:
                     print("  Retrying with alternative endpoint...")
                     base_url_alt = "https://www1.royalbank.com/sgw5/digital/transaction-presentation-service-v3/v3"
                     url_alt = f"{base_url_alt}/{service_path}/{encrypted_id}?{params}"
                     response = self.page.request.get(url_alt)
                     if response.status != 200:
                         print(f"  Error fetching transactions (retry): {response.status} {response.status_text}")
                         return []
                else:
                    return []
                
            data = response.json()
            
            transactions = []
            
            raw_txns = data.get('transactionList', [])
            if not raw_txns:
                 raw_txns = data.get('transactions', [])
                 
            print(f"  Found {len(raw_txns)} transactions.")
            
            for raw in raw_txns:
                txn = self._process_transaction(raw, account)
                if txn:
                    transactions.append(txn)
                    
            return transactions
            
        except Exception as e:
            print(f"  Exception fetching transactions for {account.account_name}: {e}")
            return []

    def _process_transaction(self, raw: Dict[str, Any], account: Account) -> Transaction:
        """Process a raw transaction dictionary into a Transaction object."""
        try:
            # Date
            date_str = raw.get('bookingDate') or raw.get('transactionDate')
            date = TransactionNormalizer.normalize_date(date_str)
            
            # Amount
            amount = float(raw.get('amount', 0))
            
            # Description
            desc1 = raw.get('description1', '')
            desc2 = raw.get('description2', '')
            description = f"{desc1} {desc2}".strip()
            description = TransactionNormalizer.clean_description(description)
            
            # Payee
            payee_name = TransactionNormalizer.normalize_payee(description)
            
            # ID
            txn_id = raw.get('id')
            unique_trans_id = txn_id if txn_id else TransactionNormalizer.generate_transaction_id(date, amount, description, account.unique_account_id)
            
            # Create Transaction
            txn = Transaction(raw, account.unique_account_id)
            txn.unique_transaction_id = unique_trans_id
            txn.account_name = account.account_name
            txn.date = date
            txn.description = description
            txn.payee = description  # Original (cleaned) description
            txn.payee_name = payee_name # Normalized payee
            txn.amount = amount
            txn.currency = account.currency
            
            return txn
        except Exception as e:
            print(f"Error processing transaction: {e}")
            return None

    def download_transactions(self) -> List[Transaction]:
        """Orchestrate the download process (API + CSV Fallback)."""
        all_transactions = []
        seen_ids = set()
        
        # 1. Try API Fetch
        print("\n--- Starting API Fetch ---")
        accounts = self.fetch_accounts()
        
        if accounts:
            self.save_accounts(accounts)
            for account in accounts:
                txns = self.fetch_transactions_for_account(account)
                for txn in txns:
                    tid = txn.unique_transaction_id
                    if tid and tid not in seen_ids:
                        all_transactions.append(txn)
                        seen_ids.add(tid)
                time.sleep(1)
        else:
            print("No accounts found via API.")

        # 2. Try CSV Download (Fallback/Supplement)
        print("\n--- Starting CSV Download (Fallback) ---")
        try:
            csv_txns = self.download_transactions_csv()
            print(f"Downloaded {len(csv_txns)} transactions via CSV.")
            
            # Build a set of existing Unique Account IDs from API
            api_account_ids = set()
            for acc in accounts:
                # Re-generate the ID logic to match what we do in _process_transaction and CSV parsing
                # Logic: Use Account Number
                num = acc.account_number
                if num:
                    api_account_ids.add(f"RBC-{num}")

            for txn in csv_txns:
                # txn is a Transaction object
                acc_num = txn.raw_data.get('Account Number', '')
                
                # Generate ID for this CSV transaction's account
                # Use Account Number directly
                if acc_num:
                    csv_acc_id = f"RBC-{acc_num}"
                else:
                    csv_acc_id = "RBC-UNKNOWN"
                
                # Check coverage by ID
                if csv_acc_id in api_account_ids:
                    continue # Skip, already covered by API
                
                # Add transaction
                all_transactions.append(txn)
        except Exception as e:
            print(f"CSV Download failed: {e}")
            
        return all_transactions

    def download_transactions_csv(self) -> List[Transaction]:
        """Download CSV and parse it (Legacy Method)."""
        print("Navigating to Account Services page for CSV download...")
        try:
            self.page.goto("https://www1.royalbank.com/sgw1/olb/index-en/#/account-services", timeout=60000)
            self.page.wait_for_load_state('networkidle', timeout=60000)
        except Exception as e:
            print(f"Navigation warning (continuing): {e}")
        time.sleep(2)

        print("Looking for Download Transactions link...")
        
        download_link = None
        if self.page.get_by_text("Download", exact=False).count():
            download_link = self.page.get_by_text("Download", exact=False).first
        elif self.page.locator('a[href*="downloadTransactions"]').count():
            download_link = self.page.locator('a[href*="downloadTransactions"]').first
        
        if not download_link:
            print("Could not find Download link on Account Services page.")
            return []
            
        print("Clicking Download Transactions link...")
        download_link.click()
        self.page.wait_for_load_state('networkidle')
        time.sleep(2)
        
        # Select CSV format
        print("Selecting CSV format...")
        csv_radio = self.page.query_selector('input#Excel')
        if csv_radio:
            csv_radio.click()
            time.sleep(0.5)
            
        # Select "All accounts"
        print("Selecting all accounts...")
        account_select = self.page.query_selector('select#accountInfo')
        if account_select:
            account_select.select_option(index=0)
            time.sleep(0.5)
            
        # Select "All transactions on file"
        print("Selecting all transactions on file...")
        transaction_select = self.page.query_selector('select#transactionDropDown')
        if transaction_select:
            options = transaction_select.query_selector_all('option')
            if options:
                transaction_select.select_option(index=len(options) - 1)
                time.sleep(0.5)
                
        # Click Continue
        print("Downloading transactions...")
        continue_button = self.page.query_selector('a#id_btn_continue')
        if not continue_button:
            print("Continue button not found.")
            return []
            
        try:
            print("Waiting before clicking download...")
            time.sleep(2)
            with self.page.expect_download(timeout=60000) as download_info:
                continue_button.click()
                
            download = download_info.value
            download_path = download.path()
            
            # Parse the downloaded file
            return self._parse_rbc_csv(download_path)
        except Exception as e:
            print(f"Error during file download: {e}")
            return []

    def _parse_rbc_csv(self, csv_path: str) -> List[Transaction]:
        """Parse RBC CSV."""
        import pandas as pd
        transactions = []
        try:
            # RBC CSV format has trailing commas causing extra columns
            # The first column (Account Type) is not quoted, so we need index_col=False
            df = pd.read_csv(csv_path, encoding='latin-1', index_col=False)
            
            # Check format
            if 'Transaction Date' in df.columns:
                # Format 1
                for _, row in df.iterrows():
                    raw_date = row.get('Transaction Date')
                    if pd.isna(raw_date): continue
                    
                    date = TransactionNormalizer.normalize_date(raw_date)
                    
                    # Account info
                    acc_type = str(row.get('Account Type', '')) if not pd.isna(row.get('Account Type')) else ''
                    acc_number = str(row.get('Account Number', '')) if not pd.isna(row.get('Account Number')) else ''
                    account_display = f"{acc_type} {acc_number}".strip()
                    
                    # Description
                    desc1 = str(row.get('Description 1', '')) if not pd.isna(row.get('Description 1')) else ''
                    desc2 = str(row.get('Description 2', '')) if not pd.isna(row.get('Description 2')) else ''
                    description = f"{desc1} {desc2}".strip() if desc2 else desc1
                    description = TransactionNormalizer.clean_description(description)
                    
                    # Amount
                    amount = 0.0
                    currency = 'CAD'
                    
                    # Try CAD$
                    cad_val = row.get('CAD$')
                    if not pd.isna(cad_val) and cad_val != '':
                        try: amount = float(cad_val); currency = 'CAD'
                        except: pass
                    
                    # Try USD$
                    if amount == 0:
                        usd_val = row.get('USD$')
                        if not pd.isna(usd_val) and usd_val != '':
                            try: amount = float(usd_val); currency = 'USD'
                            except: pass
                            
                    # Generate IDs
                    unique_account_id = f"RBC-{acc_number}" if acc_number else "RBC-UNKNOWN"
                    unique_trans_id = TransactionNormalizer.generate_transaction_id(date, amount, description, unique_account_id)
                    
                    # Create Transaction
                    txn = Transaction(row.to_dict(), unique_account_id)
                    txn.unique_transaction_id = unique_trans_id
                    txn.account_name = account_display
                    txn.date = date
                    txn.description = description
                    txn.amount = amount
                    txn.currency = currency
                    
                    # Extra fields
                    txn.raw_data['Cheque Number'] = str(row.get('Cheque Number', '')) if not pd.isna(row.get('Cheque Number')) else ''
                    
                    transactions.append(txn)
                    
            elif 'Date' in df.columns or 'date' in df.columns:
                # Format 2 (Simple export)
                date_col = 'Date' if 'Date' in df.columns else 'date'
                for _, row in df.iterrows():
                    raw_date = row.get(date_col)
                    if pd.isna(raw_date): continue
                    
                    date = TransactionNormalizer.normalize_date(raw_date)
                    description = str(row.get('Description', ''))
                    description = TransactionNormalizer.clean_description(description)
                    
                    debit = row.get('Debit', 0)
                    credit = row.get('Credit', 0)
                    amount = 0.0
                    if not pd.isna(credit) and credit != 0:
                        amount = float(credit)
                    elif not pd.isna(debit) and debit != 0:
                        amount = -float(debit)
                        
                    unique_account_id = "RBC-Simple" # Less info here
                    unique_trans_id = TransactionNormalizer.generate_transaction_id(date, amount, description, unique_account_id)
                    
                    txn = Transaction(row.to_dict(), unique_account_id)
                    txn.unique_transaction_id = unique_trans_id
                    txn.date = date
                    txn.description = description
                    txn.amount = amount
                    txn.currency = 'CAD'
                    
                    transactions.append(txn)


        except Exception as e:
            print(f"Error parsing CSV {csv_path}: {e}")
            
        return transactions


