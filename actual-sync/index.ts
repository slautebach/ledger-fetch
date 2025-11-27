import * as api from '@actual-app/api';
import * as fs from 'fs';
import * as path from 'path';
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
  const transactionsDir = config.output_dir ? path.resolve(path.dirname(argv.config), config.output_dir) : path.resolve(__dirname, '../transactions');
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

  // 4. Process Each Bank
  let accountsCache = await api.getAccounts();

  for (const bankDir of bankDirs) {
    const bankName = path.basename(bankDir);
    console.log(`\nProcessing bank: ${bankName}`);

    // 4a. Process Accounts
    const accountsFile = path.join(bankDir, 'accounts.csv');
    if (fs.existsSync(accountsFile)) {
      console.log(`  Found accounts.csv. Syncing accounts...`);
      const csvAccounts: CsvAccount[] = [];
      
      await new Promise<void>((resolve, reject) => {
        fs.createReadStream(accountsFile)
          .pipe(csv())
          .on('data', (data: any) => csvAccounts.push(data))
          .on('end', () => resolve())
          .on('error', (err: any) => reject(err));
      });

      for (const csvAcc of csvAccounts) {
        const accountId = csvAcc['Unique Account ID'];
        const accountName = csvAcc['Account Name'];
        
        if (!accountId) continue;

        // Check if account exists in Actual (matching by Name for now, as we don't store external ID easily yet)
        // Ideally we should store the external ID in notes or a custom field if possible, but Name is the standard fallback.
        // However, the previous logic used 'Unique Account ID' as the name. 
        // Let's stick to using the 'Unique Account ID' as the name for uniqueness if that's what was intended, 
        // OR use 'Account Name' if we want it human readable. 
        // The user's schema has both. 
        // The previous code used: `const accountId = tx['Unique Account ID']; ... name: accountId`
        // So it was naming the account with the ID. 
        // Let's switch to using the human readable name if available, but we need to be careful about duplicates.
        // Actually, to maintain compatibility and uniqueness, maybe we should stick to ID or check if the user wants human names.
        // The prompt says "Account Name: str - The human-readable name of the account".
        // Let's try to find an account by name first.
        
        // Strategy: Use 'Unique Account ID' as the source of truth for mapping. 
        // But Actual accounts have their own IDs. 
        // We will look for an account where the name matches 'Account Name' OR 'Unique Account ID'.
        // To be safe and consistent with previous logic, let's look for `Unique Account ID` as the name first? 
        // No, `Account Name` is better for the user. 
        // Let's use `Account Name`.
        
        let actualAccount = accountsCache.find((a: any) => a.name === accountName || a.name === accountId);

        if (!actualAccount) {
             console.log(`    Account "${accountName}" (${accountId}) not found. Creating...`);
             if (!argv['dry-run']) {
                 try {
                    const newId = await api.createAccount({ 
                        name: accountName, 
                        type: 'other', 
                        offbudget: true 
                    });
                    actualAccount = { id: newId, name: accountName };
                    accountsCache.push(actualAccount);
                 } catch (e: any) {
                     console.error(`    Error creating account: ${e.message}`);
                 }
             } else {
                 console.log(`    [Dry Run] Would create account "${accountName}"`);
                 actualAccount = { id: 'dry-run-id', name: accountName };
                 accountsCache.push(actualAccount);
             }
        } else {
            // console.log(`    Account "${accountName}" already exists.`);
        }
      }
      // Refresh cache after processing accounts.csv
      if (!argv['dry-run']) {
          accountsCache = await api.getAccounts();
      }
      if (!argv['dry-run']) {
        console.log('    Syncing accounts...');
        await api.sync();
      }
    } else {
      console.log(`  No accounts.csv found. Accounts will be created from transaction data if they don't exist.`);
    }

    // 4b. Process Transactions
    const transactionFiles = fs.readdirSync(bankDir)
        .filter(f => f.endsWith('.csv') && f !== 'accounts.csv')
        .map(f => path.join(bankDir, f));

    for (const file of transactionFiles) {
        console.log(`  Processing transactions file: ${path.basename(file)}...`);
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
            const accountId = tx['Unique Account ID']; // This maps to the account
            if (!accountId) continue;
            if (!transactionsByAccount[accountId]) {
                transactionsByAccount[accountId] = [];
            }
            transactionsByAccount[accountId].push(tx);
        }

        for (const [accountId, txs] of Object.entries(transactionsByAccount)) {
            // Find the account in Actual
            // We attempt to find the account by Name (from transaction) or ID.
            // If accounts.csv was present, the account might have been created/synced there.
            // If not, we fall back to creating it here.
            
            const txAccountName = txs[0]['Account Name'];
            
            let actualAccount = accountsCache.find((a: any) => a.name === txAccountName || a.name === accountId);

            if (!actualAccount) {
                console.log(`    Account "${txAccountName || accountId}" not found in Actual. Creating...`);
                 if (!argv['dry-run']) {
                    const nameToUse = txAccountName || accountId;
                    const newId = await api.createAccount({ name: nameToUse, type: 'other', offbudget: true });
                    actualAccount = { id: newId, name: nameToUse };
                    accountsCache.push(actualAccount);
                } else {
                    console.log(`    [Dry Run] Would create account "${txAccountName || accountId}"`);
                    actualAccount = { id: 'dry-run-id', name: txAccountName || accountId };
                }
            }

            // Prepare Transactions
            const actualTransactions: ActualTransaction[] = txs.map(tx => {
                // Amount: CSV is usually float, Actual needs integer cents
                const amount = Math.round(parseFloat(tx['Amount']) * 100);
                
                // Date: Actual needs YYYY-MM-DD
                const dateStr = tx['Date'].substring(0, 10);

                // Payee: Use 'Payee' or 'Payee Name' if available, else 'Description'
                const payee = tx['Payee'] || tx['Payee Name'] || tx['Description'];

                // Notes: Use 'Notes' if available
                let notes = tx['Notes'] || '';
                
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
