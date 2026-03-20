python "$PSScriptRoot\main.py" --all
python "$PSScriptRoot\main.py" --normalize

Set-Location "$PSScriptRoot\actual-sync"
npm run import-transactions -- --since 2025-02-01
npm run tag-transactions -- --commit

Set-Location "$PSScriptRoot"