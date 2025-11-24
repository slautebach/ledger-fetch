# ledger-fetch
A browser automation suite for personal finance. Uses Python and Playwright to simulate user sessions, manage 2FA logins, and scrape banking transactions for local analysis.

## Supported Banks

The `ledger-fetch` tool currently supports the following financial institutions:

*   RBC (Royal Bank of Canada)
*   Wealthsimple
*   Amex (American Express)
*   Canadian Tire Bank

### 1. Setup

It is recommended to use a virtual environment.

```bash
# Create a virtual environment
python -m venv .venv

# Activate the virtual environment
# On Windows
.venv\Scripts\activate
# On macOS/Linux
source .venv/bin/activate
```

### 2. Install Dependencies

Install the required Python packages from `requirements.txt`.

```bash
pip install -r requirements.txt
```

### 3. Configuration

The application can be configured via a `config.yaml` file. The script will search for this file in the following locations:
1. The current directory (`.`)
2. The user's home directory (`~/.ledger_fetch/`)

A sample `config.yaml` has been provided in the project root. Key configurable settings include:

*   `browser_profile_path`: Path to the Chrome user profile directory for persistent logins.
*   `output_dir`: Directory where downloaded transactions will be saved.
*   `headless`: Run browser in headless mode (without a visible UI).
*   `timeout`: Default timeout for browser actions in milliseconds.
*   Bank-specific `enabled` flags to enable/disable specific downloaders.

### 4. Running the Application

Run the main script using `main.py`. You can specify a single bank to fetch or run all of them.

```bash
# Run for all configured banks
python main.py

# Run for a specific bank (e.g., rbc)
python main.py --bank rbc

# Run in headless mode (overrides config)
python main.py --headless
```

**Important:** The login process for each bank is semi-automated. The script will launch a browser for you where you can log in to your bank account. Once you have logged in and completed any two-factor authentication, the script will then automatically navigate to the correct page and download all available transactions for you. Playwright will then save the session for future runs, so you won't have to log in every time.

## Output Structure

Upon successful execution, `ledger-fetch` will create a directory for each bank within the `output_dir` (defaulting to `./transactions`). Inside each bank's directory, transaction data will be saved into separate CSV files, organized by month.

For example, transactions from RBC for October 2025 would be saved to:
`./transactions/rbc/2025-10.csv`

Each CSV file contains normalized transaction data, including:
*   `Unique Transaction ID`
*   `Unique Account ID`
*   `Date` (in `YYYY-MM-DD` format)
*   `Description`
*   `Amount`
*   `Currency`
*   `Category`
And potentially other bank-specific fields that the bank provides in their transaction exports.


