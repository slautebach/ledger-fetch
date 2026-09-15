#!/usr/bin/env python
"""
Open a bank's browser profile for a manual login session.

Use this when a bank's session expires: log in at your own pace, let Chrome
save the password when it offers, and verify it pre-fills. Close the browser
(or Ctrl+C) when done - the saved session and password then carry over to
automated fetches.

    ./open_bank.py --bank amex
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ledger_fetch.config import settings
from ledger_fetch.base import BankDownloader

LOGIN_URLS = {
    "rbc": "https://www.rbcroyalbank.com/ways-to-bank/online-banking.html",
    "bmo": "https://www1.bmo.com/banking/digital/login?lang=en",
    "amex": "https://global.americanexpress.com/activity?COUNTRY_CODE=CA&cycleIndex=0",
    "cibc": "https://www.cibc.com/en/personal-banking.html?loggedOut=true",
    "national_bank": "https://app.bnc.ca/",
    "wealthsimple": "https://my.wealthsimple.com/app/login",
    "canadiantire": "https://www.ctfs.com/content/dash/en/private/Details.html#!/view?tab=account-details",
}


def main():
    parser = argparse.ArgumentParser(description="Open a bank profile for manual login")
    parser.add_argument("--bank", required=True, choices=list(LOGIN_URLS.keys()))
    args = parser.parse_args()

    browser_cfg = settings.browser
    if not browser_cfg.profile_root:
        print("ERROR: per-bank profiles not configured (browser.profile_root).")
        return 1
    user_data_dir = browser_cfg.profile_root / args.bank
    user_data_dir.mkdir(parents=True, exist_ok=True)
    BankDownloader._ensure_password_prefs(user_data_dir)

    from playwright.sync_api import sync_playwright

    print(f"Opening {args.bank} profile: {user_data_dir}")
    print("Log in, click Save when Chrome offers, verify pre-fill, then just close the window.\n")

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=str(user_data_dir),
            channel="chrome",
            headless=False,
            accept_downloads=True,
            ignore_default_args=["--enable-automation", "--use-mock-keychain"],
            args=[
                "--disable-blink-features=AutomationControlled",
                "--password-store=basic",
            ],
        )
        ctx.set_default_timeout(60000)
        page = ctx.new_page()
        page.goto(LOGIN_URLS[args.bank])

        closed = {"done": False}
        ctx.on("close", lambda _c: closed.__setitem__("done", True))

        try:
            while not closed["done"]:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        finally:
            try:
                ctx.close()
            except Exception:
                pass
    print("Done. Session and any saved passwords persist for future fetches.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
