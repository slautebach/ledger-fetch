/**
 * Transaction Import and Sync Tool
 *
 * This script is the core logic for importing downloaded bank transactions into Actual Budget.
 * It operates in three distinct phases to ensure data integrity and ease of reconciliation.
 *
 * Phases:
 * 1. **Phase 1: Account Creation**
 *    - Scans for `accounts.csv` files.
 *    - Creates corresponding accounts in Actual Budget if they don't exist.
 *    - Maps Bank Account IDs to Actual Budget Account UUIDs.
 * 
 * 2. **Phase 2: Transaction Import**
 *    - Reads CSV transaction files.
 *    - Normalizes data (dates, amounts, payees).
 *    - Imports transactions into the mapped accounts.
 *    - Handles transfers by appending notes.
 * 
 * 3. **Phase 3: Initial Reconciliation (Optional)**
 *    - For *newly created* accounts, compares the bank's "Current Balance" with the calculated Actual balance.
 *    - Creates a manual balance adjustment transaction if there is a discrepancy to set the initial balance correctly.
 *
 * Usage:
 *   npx ts-node import-transactions.ts [--config-dir <path>] [--transactions-dir <path>] [--bank <bank_name>]
 */
import * as api from '@actual-app/api';
import * as fs from 'fs';
import * as path from 'path';
import * as crypto from 'crypto';
import csv from 'csv-parser';
import yargs from 'yargs/yargs';
import { hideBin } from 'yargs/helpers';
import { Config, loadConfig, initActual, shutdownActual } from './utils';

// Define interfaces
// Config interface removed (imported from utils)

interface CsvTransaction {
  'Unique Transaction ID': string;
  'Unique Account ID': string;
  'Account Name': string;
  'Date': string;
  'Description': string;
  'Payee'?: string;
  'Payee Name'?: string;
  'Amount': string;
  'Currency': string;
  'Category'?: string;
  'Is Transfer'?: string;
  'Transfer Id'?: string;
  'Notes'?: string;
}

interface CsvAccount {
  'Unique Account ID': string;
  'Account Name': string;
  'Account Number': string;
  'Currency': string;
  'Status': string;
  'Type': string;
  'Unified Type': string;
  'Net Value': string;
  'Net Deposits': string;
  'Created At': string;
  'Current Balance': string;
}

interface ActualTransaction {
  date: string;
  amount: number;
  payee_name: string;
  imported_id: string;
  notes: string;
  cleared: boolean;
  account: string;
  transfer_id?: string;
}

// Map CSV Account ID -> Actual Account UUID
let accountMap: Record<string, string> = {};
let ACCOUNT_MAP_FILE = path.join(__dirname, 'config', 'account-map.json'); // Default

function loadAccountMap() {
  if (fs.existsSync(ACCOUNT_MAP_FILE)) {
    try {
      accountMap = JSON.parse(fs.readFileSync(ACCOUNT_MAP_FILE, 'utf8'));
    } catch (e) {
      console.error('Error loading account map:', e);
    }
  }
}

function saveAccountMap() {
  try {
    fs.writeFileSync(ACCOUNT_MAP_FILE, JSON.stringify(accountMap, null, 2), 'utf8');
  } catch (e) {
    console.error('Error saving account map:', e);
  }
}

// Parse arguments
const argv = yargs(hideBin(process.argv))
  .option('config-dir', {
    type: 'string',
    description: 'Path to config directory',
    default: './config'
  })
  .option('transactions-dir', {
    alias: 't',
    type: 'string',
    description: 'Path to transactions directory'
  })
  .option('bank', {
    alias: 'b',
    type: 'string',
    description: 'Specific bank directory to process'
  })
  .parseSync();


async function main() {
  console.log('Starting Actual Budget Sync...');
  loadAccountMap();

  // 1. Load Config
  let config: Config;
  const configPath = path.join(argv['config-dir'], 'config.yaml');
  try {
    config = loadConfig(configPath);
  } catch (e: any) {
    console.error(e.message);
    process.exit(1);
  }

  // Update Map Path
  ACCOUNT_MAP_FILE = path.join(argv['config-dir'], 'account-map.json');
  loadAccountMap(); // Reload with correct path

  // 2. Connect to Actual
  try {
    await initActual(config);
  } catch (e: any) {
    console.error(e.message);
    process.exit(1);
  }

  // Determine Output Dir (Transactions Dir)
  // Determine Output Dir (Transactions Dir)
  let transactionsDir = config.transactions_path || path.resolve(__dirname, '../../transactions');
  if (argv['transactions-dir']) {
    transactionsDir = path.resolve(argv['transactions-dir']);
  }

  if (!fs.existsSync(transactionsDir)) {
    console.error(`Transactions directory not found at configured path: ${transactionsDir}`);
    // Fallback: Try relative to project root (./transactions or ../transactions)
    const fallbackDir = path.resolve(__dirname, '../../transactions');
    if (fs.existsSync(fallbackDir)) {
      console.log(`  Found transactions directory at fallback path: ${fallbackDir}`);
      transactionsDir = fallbackDir;
    } else {
      console.error(`  Fallback path also not found: ${fallbackDir}`);
    }
  }

  console.log(`Scanning for bank directories in: ${transactionsDir}`);

  let bankDirs = fs.readdirSync(transactionsDir)
    .map(item => path.join(transactionsDir, item))
    .filter(fullPath => fs.statSync(fullPath).isDirectory());

  if (argv.bank) {
    const bankName = argv.bank.toLowerCase();
    bankDirs = bankDirs.filter(dir => path.basename(dir).toLowerCase() === bankName);
    if (bankDirs.length === 0) {
      console.error(`Bank "${argv.bank}" not found in ${transactionsDir}`);
      process.exit(1);
    }
  }

  console.log(`Found ${bankDirs.length} bank directories.`);

  // 4. Process Accounts (Phase 1)
  // In this phase, we read the 'accounts.csv' for each bank and ensure
  // that a corresponding account exists in Actual Budget.
  console.log('\n--- Phase 1: Account Creation ---');
  let accountsCache = await api.getAccounts();
  const accountBalances = new Map<string, { balance: number, name: string }>(); // Map Unique Account ID to { balance, name }
  const newlyCreatedBankLinkIds = new Set<string>();
  const earliestTransactionDates = new Map<string, string>(); // Map Unique Account ID to YYYY-MM-DD

  for (const bankDir of bankDirs) {
    const bankName = path.basename(bankDir);
    console.log(`Checking accounts for bank: ${bankName}`);

    const accountsFile = path.join(bankDir, 'accounts.csv');
    if (fs.existsSync(accountsFile)) {
      console.log(`  Found accounts.csv. Syncing accounts...`);
      const csvAccounts: CsvAccount[] = [];

      await new Promise<void>((resolve, reject) => {
        fs.createReadStream(accountsFile)
          .pipe(csv())
          .on('data', (data: any) => {
            csvAccounts.push(data);
            if (data['Unique Account ID']) {
              // Store balance in cents if available, and always store name
              const balance = data['Current Balance'] ? Math.round(parseFloat(data['Current Balance']) * 100) : 0;
              accountBalances.set(data['Unique Account ID'], {
                balance: balance,
                name: data['Account Name']
              });
            }
          })
          .on('end', () => resolve())
          .on('error', (err: any) => reject(err));
      });

      for (const csvAcc of csvAccounts) {
        const accountId = csvAcc['Unique Account ID'];
        const accountName = csvAcc['Account Name'];
        const accountType = csvAcc['Type'];

        if (!accountId) continue;

        // Optimized Lookup: 1. Map, 2. Name
        let actualAccount = null;

        // 1. Check Map
        if (accountMap[accountId]) {
          actualAccount = accountsCache.find((a: any) => a.id === accountMap[accountId]);
        }

        // 2. Check Name (Fallback)
        if (!actualAccount) {
          actualAccount = accountsCache.find((a: any) => a.name === accountName || a.name === accountId);
        }

        // Update Map if found
        if (actualAccount) {
          if (accountMap[accountId] !== actualAccount.id) {
            console.log(`    Mapping Bank ID "${accountId}" to Actual Account "${actualAccount.name}" (${actualAccount.id})`);
            accountMap[accountId] = actualAccount.id;
            saveAccountMap();
          }
        }

        if (!actualAccount) {
          console.log(`    Account "${accountName}" (${accountId}) not found. Creating...`);

          // Determine if account should be off-budget
          const getBudgetStatus = (type: string): boolean => {
            if (!type) return true; // Default to off-budget if unknown
            const lowerType = type.toLowerCase();
            if (lowerType.includes('credit card') ||
              lowerType.includes('checking') ||
              lowerType.includes('chequing') ||
              lowerType.includes('cash')) {
              return false; // On Budget
            }
            return true; // Off Budget (Investment, Mortgage, etc.)
          };

          const isOffBudget = getBudgetStatus(accountType);

          if (true) {
            try {
              const newId = await api.createAccount({
                name: accountName,
                offbudget: isOffBudget
              });
              actualAccount = { id: newId, name: accountName } as any;
              accountsCache.push(actualAccount as any);

              // Update Map
              accountMap[accountId] = newId;
              saveAccountMap();

            } catch (e: any) {
              console.error(`    Error creating account: ${e.message}`);
              continue;
            }
            newlyCreatedBankLinkIds.add(accountId);
            await api.sync();
          }
        }
      }
    }
  }

  if (true) {
    await api.sync();
    accountsCache = await api.getAccounts();
  }

  // 5. Process Transactions (Phase 2)
  // In this phase, we read the transaction CSV files and import them into
  // the appropriate accounts. We rely on the account mapping established in Phase 1.
  console.log('\n--- Phase 2: Transaction Import ---');
  // Refresh cache one last time to be sure
  if (true) {
    accountsCache = await api.getAccounts();
  }

  for (const bankDir of bankDirs) {
    const bankName = path.basename(bankDir);
    console.log(`\nImporting transactions for bank: ${bankName}`);

    const transactionFiles = fs.readdirSync(bankDir)
      .filter(f => f.endsWith('.csv') && f !== 'accounts.csv')
      .map(f => path.join(bankDir, f));

    for (const file of transactionFiles) {
      console.log(`  Processing file: ${path.basename(file)}...`);
      const transactions: CsvTransaction[] = [];

      await new Promise<void>((resolve, reject) => {
        fs.createReadStream(file)
          .pipe(csv())
          .on('data', (data: any) => transactions.push(data))
          .on('end', () => resolve())
          .on('error', (err: any) => reject(err));
      });

      // Group by Account ID
      const transactionsByAccount: Record<string, CsvTransaction[]> = {};
      for (const tx of transactions) {
        const accountId = tx['Unique Account ID'];
        if (!accountId) continue;
        if (!transactionsByAccount[accountId]) {
          transactionsByAccount[accountId] = [];
        }
        transactionsByAccount[accountId].push(tx);

        // Track earliest date
        const txDate = tx['Date'].substring(0, 10);
        const currentEarliest = earliestTransactionDates.get(accountId);
        if (!currentEarliest || txDate < currentEarliest) {
          earliestTransactionDates.set(accountId, txDate);
        }
      }

      for (const [accountId, txs] of Object.entries(transactionsByAccount)) {
        const txAccountName = txs[0]['Account Name'];
        // Check if we have a known name from accounts.csv
        const knownName = accountBalances.get(accountId)?.name;
        const nameToUse = knownName || txAccountName || accountId;

        // Find the account in Actual
        let actualAccount = null;
        if (accountMap[accountId]) {
          actualAccount = accountsCache.find((a: any) => a.id === accountMap[accountId]);
        }
        if (!actualAccount) {
          actualAccount = accountsCache.find((a: any) => a.name === nameToUse || a.name === accountId);
        }

        if (actualAccount) {
          if (accountMap[accountId] !== actualAccount.id) {
            console.log(`    Mapping Bank ID "${accountId}" to Actual Account "${actualAccount.name}" (${actualAccount.id})`);
            accountMap[accountId] = actualAccount.id;
            saveAccountMap();
          }
        }

        if (!actualAccount) {
          console.log(`    Account "${nameToUse}" (${accountId}) not found (not in accounts.csv). Creating on the fly...`);
          if (true) {
            try {
              const newId = await api.createAccount({
                name: nameToUse,
                offbudget: true
              });
              actualAccount = { id: newId, name: nameToUse } as any;
              accountsCache.push(actualAccount as any);

              // Update Map
              accountMap[accountId] = newId;
              saveAccountMap();

            } catch (e: any) {
              console.error(`    Error creating account: ${e.message}`);
              continue; // Skip transactions if account creation failed
            }
            newlyCreatedBankLinkIds.add(accountId);
          }
        }

        if (!actualAccount) continue; // Safety check

        // Prepare Transactions
        const actualTransactions: ActualTransaction[] = txs.map(tx => {
          // Amount: CSV is usually float, Actual needs integer cents
          const amount = Math.round(parseFloat(tx['Amount']) * 100);

          // Date: Actual needs YYYY-MM-DD
          const dateStr = tx['Date'].substring(0, 10);

          // Payee: Use 'Payee' or 'Payee Name' if available, else 'Description'
          const payee = tx['Payee Name'] || tx['Payee'] || tx['Description'];

          // Notes: Use 'Notes' if available
          let notes = tx['Description'] || tx['Notes'] || '';

          // Append Transfer status to notes
          if (tx['Is Transfer'] === 'True' || tx['Is Transfer'] === 'true' || tx['Is Transfer'] === '1') {
            notes = notes ? `${notes} (Transfer)` : '(Transfer)';
          }

          return {
            date: dateStr,
            amount: amount,
            payee_name: payee,
            imported_id: tx['Unique Transaction ID'],
            notes: notes,
            cleared: true,
            account: actualAccount.id,
            transfer_id: tx['Transfer Id'] || undefined
          };
        });

        console.log(`    Importing ${actualTransactions.length} transactions for account "${actualAccount.name}"...`);
        if (true) {
          try {
            const result = await api.importTransactions(actualAccount.id, actualTransactions);
            console.log(`      Added: ${result.added.length}, Updated: ${result.updated.length}, Errors: ${result.errors ? result.errors.length : 0}`);
          } catch (e: any) {
            console.error(`      Error importing transactions: ${e.message}`);
          }
        }
      }
      if (true) {
        console.log(`    Syncing transactions for ${path.basename(file)}...`);
        await api.sync();
      }
    }
  }

  // 6. Phase 3: Reconciliation (Global)
  // In this phase, we compare the "Current Balance" from the bank's accounts.csv
  // with the calculated balance in Actual Budget. If there's a mismatch for a NEW account,
  // we create an initial balance adjustment transaction.
  if (true) {
    console.log('\n--- Phase 3: Reconciliation ---');
    // Refresh cache to get latest balances/accounts
    accountsCache = await api.getAccounts();

    for (const [uniqueAccountId, info] of accountBalances) {
      const { balance: expectedBalance, name: accountName } = info;

      // Find the account in Actual
      let actualAccount = null;
      if (accountMap[uniqueAccountId]) {
        actualAccount = accountsCache.find((a: any) => a.id === accountMap[uniqueAccountId]);
      }
      if (!actualAccount) {
        actualAccount = accountsCache.find((a: any) => a.name === accountName || a.name === uniqueAccountId);
      }

      if (!actualAccount) {
        console.log(`    Skipping reconciliation for "${accountName}" (${uniqueAccountId}) - Account not found in Actual.`);
        continue;
      }

      try {
        let currentBalance = 0;
        if (actualAccount.id === 'dry-run-id') {
          console.log(`    [Dry Run] Assuming current balance is 0 for new account.`);
          currentBalance = 0;
        } else {
          currentBalance = await api.getAccountBalance(actualAccount.id);
        }

        if (expectedBalance !== currentBalance) {
          const diff = expectedBalance - currentBalance;
          console.log(`    [Balance Mismatch] Account "${actualAccount.name}": Expected ${expectedBalance / 100}, Actual ${currentBalance / 100}, Diff ${diff / 100}`);

          // CHECK IF THIS IS A NEW ACCOUNT
          if (newlyCreatedBankLinkIds.has(uniqueAccountId)) {
            console.log(`    -> Account is NEW. Creating initial reconciliation transaction...`);

            const transactionId = crypto.createHash('md5').update(uniqueAccountId + '_initial_reconcile').digest('hex');

            // Calculate date: Day before earliest transaction, or today if no transactions
            let dateStr = new Date().toISOString().substring(0, 10);
            const earliestDate = earliestTransactionDates.get(uniqueAccountId);
            if (earliestDate) {
              const dateObj = new Date(earliestDate);
              dateObj.setDate(dateObj.getDate() - 1);
              dateStr = dateObj.toISOString().substring(0, 10);
              console.log(`      Earliest transaction: ${earliestDate}. Setting reconciliation date to: ${dateStr}`);
            } else {
              console.log(`      No transactions found. Setting reconciliation date to today: ${dateStr}`);
            }

            const reconciliationTx: ActualTransaction = {
              date: dateStr,
              amount: diff, // The difference is what we need to add/subtract
              payee_name: 'Manual Balance Adjustment',
              imported_id: transactionId,
              notes: 'Initial reconciliation balance adjustment',
              cleared: true,
              account: actualAccount.id
            };

            if (true) {
              try {
                await api.importTransactions(actualAccount.id, [reconciliationTx]);
                console.log(`      SUCCESS: Created initial reconciliation transaction for ${diff / 100}`);
              } catch (e: any) {
                console.error(`      ERROR: Failed to create reconciliation transaction: ${e.message}`);
              }
            }
          } else {
            console.log(`    -> Account is EXISTING. Skipping auto-reconciliation.`);
          }

        } else {
          console.log(`    Account "${actualAccount.name}" is balanced.`);
        }
      } catch (e: any) {
        console.error(`    Error reconciling account "${accountName}": ${e.message}`);
      }
    }
  }

  // Sync after processing all files
  if (true) {
    console.log('Syncing with server...');
    await api.sync();
  }

  console.log('Sync complete.');
  await shutdownActual();
}

main().catch(err => {
  console.error(err);
  process.exit(1);
});
