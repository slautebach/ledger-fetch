"""
ledger_fetch package.

This package contains the core logic for downloading financial transactions
from various banks (RBC, BMO, Amex, Wealthsimple, Canadian Tire, CIBC).
It includes the abstract base class `BankDownloader`, bank-specific implementations,
and utility functions for data normalization and CSV export.
"""
from .base import BankDownloader
from .models import Transaction, Account, AccountType
from .config import settings, Config
from .amex import AmexDownloader
from .bmo import BMODownloader
from .canadiantire import CanadianTireDownloader
from .cibc import CIBCDownloader
from .national_bank import NationalBankDownloader
from .rbc import RBCDownloader
from .wealthsimple import WealthsimpleDownloader

__all__ = [
    "BankDownloader",
    "Transaction",
    "Account",
    "AccountType",
    "settings",
    "Config",
    "AmexDownloader",
    "BMODownloader",
    "CanadianTireDownloader",
    "CIBCDownloader",
    "NationalBankDownloader",
    "RBCDownloader",
    "WealthsimpleDownloader",
]
