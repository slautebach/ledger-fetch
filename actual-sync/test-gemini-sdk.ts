import { loadConfig, callGemini } from './utils';
import * as path from 'path';

async function test() {
    try {
        const configPath = path.resolve(__dirname, '../config/config.yaml');
        console.log(`Loading config from ${configPath}`);
        const config = loadConfig(configPath);

        if (!config.ai || !config.ai.api_key) {
            console.warn("Skipping API test: No gemini_api_key found in config.");
            return;
        }

        console.log("Testing callGemini...");
        const response = await callGemini("Say hello!", JSON.stringify({ name: "World" }), config);
        console.log("Gemini Response:", response);
    } catch (e: any) {
        console.error("Test failed:", e.message);
    }
}

test();
