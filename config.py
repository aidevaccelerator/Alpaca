import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Config:
    api_key: str = field(default_factory=lambda: os.getenv("ALPACA_API_KEY", ""))
    api_secret: str = field(default_factory=lambda: os.getenv("ALPACA_SECRET_KEY", ""))
    base_url: str = field(default_factory=lambda: os.getenv(
        "ALPACA_BASE_URL",
        "https://paper-api.alpaca.markets",
    ))
    data_url: str = field(default_factory=lambda: os.getenv(
        "ALPACA_DATA_URL",
        "https://data.alpaca.markets",
    ))
    live_trading: bool = field(default_factory=lambda: os.getenv("ALPACA_LIVE_TRADE", "false").lower() == "true")

    poll_interval_seconds: int = int(os.getenv("POLL_INTERVAL", "60"))
    max_daily_drawdown_pct: float = float(os.getenv("MAX_DAILY_DRAWDOWN", "2.0"))
    max_position_pct: float = float(os.getenv("MAX_POSITION_PCT", "5.0"))
    max_leverage: float = float(os.getenv("MAX_LEVERAGE", "1.0"))

    symbols: list[str] = field(default_factory=lambda: os.getenv(
        "TRADE_SYMBOLS", "SPY,QQQ,TLT,GLD"
    ).split(","))

    log_dir: Path = Path(os.getenv("LOG_DIR", "logs"))
    data_dir: Path = Path(os.getenv("DATA_DIR", "data"))

    def __post_init__(self):
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)
