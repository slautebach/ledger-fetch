/**
 * Shared Utilities for Actual Sync Scripts
 * 
 * This module provides common functionality for connecting to the Actual Budget API,
 * loading configuration, and managing transaction links.
 */
import * as api from '@actual-app/api';
import * as fs from 'fs';
import * as path from 'path';
import * as yaml from 'js-yaml';

/**
 * Configuration interface for Actual Budget connection and file paths.
 */
export interface Config {
    /** Actual Budget API configuration */
    actual: {
        /** The URL of the Actual Budget server (e.g., http://localhost:5006) */
        server_url: string;
        /** The password for the Actual Budget server */
        password: string;
        /** The Sync ID (Budget ID) of the file to access */
        sync_id: string;
    };
    /** Optional: Path to the directory where output files are stored */
    output_dir?: string;
    /** Optional: Specific path to the transactions directory */
    transactions_path?: string;
}

/**
 * Loads and parses the configuration file from the specified path.
 * 
 * This function handles both nested (namespaced under 'actual') and flat configuration structures.
 * It also resolves the `transactions_path` relative to the config file location.
 * 
 * @param configPath - The absolute or relative path to the config.yaml file.
 * @returns A validated Config object.
 * @throws Error if the file doesn't exist or cannot be parsed.
 */
export function loadConfig(configPath: string): Config {
    const resolvedPath = path.resolve(configPath);
    if (!fs.existsSync(resolvedPath)) {
        throw new Error(`Config file not found at ${resolvedPath}`);
    }
    try {
        const fileContents = fs.readFileSync(resolvedPath, 'utf8');
        const loaded: any = yaml.load(fileContents);

        let config: Config;

        // Normalize config: If flat properties exist, wrap them in an 'actual' object
        // This supports legacy config formats
        if (loaded.server_url && loaded.password && loaded.sync_id) {
            config = {
                actual: {
                    server_url: loaded.server_url,
                    password: loaded.password,
                    sync_id: loaded.sync_id
                },
                output_dir: loaded.output_dir,
                transactions_path: loaded.transactions_path
            };
        } else {
            config = loaded as Config;
        }

        // Logic to resolve transactions_path (or output_dir fallback) relative to config file
        const rawPath = config.transactions_path || config.output_dir;
        if (rawPath) {
            const configDir = path.dirname(resolvedPath);
            config.transactions_path = path.resolve(configDir, rawPath);
        }

        return config;
    } catch (e: any) {
        throw new Error(`Error parsing config file: ${e.message}`);
    }
}

/**
 * Initializes the connection to the Actual Budget API.
 * 
 * This function sets up the data directory, connects to the server, 
 * and downloads the specified budget.
 * 
 * @param config - The configuration object containing server details.
 * @throws Error if the configuration is missing required 'actual' fields.
 */
export async function initActual(config: Config) {
    if (!config.actual || !config.actual.server_url || !config.actual.password || !config.actual.sync_id) {
        throw new Error('Missing "actual" configuration in config.yaml');
    }

    console.log(`Connecting to Actual Budget at ${config.actual.server_url.trim()}...`);
    const dataDir = path.resolve(__dirname, 'data');
    if (!fs.existsSync(dataDir)) {
        fs.mkdirSync(dataDir, { recursive: true });
    }

    await api.init({
        dataDir: dataDir,
        serverURL: config.actual.server_url.trim(),
        password: config.actual.password.trim(),
    });
    await api.downloadBudget(config.actual.sync_id.trim());
    console.log('Connected to budget.');
}

/**
 * Shuts down the Actual Budget API connection.
 * Should be called when the script finishes or errors out.
 */
export async function shutdownActual() {
    await api.shutdown();
}

/**
 * Link two transactions as a transfer in Actual Budget.
 * 
 * This utility finds the appropriate "Transfer Payee" for each account and updates
 * the transactions to point to each other, effectively creating a transfer.
 * 
 * @param transactionIdA The ID of the first transaction (Source/Destination A)
 * @param transactionIdB The ID of the second transaction (Source/Destination B)
 * @param accountIdA The account ID where transaction A resides
 * @param accountIdB The account ID where transaction B resides
 */
export async function linkTransactionsAsTransfer(transactionIdA: string, transactionIdB: string, accountIdA: string, accountIdB: string) {
    // 1. Fetch all payees to find the special "Transfer Payees" created by Actual
    const payees = await api.getPayees();

    // Find the payee that represents a transfer TO Account B
    // In Actual, a transfer payee has a `transfer_acct` property matching the target account ID
    const transferPayeeForAccountB = payees.find((p: any) => p.transfer_acct === accountIdB);

    // Find the payee that represents a transfer TO Account A
    const transferPayeeForAccountA = payees.find((p: any) => p.transfer_acct === accountIdA);

    if (!transferPayeeForAccountB || !transferPayeeForAccountA) {
        throw new Error(`Could not find transfer payees for one or both accounts (A: ${accountIdA}, B: ${accountIdB}).`);
    }

    console.log(`Linking ${transactionIdA} (Acct: ${accountIdA}) <-> ${transactionIdB} (Acct: ${accountIdB})`);

    // 2. Update Transaction A
    // Point it to Transaction B (via transfer_id) and set the payee to "Transfer: Account B"
    await api.updateTransaction(transactionIdA, {
        transfer_id: transactionIdB,
        payee: transferPayeeForAccountB.id
    });

    // 3. Update Transaction B
    // Point it to Transaction A (via transfer_id) and set the payee to "Transfer: Account A"
    await api.updateTransaction(transactionIdB, {
        transfer_id: transactionIdA,
        payee: transferPayeeForAccountA.id
    });

    console.log("Transactions linked successfully!");
}
