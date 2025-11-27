import yaml
from pathlib import Path

def sort_payee_rules():
    file_path = Path("payee_rules.yaml")
    
    if not file_path.exists():
        print(f"Error: {file_path} not found.")
        return

    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f)

        if not data or 'rules' not in data:
            print("Error: Invalid YAML format or missing 'rules' key.")
            return

        # Sort rules by name (case-insensitive)
        data['rules'].sort(key=lambda x: x.get('name', '').lower())

        # Write back to file
        with open(file_path, 'w', encoding='utf-8') as f:
            # default_flow_style=False keeps it in block format (readable)
            # sort_keys=False preserves key order within dictionaries (though we only have name/patterns usually)
            # allow_unicode=True allows characters like é
            yaml.dump(data, f, default_flow_style=False, sort_keys=False, allow_unicode=True, indent=2)
            
        print(f"Successfully sorted {len(data['rules'])} rules in {file_path}")

    except Exception as e:
        print(f"An error occurred: {e}")

if __name__ == "__main__":
    sort_payee_rules()
