import argparse
import sys
import pandas as pd
from typing import List
from pathlib import Path

"""
Ledger Fetch - Main Entry Point

This script serves as the command-line interface (CLI) for the Ledger Fetch application.
It orchestrates the process of downloading financial transactions from various banks 
and saving them to local CSV files.

Purpose:
- Parse command-line arguments to determine which banks to process.
- Initialize and execute the appropriate BankDownloader instances.
- Handle browser automation setup using Playwright.
- Provide utility execution, such as offline payee normalization.

Usage:
    python main.py --bank <bank_name>    # Download from a specific bank
    python main.py --all                 # Download from all configured banks
    python main.py --normalize           # Run offline payee normalization
    python main.py --headless            # Run in headless mode (no visible browser)

Dependencies:
- playwright: For browser automation.
- pandas: For CSV handling and data manipulation.
- ledger_fetch.*: Internal modules for bank logic.
"""
from ledger_fetch.config import settings
from ledger_fetch.base import BankDownloader
from ledger_fetch.utils import TransactionNormalizer
from ledger_fetch.rbc import RBCDownloader
from ledger_fetch.wealthsimple import WealthsimpleDownloader
from ledger_fetch.amex import AmexDownloader
from ledger_fetch.canadiantire import CanadianTireDownloader
from ledger_fetch.bmo import BMODownloader
from ledger_fetch.cibc import CIBCDownloader
from ledger_fetch.national_bank import NationalBankDownloader

BANKS = {
    "rbc": RBCDownloader,
    "amex": AmexDownloader,
    "wealthsimple": WealthsimpleDownloader,
    "canadiantire": CanadianTireDownloader,
    "bmo": BMODownloader,
    "cibc": CIBCDownloader,
    "national_bank": NationalBankDownloader,
}

def get_downloaders(banks: List[str]) -> List[BankDownloader]:
    """
    Return a list of initialized downloader instances based on requested bank names.
    
    Args:
        banks: A list of bank key strings (e.g., ['rbc', 'bmo']). 
               If 'all' is present in the list, returns downloaders for all registered banks.
               
    Returns:
        List[BankDownloader]: A list of instantiated downloader objects ready to run.
    """
    downloaders = []
    
    requested = set(banks)
    # If the user requested 'all' banks, iterate through the entire registry
    if 'all' in requested:
        # Return all instantiated downloaders
        return [cls() for cls in BANKS.values()]
    
    # Otherwise, only instantiate the specifically requested banks
    for bank_name, cls in BANKS.items():
        if bank_name in requested:
            downloaders.append(cls())
        
    return downloaders

def run_normalization():
    """Run payee normalization on all existing CSV files."""
    print("Running offline payee normalization...")
    output_dir = settings.transactions_path
    if not output_dir.exists():
        print(f"Output directory {output_dir} does not exist.")
        return

    # Walk through all files in output_dir recursively
    count = 0
    for file_path in output_dir.rglob("*.csv"):
        # We only want to normalize transaction files. 
        # Skip 'accounts.csv' and other non-transactional system files.
        if file_path.name.lower() == "accounts.csv":
            continue
            
        print(f"Processing {file_path.parent.name}/{file_path.name}...")
        try:
            # Read CSV into a pandas DataFrame
            df = pd.read_csv(file_path)
            
            # Check if Description column exists
            if 'Description' not in df.columns:
                print(f"  Skipping {file_path.name}: No 'Description' column found.")
                continue
            
            # Apply normalization
            # We update 'Payee' and 'Payee Name' based on 'Description'
            df['Payee'] = df['Description'].apply(lambda x: TransactionNormalizer.normalize_payee(str(x)))
            df['Payee Name'] = df['Payee']
            
            # Save back to CSV
            df.to_csv(file_path, index=False)
            print(f"  Updated {file_path.name}")
            count += 1
            
        except Exception as e:
            print(f"  Error processing {file_path.name}: {e}")
    
    print(f"Normalization complete. Processed {count} files.")

def main():
    parser = argparse.ArgumentParser(description="Ledger Fetch - Financial Transaction Downloader")
    parser.add_argument(
        "--bank", 
        choices=['all'] + list(BANKS.keys()), 
        default='all',
        help="Specific bank to download from (default: all)"
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Download from all banks (equivalent to --bank all)"
    )
    parser.add_argument(
        "--headless", 
        action="store_true", 
        help="Run in headless mode (overrides config)"
    )
    parser.add_argument(
        "--normalize",
        action="store_true",
        help="Run payee normalization on existing transaction files without downloading"
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug mode (HAR recording, verbose logging, pause on error)"
    )
    
    args = parser.parse_args()
    
    # Handle --normalize flag
    if args.normalize:
        run_normalization()
        return

    # Handle --all flag
    if args.all:
        args.bank = 'all'
    
    # Update config from args
    if args.headless:
        settings.headless = True
    if args.debug:
        settings.debug = True

    print(f"Starting Ledger Fetch...")
    print(f"Output directory: {settings.transactions_path.resolve()}")
    print(f"Browser profile: {settings.browser_profile_path.resolve()}")
    
    banks_to_run = [args.bank]
    from playwright.sync_api import sync_playwright
    
    downloaders = get_downloaders(banks_to_run)
    
    if not downloaders:
        print("No downloaders selected.")
        return
        
    # Use a single shared Playwright instance for all downloaders
    with sync_playwright() as p:
        for downloader in downloaders:
            try:
                print(f"\n--- Starting download for {downloader.get_bank_name().upper()} ---")
                downloader.run(playwright_instance=p)
            except Exception as e:
                print(f"Error running {downloader.get_bank_name()}: {e}")
                if settings.debug:
                    import traceback
                    traceback.print_exc()

    #run_normalization()        
    print("\nAll tasks completed.")

if __name__ == "__main__":
    main()
