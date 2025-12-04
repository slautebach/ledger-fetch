from abc import ABC, abstractmethod
from typing import List, Dict, Any
from pathlib import Path
from playwright.sync_api import Playwright, BrowserContext, Page, sync_playwright
from .config import Config, settings
from .models import Transaction, Account

class BankDownloader(ABC):
    """
    Abstract base class for bank transaction downloaders.
    
    This class defines the interface that all bank-specific downloaders must implement.
    It handles the common Playwright setup and teardown, as well as the high-level
    execution flow (login -> navigate -> download -> save).
    """
    
    def __init__(self, config: Config = settings):
        self.config = config
        self.context: BrowserContext = None
        self.page: Page = None
        self.playwright: Playwright = None

    def run(self):
        """
        Main execution method.
        
        This method orchestrates the entire download process:
        1.  Sets up the Playwright browser.
        2.  Performs the login.
        3.  Navigates to the transaction page.
        4.  Downloads and parses transactions.
        5.  Saves the transactions to CSV files.
        6.  Cleans up resources.
        """
        with sync_playwright() as p:
            self.playwright = p
            self.setup_driver()
            try:
                self.login()
                self.navigate_to_transactions()
                transactions = self.download_transactions()
                self.save_transactions(transactions)
                # We should also fetch and save accounts if possible, but that might be bank specific
                # For now, let's assume download_transactions might also trigger account fetching or we add a new step
                # But the plan said "Implement fetch_accounts (or similar) to return List[Account] and save them."
                # So let's add a hook for it.
                accounts = self.fetch_accounts()
                if accounts:
                    self.save_accounts(accounts)
                if accounts:
                    self.save_accounts(accounts)
            except Exception as e:
                if self.config.debug:
                    print(f"\n{'='*60}")
                    print(f"CRITICAL ERROR: {e}")
                    print("The browser is still open for debugging.")
                    print("Network traffic has been recorded to the HAR file.")
                    print(f"{'='*60}\n")
                    import traceback
                    traceback.print_exc()
                    input("Press Enter to close the browser and exit...")
                raise e
            finally:
                self.teardown()

    def setup_driver(self):
        """
        Initialize Playwright browser context.
        
        Launches a persistent Chrome context using the configured profile path.
        This allows the browser to retain cookies and session data between runs,
        which is crucial for maintaining login sessions and avoiding 2FA prompts.
        """
        print(f"Launching browser with profile: {self.config.browser_profile_path}")
        
        # Ensure profile directory exists
        self.config.browser_profile_path.mkdir(parents=True, exist_ok=True)
        
        launch_args = {
            "user_data_dir": str(self.config.browser_profile_path),
            "channel": "chrome",
            "headless": self.config.headless,
            "accept_downloads": True,
            "args": ["--disable-blink-features=AutomationControlled"]
        }

        # Setup HAR recording if debug is enabled
        if self.config.debug:
            from datetime import datetime
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            har_dir = self.config.output_dir / "debug_logs"
            har_dir.mkdir(parents=True, exist_ok=True)
            har_path = har_dir / f"{self.get_bank_name()}_{timestamp}.har"
            print(f"Network traffic will be recorded to: {har_path}")
            launch_args["record_har_path"] = str(har_path)
        
        self.context = self.playwright.chromium.launch_persistent_context(**launch_args)
        self.context.set_default_timeout(self.config.timeout)
        self.page = self.context.new_page()

    @abstractmethod
    def login(self):
        """
        Perform login actions.
        
        This method should handle the navigation to the login page and any
        necessary steps to authenticate the user. It may be interactive (waiting
        for manual user input) or automated.
        """
        pass

    @abstractmethod
    def navigate_to_transactions(self):
        """
        Navigate to the transaction download page.
        
        This method should handle the navigation from the post-login state (dashboard)
        to the specific page where transactions can be viewed or downloaded.
        """
        pass

    def fetch_accounts(self) -> List[Account]:
        """
        Fetch account details.
        Override this in subclasses to extract account information.
        """
        return []

    @abstractmethod
    def download_transactions(self) -> List[Transaction]:
        """
        Download and parse transactions.
        
        This method should perform the actual extraction of transaction data.
        It may involve downloading a file (CSV/OFX) and parsing it, or scraping
        data directly from the page or API.
        
        Returns:
            A list of Transaction objects.
        """
        pass

    def save_transactions(self, transactions: List[Transaction]):
        """Save transactions to CSV."""
        from .utils import CSVWriter
        writer = CSVWriter(self.config.output_dir / self.get_bank_name())
        
        # Convert Transactions to dicts
        txn_dicts = [t.to_csv_row() for t in transactions]
        
        by_month = {}
        for txn in txn_dicts:
            # Assuming 'Date' is YYYY-MM-DD
            date = txn.get('Date', '')
            if len(date) >= 7:
                month = date[:7] # YYYY-MM
                if month not in by_month:
                    by_month[month] = []
                by_month[month].append(txn)
        
        for month, txns in by_month.items():
            writer.write(txns, f"{month}.csv", fieldnames=Transaction.CSV_FIELDS)
            
        # Ensure accounts exist
        self.ensure_accounts_exist(transactions)

    def ensure_accounts_exist(self, transactions: List[Transaction]):
        """Ensure all accounts in transactions exist in accounts.csv."""
        accounts_file = self.config.output_dir / self.get_bank_name() / "accounts.csv"
        
        known_ids = set()
        if accounts_file.exists():
            import csv
            try:
                with open(accounts_file, 'r', encoding='utf-8') as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        known_ids.add(row.get('Unique Account ID'))
            except Exception:
                pass
        
        new_accounts = {}
        for txn in transactions:
            acc_id = txn.unique_account_id
            if acc_id and acc_id not in known_ids and acc_id not in new_accounts:
                # Create minimal Account
                acc = Account({}, acc_id)
                acc.account_name = txn.account_name
                acc.currency = txn.currency
                # Try to extract account number from raw data if available (common pattern)
                if 'Account Number' in txn.raw_data:
                     acc.account_number = str(txn.raw_data['Account Number'])
                
                new_accounts[acc_id] = acc
        
        if new_accounts:
            print(f"Found {len(new_accounts)} new account(s) from transactions. Updating accounts.csv...")
            self._append_accounts_to_csv(list(new_accounts.values()))

    def _append_accounts_to_csv(self, new_accounts: List[Account]):
        """Append new accounts to the accounts file."""
        import csv
        accounts_file = self.config.output_dir / self.get_bank_name() / "accounts.csv"
        
        # Ensure directory exists
        accounts_file.parent.mkdir(parents=True, exist_ok=True)
        
        rows = [acc.to_csv_row() for acc in new_accounts]
        
        # If file doesn't exist, we need to write header
        file_exists = accounts_file.exists()
        
        fieldnames = ['Unique Account ID', 'Account Name', 'Account Number', 'Currency', 'Type', 'Status', 'Created At']
        
        try:
            with open(accounts_file, 'a', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                if not file_exists:
                    writer.writeheader()
                writer.writerows(rows)
            print(f"Appended {len(rows)} new account(s) to {accounts_file}")
        except Exception as e:
            print(f"Error appending accounts to CSV: {e}")

    def save_accounts(self, accounts: List[Account]):
        """Save accounts to CSV."""
        from .utils import CSVWriter
        writer = CSVWriter(self.config.output_dir / self.get_bank_name())
        
        account_dicts = [a.to_csv_row() for a in accounts]
        writer.write(account_dicts, "accounts.csv", fieldnames=Account.CSV_FIELDS)

    def teardown(self):
        """Close browser context."""
        if self.context:
            self.context.close()

    @abstractmethod
    def get_bank_name(self) -> str:
        """Return unique bank identifier for directory naming."""
        pass
