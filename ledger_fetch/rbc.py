import time
import json
from typing import List, Dict, Any
from datetime import datetime, timedelta
from .base import BankDownloader
from .utils import TransactionNormalizer

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

    def get_accounts(self) -> List[Dict[str, Any]]:
        """Fetch list of accounts from the API."""
        print("Fetching account list...")
        
        url = "https://www1.royalbank.com/sgw5/digital/product-summary-presentation-service-v3/v3/accountListSummary"
        
        try:
            response = self.page.request.get(url)
            if response.status != 200:
                print(f"Error fetching accounts: {response.status} {response.status_text}")
                return []
                
            data = response.json()
            
            # Debug logging removed

                
            accounts = []
            
            # Helper to extract name
            def get_name(acc):
                nick = acc.get('nickName')
                if nick: return nick
                prod = acc.get('product', {})
                return prod.get('productName', 'Unknown Account')

            # Helper to extract currency
            def get_currency(acc):
                curr = acc.get('accountCurrency', {})
                return curr.get('currencyCode', 'CAD')

            # Process Deposit Accounts
            if 'depositAccounts' in data and isinstance(data['depositAccounts'], dict):
                da_list = data['depositAccounts'].get('accounts', [])
                for acc in da_list:
                    accounts.append({
                        'id': acc.get('encryptedAccountNumber'),
                        'name': get_name(acc),
                        'number': acc.get('accountNumber', ''),
                        'type': 'Deposit',
                        'currency': get_currency(acc)
                    })

            # Process Credit Card Accounts
            # JSON key is 'creditCards', not 'creditCardAccounts'
            if 'creditCards' in data and isinstance(data['creditCards'], dict):
                cc_list = data['creditCards'].get('accounts', [])
                for acc in cc_list:
                    accounts.append({
                        'id': acc.get('encryptedAccountNumber'),
                        'name': get_name(acc),
                        'number': acc.get('accountNumber', ''),
                        'type': 'CreditCard',
                        'currency': get_currency(acc)
                    })
            
            print(f"Found {len(accounts)} accounts.")
            return accounts
            
        except Exception as e:
            print(f"Exception fetching accounts: {e}")
            return []

    def fetch_transactions_for_account(self, account: Dict[str, Any], days: int = 365) -> List[Dict[str, Any]]:
        """Fetch transactions for a specific account."""
        acc_id = account['id']
        if not acc_id:
            print(f"Skipping account {account['name']} (No ID)")
            return []
            
        print(f"Fetching transactions for {account['name']} ({account['number']})...")
        
        # Determine endpoint based on account type
        # Deposit: .../transactions/pda/account/{id}
        # CreditCard: .../transactions/cc/posted/account/{id}
        
        service_path = "transactions/pda/account"
        if account.get('type') == 'CreditCard':
            service_path = "transactions/cc/posted/account"
            
        # Base URL (try -dbb first as per HAR)
        base_url = "https://www1.royalbank.com/sgw5/digital/transaction-presentation-service-v3-dbb/v3"
        
        # Query params
        # Note: For CC, we might need different params, but let's try the standard date range first.
        # If it fails, we might need to look at 'billingStatus' etc.
        params = f"intervalType=DAY&intervalValue={days}&type=ALL"
        if account.get('type') == 'Deposit':
            params += "&txType=pda&useColtOnly=response"
        
        url = f"{base_url}/{service_path}/{acc_id}?{params}"
        
        try:
            response = self.page.request.get(url)
            if response.status != 200:
                print(f"  Error fetching transactions: {response.status} {response.status_text}")
                # Try fallback without '-dbb' if 404
                if response.status == 404:
                     print("  Retrying with alternative endpoint...")
                     base_url_alt = "https://www1.royalbank.com/sgw5/digital/transaction-presentation-service-v3/v3"
                     url_alt = f"{base_url_alt}/{service_path}/{acc_id}?{params}"
                     response = self.page.request.get(url_alt)
                     if response.status != 200:
                         print(f"  Error fetching transactions (retry): {response.status} {response.status_text}")
                         return []
                else:
                    return []
                
            data = response.json()
            
            # Debug logging removed


            transactions = []
            
            # Debug: Print keys to help identify structure if 'transactions' is missing
            if 'transactions' not in data and 'transactionList' not in data:
                print(f"  DEBUG: 'transactions' key not found. Keys: {list(data.keys())}")
            
            # The transactions list is usually under 'transactions' key
            # Update: Debugging showed it is 'transactionList'
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
            print(f"  Exception fetching transactions for {account['name']}: {e}")
            return []

    def _process_transaction(self, raw: Dict[str, Any], account: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize raw API transaction data."""
        try:
            # Extract Date
            # Format usually: "2023-10-27"
            date_str = raw.get('bookingDate') or raw.get('transactionDate')
            if not date_str:
                return None
            
            date = TransactionNormalizer.normalize_date(date_str)
            
            # Extract Description
            desc_raw = raw.get('description', '')
            if isinstance(desc_raw, list):
                desc = " ".join(desc_raw)
            else:
                desc = str(desc_raw)

            # Sometimes there are additional description fields
            if raw.get('description2'):
                desc += " " + str(raw.get('description2'))
            
            description = TransactionNormalizer.clean_description(desc)
            
            # Extract Amount
            # API usually provides 'amount' as a number, negative for debit?
            # Need to check HAR. Usually 'amount' is absolute and 'creditDebitIndicator' tells sign.
            # Or 'amount' is signed.
            # Let's assume standard banking API: amount is absolute, creditDebitIndicator is 'CRDT' or 'DBIT'
            amount = float(raw.get('amount', 0))
            indicator = raw.get('creditDebitIndicator', '')
            
            if indicator == 'DBIT':
                amount = -abs(amount)
            elif indicator == 'CRDT':
                amount = abs(amount)
            else:
                # Fallback if indicator is missing, check if amount is already signed
                # If not, we might need to guess based on transaction type, but usually indicator exists.
                pass
            
            # Generate IDs
            unique_account_id = f"RBC-{account['number'][-4:]}" if account['number'] else f"RBC-{account['id'][:8]}"
            unique_trans_id = raw.get('id') # API usually provides a unique ID
            if not unique_trans_id:
                unique_trans_id = TransactionNormalizer.generate_transaction_id(date, amount, description, unique_account_id)
            
            payee = TransactionNormalizer.normalize_payee(description)
            
            # Is Transfer?
            is_transfer = False
            # Check transaction type or description
            # raw.get('type') might be 'T' or 'Transfer'
            if 'transfer' in description.lower() or raw.get('type') == 'Transfer':
                is_transfer = True

            # Extract additional fields
            running_balance = raw.get('runningBalance', '')
            cheque_number = raw.get('checkSerialNumber') or ''
            
            # Improve Notes with Interac details
            notes = raw.get('userDescription', '') or ''
            interac = raw.get('interac', {})
            if interac and isinstance(interac, dict):
                sender = interac.get('senderName')
                if sender:
                    if notes: notes += f" (Sender: {sender})"
                    else: notes = f"Sender: {sender}"
            
            # Improve Payee with Merchant Name
            merchant_name = raw.get('merchantName')
            if merchant_name:
                payee = TransactionNormalizer.normalize_payee(merchant_name)
            
            merchant_city = raw.get('merchantCity') or ''
            merchant_province = raw.get('merchantProvince') or ''
            
            return {
                'Unique Account ID': unique_account_id,
                'Unique Transaction ID': unique_trans_id,
                'Account Name': account['name'],
                'Date': date,
                'Description': description,
                'Payee': payee,
                'Payee Name': payee,
                'Amount': amount,
                'Currency': account['currency'],
                'Category': '',
                'Is Transfer': is_transfer,
                'Notes': notes,
                'Account Number': account['number'],
                'Account Type': account['type'],
                'Running Balance': running_balance,
                'Cheque Number': cheque_number,
                'Merchant City': merchant_city,
                'Merchant Province': merchant_province
            }
            
        except Exception as e:
            print(f"Error processing transaction: {e}")
            return None

    def download_transactions(self) -> List[Dict[str, Any]]:
        """Orchestrate the download process (API + CSV Fallback)."""
        all_transactions = []
        seen_ids = set()
        
        # 1. Try API Fetch
        print("\n--- Starting API Fetch ---")
        accounts = self.get_accounts()
        
        if accounts:
            self._export_accounts_from_list(accounts)
            for account in accounts:
                txns = self.fetch_transactions_for_account(account)
                for txn in txns:
                    tid = txn.get('Unique Transaction ID')
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
                # Logic: RBC-{last 4 of number}
                num = acc.get('number', '')
                if num:
                    digits = "".join(filter(str.isdigit, num))
                    if len(digits) >= 4:
                        uid = f"RBC-{digits[-4:]}"
                        api_account_ids.add(uid)

            new_accounts_from_csv = {}
            
            for txn in csv_txns:
                acc_num = txn.get('Account Number', '')
                acc_type = txn.get('Account Type', 'Unknown')
                
                # Generate ID for this CSV transaction's account
                digits = "".join(filter(str.isdigit, str(acc_num)))
                if len(digits) >= 4:
                    csv_acc_id = f"RBC-{digits[-4:]}"
                else:
                    csv_acc_id = "RBC-UNKNOWN"
                
                # Check coverage by ID
                if csv_acc_id in api_account_ids:
                    continue # Skip, already covered by API
                
                # Add transaction
                all_transactions.append(txn)
                
                # Track new account for accounts.csv
                if acc_num and acc_num not in new_accounts_from_csv:
                    new_accounts_from_csv[acc_num] = {
                        'name': f"{acc_type} {acc_num}",
                        'number': acc_num,
                        'type': acc_type,
                        'currency': txn.get('Currency', 'CAD')
                    }
            
            # Append new accounts to accounts.csv
            if new_accounts_from_csv:
                print(f"Found {len(new_accounts_from_csv)} new account(s) in CSV. Updating accounts.csv...")
                self._append_accounts_to_csv(list(new_accounts_from_csv.values()))
                
        except Exception as e:
            print(f"CSV Download failed: {e}")
            
        # Sort transactions by Date (descending or ascending? User said "sort by date", usually descending is preferred for logs, but ascending for ledgers.
        # Let's do descending (newest first) as that's common for banking, or ascending?
        # The existing CSVs seem to be mixed or newest first.
        # "sort the csv records by date" - usually implies chronological.
        # Let's do descending (newest first) which is typical for bank statements.
        # Wait, usually ledgers are chronological (oldest first).
        # I'll stick to descending (newest first) as it matches the "Saved X transactions to YYYY-MM.csv" flow which usually processes newest first if coming from API.
        # Actually, let's look at the user request "sort the csv records by date".
        # I'll do descending.
        all_transactions.sort(key=lambda x: x.get('Date', ''), reverse=True)
            
        return all_transactions

    def _append_accounts_to_csv(self, new_accounts: List[Dict[str, Any]]):
        """Append new accounts found in CSV to the accounts file."""
        import csv
        accounts_file = self.config.output_dir / self.get_bank_name() / "accounts.csv"
        
        rows = []
        for acc in new_accounts:
            rows.append({
                'Unique Account ID': f"RBC-{acc['number'][-4:]}" if acc['number'] else f"RBC-UNKNOWN",
                'Account Name': acc['name'],
                'Account Number': acc['number'],
                'Currency': acc['currency'],
                'Type': acc['type']
            })
            
        try:
            with open(accounts_file, 'a', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=['Unique Account ID', 'Account Name', 'Account Number', 'Currency', 'Type'])
                # No header, appending
                writer.writerows(rows)
            print(f"Appended {len(rows)} new account(s) to {accounts_file}")
        except Exception as e:
            print(f"Error appending accounts to CSV: {e}")

    def download_transactions_csv(self) -> List[Dict[str, Any]]:
        """Download CSV and parse it (Legacy Method)."""
        print("Navigating to Account Services page for CSV download...")
        self.page.goto("https://www1.royalbank.com/sgw1/olb/index-en/#/account-services")
        self.page.wait_for_load_state('networkidle')
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
            with self.page.expect_download(timeout=60000) as download_info:
                continue_button.click()
                
            download = download_info.value
            download_path = download.path()
            
            # Parse the downloaded file
            return self._parse_rbc_csv(download_path)
        except Exception as e:
            print(f"Error during file download: {e}")
            return []

    def _parse_rbc_csv(self, csv_path: str) -> List[Dict[str, Any]]:
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
                    unique_account_id = f"RBC-{acc_number[-4:]}" if acc_number else "RBC-UNKNOWN"
                    unique_trans_id = TransactionNormalizer.generate_transaction_id(date, amount, description, unique_account_id)
                    
                    txn = {
                        'Unique Account ID': unique_account_id,
                        'Unique Transaction ID': unique_trans_id,
                        'Date': date,
                        'Description': description,
                        'Amount': amount,
                        'Currency': currency,
                        'Category': '', 
                        'Account Type': acc_type,
                        'Account Number': acc_number,
                        'Cheque Number': str(row.get('Cheque Number', '')) if not pd.isna(row.get('Cheque Number')) else '',
                    }
                    
                    # Add any other fields from row not explicitly handled
                    for col in df.columns:
                        if col not in ['Transaction Date', 'Account Type', 'Account Number', 'Description 1', 'Description 2', 'CAD$', 'USD$', 'Cheque Number']:
                            val = row.get(col)
                            if not pd.isna(val) and str(val).strip() != '':
                                txn[col] = val
                                
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
                    
                    txn = {
                        'Unique Account ID': unique_account_id,
                        'Unique Transaction ID': unique_trans_id,
                        'Date': date,
                        'Description': description,
                        'Amount': amount,
                        'Currency': 'CAD',
                        'Category': '',
                    }
                    transactions.append(txn)

        except Exception as e:
            print(f"Error parsing CSV {csv_path}: {e}")
            
        return transactions

    def _export_accounts_from_list(self, accounts: List[Dict[str, Any]]):
        """Save accounts to CSV."""
        import csv
        
        accounts_file = self.config.output_dir / self.get_bank_name() / "accounts.csv"
        self.config.output_dir.mkdir(parents=True, exist_ok=True)
        (self.config.output_dir / self.get_bank_name()).mkdir(parents=True, exist_ok=True)
        
        fieldnames = ['Unique Account ID', 'Account Name', 'Account Number', 'Currency', 'Type']
        
        rows = []
        for acc in accounts:
            rows.append({
                'Unique Account ID': f"RBC-{acc['number'][-4:]}" if acc['number'] else f"RBC-{acc['id'][:8]}",
                'Account Name': acc['name'],
                'Account Number': acc['number'],
                'Currency': acc['currency'],
                'Type': acc['type']
            })
            
        try:
            with open(accounts_file, 'w', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(rows)
            print(f"Exported {len(rows)} account(s) to {accounts_file}")
        except Exception as e:
            print(f"Error exporting accounts to CSV: {e}")
