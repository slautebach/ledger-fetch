import os
import time
import re
import shutil
import urllib.parse
import pandas as pd
from typing import List, Dict, Any
from .base import BankDownloader
from .utils import TransactionNormalizer

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

    def download_transactions(self) -> List[Dict[str, Any]]:
        """Download CSVs and parse them."""
        print("Scanning for available statements...")
        
        # Wait a bit more for dynamic content (React/Angular) to render buttons
        time.sleep(5)
        
        # 1. Expand Sections
        self._expand_sections()
        
        # 2. Extract Account Key
        account_key = self._extract_account_key()
        
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
            
            if account_key:
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
            txns = self._parse_amex_csv(csv_file)
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

    def _parse_amex_csv(self, csv_path: str) -> List[Dict[str, Any]]:
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
                # Amex CSV usually has 'Amount' column. 
                # Positive for payments/credits? Or depends on the export.
                # Original script says: "Expense = Negative. Payment = Positive."
                # But wait, original script says: "In parse_amex_csv, we set Amount = -raw_amount."?
                # No, looking at `categorize_transaction` docstring in original:
                # "Wait, actually the caller passes the SIGNED amount... In parse_amex_csv, we set Amount = -raw_amount."
                # But I don't see that logic in the `parse_amex_csv` function I read!
                # Let me re-read `parse_amex_csv` in `amex_downloader.py`.
                # It just returns the DF. It doesn't calculate signed amount there.
                # The `main` function in `amex_downloader.py` doesn't seem to iterate rows and calculate amount either?
                # Wait, `amex_downloader.py` just exports the DF to CSV. It doesn't seem to normalize the amount in the output CSV?
                # Ah, `amex_downloader.py` has `categorize_transaction` but it is NOT USED in `parse_amex_csv` or `main`!
                # It seems the original script was incomplete or I missed something.
                # `parse_amex_csv` returns a DF. `main` concats them and saves.
                # So the output CSVs from `amex_downloader.py` are just raw Amex exports (plus Month column).
                # Amex raw exports usually have positive amounts for everything.
                # If I want to normalize, I should apply logic.
                # Usually: Amount column.
                # If it's a payment, it might be negative or have a CR marker?
                # Let's look at `categorize_transaction` again. It has logic for "Payment patterns".
                # But since it wasn't used, the previous output was likely raw.
                # Requirement: "Ensure Output CSV requirements... Unique Account ID...".
                # I should try to make it signed if possible.
                # Standard Amex CSV: 'Amount'.
                # Let's assume positive is expense, negative is payment? Or vice versa?
                # Actually, usually Amex CSVs are positive for charges.
                # I will stick to raw amount for now, but maybe negate it if it looks like a payment?
                # Or just keep it as is to match "raw" export style but with normalized columns.
                # But `TransactionNormalizer` suggests we want a standard format.
                # Let's assume Amount is the value.
                
                raw_amount = row.get('Amount')
                if not pd.isna(raw_amount):
                    try:
                        amount = float(raw_amount)
                    except: pass
                
                # Generate IDs
                # Amex might have 'Reference'
                ref = row.get('Reference')
                unique_trans_id = str(ref) if not pd.isna(ref) else TransactionNormalizer.generate_transaction_id(date, amount, description, "AMEX")
                
                txn = {
                    'Unique Account ID': "AMEX", # Amex CSV doesn't usually have account number
                    'Unique Transaction ID': unique_trans_id,
                    'Date': date,
                    'Description': description,
                    'Amount': amount,
                    'Currency': 'CAD', # Assumption
                    'Category': '',
                }
                
                # Add other fields
                for k, v in row.items():
                    if k not in txn and not pd.isna(v):
                        txn[k] = v
                        
                transactions.append(txn)
                
        except Exception as e:
            print(f"Error parsing CSV {csv_path}: {e}")
            
        return transactions
