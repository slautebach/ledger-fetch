import os
import time
import re
import json
import shutil
import urllib.parse
from pathlib import Path
from datetime import datetime, timedelta
import pandas as pd
from typing import List, Dict, Any, Optional
from .base import BankDownloader
from .utils import TransactionNormalizer
from .models import Transaction, Account, AccountType

class AmexDownloader(BankDownloader):
    """
    American Express Transaction Downloader.

    It automates the process of fetching transactions from American Express
    using the site's own `ReadAccountActivity.web.v1` API (the legacy
    `searchTransaction.json` endpoint was retired).

    Workflow:
    1.  Interactive Login: User logs in manually.
    2.  Account Discovery: Scrapes account ID from "Recent Activity" and balances from "Dashboard".
    3.  API Capture: The app's own activity request is sniffed for the
        accountToken and required headers.
    4.  API Replay: The request is replayed with pagination to collect history.
    5.  Parsing: A tolerant parser locates the transaction list in the response.
    """

    from datetime import datetime, timedelta

    # Known request constants (verified 2026-09-15). Used when the app's own
    # request cannot be sniffed (e.g. cached page load). The accountToken is a
    # stable account identifier, not a session token.
    _activity_api_url = "https://functions.americanexpress.com/ReadAccountActivity.web.v1"
    _fallback_account_token = "WWB4IHG2HXCSFWG"

    def get_bank_name(self) -> str:
        return "amex"

    def login(self):
        """
        Navigate to login page and wait for manual login.
        
        This method directs the browser to the 'Recent Activity' page, which redirects to the login
        screen if the user is not authenticated. It then waits for the URL to change back to a 
        statement/activity page, indicating successful login.
        """
        print("Navigating to American Express Statements page (will redirect to login)...")
        # Use the new app route directly - the legacy /activity/recent route
        # triggers an SSO DestPage re-handshake that invalidates the saved
        # session on every launch. Landing on /activity reads existing cookies.
        self.page.goto("https://global.americanexpress.com/activity?COUNTRY_CODE=CA&cycleIndex=0")
        
        print("\nWaiting for user to log in...")
        print("Please complete the login process.")
        print("You should be automatically redirected to the Statements page.")
        
        # Wait for statements page (post-login lands on /activity/... now).
        # NOTE: must exclude "login" URLs - the login page's DestPage query
        # param contains the literal string "activity" (only slashes are
        # percent-encoded), which defeats a naive substring/regex match.
        # The post-login page may open in a NEW tab, so scan all pages.
        try:
            deadline = time.time() + 300
            while time.time() < deadline:
                for pg in self.context.pages:
                    if self._is_activity_url(pg.url):
                        self.page = pg
                        print(f"Login and redirect detected (tab: {pg.url.split('?')[0]}).")
                        return
                time.sleep(2)
            print("Warning: Login timeout. Proceeding anyway.")
        except Exception:
            print("Warning: error waiting for login. Proceeding anyway.")

    @staticmethod
    def _is_activity_url(url: str) -> bool:
        """True when the URL is a real activity/statement page (not login).

        The login URL's DestPage param contains the literal text 'activity',
        so 'login' must be explicitly excluded.
        """
        u = (url or "").lower()
        return ("activity" in u or "statement" in u) and "login" not in u

    def navigate_to_transactions(self):
        """Navigate to Statements & Activity, arming the API sniffer first.

        The app fires its ReadAccountActivity.web.v1 request while the page
        loads, so listeners must be in place BEFORE navigation to capture the
        account token and a sample response.
        """
        self._arm_activity_sniffer()
        print("Navigating to Statements page...")

        for attempt in (1, 2):
            try:
                self.page.goto("https://global.americanexpress.com/activity?COUNTRY_CODE=CA&cycleIndex=0")
            except Exception:
                pass

            # Wait for the activity page to settle, surviving login redirects
            # and country-code bounces (up to 4 min for manual login).
            deadline = time.time() + 240
            logged_in_wait_shown = False
            while time.time() < deadline:
                url = self.page.url
                if "login" in url.lower():
                    if not logged_in_wait_shown:
                        print("  On login page - complete login in the browser window...")
                        logged_in_wait_shown = True
                    time.sleep(2)
                    continue
                if self._is_activity_url(url):
                    try:
                        self.page.wait_for_load_state("networkidle", timeout=15000)
                    except Exception:
                        pass
                    print(f"  Activity page ready: {url.split('?')[0]}")
                    return
                time.sleep(1)

            print(f"  Attempt {attempt}: not on activity page (at {self.page.url.split('?')[0]})")

        # Sniffer stays armed; download_transactions will still wait briefly
        # in case the app fires its call late.

    def _arm_activity_sniffer(self):
        """Listen for the app's own ReadAccountActivity request/response."""
        self._activity_capture = {}

        def on_request(request):
            if "ReadAccountActivity" in request.url and request.method == "POST":
                try:
                    self._activity_capture["body"] = json.loads(request.post_data)
                except (TypeError, ValueError):
                    self._activity_capture["body"] = {}
                self._activity_capture["url"] = request.url

        def on_response(response):
            if "ReadAccountActivity" in response.url:
                try:
                    self._activity_capture["response_text"] = response.text()
                except Exception:
                    pass

        self.page.on("request", on_request)
        self.page.on("response", on_response)
        self._activity_capture["_handlers"] = [("request", on_request),
                                               ("response", on_response)]

    def _disarm_activity_sniffer(self):
        for event, handler in self._activity_capture.get("_handlers", []):
            try:
                self.page.remove_listener(event, handler)
            except Exception:
                pass

    def fetch_accounts(self) -> List[Account]:
        """
        Fetch account details by scraping both Recent Activity (for ID) and Dashboard (for balances).
        
        Amex doesn't provide a single clean "API" response for all account details that is easily 
        accessible without complex session tokens. Thus, we scrape:
        1.  Account ID (last 5 digits) from the "Recent Activity" page selector.
        2.  Current Balance and Payment Due info from the "Dashboard" page.
        
        Returns:
            List[Account]: A list containing the single primary active account (multi-card support is limited).
        """
        print("Fetching account details...")
        
        # --- Step 1: Get Account ID from Activity Page ---
        if "/activity" not in self.page.url:
            print("Navigating to Recent Activity for Account ID...")
            try:
                self.page.goto("https://global.americanexpress.com/activity?COUNTRY_CODE=CA&cycleIndex=0")
                self.page.wait_for_selector("span[data-ng-bind*='acctNumberlast5Digits']", timeout=15000)
            except:
                print("Warning: Timeout waiting for Activity page load.")

        last_digits = "00000"
        unique_id = "AMEX-DEFAULT"
        
        try:
            # Selector based on: <span class="card-member-cell ..."> - 91001</span>
            acct_el = self.page.locator("span[data-ng-bind*='acctNumberlast5Digits']").first
            if acct_el.count() > 0:
                 text = acct_el.text_content() # " - 91001"
                 match = re.search(r'(\d{4,5})', text)
                 if match:
                     last_digits = match.group(1)
                     unique_id = f"AMEX-{last_digits}"
        except Exception as e:
             print(f"Warning: could not parse account digits from Activity page: {e}")
        
        print(f"  Found account: {unique_id}")

        # --- Step 2: Get Balances from Dashboard ---
        print("Navigating to Dashboard for balances...")
        try:
            self.page.goto("https://global.americanexpress.com/dashboard")
            self.page.wait_for_selector("[data-locator-id='total_balance_title_value']", timeout=15000)
        except: 
             print("Warning: Timeout waiting for dashboard load.")

        current_balance = 0.0
        remaining_balance_due = 0.0
        statement_balance = 0.0
        payment_due_date = ""

        try:
            # Extract Balance (Current Balance / Total Balance)
            # User provided: <span ... data-locator-id="total_balance_title_value">...</span>
            balance_el = self.page.locator("[data-locator-id='total_balance_title_value']").first
            if balance_el.count() > 0:
                balance_text = balance_el.text_content()
                clean_balance = balance_text.replace('$', '').replace(',', '').strip()
                current_balance = float(clean_balance)
            
            # Extract Remaining Statement Balance
            # User provided: <span ... data-locator-id="remaining_statement_balance_title_value">...</span>
            rem_bal_el = self.page.locator("[data-locator-id='remaining_statement_balance_title_value']").first
            if rem_bal_el.count() > 0:
                txt = rem_bal_el.text_content().replace('$', '').replace(',', '').strip()
                if txt:
                    remaining_balance_due = float(txt)

            # Payment Due Date
            # Trying to find on Dashboard
            due_date_el = self.page.locator("[data-locator-id*='payment_due_date']").first
            if due_date_el.count() > 0:
                due_txt = due_date_el.text_content().strip()
                if due_txt:
                    from .utils import TransactionNormalizer
                    payment_due_date = TransactionNormalizer.normalize_date(due_txt)

        except Exception as e:
            print(f"Warning: could not parse dashboard details: {e}")

        print(f"  Balance: ${current_balance}")
        print(f"  Remaining Balance: ${remaining_balance_due}")
        print(f"  Payment Due: {payment_due_date}")

        account = Account({}, unique_id)
        account.current_balance = current_balance
        account.account_name = "American Express"
        account.currency = "CAD" # Assumption
        account.type = AccountType.CREDIT_CARD
        
        account.statement_balance = statement_balance # Not extracted yet
        account.remaining_balance_due = remaining_balance_due
        account.payment_due_date = payment_due_date
        
        return [account]



    def download_transactions(self) -> List[Transaction]:
        """
        Download transactions via the site's own ReadAccountActivity API.

        Amex retired the old searchTransaction.json endpoint. The new
        functions.americanexpress.com API requires an accountToken and custom
        headers (ce-source, one-data-correlation-id) that the site's JS
        generates. We harvest the app's own request and replay it with
        pagination to collect history.
        """
        print("Fetching transactions via ReadAccountActivity API...")

        bank_config = self.config.ledger_fetch.banks.get(self.get_bank_name())
        days = getattr(bank_config, 'days_to_fetch', 365) if bank_config else 365
        print(f"Fetch configuration: days_to_fetch={days}")

        try:
            # The sniffer was armed in navigate_to_transactions; give the app's
            # own call a moment to land if it hasn't yet.
            deadline = time.time() + 45
            while time.time() < deadline and "body" not in (self._activity_capture or {}):
                time.sleep(1)

            # Prefer the sniffed request body (adapts if Amex changes the
            # schema); fall back to known constants so a cached page that
            # fires no API call doesn't block the fetch.
            if self._activity_capture and "url" in self._activity_capture:
                base_body = self._activity_capture["body"]
                api_url = self._activity_capture["url"]
            else:
                print("App did not fire its API call; using known request constants.")
                base_body = {
                    "accountToken": self._fallback_account_token,
                    "axplocale": "en-CA",
                    "transactionFilters": {"limit": 100, "offset": 1},
                    "view": "RECENT",
                }
                api_url = self._activity_api_url
            print(f"Request template: {json.dumps(base_body)[:160]}")

            if not base_body.get("accountToken"):
                print("No accountToken available; cannot fetch.")
                return []

            all_transactions = []

            # 1. Baseline RECENT request: returns current cycle data plus the
            #    statementPeriods metadata listing every retrievable cycle.
            recent_json = self._replay_activity_api(base_body, api_url)
            if recent_json is None:
                return []

            txns = self._parse_activity_json(recent_json)
            print(f"Recent view: {len(txns)} transactions")
            all_transactions.extend(txns)

            # 2. Fetch each billed statement cycle that overlaps our window.
            periods = self._extract_statement_periods(recent_json)
            if not periods:
                print("No statementPeriods in response; recent view only.")
            else:
                oldest_needed = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
                for period in periods:
                    cycle = period.get("cycleIndex")
                    end = str(period.get("endDate", ""))[:10]
                    if cycle is None or not end:
                        continue
                    if end < oldest_needed:
                        continue
                    body = dict(base_body)
                    body["view"] = "BILLED"
                    body["cycleIndex"] = cycle
                    body["transactionFilters"] = {"limit": 100, "offset": 1}
                    try:
                        cycle_json = self._replay_activity_api(body, api_url)
                        if cycle_json is None:
                            continue
                        cycle_txns = self._parse_activity_json(cycle_json)
                        print(f"  Cycle {cycle} ({period.get('startDate')}..{end}): "
                              f"{len(cycle_txns)} transactions")
                        all_transactions.extend(cycle_txns)
                        time.sleep(1)
                    except Exception as e:
                        print(f"  Cycle {cycle} error: {e}")

            # Dedupe
            seen = {}
            for t in all_transactions:
                if t.unique_transaction_id and t.unique_transaction_id not in seen:
                    seen[t.unique_transaction_id] = t
            all_transactions = list(seen.values())
            print(f"Successfully fetched {len(all_transactions)} unique transactions.")
            return all_transactions
        finally:
            if getattr(self, "_activity_capture", None):
                self._disarm_activity_sniffer()

    def _extract_statement_periods(self, data) -> List[Dict[str, Any]]:
        """Pull the statementPeriods list (cycleIndex/startDate/endDate) from
        a ReadAccountActivity response."""
        if not isinstance(data, dict):
            return []
        periods = data.get("statementPeriods")
        if isinstance(periods, list):
            return periods
        # Hunt for it just in case it moves
        def hunt(o, depth=0):
            if depth > 5 or not isinstance(o, dict):
                return None
            if isinstance(o.get("statementPeriods"), list):
                return o["statementPeriods"]
            for v in o.values():
                r = hunt(v, depth + 1)
                if r is not None:
                    return r
            return None
        return hunt(data) or []

    def _replay_activity_api(self, payload: Dict[str, Any], api_url: str = None):
        """
        POST a ReadAccountActivity request via page.request.

        Cookies are attached automatically from the browser context. The two
        custom headers the API requires are regenerated per call.
        """
        import uuid as _uuid
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Referer": "https://global.americanexpress.com/activity?COUNTRY_CODE=CA&cycleIndex=0",
            "ce-source": "WEB",
            "one-data-correlation-id": f"CSR-{_uuid.uuid4()}",
        }
        response = self.page.request.post(
            api_url or self._activity_api_url, headers=headers,
            data=json.dumps(payload),
        )
        if not response.ok:
            print(f"  API error status: {response.status} {response.text()[:200]}")
            return None
        try:
            return response.json()
        except ValueError:
            print("  API returned non-JSON response")
            return None

    def _parse_activity_json(self, data) -> List[Transaction]:
        """
        Tolerant parser for the ReadAccountActivity response.

        The exact response schema is not formally documented, so we hunt the
        JSON tree for lists of transaction-like dicts and map fields by their
        candidate key names.
        """
        transactions = []
        account_id = "AMEX"

        def find_txn_lists(o, depth=0):
            if depth > 7 or o is None:
                return
            if isinstance(o, list):
                if (o and isinstance(o[0], dict)
                        and any(k in o[0] for k in (
                                "chargeDate", "transactionDate", "transactionAmount",
                                "descriptionLine", "transactorName", "transactionId"))):
                    parse_list(o)
                else:
                    for v in o:
                        find_txn_lists(v, depth + 1)
            elif isinstance(o, dict):
                for v in o.values():
                    find_txn_lists(v, depth + 1)

        def parse_list(items):
            nonlocal account_id
            for item in items:
                try:
                    # Dump the first item's schema for future maintenance
                    dump = Path("/tmp/amex_sample_item.json")
                    if not dump.exists():
                        try:
                            dump.write_text(json.dumps(item, indent=2, default=str))
                        except Exception:
                            pass

                    date_str = self._extract_txn_date(item)
                    if not date_str:
                        continue

                    description = ""
                    for key in ("displayDescription", "descriptionLine", "description",
                                "merchantName", "transactorName", "name"):
                        if item.get(key):
                            description = str(item[key]).strip()
                            break

                    amount = 0.0
                    for key in ("transactionAmount", "amount", "chargeAmount"):
                        if item.get(key) is not None:
                            amount = self._to_amount(item[key])
                            break

                    unique_id = ""
                    for key in ("uniqueReferenceNumber", "transactionId",
                                "referenceNumber", "id", "token"):
                        if item.get(key):
                            unique_id = str(item[key])
                            break

                    is_pending = bool(item.get("pendingTransactionIndicator")
                                      or item.get("isPending")
                                      or str(item.get("status", "")).lower() == "pending"
                                      or (isinstance(item.get("message"), dict)
                                          and str(item["message"].get("id", "")).lower() == "pending"))

                    # Try to enrich account id from nested account info
                    if account_id == "AMEX":
                        for key in ("acctNumberlast5Digits", "accountLast5",
                                    "lastFiveDigits"):
                            for sub in (item, data if isinstance(data, dict) else {}):
                                if isinstance(sub, dict) and sub.get(key):
                                    account_id = f"AMEX-{sub[key]}"
                                    break

                    clean_desc = TransactionNormalizer.clean_description(description)
                    payee_name = TransactionNormalizer.normalize_payee(clean_desc)

                    if not unique_id:
                        unique_id = TransactionNormalizer.generate_transaction_id(
                            date_str, amount, clean_desc, account_id)

                    txn = Transaction(item, account_id)
                    txn.unique_transaction_id = unique_id
                    txn.date = date_str
                    txn.description = clean_desc
                    txn.payee_name = payee_name
                    txn.amount = amount
                    txn.currency = "CAD"
                    txn.is_pending = is_pending
                    txn.raw_data['Status'] = 'Pending' if is_pending else 'Posted'
                    transactions.append(txn)
                except Exception as e:
                    print(f"Error parsing transaction item: {e}")
                    continue

        find_txn_lists(data)
        return transactions

    def _to_amount(self, val) -> float:
        """Coerce an amount field to float.

        The new API represents money as objects (e.g. {"amount": 42.99,
        "currency": "CAD"}) rather than plain numbers.
        """
        if isinstance(val, (int, float)):
            return float(val)
        if isinstance(val, dict):
            for k in ("amount", "value", "transactionAmount", "chargeAmount"):
                v = val.get(k)
                if isinstance(v, (int, float)):
                    return float(v)
                if isinstance(v, str):
                    try:
                        return float(v.replace(",", "").replace("$", ""))
                    except ValueError:
                        continue
        if isinstance(val, str):
            try:
                return float(val.replace(",", "").replace("$", ""))
            except ValueError:
                pass
        return 0.0

    def _extract_txn_date(self, item) -> Optional[str]:
        """Extract a normalized YYYY-MM-DD date from a transaction dict.

        Pending items carry only displayDate; posted items also have
        chargeDate/postDate.
        """
        for key in ("displayDate", "chargeDate", "postDate", "transactionDate", "date"):
            val = item.get(key)
            if not val:
                continue
            try:
                if isinstance(val, (int, float)):
                    # epoch ms vs epoch seconds
                    ts = val / 1000.0 if val > 1e11 else float(val)
                    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
                val = str(val).strip()
                if val.isdigit() and len(val) == 13:
                    return datetime.fromtimestamp(int(val) / 1000).strftime("%Y-%m-%d")
                if val.isdigit() and len(val) == 8:  # YYYYMMDD
                    return f"{val[:4]}-{val[4:6]}-{val[6:8]}"
                # ISO-ish strings
                return val[:10].replace("/", "-") if len(val) >= 10 else None
            except Exception:
                continue
        return None

    def _parse_amex_json(self, data: Dict[str, Any]) -> List[Transaction]:
        """
        Parse the JSON response from searchTransaction.json.
        
        Args:
            data (Dict[str, Any]): The raw JSON data from the API.
            
        Returns:
            List[Transaction]: A list of Transaction objects.
        """
        transactions = []
        
        try:
            # Navigate to transactions list
            stmt = data.get("statement", {})
            txns_list = stmt.get("transactionsList", [])
            
            if not txns_list:
                print("No transactions found in API response.")
                return []
                
            for item in txns_list:
                try:
                    # Extract fields
                    timestamp = item.get("chargeDate")
                    if timestamp:
                        date_obj = self.datetime.fromtimestamp(timestamp / 1000)
                        date_str = date_obj.strftime("%Y-%m-%d")
                    else:
                        continue
                        
                    description = item.get("descriptionLine", "").strip()
                    amount = float(item.get("transactionAmount", 0.0))
                    
                    unique_trans_id = item.get("uniqueReferenceNumber")
                    if not unique_trans_id:
                         unique_trans_id = item.get("transactionId")
                         
                    account_id = "AMEX"
                    bal_info = stmt.get("balanceInfo", {})
                    last_digits = bal_info.get("acctNumberlast5Digits")
                    if last_digits:
                        account_id = f"AMEX-{last_digits}"
                    
                    clean_desc = TransactionNormalizer.clean_description(description)
                    payee_name = TransactionNormalizer.normalize_payee(clean_desc)
                    
                    is_pending = bool(item.get("pendingTransactionIndicator"))
                    
                    txn = Transaction(item, account_id)
                    txn.unique_transaction_id = unique_trans_id
                    txn.date = date_str
                    txn.description = clean_desc

                    txn.payee_name = payee_name
                    txn.amount = amount
                    txn.currency = "CAD" # Default
                    txn.is_pending = is_pending
                    
                    # Ensure status is captured in raw data for importer
                    txn.raw_data['Status'] = 'Pending' if is_pending else 'Posted'
                    
                    transactions.append(txn)
                    
                except Exception as e:
                    print(f"Error parsing transaction item: {e}")
                    continue
                    
        except Exception as e:
            print(f"Error parsing JSON response: {e}")
            
        return transactions

    def _expand_sections(self):
        """Deprecated: No longer needed for API approach."""
        pass

    def _extract_account_key(self):
        """Extract account key from URL or page content."""
        account_key = None
        try:
            # Try URL
            for i in range(5):
                current_url = self.page.url
                parsed_url = urllib.parse.urlparse(current_url)
                query_params = urllib.parse.parse_qs(parsed_url.query)
                keys = query_params.get('account_key')
                if keys:
                    return keys[0]
                time.sleep(1)
                
            # Try Page Content
            content = self.page.content()
            match = re.search(r'account_key=["\']?([a-zA-Z0-9-]+)["\']?', content)
            if match:
                return match.group(1)
        except:
            pass
            
        return None 

    def _find_download_buttons(self):
        pass

    def _extract_date(self, btn):
        pass

    def _download_statement(self, account_key, date_part, is_latest, download_dir):
        pass

    def _parse_amex_csv(self, csv_path: str, account_id: str = "AMEX") -> List[Transaction]:
        pass

