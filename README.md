# ledger-fetch

A browser automation suite for personal finance. Uses Python and Playwright to simulate user sessions, manage 2FA logins, and scrape banking transactions for local analysis.

## Supported Banks

The `ledger-fetch` tool currently supports the following financial institutions:

| Bank | Strategy | Notes |
|------|----------|-------|
| **RBC** | Internal API | Uses `transaction-presentation-service` endpoints. |
| **BMO** | Hybrid (API Injection) | Injects JS to fetch data via internal API using browser session. |
| **Amex** | Internal API | Fetches JSON from `searchTransaction.json`. |
| **CIBC** | Passive Token Capture | Intercepts `x-auth-token` from background requests. |
| **National Bank** | GraphQL Interception | Captures session headers to query GraphQL API. |
| **Wealthsimple** | Session Hijacking | Uses browser cookies to authorize `ws-api` client. |
| **Canadian Tire** | API w/ Extrapolation | Extrapolates statement dates to fetch history. |

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
python -m pip install -r requirements.txt
```

### 3. Configuration

The application can be configured via a `config.yaml` file. The script will search for this file in the following locations:

1. The current directory (`.`)
2. The user's home directory (`~/.ledger_fetch/`)

A sample `config.yaml` has been provided in the project root. Key configurable settings include:

* `browser_profile_path`: Path to the Chrome user profile directory for persistent logins.
* `output_dir`: Directory where downloaded transactions will be saved.
* `headless`: Run browser in headless mode (without a visible UI).
* `timeout`: Default timeout for browser actions in milliseconds.
* Bank-specific `enabled` flags to enable/disable specific downloaders.
* `actual`: Configuration for Actual Budget sync (see below).

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

## Payee Normalization

`ledger-fetch` includes a tool to normalize payee names in your downloaded CSVs using a set of rules defined in `payee_rules.yaml`. This is useful for cleaning up messy bank descriptions before importing them into your budgeting tool.

To run normalization on existing files without downloading new transactions:

```bash
python main.py --normalize
```

## Actual Budget Sync

This project includes a TypeScript-based tool to sync your downloaded transactions and accounts directly to [Actual Budget](https://actualbudget.com/).

### Prerequisites

* Node.js (v18 or later)
* npm

### Setup

1. Navigate to the `actual-sync` directory:

    ```bash
    cd actual-sync
    ```

2. Install dependencies:

    ```bash
    npm install
    ```

### Configuration

The sync scripts look for configuration files in a `config` directory under the `actual-sync` directory.

You should create a `config` directory containing the necessary configuration files. The main file is `config.yaml`.

**Example `config/config.yaml`:**

```yaml
# Directory where downloaded transactions will be saved.
# Relative paths are resolved against the config file location.
transactions_path: "../../transactions"

server_url: "http://localhost:5006" # URL of your Actual Budget server
password: "your-actual-password"
sync_id: "your-sync-id" # The ID of the budget file to sync with
```

### Usage

To sync your downloaded transactions to Actual Budget:

```bash
cd actual-sync
npm start
```

#### Testing / Custom Configuration

We have removed the `--dry-run` flag. To test against a different budget or configuration (e.g., a test budget), use the `--config-dir` argument to point to a different configuration folder.

```bash
# Run with a specific testing configuration directory
npm start -- --config-dir "./config-test"
```

## Output Structure

Upon successful execution, `ledger-fetch` will create a directory for each bank within the `output_dir` (defaulting to `./transactions`). Inside each bank's directory, transaction data will be saved into separate CSV files, organized by month.

For example, transactions from RBC for October 2025 would be saved to:
`./transactions/rbc/2025-10.csv`

Each CSV file contains normalized transaction data, including:

* `Unique Transaction ID`
* `Unique Account ID`
* `Date` (in `YYYY-MM-DD` format)
* `Description`
* `Amount`
* `Currency`
* `Category`
* `Payee` (Normalized)
* `Payee Name` (Normalized)
* `Is Transfer`
* `Notes`
And potentially other bank-specific fields that the bank provides in their transaction exports.

## Account Types

`ledger-fetch` standardizes account types across all banks to the following values:

* `Chequing`
* `Savings`
* `Credit Card`
* `Line of Credit`
* `Mortgage`
* `Investment`
* `Loan`
* `Other`

## Negative Balance Enforcement

For liability accounts (Credit Card, Line of Credit, Mortgage, Loan), the tool enforces the following conventions:

* **Account Balance**: Positive balances (amount owed) are converted to negative.
* **Transactions**:
  * Purchases (Debits) are converted to negative values.
  * Payments (Credits) are converted to positive values.

### Configuration

You can control the transaction sign enforcement per bank in `config.yaml` using the `invert_credit_transactions` flag. This is useful if a bank already provides negative values for purchases.

```yaml
rbc:
  enabled: true
  invert_credit_transactions: true # Enforce negative signs for this bank
```
