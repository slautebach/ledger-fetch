import time
import json
import uuid
import calendar
from datetime import datetime, timedelta
import re
from typing import List, Dict, Any, Optional
import pdfplumber
from pathlib import Path
from .base import BankDownloader
from monopoly.banks.canadian_tire.canadian_tire import CanadianTire
from monopoly.pdf import PdfDocument, PdfParser
from monopoly.pipeline import Pipeline
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
        api_transactions = []
        pdf_transactions = []
        
        print(f"Fetching transactions for account: {target_account.account_name} ({target_account.unique_account_id})")
        print(f"Processing account: {target_account.account_name} ({target_account.unique_account_id})")
        
        transient_ref = target_account.raw_data.get("transientReference")
        if not transient_ref:
            print("No transient reference for account.")
            return []

        for date in statement_dates:
            # Implement a sweep around the calculate date
            # The API requires the EXACT statement date. 
            # If our extrapolation is off by even a day (due to weekends/holidays), it fails.
            # We will try the calculated date, then -1 day, +1 day, -2 days, +2 days, etc.
            
            target_date_obj = datetime.strptime(date, "%Y-%m-%d")
            # Generate offsets: 0, -1, 1, -2, 2, ... up to +/- 12
            sweep_offsets = [0]
            for i in range(1, 1):
                sweep_offsets.extend([-i, i])
            
            found_data_for_month = False
            
            for offset in sweep_offsets:
                sweep_date_obj = target_date_obj + timedelta(days=offset)
                sweep_date_str = sweep_date_obj.strftime("%Y-%m-%d")
                
                print(f"  Attempting statement date: {sweep_date_str} (Offset: {offset})")
                
                txns = self._fetch_transactions_for_statement(sweep_date_str, transient_ref, target_account)
                
                # Check for rate limiting (if _fetch_transactions_for_statement returns None and printed 429)
                # But here we just get None. We should be safe to sleep a bit more if we are sweeping aggressively.
                time.sleep(2) # Increased default sleep to avoid 429s during aggressive sweeping

                if txns is not None:
                    if len(txns) > 0:
                        # Success with data! Definitely the right date.
                        print(f"  -> Successfully fetched {len(txns)} transactions for {sweep_date_str}.")
                        api_transactions.extend(txns) # Collect API transactions
                        found_data_for_month = True
                        break
                    else:
                        # Success but NO data. This might be a wrong date that just returned empty, 
                        # OR the actual statement has 0 transactions. 
                        # We continue searching in case another date has data.
                        print(f"  -> Valid response but 0 transactions for {sweep_date_str}. Continuing sweep...")
            
            if not found_data_for_month:
                print(f" ** WARNING: No transactions found near {date} after sweeping (Checked +/- 12 days). **")
                # Fallback: Try PDF for this month? 
                # We can't easily download *just* this month's PDF without navigating away.
                # Strategy: Mark this month as missing?
                pass

            time.sleep(1)
            
        # 3. PDF Fallback
        # If we have missing data or just want to be safe, download statements.
        # User constraint: "Canadian tire doesn't store transaction beyond 4 months available to the API"
        # So we should ALWAYS try PDF for older dates? 
        # For now, let's just trigger PDF download/parse for the whole range requested if we want?
        # Or just do it at the end.
        
        print("\n--- Starting PDF Statement Processing ---")
        try:
            pdf_files = self.download_statements(target_account)
            
            # Fallback: Scan directory for ALL PDFs to ensure we don't miss any 
            # (e.g. if the website dropdown didn't load older years but we have them on disk)
            stmt_dir = self.config.transactions_path / "canadiantire" / "statements"
            
            if stmt_dir.exists():
                local_pdfs = list(stmt_dir.glob("Statement_*.pdf"))
                
                for local_pdf in local_pdfs:
                    str_path = str(local_pdf)
                    # Check if already in list
                    if any(f[0] == str_path for f in pdf_files):
                        continue
                        
                    try:
                        # Extract date from filename: Statement_2023-01.pdf
                        date_str = local_pdf.stem.replace("Statement_", "")
                        # Default to 1st of month
                        stmt_date = datetime.strptime(date_str, "%Y-%m").date()
                        pdf_files.append((str_path, stmt_date))
                    except ValueError:
                        print(f"Skipping malformed filename: {local_pdf.name}")
                        continue
            
            # Sort files by date (optional, helps log readability)
            pdf_files.sort(key=lambda x: x[1])

            for pdf_path, stmt_date in pdf_files:
                print(f"Parsing PDF: {pdf_path} (Date: {stmt_date})")
                pdf_txns = self.parse_statement_pdf(pdf_path, stmt_date, target_account)
                if pdf_txns:
                    print(f"  -> Extracted {len(pdf_txns)} transactions from PDF.")
                    pdf_transactions.extend(pdf_txns)
        except Exception as e:
            print(f"Error in PDF processing: {e}")

        # 4. Deduplicate
        # Merge API and PDF transactions, removing duplicates from PDF if they exist in API
        print(f"DEBUG: Deduplicating {len(api_transactions)} API txns and {len(pdf_transactions)} PDF txns...")
        all_transactions = self._deduplicate_transactions(api_transactions, pdf_transactions)
        
        return all_transactions

    def _deduplicate_transactions(self, api_txns: List['Transaction'], pdf_txns: List['Transaction']) -> List['Transaction']:
        """
        Merge API and PDF transactions.
        Priority: API (usually has better metadata).
        
        Logic:
        - Keep all API transactions.
        - Add PDF transaction ONLY IF it does not match an existing API transaction.
        - Match Criteria:
            1. Date matches.
            2. Amount matches (approximate float).
            3. Description is a subset/match (e.g. "ROYALE DI" in "ROYALE DIRECT").
        """
        merged = list(api_txns)
        
        for pdf_txn in pdf_txns:
            is_duplicate = False
            pdf_desc = pdf_txn.description.strip().upper()
            
            for api_txn in api_txns:
                # 1. Date Match
                if pdf_txn.date != api_txn.date:
                    continue
                
                # 2. Amount Match
                if abs(pdf_txn.amount - api_txn.amount) > 0.001:
                    continue
                
                # 3. Description Match (Subset)
                api_desc = api_txn.description.strip().upper()
                
                # Check if one is contained in the other
                if (api_desc in pdf_desc) or (pdf_desc in api_desc):
                    print(f"  -> Duplicate detected (preferring API):")
                    print(f"     API: {api_txn.date} | {api_txn.amount} | {api_desc}")
                    print(f"     PDF: {pdf_txn.date} | {pdf_txn.amount} | {pdf_desc}")
                    is_duplicate = True
                    break
            
            if not is_duplicate:
                merged.append(pdf_txn)
                
        return merged

    def download_statements(self, account: Account) -> List[tuple[str, datetime.date]]:
        """
        Navigate to eStatements page and download PDFs via dropdown selection.
        Returns list of (filepath, statement_date).
        """
        print("Navigating to eStatements page...")
        downloaded_files = []
        import os
        
        # Ensure directory exists
        stmt_dir = self.config.transactions_path / "canadiantire" / "statements"
        stmt_dir.mkdir(parents=True, exist_ok=True)

        try:
            # Direct URL provided by user
            statements_url = "https://www.ctfs.com/content/dash/en/private/estatements.html"
            self.page.goto(statements_url, wait_until="domcontentloaded")
            
            # Wait for the Year dropdown
            self.page.wait_for_selector("#eStatementsYears", timeout=15000)
            time.sleep(3) # Let Angular settle
            
            # Get available years
            # We want to iterate through years present in the dropdown.
            year_options = self.page.locator("#eStatementsYears option").all()
            years = []
            for opt in year_options:
                val = opt.get_attribute("value")
                label = opt.inner_text().strip()
                # Angular values are like "number:2025". Label is "2025".
                if label.isdigit():
                    years.append(label)
            
            # Filter years to fetch? e.g. last 2-3 years.
            # Let's fetch relevant years based on days_to_fetch config?
            # For now, just try them all or last 3.
            current_year = datetime.now().year
            target_years = [str(y) for y in range(current_year, current_year - 4, -1)] # e.g. 2025, 2024, 2023, 2022
            
            # Intersect available years with target years
            years_to_process = [y for y in years if y in target_years]
            
            print(f"Found years: {years}. Processing: {years_to_process}")
            
            for year in years_to_process:
                print(f"Selecting Year: {year}")
                # Select year by label
                self.page.select_option("#eStatementsYears", label=year)
                time.sleep(2) # Wait for months to update (ng-change)
                
                # Get available months
                month_options = self.page.locator("#eStatementsMonths option").all()
                months = []
                for opt in month_options:
                    label = opt.inner_text().strip()
                    # Label is "January", "February", etc.
                    # Ignore empty/placeholder if any
                    if label:
                        months.append(label)
                
                print(f"  Available months for {year}: {months}")
                
                for month_name in months:
                    try:
                        # Parse date for filename
                        try:
                            stmt_date = datetime.strptime(f"{month_name} 1 {year}", "%B %d %Y").date()
                            # Note: The statement date isn't exactly the 1st, but good enough for grouping.
                            # We can refine it after parsing the PDF if needed.
                            # Better: naming it Statement_YYYY-MM.pdf
                            filename = f"Statement_{stmt_date.strftime('%Y-%m')}.pdf"
                        except:
                            print(f"  Skipping invalid month label: {month_name}")
                            continue

                        filepath = stmt_dir / filename
                        
                        if filepath.exists():
                            # Start: Optimization - check if we already parsed/have data?
                            # For now, create list to parse.
                            print(f"  Skipping existing: {filename}")
                            downloaded_files.append((str(filepath), stmt_date))
                            continue
                        
                        print(f"  Downloading {month_name} {year}...")
                        self.page.select_option("#eStatementsMonths", label=month_name)
                        time.sleep(1) 
                        
                        # Click View/Download
                        # Button ID: viewpdf-estatements
                        with self.page.expect_download(timeout=15000) as download_info:
                            self.page.click("#viewpdf-estatements")
                            
                        download = download_info.value
                        download.save_as(filepath)
                        print(f"  -> Saved to {filename}")
                        downloaded_files.append((str(filepath), stmt_date))
                        
                        # Random sleep to be nice
                        time.sleep(2)
                        
                    except Exception as e:
                        print(f"  Error downloading {month_name} {year}: {e}")
                        continue
                        
        except Exception as e:
            print(f"Error accessing eStatements: {e}")
            
        return downloaded_files

    def _get_statement_dates(self):
        """Extract available statement dates."""
        try:
            self.page.wait_for_selector("#selectBillingDates", timeout=10000)
            options = self.page.evaluate("""
                () => {
                    const select = document.querySelector('#selectBillingDates');
                    return Array.from(select.options).map(o => o.value).filter(v => v !== "Select Statement Date" && v !== "current");
                }
            """)
            
            dates = []
            for date_str in options:
                # Format: "October 10, 2023"
                try:
                    dt = datetime.strptime(date_str, "%B %d, %Y").date()
                    dates.append(dt)
                except ValueError:
                    print(f"Could not parse date: {date_str}")
                    
            return dates
        except Exception as e:
            print(f"Error scraping statement dates: {e}")
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
                return None
                
            if not result.get("ok"):
                print(f"API error: {result.get('status')}")
                return None
                
            json_response = json.loads(result.get("text", "{}"))
            # If the response contains an error message or looks invalid, return None
            if "validationErrors" in json_response and json_response["validationErrors"]:
                 print(f"API Validation Error: {json_response['validationErrors']}")
                 return None

            return self._parse_transaction_response(json_response, account)
            
        except Exception as e:
            print(f"Error fetching transactions: {e}")
            return None

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
 
            # API returns positive for expenses. We want negative.
            # Base.py invert is disabled.
            amount = -amount_val
                
            # IDs
            ref_num = txn_data.get('referenceNumber', '')
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
            txn.is_transfer = is_transfer
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
            
    def parse_statement_pdf(self, pdf_path: str, statement_date: datetime.date, account: Optional[Account] = None) -> List[Transaction]:
        """
        Parse a Canadian Tire PDF statement to extract transactions using 'monopoly'.
        
        Args:
            pdf_path: Path to the PDF file.
            statement_date: The date of the statement.
            account: The account object to link transactions to.
            
        Returns:
            List[Transaction]: Extracted transactions.
        """
        transactions = []
        path_obj = Path(pdf_path)
        if not path_obj.exists():
            print(f"PDF not found: {pdf_path}")
            return []
            
        try:
            # Use Monopoly Library
            doc = PdfDocument(file_path=path_obj)
            parser = PdfParser(bank=CanadianTire, document=doc)
            pipeline = Pipeline(parser=parser)
            
            # Extract (safety_check=False to allow partial parsing if totals don't match, common in partial downloads)
            stmt = pipeline.extract(safety_check=False) 
            
            # Apply statement date from arguments if monopoly didn't find one or to override?
            # Monopoly creates a BaseStatement. If it found a date, use it. 
            # If not, it might error. But 'extract' checks for statement date.
            
            # Transform
            monopoly_txns = pipeline.transform(stmt)
            
            for m_txn in monopoly_txns:
                date_str = m_txn.date # ISO 8601
                # Monopoly returns negative for expenses.
                # Base.py invert is disabled.
                # So we use raw amount (Negative).
                amount = float(m_txn.amount) 
                desc = m_txn.description
                
                # Normalize
                clean_desc = TransactionNormalizer.clean_description(desc)
                payee = TransactionNormalizer.normalize_payee(clean_desc)
                
                # ID
                unique_id = TransactionNormalizer.generate_transaction_id(
                    date_str, amount, clean_desc, "CTFS-PDF"
                )
                
                # Create Transaction Object
                acc_id = account.unique_account_id if account else "CTFS-PDF"
                acc_name = account.account_name if account else "Canadian Tire"
                
                t = Transaction({}, acc_id)
                t.unique_transaction_id = unique_id
                t.date = date_str
                t.description = clean_desc
                t.payee_name = payee
                t.amount = amount
                t.currency = "CAD"
                t.account_name = acc_name
                t.notes = "Source: PDF"
                
                transactions.append(t)
                
        except Exception as e:
            print(f"Error parsing PDF with monopoly: {e}")
            # Fallback or just log? import traceback; traceback.print_exc()
            
        return transactions
