import * as api from '@actual-app/api';
import * as fs from 'fs';
import * as path from 'path';
import * as yaml from 'js-yaml';
import yargs from 'yargs/yargs';
import { hideBin } from 'yargs/helpers';
import { Config, loadConfig, initActual, shutdownActual } from './utils';
import { exit } from 'process';

// Defines the structure of the YAML file
interface AccountDetail {
    id: string; // The Actual Budget Account UUID
    name: string; // The display name
    off_budget: boolean;
}

// Map: CSV_Account_ID -> AccountDetail
type AccountDetails = Record<string, AccountDetail>;

// Legacy types for conversion
type AccountMap = Record<string, string>; // CSV_ID -> Actual_ID
interface AccountDetailOld {
    off_budget?: boolean;
    display_name?: string;
}
type AccountDetailsOld = Record<string, AccountDetailOld>;

const CONFIG_DIR = path.resolve(__dirname, 'config');
const ACCOUNT_DETAILS_YAML = path.join(CONFIG_DIR, 'account-details.yaml');
const ACCOUNT_DETAILS_JSON = path.join(CONFIG_DIR, 'account-details.json');
const ACCOUNT_MAP_JSON = path.join(CONFIG_DIR, 'account-map.json');

// --- Helper Functions ---

function loadYamlDetails(): AccountDetails {
    if (!fs.existsSync(ACCOUNT_DETAILS_YAML)) {
        return {};
    }
    return yaml.load(fs.readFileSync(ACCOUNT_DETAILS_YAML, 'utf8')) as AccountDetails;
}

function saveYamlDetails(details: AccountDetails) {
    fs.writeFileSync(ACCOUNT_DETAILS_YAML, yaml.dump(details), 'utf8');
    console.log(`Saved ${Object.keys(details).length} accounts to ${ACCOUNT_DETAILS_YAML}`);
}

// --- Commands ---

async function convert() {
    console.log('Converting JSON configuration to YAML...');

    if (!fs.existsSync(ACCOUNT_DETAILS_JSON) || !fs.existsSync(ACCOUNT_MAP_JSON)) {
        console.error('Error: account-details.json or account-map.json not found.');
        return;
    }

    const detailsOld: AccountDetailsOld = JSON.parse(fs.readFileSync(ACCOUNT_DETAILS_JSON, 'utf8'));
    const map: AccountMap = JSON.parse(fs.readFileSync(ACCOUNT_MAP_JSON, 'utf8'));

    const newDetails: AccountDetails = {};
    const missingMaps: string[] = [];

    // Iterate through all accounts known in the map (which links CSV ID to Actual ID)
    for (const [csvId, actualId] of Object.entries(map)) {
        const oldDetail = detailsOld[csvId] || {};

        newDetails[csvId] = {
            id: actualId,
            name: oldDetail.display_name || csvId, // Fallback to ID if no name
            off_budget: oldDetail.off_budget || false
        };
    }

    // Also check for items in detailsOld that might not be in map 
    // (though logically they should be linked, but let's warn)
    for (const csvId of Object.keys(detailsOld)) {
        if (!map[csvId]) {
            missingMaps.push(csvId);
        }
    }

    if (missingMaps.length > 0) {
        console.warn('Warning: The following accounts in details.json were not found in map.json and skipped:', missingMaps);
    }

    saveYamlDetails(newDetails);
    console.log('Conversion complete. Please review account-details.yaml.');
}

async function pull(config: Config) {
    console.log('Pulling account details from Actual Budget...');

    // 1. Connect
    await initActual(config);

    // 2. Load Local Config
    const localDetails = loadYamlDetails();
    if (Object.keys(localDetails).length === 0) {
        console.error('No local account details found. Run --convert first?');
        await shutdownActual();
        return;
    }

    // 3. Get Remote Accounts
    const remoteAccounts = await api.getAccounts();
    let updates = 0;

    // 4. Update Local Config
    for (const [csvId, detail] of Object.entries(localDetails)) {
        const remoteAccount = remoteAccounts.find((a: any) => a.id === detail.id);

        if (remoteAccount) {
            let changed = false;

            if (detail.name !== remoteAccount.name) {
                console.log(`[${csvId}] Name: "${detail.name}" -> "${remoteAccount.name}"`);
                detail.name = remoteAccount.name;
                changed = true;
            }

            if (detail.off_budget !== !!remoteAccount.offbudget) {
                console.log(`[${csvId}] Off-Budget: ${detail.off_budget} -> ${!!remoteAccount.offbudget}`);
                detail.off_budget = !!remoteAccount.offbudget;
                changed = true;
            }

            if (changed) updates++;
        } else {
            console.warn(`Warning: Local account ${csvId} (ID: ${detail.id}) not found on server.`);
        }
    }

    if (updates > 0) {
        saveYamlDetails(localDetails);
        console.log(`Updated ${updates} accounts.`);
    } else {
        console.log('No changes found.');
    }

    await shutdownActual();
}

async function push(config: Config) {
    console.log('Pushing account details to Actual Budget...');

    // 1. Connect
    await initActual(config);

    // 2. Load Local Config
    const localDetails = loadYamlDetails();

    // 3. Get Remote Accounts (to compare)
    const remoteAccounts = await api.getAccounts();
    let updates = 0;

    // 4. Update Remote
    for (const [csvId, detail] of Object.entries(localDetails)) {
        const remoteAccount = remoteAccounts.find((a: any) => a.id === detail.id);

        if (remoteAccount) {
            const needsUpdate = (
                detail.name !== remoteAccount.name ||
                detail.off_budget !== !!remoteAccount.offbudget
            );

            if (needsUpdate) {
                console.log(`Updating ${detail.name}...`);
                try {
                    await api.updateAccount(detail.id, {
                        name: detail.name,
                        offbudget: detail.off_budget
                    });
                    updates++;
                } catch (e: any) {
                    console.error(`Error updating ${detail.name}: ${e.message}`);
                }
            }
        } else {
            console.warn(`Warning: Local account ${csvId} (ID: ${detail.id}) not found on server. Cannot update.`);
        }
    }

    console.log(`Pushed updates for ${updates} accounts.`);
    await shutdownActual();
}

// --- Main ---

const argv = yargs(hideBin(process.argv))
    .option('config-dir', {
        type: 'string',
        default: './config',
        description: 'Path to config directory'
    })
    .option('convert', {
        type: 'boolean',
        description: 'Convert JSON config to YAML'
    })
    .option('pull', {
        type: 'boolean',
        description: 'Pull details from Actual to Local'
    })
    .option('push', {
        type: 'boolean',
        description: 'Push details from Local to Actual'
    })
    .help()
    .parseSync();

async function main() {
    const configPath = path.join(argv['config-dir'], 'config.yaml');
    let config: Config;

    // Load config if we need it (push/pull)
    if (argv.pull || argv.push) {
        try {
            config = loadConfig(configPath);
        } catch (e: any) {
            console.error(e.message);
            exit(1);
        }
    }

    if (argv.convert) {
        await convert();
    } else if (argv.pull) {
        await pull(config!);
    } else if (argv.push) {
        await push(config!);
    } else {
        console.log('Please specify --convert, --pull, or --push');
    }
}

main().catch(console.error);
