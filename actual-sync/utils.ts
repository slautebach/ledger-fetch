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
import { GoogleGenerativeAI } from '@google/generative-ai';
import * as dotenv from 'dotenv';
dotenv.config({ path: path.resolve(__dirname, '../.env') });

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
    /** AI Configuration */
    ai: {
        path: string;
        model: string;
        api_key?: string;
    };
    /** Ledger Fetch Configuration */
    ledger_fetch: {
        transactions_path: string;
        payee_rules_path?: string;
    };
    /** Browser Configuration */
    browser?: {
        headless: boolean;
        timeout: number;
        profile_path: string;
    };
}

export interface Account {
    id: string; // The Bank CSV ID
    actual_id: string; // The Actual Budget UUID
    name: string;
    off_budget: boolean;
    closed?: boolean;
}

/**
 * Loads and parses the configuration file from the specified path.
 * 
 * This function handles the new nested configuration structure.
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

        // Basic validation/typing
        const config = loaded as Config;

        // Logic to resolve transactions_path relative to config file if it exists
        if (config.ledger_fetch && config.ledger_fetch.transactions_path) {
            const rawPath = config.ledger_fetch.transactions_path;
            const configDir = path.dirname(resolvedPath);
            config.ledger_fetch.transactions_path = path.resolve(configDir, rawPath);
        }

        // Override with Environment Variables
        if (process.env.ACTUAL_SERVER_URL) {
            config.actual = config.actual || {} as any;
            config.actual.server_url = process.env.ACTUAL_SERVER_URL;
        }
        if (process.env.ACTUAL_PASSWORD) {
            config.actual = config.actual || {} as any;
            config.actual.password = process.env.ACTUAL_PASSWORD;
        }
        if (process.env.ACTUAL_SYNC_ID) {
            config.actual = config.actual || {} as any;
            config.actual.sync_id = process.env.ACTUAL_SYNC_ID;
        }
        if (process.env.GEMINI_API_KEY) {
            config.ai = config.ai || {} as any;
            config.ai.api_key = process.env.GEMINI_API_KEY;
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

/**
 * Calls the Google Gemini API with the given prompt and context data.
 * 
 * @param basePrompt The base prompt instructions.
 * @param jsonData The data context (usually JSON string) to append to the prompt.
 * @param config The configuration object containing the API key and model.
 * @returns The text response from Gemini.
 */
export async function callGemini(basePrompt: string, jsonData: string, config: Config): Promise<string> {
    if (!config.ai || !config.ai.api_key) {
        throw new Error("Gemini API Key is missing in the configuration.");
    }

    const genAI = new GoogleGenerativeAI(config.ai.api_key);
    const model = genAI.getGenerativeModel({ model: config.ai.model || "gemini-pro" });

    const fullPrompt = `${basePrompt}\n\nContext Data:\n${jsonData}`;

    try {
        const result = await model.generateContent(fullPrompt);
        const response = await result.response;
        return response.text();
    } catch (error: any) {
        throw new Error(`Gemini API Error: ${error.message}`);
    }
}

/**
 * Loads the accounts configuration from accounts.yaml.
 * 
 * @param configDir - The directory containing the config files.
 * @returns A list of Account objects.
 */
export function loadAccounts(configDir: string): Account[] {
    const accountsPath = path.join(configDir, 'accounts.yaml');
    if (!fs.existsSync(accountsPath)) {
        return [];
    }
    try {
        const fileContents = fs.readFileSync(accountsPath, 'utf8');
        const loaded: any = yaml.load(fileContents);
        return loaded?.accounts || [];
    } catch (e: any) {
        throw new Error(`Error parsing accounts file: ${e.message}`);
    }
}

/**
 * Appends a new account to the accounts.yaml file.
 * 
 * @param configDir - The directory containing the config files.
 * @param account - The account object to append.
 */
export function appendAccount(configDir: string, account: Account) {
    const accountsPath = path.join(configDir, 'accounts.yaml');
    let accounts: Account[] = [];

    if (fs.existsSync(accountsPath)) {
        try {
            const fileContents = fs.readFileSync(accountsPath, 'utf8');
            const loaded: any = yaml.load(fileContents);
            accounts = loaded?.accounts || [];
        } catch (e: any) {
            console.error(`Error reading accounts.yaml for append: ${e.message}`);
        }
    }

    accounts.push(account);

    // Sort by Name
    accounts.sort((a, b) => a.name.localeCompare(b.name));

    try {
        const yamlStr = yaml.dump({ accounts: accounts });
        fs.writeFileSync(accountsPath, yamlStr, 'utf8');
        console.log(`Saved new account "${account.name}" to accounts.yaml`);
    } catch (e: any) {
        throw new Error(`Error writing to accounts.yaml: ${e.message}`);
    }
}

/**
 * Synchronizes local accounts.yaml with Actual Budget.
 * 
 * 1. Fetches remote accounts.
 * 2. Updates local accounts with remote data (name, off_budget, closed).
 * 3. Adds missing remote accounts to local list (using Name as default CSV ID).
 * 4. Sorts: Open accounts first, then by Name.
 * 5. Saves back to accounts.yaml.
 * 
 * @param configDir Directory containing accounts.yaml
 * @param remoteAccounts List of accounts from Actual Budget API
 */
export function syncAccountsWithActual(configDir: string, remoteAccounts: any[]) {
    const accountsPath = path.join(configDir, 'accounts.yaml');
    let localAccounts: Account[] = [];

    if (fs.existsSync(accountsPath)) {
        try {
            const fileContents = fs.readFileSync(accountsPath, 'utf8');
            const loaded: any = yaml.load(fileContents);
            localAccounts = loaded?.accounts || [];
        } catch (e: any) {
            console.error(`Error reading accounts.yaml for sync: ${e.message}`);
        }
    }

    let updates = 0;
    const localMap = new Map<string, Account[]>();
    localAccounts.forEach(a => {
        const existing = localMap.get(a.actual_id) || [];
        existing.push(a);
        localMap.set(a.actual_id, existing);
    });

    // 1. Process Remote Accounts (Update Existing + Add Missing)
    for (const remote of remoteAccounts) {
        const locals = localMap.get(remote.id);

        if (locals) {
            // Update all existing local accounts matching this Actual ID
            for (const local of locals) {
                let changed = false;

                // Name
                if (local.name !== remote.name) {
                    local.name = remote.name;
                    changed = true;
                }

                // Off Budget
                if (local.off_budget !== !!remote.offbudget) {
                    local.off_budget = !!remote.offbudget;
                    changed = true;
                }

                // Closed - Ensure property is always set (even if false)
                const remoteClosed = !!remote.closed;
                if (local.closed !== remoteClosed) {
                    local.closed = remoteClosed;
                    changed = true;
                }

                if (changed) updates++;
            }

        } else {
            // Add missing (New Account found in Actual)
            // We need a CSV ID. Since we don't know it, we default to the Account Name.
            // Only add if it doesn't conflict with an existing CSV ID? 
            // Actually, we just add it to the list.
            const newAccount: Account = {
                id: remote.name, // Default ID to Name
                actual_id: remote.id,
                name: remote.name,
                off_budget: !!remote.offbudget,
                closed: !!remote.closed
            };
            localAccounts.push(newAccount);
            // Update map just in case (though not strictly needed for this loop)
            const existing = localMap.get(remote.id) || [];
            existing.push(newAccount);
            localMap.set(remote.id, existing);

            console.log(`Added new account from server: ${newAccount.name}`);
            updates++;
        }
    }

    // 1b. Ensure ALL local accounts have a 'closed' property (default to false if missing)
    for (const local of localAccounts) {
        if (local.closed === undefined) {
            local.closed = false;
            updates++;
        }
    }

    // 2. Sort and Save
    if (updates > 0) {
        saveAccounts(configDir, localAccounts);
        console.log(`Synced accounts.yaml: ${updates} accounts updated/added.`);
    } else {
        // We still save to ensure sort order is enforced if it wasn't
        saveAccounts(configDir, localAccounts);
        console.log(`Synced accounts.yaml: No changes.`);
    }
}

/**
 * Sorts (Open > Name) and saves the list of accounts to accounts.yaml.
 * 
 * @param configDir Directory containing accounts.yaml
 * @param accounts List of Account objects
 */
export function saveAccounts(configDir: string, accounts: Account[]) {
    const accountsPath = path.join(configDir, 'accounts.yaml');

    // Sort: Open First, then Name
    accounts.sort((a, b) => {
        const aClosed = a.closed || false;
        const bClosed = b.closed || false;

        // If one is closed and other is open, Open comes first (false < true)
        if (aClosed !== bClosed) {
            return aClosed ? 1 : -1;
        }

        // If same status, sort by name
        return a.name.localeCompare(b.name);
    });

    try {
        const yamlStr = yaml.dump({ accounts: accounts });
        fs.writeFileSync(accountsPath, yamlStr, 'utf8');
    } catch (e: any) {
        throw new Error(`Error writing to accounts.yaml: ${e.message}`);
    }
}
