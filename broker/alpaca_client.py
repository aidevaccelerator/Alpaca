import logging
from datetime import datetime, timezone

import requests

from config import Config

log = logging.getLogger("alpaca_client")


class AlpacaClient:
    def __init__(self, config: Config):
        self.cfg = config
        self._session = requests.Session()
        self._session.headers.update({
            "APCA-API-KEY-ID": config.api_key,
            "APCA-API-SECRET-KEY": config.api_secret,
        })

    def _url(self, path: str) -> str:
        return f"{self.cfg.base_url}/v2{path}"

    def _data_url(self, path: str) -> str:
        return f"{self.cfg.data_url}/v2{path}"

    def _get(self, url: str) -> dict | list:
        r = self._session.get(url)
        r.raise_for_status()
        return r.json()

    def _post(self, url: str, json: dict) -> dict:
        r = self._session.post(url, json=json)
        r.raise_for_status()
        return r.json()

    def _delete(self, url: str) -> None:
        r = self._session.delete(url)
        r.raise_for_status()

    # -- Account --
    def get_account(self) -> dict:
        return self._get(self._url("/account"))

    # -- Clock --
    def get_clock(self) -> dict:
        return self._get(self._url("/clock"))

    def market_is_open(self) -> bool:
        return self.get_clock().get("is_open", False)

    # -- Positions --
    def get_positions(self) -> list[dict]:
        return self._get(self._url("/positions"))

    def get_position(self, symbol: str) -> dict | None:
        try:
            return self._get(self._url(f"/positions/{symbol}"))
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 404:
                return None
            raise

    # -- Orders --
    def place_order(
        self,
        symbol: str,
        qty: float,
        side: str,
        order_type: str = "market",
        time_in_force: str = "day",
        limit_price: float | None = None,
        stop_price: float | None = None,
    ) -> dict:
        body = {
            "symbol": symbol,
            "qty": str(qty),
            "side": side,
            "type": order_type,
            "time_in_force": time_in_force,
        }
        if limit_price:
            body["limit_price"] = str(limit_price)
        if stop_price:
            body["stop_price"] = str(stop_price)

        if not self.cfg.live_trading:
            log.info("DRY-RUN order: %s", body)
            return {"status": "dry-run", "id": None, **body}

        result = self._post(self._url("/orders"), body)
        log.info("Order placed: %s", result.get("id"))
        return result

    def cancel_all_orders(self) -> None:
        self._delete(self._url("/orders"))

    def get_orders(self, status: str = "open") -> list[dict]:
        return self._get(self._url(f"/orders?status={status}"))

    # -- Bars (historical data) --
    def get_bars(
        self,
        symbol: str,
        timeframe: str = "5Min",
        limit: int = 100,
    ) -> list[dict]:
        url = (
            f"{self.cfg.data_url}/v2/stocks/{symbol}/bars"
            f"?timeframe={timeframe}&limit={limit}&adjustment=raw"
        )
        return self._get(url).get("bars", [])

    # -- Asset info --
    def get_asset(self, symbol: str) -> dict:
        return self._get(self._url(f"/assets/{symbol}"))

    def is_tradable(self, symbol: str) -> bool:
        try:
            asset = self.get_asset(symbol)
            return asset.get("tradable", False)
        except Exception:
            return False
