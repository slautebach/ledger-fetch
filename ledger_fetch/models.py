from typing import Dict, Any, Union, List
from abc import ABC, abstractmethod
from enum import Enum

"""
Data Models for Ledger Fetch

This module defines the core data structures used throughout the application to 
represent financial entities. It uses Pydantic-like structures (though implemented 
manually here for simplicity/flexibility) to handle data ingestion and CSV serialization.

Key Classes:
- AccountType: Enum for account classifications.
- BaseModel: Abstract base class providing common dictionary-base storage and CSV export methods.
- Transaction: Represents a single financial transaction.
- Account: Represents a bank account or credit card.
"""

class AccountType(str, Enum):
    CHEQUING = "Chequing"
    SAVINGS = "Savings"
    CREDIT_CARD = "Credit Card"
    LINE_OF_CREDIT = "Line of Credit"
    MORTGAGE = "Mortgage"
    INVESTMENT = "Investment"
    LOAN = "Loan"
    OTHER = "Other"

class BaseModel(ABC):
    """
    Abstract base model that wraps a raw data dictionary.
    
    Provides utility methods to get/set values and flatten nested dictionaries 
    for flat CSV export.
    """
    def __init__(self, raw_data: Dict[str, Any]):
        self.raw_data = raw_data

    def get(self, key: str, default: Any = None) -> Any:
        return self.raw_data.get(key, default)

    def set(self, key: str, value: Any):
        self.raw_data[key] = value

    def _flatten_raw_data(self) -> Dict[str, Any]:
        """
        Flattens the raw_data dictionary using dot notation for nested keys.
        """
        out = {}
        def flatten(x, name=''):
            if type(x) is dict:
                for a in x:
                    flatten(x[a], name + a + '.')
            else:
                out[name[:-1]] = x
        flatten(self.raw_data)
        return out

    @abstractmethod
    def get_required_csv_row(self) -> Dict[str, Any]:
        """
        Returns a dictionary of the required CSV fields and their values.
        """
        pass

    def to_csv_row(self) -> Dict[str, Any]:
        """
        Serializes the model to a CSV row dictionary.
        Merges required fields with flattened raw data.
        """
        row = self.get_required_csv_row()
        
        # Add flattened raw data
        flat_raw = self._flatten_raw_data()
        row.update(flat_raw)
        
        return row

class Transaction(BaseModel):
    """
    Represents a single financial transaction.
    
    This class normalizes transaction data from various bank formats into a consistent structure.
    It provides properties for accessing standard fields like date, amount, description, etc., 
    while preserving the original 'raw_data' from the bank for debugging or extended detail.
    """
    CSV_FIELDS = [
        'Unique Transaction ID',
        'Unique Account ID',
        'Account Name',
        'Date',
        'Description',
        'Payee Name',
        'Amount',
        'Currency',
        'Category',
        'Is Transfer',
        'Notes'
    ]

    def __init__(self, raw_data: Dict[str, Any], unique_account_id: str):
        super().__init__(raw_data)
        # Ensure Unique Account ID is set in raw_data
        self.unique_account_id = unique_account_id

    @property
    def unique_transaction_id(self) -> str:
        return self.get('Unique Transaction ID', '')

    @unique_transaction_id.setter
    def unique_transaction_id(self, value: str):
        self.set('Unique Transaction ID', value)

    @property
    def unique_account_id(self) -> str:
        return self.get('Unique Account ID', '')

    @unique_account_id.setter
    def unique_account_id(self, value: str):
        self.set('Unique Account ID', value)

    @property
    def account_name(self) -> str:
        return self.get('Account Name', '')

    @account_name.setter
    def account_name(self, value: str):
        self.set('Account Name', value)

    @property
    def date(self) -> str:
        return self.get('Date', '')

    @date.setter
    def date(self, value: str):
        from .utils import TransactionNormalizer
        self.set('Date', TransactionNormalizer.normalize_date(value))

    @property
    def description(self) -> str:
        return self.get('Description', '')

    @description.setter
    def description(self, value: str):
        self.set('Description', value)


    @property
    def payee_name(self) -> str:
        return self.get('Payee Name', '')

    @payee_name.setter
    def payee_name(self, value: str):
        self.set('Payee Name', value)

    @property
    def amount(self) -> float:
        return self.get('Amount', 0.0)

    @amount.setter
    def amount(self, value: float):
        self.set('Amount', value)

    @property
    def currency(self) -> str:
        return self.get('Currency', '')

    @currency.setter
    def currency(self, value: str):
        self.set('Currency', value)

    @property
    def category(self) -> str:
        return self.get('Category', '')

    @category.setter
    def category(self, value: str):
        self.set('Category', value)

    @property
    def is_transfer(self) -> Union[bool, str]:
        return self.get('Is Transfer', False)

    @is_transfer.setter
    def is_transfer(self, value: Union[bool, str]):
        self.set('Is Transfer', value)

    @property
    def notes(self) -> str:
        return self.get('Notes', '')

    @notes.setter
    def notes(self, value: str):
        self.set('Notes', value)

    def get_required_csv_row(self) -> Dict[str, Any]:
        return {
            'Unique Transaction ID': self.unique_transaction_id,
            'Unique Account ID': self.unique_account_id,
            'Account Name': self.account_name,
            'Date': self.date,
            'Description': self.description,
            'Payee Name': self.payee_name,
            'Amount': self.amount,
            'Currency': self.currency,
            'Category': self.category,
            'Is Transfer': self.is_transfer,
            'Notes': self.notes,
        }

class Account(BaseModel):
    CSV_FIELDS = [
        'Unique Account ID',
        'Account Name',
        'Account Number',
        'Currency',
        'Type',
        'Status',
        'Current Balance',
        'Created At',
        'Statement Balance',
        'Remaining Balance Due',
        'Payment Due Date'
    ]

    def __init__(self, raw_data: Dict[str, Any], unique_account_id: str):
        super().__init__(raw_data)
        self.unique_account_id = unique_account_id

    @property
    def unique_account_id(self) -> str:
        return self.get('Unique Account ID', '')

    @unique_account_id.setter
    def unique_account_id(self, value: str):
        self.set('Unique Account ID', value)

    @property
    def account_name(self) -> str:
        return self.get('Account Name', '')

    @account_name.setter
    def account_name(self, value: str):
        self.set('Account Name', value)

    @property
    def account_number(self) -> str:
        return self.get('Account Number', '')

    @account_number.setter
    def account_number(self, value: str):
        self.set('Account Number', value)

    @property
    def currency(self) -> str:
        return self.get('Currency', '')

    @currency.setter
    def currency(self, value: str):
        self.set('Currency', value)

    @property
    def type(self) -> str:
        return self.get('Type', '')

    @type.setter
    def type(self, value: str):
        self.set('Type', value)

    @property
    def is_liability(self) -> bool:
        """Check if the account is a liability (credit) account."""
        # Normalize type to check against Enum values
        t = self.type
        return t in [
            AccountType.CREDIT_CARD,
            AccountType.LINE_OF_CREDIT,
            AccountType.MORTGAGE,
            AccountType.LOAN
        ]

    @property
    def status(self) -> str:
        return self.get('Status', '')

    @status.setter
    def status(self, value: str):
        self.set('Status', value)

    @property
    def current_balance(self) -> float:
        val = self.get('Current Balance', 0.0)
        try:
            return float(val)
        except (ValueError, TypeError):
            return 0.0

    @current_balance.setter
    def current_balance(self, value: float):
        self.set('Current Balance', value)

    @property
    def created_at(self) -> str:
        return self.get('Created At', '')

    @created_at.setter
    def created_at(self, value: str):
        self.set('Created At', value)

    @property
    def statement_balance(self) -> float:
        val = self.get('Statement Balance', 0.0)
        try:
            return float(val)
        except (ValueError, TypeError):
            return 0.0

    @statement_balance.setter
    def statement_balance(self, value: float):
        self.set('Statement Balance', value)

    @property
    def remaining_balance_due(self) -> float:
        val = self.get('Remaining Balance Due', 0.0)
        try:
            return float(val)
        except (ValueError, TypeError):
            return 0.0

    @remaining_balance_due.setter
    def remaining_balance_due(self, value: float):
        self.set('Remaining Balance Due', value)

    @property
    def payment_due_date(self) -> str:
        return self.get('Payment Due Date', '')

    @payment_due_date.setter
    def payment_due_date(self, value: str):
        self.set('Payment Due Date', value)

    def get_required_csv_row(self) -> Dict[str, Any]:
        return {
            'Unique Account ID': self.unique_account_id,
            'Account Name': self.account_name,
            'Account Number': self.account_number,
            'Currency': self.currency,
            'Type': self.type,
            'Status': self.status,
            'Current Balance': self.current_balance,
            'Created At': self.created_at,
            'Statement Balance': self.statement_balance,
            'Remaining Balance Due': self.remaining_balance_due,
            'Payment Due Date': self.payment_due_date
        }