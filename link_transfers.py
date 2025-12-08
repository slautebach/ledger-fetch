import pandas as pd
import argparse
from pathlib import Path
from ledger_fetch.config import settings

def link_transfers():
    """
    Scans all transaction CSV files in the configured output directory,
    identifies matching transfers based on specific criteria, and updates
    the 'Transfer Id' column with the ID of the matching transaction.

    Matching Criteria (Strict):
    - Same Date
    - Opposite Amounts (e.g. +100.00 and -100.00)
    - Distinct Accounts (Source Account != Target Account)
    - No Ambiguity (Must be exactly 1 positive and 1 negative candidate)
    
    Logging:
    - Ambiguous matches are logged to 'ambiguous_transfers.log'
    """
    parser = argparse.ArgumentParser(description="Link matching transfers in transaction CSV files.")
    parser.add_argument("--clear-transfers", action="store_true", help="Clear all existing transfer links before processing.")
    args = parser.parse_args()

    output_dir = settings.output_dir
    log_file = output_dir / "ambiguous_transfers.log"
    print(f"Scanning for transaction files in {output_dir.resolve()}...")
    
    # 1. Load all CSV files (recursively)
    files = list(output_dir.rglob("*.csv"))
    
    # Filter 1: Must be in a subdirectory (not in root of output_dir)
    # Filter 2: Ignore 'accounts.csv' specifically (though Filter 1 catches matches in root)
    filtered_files = []
    for f in files:
        # Check if file is directly in output_dir
        if f.parent == output_dir:
            continue
        if f.name.lower() == 'accounts.csv':
            continue
        filtered_files.append(f)
        
    files = filtered_files
    
    if not files:
        print("No CSV files found.")
        return

    all_dfs = []
    file_map = {} 

    print(f"Found {len(files)} files. Loading data...")

    for i, file_path in enumerate(files):
        try:
            df = pd.read_csv(file_path)
            
            # Ensure required columns exist
            # Note: 'Unique Account ID' is required for the distinct account check
            required = ['Unique Transaction ID', 'Date', 'Amount', 'Unique Account ID']
            missing = [col for col in required if col not in df.columns]
            if missing:
                print(f"  Skipping {file_path.name}: Missing columns {missing}")
                continue
                
            df['_source_file_index'] = i
            df['_original_index'] = df.index
            
            if 'Transfer Id' not in df.columns:
                df['Transfer Id'] = None
            
            # Convert Amount to float and Date to datetime
            df['Amount'] = pd.to_numeric(df['Amount'], errors='coerce')
            df['pd_date'] = pd.to_datetime(df['Date'], errors='coerce')
            
            # Helper for absolute amount grouping
            df['abs_amount'] = df['Amount'].abs()
            
            all_dfs.append(df)
            file_map[i] = file_path
        except Exception as e:
            print(f"Error reading {file_path}: {e}")

    if not all_dfs:
        print("No valid transaction files found.")
        return

    full_df = pd.concat(all_dfs, ignore_index=True)
    print(f"Loaded {len(full_df)} transactions.")
    
    if args.clear_transfers:
        print("Cmd --clear-transfers: Clearing all existing transfer links...")
        full_df['Transfer Id'] = None

    # 2. Logic to find matches
    matches_found = 0
    ambiguous_entries = []
    
    # We only care about rows that are NOT currently linked
    # However, for accurate ambiguity checking, should we consider all rows?
    # User Requirement: "If it matches, it matches. If ambiguity, log."
    # We will process ONLY Unmatched rows. If a row is already linked, it's "taken".
    
    unmatched_mask = full_df['Transfer Id'].isna() & full_df['pd_date'].notna() & (full_df['Amount'] != 0)
    process_df = full_df[unmatched_mask]
    
    # Group by Date and Absolute Amount
    groups = process_df.groupby(['pd_date', 'abs_amount'])
    
    for (date, abs_amt), group in groups:
        positives = group[group['Amount'] > 0]
        negatives = group[group['Amount'] < 0]
        
        pos_count = len(positives)
        neg_count = len(negatives)
        
        if pos_count == 0 or neg_count == 0:
            continue
            
        # Case 1: Exact 1-to-1 Match
        if pos_count == 1 and neg_count == 1:
            p_idx = positives.index[0]
            n_idx = negatives.index[0]
            
            p_acct = full_df.at[p_idx, 'Unique Account ID']
            n_acct = full_df.at[n_idx, 'Unique Account ID']
            
            # Check: Different Accounts
            if p_acct != n_acct:
                # MATCH!
                p_id = full_df.at[p_idx, 'Unique Transaction ID']
                n_id = full_df.at[n_idx, 'Unique Transaction ID']
                
                full_df.at[p_idx, 'Transfer Id'] = n_id
                full_df.at[n_idx, 'Transfer Id'] = p_id
                matches_found += 1
            else:
                # Same Account - Log as ignored/skipped? 
                # Or is this "ambiguous"? Usually simplified as just "internal offset/refund", safe to skip silently or log verbose.
                # User said: "Transfers shall not be identified for the same account."
                # We'll skip silently to avoid log spam for refunds, unless requested.
                pass
                
        # Case 2: Ambiguity (Multiple candidates on either side)
        else:
            # Prepare log entry
            entry = {
                "Date": date.strftime('%Y-%m-%d'),
                "Amount": abs_amt,
                "Pos_Count": pos_count,
                "Neg_Count": neg_count,
                "Pos_IDs": list(positives['Unique Transaction ID']),
                "Neg_IDs": list(negatives['Unique Transaction ID'])
            }
            ambiguous_entries.append(entry)

    # 3. Save Log
    if ambiguous_entries:
        print(f"Found {len(ambiguous_entries)} ambiguous groups. key details to {log_file}")
        with open(log_file, 'w', encoding='utf-8') as f:
            f.write("Date,Amount,Pos_Count,Neg_Count,Details\n")
            for entry in ambiguous_entries:
                details = f"Pos: {entry['Pos_IDs']} | Neg: {entry['Neg_IDs']}"
                f.write(f"{entry['Date']},{entry['Amount']},{entry['Pos_Count']},{entry['Neg_Count']},\"{details}\"\n")
    else:
        # Clear log if empty
        if log_file.exists():
            log_file.unlink()

    # 4. Generate Matched Transfers Report
    print("Generating matched_transfers.csv...")
    matched_file = output_dir / "matched_transfers.csv"
    
    # Filter for rows that HAVE a Transfer Id
    linked_df = full_df[full_df['Transfer Id'].notna()].copy()
    
    if not linked_df.empty:
        # We need to join linked_df with full_df to get details of the 'Transfer Id' transaction
        # Let's call the original side "Source" and the linked side "Target"
        
        # Prepare target df (subset of full_df) for merging
        target_df = full_df[['Unique Transaction ID', 'Unique Account ID', 'Date', 'Amount', 'Description', 'Account Name']].copy()
        # Rename target columns to distinguish them
        target_df.columns = ['Target Transaction Id', 'Target Account ID', 'Target Date', 'Target Amount', 'Target Description', 'Target Account Name']
        
        # Merge Source (linked_df) with Target (target_df)
        # Join condition: Source['Transfer Id'] == Target['Target Transaction Id']
        merged_df = pd.merge(
            linked_df, 
            target_df, 
            left_on='Transfer Id', 
            right_on='Target Transaction Id', 
            how='inner'
        )
        
        # Filter to ensure unique pairs and enforce Source = Negative Amount.
        # Since A links to B and B links to A, we will have two rows for every pair.
        # One row will have Source(Neg) -> Target(Pos), the other Source(Pos) -> Target(Neg).
        # We only want the first case.
        
        # Ensure we are comparing strings to avoid TypeError between int and str, though not strictly needed for the sign check.
        merged_df['Unique Transaction ID'] = merged_df['Unique Transaction ID'].astype(str)
        merged_df['Target Transaction Id'] = merged_df['Target Transaction Id'].astype(str)
        
        # Filter for mismatched amounts (Source + Target should be ~0)
        amount_sum = (merged_df['Amount'] + merged_df['Target Amount']).abs()
        mismatch_mask = amount_sum > 0.01
        
        if mismatch_mask.any():
            bad_rows = merged_df[mismatch_mask]
            # Since rows are duplicated (A->B and B->A), divide count by 2 for logical pairs
            print(f"WARNING: Excluded {len(bad_rows)//2} linked pairs due to amount mismatch.")
            
        merged_df = merged_df[~mismatch_mask]
        
        merged_df = merged_df[merged_df['Amount'] < 0]
        
        # Select and rename columns for final output
        # Source Columns
        final_df = pd.DataFrame()
        final_df['Source Account ID'] = merged_df['Unique Account ID']
        final_df['Source Account Name'] = merged_df.get('Account Name', '')
        final_df['Source Transaction Id'] = merged_df['Unique Transaction ID']
        final_df['Source Date'] = merged_df['Date']
        final_df['Source Description'] = merged_df['Description']
        final_df['Source Amount'] = merged_df['Amount']
        
        # Target Columns
        final_df['Target Account ID'] = merged_df['Target Account ID']
        final_df['Target Account Name'] = merged_df.get('Target Account Name', '')
        final_df['Target Transaction Id'] = merged_df['Target Transaction Id']
        final_df['Target Date'] = merged_df['Target Date']
        final_df['Target Description'] = merged_df['Target Description']
        final_df['Target Amount'] = merged_df['Target Amount']
        
        # Save to CSV
        final_df.to_csv(matched_file, index=False)
        print(f"Saved {len(final_df)} matched transfer pairs to {matched_file}")
    else:
        print("No matched transfers found to report.")

    print(f"Found and linked {matches_found} new pair(s) of transfers.")

    if matches_found > 0 or args.clear_transfers:
        print("Saving changes to CSV files...")
        saved_count = 0
        for i, file_path in file_map.items():
            # Filter for rows belonging to this file from the main DF
            file_df = full_df[full_df['_source_file_index'] == i].copy()
            file_df = file_df.sort_values('_original_index')
            
            # Cleanup helper cols
            cols_to_drop = ['_source_file_index', '_original_index', 'pd_date', 'abs_amount']
            file_df = file_df.drop(columns=[c for c in cols_to_drop if c in file_df.columns])
            
            try:
                file_df.to_csv(file_path, index=False)
                saved_count += 1
            except Exception as e:
                print(f"Error saving {file_path.name}: {e}")
        print(f"Successfully updated {saved_count} files.")
    else:
        print("No changes to save.")

if __name__ == "__main__":
    link_transfers()
