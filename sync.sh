#!/usr/bin/bash
# Fetch transactions, normalize payees, and (opt-in) sync to Actual Budget.
#
# The Actual sync is gated on a clean fetch: main.py exits non-zero when any
# bank returns zero transactions despite having existing history, and we
# refuse to import a partial dataset in that case.
#
#   RUN_ACTUAL_SYNC=1 ./sync.sh   # also import + tag into Actual Budget
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ -d "$SCRIPT_DIR/venv" ]; then
    # shellcheck source=/dev/null
    source "$SCRIPT_DIR/venv/bin/activate"
fi

fetch_rc=0
python3 "$SCRIPT_DIR/main.py" --all --parallel || fetch_rc=$?
python3 "$SCRIPT_DIR/main.py" --normalize || true

if [ "$fetch_rc" -ne 0 ]; then
    echo ""
    echo "Fetch reported failures (exit $fetch_rc) - skipping Actual sync."
    echo "Check the RUN SUMMARY and logs/ from the fetch step above."
    exit "$fetch_rc"
fi

if [ "${RUN_ACTUAL_SYNC:-0}" = "1" ]; then
    cd "$SCRIPT_DIR/actual-sync" || exit 1
    npm run import-transactions
    npm run tag-transactions -- --commit
else
    echo ""
    echo "Fetch OK. Actual sync skipped (set RUN_ACTUAL_SYNC=1 to enable)."
fi
