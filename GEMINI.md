# Project Overview

The `ledger-fetch` project is a comprehensive personal finance automation suite that combines browser automation for transaction downloading with sophisticated budget synchronization capabilities. It consists of two major components:

1. **Python Transaction Fetcher**: Browser automation using Playwright to download transaction data from Canadian financial institutions
2. **TypeScript Actual Budget Sync**: A suite of tools to import, normalize, and tag transactions in Actual Budget

## Architecture

### Python Component (`ledger_fetch/`)

The Python component uses Playwright to automate browser sessions and download transactions from 7 Canadian financial institutions:

| Bank | Strategy | Implementation |
|------|----------|----------------|
| **RBC** | Internal API | Uses `transaction-presentation-service` endpoints |
| **BMO** | Hybrid (API Injection) | Injects JavaScript to fetch data via internal API |
| **Amex** | Internal API | Fetches JSON from `searchTransaction.json` |
| **CIBC** | Passive Token Capture | Intercepts `x-auth-token` from background requests |
| **National Bank** | GraphQL Interception | Captures session headers to query GraphQL API |
| **Wealthsimple** | Session Hijacking | Uses browser cookies to authorize `ws-api` client |
| **Canadian Tire** | API w/ Extrapolation | Extrapolates statement dates to fetch history |

**Key Files:**
- `base.py`: Abstract `BankDownloader` class defining the common interface
- `utils.py`: Transaction normalization, CSV writing, and ID generation
- `models.py`: Pydantic models for configuration and data structures
- `config.py`: Configuration loading and validation
- Individual bank files: `rbc.py`, `bmo.py`, `amex.py`, `cibc.py`, `national_bank.py`, `wealthsimple.py`, `canadiantire.py`

### TypeScript Component (`actual-sync/`)

The TypeScript component provides a comprehensive suite of tools for managing Actual Budget data:

**Core Scripts:**
- `import-transactions.ts`: Main import script with 3-phase processing (accounts, transactions, reconciliation)
- `tag-transactions.ts`: Rule-based transaction tagging system with AI support
- `sync-rules.ts`: Bi-directional sync of transaction rules
- `sync-accounts.ts`: Account configuration synchronization
- `sync-budget-categories.ts`: Category structure synchronization
- `import-accounts.ts`: Bootstrap accounts from CSV files
- `import_payees.ts`: Pre-populate payees from CSV
- `clean-notes.ts`: Clean and normalize transaction notes
- `ai-transaction-tagging.ts`: AI-powered transaction categorization using Google Gemini

**Utility Files:**
- `utils.ts`: Core utilities for Actual Budget API, configuration loading, and account management
- `tag-utils.ts`: Tag matching, rule processing, and tag configuration management

## Technologies

### Python Stack
- **Python 3**: Core language
- **Playwright**: Browser automation
- **Pandas**: Data manipulation and CSV parsing
- **Pydantic**: Configuration management and validation
- **PyYAML**: Configuration file parsing

### TypeScript Stack
- **Node.js** (v18+): Runtime environment
- **TypeScript**: Type-safe development
- **@actual-app/api**: Official Actual Budget API client
- **@google/generative-ai**: Google Gemini SDK for AI tagging
- **csv-parser**: CSV file parsing
- **js-yaml**: YAML configuration management
- **yargs**: Command-line argument parsing
- **dotenv**: Environment variable management

# Building and Running

## Python Setup

### 1. Virtual Environment

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

```bash
python -m pip install -r requirements.txt
```

### 3. Configuration

Create a `config.yaml` file in the project root or `~/.ledger_fetch/`:

```yaml
browser_profile_path: C:/Users/YourUser/.ledger_fetch_chrome_profile
transactions_path: ./transactions
headless: false
timeout: 30000

# Enable/disable specific banks
rbc:
  enabled: true
wealthsimple:
  enabled: true
amex:
  enabled: true
  accounts:
    - id: "AMEX"
      invert_credit_transactions: true
canadiantire:
  enabled: true
  days_to_fetch: 150
bmo:
  enabled: true
cibc:
  enabled: true
national_bank:
  enabled: true
```

### 4. Running the Fetcher

```bash
# Run for all configured banks
python main.py

# Run for a specific bank
python main.py --bank rbc

# Run in headless mode
python main.py --headless

# Normalize existing files without downloading
python main.py --normalize
```

**Note:** The login process is semi-automated. You'll need to manually complete authentication and 2FA in the browser window. Playwright saves the session for future runs.

## TypeScript Setup

### 1. Install Dependencies

```bash
cd actual-sync
npm install
```

### 2. Configuration

Create a `.env` file in the project root with sensitive credentials:

```env
ACTUAL_SERVER_URL=http://localhost:5006
ACTUAL_PASSWORD=your-password
ACTUAL_SYNC_ID=your-sync-id
```

Create `config/config.yaml` for general configuration:

```yaml
transactions_path: "../../transactions"
server_url: "${ACTUAL_SERVER_URL}"
password: "${ACTUAL_PASSWORD}"
sync_id: "${ACTUAL_SYNC_ID}"
```

### 3. Available Scripts

All scripts support `--config-dir` to specify an alternate configuration directory.

#### Import Transactions (Main Workflow)
```bash
npm run import-transactions
```
Three-phase import process:
1. **Phase 1**: Create missing accounts
2. **Phase 2**: Import transactions with payee normalization and transfer handling
3. **Phase 3**: Reconcile initial balances for new accounts

Options:
- `--bank <name>`: Process only a specific bank
- `--since <YYYY-MM-DD>`: Import transactions on or after this date

#### Tag Transactions
```bash
npm run tag-transactions
```
Apply rule-based tags to transactions using `config/tags.yaml`.

Options:
- `--list-uncategorized`: List all uncategorized on-budget transactions
- `--remove-tag <tag>`: Remove a specific tag from all transactions
- `--config-file <path>`: Specify alternate tags configuration

#### Sync Rules
```bash
npm run sync-rules
```
Bi-directional sync of transaction rules between `config/actual_rules.yaml` and Actual Budget.

#### Sync Accounts
```bash
npm run sync-accounts
```
Synchronize account configurations (names, off-budget status) between `config/accounts.yaml` and Actual Budget.

#### Sync Budget Categories
```bash
npm run sync-budget-categories
```
Synchronize category groups and categories from `config/budget-categories.yaml`.

#### Import Accounts
```bash
npm run import-accounts
```
Bootstrap accounts by scanning downloaded CSV files.

#### Import Payees
```bash
npm run import-payees
```
Pre-populate payees from `payee_counts.csv`.

#### Clean Notes
```bash
npm run clean-notes
```
Clean and normalize transaction notes.

#### AI Transaction Tagging
```bash
npm run ai-transaction-tagging
```
Use Google Gemini to automatically categorize and tag transactions.

# Development Conventions

## Adding a New Bank

To add support for a new financial institution:

1. Create a new Python file in `ledger_fetch/` (e.g., `newbank.py`)
2. Create a class that inherits from `BankDownloader`
3. Implement the required abstract methods:
   - `get_bank_name(self) -> str`: Return unique lowercase bank identifier
   - `login(self)`: Navigate to login page and wait for manual authentication
   - `navigate_to_transactions(self)`: Navigate to transaction download page
   - `download_transactions(self) -> List[Dict[str, Any]]`: Download and parse transactions

4. Use helper classes from `utils.py`:
   - `TransactionNormalizer`: Clean dates, descriptions, and generate unique IDs
   - `CSVWriter`: Save normalized data to CSV

## Transaction Normalization

All transactions are normalized to a consistent CSV format with the following required fields:

- `Unique Transaction ID`: Generated or from bank data
- `Unique Account ID`: Bank-specific account identifier
- `Account Name`: Human-readable account name
- `Date`: ISO format (YYYY-MM-DD)
- `Description`: Transaction description
- `Amount`: Decimal amount
- `Currency`: Currency code (e.g., CAD)
- `Category`: Optional category
- `Payee`: Normalized payee identifier
- `Payee Name`: Human-readable payee name
- `Is Transfer`: Boolean flag
- `Transfer Id`: Optional transfer linking ID
- `Notes`: Additional transaction notes
- `Pending`: Optional pending status

## Output Structure

Transactions are saved to `./transactions/<bank>/<YYYY-MM>.csv`:

```
./transactions/
  rbc/
    2025-10.csv
    2025-11.csv
  bmo/
    2025-10.csv
```

## Account Types

Standardized account types across all banks:
- `Chequing`
- `Savings`
- `Credit Card`
- `Line of Credit`
- `Mortgage`
- `Investment`
- `Loan`
- `Other`

## Negative Balance Enforcement

For liability accounts (Credit Card, Line of Credit, Mortgage, Loan):
- **Account Balance**: Positive balances (amount owed) → negative
- **Transactions**:
  - Purchases (Debits) → negative values
  - Payments (Credits) → positive values

Control per-bank enforcement with `invert_credit_transactions` in `config.yaml`.

## Configuration Files

The `config/` directory contains YAML configuration files:

- `config.yaml`: Main configuration (server URL, paths, sync settings)
- `accounts.yaml`: Account mappings and settings (off-budget status, display names)
- `tags.yaml`: Transaction tagging rules
- `actual_rules.yaml`: Actual Budget transaction rules
- `budget-categories.yaml`: Category structure
- `payee_rules/`: Directory containing payee normalization rules

## Tag Configuration

Tags are defined in `config/tags.yaml` with rule-based matching:

```yaml
tags:
  - tag: "groceries"
    rules:
      - payee_any: ["Loblaws", "Metro", "Sobeys"]
        category_any: ["Food"]
  - tag: "recurring"
    rules:
      - notes_any: ["PREAUTH", "RECURRING"]
```

Matching criteria:
- `payee_any`: Match any payee in list
- `account_any`: Match any account in list
- `category_any`: Match any category in list
- `notes_any`: Match any substring in notes

# Reference

## ActualBudget API
- [API Reference](https://actualbudget.org/docs/api/reference)
- [ActualQL](https://actualbudget.org/docs/api/actual-ql/)

## Key Workflows

### Full Import Workflow
1. Run Python fetcher: `python main.py`
2. Import transactions: `cd actual-sync && npm run import-transactions`
3. Apply tags: `npm run tag-transactions`
4. Review uncategorized: `npm run tag-transactions -- --list-uncategorized`

### Testing Against Test Budget
```bash
# Use alternate config directory
npm run import-transactions -- --config-dir "./config-test"
```

### Debugging Specific Bank
```bash
# Python: Fetch only RBC
python main.py --bank rbc

# TypeScript: Import only RBC transactions
npm run import-transactions -- --bank rbc
```
