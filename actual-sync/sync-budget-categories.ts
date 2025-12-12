/**
 * Import Categories Script
 *
 * This script is responsible for synchronizing category groups and categories
 * from a local YAML configuration file (budget-categories.yaml) to Actual Budget.
 *
 * Usage:
 *   npx ts-node import-categories.ts --config <path_to_config>
 */
import * as api from '@actual-app/api';
import * as fs from 'fs';
import * as path from 'path';
import * as yaml from 'js-yaml';
import yargs from 'yargs/yargs';
import { hideBin } from 'yargs/helpers';
import { CategoryImporter } from './category-importer';

interface Config {
    actual: {
        server_url: string;
        password: string;
        sync_id: string;
    };
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

/**
 * Main execution function
 */
async function main() {
    // 1. Load Config
    let config: Config;
    try {
        const configPath = path.resolve(argv.config);
        console.log(`Loading config from: ${configPath}`);
        if (!fs.existsSync(configPath)) {
            console.error(`Config file not found at ${configPath}`);
            process.exit(1);
        }
        const fileContents = fs.readFileSync(configPath, 'utf8');
        config = yaml.load(fileContents) as Config;
        console.log('Config loaded. Actual keys:', config.actual ? Object.keys(config.actual) : 'missing');
    } catch (e) {
        console.error('Error loading config:', e);
        process.exit(1);
    }

    if (!config.actual || !config.actual.server_url || !config.actual.password || !config.actual.sync_id) {
        console.error('Missing "actual" configuration in config.yaml');
        process.exit(1);
    }

    // 2. Connect to Actual
    // We must initialize the API with the server URL and password.
    // The dataDir is where Actual stores its local database file (SQLite).
    console.log(`Connecting to Actual Budget at ${config.actual.server_url} with Sync ID ${config.actual.sync_id}...`);
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
    } catch (e: any) {
        console.error('Failed to connect to Actual Budget:', e.message);
        process.exit(1);
    }

    // 3. Run Importer
    const categoriesPath = path.join(__dirname, 'budget-categories.yaml');
    try {
        const importer = new CategoryImporter(categoriesPath);
        await importer.import(argv['dry-run']);
    } catch (e: any) {
        console.error('Import failed:', e.message);
    } finally {
        await api.shutdown();
    }
}

main().catch(err => {
    console.error(err);
    process.exit(1);
});
