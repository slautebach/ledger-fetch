/**
 * Import Accounts Script
 *
 * Purpose:
 * This script bootstraps the account setup in Actual Budget by scanning bank directories
 * for `accounts.csv` files.
 *
 * Workflow:
 * 1. Scans the transactions directory (configured via `config.yaml` or arg) for bank subdirectories.
 * 2. Reads `accounts.csv` from each bank directory.
 * 3. Checks if the account already exists in Actual Budget:
 *    - First by checking a persistent `account-map.json` (CSV ID -> Actual ID).
 *    - Second by checking for a name match.
 * 4. Creates missing accounts.
 * 5. Updates `account-map.json` with any new linkages.
 *
 * Usage:
 *   npx ts-node import-accounts.ts [--config-dir <path>] [--transactions-dir <path>]
 */
import * as api from '@actual-app/api';
import * as fs from 'fs';
import * as path from 'path';
import csv from 'csv-parser';
import yargs from 'yargs/yargs';
import { hideBin } from 'yargs/helpers';
import { Config, loadConfig, initActual, shutdownActual } from './utils';

// Define interfaces
// Config interface removed (imported from utils)

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
let ACCOUNT_MAP_FILE = path.join(__dirname, 'config', 'account-map.json'); // Default, will overwrite in main based on config-dir

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
    .parseSync();

async function main() {
    console.log('Starting Accounts Import...');
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

    // 2. Connect to Actual
    try {
        await initActual(config);
    } catch (e: any) {
        console.error(e.message);
        process.exit(1);
    }

    // Update ACCOUNT_MAP_FILE based on config dir
    ACCOUNT_MAP_FILE = path.join(argv['config-dir'], 'account-map.json');
    loadAccountMap();

    // 3. Determine Transactions Dir
    // 3. Determine Transactions Dir
    let transactionsDir = config.transactions_path || path.resolve(__dirname, '../../transactions');
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
                const accountName = csvAcc['Account Name'].trim();
                const accountType = csvAcc['Type'].trim();

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
                }
            }
        }

        console.log('Syncing with server...');
        await api.sync();

    }

    console.log('Done.');
    await shutdownActual();
}

main().catch(err => {
    console.error(err);
    process.exit(1);
});
