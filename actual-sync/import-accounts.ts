/**
 * Import Accounts Script
 *
 * This script scans the transactions directory for `accounts.csv` files and ensures
 * that corresponding accounts exist in Actual Budget. It also maintains an
 * `account-map.json` file to map CSV Account IDs to Actual Budget Account IDs.
 *
 * Usage:
 *   npx ts-node import-accounts.ts [--config <path>] [--dry-run]
 */
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

// Map CSV Account ID -> Actual Account UUID
let accountMap: Record<string, string> = {};
const ACCOUNT_MAP_FILE = path.join(__dirname, 'account-map.json');

function loadAccountMap() {
    if (fs.existsSync(ACCOUNT_MAP_FILE)) {
        try {
            accountMap = JSON.parse(fs.readFileSync(ACCOUNT_MAP_FILE, 'utf8'));
            console.log(`Loaded ${Object.keys(accountMap).length} account mappings.`);
        } catch (e) {
            console.error('Error loading account map:', e);
        }
    } else {
        console.log('No existing account map found, starting fresh.');
    }
}

function saveAccountMap() {
    try {
        fs.writeFileSync(ACCOUNT_MAP_FILE, JSON.stringify(accountMap, null, 2), 'utf8');
        console.log('Saved account map.');
    } catch (e) {
        console.error('Error saving account map:', e);
    }
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
    console.log('Starting Accounts Import...');
    loadAccountMap();

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
        process.exit(1);
    }

    // 2. Connect to Actual
    console.log(`Connecting to Actual Budget at ${config.actual.server_url}...`);
    const dataDir = path.resolve(__dirname, 'data');
    if (!fs.existsSync(dataDir)) {
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

    // 3. Determine Transactions Dir
    const absConfigPath = path.resolve(argv.config);
    const configDir = path.dirname(absConfigPath);
    let transactionsDir = config.output_dir ? path.resolve(configDir, config.output_dir) : path.resolve(__dirname, '../../transactions');
    if (argv['transactions-dir']) {
        transactionsDir = path.resolve(argv['transactions-dir']);
    }

    if (!fs.existsSync(transactionsDir)) {
        console.error(`Transactions directory not found: ${transactionsDir}`);
        // Check fallback
        const fallbackDir = path.resolve(__dirname, '../../transactions');
        if (fs.existsSync(fallbackDir)) {
            console.log(`Fallback found: ${fallbackDir}`);
            transactionsDir = fallbackDir;
        } else {
            process.exit(1);
        }
    }

    console.log(`Scanning for bank directories in: ${transactionsDir}`);
    const bankDirs = fs.readdirSync(transactionsDir)
        .map(item => path.join(transactionsDir, item))
        .filter(fullPath => fs.statSync(fullPath).isDirectory());

    console.log(`Found ${bankDirs.length} bank directories.`);

    // 4. Process Accounts
    console.log('\n--- Account Processing ---');
    let accountsCache = await api.getAccounts();
    const newlyCreatedBankLinkIds = new Set<string>();

    for (const bankDir of bankDirs) {
        const bankName = path.basename(bankDir);
        const accountsFile = path.join(bankDir, 'accounts.csv');

        if (fs.existsSync(accountsFile)) {
            console.log(`Processing ${bankName}/accounts.csv...`);
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
                const accountType = csvAcc['Type'];

                if (!accountId) continue;

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
                        console.log(`  Mapping Bank ID "${accountId}" to Actual Account "${actualAccount.name}" (${actualAccount.id})`);
                        accountMap[accountId] = actualAccount.id;
                        saveAccountMap();
                    }
                } else {
                    // Create Account
                    console.log(`  Account "${accountName}" (${accountId}) not found. Creating...`);

                    const getBudgetStatus = (type: string): boolean => {
                        if (!type) return true;
                        const lowerType = type.toLowerCase();
                        return !(lowerType.includes('credit card') ||
                            lowerType.includes('checking') ||
                            lowerType.includes('chequing') ||
                            lowerType.includes('cash'));
                    };

                    const isOffBudget = getBudgetStatus(accountType);

                    if (!argv['dry-run']) {
                        try {
                            const newId = await api.createAccount({
                                name: accountName,
                                offbudget: isOffBudget
                            });
                            actualAccount = { id: newId, name: accountName } as any;
                            accountsCache.push(actualAccount as any);

                            accountMap[accountId] = newId;
                            saveAccountMap();
                            console.log(`  -> Created account with ID: ${newId}`);

                        } catch (e: any) {
                            console.error(`  Error creating account: ${e.message}`);
                            continue;
                        }
                    } else {
                        console.log(`  [Dry Run] Would create account "${accountName}" (Type: ${accountType}, OffBudget: ${isOffBudget})`);
                    }
                }
            }
        }
    }

    console.log('Done.');
    await api.shutdown();
}

main().catch(err => {
    console.error(err);
    process.exit(1);
});
