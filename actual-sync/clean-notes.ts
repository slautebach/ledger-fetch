/**
 * Transaction Note Cleaner
 * 
 * This script iterates through all transactions in Actual Budget and cleans up the notes.
 * Specifically, it extracts hashtags (e.g., #tag) from anywhere in the note and moves them 
 * to the end of the note, separated by spaces.
 * 
 * It also cleans up extra whitespace.
 * 
 * Usage:
 *   npx ts-node clean-notes.ts [--config-dir <path>] [--commit]
 * 
 *   --commit: Actually apply the changes. Without this flag, it runs in dry-run mode.
 */

import * as api from '@actual-app/api';
import * as path from 'path';
import yargs from 'yargs/yargs';
import { hideBin } from 'yargs/helpers';
import { Config, loadConfig, initActual, shutdownActual } from './utils';
import { cleanAndSortTags } from './tag-utils';

// Parse arguments
const argv = yargs(hideBin(process.argv))
    .option('config-dir', {
        type: 'string',
        description: 'Path to config directory',
        default: '../config'
    })
    .option('commit', {
        type: 'boolean',
        description: 'Apply changes to Actual Budget',
        default: false
    })
    .parseSync();



async function main() {
    console.log('Starting Transaction Note Cleaner...');

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

    console.log('Fetching all accounts...');
    const accounts = await api.getAccounts();
    console.log(`Found ${accounts.length} accounts.`);

    let totalChanges = 0;

    for (const account of accounts) {
        // console.log(`Checking transactions for account: ${account.name}`);

        // Fetch all transactions for this account
        // We use a wide date range to get everything
        const transactions = await api.getTransactions(account.id, '1900-01-01', '2100-01-01');

        const updates = [];

        for (const tx of transactions) {
            if (tx.notes) {
                const cleaned = cleanAndSortTags(tx.notes);
                if (cleaned !== tx.notes) {
                    updates.push({
                        id: tx.id,
                        original: tx.notes,
                        cleaned: cleaned,
                        date: tx.date,
                        payee: tx.payee
                    });
                }
            }
        }

        if (updates.length > 0) {
            console.log(`\nAccount: ${account.name} (${updates.length} updates needed)`);

            for (const update of updates) {
                console.log(`  [${update.date}]`);
                console.log(`    Old: "${update.original}"`);
                console.log(`    New: "${update.cleaned}"`);

                if (argv.commit) {
                    await api.updateTransaction(update.id, { notes: update.cleaned });
                    // process.stdout.write('.'); // Progress indicator
                }
            }
            totalChanges += updates.length;
        }
    }

    console.log('\n------------------------------------------------');
    if (argv.commit) {
        console.log(`Successfully updated ${totalChanges} transactions.`);
    } else {
        console.log(`Dry Run Complete. Found ${totalChanges} transactions to update.`);
        console.log('Run with --commit to apply changes.');
    }

    await shutdownActual();
}

if (require.main === module) {
    main().catch(err => {
        console.error(err);
        process.exit(1);
    });
}
