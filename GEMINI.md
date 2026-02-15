# Project Overview

This project, `ledger-fetch`, is a Python-based browser automation suite designed to download financial transaction data from various Canadian financial institutions. It uses Playwright to automate browser sessions, handle logins (including 2FA, which requires manual user intervention), navigate to transaction pages, and download transaction files.

The downloaded data is then parsed, normalized, and saved into monthly CSV files using `pandas`. The project is structured with an abstract base class (`BankDownloader`) that defines a common interface for all bank-specific downloaders, making it extensible for new institutions.

## Technologies

*   **Python 3**: Core language.
*   **Playwright**: For browser automation.
*   **Pandas**: For data manipulation and CSV parsing.
*   **Pydantic**: For configuration management.
*   **PyYAML**: For reading configuration files.

# Building and Running

## 1. Setup

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

## 2. Install Dependencies

Install the required Python packages from `requirements.txt`.

```bash
pip install -r requirements.txt
```

## 3. Configuration

The application can be configured via a `config.yaml` file. The script will search for this file in the following locations:
1.  The current directory (`.`)
2.  The user's home directory (`~/.ledger_fetch/`)

A key configuration is the `browser_profile_path`, which allows Playwright to maintain a persistent browser session to remember logins.

**Example `config.yaml`:**
```yaml
browser_profile_path: "C:/Users/YourUser/.ledger_fetch_chrome_profile"
headless: false
timeout: 30000
```

## 4. Running the Application

Run the main script using `main.py`. You can specify a single bank to fetch or run all of them.

```bash
# Run for all configured banks
python main.py

# Run for a specific bank (e.g., RBC)
python main.py --bank rbc

# Run in headless mode (overrides config)
python main.py --headless
```

**Important:** The login process is not fully automated. You will need to manually enter your credentials and complete any two-factor authentication prompts in the browser window that Playwright opens.

# Development Conventions

## Adding a New Bank

To add support for a new financial institution, create a new Python file in the `ledger_fetch/` directory (e.g., `newbank.py`). Inside this file, create a class that inherits from `BankDownloader` and implements the following abstract methods:

*   **`get_bank_name(self) -> str`**: Return a unique, lowercase string for the bank (e.g., `"newbank"`). This is used for directory and file naming.
*   **`login(self)`**: Implement the steps to navigate to the login page and wait for the user to manually authenticate.
*   **`navigate_to_transactions(self)`**: Implement the browser automation to get to the page where transactions can be downloaded.
*   **`download_transactions(self) -> List[Dict[str, Any]]`**: Implement the logic to select date ranges, file formats (preferably CSV), and download the transaction file. This method should parse the file and return a list of transaction dictionaries.

## Transaction Normalization

Use the helper classes in `ledger_fetch/utils.py`:
*   **`TransactionNormalizer`**: To clean up data like dates and descriptions, and to generate unique transaction IDs if the source data does not provide them.
*   **`CSVWriter`**: To save the normalized data to a CSV file. The base class's `save_transactions` method already uses this, so you may not need to call it directly.

## Output CSV Format

The output CSV files are organized by bank and month in the `transactions/` directory (or the configured `output_dir`). The goal is to have a consistent set of columns for all transactions, with the following required fields:

*   `Unique Transaction ID`
*   `Unique Account ID`
*   `Date` (in `YYYY-MM-DD` format)
*   `Description`
*   `Amount`
*   `Currency`
*   `Category`
