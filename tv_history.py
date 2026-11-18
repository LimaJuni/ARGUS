"""
One-shot multi-timeframe history fetcher (TradingView public WebSocket, guest mode, free).
Falls back to Yahoo Finance if TradingView fails.

    data, meta = fetch_multi_tf("OANDA:XAUUSD", {"MN1": 120, "W1": 260, "D1": 400, ...})
"""
import json
import logging
import math
import random
import re
import string
import time
from typing import Dict, Optional, Tuple

import pandas as pd
import requests
import websocket

logger = logging.getLogger("TVHistory")

TV_WS_URL = "wss://data.tradingview.com/socket.io/websocket"
TV_RES = {"MN1": "1M", "W1": "1W", "D1": "1D", "H4": "240", "H1": "60",
          "M30": "30", "M15": "15", "M5": "5", "M1": "1"}


def _rid(prefix: str) -> str:
    return prefix + "".join(random.choices(string.ascii_lowercase, k=12))


def _frame(func: str, params: list) -> str:
    msg = json.dumps({"m": func, "p": params}, separators=(",", ":"))
    return f"~m~{len(msg)}~m~{msg}"


def _to_df(bars: Dict[int, list]) -> pd.DataFrame:
    if not bars:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    times = sorted(bars)
    df = pd.DataFrame([bars[t] for t in times], columns=["open", "high", "low", "close", "volume"],
                      index=pd.to_datetime(times, unit="s", utc=True))
    df.index.name = "time"
    return df


def fetch_tradingview(tv_symbol: str, spec: Dict[str, int], timeout: float = 30.0
                      ) -> Tuple[Dict[str, pd.DataFrame], Dict]:
    """Fetch several timeframes for one symbol with automatic retry on transient reset."""
    last_err = None
    for attempt in range(3):
        try:
            return _fetch_tradingview_single(tv_symbol, spec, timeout)
        except Exception as e:
            last_err = e
            logger.warning(f"[TradingView] attempt {attempt+1}/3 failed for {tv_symbol}: {e}")
            time.sleep(1.5)
    raise last_err or RuntimeError(f"Failed to fetch {tv_symbol} after 3 attempts")


def _fetch_tradingview_single(tv_symbol: str, spec: Dict[str, int], timeout: float = 25.0
                              ) -> Tuple[Dict[str, pd.DataFrame], Dict]:
    """Fetch several timeframes for one symbol. Guest = 1 series per chart session, so 1 session per TF."""
    store: Dict[str, Dict[int, list]] = {tf: {} for tf in spec}
    sessions: Dict[str, str] = {}
    done = set()
    meta: Dict = {}

    ws = websocket.create_connection(TV_WS_URL, header=["Origin: https://www.tradingview.com"], timeout=timeout)
    try:
        ws.send(_frame("set_auth_token", ["unauthorized_user_token"]))
        for tf, count in spec.items():
            cs = _rid("cs_")
            sessions[cs] = tf
            ws.send(_frame("chart_create_session", [cs, ""]))
            ws.send(_frame("resolve_symbol", [cs, "sym1", "=" + json.dumps({"symbol": tv_symbol, "adjustment": "splits"})]))
            ws.send(_frame("create_series", [cs, "sds_1", "s1", "sym1", TV_RES[tf], int(count), ""]))

        deadline = time.time() + timeout
        while len(done) < len(spec) and time.time() < deadline:
            ws.settimeout(max(0.5, deadline - time.time()))
            try:
                raw = ws.recv()
            except websocket.WebSocketTimeoutException:
                break
            for payload in re.split(r"~m~\d+~m~", raw):
                if not payload:
                    continue
                if payload.startswith("~h~"):
                    ws.send(f"~m~{len(payload)}~m~{payload}")
                    continue
                try:
                    msg = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                if not isinstance(msg, dict):
                    continue
                m, p = msg.get("m"), msg.get("p", [])
                cs = p[0] if p else None
                tf = sessions.get(cs)
                if m in ("timescale_update", "du") and tf and len(p) > 1:
                    for bar in p[1].get("sds_1", {}).get("s", []):
                        v = bar.get("v", [])
                        if len(v) >= 5:
                            store[tf][int(v[0])] = [float(v[1]), float(v[2]), float(v[3]), float(v[4]),
                                                    float(v[5]) if len(v) > 5 and v[5] is not None else 0.0]
                elif m == "series_completed" and tf:
                    done.add(tf)
                elif m == "symbol_resolved" and len(p) > 2 and not meta:
                    meta = p[2]
                elif m in ("symbol_error", "series_error", "critical_error"):
                    logger.warning(f"[TradingView] {m} for {tv_symbol}: {p}")
                    if tf:
                        done.add(tf)
    finally:
        try:
            ws.close()
        except Exception:
            pass

    return {tf: _to_df(b) for tf, b in store.items()}, meta


# ---------------------------------------------------------------------- #
# Yahoo fallback
# ---------------------------------------------------------------------- #
YF_PARAMS = {"MN1": ("1mo", "max"), "W1": ("1wk", "10y"), "D1": ("1d", "2y"),
             "H1": ("60m", "60d"), "H4": ("60m", "60d"), "M15": ("15m", "30d")}


def fetch_yahoo(yf_symbol: str, spec: Dict[str, int]) -> Dict[str, pd.DataFrame]:
    out = {}
    hdr = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36"}
    for tf, count in spec.items():
        if tf not in YF_PARAMS:
            continue
        interval, rng = YF_PARAMS[tf]
        try:
            r = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{yf_symbol}",
                             params={"interval": interval, "range": rng}, headers=hdr, timeout=15)
            r.raise_for_status()
            res = r.json()["chart"]["result"][0]
            q = res["indicators"]["quote"][0]
            df = pd.DataFrame({"open": q["open"], "high": q["high"], "low": q["low"], "close": q["close"],
                               "volume": q.get("volume") or 0},
                              index=pd.to_datetime(res["timestamp"], unit="s", utc=True)).dropna(
                subset=["open", "high", "low", "close"])
            if tf == "H4":
                df = df.resample("4h", origin="epoch").agg(
                    {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()
            df.index.name = "time"
            out[tf] = df.tail(count)
        except Exception as e:
            logger.warning(f"[Yahoo] {yf_symbol} {tf} failed: {e}")
    return out


def fetch_multi_tf(tv_symbol: str, spec: Dict[str, int], yf_symbol: Optional[str] = None
                   ) -> Tuple[Dict[str, pd.DataFrame], Dict, str]:
    """Returns (data, meta, source_name)."""
    try:
        data, meta = fetch_tradingview(tv_symbol, spec)
        if all(len(data.get(tf, [])) > 20 for tf in spec):
            return data, meta, "TradingView"
        missing = [tf for tf in spec if len(data.get(tf, [])) <= 20]
        logger.warning(f"[TradingView] incomplete data for {tv_symbol}: {missing}")
    except Exception as e:
        logger.warning(f"[TradingView] fetch failed for {tv_symbol}: {e}")
        data, meta = {}, {}

    if yf_symbol:
        yf = fetch_yahoo(yf_symbol, spec)
        for tf in spec:
            if len(data.get(tf, [])) <= 20 and tf in yf:
                data[tf] = yf[tf]
        return data, meta, "TradingView+Yahoo"
    return data, meta, "TradingView"


def digits_from_meta(meta: Dict, fallback_price: float) -> int:
    ps = meta.get("pricescale")
    if ps:
        return max(0, int(round(math.log10(ps))))
    if fallback_price > 500:
        return 2
    if fallback_price > 20:
        return 3
    return 5
