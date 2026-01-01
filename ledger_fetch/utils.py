"""
Utility Functions and Classes

This module provides common utilities used across the application, including:
1.  Transaction Normalization: Cleaning descriptions, parsing dates, and generating IDs.
2.  Payee Normalization: Standardizing payee names based on configurable rules.
3.  CSV Writing: Handling the robust export of transaction data to CSV files.
"""

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
        # Ensure path is absolute if possible, or relative to CWD
        
        cls._payee_rules = []
        
        if rules_path.exists():
            if rules_path.is_dir():
                # Load all .yaml and .yml files in the directory
                for file_path in sorted(rules_path.glob("*.y*ml")):
                    try:
                        with open(file_path, 'r', encoding='utf-8') as f:
                            data = yaml.safe_load(f)
                            if data and 'rules' in data:
                                cls._payee_rules.extend(data['rules'])
                                print(f"Loaded {len(data['rules'])} rules from {file_path.name}")
                    except Exception as e:
                         print(f"Warning: Failed to load payee rules from {file_path}: {e}")
            else:
                # Load single file
                try:
                    with open(rules_path, 'r', encoding='utf-8') as f:
                        data = yaml.safe_load(f)
                        if data and 'rules' in data:
                            cls._payee_rules = data['rules']
                            print(f"Loaded {len(data['rules'])} rules from {rules_path.name}")
                except Exception as e:
                    print(f"Warning: Failed to load payee rules from {rules_path}: {e}")
        else:
            print(f"Warning: Payee rules path not found at {rules_path}")
            
        return cls._payee_rules

    @classmethod
    def normalize_payee(cls, raw_payee: str) -> str:
        """
        Normalize payee name based on configured rules.
        
        Applies a 'First Match Wins' strategy using the rules defined in
        payee_rules.yaml. Supports both simple keywords (substring match)
        and regex patterns.
        """
        if not raw_payee:
            return ""
            
        cleaned = cls.clean_description(raw_payee)
        rules = cls._load_payee_rules()
        
        for rule in rules:
            name = rule.get('name')
            
            # 1. Simple Keywords (Preferred for speed/simplicity)
            keywords = rule.get('keywords') or []
            for keyword in keywords:
                 if keyword.lower() in cleaned.lower():
                     return name

            # 2. Regex Patterns
            regexes = rule.get('regex') or []
            for pattern in regexes:
                try:
                    if re.search(pattern, cleaned, re.IGNORECASE):
                        return name
                except re.error:
                    print(f"Warning: Invalid regex pattern '{pattern}' for rule '{name}'")
                    
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
            # Added: %d %b %Y (01 Aug 2025), %d %b %Y (1 Aug 2025), %B %d, %Y (August 1, 2025)
            formats = [
                '%Y-%m-%d', 
                '%m/%d/%Y', 
                '%d/%m/%Y', 
                '%Y/%m/%d', 
                '%b %d, %Y', 
                '%d %b %Y', 
                '%B %d, %Y',
                '%Y-%m-%dT%H:%M:%S', # ISO with time
                '%Y-%m-%dT%H:%M:%S.%f', # ISO with microseconds
                '%Y-%m-%dT%H:%M:%S%z', # ISO with time and timezone
                '%Y-%m-%dT%H:%M:%S.%f%z' # ISO with microseconds and timezone
            ]
            
            for fmt in formats:
                try:
                    dt = datetime.strptime(str(date_str), fmt)
                    return dt.strftime('%Y-%m-%d')
                except ValueError:
                    continue
            
            # If pandas timestamp or similar
            if hasattr(date_str, 'strftime'):
                return date_str.strftime('%Y-%m-%d')
                
            # If we get here, we couldn't parse it. 
            # Check if it already looks like YYYY-MM-DD
            if re.match(r'^\d{4}-\d{2}-\d{2}$', str(date_str)):
                return str(date_str)

            print(f"Warning: Could not normalize date '{date_str}'")
            return str(date_str)
        except Exception as e:
            print(f"Error normalizing date '{date_str}': {e}")
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
    
    Any additional keys in the transaction dictionaries will be appended as
    extra columns.
    """
    
    def __init__(self, output_dir: Path):
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def write(self, transactions: List[Dict[str, Any]], filename: str, fieldnames: List[str] = None):
        """
        Write transactions (or any dicts) to a CSV file.
        
        Args:
            transactions: List of dictionaries to write.
            filename: Name of the output file.
            fieldnames: Optional list of field names to enforce order and presence.
                        If provided, these fields will come first.
                        Any extra fields found in the data will be appended.
        """
        if not transactions:
            return

        filepath = self.output_dir / filename
        
        # 1. Collect all potential keys
        all_keys = set().union(*(d.keys() for d in transactions))
        
        # 2. Identify keys that have at least one non-empty value
        active_keys = set()
        for key in all_keys:
            for d in transactions:
                val = d.get(key)
                if val is not None:
                     s_val = str(val).strip()
                     if s_val != "" and s_val.lower() != "nan":
                        active_keys.add(key)
                        break
        
        # 3. Filter fieldnames to only include active keys
        if fieldnames:
             # Use provided fieldnames if they are active + any extra active keys
             final_fieldnames = [k for k in fieldnames if k in active_keys] + \
                                [k for k in all_keys if k not in fieldnames and k in active_keys]
        else:
             # Just sort active keys
             final_fieldnames = sorted(list(active_keys))
        
        with open(filepath, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=final_fieldnames, extrasaction='ignore')
            writer.writeheader()
            writer.writerows(transactions)
        
        print(f"Saved {len(transactions)} transactions to {filepath}")
