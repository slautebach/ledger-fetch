const yaml = require('js-yaml');

const yamlStr = `
groups:
  - name: ExistingGroup
    categories: []
  - name: GroupNoCategories
`;

const data = yaml.load(yamlStr);

// Simulation of the logic in pullMissingCategories
const groups = data.groups;

// 1. Existing Group with categories
const g1 = groups.find(g => g.name === 'ExistingGroup');
if (g1) {
    g1.categories.push({ name: 'NewCategory1' });
}

// 2. New Group
const newGroup = { name: 'NewGroup', categories: [] };
groups.push(newGroup);
// Modify via reference
const g2 = groups.find(g => g.name === 'NewGroup');
if (g2) {
    g2.categories.push({ name: 'NewCategory2' });
}

// 3. Group with NO categories property
const g3 = groups.find(g => g.name === 'GroupNoCategories');
console.log('GroupNoCategories:', g3);
try {
    // This is what the current code does:
    const found = g3.categories.find(c => c.name === 'Something');
} catch (e) {
    console.log('Error accessing categories on GroupNoCategories:', e.message);
}

// Fix simulation: ensure categories exists
if (!g3.categories) {
    g3.categories = [];
}
g3.categories.push({ name: 'NewCategory3' });

console.log('--- Result ---');
console.log(yaml.dump(data, { lineWidth: -1, noRefs: true }));
