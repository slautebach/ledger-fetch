import pandas as pd
from pathlib import Path
from collections import Counter
from ledger_fetch.config import settings

def count_payees():
    # Use the output directory from settings
    transactions_dir = settings.output_dir
    
    print(f"Scanning files in {transactions_dir.resolve()}...")
    
    all_payees = []
    
    for file_path in transactions_dir.rglob("*.csv"):
        if file_path.name.lower() == "accounts.csv":
            continue
            
        try:
            df = pd.read_csv(file_path)
            
            # Check for Payee column
            if 'Payee' in df.columns:
                # Drop NaNs and convert to string
                payees = df['Payee'].dropna().astype(str).tolist()
                all_payees.extend(payees)
            elif 'Description' in df.columns:
                # Fallback to Description if Payee is missing (though it shouldn't be if normalized)
                print(f"  Note: {file_path.name} has no 'Payee' column, using 'Description'")
                payees = df['Description'].dropna().astype(str).tolist()
                all_payees.extend(payees)
                
        except Exception as e:
            print(f"Error reading {file_path}: {e}")
            
    print(f"\nTotal transactions analyzed: {len(all_payees)}")
    
    counts = Counter(all_payees)
    
    # Save to CSV
    output_csv = transactions_dir / "payees.csv"
    df_counts = pd.DataFrame(counts.most_common(), columns=['Payee', 'Count'])
    df_counts.to_csv(output_csv, index=False)
    print(f"\nSaved payee counts to {output_csv}")
    
    print("\n--- Payee Counts (Top 100) ---")
    print(f"{'Count':<6} | {'Payee'}")
    print("-" * 40)
    
    for payee, count in counts.most_common(100):
        print(f"{count:<6} | {payee}")

if __name__ == "__main__":
    count_payees()
