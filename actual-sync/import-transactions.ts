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
 *   npx ts-node import-transactions.ts [--config-dir <path>] [--transactions-dir <path>] [--bank <bank_name>] [--since <YYYY-MM-DD>]
 */
import * as api from '@actual-app/api';
import * as fs from 'fs';
import * as path from 'path';
import * as crypto from 'crypto';
import csv from 'csv-parser';
import yargs from 'yargs/yargs';
import { hideBin } from 'yargs/helpers';
import { Config, loadConfig, initActual, shutdownActual, loadAccounts, appendAccount, Account, syncAccountsWithActual, saveAccounts } from './utils';
import { loadTagConfig, matchesRule, TagConfig, TagRule, escapeRegex } from './tag-utils';

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
  'Pending'?: string;
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
// Account Details (off_budget, etc)
interface AccountDetail {
  off_budget?: boolean;
  display_name?: string;
}
let accountDetails: Record<string, AccountDetail> = {};

// Parse arguments
const argv = yargs(hideBin(process.argv))
  .option('config-dir', {
    type: 'string',
    description: 'Path to config directory',
    default: '../config'
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
  .option('since', {
    alias: 'date',
    type: 'string',
    description: 'Import transactions on or after this date (YYYY-MM-DD)'
  })
  .parseSync();



async function main() {
  console.log('Starting Actual Budget Sync...');

  // 1. Load Config
  let config: Config;
  const configPath = path.join(argv['config-dir'], 'config.yaml');
  try {
    config = loadConfig(configPath);
  } catch (e: any) {
    console.error(e.message);
    process.exit(1);
  }

  // 1b. Connect to Actual (moved up to support sync)
  try {
    await initActual(config);
  } catch (e: any) {
    console.error(e.message);
    process.exit(1);
  }

  // 1c. Sync Accounts with Actual
  console.log('Syncing local accounts.yaml with Actual Budget...');
  try {
    const remoteAccounts = await api.getAccounts();
    syncAccountsWithActual(argv['config-dir'], remoteAccounts);
  } catch (e: any) {
    console.error(`Failed to sync accounts: ${e.message}`);
    // Proceed? Or fail? Let's fail to ensure consistency
    process.exit(1);
  }

  // 1d. Load Accounts from YAML (Reload after sync)
  const accountsList = loadAccounts(argv['config-dir']);
  console.log(`Loaded ${accountsList.length} accounts from accounts.yaml`);

  // Populate maps for O(1) lookups
  accountMap = {};
  accountDetails = {};
  for (const acc of accountsList) {
    accountMap[acc.id] = acc.actual_id;
    accountDetails[acc.id] = {
      off_budget: acc.off_budget,
      display_name: acc.name
    };
  }

  // 3. Determine Transactions Dir

  // 1c. Load Tag Config
  let tagConfig: TagConfig | undefined;

  // 2. Connect to Actual (Already connected in step 1b)
  /*
  try {
    await initActual(config);
  } catch (e: any) {
    console.error(e.message);
    process.exit(1);
  }
  */

  // Determine Output Dir (Transactions Dir)
  // Determine Output Dir (Transactions Dir)
  let transactionsDir = config.ledger_fetch?.transactions_path || path.resolve(__dirname, '../../transactions');
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
  const accountBalances = new Map<string, { balance: number, name: string, type: string }>(); // Map Unique Account ID to { balance, name, type }
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
                name: data['Account Name'],
                type: data['Type']
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
            console.warn(`    WARNING: Account mapping changed for ${accountId} but ` +
              `updating existing entries in accounts.yaml is not yet supported. ` +
              `Please update accounts.yaml manually.`);
          }
        }

        if (!actualAccount) {
          console.log(`    Account "${accountName}" (${accountId}) not found. Creating...`);

          // Check if we have an existing entry in accounts.yaml (but missing actual_id)
          const configAccount = accountsList.find(a => a.id === accountId);

          let newId: string;
          let isOffBudget: boolean;

          if (configAccount) {
            console.log(`    Found existing config for "${accountId}". Using config settings...`);
            isOffBudget = configAccount.off_budget;

            try {
              newId = await api.createAccount({
                name: configAccount.name,
                offbudget: isOffBudget
              });
              // Update existing config object
              configAccount.actual_id = newId;
              configAccount.closed = false; // Assume open if we are linking

              // Save updated list
              saveAccounts(argv['config-dir'], accountsList);
              console.log(`    -> Created account and updated config: ${newId}`);

            } catch (e: any) {
              console.error(`    Error creating account from config: ${e.message}`);
              continue;
            }

          } else {
            // Creating Brand New Account (not in config)
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

            isOffBudget = getBudgetStatus(accountType);

            try {
              newId = await api.createAccount({
                name: accountName,
                offbudget: isOffBudget
              });

              // Add new to list
              accountsList.push({
                id: accountId,
                actual_id: newId,
                name: accountName,
                off_budget: isOffBudget,
                closed: false
              });

              // Save updated list
              saveAccounts(argv['config-dir'], accountsList);
              console.log(`    -> Created new account and appended to config: ${newId}`);

            } catch (e: any) {
              console.error(`    Error creating account: ${e.message}`);
              continue;
            }
          }

          actualAccount = { id: newId, name: configAccount ? configAccount.name : accountName } as any;
          accountsCache.push(actualAccount as any);

          // Update Map
          accountMap[accountId] = newId;
          newlyCreatedBankLinkIds.add(accountId);
          await api.sync();

        }
      }
    }
  }

  // Sync before proceeding
  await api.sync();
  accountsCache = await api.getAccounts();

  // 5. Process Transactions (Phase 2)
  // In this phase, we read the transaction CSV files and import them into
  // the appropriate accounts. We rely on the account mapping established in Phase 1.
  console.log('\n--- Phase 2: Transaction Import ---');
  // Refresh cache one last time to be sure
  // Refresh cache one last time to be sure
  accountsCache = await api.getAccounts();

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
        // Removed skipping of pending transactions


        // Filter by date if --since argument is provided
        if (argv.since) {
          const txDate = tx['Date'].substring(0, 10);
          if (txDate < (argv.since as string)) {
            // console.log(`      Skipping transaction from ${txDate} (before ${argv.since})`);
            continue;
          }
        }

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
            console.warn(`    WARNING: Account mapping changed for ${accountId} but ` +
              `updating existing entries in accounts.yaml is not yet supported. ` +
              `Please update accounts.yaml manually.`);
          }
        }

        if (!actualAccount) {
          console.log(`    Account "${nameToUse}" (${accountId}) not found (not in accounts.csv). Creating on the fly...`);

          try {
            const newId = await api.createAccount({
              name: nameToUse,
              offbudget: true
            });
            actualAccount = { id: newId, name: nameToUse } as any;
            accountsCache.push(actualAccount as any);

            // Update Map
            accountMap[accountId] = newId;
            // Persist to accounts.yaml
            appendAccount(argv['config-dir'], {
              id: accountId,
              actual_id: newId,
              name: nameToUse,
              off_budget: true
            });

          } catch (e: any) {
            console.error(`    Error creating account: ${e.message}`);
            continue; // Skip transactions if account creation failed
          }
          newlyCreatedBankLinkIds.add(accountId);
        }

        if (!actualAccount) continue; // Safety check

        // Prepare Transactions
        const isInvestment = accountBalances.get(accountId)?.type?.toLowerCase().includes('investment');

        let existingTransactions: any[] = [];
        try {
          // Fetch existing transactions to match against
          const earliestDateStr = earliestTransactionDates.get(accountId) || '2000-01-01';
          existingTransactions = await api.getTransactions(actualAccount.id, earliestDateStr, '2100-01-01');
        } catch (e: any) {
          console.warn(`    Warning: Could not fetch existing transactions: ${e.message}`);
        }

        const existingTxMap = new Map();
        for (const t of existingTransactions) {
          if (t.imported_id) {
            existingTxMap.set(t.imported_id, t);
          }
        }

        const actualTransactions: ActualTransaction[] = [];

        for (const tx of txs) {
          // Payee: Use 'Payee' or 'Payee Name' if available, else 'Description'
          const payee = tx['Payee Name'] || tx['Payee'] || tx['Description'];

          // Notes: Use 'Notes' if available
          let notes = tx['Description'] || tx['Notes'] || '';

          // Amount: CSV is usually float, Actual needs integer cents
          let amountVal = parseFloat(tx['Amount']);

          // Special logic for Investment Accounts: "Trade" transactions should be 0 amount
          const tradePayees = ['Trade', 'Market buy', 'Market sell', 'Recurring Buy'];
          if (isInvestment && payee && tradePayees.includes(payee)) {
            const originalAmountNote = `(Original Amount: ${tx['Amount']})`;
            notes = notes ? `${notes} ${originalAmountNote}` : originalAmountNote;
            amountVal = 0;
          }

          const amount = Math.round(amountVal * 100);

          // Date: Actual needs YYYY-MM-DD
          const dateStr = tx['Date'].substring(0, 10);

          // Append Transfer status to notes
          if (tx['Is Transfer'] === 'True' || tx['Is Transfer'] === 'true' || tx['Is Transfer'] === '1') {
            notes = notes ? `${notes} (Transfer)` : '(Transfer)';
          }

          let importTxId = tx['Unique Transaction ID']?.trim();
          if (!importTxId) {
            console.warn(`    No Unique Transaction ID found for transaction. Generating deterministic ID based on content.`);
            const idString = `${dateStr}:${amount}:${payee}:${actualAccount.id}`;
            importTxId = crypto.createHash('md5').update(idString).digest('hex');
          }

          const isPending = (tx['Pending'] === 'True' || tx['Pending'] === 'true');
          const existingTx = existingTxMap.get(importTxId);

          if (existingTx) {
            if (isPending) {
              // Existing transaction is still pending in our CSV. Skip it to avoid unnecessary updates.
              continue;
            } else {
              // CSV transaction is no longer pending. Did it use to be pending?
              const existingNotes = existingTx.notes || '';
              const wasPending = existingNotes.includes('#pending') || existingNotes.toLowerCase().startsWith('pending:');

              if (wasPending) {
                console.log(`      [DEBUG] Transaction posted: ${payee} (${dateStr})`);
                let cleanedNotes = existingNotes.replace(/^Pending:\s*/i, '').replace(/\s*#pending/gi, '').trim();
                actualTransactions.push({
                  date: dateStr,
                  amount: amount,
                  payee_name: payee,
                  imported_id: importTxId,
                  notes: cleanedNotes,
                  cleared: true,
                  account: actualAccount.id,
                  transfer_id: tx['Transfer Id'] || undefined
                });
              } else {
                // Was already posted. Skip to avoid rewriting it.
                continue;
              }
            }
          } else {
            // New transaction
            let finalNotes = notes;
            if (isPending) {
              finalNotes = `Pending: ${notes} #pending`;
            }
            actualTransactions.push({
              date: dateStr,
              amount: amount,
              payee_name: payee,
              imported_id: importTxId,
              notes: finalNotes,
              cleared: !isPending,
              account: actualAccount.id,
              transfer_id: tx['Transfer Id'] || undefined
            });
          }
        }

        console.log(`    Importing ${actualTransactions.length} transactions for account "${actualAccount.name}"...`);
        if (actualTransactions.length > 0) {
          const dates = actualTransactions.map(t => t.date).sort();
          console.log(`      [DEBUG] Date range to import: ${dates[0]} to ${dates[dates.length - 1]}`);
        }
        try {
          const result = await api.importTransactions(actualAccount.id, actualTransactions);
          console.log(`      Added: ${result.added.length}, Updated: ${result.updated.length}, Errors: ${result.errors ? result.errors.length : 0}`);
          if (result.errors && result.errors.length > 0) {
            console.log(`      [DEBUG] Import Errors: ${JSON.stringify(result.errors)}`);
          }
        } catch (e: any) {
          console.error(`      Error importing transactions: ${e.message}`);
        }
      }
    }
    console.log(`    Syncing transactions for bank ${bankName}...`);
    await api.sync();
  }

  // Phase 2.5: Post-Import Tagging
  // Now that transactions are imported and (potentially) matched to existing payees/categories,
  // we run the tagging rules again to ensure everything is caught.
  if (tagConfig) {
    console.log('\n--- Phase 2.5: Post-Import Tagging ---');

    // Determine lookback window. If --since is set, use that. 
    // Otherwise, use a safe default (e.g. 30 days ago) or ideally the earliest date we touched.
    // let's use the earliestTransactionDates map we built.

    // We need to process by account

    // Refresh cache one last time to be sure
    accountsCache = await api.getAccounts(); // definitions might have changed if we created new ones? (actually we did in phase 1)

    for (const [accountId, earliestDate] of earliestTransactionDates) {
      // Resolve Actual Account ID
      let actualAccountId = accountMap[accountId];
      if (!actualAccountId) {
        // Try name...
        const balanceInfo = accountBalances.get(accountId);
        if (balanceInfo) {
          const acc = accountsCache.find((a: any) => a.name === balanceInfo.name || a.name === accountId);
          if (acc) actualAccountId = acc.id;
        }
      }

      if (!actualAccountId) continue;

      const actualAccount = accountsCache.find((a: any) => a.id === actualAccountId);
      if (!actualAccount) continue;

      console.log(`  Running tagging rules for account "${actualAccount.name}" (since ${earliestDate})...`);

      const transactions = await api.getTransactions(actualAccountId, earliestDate, '2100-01-01');

      const updates: any[] = [];

      // Pre-fetch Payees and Categories for resolution
      const payees = await api.getPayees();
      const payeeMap = new Map<string, string>();
      payees.forEach((p: any) => payeeMap.set(p.id, p.name));

      const categories = await api.getCategories();
      const categoryMap = new Map<string, string>();
      categories.forEach((c: any) => categoryMap.set(c.id, c.name));

      for (const tx of transactions) {
        const payeeName = payeeMap.get(tx.payee || '') || ''; // ID -> Name
        const categoryName = categoryMap.get(tx.category || '') || ''; // ID -> Name
        const accountName = actualAccount.name;

        let currentNotes = tx.notes || '';
        const originalNotes = currentNotes;

        for (const rule of tagConfig.rules) {
          // Using 'tx.amount' (integer cents) directly
          if (matchesRule({ ...tx, amount: tx.amount }, rule, payeeName || '', String(accountName || ''), categoryName || '')) {
            for (const tag of rule.tags) {
              // Helper to add tag safely
              const tagRegex = new RegExp(`(?<=^|\\s)${escapeRegex(tag)}(?=$|\\s)`, 'i');
              if (!tagRegex.test(currentNotes)) {
                currentNotes = (currentNotes + ' ' + tag).trim();
              }
            }
          }
        }

        if (currentNotes !== originalNotes) {
          updates.push({
            id: tx.id,
            notes: currentNotes
          });
        }
      }

      if (updates.length > 0) {
        console.log(`    Applying ${updates.length} tag updates...`);
        await api.batchBudgetUpdates(async () => {
          for (const update of updates) {
            await api.updateTransaction(update.id, { notes: update.notes });
          }
        });
      }
    }
    await api.sync();
  }

  // 6. Phase 3: Reconciliation (Global)
  // In this phase, we compare the "Current Balance" from the bank's accounts.csv
  // with the calculated balance in Actual Budget. If there's a mismatch for a NEW account,
  // we create an initial balance adjustment transaction.
  console.log('\n--- Phase 3: Reconciliation ---');
  // Refresh cache to get latest balances/accounts
  accountsCache = await api.getAccounts();

  for (const [uniqueAccountId, info] of accountBalances) {
    const { balance: expectedBalance, name: accountName, type: accountType } = info;

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

        // --- INVESTMENT ACCOUNT LOGIC ---
        const isInvestment = accountType && accountType.toLowerCase().includes('investment');

        if (isInvestment) {
          console.log(`    -> Investment Account Detected. Creating adjustment transaction.`);

          const transactionId = crypto.createHash('md5').update(uniqueAccountId + '_reconcile_' + new Date().toISOString().substring(0, 10)).digest('hex');
          const dateStr = new Date().toISOString().substring(0, 10); // Today's date

          const payeeName = diff > 0 ? 'Unrealized Investment Gains' : 'Unrealized Investment Loss';

          const reconciliationTx: ActualTransaction = {
            date: dateStr,
            amount: diff,
            payee_name: payeeName,
            imported_id: transactionId,
            notes: 'Automatic Investment Reconciliation',
            cleared: true,
            account: actualAccount.id
          };

          try {
            await api.importTransactions(actualAccount.id, [reconciliationTx]);
            console.log(`      SUCCESS: Created investment adjustment (${payeeName}) for ${diff / 100}`);
          } catch (e: any) {
            console.error(`      ERROR: Failed to create investment adjustment: ${e.message}`);
          }

        } else {
          // --- STANDARD ACCOUNT LOGIC (Initial Only) ---

          // Check for existing reconciliation transaction
          const RECONCILIATION_NOTE = 'Initial reconciliation balance adjustment';
          let allTransactions: any[] = [];
          try {
            // Fetch all transactions to check for existing reconciliation (using wide date range)
            allTransactions = await api.getTransactions(actualAccount.id, '1900-01-01', '2100-01-01');
          } catch (e: any) {
            console.warn(`    Warning: Could not fetch transactions for check: ${e.message}`);
          }

          const existingReconciliationTx = allTransactions.find((t: any) =>
            t.notes === RECONCILIATION_NOTE || (t.notes && t.notes.includes(RECONCILIATION_NOTE))
          );

          if (existingReconciliationTx) {
            console.log(`    -> reconciliation transaction already exists (Date: ${existingReconciliationTx.date}). Skipping auto-reconciliation.`);
          } else {
            console.log(`    -> Reconciliation needed (No existing '${RECONCILIATION_NOTE}' found). Creating transaction...`);

            const transactionId = crypto.createHash('md5').update(uniqueAccountId + '_initial_reconcile').digest('hex');

            // Calculate date: Day before earliest transaction (from CSV or existing), or today
            let dateStr = new Date().toISOString().substring(0, 10);

            let referenceDate = earliestTransactionDates.get(uniqueAccountId);

            // If no CSV transactions, try to find earliest existing transaction
            if (!referenceDate && allTransactions.length > 0) {
              // sort by date asc
              const sortedTxs = allTransactions.sort((a: any, b: any) => a.date.localeCompare(b.date));
              referenceDate = sortedTxs[0].date;
            }

            if (referenceDate) {
              const dateObj = new Date(referenceDate);
              dateObj.setDate(dateObj.getDate() - 1);
              dateStr = dateObj.toISOString().substring(0, 10);
              console.log(`      Reference transaction date: ${referenceDate}. Setting reconciliation date to: ${dateStr}`);
            } else {
              console.log(`      No transactions found to reference. Setting reconciliation date to today: ${dateStr}`);
            }

            const reconciliationTx: ActualTransaction = {
              date: dateStr,
              amount: diff, // The difference is what we need to add/subtract
              payee_name: 'Manual Balance Adjustment',
              imported_id: transactionId,
              notes: RECONCILIATION_NOTE,
              cleared: true,
              account: actualAccount.id
            };

            try {
              await api.importTransactions(actualAccount.id, [reconciliationTx]);
              console.log(`      SUCCESS: Created initial reconciliation transaction for ${diff / 100}`);
            } catch (e: any) {
              console.error(`      ERROR: Failed to create reconciliation transaction: ${e.message}`);
            }
          }
        }

      } else {
        console.log(`    Account "${actualAccount.name}" is balanced.`);
      }
    } catch (e: any) {
      console.error(`    Error reconciling account "${accountName}": ${e.message}`);
    }
  }
  console.log('Syncing with server...');
  await api.sync();

  console.log('Sync complete.');
  await shutdownActual();
}

main().catch(err => {
  console.error(err);
  process.exit(1);
});
