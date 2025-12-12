/**
 * Category Importer Class
 *
 * This class encapsulates the logic for importing categories from a YAML definition.
 * It handles the hierarchical structure of Category Groups -> Categories.
 */
import * as api from '@actual-app/api';
import * as fs from 'fs';
import * as path from 'path';
import * as yaml from 'js-yaml';

export interface Category {
    name: string;
}

export interface CategoryGroup {
    name: string;
    is_income?: boolean;
    categories: Category[];
}

export interface CategoriesYaml {
    groups: CategoryGroup[];
}

export class CategoryImporter {
    private categoriesYaml!: CategoriesYaml;

    constructor(yamlPath: string) {
        if (!fs.existsSync(yamlPath)) {
            throw new Error(`Categories file not found at ${yamlPath}`);
        }
        const fileContents = fs.readFileSync(yamlPath, 'utf8');
        this.categoriesYaml = yaml.load(fileContents) as CategoriesYaml;
    }

    async import(dryRun: boolean = false) {
        console.log(`Starting Category Import (Dry Run: ${dryRun})...`);

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
                if (!dryRun) {
                    try {
                        const newGroupId = await api.createCategoryGroup({
                            name: yamlGroup.name,
                            is_income: yamlGroup.is_income || false
                        });
                        // Construct a local representation of the new group
                        group = { id: newGroupId, name: yamlGroup.name, categories: [] };
                    } catch (e: any) {
                        console.error(`  Error creating group "${yamlGroup.name}": ${e.message}`);
                        continue;
                    }
                } else {
                    console.log(`  [Dry Run] Would create group "${yamlGroup.name}"`);
                    // Mock group for dry-run to proceed with category checks
                    group = { id: 'dry-run-group-' + yamlGroup.name, name: yamlGroup.name, categories: [] };
                }
            } else {
                console.log(`Group "${yamlGroup.name}" exists.`);
            }

            // 3. Sync Categories
            // Once the group is confirmed/created, ensure all its categories exist.
            if (yamlGroup.categories && group) {
                for (const yamlCategory of yamlGroup.categories) {
                    // Case-insensitive check
                    const category = group.categories.find((c: any) => c.name.toLowerCase().trim() === yamlCategory.name.toLowerCase().trim());

                    if (!category) {
                        console.log(`  Category "${yamlCategory.name}" does not exist in group "${yamlGroup.name}". Creating...`);
                        if (!dryRun) {
                            try {
                                // Ensure we have a real ID before trying to create a category
                                if (group.id && !group.id.startsWith('dry-run')) {
                                    await api.createCategory({
                                        name: yamlCategory.name,
                                        group_id: group.id
                                    });
                                } else {
                                    console.log(`    Skipping creation because group ID is temporary.`);
                                }
                            } catch (e: any) {
                                console.error(`    Error creating category "${yamlCategory.name}": ${e.message}`);
                            }
                        } else {
                            console.log(`    [Dry Run] Would create category "${yamlCategory.name}" in group "${yamlGroup.name}"`);
                        }
                    } else {
                        // console.log(`  Category "${yamlCategory.name}" already exists.`);
                    }
                }
            }
        }


        if (!dryRun) {
            console.log('Syncing changes...');
            await api.sync();
        }
        console.log('Category import complete.');
    }
}
