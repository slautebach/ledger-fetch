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
    
    This downloader automates the process of downloading CSV statements from the
    American Express website.
    
    Workflow:
    1.  Interactive Login: The user logs in manually.
    2.  Navigation: The script ensures the user is on the "Statements & Activity" page.
    3.  Discovery: It scans the page for available statement download buttons.
    4.  Download: It triggers the download for each available statement (CSV format).
    5.  Parsing: It reads the downloaded CSV files and normalizes the data.
    """

    def get_bank_name(self) -> str:
        return "amex"

    def login(self):
        """Navigate to login page and wait for manual login."""
        print("Navigating to American Express Statements page (will redirect to login)...")
        # Use the direct link that redirects back to statements after login
        self.page.goto("https://global.americanexpress.com/statements?inav=ca_myca_view_downl_pdf")
        
        print("\nWaiting for user to log in...")
        print("Please complete the login process.")
        print("You should be automatically redirected to the Statements page.")
        
        # Wait for statements page
        try:
            # Wait for URL to indicate we are on statements/activity
            self.page.wait_for_url(re.compile(r".*(statements|activity).*"), timeout=300000)
            print("Login and redirect detected.")
        except Exception:
            print("Warning: Login timeout or URL not matched. Proceeding anyway.")

    def navigate_to_transactions(self):
        """Navigate to Statements & Activity."""
        print("Ensuring we are on the Statements page...")
        
        # If we are on dashboard, try to navigate to activity/statements
        if "dashboard" in self.page.url:
            print("On dashboard. Attempting to navigate to Statements...")
            # Try to find a link to "Statements" or "Activity"
            # This is best effort. If it fails, we ask the user.
            try:
                # Common selectors for statements link
                link = self.page.get_by_text("Statements & Activity")
                if link.count() > 0:
                    link.first.click()
                    self.page.wait_for_load_state('networkidle')
                else:
                    self.page.goto("https://global.americanexpress.com/activity/recent")
            except:
                pass
                
        # Wait for user to be on the right page if automation didn't work
        print("Please ensure you are on the 'Statements & Activity' page.")
        
        # Wait for the page to settle
        try:
            self.page.wait_for_load_state('networkidle', timeout=10000)
        except:
            pass
            
        # Explicitly wait for the "Statements" or "Activity" header or download buttons
        print("Waiting for statements to load...")
        try:
            # Wait for either the download buttons OR the 'Recent Activity' header
            self.page.wait_for_selector('button[data-testid*="download-button"], button[aria-label*="Download"], h1:has-text("Statements"), h2:has-text("Statements")', timeout=30000)
        except Exception:
            print("Warning: Timed out waiting for Statements elements. Script might fail to find buttons.")

    def fetch_accounts(self) -> List[Account]:
        """
        Fetch account details including current balance from the dashboard.
        """
        print("Fetching account details from Dashboard...")
        try:
            # Navigate to dashboard if not already there
            if "dashboard" not in self.page.url:
                self.page.goto("https://global.americanexpress.com/dashboard")
            
            # Wait for the balance element
            # User provided HTML structure suggests:
            # <span data-locator-id="total_balance_title_value"><h1 ...><span>$1,675.45</span>...
            balance_selector = 'span[data-locator-id="total_balance_title_value"] h1 span'
            
            try:
                self.page.wait_for_selector(balance_selector, timeout=15000)
                balance_el = self.page.locator(balance_selector).first
                balance_text = balance_el.text_content()
                
                # Clean balance text (remove '$', ',', etc)
                # Note: The HTML shows "1,675<!-- -->.45", text_content() should handle the comment but let's be safe
                clean_balance = balance_text.replace('$', '').replace(',', '').strip()
                current_balance = float(clean_balance)
                print(f"  Found current balance: ${current_balance}")
            except Exception as e:
                print(f"  Could not extract balance: {e}")
                current_balance = 0.0

            # Get Account Key/ID
            account_key = self._extract_account_key()
            if not account_key:
                # Fallback if we can't find the key
                account_key = "AMEX-DEFAULT"
                
            # Create Account object
            # We don't have full account number or name easily available on dashboard without more scraping
            # But we can create a basic account object
            account = Account({}, account_key)
            account.current_balance = current_balance
            account.account_name = "American Express" # Default name
            account.currency = "CAD" # Assumption
            account.type = AccountType.CREDIT_CARD
            
            return [account]

        except Exception as e:
            print(f"Error fetching accounts: {e}")
            return []

    def download_transactions(self) -> List[Transaction]:
        """Download CSVs and parse them."""
        print("Scanning for available statements...")
        
        # Wait a bit more for dynamic content (React/Angular) to render buttons
        time.sleep(5)
        
        # 1. Expand Sections
        self._expand_sections()
        
        # 2. Extract Account Key
        account_key = self._extract_account_key()
        if not account_key:
            account_key = "AMEX-DEFAULT"
        
        # 3. Find Download Buttons
        # Retry finding buttons for a few seconds
        download_buttons = None
        for _ in range(3):
            download_buttons = self._find_download_buttons()
            if download_buttons.count() > 0:
                break
            time.sleep(2)
            
        count = download_buttons.count()
        print(f"Found {count} potential statements.")
        
        downloaded_files = []
        
        # Create a temporary directory for this run
        temp_dir = self.config.output_dir / "temp_amex"
        temp_dir.mkdir(parents=True, exist_ok=True)
        
        for i in range(count):
            btn = download_buttons.nth(i)
            test_id = btn.get_attribute("data-testid")
            
            # Skip Year End Summaries
            if test_id and "year-end-summary" in test_id:
                continue
                
            # Extract date
            date_part = self._extract_date_from_testid(test_id)
            if not date_part:
                print(f"Could not extract date from {test_id}, skipping.")
                continue
                
            print(f"Processing statement for {date_part}...")
            
            # We use the key for downloading if available, but we also need it for parsing
            # If we have a key, we use it. If not, we skip downloading?
            # The original code skipped if no account key.
            # But now we want to support AMEX-DEFAULT if key is missing?
            # Actually, the download URL requires an account_key.
            # So if we don't have a real key, we probably can't download via API.
            # But fetch_accounts can still work with AMEX-DEFAULT.
            
            # Let's check the original logic:
            # if account_key:
            #    ... download ...
            # else:
            #    print("  Skipping (No Account Key)")
            
            # So if we can't find a key, we can't download.
            # Thus, for transactions, we will always have a real key if we download.
            # So we should use that real key for the ID.
            
            if account_key and account_key != "AMEX-DEFAULT":
                try:
                    file_path = self._download_statement(account_key, date_part, i == 0, temp_dir)
                    if file_path:
                        downloaded_files.append(file_path)
                except Exception as e:
                    print(f"  Download failed: {e}")
            else:
                print("  Skipping (No Account Key)")
            
            time.sleep(1)
            
        # Parse all downloaded files
        all_transactions = []
        for csv_file in downloaded_files:
            # Pass the account_key (which must be valid if we downloaded)
            txns = self._parse_amex_csv(csv_file, account_key)
            all_transactions.extend(txns)
            
        # Cleanup temp dir
        try:
            shutil.rmtree(temp_dir)
        except: pass
            
        return all_transactions

    def _expand_sections(self):
        """Expand Recent and Previous statements sections."""
        try:
            recent_btn = self.page.locator("#recent-statements")
            if recent_btn.count() > 0 and recent_btn.get_attribute("aria-expanded") == "false":
                recent_btn.click()
                time.sleep(1)
        except: pass

        try:
            older_btn = self.page.locator("#older-statements")
            if older_btn.count() > 0 and older_btn.get_attribute("aria-expanded") == "false":
                older_btn.click()
                time.sleep(1)
        except: pass

    def _extract_account_key(self):
        """Extract account key from URL or page content."""
        account_key = None
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
            
        return None

    def _find_download_buttons(self):
        """Find download buttons using multiple strategies."""
        # Strategy 1: data-testid
        btns = self.page.locator('button[data-testid*="download-button"]')
        if btns.count() == 0:
            # Strategy 2: aria-label
            btns = self.page.locator('button[aria-label*="Download"]')
        if btns.count() == 0:
            # Strategy 3: text
            btns = self.page.get_by_text("Download")
        return btns

    def _extract_date_from_testid(self, test_id):
        if not test_id: return None
        parts = test_id.split('/')
        for part in parts:
            if re.match(r'\d{4}-\d{2}-\d{2}', part):
                return part
        return None

    def _download_statement(self, account_key, date_part, is_latest, download_dir):
        """Download a specific statement."""
        if is_latest:
            url = (
                f"https://global.americanexpress.com/api/servicing/v1/financials/documents"
                f"?account_key={account_key}&file_format=csv&limit=ALL"
                f"&itemized_transactions=true&status=posted&client_id=AmexAPI&additional_fields=true"
            )
        else:
            url = (
                f"https://global.americanexpress.com/api/servicing/v1/financials/documents"
                f"?account_key={account_key}&file_format=csv&limit=ALL"
                f"&statement_end_date={date_part}&additional_fields=true&status=posted&client_id=AmexAPI"
            )
            
        try:
            with self.page.expect_download(timeout=30000) as download_info:
                try:
                    # When navigating to a download URL, Playwright may throw "Download is starting"
                    # This is actually expected and means it worked.
                    self.page.goto(url, wait_until="commit")
                except Exception as e:
                    if "Download is starting" in str(e) or "net::ERR_ABORTED" in str(e):
                        pass
                    else:
                        raise e
            
            download = download_info.value
            path = download.path()
            
            # Ensure filename is YYYY-MM.csv
            # date_part is usually YYYY-MM-DD
            try:
                dt = pd.to_datetime(date_part)
                new_filename = dt.strftime('%Y-%m.csv')
            except:
                new_filename = f"amex_statement_{date_part}.csv"
                
            new_path = download_dir / new_filename
            shutil.copy(path, new_path)
            return str(new_path)
        except Exception as e:
            print(f"  Download error details: {e}")
            return None

    def _parse_amex_csv(self, csv_path: str, account_id: str = "AMEX") -> List[Transaction]:
        """Parse Amex CSV."""
        transactions = []
        try:
            df = pd.read_csv(csv_path, encoding='utf-8')
            
            # Find date column
            date_col = None
            for col in ['Date', 'date', 'Transaction Date', 'Post Date']:
                if col in df.columns:
                    date_col = col
                    break
            
            if not date_col:
                return []
                
            for _, row in df.iterrows():
                raw_date = row.get(date_col)
                if pd.isna(raw_date): continue
                
                date = TransactionNormalizer.normalize_date(raw_date)
                description = str(row.get('Description', ''))
                description = TransactionNormalizer.clean_description(description)
                
                # Amount
                amount = 0.0
                raw_amount = row.get('Amount')
                if not pd.isna(raw_amount):
                    try:
                        amount = float(raw_amount)
                    except: pass
                
                # Generate IDs
                # Amex might have 'Reference'
                ref = row.get('Reference')
                unique_account_id = account_id 
                unique_trans_id = str(ref) if not pd.isna(ref) else TransactionNormalizer.generate_transaction_id(date, amount, description, unique_account_id)
                
                payee_name = TransactionNormalizer.normalize_payee(description)

                # Create Transaction
                txn = Transaction(row.to_dict(), unique_account_id)
                txn.unique_transaction_id = unique_trans_id
                txn.date = date
                txn.description = description
                txn.payee = description # Original (cleaned) description
                txn.payee_name = payee_name # Normalized payee
                txn.amount = amount
                txn.currency = 'CAD' # Assumption
                
                transactions.append(txn)
                
        except Exception as e:
            print(f"Error parsing CSV {csv_path}: {e}")
            
        return transactions
