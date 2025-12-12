/**
 * Import Rules Script
 *
 * This script synchronizes rules from a local YAML file (actual_rules.yaml) to Actual Budget.
 * It handles:
 *  - Creating new rules
 *  - Updating existing rules
 *  - Ensuring payees referenced in rules exist
 *  - Writing back generated Rule IDs to the local YAML file for future updates
 */
import * as api from '@actual-app/api';
import * as fs from 'fs';
import * as path from 'path';
import * as yaml from 'js-yaml';

// --- Configuration ---
const CONFIG_PATH = path.resolve('../config.yaml');
const RULES_YAML_PATH = path.resolve('./actual_rules.yaml');
const ACCOUNT_MAP_PATH = path.resolve(__dirname, 'account-map.json');

// --- Interfaces ---
interface YamlCondition {
    field: string;
    op: string;
    value: any;
}

interface YamlAction {
    field: string;
    op?: string;
    value: any;
}

interface YamlRule {
    id?: string;
    stage?: string;
    conditions: YamlCondition[];
    op?: 'and' | 'or'; // condition operator
    actions: YamlAction[];
}

interface YamlFile {
    rules: YamlRule[];
}

// --- Helper Functions ---
/**
 * Helper function to compare two rule objects for equality.
 * This is used to determine if an update is necessary.
 *
 * @param r1 Rule object 1 (usually the local definition)
 * @param r2 Rule object 2 (usually the existing rule from API)
 */
function rulesEqual(r1: any, r2: any): boolean {
    if (r1.stage !== r2.stage) return false;
    if (r1.conditionsOp !== r2.conditionsOp) return false;

    if (r1.conditions.length !== r2.conditions.length) return false;
    for (let i = 0; i < r1.conditions.length; i++) {
        const c1 = r1.conditions[i];
        const c2 = r2.conditions[i];
        if (c1.field !== c2.field || c1.op !== c2.op) return false;
        // Simple value check - for arrays/objects this might need better deep equals,
        // but for now JSON.stringify is a decent catch-all for structural equality.
        if (JSON.stringify(c1.value) !== JSON.stringify(c2.value)) return false;
    }

    if (r1.actions.length !== r2.actions.length) return false;
    for (let i = 0; i < r1.actions.length; i++) {
        const a1 = r1.actions[i];
        const a2 = r2.actions[i];
        if (a1.field !== a2.field || a1.op !== a2.op) return false;
        if (JSON.stringify(a1.value) !== JSON.stringify(a2.value)) return false;
    }

    return true;
}

// --- Main ---
// --- Main ---
async function main() {
    console.log('--- Starting Rule Import ---');

    // 1. Load Config
    // Loads connection details for Actual Budget
    if (!fs.existsSync(CONFIG_PATH)) {
        console.error(`Config file not found: ${CONFIG_PATH}`);
        process.exit(1);
    }
    const config = yaml.load(fs.readFileSync(CONFIG_PATH, 'utf8')) as any;

    // 2. Load Rules YAML
    if (!fs.existsSync(RULES_YAML_PATH)) {
        console.error(`Rules file not found: ${RULES_YAML_PATH}`);
        process.exit(1);
    }
    const yamlContent = fs.readFileSync(RULES_YAML_PATH, 'utf8');
    const rulesData = yaml.load(yamlContent) as YamlFile;
    const rules = rulesData.rules;

    console.log(`Loaded ${rules.length} rules from YAML.`);

    // 3. Connect to Actual
    console.log('Connecting to Actual Budget...');

    // Trim values to avoid whitespace issues
    const serverURL = config.actual.server_url.trim();
    const syncID = config.actual.sync_id.trim();
    const password = config.actual.password.trim();

    console.log(`  Server: ${serverURL}`);
    console.log(`  Sync ID: ${syncID}`);

    const dataDir = path.resolve(__dirname, 'data');
    if (!fs.existsSync(dataDir)) {
        console.log(`Creating data directory at ${dataDir}...`);
        fs.mkdirSync(dataDir, { recursive: true });
    }

    try {
        await api.init({
            dataDir: dataDir,
            serverURL: serverURL,
            password: password,
        });
        console.log('Initialized. Downloading budget...');
        await api.downloadBudget(syncID);
        console.log('Connected.');
    } catch (e: any) {
        console.error('Connection Error:', e);
        process.exit(1);
    }

    // 4. Ensure Payees Exist
    // Rules often reference Payees. If a rule sets a Payee that doesn't exist, it might fail or create a mess.
    // We pre-scan rules for any referenced Payees and ensure they exist in Actual.
    console.log('Fetching existing payees...');
    const existingPayees = await api.getPayees();
    const payeeMap = new Map<string, string>(); // Name -> ID
    for (const p of existingPayees) {
        payeeMap.set(p.name, p.id);
    }

    // 4b. Load Account Map
    console.log('Loading Account Map...');
    let accountMap: Record<string, string> = {};
    if (fs.existsSync(ACCOUNT_MAP_PATH)) {
        try {
            accountMap = JSON.parse(fs.readFileSync(ACCOUNT_MAP_PATH, 'utf8'));
            console.log(`  Loaded ${Object.keys(accountMap).length} account mappings.`);
        } catch (e) {
            console.error('  Error loading account map:', e);
        }
    } else {
        console.log('  No account map found.');
    }

    // Identify payees used in rule actions
    const payeesToEnsure = new Set<string>();
    for (const r of rules) {
        // Check actions
        for (const a of r.actions) {
            if (a.field === 'payee' && a.value) {
                if (Array.isArray(a.value)) {
                    a.value.forEach((v: string) => payeesToEnsure.add(v));
                } else {
                    payeesToEnsure.add(a.value);
                }
            }
        }
        // Check conditions
        for (const c of r.conditions) {
            if (c.field === 'payee' && c.value) {
                if (Array.isArray(c.value)) {
                    c.value.forEach((v: string) => payeesToEnsure.add(v));
                } else {
                    payeesToEnsure.add(c.value);
                }
            }
        }
    }

    // Create missing payees
    for (const payeeName of payeesToEnsure) {
        if (!payeeMap.has(payeeName)) {
            console.log(`Payee '${payeeName}' not found. Creating...`);
            try {
                const newPayeeId = await api.createPayee({ name: payeeName });
                console.log(`  -> Created with ID: ${newPayeeId}`);
                payeeMap.set(payeeName, newPayeeId);
            } catch (e: any) {
                console.error(`  Failed to create payee '${payeeName}': ${e.message}`);
            }
        }
    }

    // 5. Fetch Existing Rules
    console.log('Fetching existing rules...');
    const existingRules = await api.getRules();
    const existingRulesMap = new Map<string, any>();
    for (const r of existingRules) {
        existingRulesMap.set(r.id, r);
    }

    // 6. Iterate and Upsert
    let changesMade = false;

    for (let i = 0; i < rules.length; i++) {
        const r = rules[i];

        // Prepare rule object for API
        // Actual API expects: { stage, conditionsOp, conditions, actions, id? }
        const rulePayload: any = {
            // Map 'pre' to null as Actual uses null for pre-stage
            stage: r.stage === 'pre' ? null : (r.stage || null),
            conditionsOp: r.op || 'and',
            conditions: r.conditions.map(c => {
                let val = c.value;
                if (c.field === 'payee') {
                    if (Array.isArray(val)) {
                        val = val.map((v: string) => payeeMap.has(v) ? payeeMap.get(v) : v);
                    } else if (payeeMap.has(val)) {
                        val = payeeMap.get(val);
                    }
                }
                if (c.field === 'account') {
                    if (accountMap[val]) {
                        val = accountMap[val];
                    }
                }
                return {
                    field: c.field,
                    op: c.op,
                    value: val
                };
            }),
            actions: r.actions.map(a => {
                let val = a.value;
                if (a.field === 'payee') {
                    if (Array.isArray(val)) {
                        val = val.map((v: string) => payeeMap.has(v) ? payeeMap.get(v) : v);
                    } else if (payeeMap.has(val)) {
                        val = payeeMap.get(val);
                    }
                }
                if (a.field === 'account') {
                    if (accountMap[val]) {
                        val = accountMap[val];
                    }
                }
                return {
                    field: a.field,
                    op: a.op || 'set', // Default to 'set'
                    value: val
                };
            })
        };

        try {
            if (r.id) {
                // UPDATE
                if (existingRulesMap.has(r.id)) {
                    const existing = existingRulesMap.get(r.id);
                    rulePayload.id = r.id; // ensure ID is in payload for creating new ones, but for compare we need it
                    // To compare, we should form the "expected" object.
                    // The API returns explicit objects, our rulePayload is what we WANT.
                    // We compare rulePayload vs existing.
                    if (rulesEqual(rulePayload, existing)) {
                        console.log(`[${i + 1}/${rules.length}] Skipping rule ${r.id} (no changes)`);
                        continue;
                    }
                }

                console.log(`[${i + 1}/${rules.length}] Updating rule ${r.id}...`);
                rulePayload.id = r.id;
                await api.updateRule(rulePayload);
            } else {
                // CREATE
                console.log(`[${i + 1}/${rules.length}] Creating new rule...`);
                const response = await api.createRule(rulePayload);
                const newId = (typeof response === 'string') ? response : (response as any)?.id;
                console.log(`  -> Created with ID: ${newId} (Response type: ${typeof response})`);

                // Update local object to write back later
                if (newId) {
                    (r as any).id = newId;
                    changesMade = true;
                }
            }
        } catch (e: any) {
            console.error(`  Error processing rule #${i + 1}: ${e.message}`);
        }
    }

    // 5. Write Back IDs if needed
    // If we created new rules, they now have IDs. We write these IDs back to the YAML file
    // so that subsequent runs can identify them as "existing" rules and update them instead of creating duplicates.
    if (changesMade) {
        console.log('Writing new IDs back to actual_rules.yaml...');
        const newYaml = yaml.dump({ rules: rules }, { schema: yaml.JSON_SCHEMA, noRefs: true, quotingType: '"' });
        // Note: yaml.dump might coerce comments or formatting. 
        // Ideally we'd preserve comments, but standard js-yaml dump doesn't.
        // The user's goal was "update the yaml with the id", effectively accepting a reformat.
        fs.writeFileSync(RULES_YAML_PATH, newYaml, 'utf8');
        console.log('File updated.');
    } else {
        console.log('No new IDs to write back.');
    }

    console.log('Syncing...');
    await api.sync();
    await api.shutdown();
    console.log('Done.');
}

main().catch(err => {
    console.error(err);
    process.exit(1);
});
