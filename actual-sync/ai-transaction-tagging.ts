import * as api from '@actual-app/api';
import { spawn, execSync } from 'child_process';
import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';
import yargs from 'yargs/yargs';
import { hideBin } from 'yargs/helpers';
import { Config, loadConfig, initActual, shutdownActual } from './utils';
import { loadTagConfig, matchesRule, TagConfig, TagRule, escapeRegex } from './tag-utils';
import { createRequire } from 'module'; // Needed if you are using ESM/TypeScript
import { cleanAndSortTags, addTags } from './tag-utils';


// --- Logic ---

async function callGemini(basePrompt: string, jsonData: string, config: Config): Promise<any> {
    return new Promise((resolve, reject) => {
        const aiPath = config.ai?.path;
        if (!aiPath) return reject(new Error("AI path not configured"));
        // 1. Create a temporary file path
        const tempFilePath = path.join(aiPath, "transaction", `transaction-under-analysis.json`);

        try {
            // 2. Write the data to the temp file synchronously
            fs.writeFileSync(tempFilePath, jsonData, 'utf8');
        } catch (err: any) {
            return reject(new Error(`Failed to write temp file: ${err.message}`));
        }

        // Construct the prompt with the @file syntax
        // The user wants: gemini -p "Prompt... @file" -y -o json
        // We'll append the file reference to the prompt.
        const fullPrompt = `${basePrompt} Context: @${tempFilePath}`;


        // 1. Dynamically find the global npm installation folder
        // This returns something like: C:\Users\Shawn\AppData\Roaming\npm\node_modules
        const globalNodeModules = execSync('npm root -g', { encoding: 'utf8' }).trim();

        // 2. Construct the full path to the JS file based on the package.json structure
        const geminiScriptPath = path.join(globalNodeModules, '@google/gemini-cli', 'dist', 'index.js');

        // Verify it exists before trying to run it (Good for debugging)
        if (!fs.existsSync(geminiScriptPath)) {
            throw new Error(`Could not find global gemini-cli at: ${geminiScriptPath}`);
        }

        // Detect if we are on Windows
        const command = process.execPath;

        const args = [
            geminiScriptPath, // The script becomes the first argument to Node
            '-p', fullPrompt,
            '-y',
            '-o', 'json',
            '-m', 'gemini-3-flash-preview'
        ];

        // 4. Run the CLI
        const psCommand = `${command} ${geminiScriptPath} -p '${fullPrompt}' -y -o json`;
        console.log(`\n[DEBUG] Executing PowerShell Command:\n${psCommand}\n`);

        const child = spawn(command, args, {
            stdio: ['ignore', 'pipe', 'pipe'],
            shell: false, // FIX: Explicitly strictly false
            cwd: aiPath
        });

        let stdoutData = '';
        let stderrData = '';

        child.stdout.on('data', (data) => {
            stdoutData += data.toString();
        });

        child.stderr.on('data', (data) => {
            stderrData += data.toString();
        });

        child.on('close', (code) => {
            // 5. CLEANUP: Delete the file after the process finishes
            try {
                if (fs.existsSync(tempFilePath)) fs.unlinkSync(tempFilePath);
            } catch (cleanupErr) {
                console.warn("Failed to cleanup temp file:", cleanupErr);
            }

            if (code !== 0) {
                return reject(new Error(`Gemini process exited with code ${code}: ${stderrData}`));
            }

            try {
                // Attempt to find JSON in the output (in case of extra text)
                const jsonMatch = stdoutData.match(/\{[\s\S]*\}/);
                if (jsonMatch) {
                    resolve(JSON.parse(jsonMatch[0]));
                } else {
                    // Fallback: try parsing the whole thing
                    resolve(JSON.parse(stdoutData));
                }
            } catch (err: any) {
                reject(new Error(`Failed to parse Gemini output: ${err.message}\nOutput: ${stdoutData}`));
            }
        });
    });
}

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
    .parseSync();



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
    accounts.forEach(a => accountMap.set(a.id, a.name));

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
            continue;
        }

        const txData = {
            "transaction_date": tx.date,
            "amount": tx.amount, // maybe format this?
            "original_payee": payeeName, // This might be raw or already cleaned. 
            // Ideally we want the imported_payee if available, or just the current payee.
            // But for 'raw' context, the notes often have raw details if the bank importer put them there.
            "notes": tx.notes,
            "account": accountName,
            "current_category": categoryName // Optional, maybe we don't want to bias it too much?
        };

        const txDataJson = JSON.stringify(txData);

        // For testing, use a very simple prompt to verify integration
        const prompt = ` @suggest_tags.md`;

        try {
            console.log(`Analyzing: ${tx.date} - ${payeeName} (${tx.amount})...`);
            const result = await callGemini(prompt, txDataJson, config);


            console.log(`Gemini Result:`);
            console.log(result);
            const response = result.response;
            let jsonContent = response;
            const jsonMatch = response.match(/```json\s*([\s\S]*?)\s*```/);
            if (jsonMatch) {
                jsonContent = jsonMatch[1];
            }

            const ai_response = JSON.parse(jsonContent);
            if (ai_response) {
                console.log(`  -> Suggested Tags: ${JSON.stringify(ai_response.suggested_tags)}`);
                console.log(ai_response);
                tx.notes = addTags(tx.notes, ai_response.suggested_tags);
                console.log(tx);
            }

        } catch (error: any) {
            console.error(`Error processing transaction ${tx.id}:`, error.message);
        }
    }
    await shutdownActual();
}

if (require.main === module) {
    main().catch(err => {
        console.error(err);
        process.exit(1);
    });
}
