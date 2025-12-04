import os
from pathlib import Path
from typing import Optional, Dict, Any
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

class BankConfig(BaseSettings):
    """Configuration specific to a bank."""
    enabled: bool = True
    invert_credit_transactions: bool = False
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

    model_config = SettingsConfigDict(
        env_prefix='LEDGER_FETCH_',
        env_nested_delimiter='__',
        extra='ignore'
        # We will load the config file manually in a factory method or 
        # let the user pass it. For now, we'll keep it simple.
        # To support yaml/toml automatically, we might need extra dependencies 
        # and a custom source.
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
