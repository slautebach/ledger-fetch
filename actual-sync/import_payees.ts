/**
 * Import Payees Script
 *
 * Purpose:
 * This script imports a list of payees from a CSV file (`payee_counts.csv`) into Actual Budget.
 * It is designed to be run before importing transactions or rules to ensure that all necessary
 * payees already exist in the system, preventing issues with unlinked transactions.
 *
 * Logic:
 * 1. Checks existing payees in Actual Budget to avoid creation of duplicates.
 * 2. Reads `payee_counts.csv` to identify potential new payees.
 * 3. Creates any payees that do not currently exist.
 */
import * as api from '@actual-app/api';
import * as fs from 'fs';
import * as path from 'path';
import csv from 'csv-parser'; // Import default export from csv-parser
import { Config, loadConfig, initActual, shutdownActual } from './utils';

// --- Configuration ---
// CONFIG_PATH removed


// Config interface removed

interface PayeeRow {
    Payee: string;
    Count: string;
}

// --- Main ---
async function main() {
    console.log('--- Starting Payee Import ---');

    // Usage: ts-node import_payees.ts [--config-dir <path>]
    const args = process.argv.slice(2);
    let configDir = './config';
    const configDirIndex = args.indexOf('--config-dir');
    if (configDirIndex !== -1 && args.length > configDirIndex + 1) {
        configDir = args[configDirIndex + 1];
    }
    const resolvedConfigDir = path.resolve(configDir);
    const configPath = path.join(resolvedConfigDir, 'config.yaml');

    // 1. Load Config
    let config: Config;
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

    // 3. Determine Transactions Dir
    const transactionsDir = config.transactions_path || path.resolve(__dirname, '../../transactions');
    const payeesCsvPath = path.join(transactionsDir, 'payee_counts.csv');

    // 4. Fetch Existing Payees
    console.log('Fetching existing payees...');
    const existingPayees = await api.getPayees();
    // Use a Set for faster lookup. Actual names are case sensitive usually, but let's check exact match first.
    // The user said "If the payee exists it should skip it", implying name match.
    const existingPayeeNames = new Set<string>(existingPayees.map(p => p.name));
    console.log(`Found ${existingPayees.length} existing payees.`);

    // 5. Read CSV and Import
    // We stream the CSV file to handle large datasets efficiently.
    console.log(`Reading CSV from: ${payeesCsvPath}`);
    if (!fs.existsSync(payeesCsvPath)) {
        console.error(`CSV file not found: ${payeesCsvPath}`);
        await api.shutdown();
        process.exit(1);
    }

    const payeesToImport: string[] = [];

    // Promisify the CSV reading
    await new Promise<void>((resolve, reject) => {
        fs.createReadStream(payeesCsvPath)
            .pipe(csv())
            .on('data', (row: PayeeRow) => {
                if (row.Payee) {
                    const payeeName = row.Payee.trim();
                    if (payeeName && !existingPayeeNames.has(payeeName)) {
                        // Avoid duplicates within the CSV itself
                        if (!payeesToImport.includes(payeeName)) {
                            payeesToImport.push(payeeName);
                        }
                    }
                }
            })
            .on('end', () => {
                resolve();
            })
            .on('error', (err) => {
                reject(err);
            });
    });

    console.log(`Found ${payeesToImport.length} new payees to import.`);

    // 6. Create Payees
    // We iterate through the unique list of new payees and create them one by one.
    for (let i = 0; i < payeesToImport.length; i++) {
        const payeeName = payeesToImport[i];
        try {
            console.log(`[${i + 1}/${payeesToImport.length}] Creating Check: '${payeeName}'`);

            // Double check existence just in case, though we checked the initial set
            // The API createPayee might throw or return duplicate if it exists, but we are being safe
            await api.createPayee({ name: payeeName });
            // console.log(`  -> Created.`);
        } catch (e: any) {
            console.error(`  Failed to create payee '${payeeName}': ${e.message}`);
        }
    }

    console.log('Syncing...');
    await api.sync();
    await shutdownActual();
    console.log('Done.');
}

main().catch(err => {
    console.error(err);
    process.exit(1);
});
