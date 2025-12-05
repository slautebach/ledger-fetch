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
        self.accounts_cache: Dict[str, Account] = {}

        # Log configuration
        try:
            bank_name = self.get_bank_name()
            bank_config = getattr(self.config, bank_name, None)
            if bank_config:
                # Handle Pydantic v1/v2 compatibility
                dump_func = getattr(bank_config, 'model_dump', getattr(bank_config, 'dict', None))
                config_dict = dump_func() if dump_func else str(bank_config)
                print(f"[{bank_name.upper()}] Configuration: {config_dict}")
        except Exception:
            # Ignore errors during init logging (e.g. if get_bank_name relies on uninitialized vars)
            pass

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
                
                # Fetch accounts first so we have type information
                try:
                    accounts = self.fetch_accounts()
                    if accounts:
                        self.save_accounts(accounts)
                        # Cache accounts for transaction processing
                        self.accounts_cache = {a.unique_account_id: a for a in accounts}
                except Exception as e:
                    print(f"Warning: Failed to fetch accounts: {e}")
                    self.accounts_cache = {}

                self.navigate_to_transactions()
                transactions = self.download_transactions()
                self.save_transactions(transactions)
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
        
        # Convert Transactions to dicts and enforce signs
        txn_dicts = []
        for t in transactions:
            # Check if account is liability
            acc = self.accounts_cache.get(t.unique_account_id)
            if acc and acc.is_liability:
                # Check if this bank is configured to invert credit transactions
                bank_name = self.get_bank_name()
                bank_config = getattr(self.config, bank_name, None)
                
                if bank_config and bank_config.invert_credit_transactions:
                    # Enforce negative for purchases (if positive) and positive for payments (if negative)
                    # Assumption: Bank returns positive for purchases.
                    # We want: Purchase = Negative, Payment = Positive.
                    # If we just multiply by -1, we assume the input is "Amount Owed" or "Debit Amount".
                    try:
                        amount = float(t.amount)
                        # If it's a liability account, we invert the sign relative to "Debit is Positive" convention
                        # So a $50 purchase (Debit) becomes -50.
                        # A -$50 payment (Credit) becomes +50.
                        t.amount = -amount
                    except (ValueError, TypeError):
                        pass
            
            txn_dicts.append(t.to_csv_row())
        
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
        """
        Ensure all accounts in transactions exist in accounts.csv.
        Also updates existing accounts if the transaction provides a 'better' (e.g. unmasked) account number.
        """
        accounts_file = self.config.output_dir / self.get_bank_name() / "accounts.csv"
        
        known_accounts: Dict[str, Account] = {}
        if accounts_file.exists():
            import csv
            try:
                with open(accounts_file, 'r', encoding='utf-8') as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        acc_id = row.get('Unique Account ID')
                        if acc_id:
                            # Reconstruct Account object
                            # We pass row as raw_data, which works for simple fields
                            known_accounts[acc_id] = Account(row, acc_id)
            except Exception as e:
                print(f"Warning: Error reading existing accounts.csv: {e}")

        updated = False
        for txn in transactions:
            acc_id = txn.unique_account_id
            if not acc_id:
                continue
            
            # Extract potential new number from transaction raw data
            # Adjust key if necessary based on what _parse_rbc_csv puts in raw_data
            new_number = str(txn.raw_data.get('Account Number', ''))
            
            if acc_id in known_accounts:
                # Check if we should update the existing account
                existing_acc = known_accounts[acc_id]
                current_number = existing_acc.account_number
                
                if self._is_better_account_number(current_number, new_number, acc_id):
                    print(f"Updating account {acc_id}: Number changed from '{current_number}' to '{new_number}'")
                    existing_acc.account_number = new_number
                    updated = True
            else:
                # Create new account
                acc = Account({}, acc_id)
                acc.account_name = txn.account_name
                acc.currency = txn.currency
                if new_number:
                    acc.account_number = new_number
                
                known_accounts[acc_id] = acc
                updated = True
        
        if updated:
            print(f"Saving updated accounts list to {accounts_file}...")
            self.save_accounts(list(known_accounts.values()))

    def _is_better_account_number(self, existing: str, new: str, unique_id: str = None) -> bool:
        """
        Determine if the 'new' account number is better than the 'existing' one.
        Better means:
        1. Existing is empty/None, and New is not.
        2. Existing contains masked characters ('*') and New does not.
        3. New is longer than Existing (and not masked), assuming more detail.
        
        EXCEPTION: If existing matches unique_id (e.g. RBC-XXXX), we prefer that format
        and do NOT update it, even if new is longer/unmasked.
        """
        if not new or new.strip() == '':
            return False
        if not existing or existing.strip() == '':
            return True
            
        # If existing matches unique_id, keep it (User preference for RBC-XXXX format)
        if unique_id and existing == unique_id:
            return False
            
        # If existing is masked and new is not
        if '*' in existing and '*' not in new:
            return True
            
        # If both are unmasked (or both masked), prefer the longer one?
        # Usually full number is longer than masked if masked is truncated, 
        # but sometimes masked is same length. 
        # If we have a full number vs a partial number, full is better.
        if '*' not in new and len(new) > len(existing):
            return True
            
        return False

    def save_accounts(self, accounts: List[Account]):
        """Save accounts to CSV."""
        from .utils import CSVWriter
        writer = CSVWriter(self.config.output_dir / self.get_bank_name())
        
        # Enforce negative balance for liabilities
        for acc in accounts:
            if acc.is_liability and acc.current_balance > 0:
                acc.current_balance = -(acc.current_balance)
        
        account_dicts = [a.to_csv_row() for a in accounts]
        writer.write(account_dicts, "accounts.csv", fieldnames=Account.CSV_FIELDS)

    def teardown(self):
        """Close browser context."""
        if self.context:
            try:
                self.context.close()
                # Give the browser process a moment to fully release file locks
                import time
                time.sleep(5)
            except Exception as e:
                print(f"Warning: Error closing context: {e}")

    @abstractmethod
    def get_bank_name(self) -> str:
        """Return unique bank identifier for directory naming."""
        pass
