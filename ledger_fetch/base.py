from abc import ABC, abstractmethod
from typing import List, Dict, Any
from pathlib import Path
from playwright.sync_api import Playwright, BrowserContext, Page, sync_playwright
from .config import Config, settings

class BankDownloader(ABC):
    """Abstract base class for bank transaction downloaders."""
    
    def __init__(self, config: Config = settings):
        self.config = config
        self.context: BrowserContext = None
        self.page: Page = None
        self.playwright: Playwright = None

    def run(self):
        """Main execution method."""
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
        """Initialize Playwright browser context."""
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
        """Perform login actions."""
        pass

    @abstractmethod
    def navigate_to_transactions(self):
        """Navigate to the transaction download page."""
        pass

    @abstractmethod
    def download_transactions(self) -> List[Dict[str, Any]]:
        """Download and parse transactions."""
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
