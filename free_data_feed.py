"""
Free, MT5-independent market data feed (works on Ubuntu / Linux).

Primary source : TradingView public WebSocket (guest token, no API key)
Fallback source: Yahoo Finance chart REST API (no API key)

Exposes the same interface the engine used with MT5DataFeed:
    connect(), get_latest_tick(symbol), get_historical_bars(symbol, timeframe, count), disconnect()
"""
import json
import logging
import random
import re
import string
import threading
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import pandas as pd
import requests
import websocket

from config import AssetSettings

logger = logging.getLogger("FreeDataFeed")

TV_WS_URL = "wss://data.tradingview.com/socket.io/websocket"
TV_HEADERS = ["Origin: https://www.tradingview.com"]
TV_TF_MAP = {"M1": "1", "M5": "5", "M15": "15", "M30": "30", "H1": "60", "H4": "240", "D1": "1D"}

YF_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
YF_TF_MAP = {"M1": ("1m", "1d"), "M5": ("5m", "5d"), "M15": ("15m", "5d"),
             "M30": ("30m", "1mo"), "H1": ("60m", "1mo"), "D1": ("1d", "1y")}
YF_CACHE_SECONDS = {"M1": 5, "M5": 20, "M15": 45}
YF_HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"}

TV_BARS_PER_SERIES = 300
TV_STALE_SECONDS = 120  # if no TV data for this long, fall back to Yahoo


def _rand_id(prefix: str) -> str:
    return prefix + "".join(random.choices(string.ascii_lowercase, k=12))


class FreeDataFeed:
    def __init__(self, symbols: List[str], assets: Dict[str, AssetSettings],
                 timeframes: Tuple[str, ...] = ("M1", "M15"), source: str = "tradingview"):
        self.symbols = symbols
        self.assets = assets
        self.timeframes = timeframes
        self.source = source.lower()

        self._lock = threading.Lock()
        # bars[(symbol, tf)] = {unix_ts: [o, h, l, c, v]}
        self._bars: Dict[Tuple[str, str], Dict[int, List[float]]] = {}
        self._last_bar_update: Dict[Tuple[str, str], float] = {}
        # quotes[symbol] = {"bid":..., "ask":..., "lp":..., "ts":...}
        self._quotes: Dict[str, Dict] = {}
        self._yf_cache: Dict[Tuple[str, str], Tuple[float, pd.DataFrame]] = {}

        self._series_map: Dict[str, Tuple[str, str]] = {}
        self._tv_to_symbol: Dict[str, str] = {}
        self._ws: Optional[websocket.WebSocketApp] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._connected = False

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    def connect(self) -> bool:
        valid = [s for s in self.symbols if s in self.assets]
        for s in set(self.symbols) - set(valid):
            logger.warning(f"No AssetSettings for {s}; skipping.")
        self.symbols = valid
        if not self.symbols:
            return False

        if self.source == "tradingview":
            self._running = True
            self._thread = threading.Thread(target=self._ws_loop, name="TradingViewWS", daemon=True)
            self._thread.start()
            # Wait (max ~15s) for initial history to arrive
            deadline = time.time() + 15
            while time.time() < deadline:
                with self._lock:
                    ready = all(len(self._bars.get((s, tf), {})) > 0
                                for s in self.symbols for tf in self.timeframes)
                if ready:
                    break
                time.sleep(0.5)
            with self._lock:
                for s in self.symbols:
                    for tf in self.timeframes:
                        n = len(self._bars.get((s, tf), {}))
                        logger.info(f"[TradingView] {s} {tf}: {n} bars loaded"
                                    + ("" if n else " (will use Yahoo fallback)"))
        else:
            logger.info("Using Yahoo Finance polling as the data source.")

        self._connected = True
        return True

    def disconnect(self):
        self._running = False
        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass
        self._connected = False
        logger.info("Data feed disconnected.")

    # ------------------------------------------------------------------ #
    # Public API (MT5DataFeed-compatible)
    # ------------------------------------------------------------------ #
    def get_latest_tick(self, symbol: str) -> Optional[Dict]:
        asset = self.assets.get(symbol)
        if not asset:
            return None
        point, digits = asset.point, asset.digits

        bid = ask = last = None
        ts = time.time()
        with self._lock:
            q = self._quotes.get(symbol)
            if q and time.time() - q.get("ts", 0) < TV_STALE_SECONDS:
                bid, ask, last, ts = q.get("bid"), q.get("ask"), q.get("lp"), q["ts"]

        if last is None and bid is None:
            df = self.get_historical_bars(symbol, "M1", count=5)
            if df is None or df.empty:
                return None
            last = float(df["close"].iloc[-1])

        if bid is None or ask is None:
            bid = ask = last  # free feeds often don't expose bid/ask -> spread unknown (0)
        mid = (bid + ask) / 2.0
        spread_quote = max(ask - bid, 0.0)

        return {
            "symbol": symbol,
            "time": datetime.fromtimestamp(ts, tz=timezone.utc),
            "bid": bid,
            "ask": ask,
            "mid": mid,
            "point": point,
            "digits": digits,
            "spread_points": round(spread_quote / point, 1),
            "spread_quote": round(spread_quote, digits),
            "volume": 0,
        }

    def get_historical_bars(self, symbol: str, timeframe: str = "M1", count: int = 200) -> Optional[pd.DataFrame]:
        tf = timeframe.upper()
        key = (symbol, tf)

        if self.source == "tradingview":
            with self._lock:
                bars = dict(self._bars.get(key, {}))
                last_upd = self._last_bar_update.get(key, 0)
            if bars and time.time() - last_upd < TV_STALE_SECONDS * 5:
                return self._bars_to_df(bars, count)

        return self._yahoo_bars(symbol, tf, count)

    # ------------------------------------------------------------------ #
    # TradingView WebSocket
    # ------------------------------------------------------------------ #
    @staticmethod
    def _frame(func: str, params: list) -> str:
        msg = json.dumps({"m": func, "p": params}, separators=(",", ":"))
        return f"~m~{len(msg)}~m~{msg}"

    def _send(self, ws, func: str, params: list):
        ws.send(self._frame(func, params))

    def _ws_loop(self):
        backoff = 2
        while self._running:
            self._ws = websocket.WebSocketApp(
                TV_WS_URL,
                header=TV_HEADERS,
                on_open=self._on_open,
                on_message=self._on_message,
                on_error=lambda ws, e: logger.warning(f"[TradingView] WS error: {e}"),
                on_close=lambda ws, c, m: logger.info(f"[TradingView] WS closed ({c})"),
            )
            self._ws.run_forever(ping_interval=0)
            if not self._running:
                break
            logger.info(f"[TradingView] Reconnecting in {backoff}s...")
            time.sleep(backoff)
            backoff = min(backoff * 2, 60)

    def _on_open(self, ws):
        logger.info("[TradingView] WebSocket connected (guest mode).")
        self._series_map.clear()
        self._tv_to_symbol.clear()

        self._send(ws, "set_auth_token", ["unauthorized_user_token"])

        # Quote session (live last price / bid / ask)
        qs = _rand_id("qs_")
        self._send(ws, "quote_create_session", [qs])
        self._send(ws, "quote_set_fields", [qs, "lp", "bid", "ask", "ch", "chp", "volume"])

        # Guest accounts allow only 1 series per chart session -> one session per (symbol, tf)
        n = 0
        for sym in self.symbols:
            asset = self.assets[sym]
            tv_sym = asset.tv_symbol
            if not tv_sym:
                continue
            self._tv_to_symbol[tv_sym] = sym
            self._send(ws, "quote_add_symbols", [qs, tv_sym])
            for tf in self.timeframes:
                n += 1
                cs = _rand_id("cs_")
                self._send(ws, "chart_create_session", [cs, ""])
                sym_ref, series_id = f"sds_sym_{n}", f"sds_{n}"
                self._series_map[series_id] = (sym, tf)
                self._send(ws, "resolve_symbol",
                           [cs, sym_ref, "=" + json.dumps({"symbol": tv_sym, "adjustment": "splits"})])
                self._send(ws, "create_series",
                           [cs, series_id, f"s{n}", sym_ref, TV_TF_MAP[tf], TV_BARS_PER_SERIES, ""])

    def _on_message(self, ws, raw: str):
        for payload in re.split(r"~m~\d+~m~", raw):
            if not payload:
                continue
            if payload.startswith("~h~"):  # heartbeat -> echo back
                ws.send(f"~m~{len(payload)}~m~{payload}")
                continue
            try:
                msg = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if not isinstance(msg, dict):
                continue
            m, p = msg.get("m"), msg.get("p", [])
            if m in ("timescale_update", "du") and len(p) > 1:
                self._handle_series_data(p[1])
            elif m == "qsd" and len(p) > 1:
                self._handle_quote(p[1])
            elif m in ("symbol_error", "series_error", "critical_error", "protocol_error"):
                logger.warning(f"[TradingView] {m}: {p}")

    def _handle_series_data(self, data: dict):
        now = time.time()
        with self._lock:
            for series_id, content in data.items():
                key = self._series_map.get(series_id)
                if not key or not isinstance(content, dict):
                    continue
                store = self._bars.setdefault(key, {})
                for bar in content.get("s", []):
                    v = bar.get("v", [])
                    if len(v) >= 5:
                        store[int(v[0])] = [float(v[1]), float(v[2]), float(v[3]), float(v[4]),
                                            float(v[5]) if len(v) > 5 else 0.0]
                # cap memory
                if len(store) > TV_BARS_PER_SERIES * 2:
                    for t in sorted(store)[: len(store) - TV_BARS_PER_SERIES]:
                        del store[t]
                self._last_bar_update[key] = now

    def _handle_quote(self, q: dict):
        sym = self._tv_to_symbol.get(q.get("n", ""))
        if not sym or q.get("s") != "ok":
            return
        vals = q.get("v", {})
        with self._lock:
            cur = self._quotes.setdefault(sym, {})
            for f in ("lp", "bid", "ask"):
                if vals.get(f) is not None:
                    cur[f] = float(vals[f])
            cur["ts"] = time.time()

    @staticmethod
    def _bars_to_df(bars: Dict[int, List[float]], count: int) -> pd.DataFrame:
        times = sorted(bars)[-count:]
        df = pd.DataFrame([bars[t] for t in times], columns=["open", "high", "low", "close", "volume"],
                          index=pd.to_datetime(times, unit="s", utc=True))
        df.index.name = "time"
        df["spread"] = 0
        return df

    # ------------------------------------------------------------------ #
    # Yahoo Finance fallback
    # ------------------------------------------------------------------ #
    def _yahoo_bars(self, symbol: str, tf: str, count: int) -> Optional[pd.DataFrame]:
        asset = self.assets.get(symbol)
        if not asset or not asset.yf_symbol or tf not in YF_TF_MAP:
            return None
        key = (symbol, tf)
        cached = self._yf_cache.get(key)
        if cached and time.time() - cached[0] < YF_CACHE_SECONDS.get(tf, 60):
            return cached[1].tail(count)

        interval, rng = YF_TF_MAP[tf]
        try:
            r = requests.get(YF_URL.format(symbol=asset.yf_symbol),
                             params={"interval": interval, "range": rng},
                             headers=YF_HEADERS, timeout=10)
            r.raise_for_status()
            res = r.json()["chart"]["result"][0]
            q = res["indicators"]["quote"][0]
            df = pd.DataFrame({
                "open": q["open"], "high": q["high"], "low": q["low"],
                "close": q["close"], "volume": q.get("volume") or [0] * len(res["timestamp"]),
            }, index=pd.to_datetime(res["timestamp"], unit="s", utc=True)).dropna(subset=["open", "high", "low", "close"])
            df.index.name = "time"
            df["volume"] = df["volume"].fillna(0)
            df["spread"] = 0
            self._yf_cache[key] = (time.time(), df)
            return df.tail(count)
        except Exception as e:
            logger.warning(f"[Yahoo] Failed to fetch {asset.yf_symbol} {tf}: {e}")
            return cached[1].tail(count) if cached else None
