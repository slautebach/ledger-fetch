import * as api from '@actual-app/api';
import * as fs from 'fs';
import * as path from 'path';
import * as yaml from 'js-yaml';

// Reuse config loading from index.ts (simplified)
const configPath = path.resolve('./config.yaml');
const config = yaml.load(fs.readFileSync(configPath, 'utf8')) as any;

async function main() {
    console.log('Connecting...');
    await api.init({
        dataDir: path.resolve(__dirname, 'data'),
        serverURL: config.actual.server_url,
        password: config.actual.password,
    });
    await api.downloadBudget(config.actual.sync_id);

    console.log('Fetching rules...');
    const rules = await api.getRules();
    console.log(JSON.stringify(rules[0], null, 2)); // Print first rule

    await api.shutdown();
}

main();
