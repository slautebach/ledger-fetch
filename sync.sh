#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

python3 "$SCRIPT_DIR/main.py" --all
python3 "$SCRIPT_DIR/main.py" --normalize

cd "$SCRIPT_DIR/actual-sync" || exit
npm run import-transactions -- --since 2025-02-01
npm run tag-transactions -- --commit

cd "$SCRIPT_DIR" || exit
