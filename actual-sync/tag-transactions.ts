import * as api from '@actual-app/api';
import * as path from 'path';
import yargs from 'yargs/yargs';
import { hideBin } from 'yargs/helpers';
import { Config, loadConfig, initActual, shutdownActual } from './utils';
import { loadTagConfig, matchesRule, TagConfig, TagRule, escapeRegex, addTags, removeTag, sortTagConfig, saveTagConfig } from './tag-utils';

// --- Interfaces ---


interface TransactionUpdate {
    id: string;
    date: string;
    payee?: string; // ID
    payee_name?: string; // Resolved name
    account_name?: string; // Resolved name
    original_notes: string;
    new_notes: string;
    category_name?: string; // Resolved name
    account_off_budget: boolean;
    added_tags: string[];
    removed_tags: string[];
}

// --- Logic ---

console.log('DEBUG: process.argv:', process.argv);
const argv = yargs(hideBin(process.argv))
    .option('config-file', {
        type: 'string',
        description: 'Path to tags configuration file',
        default: '../config/tags.yaml'
    })
    .option('since', {
        type: 'string',
        description: 'Process transactions on or after this date (YYYY-MM-DD)'
    })
    .option('commit', {
        type: 'boolean',
        description: 'Apply changes to Actual Budget',
        default: false
    })
    .option('sort', {
        type: 'boolean',
        description: 'Sort the tags configuration file alphabetically',
        default: false
    })
    .option('list-uncategorized', {
        type: 'boolean',
        description: 'List transactions that do not have a category',
        default: false
    })
    .option('remove-tag', {
        type: 'string',
        description: 'Remove a specific tag from all transactions'
    })
    .parseSync();
console.log('DEBUG: parsed argv:', argv);



async function main() {
    console.log('Starting Transaction Tagger...');

    // 0. Load Main Config (for connection)
    let config: Config;
    // Assuming config.yaml is in the same folder as simple config loader usually expects or passed via env?
    // Use hardcoded logical path or relative to script.
    // clean-notes.ts uses: path.join(argv['config-dir'], 'config.yaml');
    // We'll mimic that structure but just look in ./config for now as default
    const mainConfigPath = path.resolve(__dirname, '../config', 'config.yaml');
    try {
        config = loadConfig(mainConfigPath);
    } catch (e: any) {
        // Fallback or error
        console.error("Could not load main config.yaml from " + mainConfigPath);
        // Try default location if loadConfig handles it? 
        // loadConfig implementation likely reads from file.
        process.exit(1);
    }

    // 1. Load Tag Rules
    let tagConfig: TagConfig;
    try {
        tagConfig = loadTagConfig(argv['config-file']);
        console.log(`Loaded ${tagConfig.rules.length} tagging rules.`);
    } catch (e: any) {
        console.error(e.message);
        process.exit(1);
    }

    // 1.5 Always Sort and Update Config
    // This ensures that the tags file is always strictly ordered by name and rules
    const sortedConfig = sortTagConfig(tagConfig);
    try {
        saveTagConfig(argv['config-file'], sortedConfig);
        // console.log(`Sorted and saved configuration: ${argv['config-file']}`);
    } catch (e: any) {
        console.error(`Failed to save sorted configuration: ${e.message}`);
        // We don't exit here, as we can still process with the loaded (and sorted in memory) config
        // but it's worth noting the file wasn't updated.
    }

    // If only sorting was requested, exit now
    if (argv.sort) {
        console.log(`Successfully yielded sorted configuration: ${argv['config-file']}`);
        return;
    }

    // 2. Connect to Actual
    try {
        await initActual(config);
    } catch (e: any) {
        console.error(e.message);
        process.exit(1);
    }

    // 3. Fetch Data
    console.log('Fetching accounts and payees...');
    const accounts = await api.getAccounts();
    const payees = await api.getPayees();
    const categories = await api.getCategories();

    const accountMap = new Map<string, string>(); // ID -> Name
    const accountOffBudgetMap = new Map<string, boolean>(); // ID -> OffBudget
    accounts.forEach(a => {
        accountMap.set(a.id, a.name);
        accountOffBudgetMap.set(a.id, !!a.offbudget);
    });

    const payeeMap = new Map<string, string>(); // ID -> Name
    payees.forEach(p => payeeMap.set(p.id, p.name));

    const categoryMap = new Map<string, string>(); // ID -> Name
    categories.forEach(c => categoryMap.set(c.id, c.name));

    // 4. Fetch Transactions
    let sinceDate = '1900-01-01'; // Default: All time
    if (argv.since) {
        sinceDate = argv.since;
        console.log(`Fetching transactions since ${sinceDate}...`);
    } else {
        console.log(`Fetching all transactions...`);
        // If listing uncategorized, maybe default to a reasonable timeframe if not specified? 
        // But user asked for "list transactions", let's assume all or --since.
    }

    // To get transactions for all accounts, we can use api.getTransactions with null accountId 
    // if supported, or iterate accounts. Actual API usually requires account ID or we can use budget.
    // Let's iterate accounts to be safe and consistent with previous scripts.

    let allTransactions: any[] = [];
    for (const account of accounts) {
        const txs = await api.getTransactions(account.id, sinceDate, '2100-01-01');
        // Attach account ID to tx for reference if not present (usually it is)
        // tx.account = account.id; 
        allTransactions.push(...txs);
    }

    console.log(`Fetched ${allTransactions.length} transactions total.`);

    // 4.5 Handle List Uncategorized Command
    if (argv['list-uncategorized']) {
        console.log('\nScanning for uncategorized transactions (On-Budget only)...');
        let uncategorizedCount = 0;

        for (const tx of allTransactions) {
            // Skip off-budget accounts
            if (accountOffBudgetMap.get(tx.account)) {
                continue;
            }
            if (tx.transfer_id) {
                // skip transfers
                continue;
            }

            if (!tx.category) {
                uncategorizedCount++;
                const accountName = accountMap.get(tx.account) || 'Unknown Account';
                const payeeName = payeeMap.get(tx.payee) || '(No Payee)';
                const notes = tx.notes || '';

                // Extract tags
                const tagRegex = /#[\w-]+/g;
                const tags = (notes.match(tagRegex) || []).map((t: string) => t.toLowerCase()).sort();

                if (tags.length === 0) {
                    continue;
                }

                const uniqueTags = Array.from(new Set(tags));

                console.log(`\nTransaction: [${tx.date}] ${payeeName} (${accountName}) Amount: ${tx.amount / 100}`);
                console.log(`  Notes: "${notes}"`);
                if (uniqueTags.length > 0) {
                    console.log(`  Tags: ${uniqueTags.join(', ')}`);
                } else {
                    console.log(`  Tags: (None)`);
                }
            }
        }

        console.log(`\nFound ${uncategorizedCount} uncategorized transactions.`);
        await shutdownActual();
        return;
    }

    // 5. Apply Rules OR Remove Tag
    let updates: TransactionUpdate[] = [];

    if (argv['remove-tag']) {
        console.log(`\nRemoving tag "${argv['remove-tag']}" from transactions...`);
        const tagToRemove = argv['remove-tag'];

        for (const tx of allTransactions) {
            const accountName = accountMap.get(tx.account) || 'Unknown Account';
            const payeeName = payeeMap.get(tx.payee) || '';
            const categoryName = categoryMap.get(tx.category) || '';

            let currentNotes = tx.notes || '';
            const originalNotes = currentNotes;

            // Only attempt removal if the tag might be there (simple check first)
            // We can use a regex similar to removeTag to be sure we don't skip normalization?
            // Actually, if we want to ONLY remove the tag and NOT normalize others if tag isn't there:
            // We should check if removeTag actually removed it.
            // But removeTag normalizes anyway.
            // So if we only want updates when the tag IS removed:
            const tagRegex = new RegExp(`#${escapeRegex(tagToRemove.replace(/^#/, ''))}\\b`, 'i');
            if (tagRegex.test(currentNotes)) {
                currentNotes = removeTag(currentNotes, tagToRemove);
            }

            if (currentNotes !== originalNotes) {
                updates.push({
                    id: tx.id,
                    date: tx.date,
                    payee: tx.payee,
                    payee_name: payeeName,
                    account_name: accountName,
                    category_name: categoryName,
                    original_notes: tx.notes,
                    new_notes: currentNotes,
                    account_off_budget: accountOffBudgetMap.get(tx.account) || false,
                    added_tags: [], // No added tags
                    removed_tags: [tagToRemove]
                });
            }
        }
    } else {
        // Normal Tagging Mode
        for (const tx of allTransactions) {
            // Resolve names
            const accountName = accountMap.get(tx.account) || 'Unknown Account';
            const payeeName = payeeMap.get(tx.payee) || ''; // Transfer payees might be null or have special handling?
            const categoryName = categoryMap.get(tx.category) || '';
            // Note: Transfers have payee field as null usually and transfer_id set. 
            // We can potentially match on transfer payee names if we resolve them via transfer_id but keep it simple for now.

            if (tx.is_parent) {
                // Handle split transactions? 
                // Usually we tag the parent or the subtransactions?
                // If we tag the parent notes, it applies to the whole.
                // Let's focus on non-split or parent for now.
            }

            let currentNotes = tx.notes || '';
            const originalNotes = currentNotes;
            const addedTags: string[] = [];

            for (const rule of tagConfig.rules) {
                if (matchesRule(tx, rule, payeeName, accountName, categoryName)) {
                    for (const tag of rule.tags) {
                        const previousNotes = currentNotes;
                        currentNotes = addTags(currentNotes, [tag]);

                        // simple check to see if we should report it as "added"
                        // This isn't perfect if addTag only re-sorted, but it's close enough for reporting
                        // unless we check existence beforehand. 
                        // Let's check existence beforehand to be accurate about "added" vs "sorted"
                        // actually, addTag handles the check.
                        // Let's just track that we matched this rule.
                        if (!previousNotes.includes(tag) && currentNotes.includes(tag)) {
                            addedTags.push(tag);
                        }
                    }
                }
            }

            if (currentNotes !== originalNotes) {
                updates.push({
                    id: tx.id,
                    date: tx.date,
                    payee: tx.payee,
                    payee_name: payeeName,
                    account_name: accountName,
                    category_name: categoryName,
                    original_notes: tx.notes,
                    new_notes: currentNotes,
                    account_off_budget: accountOffBudgetMap.get(tx.account) || false,
                    added_tags: addedTags,
                    removed_tags: []
                });
            }
        }
    }

    updates = updates.sort((a, b) => {
        // 1. Primary Sort: On-Budget (false) before Off-Budget (true)
        // This ensures ALL Off-Budget items are at the very bottom
        if (a.account_off_budget !== b.account_off_budget) {
            return a.account_off_budget ? 1 : -1;
        }

        // 2. Secondary Sort: Uncategorized first (within their budget group)
        const aEmpty = !a.category_name;
        const bEmpty = !b.category_name;

        if (aEmpty !== bEmpty) {
            return aEmpty ? -1 : 1;
        }

        // 3. Tertiary Sort: Date (Ascending)
        return a.date.localeCompare(b.date);
    });

    // 6. Commit or Report
    if (updates.length > 0) {


        let updateCount = 0;
        console.log(`\nFound ${updates.length} transactions to update:`);
        for (const update of updates) {
            console.log(`  Transaction: ${updateCount + 1}/${updates.length}: [${update.date}] ${update.payee_name} (${update.account_name}) [${update.category_name || 'No Category'}]`);

            const existingTagsList = (update.original_notes?.match(/#[\w-]+/g) || []).map(t => t.toLowerCase());
            const newTagsList = (update.new_notes?.match(/#[\w-]+/g) || []).map(t => t.toLowerCase());

            console.log(`    = Existing tags: ${existingTagsList.join(', ')}`);
            if (update.added_tags.length > 0) console.log(`    + Tags: ${update.added_tags.join(', ')}`);
            if (update.removed_tags.length > 0) console.log(`    - Tags: ${update.removed_tags.join(', ')}`);
            console.log(`    $ New Tags: ${newTagsList.join(', ')}`);
            updateCount++;
        }

        if (argv.commit) {
            console.log(`\nCommitting ${updates.length} updates to Actual Budget...`);
            await api.batchBudgetUpdates(async () => {
                let updateCount = 0;
                for (const update of updates) {
                    await api.updateTransaction(update.id, { notes: update.new_notes });
                    updateCount++;
                }
            });
            console.log(`\nSUCCESS: Updated ${updates.length} transactions.`);
            await api.sync();
        } else {
            console.log(`\nDry run complete. Use --commit to apply changes.`);
        }
    } else {
        console.log('\nNo matching transactions found to update.');
    }

    await shutdownActual();
}





if (require.main === module) {
    main().catch(err => {
        console.error(err);
        process.exit(1);
    });
}
