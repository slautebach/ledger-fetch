

python "$PSScriptRoot\main.py" --all
python "$PSScriptRoot\main.py" --normalize
python "$PSScriptRoot\link_transfers.py"
python "$PSScriptRoot\process_payees.py"