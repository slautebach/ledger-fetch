import * as api from '@actual-app/api';
import * as fs from 'fs';
import * as path from 'path';
import * as crypto from 'crypto';
import * as yaml from 'js-yaml';
import csv from 'csv-parser';
import yargs from 'yargs/yargs';
import { hideBin } from 'yargs/helpers';

// Define interfaces
interface Config {
  actual: {
    server_url: string;
    password: string;
    sync_id: string;
  };
  output_dir?: string;
}

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
}

// Parse arguments
const argv = yargs(hideBin(process.argv))
  .option('config', {
    alias: 'c',
    type: 'string',
    description: 'Path to config file',
    default: '../config.yaml'
  })
  .option('dry-run', {
    type: 'boolean',
    description: 'Run without making changes',
    default: false
  })
  .option('transactions-dir', {
    alias: 't',
    type: 'string',
    description: 'Path to transactions directory'
  })
  .parseSync();

async function main() {
  console.log('Starting Actual Budget Sync...');

  // 1. Load Config
  let config: Config;
  try {
    const configPath = path.resolve(argv.config);
    if (!fs.existsSync(configPath)) {
        console.error(`Config file not found at ${configPath}`);
        process.exit(1);
    }
    const fileContents = fs.readFileSync(configPath, 'utf8');
    config = yaml.load(fileContents) as Config;
  } catch (e) {
    console.error('Error loading config:', e);
    process.exit(1);
  }

  if (!config.actual || !config.actual.server_url || !config.actual.password || !config.actual.sync_id) {
    console.error('Missing "actual" configuration in config.yaml');
    console.error('Please add: actual: { server_url, password, sync_id }');
    process.exit(1);
  }

  // 2. Connect to Actual
  console.log(`Connecting to Actual Budget at ${config.actual.server_url}...`);
  const dataDir = path.resolve(__dirname, 'data');
  if (!fs.existsSync(dataDir)) {
      console.log(`Creating data directory at ${dataDir}...`);
      fs.mkdirSync(dataDir, { recursive: true });
  }

  try {
    await api.init({
      dataDir: dataDir,
      serverURL: config.actual.server_url,
      password: config.actual.password,
    });
    await api.downloadBudget(config.actual.sync_id);
    console.log('Connected to budget.');
  } catch (e) {
    console.error('Failed to connect to Actual Budget:', e);
    process.exit(1);
  }

  // 3. Find Bank Directories
  let transactionsDir = config.output_dir ? path.resolve(path.dirname(argv.config), config.output_dir) : path.resolve(__dirname, '../transactions');
  
  if (argv['transactions-dir']) {
      transactionsDir = path.resolve(argv['transactions-dir']);
  }

  console.log(`Scanning for bank directories in: ${transactionsDir}`);
  
  if (!fs.existsSync(transactionsDir)) {
      console.error(`Transactions directory not found: ${transactionsDir}`);
      await api.shutdown();
      process.exit(1);
  }

  const bankDirs = fs.readdirSync(transactionsDir)
    .map(item => path.join(transactionsDir, item))
    .filter(fullPath => fs.statSync(fullPath).isDirectory());

  console.log(`Found ${bankDirs.length} bank directories.`);

  // 4. Process Accounts (Pass 1)
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
        
        if (!accountId) continue;

        let actualAccount = accountsCache.find((a: any) => a.name === accountName || a.name === accountId);

        if (!actualAccount) {
             console.log(`    Account "${accountName}" (${accountId}) not found. Creating...`);
             if (!argv['dry-run']) {
                 try {
                    const newId = await api.createAccount({ 
                        name: accountName, 
                        offbudget: true 
                    });
                    actualAccount = { id: newId, name: accountName };
                    accountsCache.push(actualAccount);
                 } catch (e: any) {
                     console.error(`    Error creating account: ${e.message}`);
                 }
                 newlyCreatedBankLinkIds.add(accountId);
             } else {
                 console.log(`    [Dry Run] Would create account "${accountName}"`);
                 actualAccount = { id: 'dry-run-id', name: accountName };
                 accountsCache.push(actualAccount);
                 newlyCreatedBankLinkIds.add(accountId);
             }
        }
      }
    }
  }
  
  if (!argv['dry-run']) {
    await api.sync();
    accountsCache = await api.getAccounts();
  }

  // 5. Process Transactions (Pass 2)
  console.log('\n--- Phase 2: Transaction Import ---');
  // Refresh cache one last time to be sure
  if (!argv['dry-run']) {
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
            let actualAccount = accountsCache.find((a: any) => a.name === nameToUse || a.name === accountId);

            if (!actualAccount) {
                console.log(`    Account "${nameToUse}" (${accountId}) not found (not in accounts.csv). Creating on the fly...`);
                 if (!argv['dry-run']) {
                     try {
                        const newId = await api.createAccount({ 
                            name: nameToUse, 
                            offbudget: true 
                        });
                        actualAccount = { id: newId, name: nameToUse };
                        accountsCache.push(actualAccount);
                     } catch (e: any) {
                         console.error(`    Error creating account: ${e.message}`);
                         continue; // Skip transactions if account creation failed
                     }
                     newlyCreatedBankLinkIds.add(accountId);
                 } else {
                     console.log(`    [Dry Run] Would create account "${nameToUse}"`);
                     actualAccount = { id: 'dry-run-id', name: nameToUse };
                     accountsCache.push(actualAccount);
                     newlyCreatedBankLinkIds.add(accountId);
                 }
            }

            // Prepare Transactions
            const actualTransactions: ActualTransaction[] = txs.map(tx => {
                // Amount: CSV is usually float, Actual needs integer cents
                const amount = Math.round(parseFloat(tx['Amount']) * 100);
                
                // Date: Actual needs YYYY-MM-DD
                const dateStr = tx['Date'].substring(0, 10);

                // Payee: Use 'Payee' or 'Payee Name' if available, else 'Description'
                const payee = tx['Payee Name'] || tx['Payee'] || tx['Description'];

                // Notes: Use 'Notes' if available
                let notes = tx['Notes'] || tx['Description'] || '';
                
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
                    account: actualAccount.id
                };
            });

            console.log(`    Importing ${actualTransactions.length} transactions for account "${actualAccount.name}"...`);
            if (!argv['dry-run']) {
                try {
                    const result = await api.importTransactions(actualAccount.id, actualTransactions);
                    console.log(`      Added: ${result.added.length}, Updated: ${result.updated.length}, Errors: ${result.errors ? result.errors.length : 0}`);
                } catch (e: any) {
                    console.error(`      Error importing transactions: ${e.message}`);
                }
            } else {
                console.log(`      [Dry Run] Would import ${actualTransactions.length} transactions.`);
            }
        }
        if (!argv['dry-run']) {
            console.log(`    Syncing transactions for ${path.basename(file)}...`);
            await api.sync();
        }
    }
  }
    
  // 6. Phase 3: Reconciliation (Global)
  if (!argv['dry-run']) {
      console.log('\n--- Phase 3: Reconciliation ---');
      // Refresh cache to get latest balances/accounts
      accountsCache = await api.getAccounts();

      for (const [uniqueAccountId, info] of accountBalances) {
          const { balance: expectedBalance, name: accountName } = info;

          // Find the account in Actual
          const actualAccount = accountsCache.find((a: any) => a.name === accountName || a.name === uniqueAccountId);

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

                       if (!argv['dry-run']) {
                          try {
                              await api.importTransactions(actualAccount.id, [reconciliationTx]);
                              console.log(`      SUCCESS: Created initial reconciliation transaction for ${diff / 100}`);
                          } catch (e: any) {
                              console.error(`      ERROR: Failed to create reconciliation transaction: ${e.message}`);
                          }
                       } else {
                           console.log(`      [Dry Run] Would create transaction: ${JSON.stringify(reconciliationTx)}`);
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
  if (!argv['dry-run']) {
      console.log('Syncing with server...');
      await api.sync();
  }

  console.log('Sync complete.');
  await api.shutdown();
}

main().catch(err => {
  console.error(err);
  process.exit(1);
});
