import csv
import re
import hashlib
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional

class TransactionNormalizer:
    """
    Utility class for standardizing transaction data.
    
    This class provides static methods to clean and normalize common transaction
    fields such as descriptions and dates, ensuring consistency across different
    bank sources.
    """
    
    @staticmethod
    def clean_description(description: str) -> str:
        """
        Clean and simplify transaction descriptions.
        
        Removes excessive whitespace and common bank prefixes (e.g., "RBC ", "AMEX ")
        to produce a cleaner, more readable description.
        """
        if not description:
            return ""
        
        # Remove excessive whitespace
        cleaned = re.sub(r'\s+', ' ', str(description)).strip()
        
        # Remove common prefixes that add clutter (can be expanded)
        cleaned = re.sub(r'^(RBC |ROYAL BANK |AMEX )', '', cleaned, flags=re.IGNORECASE)
        
        return cleaned

    _payee_rules = None

    @classmethod
    def _load_payee_rules(cls):
        if cls._payee_rules is not None:
            return cls._payee_rules
        
        from .config import settings
        import yaml
        
        rules_path = settings.payee_rules_path
        if not rules_path.is_absolute():
            # Try finding it relative to current working directory first
            if not rules_path.exists():
                 # Fallback to package directory if needed, or just keep as is
                 pass

        cls._payee_rules = []
        if rules_path.exists():
            try:
                with open(rules_path, 'r', encoding='utf-8') as f:
                    data = yaml.safe_load(f)
                    if data and 'rules' in data:
                        cls._payee_rules = data['rules']
            except Exception as e:
                print(f"Warning: Failed to load payee rules from {rules_path}: {e}")
        else:
            print(f"Warning: Payee rules file not found at {rules_path}")
            
        return cls._payee_rules

    @classmethod
    def normalize_payee(cls, raw_payee: str) -> str:
        """
        Normalize payee name based on configured rules.
        
        Applies a 'First Match Wins' strategy using the rules defined in
        payee_rules.yaml. If no rule matches, returns the cleaned original payee.
        """
        if not raw_payee:
            return ""
            
        cleaned = cls.clean_description(raw_payee)
        rules = cls._load_payee_rules()
        
        for rule in rules:
            name = rule.get('name')
            patterns = rule.get('patterns', [])
            
            for pattern in patterns:
                # Check for regex prefix
                if pattern.startswith("regex:"):
                    regex_pattern = pattern[6:]
                    try:
                        if re.search(regex_pattern, cleaned, re.IGNORECASE):
                            return name
                    except re.error:
                        # Log warning only if verbose? For now, just skip invalid regex
                        print(f"Warning: Invalid regex pattern '{regex_pattern}' for rule '{name}'")
                        continue
                        
                # Simple case-insensitive substring match
                elif pattern.lower() in cleaned.lower():
                    return name
                    
        return cleaned

    @staticmethod
    def normalize_date(date_str: str) -> str:
        """
        Ensure date is in YYYY-MM-DD format.
        
        Attempts to parse various common date formats and convert them to the
        standard ISO 8601 format (YYYY-MM-DD).
        """
        if not date_str:
            return ""
            
        try:
            # Try common formats
            # Added: %d %b %Y (01 Aug 2025), %d %b %Y (1 Aug 2025)
            for fmt in ['%Y-%m-%d', '%m/%d/%Y', '%d/%m/%Y', '%Y/%m/%d', '%b %d, %Y', '%d %b %Y', '%d %b %Y']:
                try:
                    dt = datetime.strptime(str(date_str), fmt)
                    return dt.strftime('%Y-%m-%d')
                except ValueError:
                    continue
            
            # If pandas timestamp or similar
            if hasattr(date_str, 'strftime'):
                return date_str.strftime('%Y-%m-%d')
                
            return str(date_str)
        except Exception:
            return str(date_str)

    @staticmethod
    def generate_transaction_id(date: str, amount: float, description: str, account_id: str) -> str:
        """
        Generate a deterministic unique ID for a transaction.
        
        Creates an MD5 hash based on the transaction's core properties (date, amount,
        description, and account ID). This is used as a fallback when the bank
        does not provide a unique transaction ID.
        """
        # Create a string unique to this transaction
        # Note: This might collide if there are identical transactions on the same day
        # Ideally we'd use a bank-provided ID, but if not available, this is a fallback.
        raw_str = f"{date}|{amount}|{description}|{account_id}"
        return hashlib.md5(raw_str.encode('utf-8')).hexdigest()

class CSVWriter:
    """
    Helper class to write normalized transactions to CSV files.
    
    This class handles the creation of the output directory and the writing of
    transaction dictionaries to CSV files, ensuring that all required fields
    are present and properly ordered.
    
    The output CSV will always contain the following columns (in order):
    1. Unique Transaction ID
    2. Unique Account ID
    3. Account Name
    4. Date
    5. Description
    6. Payee
    7. Payee Name
    8. Amount
    9. Currency
    10. Category
    11. Is Transfer
    12. Notes
    
    Any additional keys in the transaction dictionaries will be appended as
    extra columns.
    """
    
    REQUIRED_FIELDS = [
        'Unique Transaction ID',
        'Unique Account ID',
        'Account Name',
        'Date',
        'Description',
        'Payee',
        'Payee Name',
        'Amount',
        'Currency',
        'Category',
        'Is Transfer',
        'Notes'
    ]
    
    def __init__(self, output_dir: Path):
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def write(self, transactions: List[Dict[str, Any]], filename: str):
        """Write transactions to a CSV file."""
        if not transactions:
            return

        filepath = self.output_dir / filename
        
        # Collect all fields, ensuring required ones are first
        all_keys = set().union(*(d.keys() for d in transactions))
        fieldnames = self.REQUIRED_FIELDS + [k for k in all_keys if k not in self.REQUIRED_FIELDS]
        
        with open(filepath, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(transactions)
        
        print(f"Saved {len(transactions)} transactions to {filepath}")
