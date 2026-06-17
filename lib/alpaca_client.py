import logging

import requests

from lib.config import Config

log = logging.getLogger("alpaca")


class AlpacaClient:
    def __init__(self, config: Config):
        self.cfg = config
        self._s = requests.Session()
        self._s.headers.update({
            "APCA-API-KEY-ID": config.api_key,
            "APCA-API-SECRET-KEY": config.api_secret,
        })

    def _url(self, path: str) -> str:
        return f"{self.cfg.base_url}/v2{path}"

    def _data_url(self, path: str) -> str:
        return f"{self.cfg.data_url}/v2{path}"

    def _get(self, url: str) -> dict | list:
        r = self._s.get(url)
        r.raise_for_status()
        return r.json()

    def _post(self, url: str, json: dict) -> dict:
        r = self._s.post(url, json=json)
        r.raise_for_status()
        return r.json()

    def _delete(self, url: str) -> None:
        r = self._s.delete(url)
        r.raise_for_status()

    def get_account(self) -> dict:
        return self._get(self._url("/account"))

    def get_clock(self) -> dict:
        return self._get(self._url("/clock"))

    def get_positions(self) -> list[dict]:
        return self._get(self._url("/positions"))

    def get_bars(self, symbol: str, timeframe: str = "5Min", limit: int = 50) -> list[dict]:
        url = f"{self.cfg.data_url}/v2/stocks/{symbol}/bars?timeframe={timeframe}&limit={limit}&adjustment=raw"
        return self._get(url).get("bars", [])

    def get_asset(self, symbol: str) -> dict:
        return self._get(self._url(f"/assets/{symbol}"))

    def place_order(self, symbol: str, qty: float, side: str, order_type: str = "market", time_in_force: str = "day") -> dict:
        body = {"symbol": symbol, "qty": str(qty), "side": side, "type": order_type, "time_in_force": time_in_force}
        if not self.cfg.live_trading:
            return {"status": "dry-run", "symbol": symbol, "qty": qty, "side": side}
        return self._post(self._url("/orders"), body)

    def cancel_all_orders(self) -> None:
        self._delete(self._url("/orders"))

    def get_orders(self, status: str = "open") -> list[dict]:
        return self._get(self._url(f"/orders?status={status}"))
