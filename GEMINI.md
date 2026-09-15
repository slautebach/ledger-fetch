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
| **BMO** | Capture & Replay | Sniffs the app's own request (device-bound headers), replays it with new date ranges |
| **Amex** | Capture & Replay | `ReadAccountActivity.web.v1` API; harvests request, replays per statement cycle |
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

# Bank API & Auth Field Notes

Hard-won knowledge from fixing all seven fetchers after a 2-month gap (2026-09-15). Read this before debugging any bank failure.

## Universal pattern: capture & replay (don't synthesize auth)

When a bank's internal API starts rejecting our hand-built requests, the durable fix is **not** to reverse-engineer their headers — it's to harvest a live request from their own web app and replay it:

1. Arm Playwright listeners (`page.on("request")` / `page.on("response")`) **before** triggering the navigation — apps fire their data calls at the instant the route activates; arming after you detect the URL change means you miss the call.
2. Let the real UI make the call (click the real card / open the real page).
3. Replay via `page.request` or in-page `fetch` with the harvested headers verbatim, regenerating only per-request IDs (`x-request-id`, `x-fapi-interaction-id`, `x-original-request-time`, `one-data-correlation-id`).
4. Drop the `cookie` header from the template — the browser attaches fresh ones.

This auto-adapts when banks rotate API keys or add device headers. Used by `bmo.py` (`_arm_api_sniffer`) and `amex.py` (`_arm_activity_sniffer`).

## SPA click races (Angular apps: BMO, Amex)

- DOM render ≠ click handlers bound. A click fired the moment `querySelector` finds the element is silently swallowed. **Always verify the navigation happened** (URL contains the target route) and retry the click if not. See `bmo.py::_click_account`.
- Playwright `page.evaluate` is blocked on Amex (their app.js monkeypatches `eval`) — use `page.request` and Playwright locators instead.

## Per-bank notes

### RBC — stable
- No changes after 2 months; saved session survived. API fetch for chequing/savings; CSV fallback for cards/LOC/mortgage/investments.

### BMO — device-bound auth (fixed 2026-09)
- The transient-cache endpoint (`utility/cache/transient-extended-credit-card-data/get`) still works, but Akamai now **rejects synthesized headers with 503** (error page references `errors.edgesuite.net`). The real UI sends `x-bmo-device-fingerprint`, `x-bmo-device-id`, `x-bmo-mfa-device-token`, `x-bmo-user-session-id`. The old hardcoded `x-api-key` is gone from real requests entirely.
- `mfaDeviceToken` also appears in the `customer-access-entitlement/accounts/entitlements` response (valid ~365 days).
- Accounts page load bounces through a CIAM OAuth callback (~20 s); a 401 on `signout/signOut` during startup is normal noise.
- Card details route: `/banking/digital/account-details/cc/<uuid>?tab=overview`; the details-page API cluster (incl. the transient-cache call) fires when the route activates.
- Date ranges must not cross calendar years (still true).

### Amex — new API + session traps (fixed 2026-09)
- `searchTransaction.json` is **retired**. New API: `POST https://functions.americanexpress.com/ReadAccountActivity.web.v1`.
- Body: `{"accountToken": "<stable per-card id, not a session token>", "axplocale": "en-CA", "transactionFilters": {"limit": 100, "offset": 1}, "view": "RECENT" | "BILLED", "cycleIndex": N}` — `cycleIndex` is top-level and only with `view: "BILLED"`.
- Required headers: `ce-source: WEB` and `one-data-correlation-id: CSR-<uuid4>`; auth is browser cookies.
- The RECENT response contains `statementPeriods` (list of `{startDate, endDate, cycleIndex}`) and `member.startDateForSearch` — enumerate cycles from that, then fetch each with `view: "BILLED"`.
- **Session trap 1:** navigating to the legacy `/activity/recent` URL triggers an SSO `DestPage` re-handshake that *invalidates the saved session on every launch*. Always land on `/activity?COUNTRY_CODE=CA&cycleIndex=N`. This single fix ended the login-every-run loop.
- **Session trap 2:** login-page URLs contain the literal string "activity" inside the `DestPage` query param (only slashes get percent-encoded) — any URL match must require `activity` AND require `login` absent. Also, post-login may open a **new tab**: scan `context.pages`, not just `self.page`.
- Amounts are money objects: `{"currency": "CAD", "amount": "29.56"}`. Pending items carry only `displayDate`; posted items have `chargeDate`/`postDate`. Status lives in `status` / `message.id`.
- Sessions are short-lived (minutes); expect roughly one manual login per run. The fallback request constants (accountToken etc.) live in `AmexDownloader` class attributes.

### Wealthsimple — ws-api version drift (fixed 2026-09)
- The `ws-api` pip package evolves its internals: `send_http_request` grew a `return_response` kwarg (v0.35.0) that our monkey-patch must accept. After any `pip install -U ws-api`, diff the patch in `wealthsimple.py::_setup_monkey_patch` against `venv/lib/python3.14/site-packages/ws_api/wealthsimple_api.py`.
- Session hijack from browser cookies + localStorage still works; sessions survive months.

### Canadian Tire — rate limiting
- Aggressive statement-date sweeping (±12 days per guessed date × many months) trips **HTTP 429**. Remedy: wait ~1 h, then re-run targeted: `python main.py --bank canadiantire --since 2026-08` (limits generated dates, dedup handles overlap).

### CIBC / National Bank — stable
- No changes after 2 months; sessions survived.

## Ops notes

- **Fetch windows are automatic** (`auto_window`, on by default): each bank fetches from one month before its newest on-disk data. Force a full window once with `--since YYYY-MM`; set `since_month` in config.yaml only for a permanent override.
- **Failures are loud**: every run prints a RUN SUMMARY table (bank / txns / newest date / status) and exits non-zero when a bank with existing history returns zero transactions or errors. Each run also tees all output to `logs/<ts>_<bank>.log`.
- **`--diagnose`** records all XHR/fetch traffic to `transactions/debug_logs/<bank>_<ts>_traffic.jsonl` (+ HAR) — use this instead of writing ad-hoc traffic-capture scripts when a bank's API breaks.
- **`sync.sh`** gates the Actual Budget import on a clean fetch (`RUN_ACTUAL_SYNC=1 ./sync.sh` to enable the import step).
- **Credentials live at `~/.ledger_fetch/env`** (user scope, outside this Drive-synced folder); both the Python config loader and `actual-sync/utils.ts` read it, falling back to a local `.env`.
- **Parser regression tests**: `./venv/bin/python -m pytest tests/ -v` — fixtures in `tests/fixtures/` were captured from real API responses (2026-09); update them when a bank's schema changes.
- Requirements are pinned (`requirements.txt`); on any `ws-api` bump, re-diff the monkey-patch in `wealthsimple.py` against the installed `ws_api` source.
- **Per-bank profiles + parallel fetching** (2026-09-15): `browser.profile_root` gives each bank its own Chrome user-data-dir at `<root>/<bank>` (seeded from the old shared profile, so existing logins carried over). `python main.py --all --parallel [--jobs N]` fetches all banks concurrently, one subprocess per bank (each writes its own `logs/` file and RUN SUMMARY; the shared `creditcard-statements.csv` is flock-serialized). `sync.sh` uses this mode. Shared-profile mode still works by unsetting `profile_root` and setting `profile_path` (+ `profile_directory`); borrowing the real daily Chrome profile requires daily Chrome fully closed (user-data-dir singleton lock).
- **Password saving is enabled**: Playwright's default `--enable-automation` (which suppresses Chrome's save-password bubble) is dropped via `ignore_default_args`, and `--password-store=basic` keeps the password manager working on Linux without a keyring. Password-manager prefs are force-enabled in each profile at launch (`_ensure_password_prefs`), and automated runs pause a few seconds after login (`linger_after_login_seconds`) so the save bubble can be clicked. **When a bank session expires, prefer `./open_bank.py --bank <bank>`**: it opens that bank's profile as a plain window - log in at leisure, click Save when Chrome offers, verify pre-fill, close. The session and password then carry over to automated fetches. Saved passwords are stored **unencrypted** inside each profile dir (hence `chmod 700`); review via `chrome://settings/passwords` in that bank's profile.
- Run fetchers unattended in background with `PYTHONUNBUFFERED=1` so logs stream to file.
- Manual 2FA happens in the visible browser window; each bank's `login()` prints instructions and waits (5 min default).
- When killing background fetch processes, `pkill -f` patterns match your own monitoring shell's command line — prefer `ps aux | grep "[v]env/bin/python main.py"` to check state.

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

Create a `config.yaml` file in the project root or `~/.ledger_fetch/` (the live config lives at `config/config.yaml`):

```yaml
browser:
  headless: false
  timeout: 30000
  # Real Chrome user data dir + the "Shawn" (Default) profile, so bank logins
  # are shared with daily browsing. Daily Chrome must be fully closed while
  # fetching, or Chrome's singleton lock breaks the launch.
  profile_path: ~/.config/google-chrome
  profile_directory: Default

ledger_fetch:
  transactions_path: ./transactions

  # Enable/disable specific banks
  banks:
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
