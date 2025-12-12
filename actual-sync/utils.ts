/**
 * Shared Utilities for Actual Sync Scripts
 */
import * as api from '@actual-app/api';
import * as fs from 'fs';
import * as path from 'path';
import * as yaml from 'js-yaml';

export interface Config {
    actual: {
        server_url: string;
        password: string;
        sync_id: string;
    };
    output_dir?: string;
    transactions_path?: string;
}

export function loadConfig(configPath: string): Config {
    const resolvedPath = path.resolve(configPath);
    if (!fs.existsSync(resolvedPath)) {
        throw new Error(`Config file not found at ${resolvedPath}`);
    }
    try {
        const fileContents = fs.readFileSync(resolvedPath, 'utf8');
        const loaded: any = yaml.load(fileContents);

        let config: Config;

        // Normalize config: If flat, wrap in 'actual' object
        if (loaded.server_url && loaded.password && loaded.sync_id) {
            config = {
                actual: {
                    server_url: loaded.server_url,
                    password: loaded.password,
                    sync_id: loaded.sync_id
                },
                output_dir: loaded.output_dir,
                transactions_path: loaded.transactions_path
            };
        } else {
            config = loaded as Config;
        }

        // Logic to resolve transactions_path (or output_dir fallback) relative to config file
        const rawPath = config.transactions_path || config.output_dir;
        if (rawPath) {
            const configDir = path.dirname(resolvedPath);
            config.transactions_path = path.resolve(configDir, rawPath);
        }

        return config;
    } catch (e: any) {
        throw new Error(`Error parsing config file: ${e.message}`);
    }
}

export async function initActual(config: Config) {
    if (!config.actual || !config.actual.server_url || !config.actual.password || !config.actual.sync_id) {
        throw new Error('Missing "actual" configuration in config.yaml');
    }

    console.log(`Connecting to Actual Budget at ${config.actual.server_url.trim()}...`);
    const dataDir = path.resolve(__dirname, 'data');
    if (!fs.existsSync(dataDir)) {
        fs.mkdirSync(dataDir, { recursive: true });
    }

    await api.init({
        dataDir: dataDir,
        serverURL: config.actual.server_url.trim(),
        password: config.actual.password.trim(),
    });
    await api.downloadBudget(config.actual.sync_id.trim());
    console.log('Connected to budget.');
}

export async function shutdownActual() {
    await api.shutdown();
}
