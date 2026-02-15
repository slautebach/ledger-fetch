/**
 * Category Importer
 * 
 * This module defines the `CategoryImporter` class, which is responsible for synchronizing
 * categories from a local YAML definition file into the Actual Budget.
 * 
 * Features:
 * - Reads a YAML file defining Category Groups and Categories.
 * - Creates Category Groups if they don't exist.
 * - Creates Categories within those groups if they don't exist.
 * - Performs case-insensitive matching to avoid duplicates.
 * - Can optionally check for categories that exist on the server but are missing from the local YAML.
 *
 * Usage:
 * Instantiated and used by `sync-budget-categories.ts`.
 */
import * as api from '@actual-app/api';
import * as fs from 'fs';
import * as path from 'path';
import * as yaml from 'js-yaml';

export interface Category {
    name: string;
    hidden?: boolean;
}

export interface CategoryGroup {
    name: string;
    is_income?: boolean;
    hidden?: boolean;
    categories: Category[];
}

export interface CategoriesYaml {
    groups: CategoryGroup[];
}

export class CategoryImporter {
    private categoriesYaml!: CategoriesYaml;
    private yamlPath: string;

    constructor(yamlPath: string) {
        this.yamlPath = yamlPath;
        if (fs.existsSync(yamlPath)) {
            const fileContents = fs.readFileSync(yamlPath, 'utf8');
            this.categoriesYaml = yaml.load(fileContents) as CategoriesYaml;
        } else {
            console.log(`Categories file not found at ${yamlPath}. Starting with empty list.`);
        }

        if (!this.categoriesYaml) {
            this.categoriesYaml = { groups: [] };
        }
        if (!this.categoriesYaml.groups) {
            this.categoriesYaml.groups = [];
        }
    }

    async import() {
        console.log(`Starting Category Import...`);

        // 1. Fetch Existing Groups and Categories
        // We need to know what exists to avoid creating duplicates.
        // The Actual API returns groups with their nested categories.
        const existingGroups = await api.getCategoryGroups();
        console.log(`Found ${existingGroups.length} existing category groups.`);

        // 2. Sync Groups
        // Iterate through each group in the YAML file and ensure it exists in Actual.
        for (const yamlGroup of this.categoriesYaml.groups) {
            let group = existingGroups.find((g: any) => g.name === yamlGroup.name);

            // Define default structure for new or existing groups to avoid TS errors
            if (group && !group.categories) {
                group.categories = [];
            }

            if (!group) {
                console.log(`Group "${yamlGroup.name}" does not exist. Creating...`);
                try {
                    const newGroupId = await api.createCategoryGroup({
                        name: yamlGroup.name,
                        is_income: yamlGroup.is_income || false,
                        hidden: yamlGroup.hidden || false
                    });
                    // Construct a local representation of the new group
                    group = { id: newGroupId, name: yamlGroup.name, categories: [] };
                } catch (e: any) {
                    console.error(`  Error creating group "${yamlGroup.name}": ${e.message}`);
                    continue;
                }
            } else {
                console.log(`Group "${yamlGroup.name}" exists.`);
                // Update hidden status if changed
                if (group.hidden !== (yamlGroup.hidden || false)) {
                    console.log(`  Updating hidden status for group "${yamlGroup.name}" to ${yamlGroup.hidden || false}`);
                    try {
                        await api.updateCategoryGroup(group.id, { hidden: yamlGroup.hidden || false });
                    } catch (e: any) {
                        console.error(`  Error updating group "${yamlGroup.name}": ${e.message}`);
                    }
                }
            }

            // 3. Sync Categories
            // Once the group is confirmed/created, ensure all its categories exist.
            if (yamlGroup.categories && group) {
                for (const yamlCategory of yamlGroup.categories) {
                    // Case-insensitive check
                    const category = group.categories?.find((c: any) => c.name.toLowerCase().trim() === yamlCategory.name.toLowerCase().trim());

                    if (!category) {
                        console.log(`  Category "${yamlCategory.name}" does not exist in group "${yamlGroup.name}". Creating...`);
                        try {
                            // Ensure we have a real ID before trying to create a category
                            if (group.id) {
                                await api.createCategory({
                                    name: yamlCategory.name,
                                    group_id: group.id,
                                    hidden: yamlCategory.hidden || false
                                });
                            } else {
                                console.log(`    Skipping creation because group ID is missing.`);
                            }
                        } catch (e: any) {
                            console.error(`    Error creating category "${yamlCategory.name}": ${e.message}`);
                        }
                    } else {
                        // console.log(`  Category "${yamlCategory.name}" already exists.`);
                        // Update hidden status if changed
                        if (category.hidden !== (yamlCategory.hidden || false)) {
                            console.log(`  Updating hidden status for category "${yamlCategory.name}" to ${yamlCategory.hidden || false}`);
                            try {
                                await api.updateCategory(category.id, { hidden: yamlCategory.hidden || false });
                            } catch (e: any) {
                                console.error(`  Error updating category "${yamlCategory.name}": ${e.message}`);
                            }
                        }
                    }
                }
            }
        }

        console.log('Syncing changes...');
        await api.sync();
        console.log('Category import complete.');
    }

    async pullMissingCategories() {
        console.log('Checking for missing categories on server...');
        const existingGroups = await api.getCategoryGroups();
        let changesMade = false;

        // Ensure we have a valid object to work with
        if (!this.categoriesYaml) {
            this.categoriesYaml = { groups: [] };
        }
        if (!this.categoriesYaml.groups) {
            this.categoriesYaml.groups = [];
        }

        // Create a copy of the current YAML structure to modify, or just modify in place?
        // Modifying in place is easier if we want import() to see changes immediately.
        // Let's modify in place.
        const yamlData = this.categoriesYaml;

        for (const serverGroup of existingGroups) {
            let localGroup = yamlData.groups.find(g => g.name === serverGroup.name);

            if (!localGroup) {
                console.log(`  Found new group on server: "${serverGroup.name}". Adding to local YAML.`);
                localGroup = {
                    name: serverGroup.name,
                    is_income: serverGroup.is_income || false,
                    hidden: serverGroup.hidden || false,
                    categories: []
                };
                yamlData.groups.push(localGroup);
                changesMade = true;
            }

            // Check categories within the group
            if (serverGroup.categories) {
                for (const serverCat of serverGroup.categories) {
                    const localCat = localGroup.categories.find(c => c.name === serverCat.name);
                    if (!localCat) {
                        console.log(`  Found new category on server: "${serverCat.name}" (in group "${serverGroup.name}"). Adding to local YAML.`);
                        localGroup.categories.push({
                            name: serverCat.name,
                            hidden: serverCat.hidden || false
                        });
                        changesMade = true;
                    } else {
                        // Update local YAML if hidden status differs on server
                        if ((localCat.hidden || false) !== (serverCat.hidden || false)) {
                            console.log(`  Start hidden status mismatch for category "${serverCat.name}". Local: ${localCat.hidden}, Server: ${serverCat.hidden}. Updating local YAML.`);
                            localCat.hidden = serverCat.hidden || false;
                            changesMade = true;
                        }
                    }
                }
            }
        }

        if (changesMade) {
            console.log('Writing updated categories to budget-categories.yaml...');
            // Convert back to YAML
            const yamlString = yaml.dump(yamlData, { lineWidth: -1, noRefs: true });
            fs.writeFileSync(this.yamlPath, yamlString, 'utf8');
        } else {
            console.log('No missing categories found on server.');
        }
    }
}
