import argparse
import sys
from typing import List
from ledger_fetch.config import settings
from ledger_fetch.base import BankDownloader
from ledger_fetch.rbc import RBCDownloader
from ledger_fetch.wealthsimple import WealthsimpleDownloader
from ledger_fetch.amex import AmexDownloader
from ledger_fetch.canadiantire import CanadianTireDownloader

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
        
    return downloaders

def main():
    parser = argparse.ArgumentParser(description="Ledger Fetch - Financial Transaction Downloader")
    parser.add_argument(
        "--bank", 
        choices=['all', 'rbc', 'wealthsimple', 'amex', 'canadiantire'], 
        default='all',
        help="Specific bank to download from (default: all)"
    )
    parser.add_argument(
        "--headless", 
        action="store_true", 
        help="Run in headless mode (overrides config)"
    )
    
    args = parser.parse_args()
    
    # Update config from args
    if args.headless:
        settings.headless = True
        
    print(f"Starting Ledger Fetch...")
    print(f"Output directory: {settings.output_dir.resolve()}")
    print(f"Browser profile: {settings.browser_profile_path.resolve()}")
    
    banks_to_run = [args.bank]
    downloaders = get_downloaders(banks_to_run)
    
    if not downloaders:
        print("No downloaders selected.")
        return
        
    for downloader in downloaders:
        name = downloader.get_bank_name()
        print(f"\n{'='*50}")
        print(f"Running {name.upper()} Downloader")
        print(f"{'='*50}")
        
        try:
            downloader.run()
            print(f"\n✅ {name.upper()} completed successfully.")
        except Exception as e:
            print(f"\n❌ {name.upper()} failed: {e}")
            # Continue to next downloader even if one fails
            
    print("\nAll tasks completed.")

if __name__ == "__main__":
    main()
