#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

python3 "$SCRIPT_DIR/main.py" --all
python3 "$SCRIPT_DIR/main.py" --normalize
# python3 "$SCRIPT_DIR/link_transfers.py"
# python3 "$SCRIPT_DIR/process_payees.py"
