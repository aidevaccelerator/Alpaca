import logging
import time

import requests

from lib.config import Config

log = logging.getLogger("alpaca")

MAX_RETRIES = 3
RETRY_BACKOFF = 1.0


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

    def _request_with_retry(self, method: str, url: str, **kwargs) -> requests.Response:
        last_exc = None
        for attempt in range(MAX_RETRIES):
            try:
                r = self._s.request(method, url, **kwargs)
                if r.status_code == 429:
                    wait = float(r.headers.get("Retry-After", RETRY_BACKOFF * (2 ** attempt)))
                    log.warning("Rate limited, waiting %.1fs (attempt %d/%d)", wait, attempt + 1, MAX_RETRIES)
                    time.sleep(wait)
                    continue
                r.raise_for_status()
                return r
            except requests.exceptions.ConnectionError as e:
                last_exc = e
                wait = RETRY_BACKOFF * (2 ** attempt)
                log.warning("Connection error, retrying in %.1fs: %s", wait, e)
                time.sleep(wait)
            except requests.exceptions.Timeout as e:
                last_exc = e
                wait = RETRY_BACKOFF * (2 ** attempt)
                log.warning("Timeout, retrying in %.1fs: %s", wait, e)
                time.sleep(wait)
        raise last_exc or RuntimeError(f"Failed after {MAX_RETRIES} attempts")

    def _get(self, url: str) -> dict | list:
        r = self._request_with_retry("GET", url)
        return r.json()

    def _get_dict(self, url: str) -> dict:
        r = self._request_with_retry("GET", url)
        return r.json()

    def _get_list(self, url: str) -> list[dict]:
        r = self._request_with_retry("GET", url)
        return r.json()

    def _post(self, url: str, json: dict) -> dict:
        r = self._request_with_retry("POST", url, json=json)
        return r.json()

    def _delete(self, url: str) -> None:
        self._request_with_retry("DELETE", url)

    def get_account(self) -> dict:
        return self._get_dict(self._url("/account"))

    def get_clock(self) -> dict:
        return self._get_dict(self._url("/clock"))

    def get_positions(self) -> list[dict]:
        return self._get_list(self._url("/positions"))

    def get_bars(self, symbol: str, timeframe: str = "5Min", limit: int = 50, feed: str = "iex",
                 start: str | None = None, end: str | None = None) -> list[dict]:
        from datetime import datetime, timedelta
        if not start:
            if timeframe.startswith("1Min"):
                start = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%dT00:00:00Z")
            elif timeframe.startswith("5Min") or timeframe.startswith("15Min"):
                start = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%dT00:00:00Z")
            else:
                start = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%dT00:00:00Z")
        if not end:
            end = datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ")
        url = f"{self.cfg.data_url}/v2/stocks/{symbol}/bars?timeframe={timeframe}&start={start}&end={end}&limit={limit}&adjustment=raw&feed={feed}"
        data = self._get_dict(url)
        return data.get("bars") or []  # type: ignore[return-value]

    def get_asset(self, symbol: str) -> dict:
        return self._get_dict(self._url(f"/assets/{symbol}"))

    def get_snapshots(self, symbols: list[str]) -> dict:
        joined = ",".join(symbols)
        return self._get_dict(f"{self.cfg.data_url}/v2/stocks/snapshots?symbols={joined}")

    def get_quote(self, symbol: str) -> dict:
        return self._get_dict(f"{self.cfg.data_url}/v2/stocks/{symbol}/quotes/latest")

    def place_order(self, symbol: str, qty: float, side: str,
                    order_type: str = "market", time_in_force: str = "day",
                    limit_price: str | None = None,
                    stop_price: str | None = None) -> dict:
        body: dict = {
            "symbol": symbol,
            "qty": str(qty),
            "side": side,
            "type": order_type,
            "time_in_force": time_in_force,
        }
        if limit_price:
            body["limit_price"] = limit_price
        if stop_price:
            body["stop_price"] = stop_price
        if not self.cfg.live_trading:
            return {"status": "dry-run", "symbol": symbol, "qty": qty, "side": side}
        return self._post(self._url("/orders"), body)

    def place_bracket_order(
        self,
        symbol: str,
        qty: float,
        side: str,
        limit_price: str | None = None,
        take_profit_price: str | None = None,
        stop_loss_price: str | None = None,
        stop_loss_limit_price: str | None = None,
    ) -> dict:
        order_class = "bracket"
        body: dict = {
            "symbol": symbol,
            "qty": str(qty),
            "side": side,
            "type": "limit" if limit_price else "market",
            "time_in_force": "day",
            "order_class": order_class,
        }
        if limit_price:
            body["limit_price"] = limit_price
        if take_profit_price:
            body["take_profit"] = {"limit_price": take_profit_price}
        if stop_loss_price:
            sl: dict = {"stop_price": stop_loss_price}
            if stop_loss_limit_price:
                sl["limit_price"] = stop_loss_limit_price
            body["stop_loss"] = sl
        if not self.cfg.live_trading:
            return {"status": "dry-run", "symbol": symbol, "qty": qty, "side": side}
        return self._post(self._url("/orders"), body)

    def get_order(self, order_id: str) -> dict:
        return self._get_dict(self._url(f"/orders/{order_id}"))

    def cancel_all_orders(self) -> None:
        self._delete(self._url("/orders"))

    def get_orders(self, status: str = "open") -> list[dict]:
        return self._get_list(self._url(f"/orders?status={status}"))

    def get_news(self, symbols: list[str] | None = None, limit: int = 20) -> list[dict]:
        url = f"{self.cfg.data_url}/v1beta1/news?limit={limit}"
        if symbols:
            url += f"&symbols={','.join(symbols)}"
        data = self._get_dict(url)
        return data.get("news") or []  # type: ignore[return-value]

    def get_market_news(self, limit: int = 20) -> list[dict]:
        url = f"{self.cfg.data_url}/v1beta1/news?limit={limit}"
        data = self._get_dict(url)
        return data.get("news") or []  # type: ignore[return-value]
