#!/bin/bash
# npm run import-payees
# npm run import-accounts
# npm run sync-budget-categories
# npm run sync-rules
npm run import-transactions -- --since 2025-02-01
npm run tag-transactions -- --commit
