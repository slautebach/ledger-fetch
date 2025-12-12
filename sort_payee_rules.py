"""
Payee Rules Sorter

This utility script sorts and formats the YAML files used for payee normalization (rules).
It enforces a consistent structure to make the rules files easier to maintain manually.

Features:
1. Sorts rules alphabetically by name.
2. Sorts keywords and regex patterns within each rule.
3. Fixes common copy-paste errors (duplicate keys).
4. Enforces 'quoted string' style for scalars using a custom YAML presenter.
"""

import yaml
from pathlib import Path
from typing import Any
import re
from ledger_fetch.config import settings

class QuotedString(str):
    pass

def quoted_scalar_presenter(dumper, data):
    return dumper.represent_scalar('tag:yaml.org,2002:str', data, style='"')

def fix_duplicate_rules_keys(file_path):
    """
    Reads the file as text and removes secondary 'rules:' keys to merge lists.
    This handles the case where a user pastes 'rules: ...' at the bottom of an existing file.
    """
    with open(file_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    
    new_lines = []
    rules_keys_found = 0
    
    for line in lines:
        if re.match(r'^rules:\s*$', line):
            rules_keys_found += 1
            if rules_keys_found > 1:
                continue # Skip subsequent "rules:" lines
        new_lines.append(line)
        
    return "".join(new_lines)

def sort_file(file_path):
    print(f"Processing {file_path.name}...")
    
    try:
        # Pre-process to fix duplicate keys
        content = fix_duplicate_rules_keys(file_path)
        data = yaml.safe_load(content)

        if not data or 'rules' not in data:
            print(f"  Warning: Invalid YAML format or missing 'rules' key in {file_path.name}. Skipping.")
            return

        # Sort patterns within each rule first
        # Sort keywords and regexes within each rule
        for rule in data['rules']:
            if 'keywords' in rule and rule['keywords']:
                rule['keywords'].sort(key=lambda x: str(x).lower())
            if 'regex' in rule and rule['regex']:
                rule['regex'].sort(key=lambda x: str(x).lower())

        # Sort rules by name (case-insensitive)
        data['rules'].sort(key=lambda x: x.get('name', '').lower())

        # Helper to recursively wrap strings in values
        def force_quote_values(obj):
            if isinstance(obj, dict):
                return {k: force_quote_values(v) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [force_quote_values(i) for i in obj]
            elif isinstance(obj, str):
                return QuotedString(obj)
            return obj

        # Transform data locally for dumping
        quoted_data = force_quote_values(data)

        # Create a custom dumper that knows about QuotedString
        class CustomDumper(yaml.SafeDumper):
            pass

        CustomDumper.add_representer(QuotedString, quoted_scalar_presenter)

        # Write back to file
        with open(file_path, 'w', encoding='utf-8') as f:
            yaml.dump(quoted_data, f, Dumper=CustomDumper, default_flow_style=False, sort_keys=False, allow_unicode=True, indent=2)
            
        print(f"  Successfully sorted {len(data['rules'])} rules.")

    except Exception as e:
        print(f"  Error processing {file_path.name}: {e}")

def sort_payee_rules():
    rules_path = settings.payee_rules_path
    
    if not rules_path.exists():
        print(f"Error: {rules_path} not found.")
        return

    if rules_path.is_file():
        sort_file(rules_path)
    elif rules_path.is_dir():
        for file_path in sorted(rules_path.glob("*.yaml")):
            sort_file(file_path)
    else:
        print("Invalid path type.")

if __name__ == "__main__":
    sort_payee_rules()
