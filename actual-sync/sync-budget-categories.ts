/**
 * Sync Budget Categories Script
 *
 * Purpose:
 * This script synchronizes categories between the local `budget-categories.yaml` definition
 * and the Actual Budget server. It uses the `CategoryImporter` class to reconcile changes.
 *
 * Workflow:
 * 1. Connects to Actual Budget.
 * 2. Initializes `CategoryImporter` with the path to the YAML configuration.
 * 3. Pulls any missing categories from the server to update the local YAML (Bidirectional sync).
 * 4. Pushes local categories to the server (creation).
 *
 * Usage:
 *   npx ts-node sync-budget-categories.ts [--config-dir <path>]
 */
import * as path from 'path';
import yargs from 'yargs/yargs';
import { hideBin } from 'yargs/helpers';
import { CategoryImporter } from './category-importer';
import { Config, loadConfig, initActual, shutdownActual } from './utils';

// Config interface removed

// Parse arguments
const argv = yargs(hideBin(process.argv))
    .option('config-dir', {
        type: 'string',
        description: 'Path to config directory',
        default: './config'
    })
    .parseSync();

/**
 * Main execution function
 */
async function main() {
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

    // 3. Run Importer
    const categoriesPath = path.join(argv['config-dir'], 'budget-categories.yaml');
    try {
        const importer = new CategoryImporter(categoriesPath);
        // Sync down missing categories first
        await importer.pullMissingCategories();
        // Then sync up
        await importer.import();
    } catch (e: any) {
        console.error('Import failed:', e.message);
    } finally {
        await shutdownActual();
    }
}

main().catch(err => {
    console.error(err);
    process.exit(1);
});
