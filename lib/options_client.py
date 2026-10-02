"""Options client for Bot #2 — separate Alpaca account (multi-leg spreads).

Extends AlpacaClient's transport (retry/backoff) with the options-specific
endpoints: option contracts, option chain snapshots (greeks), and mleg orders.

MLeg limit_price sign convention (official Create Order reference):
  positive = debit  (you pay)
  negative = credit (you receive)
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from lib.alpaca_client import AlpacaClient
from lib.config import OptionsConfig

log = logging.getLogger("options")

FILL_POLL_INTERVAL = 2.0
FILL_POLL_TIMEOUT = 120.0  # touch-priced mleg orders have filled at ~80s

OCC_FORMAT = "{root}{yymmdd}{cp}{strike:08d}"


class OptionsClient(AlpacaClient):
    def __init__(self, ocfg: OptionsConfig):
        super().__init__(ocfg)
        self.ocfg: OptionsConfig = ocfg

    def _v1beta1(self, path: str) -> str:
        return f"{self.ocfg.data_url}/v1beta1{path}"

    # ------------------------------------------------------------------
    # Contracts & chain data
    # ------------------------------------------------------------------
    def get_option_contracts(self, underlying: str, exp_gte: str, exp_lte: str,
                             opt_type: str = "put", limit: int = 1000) -> list[dict]:
        """GET /v2/options/contracts — filter by underlying + expiration window."""
        url = (
            f"{self._url('/options/contracts')}"
            f"?underlying_symbols={underlying}"
            f"&type={opt_type}"
            f"&expiration_date_gte={exp_gte}"
            f"&expiration_date_lte={exp_lte}"
            f"&status=active"
            f"&limit={limit}"
        )
        data = self._get_dict(url)
        return data.get("option_contracts") or []

    def get_chain(self, underlying: str, feed: str = "indicative") -> dict[str, dict]:
        """GET /v1beta1/options/snapshots/{underlying} — quotes + greeks + IV.

        Returns {contract_symbol: snapshot}. Note: paginated (100–1000/page);
        prefer get_snapshots_by_symbols() for targeted expirations.
        """
        url = f"{self._v1beta1(f'/options/snapshots/{underlying}')}?feed={feed}"
        data = self._get_dict(url)
        snapshots = data.get("snapshots") or {}
        if not isinstance(snapshots, dict):
            raise RuntimeError(f"Unexpected chain response for {underlying}: {type(snapshots)}")
        return snapshots

    def get_snapshots_by_symbols(self, symbols: list[str],
                                 feed: str = "indicative") -> dict[str, dict]:
        """Batch option snapshots for specific contracts (chunks of 100).

        The batch endpoint has no greeks pagination issues and is precise —
        use this instead of paging the full underlying chain.
        """
        result: dict[str, dict] = {}
        for i in range(0, len(symbols), 100):
            chunk = symbols[i:i + 100]
            url = (f"{self._v1beta1('/options/snapshots')}"
                   f"?symbols={','.join(chunk)}&feed={feed}")
            data = self._get_dict(url)
            snaps = data.get("snapshots") or {}
            if isinstance(snaps, dict):
                result.update(snaps)
        return result

    # ------------------------------------------------------------------
    # Multi-leg orders
    # ------------------------------------------------------------------
    def place_mleg_order(self, legs: list[dict], qty: int, limit_price: float,
                         time_in_force: str = "day") -> dict:
        """Place a multi-leg option order.

        legs: [{"symbol", "ratio_qty", "side", "position_intent"}, ...]
        limit_price: net price — negative for credit, positive for debit.
        """
        if len(legs) > 4:
            raise ValueError("mleg orders support max 4 legs")
        body = {
            "order_class": "mleg",
            "qty": str(int(qty)),
            "type": "limit",
            "limit_price": f"{limit_price:.2f}",
            "time_in_force": time_in_force,
            "legs": legs,
        }
        if not self.ocfg.live_trading:
            return {
                "status": "dry-run",
                "order_class": "mleg",
                "limit_price": body["limit_price"],
                "qty": body["qty"],
                "legs": legs,
            }
        return self._post(self._url("/orders"), body)

    def wait_for_fill(self, order_id: str, timeout: float = FILL_POLL_TIMEOUT) -> dict:
        elapsed = 0.0
        while elapsed < timeout:
            try:
                order = self.get_order(order_id)
            except Exception as e:
                return {"status": "error", "error": str(e)}
            status = order.get("status", "unknown")
            if status in ("filled", "canceled", "expired", "rejected"):
                return order
            time.sleep(FILL_POLL_INTERVAL)
            elapsed += FILL_POLL_INTERVAL
        return {"status": "timeout", "order_id": order_id}

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def occ_symbol(root: str, expiration_yymmdd: str, right: str, strike: float) -> str:
        """Build an OCC option symbol (e.g. SPY261030P00645000)."""
        return f"{root}{expiration_yymmdd}{right}{int(round(strike * 1000)):08d}"

    @staticmethod
    def parse_occ(symbol: str) -> dict | None:
        """Parse OCC symbol → {root, yymmdd, right, strike} or None."""
        import re
        m = re.match(r"^([A-Z]+?)(\d{6})([CP])(\d{8})$", symbol)
        if not m:
            return None
        return {
            "root": m.group(1),
            "yymmdd": m.group(2),
            "right": m.group(3),
            "strike": int(m.group(4)) / 1000.0,
        }

    @staticmethod
    def quote_mid(quote: dict) -> float | None:
        bid, ask = quote.get("bp"), quote.get("ap")
        if bid is None or ask is None:
            return None
        return round((float(bid) + float(ask)) / 2.0, 4)

    @staticmethod
    def now_iso() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")
