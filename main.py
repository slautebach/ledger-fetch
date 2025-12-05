import argparse
import sys
import pandas as pd
from typing import List
from pathlib import Path
from ledger_fetch.config import settings
from ledger_fetch.base import BankDownloader
from ledger_fetch.utils import TransactionNormalizer
from ledger_fetch.rbc import RBCDownloader
from ledger_fetch.wealthsimple import WealthsimpleDownloader
from ledger_fetch.amex import AmexDownloader
from ledger_fetch.canadiantire import CanadianTireDownloader
from ledger_fetch.bmo import BMODownloader
from ledger_fetch.cibc import CIBCDownloader

def get_downloaders(banks: List[str]) -> List[BankDownloader]:
    """Return list of downloader instances based on requested banks."""
    downloaders = []
    
    if 'all' in banks or 'rbc' in banks:
        downloaders.append(RBCDownloader())
        
    if 'all' in banks or 'wealthsimple' in banks:
        downloaders.append(WealthsimpleDownloader())
        
    if 'all' in banks or 'amex' in banks:
        downloaders.append(AmexDownloader())
        
    if 'all' in banks or 'canadiantire' in banks:
        downloaders.append(CanadianTireDownloader())

    if 'all' in banks or 'bmo' in banks:
        downloaders.append(BMODownloader())

    if 'all' in banks or 'cibc' in banks:
        downloaders.append(CIBCDownloader())
        
    return downloaders

def run_normalization():
    """Run payee normalization on all existing CSV files."""
    print("Running offline payee normalization...")
    output_dir = settings.output_dir
    if not output_dir.exists():
        print(f"Output directory {output_dir} does not exist.")
        return

    # Walk through all files in output_dir
    count = 0
    for file_path in output_dir.rglob("*.csv"):
        # Skip accounts.csv and non-transaction files if possible
        if file_path.name.lower() == "accounts.csv":
            continue
            
        print(f"Processing {file_path.parent.name}/{file_path.name}...")
        try:
            # Read CSV
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
        choices=['all', 'rbc', 'wealthsimple', 'amex', 'canadiantire', 'bmo', 'cibc'], 
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
    print(f"Output directory: {settings.output_dir.resolve()}")
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

    run_normalization()        
    print("\nAll tasks completed.")

if __name__ == "__main__":
    main()
