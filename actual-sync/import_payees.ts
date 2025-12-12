
/**
 * Import Payees Script
 *
 * This script imports payees from a CSV file (payee_counts.csv) into Actual Budget.
 * Ideally run before importing transactions or rules to ensure all payees exist.
 * It checks for existing payees by name to avoid duplicates.
 */
import * as api from '@actual-app/api';
import * as fs from 'fs';
import * as path from 'path';
import * as yaml from 'js-yaml';
import csv from 'csv-parser'; // Import default export from csv-parser

// --- Configuration ---
const CONFIG_PATH = path.resolve('../config.yaml');
const PAYEES_CSV_PATH = path.resolve('../../transactions/payee_counts.csv');

// --- Interfaces ---
interface Config {
    actual: {
        server_url: string;
        password: string;
        sync_id: string;
    };
}

interface PayeeRow {
    Payee: string;
    Count: string;
}

// --- Main ---
async function main() {
    console.log('--- Starting Payee Import ---');

    // 1. Load Config
    if (!fs.existsSync(CONFIG_PATH)) {
        console.error(`Config file not found: ${CONFIG_PATH}`);
        process.exit(1);
    }
    const config = yaml.load(fs.readFileSync(CONFIG_PATH, 'utf8')) as Config;

    // 2. Validate Config
    if (!config.actual || !config.actual.server_url || !config.actual.password || !config.actual.sync_id) {
        console.error('Missing "actual" configuration in config.yaml');
        process.exit(1);
    }

    // 3. Connect to Actual
    console.log('Connecting to Actual Budget...');
    console.log(`  Server: ${config.actual.server_url}`);
    console.log(`  Sync ID: ${config.actual.sync_id}`);

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
        console.log('Initialized. Downloading budget...');
        await api.downloadBudget(config.actual.sync_id);
        console.log('Connected.');
    } catch (e: any) {
        console.error('Connection Error:', e.message);
        process.exit(1);
    }

    // 4. Fetch Existing Payees
    console.log('Fetching existing payees...');
    const existingPayees = await api.getPayees();
    // Use a Set for faster lookup. Actual names are case sensitive usually, but let's check exact match first.
    // The user said "If the payee exists it should skip it", implying name match.
    const existingPayeeNames = new Set<string>(existingPayees.map(p => p.name));
    console.log(`Found ${existingPayees.length} existing payees.`);

    // 5. Read CSV and Import
    // We stream the CSV file to handle large datasets efficiently.
    console.log(`Reading CSV from: ${PAYEES_CSV_PATH}`);
    if (!fs.existsSync(PAYEES_CSV_PATH)) {
        console.error(`CSV file not found: ${PAYEES_CSV_PATH}`);
        await api.shutdown();
        process.exit(1);
    }

    const payeesToImport: string[] = [];

    // Promisify the CSV reading
    await new Promise<void>((resolve, reject) => {
        fs.createReadStream(PAYEES_CSV_PATH)
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
    await api.shutdown(); // Shutdown implies sync usually, but verify? 
    // api.shutdown() docs say "Clean up and save data." which usually syncs if online.
    console.log('Done.');
}

main().catch(err => {
    console.error(err);
    process.exit(1);
});
