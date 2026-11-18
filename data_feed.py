import logging
from datetime import datetime, timezone
from typing import Optional, Dict, List
import pandas as pd
import MetaTrader5 as mt5

logger = logging.getLogger("DataFeed")

TF_MAP = {
    "M1": mt5.TIMEFRAME_M1,
    "M5": mt5.TIMEFRAME_M5,
    "M15": mt5.TIMEFRAME_M15,
    "M30": mt5.TIMEFRAME_M30,
    "H1": mt5.TIMEFRAME_H1,
    "H4": mt5.TIMEFRAME_H4,
    "D1": mt5.TIMEFRAME_D1,
}

class MT5DataFeed:
    def __init__(self, symbols: List[str]):
        self.symbols = symbols
        self._connected = False

    def connect(self) -> bool:
        """Initialize connection to local MT5 terminal and subscribe to symbols."""
        if not mt5.initialize():
            logger.error(f"MT5 initialization failed: {mt5.last_error()}")
            return False

        term_info = mt5.terminal_info()
        if not term_info.trade_allowed:
            logger.warning("[WARNING] MT5 Algo Trading is currently DISABLED in the terminal. Click the 'Algo Trading' button in MT5 toolbar to allow order execution.")
        else:
            logger.info("[OK] MT5 Algo Trading is ENABLED.")

        subscribed = []
        for sym in self.symbols:
            if not mt5.symbol_select(sym, True):
                # Check without 'm' suffix fallback
                fallback = sym.rstrip("m")
                if mt5.symbol_select(fallback, True):
                    logger.info(f"Subscribed to fallback symbol: {fallback}")
                    subscribed.append(fallback)
                else:
                    logger.warning(f"Could not subscribe to symbol: {sym}")
            else:
                info = mt5.symbol_info(sym)
                logger.info(f"Subscribed: {sym} (Digits: {info.digits}, Point: {info.point}, Spread: {info.spread})")
                subscribed.append(sym)

        self.symbols = subscribed
        self._connected = True
        return len(self.symbols) > 0

    def get_symbol_info(self, symbol: str):
        """Retrieve latest symbol specs & spread."""
        return mt5.symbol_info(symbol)

    def get_latest_tick(self, symbol: str) -> Optional[Dict]:
        """Fetch live tick with bid, ask, and spread for the given symbol."""
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            return None

        sym_info = self.get_symbol_info(symbol)
        point = sym_info.point if sym_info else 0.001
        spread_points = (tick.ask - tick.bid) / point

        return {
            "symbol": symbol,
            "time": datetime.fromtimestamp(tick.time, tz=timezone.utc),
            "bid": tick.bid,
            "ask": tick.ask,
            "mid": (tick.bid + tick.ask) / 2.0,
            "point": point,
            "digits": sym_info.digits if sym_info else 3,
            "spread_points": round(spread_points, 1),
            "spread_quote": round(tick.ask - tick.bid, sym_info.digits if sym_info else 3),
            "volume": tick.volume,
        }

    def get_historical_bars(self, symbol: str, timeframe: str = "M1", count: int = 200) -> Optional[pd.DataFrame]:
        """Retrieve recent OHLCV bars for the specified symbol & timeframe."""
        tf = TF_MAP.get(timeframe.upper())
        if tf is None:
            raise ValueError(f"Unsupported timeframe: {timeframe}")

        rates = mt5.copy_rates_from_pos(symbol, tf, 0, count)
        if rates is None or len(rates) == 0:
            return None

        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
        df.set_index("time", inplace=True)
        df.rename(columns={"tick_volume": "volume"}, inplace=True)
        return df[["open", "high", "low", "close", "volume", "spread"]]

    def disconnect(self):
        """Close MT5 connection."""
        mt5.shutdown()
        self._connected = False
        logger.info("MT5 disconnected.")
