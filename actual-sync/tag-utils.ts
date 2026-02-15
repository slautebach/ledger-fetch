
import * as fs from 'fs';
import * as yaml from 'js-yaml';

// --- Interfaces ---

export interface TagConfig {
    rules: TagRule[];
}

export interface TagRule {
    name: string;
    match: MatchCriteria;
    tags: string[];
}

export interface MatchCriteria {
    payee_name?: string; // Regex
    notes?: string;      // Regex
    account_name?: string; // Regex
    amount_min?: number;
    amount_max?: number;
    payee_any?: string[]; // List of Regex strings
    account_any?: string[]; // List of Regex strings
    category_name?: string; // Regex
    category_any?: string[]; // List of Regex strings
    notes_any?: string[]; // List of Regex strings
}

// --- Logic ---

export function loadTagConfig(filePath: string): TagConfig {
    if (!fs.existsSync(filePath)) {
        throw new Error(`Tag configuration file not found: ${filePath}`);
    }
    const fileContents = fs.readFileSync(filePath, 'utf8');
    return yaml.load(fileContents) as TagConfig;
}

export function escapeRegex(string: string): string {
    return string.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

export function matchesRule(tx: any, rule: TagRule, payeeName: string, accountName: string, categoryName: string): boolean {
    const criteria = rule.match;
    let isMatch = true;

    // 1. Payee Name (Regex)
    if (criteria.payee_name) {
        if (!payeeName) {
            isMatch = false;
        } else {
            const regex = new RegExp(criteria.payee_name, 'i');
            if (!regex.test(payeeName)) {
                isMatch = false;
            }
        }
    }

    // 2. Notes (Regex)
    if (isMatch && criteria.notes) {
        const notes = tx.notes || '';
        const regex = new RegExp(criteria.notes, 'i');
        if (!regex.test(notes)) {
            isMatch = false;
        }
    }

    // 3. Account Name (Regex)
    if (isMatch && criteria.account_name) {
        const regex = new RegExp(criteria.account_name, 'i');
        if (!regex.test(accountName)) {
            isMatch = false;
        }
    }

    // 4. Amount Min
    if (isMatch && criteria.amount_min !== undefined) {
        if (tx.amount < criteria.amount_min) {
            isMatch = false;
        }
    }

    // 5. Amount Max
    if (isMatch && criteria.amount_max !== undefined) {
        if (tx.amount > criteria.amount_max) {
            isMatch = false;
        }
    }

    // 6. Payee Any (List of Regex)
    if (isMatch && criteria.payee_any && criteria.payee_any.length > 0) {
        if (!payeeName) {
            isMatch = false;
        } else {
            const hasAnyMatch = criteria.payee_any.some(pattern => {
                try {
                    return new RegExp(pattern, 'i').test(payeeName);
                } catch (e) {
                    console.warn(`Invalid regex in payee_any: ${pattern}`);
                    return false;
                }
            });
            if (!hasAnyMatch) {
                isMatch = false;
            }
        }
    }

    // 7. Account Any (List of Regex)
    if (isMatch && criteria.account_any && criteria.account_any.length > 0) {
        const hasAnyMatch = criteria.account_any.some(pattern => {
            try {
                return new RegExp(pattern, 'i').test(accountName);
            } catch (e) {
                console.warn(`Invalid regex in account_any: ${pattern}`);
                return false;
            }
        });
        if (!hasAnyMatch) {
            isMatch = false;
        }
    }

    // 8. Category Name (Regex)
    if (isMatch && criteria.category_name) {
        if (!categoryName) {
            isMatch = false;
        } else {
            const regex = new RegExp(criteria.category_name, 'i');
            if (!regex.test(categoryName)) {
                isMatch = false;
            }
        }
    }

    // 9. Category Any (List of Regex)
    if (isMatch && criteria.category_any && criteria.category_any.length > 0) {
        if (!categoryName) {
            isMatch = false;
        } else {
            const hasAnyMatch = criteria.category_any.some(pattern => {
                try {
                    return new RegExp(pattern, 'i').test(categoryName);
                } catch (e) {
                    console.warn(`Invalid regex in category_any: ${pattern}`);
                    return false;
                }
            });
            if (!hasAnyMatch) {
                isMatch = false;
            }
        }
    }

    // 10. Notes Any (List of Regex)
    if (isMatch && criteria.notes_any && criteria.notes_any.length > 0) {
        const notes = tx.notes || '';
        const hasAnyMatch = criteria.notes_any.some(pattern => {
            try {
                return new RegExp(pattern, 'i').test(notes);
            } catch (e) {
                console.warn(`Invalid regex in notes_any: ${pattern}`);
                return false;
            }
        });
        if (!hasAnyMatch) {
            isMatch = false;
        }
    }

    return isMatch;
}

/**
 * Cleans a note string by extracting hashtags, removing duplicates, sorting them,
 * and moving them to the end of the note.
 * @param note The original note string.
 * @returns The cleaned note string.
 */
export function cleanAndSortTags(note: string | null | undefined): string {
    if (!note) return '';

    const tagRegex = /#[\w-]+/g;
    const tags = note.match(tagRegex) || [];

    // Remove tags from body, replace with space to prevent word merging
    let body = note.replace(tagRegex, ' ');

    // Clean up whitespace
    body = body.replace(/\s+/g, ' ').trim();

    // Deduplicate and sort tags
    const uniqueTags = Array.from(new Set(tags.map(t => t.toLowerCase()))).sort();

    if (uniqueTags.length > 0) {
        const tagString = uniqueTags.join(' ');
        return body ? `${body} ${tagString}` : tagString;
    }

    return body;
}

/**
 * Adds multiple tags to the notes, then cleans and sorts all tags.
 * @param note The existing note string.
 * @param tags The tags to add (strings, with or without #).
 * @returns The updated note string.
 */
export function addTags(note: string | null | undefined, tags: string[]): string {
    const currentNote = note || '';
    const tagsWithHash = tags.map(tag => tag.startsWith('#') ? tag : `#${tag}`).join(' ');

    // Append all new tags then clean/dedupe/sort
    return cleanAndSortTags(`${currentNote} ${tagsWithHash}`);
}

/**
 * Sorts the tagging configuration.
 * Rules are sorted by name (case-insensitive).
 * Match arrays and tags within each rule are sorted alphabetically (case-insensitive).
 */
export function sortTagConfig(config: TagConfig): TagConfig {
    // Sort rules by name
    config.rules.sort((a, b) => a.name.toLowerCase().localeCompare(b.name.toLowerCase()));

    for (const rule of config.rules) {
        // Sort tags
        if (rule.tags) {
            rule.tags.sort((a, b) => a.toLowerCase().localeCompare(b.toLowerCase()));
        }

        // Sort match arrays
        const match = rule.match;
        if (match.payee_any) {
            match.payee_any = Array.from(new Set(match.payee_any));
            match.payee_any.sort((a, b) => a.toLowerCase().localeCompare(b.toLowerCase()));
        }
        if (match.notes_any) {
            match.notes_any = Array.from(new Set(match.notes_any));
            match.notes_any.sort((a, b) => a.toLowerCase().localeCompare(b.toLowerCase()));
        }
        if (match.account_any) {
            match.account_any = Array.from(new Set(match.account_any));
            match.account_any.sort((a, b) => a.toLowerCase().localeCompare(b.toLowerCase()));
        }
        if (match.category_any) {
            match.category_any = Array.from(new Set(match.category_any));
            match.category_any.sort((a, b) => a.toLowerCase().localeCompare(b.toLowerCase()));
        }
    }
    return config;
}

/**
 * Saves the tag configuration to a YAML file.
 */
export function saveTagConfig(filePath: string, config: TagConfig): void {
    const yamlStr = yaml.dump(config, { indent: 2, lineWidth: -1, noRefs: true });
    fs.writeFileSync(filePath, yamlStr, 'utf8');
}
