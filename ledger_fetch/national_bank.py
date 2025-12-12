from typing import List, Dict, Any, Optional
import time
import json
import logging
from datetime import datetime
from .base import BankDownloader
from .models import Transaction, Account, AccountType
from .utils import TransactionNormalizer

logger = logging.getLogger(__name__)

class NationalBankDownloader(BankDownloader):
    """
    National Bank of Canada (BNC) Transaction Downloader.
    
    This downloader targets the new BNC digital banking platform, which uses a GraphQL API.
    
    Workflow:
    1.  Interactive Login: User logs in manually.
    2.  Session Capture: The script listens for any GraphQL requests (`sbip/graphql`) 
        to capture the `session_id` and other authorization headers.
    3.  Storage Fallback: If network capture fails, it attempts to read the session ID 
        from `sessionStorage` or `localStorage`.
    4.  GraphQL Querying: Uses the captured session to execute specific GraphQL operations 
        (Accounts List, Transaction History) directly against the endpoint.
    """
    def get_bank_name(self) -> str:
        return "national_bank"

    def login(self):
        """
        Navigates to the login page and waits for the user to log in.
        Captures the session ID from network requests.
        """
        self.page.goto("https://app.bnc.ca/")
        
        # We need to capture the session headers.
        # We'll set up a listener to grab them from any graphql request.
        self.session_headers = {}

        def handle_request(request):
            if "sbip/graphql" in request.url and request.method == "POST":
                headers = request.headers
                if "session_id" in headers: # Verify specific header
                     self.session_headers["session_id"] = headers["session_id"]
                # Capture other potentially useful headers if present
                for key in ["x-auth-token", "x-xsrf-token", "authorization"]:
                     if key in headers:
                         self.session_headers[key] = headers[key]

        self.page.on("request", handle_request)

        print("Please log in to National Bank manually.")
        print("Waiting for dashboard to load...")
        
        # Wait up to 5 minutes for user to login
        max_retries = 300
        for _ in range(max_retries):
            # 1. Check if we captured via network
            if "session_id" in self.session_headers:
                print("Session captured via network!")
                break
                
            # 2. Check if we are on dashboard
            try:
                # Use specific selector from user provided HTML
                if self.page.is_visible("h2:has-text('My accounts')") or self.page.is_visible("text=My accounts"):
                    print("Dashboard detected! Attempting to extract session from storage...")
                    
                    # Attempt to get session_id from storage if network miss
                    session_id = self.page.evaluate("""() => {
                        return sessionStorage.getItem('session_id') || localStorage.getItem('session_id') || localStorage.getItem('sessionId');
                    }""")
                    
                    if session_id:
                        self.session_headers['session_id'] = session_id
                        print("Session captured from storage!")
                        break
                    else:
                        # Sometimes we might just proceed and hope cookies are enough for some requests,
                        # or trigger a refresh? Let's just wait a bit more or force a known request?
                        # For now, just continue waiting for a network event.
                        pass
            except:
                pass
                
            time.sleep(1)
        else:
             logger.warning("Session ID not captured after waiting. Login might have failed or format changed.")


    def navigate_to_transactions(self):
        """
        No-op for API-based downloader.
        We are already logged in and will use GraphQL endpoints.
        """
        pass

    def fetch_accounts(self) -> List[Account]:
        """
        Fetches the list of accounts using the GraphQL API.
        """
        if not self.session_headers:
            logger.error("No session headers available. Cannot fetch accounts.")
            return []

        # OP3957c7b5c49241df8191dddd45197b60 : accountsWithProductProfile
        payload = {
            "operationName": "OP3957c7b5c49241df8191dddd45197b60",
            "variables": {}
        }
        
        response = self._call_graphql(payload)
        
        print(f"DEBUG: Response Type: {type(response)}")
        print(f"DEBUG: Response Content: {str(response)[:1000]}")
        
        if not response:
            return []
            
        if isinstance(response, str):
            try:
                response = json.loads(response)
            except Exception as e:
                logger.error(f"Failed to parse response: {e}")
                return []

        accounts = []
        try:
            # Structure: data -> accounts -> items
            # Based on actual debug output
            raw_accounts = response.get("data", {}).get("accounts", {}).get("items", [])
            
            for item in raw_accounts:
                account_id = item.get("key")
                number = item.get("accountNumber")
                
                # Name
                product_name = item.get("productName", {})
                name = product_name.get("en") or product_name.get("fr") or "Unknown Account"
                
                currency = item.get("currency", "CAD")

                if account_id:
                    # Constructor is (raw_data, unique_account_id)
                    # We pass the full item as raw_data
                    acc = Account(item, account_id)
                    
                    # Set properties explicitly where our parsing logic differs from direct mapping
                    acc.account_name = f"{name} ({number if number else account_id})"
                    acc.currency = currency
                    
                    if number:
                        acc.account_number = number
                        
                    # Check for liability
                    acc_type = item.get("type", "")
                    print(f"DEBUG: Account Type: {acc_type}")
            
                    if acc_type == "LINE_OF_CREDIT":
                        acc.type = AccountType.LINE_OF_CREDIT
                    elif acc_type == "CREDIT_CARD":
                        acc.type = AccountType.CREDIT_CARD
                    elif acc_type == "CHECKING":
                        acc.type = AccountType.CHEQUING
                    elif acc_type == "SAVINGS":
                        acc.type = AccountType.SAVINGS
                    else:
                        acc.type = AccountType.UNKNOWN

                    accounts.append(acc)
                
                    
        except Exception as e:
            import traceback
            logger.error(f"Error parsing accounts: {e}")
            logger.error(traceback.format_exc())
            logger.debug(f"Response: {response}")

        return accounts

    def download_transactions(self) -> List[Transaction]:
        accounts = list(self.accounts_cache.values())
        
        # Determine days to fetch
        days_to_fetch = 365 # Default fallback
        if self.config.national_bank:
             days_to_fetch = self.config.national_bank.days_to_fetch
        
        logger.info(f"Fetching transactions for the last {days_to_fetch} days")
        
        all_transactions = []
        
        end_date = datetime.now()
        # BNC HAR showed relative dates, but we'll use absolute YYYY-MM-DD
        from datetime import timedelta
        start_date = end_date - timedelta(days=days_to_fetch)
        
        fmt_from = start_date.strftime("%Y-%m-%d")
        fmt_to = end_date.strftime("%Y-%m-%d")

        for account in accounts:
            logger.info(f"Fetching transactions for account: {account.account_name}")
            
            # Step 1: Select the account? (Optional but recommended based on HAR)
            # OP2b45a5923f314646a72c112e6bf4da27 : accountById
            select_payload = {
                "operationName": "OP2b45a5923f314646a72c112e6bf4da27",
                "variables": {
                    "byIdRequestInput": {
                        "id": account.unique_account_id
                    }
                }
            }
            try:
                account_details = self._call_graphql(select_payload)
                
                # Try to extract balance
                if account_details:
                    # Expected structure: data -> accountById
                    data = account_details.get("data", {}).get("accountById", {})
                    if data:
                        # Extract currency if available to confirm
                        # currency = data.get("currency")
                        
                        # Balance keys seen in other calls or typical for this API
                        # We'll try common ones. data might have 'balance' directly or nested.
                        raw_balance = data.get("balance")
                        
                        if raw_balance is not None:
                            try:
                                account.current_balance = float(raw_balance)
                                logger.info(f"Updated balance for {account.account_name}: {account.current_balance}")
                                # Save accounts immediately to persist balance
                                self.save_accounts(accounts)
                            except (ValueError, TypeError):
                                logger.warning(f"Could not parse balance: {raw_balance}")
                        else:
                            # Debug print if balance not found, to help refine
                            print(f"DEBUG: Account Details Keys: {list(data.keys())}")

            except Exception as e:
                logger.warning(f"Failed to select/update account {account.unique_account_id}: {e}")
            
            # Step 2: Fetch transactions
            # OPbba3ce1cb8f44bec99877c8e7c36cbaa : detailedTransactions
            
            trans_payload = {
                "operationName": "OPbba3ce1cb8f44bec99877c8e7c36cbaa",
                "variables": {
                    "transactionsRequestInput": {
                         "queryParams": {
                            "sorting": [
                              { "ascending": True, "fieldName": "effectiveDate" },
                            ]
                          },
                         "fromDate": fmt_from,
                         "toDate": fmt_to
                    }
                }
            }
            
            response = self._call_graphql(trans_payload)
            if not response:
                logger.warning(f"No response for account {account.account_name}")
                continue

            # Handle potential string response (like in fetch_accounts)
            if isinstance(response, str):
                try:
                    response = json.loads(response)
                except Exception:
                    pass

            print(f"DEBUG: Transaction Response for {account.account_name}: {str(response)[:1000]}") # Log first 1000 chars

            try:
                raw_txs = response.get("data", {}).get("detailedTransactions", {})
                
                if isinstance(raw_txs, dict):
                     # Likely wrapped in 'items' or similar
                     if "items" in raw_txs:
                         raw_txs = raw_txs["items"]
                     elif "transactions" in raw_txs:
                         raw_txs = raw_txs["transactions"]
                     else:
                         # Maybe it IS the list?
                         print(f"DEBUG: raw_txs is a dict but no known key found: {raw_txs.keys()}")
                         raw_txs = []
                
                print(f"DEBUG: Found {len(raw_txs)} raw transactions")

                for raw in raw_txs:
                    t_date = raw.get("effectiveDate") or raw.get("transactionDate")
                    
                    # Description is a dict like {'fr': '...', 'en': '...'}
                    desc_raw = raw.get("description")
                    description = ""
                    if isinstance(desc_raw, dict):
                         description = desc_raw.get("en") or desc_raw.get("fr") or ""
                    elif isinstance(desc_raw, str):
                         description = desc_raw
                    else:
                         description = raw.get("label") or raw.get("merchantName") or "Unknown Transaction"

                    # Amount logic
                    # We consistently see 'realAmount' in the logs.
                    # 'type' is 'DEBIT' or 'CREDIT'.
                    # DEBIT = expense/outflow (-), CREDIT = income/inflow/payment (+)
                    
                    amount_val = raw.get("realAmount")
                    if amount_val is None:
                        # Fallback
                        amount_val = raw.get("amount") or raw.get("transactionAmount")
                    
                    if not t_date:
                        # logger.warning(f"Transaction missing date: {raw}")
                        continue
                        
                    if amount_val is None:
                         # logger.warning(f"Transaction missing amount: {raw}")
                         continue
                    
                    try:
                        amount = float(amount_val)
                    except:
                        continue
                        
                    tx_type = raw.get("type", "DEBIT") # Default to DEBIT if unknown?
                    if tx_type == "DEBIT":
                        amount = -abs(amount)
                    elif tx_type == "CREDIT":
                        amount = abs(amount)
                        
                    # Handle invert_credit_transactions config
                    if self.config.national_bank and self.config.national_bank.invert_credit_transactions:
                         amount = -amount
                        
                    # Normalize description and payee
                    clean_desc = TransactionNormalizer.clean_description(description)
                    payee_name = TransactionNormalizer.normalize_payee(clean_desc)
                    
                    # Use generated ID as bank IDs are not sufficiently unique
                    t_id =  raw.get("guid") or TransactionNormalizer.generate_transaction_id(
                        t_date, 
                        amount, 
                        description, 
                        account.unique_account_id
                    )
                    
                    # Fix: Constructor takes (raw_data, unique_account_id)
                    tx = Transaction(raw, account.unique_account_id)
                    
                    # Set properties explicitly
                    tx.unique_transaction_id = t_id
                    tx.date = t_date
                    tx.description = clean_desc # Use cleaned description
                    tx.amount = amount
                    tx.currency = account.currency
                    tx.account_name = account.account_name
                    
                    # Populate extra fields
                    # Payee Name: use normalized payee
                    tx.payee_name = payee_name
                    
                    # Category: Use the categoryId we saw in logs
                    tx.set('Category', raw.get("categoryId", ""))
                    
                    # Notes: Use 'memo' from logs
                    tx.set('Notes', raw.get("memo", ""))
                    
                    # Is Transfer: check description for keywords seen in logs
                    # Logs showed: "Transfert entre comptes", "Transfer between accounts", "VIREMENT INTERAC"
                    is_transfer = False
                    desc_upper = description.upper()
                    if "TRANSFER" in desc_upper or "VIREMENT" in desc_upper:
                        is_transfer = True
                    tx.set('Is Transfer', is_transfer)
                    
                    # Explicitly map extra fields for consistent CSV output
                    tx.set('Transaction Type', raw.get('type'))
                    tx.set('Operation', raw.get('operation'))
                    tx.set('Operation Number', raw.get('operationNumber'))
                    tx.set('Effective Date', raw.get('effectiveDate'))
                    tx.set('Created Date', raw.get('createdDate'))
                    tx.set('Balance', raw.get('balance'))
                    tx.set('Check Number', raw.get('checkNumber'))
                    tx.set('Confirmation Number', raw.get('confirmationNumber') or raw.get('referenceNumber'))
                    
                    all_transactions.append(tx)

            except Exception as e:
                logger.error(f"Error parsing transactions for {account.account_name}: {e}")

        return all_transactions

    return all_transactions

    def _call_graphql(self, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Execute a GraphQL query/mutation using the browser's fetch API.
        
        We inject a `fetch` call into the browser page using `page.evaluate`. 
        This is critical because:
        1. It ensures the request originates from the correct origin (CORS).
        2. It automatically attaches all cookies.
        3. We can manually mix in our captured `session_headers`.
        """
        url = "https://digitalretail.apis.bnc.ca/sbip/graphql"
        
        # We use page.evaluate to make the fetch from the browser context
        # This ensures cookies and existing session are used.
        # We also inject the captured session_id header if needed, but browser fetch usually handles it 
        # IF we explicitly set headers.
        
        # NOTE: standard `fetch` in browser might not send all custom headers automatically 
        # unless we add them. We captured `session_headers`.
        
        try:
            # Prepare headers JS string
            headers_json = json.dumps(self.session_headers)
            payload_json = json.dumps(payload)
            
            result = self.page.evaluate(f'''
                async () => {{
                    const response = await fetch("{url}", {{
                        method: "POST",
                        headers: {{
                            "Content-Type": "application/json",
                            ...{headers_json}
                        }},
                        body: JSON.stringify({payload_json})
                    }});
                    return await response.json();
                }}
            ''')
            return result
        except Exception as e:
            logger.error(f"GraphQL request failed: {e}")
            return None    
