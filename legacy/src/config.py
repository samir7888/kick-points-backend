"""Configuration management."""

import json
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Config:
    """Application configuration."""

    channels: list[str]
    messages: list[str]
    authorization: str
    wait_times: dict[str, int | dict[str, int]]
    web_dashboard: Optional[dict] = field(default=None)
    prediction: Optional[dict] = field(default=None)
    telegram: Optional[dict] = field(default=None)

    @classmethod
    def load(cls, path: str = "config.json") -> "Config":
        """Load configuration from JSON file or CONFIG_JSON environment variable."""
        import os

        # First check for CONFIG_JSON environment variable
        if os.environ.get("CONFIG_JSON"):
            data = json.loads(os.environ["CONFIG_JSON"])
            return cls(**data)

        # Check if config file exists
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return cls(**data)

        # If config.json doesn't exist, try config.example.json as fallback
        if os.path.exists("config.example.json"):
            print("Warning: config.json not found, using config.example.json")
            with open("config.example.json", "r", encoding="utf-8") as f:
                data = json.load(f)
            return cls(**data)

        # If no config file exists, raise a helpful error
        raise FileNotFoundError(
            f"Configuration file not found. Please create '{path}' or set CONFIG_JSON environment variable."
        )
