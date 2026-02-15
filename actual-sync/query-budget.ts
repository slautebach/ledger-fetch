import * as api from '@actual-app/api';
import * as yargs from 'yargs';
import { initActual, shutdownActual, loadConfig } from './utils';

async function run() {
    const argv = await yargs
        .option('table', {
            alias: 't',
            description: 'The table to query',
            type: 'string',
            default: 'transactions'
        })
        .option('filter', {
            alias: 'f',
            description: 'Filter criteria (e.g. imported_id=123 or JSON string)',
            type: 'string'
        })
        .option('select', {
            alias: 's',
            description: 'Fields to select',
            type: 'string',
            default: '*'
        })
        .option('limit', {
            alias: 'l',
            description: 'Limit number of results',
            type: 'number'
        })
        .option('config', {
            alias: 'c',
            description: 'Path to config file',
            type: 'string',
            default: 'config/config.yaml'
        })
        .help()
        .alias('help', 'h')
        .argv;

    const config = loadConfig(argv.config);

    try {
        await initActual(config);



        let query = api.q(argv.table);

        if (argv.filter) {
            let filterString = argv.filter;
            let filterObj: any = {};

            // Try parsing as JSON first
            try {
                filterObj = JSON.parse(filterString);
            } catch (e) {
                // If not JSON, assume key=value format
                // This is a simple implementation, assumes single filter or manual JSON for complex ones
                if (filterString.includes('=')) {
                    const [key, value] = filterString.split('=');
                    filterObj[key.trim()] = value.trim();
                } else {
                    console.error("Invalid filter format. Use 'key=value' or JSON string.");
                    await shutdownActual();
                    process.exit(1);
                }
            }
            console.log(`Applying filter:`, filterObj);
            query = query.filter(filterObj);
        }

        const selectFields = argv.select.split(',').map(s => s.trim());
        query = query.select(selectFields);

        if (argv.limit) {
            query = query.limit(argv.limit);
        }

        console.log(`Running query on table '${argv.table}'...`);
        const result = await (api as any).aqlQuery(query);

        // console.log('Raw result:', result);
        console.log(JSON.stringify(result && result.data ? result.data : result, null, 2));

    } catch (error) {
        console.error('Error running query:', error);
    } finally {
        await shutdownActual();
    }
}

run();
