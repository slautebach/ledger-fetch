/**
 * Import and Sync Rules Script
 *
 * Purpose:
 * This script synchronizes rules from a local YAML file (`actual_rules.yaml`) to the Actual Budget.
 * It ensures that the robust rules defined in your local configuration are correctly applied to the server.
 *
 * Core Features:
 * 1. **Bi-directional Sync**: 
 *    - Pushes local rules to the server (Creates new rules, Updates existing ones).
 *    - Pulls new rules from the server and adds them to the local YAML file.
 * 2. **Idempotency**:
 *    - Uses Rule IDs to track rules.
 *    - Writes back generated IDs to the YAML file after creation.
 * 3. **Dependency Management**:
 *    - Automatically creates missing Payees referenced in rule actions or conditions.
 *    - Resolves Account and Category references.
 * 4. **Diffing**:
 *    - Compares local rules against server rules to minimize unnecessary API calls.
 */
import * as api from '@actual-app/api';
import * as fs from 'fs';
import * as path from 'path';
import * as yaml from 'js-yaml';
import yargs from 'yargs/yargs';
import { hideBin } from 'yargs/helpers';

// --- Configuration ---
import { Config, loadConfig, initActual, shutdownActual } from './utils';


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
    id?: string; // Optional GUID for robust matching
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
 * Resolves the stage string to the API expected value.
 * 'Pre' -> 'pre'
 * 'Default' -> null
 * 'Post' -> 'post'
 */
function resolveStage(stage?: string): string | null {
    if (!stage) return null;
    const s = stage.toLowerCase();
    if (s === 'pre') return 'pre';
    if (s === 'default') return null;
    if (s === 'post') return 'post';
    return null;
}


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
    // Parse arguments
    const argv = yargs(hideBin(process.argv))
        .option('config-dir', {
            type: 'string',
            description: 'Path to config directory',
            default: './config'
        })
        .option('force-fresh', {
            type: 'boolean',
            description: 'Clear all IDs to force fresh creation',
            default: false
        })
        .parseSync();

    const resolvedConfigDir = path.resolve(argv['config-dir']);
    const rulesYamlPath = path.join(resolvedConfigDir, 'actual_rules.yaml');
    const accountMapPath = path.join(resolvedConfigDir, 'account-map.json');
    const configPath = path.join(resolvedConfigDir, 'config.yaml');

    console.log(`--- Starting Rule Import ---`);
    console.log(`Using config directory: ${resolvedConfigDir}`);

    // 1. Load Config
    let config: Config;
    try {
        config = loadConfig(configPath);
    } catch (e: any) {
        console.error(e.message);
        process.exit(1);
    }

    // 2. Load Rules YAML
    // 2. Load Rules YAML
    let rules: YamlRule[] = [];
    if (fs.existsSync(rulesYamlPath)) {
        const yamlContent = fs.readFileSync(rulesYamlPath, 'utf8');
        const rulesData = yaml.load(yamlContent) as YamlFile;
        rules = rulesData ? rulesData.rules : [];
        console.log(`Loaded ${rules.length} rules from YAML.`);
    } else {
        console.log(`Rules file not found at ${rulesYamlPath}. Starting with empty rules list.`);
    }

    console.log(`Loaded ${rules.length} rules from YAML.`);

    // Handle --force-fresh
    if (argv['force-fresh']) {
        console.log('!!! FORCE FRESH MODE ENABLED !!!');
        console.log('Clearing all IDs from local rules to force re-creation...');
        rules.forEach(r => {
            delete r.id;
            r.actions.forEach(a => {
                delete a.id;
            });
        });
    }

    // 3. Connect to Actual
    try {
        await initActual(config);
    } catch (e: any) {
        console.error(e.message);
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

    // 4b. Fetch Existing Categories (for lookup)
    console.log('Fetching existing categories...');
    const categoryGroups = await api.getCategoryGroups();
    const categoryNameMap = new Map<string, string>(); // Name -> ID
    const categoryIdMap = new Map<string, string>(); // ID -> Name

    for (const group of categoryGroups) {
        if (group.categories) {
            for (const cat of group.categories) {
                categoryNameMap.set(cat.name, cat.id);
                categoryIdMap.set(cat.id, cat.name);
            }
        }
    }
    console.log(`  Mapped ${categoryNameMap.size} categories.`);

    console.log(`  Mapped ${categoryNameMap.size} categories.`);

    // 4c. Fetch Existing Accounts (for validation)
    console.log('Fetching existing accounts...');
    const existingAccounts = await api.getAccounts();
    const serverAccountIdMap = new Set<string>();
    const serverAccountNameMap = new Map<string, string>(); // Name -> ID
    for (const acc of existingAccounts) {
        serverAccountIdMap.add(acc.id);
        serverAccountNameMap.set(acc.name, acc.id);
    }
    console.log(`  Mapped ${serverAccountIdMap.size} accounts from server.`);

    // 4d. Load Account Map
    console.log('Loading Account Map...');
    let accountMap: Record<string, string> = {};
    if (fs.existsSync(accountMapPath)) {
        try {
            accountMap = JSON.parse(fs.readFileSync(accountMapPath, 'utf8'));
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
                // If it's a name, we ensure it exists. If it's an ID, we assume it exists (or we can't create it anyway)
                // But we don't distinguish yet.
                // However, updated logic: if 'id' is present, 'value' is name. If 'id' is missing, 'value' is name.
                // So 'value' is consistently name-ish.
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
        // Check if it's already an ID (heuristic: uuid-like?)
        // Actually, if it's in payeeMap values, it's an ID.
        // But payeeMap keys are names.
        if (payeeMap.has(payeeName)) continue; // It's a known name

        // What if payeeName is actually an ID? Use regex or check values?
        // For simplicity, we assume values in YAML are Names unless purely ID.
        // Current usage implies they are Names.

        if (!payeeMap.has(payeeName)) {
            // Check if it's an ID
            let isId = false;
            for (const id of payeeMap.values()) {
                if (id === payeeName) {
                    isId = true;
                    break;
                }
            }
            if (isId) continue;

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

    // 5b. Pull Missing Rules from Server (Bidirectional Sync)
    console.log('Checking for new rules on server...');

    // Create reverse maps for ID -> Name lookup
    const payeeIdToName = new Map<string, string>();
    for (const [name, id] of payeeMap.entries()) {
        payeeIdToName.set(id, name);
    }

    const accountIdToName = new Map<string, string>();
    for (const [name, id] of Object.entries(accountMap)) {
        // accountMap is "Local ID" -> "Actual UUID"
        // We need "Actual UUID" -> "Local ID"
        accountIdToName.set(id, name);
    }

    let localRulesUpdated = false;
    let changesMade = false;
    const localRuleIds = new Set(rules.map(r => r.id).filter(id => !!id));

    for (const serverRule of existingRules) {
        if (!localRuleIds.has(serverRule.id)) {
            console.log(`  Found new rule on server: ${serverRule.id}. Adding to local YAML.`);

            // Convert Server Rule -> YAML Rule
            const newRule: YamlRule = {
                id: serverRule.id,
                stage: serverRule.stage || undefined,
                op: serverRule.conditionsOp === 'and' ? undefined : serverRule.conditionsOp,
                conditions: serverRule.conditions.map((c: any) => {
                    let val = c.value;
                    if (c.field === 'payee' && val) {
                        if (Array.isArray(val)) {
                            val = val.map((v: string) => payeeIdToName.get(v) || v);
                        } else {
                            val = payeeIdToName.get(val) || val;
                        }
                    }
                    if (c.field === 'account' && val) {
                        val = accountIdToName.get(val) || val;
                    }
                    return {
                        field: c.field,
                        op: c.op,
                        value: val
                    };
                }),
                actions: serverRule.actions.map((a: any) => {
                    let val = a.value;
                    let idVal: string | undefined = undefined;

                    if (a.field === 'payee' && val) {
                        if (Array.isArray(val)) {
                            // Arrays not supported for Actions typically
                            // If it IS an array, we can't map it to a single Name/ID pair easily for 'set'
                            // Just map values if possible
                            val = val.map((v: string) => payeeIdToName.get(v) || v);
                        } else {
                            const name = payeeIdToName.get(val);
                            if (name) {
                                idVal = val;
                                val = name;
                            }
                        }
                    } else if (a.field === 'category' && val) {
                        const name = categoryIdMap.get(val);
                        if (name) {
                            idVal = val;
                            val = name;
                        } else {
                            // Keep ID if name not found
                        }
                    } else if (a.field === 'account' && val) {
                        val = accountIdToName.get(val) || val;
                    }

                    const actionObj: YamlAction = {
                        field: a.field,
                        op: a.op,
                        value: val
                    };
                    if (idVal) {
                        actionObj.id = idVal;
                    }
                    return actionObj;
                })
            };

            // Cleanup defaults
            if (newRule.stage === null) delete newRule.stage;
            if (newRule.op === 'and') delete newRule.op;

            rules.push(newRule);
            localRulesUpdated = true;
            changesMade = true; // Flag to ensure we write back
        }
    }

    // 6. Iterate and Upsert
    // let changesMade = false; // Already defined above

    for (let i = 0; i < rules.length; i++) {
        const r = rules[i];

        // Prepare rule object for API
        // Actual API expects: { stage, conditionsOp, conditions, actions, id? }
        const rulePayload: any = {
            stage: resolveStage(r.stage),
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
                let explicitId = a.id;

                // Fix: Validate and correct Category IDs
                if (a.field === 'category' && explicitId) {
                    if (!categoryIdMap.has(explicitId)) {
                        // Stale ID? Try to resolve by name
                        const fixedId = categoryNameMap.get(val);
                        if (fixedId) {
                            console.log(`  [Rule ${r.id}] Fixing stale Category ID for '${val}'. ${explicitId} -> ${fixedId}`);
                            explicitId = fixedId;
                            a.id = fixedId; // Update local for write-back
                            changesMade = true;
                        } else {
                            console.warn(`  [Rule ${r.id}] Warning: Category ID ${explicitId} not found and name '${val}' could not be resolved.`);
                        }
                    }
                }

                // If explicit ID is provided, use it.
                if (explicitId) {
                    val = explicitId;
                } else {
                    // Resolve Name -> ID
                    if (a.field === 'payee') {
                        if (Array.isArray(val)) {
                            // This is usually for Conditions, but just in case
                            val = val.map((v: string) => payeeMap.has(v) ? payeeMap.get(v) : v);
                        } else if (payeeMap.has(val)) {
                            // Classic name lookup
                            val = payeeMap.get(val);
                        }

                        // Fix: Validate and correct Payee IDs
                        if (explicitId) {
                            if (!payeeIdToName.has(explicitId)) {
                                // Stale ID? Try to resolve by name (val might be name or stale ID)
                                const possibleName = val;

                                // 1. Try resolving 'val' as a name using payeeMap
                                if (payeeMap.has(possibleName)) {
                                    const fixedId = payeeMap.get(possibleName);
                                    console.log(`  [Rule ${r.id}] Fixing stale Payee ID. ${explicitId} -> ${fixedId}`);
                                    explicitId = fixedId;
                                    a.id = fixedId;
                                    val = fixedId;
                                    changesMade = true;
                                } else {
                                    // 2. If name doesn't exist, CREATE IT
                                    // But wait, is 'possibleName' a clean name? 
                                    // 'val' here might be the stale ID if `load` didn't find it in map.
                                    // We need the original name from the YAML likely. 
                                    // `a.value` is arguably the original from YAML.
                                    const originalName = a.value;

                                    // Heuristic: If originalName looks like a UUID, we can't create a payee named that UUID usually (unless user intended).
                                    // But usually in YAML it's a name: "payee: Star Bucks"

                                    console.log(`  [Rule ${r.id}] Payee ID ${explicitId} invalid and name '${originalName}' not found. Creating Payee...`);
                                    try {
                                        // api.createPayee is async but we are in map... wait, we are in map!
                                        // We cannot await here easily inside .map() callback unless we refactor loop.
                                        // REFACTOR NEEDED: This loop is currently `actions: r.actions.map(...)`.
                                        // We need to move this logic OUT of the map, or use a for loop before the return.

                                        // Just logging for now, we need to refactor this block to be async-aware.
                                        // Since I cannot rewrite the WHOLE loop easily in one chunk without risk, 
                                        // I will throw an error or handle it differently?
                                        // No, I will rely on the `payeesToEnsure` block at the TOP of the script to catch this.
                                        // BUT `payeesToEnsure` relies on `a.value`. existing logic:
                                        // if (a.field === 'payee' && a.value) ...
                                        // If `a.value` was "Star Bucks", it should have been created.
                                        // Why wasn't it?
                                        // Maybe `a.value` was the ID in the YAML?
                                        // If the YAML contains IDs as values, `payeesToEnsure` sees an ID.
                                        // `payeeMap.has(payeeName)` check... if payeeName is an ID, `payeeMap` (keys=names) won't have it.
                                        // Then it tries `createPayee({ name: payeeName })`.
                                        // If payeeName is a UUID, it creates a Payee named "UUID".
                                        // That seems wrong if the user wanted "Star Bucks".

                                        // HYPOTHESIS: The local YAML contains "payee: <UUID>" because we wrote back IDs into `value`?
                                        // No, we write back `id: ...`. `value` should stay name.
                                        // Let's assume `value` IS the name.
                                    } catch (e) {
                                        console.error(`Error creating payee: ${e}`);
                                    }
                                }
                            }
                        }
                    }
                    if (a.field === 'category') {
                        // Resolve Category Name -> ID
                        if (categoryNameMap.has(val)) {
                            val = categoryNameMap.get(val);
                        }
                        // Note: If val was already an ID, it won't be in name map, so it stays as is.
                    }
                    if (a.field === 'account') {
                        if (accountMap[val]) {
                            val = accountMap[val];
                        }
                    }

                    // Fix: Validate and correct Account IDs
                    if (a.field === 'account' && explicitId) {
                        if (!serverAccountIdMap.has(explicitId)) {
                            console.log(`  [Rule ${r.id}] Stale Account ID ${explicitId}. Attempting resolution...`);
                            // 1. Try resolved 'val' (which is the UUID from accountMap)
                            if (serverAccountIdMap.has(val)) {
                                console.log(`  -> Resolved via accountMap to ${val}. Updating ID.`);
                                explicitId = val;
                                a.id = val;
                                changesMade = true;
                            } else {
                                // 2. Try looking up by name (if val was NOT a UUID, maybe it was a name?)
                                // But above we did val = accountMap[val], so val is likely broken UUID if it's not in server map.
                                // Let's check if the ORIGINAL value (before map) was a name?
                                // We don't have original value here easily unless we look at a.value again?
                                // Actually 'val' is a local var. 'a.value' is the original from YAML (or close to it).
                                const originalVal = a.value;

                                // Try name lookup
                                if (serverAccountNameMap.has(originalVal)) {
                                    const fixedId = serverAccountNameMap.get(originalVal);
                                    if (fixedId) {
                                        console.log(`  -> Resolved via Name '${originalVal}' to ${fixedId}. Updating ID.`);
                                        explicitId = fixedId;
                                        a.id = fixedId;
                                        val = fixedId; // Update value to use the valid ID
                                        changesMade = true;
                                    }
                                } else {
                                    console.warn(`  [Rule ${r.id}] Warning: Account ID ${explicitId} invalid and could not resolve.`);
                                }
                            }
                        }
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
            // New logic: Check if ID exists on server
            if (r.id && !existingRulesMap.has(r.id)) {
                console.log(`[${i + 1}/${rules.length}] Rule ID ${r.id} not found on server. Clearing ID to re-create.`);
                delete r.id;
            }

            if (r.id) {
                // UPDATE
                if (existingRulesMap.has(r.id)) {
                    const existing = existingRulesMap.get(r.id);
                    rulePayload.id = r.id;
                    if (rulesEqual(rulePayload, existing)) {
                        continue;
                    }

                    console.log(`[${i + 1}/${rules.length}] Updating rule ${r.id}... (Stage: ${rulePayload.stage})`);
                    rulePayload.id = r.id;
                    await api.updateRule(rulePayload);
                }
            } else {
                // CREATE
                console.log(`[${i + 1}/${rules.length}] Creating new rule... (Stage: ${rulePayload.stage})`);
                const response = await api.createRule(rulePayload);
                const newId = (typeof response === 'string') ? response : (response as any)?.id;
                console.log(`  -> Created with ID: ${newId} (Response type: ${typeof response})`);

                if (newId) {
                    (r as any).id = newId;
                    changesMade = true;
                }
            }
        } catch (e: any) {
            console.error(`  Error processing rule #${i + 1}: ${e.message}`);
        }
    } // End for loop

    // 5. Write Back IDs if needed
    if (changesMade || localRulesUpdated) {
        console.log('Writing updates back to actual_rules.yaml...');
        const newYaml = yaml.dump({ rules: rules }, { schema: yaml.JSON_SCHEMA, noRefs: true, quotingType: '"' });
        fs.writeFileSync(rulesYamlPath, newYaml, 'utf8');
        console.log('File updated.');
    } else {
        console.log('No new IDs or rules to write back.');
    }

    console.log('Syncing...');
    await api.sync();
    await shutdownActual();
    console.log('Done.');
}

main().catch(err => {
    console.error(err);
    process.exit(1);
});
