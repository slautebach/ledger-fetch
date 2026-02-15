"""
Payee Processing Utility

This script analyzes transaction data to generate reports on payee mappings.
It acts as a helper to identify which original descriptions map to which normalized payees.

Workflow:
1. Runs the main normalization routine to ensure data is up-to-date.
2. Scans all transaction CSVs.
3. Generates `payee_counts.csv` (frequency of each normalized payee).
4. Generates `payee_mappings.csv` (mapping from Original Description -> Normalized Payee).
"""

import pandas as pd
from pathlib import Path
from collections import Counter
from ledger_fetch.config import settings
import main

def count_payees():
    # 0. Run normalization first
    print("\n--- Running Normalization Step ---")
    main.run_normalization()
    print("--- Normalization Complete ---\n")

    # Use the output directory from settings
    transactions_dir = settings.transactions_path
    
    print(f"Scanning files in {transactions_dir.resolve()}...")
    
    all_transactions = []
    
    # Files to exclude to avoid reading our own output or config files
    excluded_files = {
        "accounts.csv", 
        "payees.csv", 
        "payee_counts.csv", 
        "payee_mappings.csv"
    }
    
    for file_path in transactions_dir.rglob("*.csv"):
        if file_path.name.lower() in excluded_files:
            continue
            
        try:
            df = pd.read_csv(file_path)
            
            # Check for required columns
            if 'Payee' in df.columns and 'Description' in df.columns:
                # Keep only relevant columns
                subset = df[['Description', 'Payee']].copy()
                # Drop rows with missing values
                subset = subset.dropna()
                all_transactions.append(subset)
            elif 'Description' in df.columns:
                print(f"  Note: {file_path.name} has no 'Payee' column, skipping for mappings.")
            else:
                print(f"  Note: {file_path.name} is missing 'Description' or 'Payee' columns.")
                
        except Exception as e:
            print(f"Error reading {file_path}: {e}")
            
    if not all_transactions:
        print("No valid transaction data found.")
        return

    # Combine all dataframes
    full_df = pd.concat(all_transactions, ignore_index=True)
    
    print(f"\nTotal transactions analyzed: {len(full_df)}")
    
    # 1. Payee Counts
    # Group by Payee and count occurrences
    payee_counts = full_df['Payee'].value_counts().reset_index()
    payee_counts.columns = ['Payee', 'Count']
    
    output_counts_csv = transactions_dir / "payee_counts.csv"
    payee_counts.to_csv(output_counts_csv, index=False)
    print(f"Saved payee counts to {output_counts_csv}")
    
    # 2. Payee Mappings
    # Get unique pairings of Description -> Payee
    # Drop duplicates to show distinct mappings
    mappings_df = full_df[['Description', 'Payee']].drop_duplicates()
    
    # Sort for easier reading (by Payee then Description)
    mappings_df = mappings_df.sort_values(by=['Payee', 'Description'])
    
    # Add "Is Mapped" column
    from ledger_fetch.utils import TransactionNormalizer
    rules = TransactionNormalizer._load_payee_rules()
    rule_names = {r['name'] for r in rules} if rules else set()
    
    mappings_df['Is Mapped'] = mappings_df.apply(
        lambda x: (x['Payee'] in rule_names) and (x['Payee'] != x['Description']), 
        axis=1
    )
    
    output_mappings_csv = transactions_dir / "payee_mappings.csv"
    mappings_df.to_csv(output_mappings_csv, index=False)
    print(f"Saved unique payee mappings to {output_mappings_csv}")

if __name__ == "__main__":
    count_payees()
