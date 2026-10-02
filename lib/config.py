import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


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


@dataclass
class OptionsConfig:
    """Bot #2 — separate Alpaca paper account for options income bot."""
    api_key: str = field(default_factory=lambda: os.getenv("ALPACA_OPTIONS_API_KEY", ""))
    api_secret: str = field(default_factory=lambda: os.getenv("ALPACA_OPTIONS_SECRET_KEY", ""))
    base_url: str = field(default_factory=lambda: os.getenv(
        "ALPACA_BASE_URL", "https://paper-api.alpaca.markets",
    ))
    data_url: str = field(default_factory=lambda: os.getenv(
        "ALPACA_DATA_URL", "https://data.alpaca.markets",
    ))
    live_trading: bool = field(default_factory=lambda: os.getenv("ALPACA_OPTIONS_LIVE_TRADE", "false").lower() == "true")
    universe: tuple[str, ...] = field(default_factory=lambda: tuple(
        s.strip().upper() for s in os.getenv("ALPACA_OPTIONS_UNIVERSE", "SPY,QQQ,IWM").split(",") if s.strip()
    ))
    reports_dir: Path = Path("reports")

    @property
    def configured(self) -> bool:
        """True when real keys have been provided (not placeholders)."""
        return bool(self.api_key) and bool(self.api_secret) and not self.api_key.startswith("PLACEHOLDER")
