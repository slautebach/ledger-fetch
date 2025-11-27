from abc import ABC, abstractmethod
from typing import List, Dict, Any
from pathlib import Path
from playwright.sync_api import Playwright, BrowserContext, Page, sync_playwright
from .config import Config, settings

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
        
        self.context = self.playwright.chromium.launch_persistent_context(
            user_data_dir=str(self.config.browser_profile_path),
            channel="chrome",
            headless=self.config.headless,
            accept_downloads=True,
            args=["--disable-blink-features=AutomationControlled"]
        )
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

    @abstractmethod
    def download_transactions(self) -> List[Dict[str, Any]]:
        """
        Download and parse transactions.
        
        This method should perform the actual extraction of transaction data.
        It may involve downloading a file (CSV/OFX) and parsing it, or scraping
        data directly from the page or API.
        
        Returns:
            A list of dictionaries, where each dictionary represents a transaction.
            
            The dictionary MUST contain the following keys to ensure compatibility with
            the CSVWriter and downstream consumers (like actual-sync):
            
            - 'Unique Transaction ID': str - A unique identifier for the transaction.
            - 'Unique Account ID': str - A unique identifier for the account.
            - 'Account Name': str - The human-readable name of the account.
            - 'Date': str - The transaction date in YYYY-MM-DD format.
            - 'Description': str - The raw transaction description.
            - 'Payee': str - The normalized payee name (optional).
            - 'Payee Name': str - The normalized payee name (alternative to Payee).
            - 'Amount': float - The transaction amount (signed).
            - 'Currency': str - The currency code (e.g., 'CAD', 'USD').
            - 'Category': str - The transaction category (optional).
            - 'Is Transfer': bool/str - Flag indicating if it's a transfer.
            - 'Notes': str - Additional notes or memo.
            
            Additional fields may be included but are not guaranteed to be processed
            by all consumers.
        """
        pass

    def save_transactions(self, transactions: List[Dict[str, Any]]):
        """Save transactions to CSV."""
        # This can be overridden or used as is with the CSVWriter
        from .utils import CSVWriter
        writer = CSVWriter(self.config.output_dir / self.get_bank_name())
        
        # Group by month or save as one file? 
        # Requirement says "Ensure Output CSV requirements".
        # Existing scripts group by month. Let's keep that pattern.
        
        by_month = {}
        for txn in transactions:
            # Assuming 'Date' is YYYY-MM-DD
            date = txn.get('Date', '')
            if len(date) >= 7:
                month = date[:7] # YYYY-MM
                if month not in by_month:
                    by_month[month] = []
                by_month[month].append(txn)
        
        for month, txns in by_month.items():
            writer.write(txns, f"{month}.csv")

    def teardown(self):
        """Close browser context."""
        if self.context:
            self.context.close()

    @abstractmethod
    def get_bank_name(self) -> str:
        """Return unique bank identifier for directory naming."""
        pass
