"""
Configuration Management Module

This module defines the configuration schema for the Ledger Fetch application
using Pydantic. It handles:
1.  Loading configuration from YAML files (e.g., `config.yaml`).
2.  Overriding settings via environment variables (prefixed with `LEDGER_FETCH_`).
3.  Defining default values for all settings.
4.  Providing typed configuration objects for the rest of the application.
"""

from typing import List, Optional, Dict, Any
from pathlib import Path
from pydantic import Field, BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict
import os
import yaml
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

DEFAULT_DAYS_TO_FETCH = 1095 # 3 years

class AccountConfig(BaseModel):
    """Configuration specific to an account."""
    id: str = Field(..., description="Unique Account ID")
    invert_credit_transactions: Optional[bool] = None

class BankConfig(BaseModel):
    """Configuration specific to a bank."""
    enabled: bool = True
    invert_credit_transactions: bool = False
    days_to_fetch: int = DEFAULT_DAYS_TO_FETCH
    accounts: List[AccountConfig] = Field(default_factory=list)

class BrowserConfig(BaseModel):
    """Configuration for Browser Automation."""
    headless: bool = Field(default=False)
    timeout: int = Field(default=30000)
    profile_path: Path = Field(
        default=Path.home() / ".ledger_fetch_chrome_profile",
        description="Path to the Chrome user profile directory"
    )

class LedgerFetchConfig(BaseModel):
    """Configuration for Ledger Fetch core."""
    transactions_path: Path = Field(
        default=Path("./transactions"),
        description="Directory where downloaded transactions will be saved"
    )
    payee_rules_path: Path = Field(
        default=Path("config/payee_rules"),
        description="Path to the payee normalization rules file or directory"
    )
    debug: bool = Field(
        default=False,
        description="Enable debug mode"
    )
    since_month: Optional[str] = Field(
        default=None,
        description="Optional month to fetch transactions from (YYYY-MM)"
    )
    banks: Dict[str, BankConfig] = Field(default_factory=dict)

    # Allow arbitrary bank keys if they match BankConfig structure, 
    # but we use 'banks' dict for cleaner structure.
    # For backward compatibility or ease, we can also map fields.

class ActualConfig(BaseModel):
    """Configuration for Actual Budget."""
    server_url: Optional[str] = None
    password: Optional[str] = None
    sync_id: Optional[str] = None

class AIConfig(BaseModel):
    """Configuration for AI features."""
    model: str = "gemini-pro"
    path: str
    api_key: Optional[str] = None

class Config(BaseSettings):
    """
    Global configuration for the ledger-fetch application.
    """
    browser: BrowserConfig = Field(default_factory=BrowserConfig)
    ledger_fetch: LedgerFetchConfig = Field(default_factory=LedgerFetchConfig)
    actual: Optional[ActualConfig] = None
    ai: Optional[AIConfig] = None

    model_config = SettingsConfigDict(
        env_prefix='LEDGER_FETCH_',
        env_nested_delimiter='__',
        extra='ignore',
        env_file='.env',
        env_file_encoding='utf-8'
    )

    @classmethod
    def load(cls, config_path: Optional[Path] = None) -> "Config":
        """
        Load configuration, optionally from a YAML file.
        """
        # Search paths for config file
        search_paths = [
            config_path,
            Path("config/config.yaml"),
            Path("config/config.yml"),
            Path("config.yaml"),
            Path("config.yml"),
            Path.home() / ".ledger_fetch" / "config.yaml",
            Path.home() / ".ledger_fetch" / "config.yml",
        ]

        config_data: Dict[str, Any] = {}
        
        # Try to find and load a config file
        found_path = None
        for path in search_paths:
            if path and path.exists() and path.is_file():
                found_path = path.resolve()
                break
        
        if found_path:
            import yaml
            try:
                with open(found_path, 'r') as f:
                    file_data = yaml.safe_load(f)
                    if file_data:
                        # Handle relative paths in ledger_fetch
                        if 'ledger_fetch' in file_data and 'transactions_path' in file_data['ledger_fetch']:
                            path_val = Path(file_data['ledger_fetch']['transactions_path'])
                            if not path_val.is_absolute():
                                # Make it absolute relative to the config file location
                                file_data['ledger_fetch']['transactions_path'] = found_path.parent / path_val
                        
                        config_data = file_data
                print(f"Loaded configuration from: {found_path}")
            except ImportError:
                print("Warning: PyYAML not installed. Skipping config file loading.")
            except Exception as e:
                print(f"Warning: Error loading config file {found_path}: {e}")
        else:
            print("No config file found. Using default configuration.")

        # Helper to load from env if not in config
        config = cls(**config_data)
        
        # Manually check for specific env vars mapping if Pydantic settings didn't pick them up 
        # (Pydantic Settings with env_file should handle it, but we have specific names in .env)
        if not config.actual:
            config.actual = ActualConfig()
            
        if os.getenv("ACTUAL_SERVER_URL") and not config.actual.server_url:
            config.actual.server_url = os.getenv("ACTUAL_SERVER_URL")
        if os.getenv("ACTUAL_PASSWORD") and not config.actual.password:
            config.actual.password = os.getenv("ACTUAL_PASSWORD")
        if os.getenv("ACTUAL_SYNC_ID") and not config.actual.sync_id:
            config.actual.sync_id = os.getenv("ACTUAL_SYNC_ID")

        if not config.ai:
             config.ai = AIConfig(path="P:\\dev\\ai-agents\\budget-ai", model="gemini-2.0-flash-exp") # Default defaults
        
        if os.getenv("GEMINI_API_KEY") and not config.ai.api_key:
            config.ai.api_key = os.getenv("GEMINI_API_KEY")

        return config

# Global config instance
settings = Config.load()
