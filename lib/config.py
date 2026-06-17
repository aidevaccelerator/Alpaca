import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Config:
    api_key: str = field(default_factory=lambda: os.getenv("ALPACA_API_KEY", ""))
    api_secret: str = field(default_factory=lambda: os.getenv("ALPACA_SECRET_KEY", ""))
    base_url: str = field(default_factory=lambda: os.getenv(
        "ALPACA_BASE_URL", "https://paper-api.alpaca.markets",
    ))
    data_url: str = field(default_factory=lambda: os.getenv(
        "ALPACA_DATA_URL", "https://data.alpaca.markets",
    ))
    live_trading: bool = field(default_factory=lambda: os.getenv("ALPACA_LIVE_TRADE", "false").lower() == "true")
    reports_dir: Path = Path("reports")
