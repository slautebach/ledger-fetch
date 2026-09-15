import argparse
import sys
import pandas as pd
from typing import List, Dict
from datetime import datetime, timedelta
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
from ledger_fetch.utils import TransactionNormalizer, CSVWriter
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


def newest_month_on_disk(bank_name: str) -> str:
    """Newest YYYY-MM with a transaction CSV on disk for the bank, or ''."""
    bank_dir = settings.ledger_fetch.transactions_path / bank_name
    if not bank_dir.exists():
        return ""
    import re
    months = [p.stem for p in bank_dir.glob("*.csv")
              if re.fullmatch(r"\d{4}-\d{2}", p.stem)]
    return sorted(months)[-1] if months else ""


def previous_month(month: str) -> str:
    """YYYY-MM of the month before the given one."""
    d = datetime.strptime(month, "%Y-%m")
    if d.month == 1:
        return f"{d.year - 1}-12"
    return f"{d.year}-{d.month - 1:02d}"


def apply_auto_window(downloaders: List[BankDownloader]):
    """
    Shrink each bank's fetch window to what it actually needs: from the month
    before its newest on-disk data through today. Keeps runs fast and banks
    under rate limits, instead of refetching since_month history every time.
    """
    print("\nAuto fetch windows (newest on-disk month -> window):")
    for d in downloaders:
        bank = d.get_bank_name()
        newest = newest_month_on_disk(bank)
        if not newest:
            print(f"  {bank:14s} no existing data - using configured default")
            continue
        since = previous_month(newest)
        start = datetime.strptime(since + "-01", "%Y-%m-%d")
        days = max(1, (datetime.now() - start).days + 5)
        bank_config = settings.ledger_fetch.banks.get(bank)
        if bank_config is None:
            from ledger_fetch.config import BankConfig
            bank_config = BankConfig()
            settings.ledger_fetch.banks[bank] = bank_config
        bank_config.days_to_fetch = days
        d.effective_since = since
        print(f"  {bank:14s} {newest} -> since {since} ({days} days)")

def run_normalization():
    """Run payee normalization on all existing CSV files."""
    print("Running offline payee normalization...")
    output_dir = settings.ledger_fetch.transactions_path
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
            
            # Save back to CSV using CSVWriter to ensure consistent formatting and blank column removal
            # Fill NaNs with empty string to ensure blank columns are correctly identified
            records = df.fillna("").to_dict(orient='records')
            
            # CSVWriter expects output_dir in init
            writer = CSVWriter(file_path.parent)
            writer.write(records, file_path.name)
            
            print(f"  Updated {file_path.name}")
            count += 1
            
        except Exception as e:
            print(f"  Error processing {file_path.name}: {e}")
            if settings.ledger_fetch.debug:
                 import traceback
                 traceback.print_exc()
    
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
    parser.add_argument(
        "--diagnose",
        action="store_true",
        help="Record all XHR/fetch traffic to debug_logs/<bank>_<ts>_traffic.jsonl (implies debug)"
    )
    parser.add_argument(
        "--since",
        type=str,
        help="Fetch transactions from the first of this month onwards (YYYY-MM)"
    )

    args = parser.parse_args()

    # Handle --normalize flag
    if args.normalize:
        run_normalization()
        return 0

    # Handle --all flag
    if args.all:
        args.bank = 'all'

    # Update config from args
    if args.headless:
        settings.browser.headless = True
    if args.debug:
        settings.ledger_fetch.debug = True
    if args.diagnose:
        settings.ledger_fetch.diagnose = True
        settings.ledger_fetch.debug = True
    if args.since:
        settings.ledger_fetch.since_month = args.since

    print(f"Starting Ledger Fetch...")
    print(f"Output directory: {settings.ledger_fetch.transactions_path.resolve()}")
    print(f"Browser profile: {settings.browser.profile_path.resolve()}")

    banks_to_run = [args.bank]
    from playwright.sync_api import sync_playwright

    downloaders = get_downloaders(banks_to_run)

    if not downloaders:
        print("No downloaders selected.")
        return 0

    if args.since or settings.ledger_fetch.since_month:
        # Explicit window: apply globally to every bank (legacy behavior)
        try:
            target = datetime.strptime(settings.ledger_fetch.since_month, "%Y-%m")
            days_to_fetch = max(1, (datetime.now() - target).days + 5)
            for b in BANKS.keys():
                if b not in settings.ledger_fetch.banks:
                    from ledger_fetch.config import BankConfig
                    settings.ledger_fetch.banks[b] = BankConfig()
                settings.ledger_fetch.banks[b].days_to_fetch = days_to_fetch
            print(f"Explicit fetch window since {settings.ledger_fetch.since_month} "
                  f"({days_to_fetch} days for all banks)")
        except ValueError as e:
            print(f"Error parsing since_month configuration. Must be YYYY-MM. {e}")
            return 1
    elif getattr(settings.ledger_fetch, 'auto_window', True):
        apply_auto_window(downloaders)

    # Per-run log file: tee stdout so every print from every module is captured
    from ledger_fetch.utils import Tee
    logs_dir = Path(__file__).parent / "logs"
    logs_dir.mkdir(exist_ok=True)
    log_path = logs_dir / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{args.bank}.log"
    tee_out = Tee(log_path, sys.stdout)
    tee_err = Tee(log_path, sys.stderr)
    sys.stdout = tee_out
    sys.stderr = tee_err
    print(f"Run log: {log_path}")

    results: List[Dict] = []
    exit_code = 0

    # Use a single shared Playwright instance for all downloaders
    with sync_playwright() as p:
        for downloader in downloaders:
            bank = downloader.get_bank_name()
            print(f"\n--- Starting download for {bank.upper()} ---")
            error = None
            try:
                downloader.run(playwright_instance=p)
            except Exception as e:
                error = str(e)
                print(f"Error running {bank}: {e}")
                if settings.ledger_fetch.debug:
                    import traceback
                    traceback.print_exc()

            stats = downloader.last_run_stats or {}
            txns = stats.get("transactions", 0)
            prior_data = bool(newest_month_on_disk(bank))

            # Zero-txn guard: a bank that already has history returning nothing
            # is a silent data gap unless we flag it
            status = "OK"
            if error:
                status = f"ERROR: {error[:80]}"
                exit_code = 1
            elif txns == 0:
                if prior_data:
                    status = "FAILED: 0 transactions (bank has existing history)"
                    exit_code = 1
                else:
                    status = "EMPTY (no prior data - possibly normal)"

            results.append({
                "bank": bank,
                "transactions": txns,
                "newest": stats.get("newest_date", ""),
                "months": stats.get("months_written", []),
                "status": status,
            })

    # Restore stdout before printing the summary so it lands in both places
    sys.stdout = tee_out.original
    sys.stderr = tee_err.original

    print("\n" + "=" * 72)
    print("RUN SUMMARY")
    print("=" * 72)
    print(f"{'Bank':<15} {'Txns':>6}  {'Newest':<12} {'Status'}")
    print("-" * 72)
    for r in results:
        print(f"{r['bank']:<15} {r['transactions']:>6}  {r['newest'] or '-':<12} {r['status']}")
    print("-" * 72)
    if exit_code:
        print("Result: FAILURES DETECTED - check the log above.")
    else:
        print("Result: all banks OK.")
    print(f"Full log: {log_path}")
    tee_out.close()
    tee_err.close()

    #run_normalization()
    print("\nAll tasks completed.")
    return exit_code

if __name__ == "__main__":
    sys.exit(main())
