from typing import List, Optional, Dict, Any
from pathlib import Path
from pydantic import Field, BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict
import os
import yaml

DEFAULT_DAYS_TO_FETCH = 1095 # 3 years

class BankConfig(BaseModel):
    """Configuration specific to a bank."""
    enabled: bool = True
    invert_credit_transactions: bool = False
    days_to_fetch: int = DEFAULT_DAYS_TO_FETCH
    # Add other bank-specific settings here if needed

class Config(BaseSettings):
    """
    Global configuration for the ledger-fetch application.
    
    This class manages all configuration settings, including browser options,
    output directories, and bank-specific settings. It supports loading configuration
    from:
    1.  Environment variables (prefixed with LEDGER_FETCH_)
    2.  Configuration files (YAML/TOML)
    3.  Default values defined in this class
    """
    
    # Core settings
    browser_profile_path: Path = Field(
        default=Path.home() / ".ledger_fetch_chrome_profile",
        description="Path to the Chrome user profile directory"
    )
    output_dir: Path = Field(
        default=Path("./transactions"),
        description="Directory where downloaded transactions will be saved"
    )
    headless: bool = Field(
        default=False,
        description="Run browser in headless mode"
    )
    timeout: int = Field(
        default=30000,
        description="Default timeout for browser actions in milliseconds"
    )
    payee_rules_path: Path = Field(
        default=Path("payee_rules.yaml"),
        description="Path to the payee normalization rules file"
    )
    debug: bool = Field(
        default=False,
        description="Enable debug mode (HAR recording, verbose logging, pause on error)"
    )
    
    # Bank specific configs
    rbc: BankConfig = Field(default_factory=BankConfig)
    wealthsimple: BankConfig = Field(default_factory=BankConfig)
    amex: BankConfig = Field(default_factory=BankConfig)
    canadiantire: BankConfig = Field(default_factory=BankConfig)
    bmo: BankConfig = Field(default_factory=BankConfig)
    cibc: BankConfig = Field(default_factory=BankConfig)

    model_config = SettingsConfigDict(
        env_prefix='LEDGER_FETCH_',
        env_nested_delimiter='__',
        extra='ignore'
    )

    @classmethod
    def load(cls, config_path: Optional[Path] = None) -> "Config":
        """
        Load configuration, optionally from a YAML file.
        """
        # Search paths for config file
        search_paths = [
            config_path,
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
                found_path = path
                break
        
        if found_path:
            import yaml
            try:
                with open(found_path, 'r') as f:
                    file_data = yaml.safe_load(f)
                    if file_data:
                        config_data = file_data
                print(f"Loaded configuration from: {found_path.resolve()}")
            except ImportError:
                print("Warning: PyYAML not installed. Skipping config file loading.")
            except Exception as e:
                print(f"Warning: Error loading config file {found_path}: {e}")
        else:
            print("No config file found. Using default configuration.")

        # Pydantic will merge init kwargs (file_data) with env vars and defaults
        return cls(**config_data)

# Global config instance
settings = Config.load()
