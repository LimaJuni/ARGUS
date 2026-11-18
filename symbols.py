"""Resolve free-text instrument names ("gold", "gu", "eurusd", "XAUUSDm") to data-feed symbols."""
import re
from dataclasses import dataclass


@dataclass
class SymbolSpec:
    name: str            # e.g. XAUUSD
    tv_symbol: str       # e.g. OANDA:XAUUSD
    yf_symbol: str       # e.g. GC=F
    display: str
    contract_size: float
    base: str
    quote: str


ALIASES = {
    "gold": "XAUUSD", "xau": "XAUUSD", "silver": "XAGUSD", "xag": "XAGUSD",
    "eu": "EURUSD", "euro": "EURUSD", "fiber": "EURUSD",
    "gu": "GBPUSD", "cable": "GBPUSD", "pound": "GBPUSD",
    "uj": "USDJPY", "yen": "USDJPY", "gj": "GBPJPY", "ej": "EURJPY",
    "swissy": "USDCHF", "uchf": "USDCHF", "loonie": "USDCAD", "ucad": "USDCAD",
    "au": "AUDUSD", "aussie": "AUDUSD", "nu": "NZDUSD", "kiwi": "NZDUSD",
    "us30": "US30USD", "dow": "US30USD", "nas": "NAS100USD", "nas100": "NAS100USD", "nasdaq": "NAS100USD",
    "spx": "SPX500USD", "sp500": "SPX500USD", "us500": "SPX500USD",
}

DISPLAY = {"XAUUSD": "Gold", "XAGUSD": "Silver", "US30USD": "Dow Jones (US30)",
           "NAS100USD": "Nasdaq 100", "SPX500USD": "S&P 500"}
YF_OVERRIDE = {"XAUUSD": "GC=F", "XAGUSD": "SI=F", "US30USD": "YM=F", "NAS100USD": "NQ=F", "SPX500USD": "ES=F"}


def resolve_symbol(query: str) -> SymbolSpec:
    q = query.strip()
    if ":" in q:  # raw TradingView symbol e.g. OANDA:GBPAUD / FX:EURUSD
        tv = q.upper()
        name = tv.split(":", 1)[1]
    else:
        key = re.sub(r"[^a-z0-9]", "", q.lower())
        name = ALIASES.get(key, key.upper())
        if len(name) == 7 and name.endswith("M"):  # broker suffix, e.g. XAUUSDm
            name = name[:-1]
        tv = f"OANDA:{name}"

    if name.startswith(("XAU",)):
        cs = 100.0
    elif name.startswith("XAG"):
        cs = 5000.0
    elif name.endswith("USD") and len(name) > 6:  # indices
        cs = 1.0
    else:
        cs = 100000.0

    base, quote = (name[:3], name[3:6]) if len(name) == 6 or name.startswith(("XAU", "XAG")) else (name, "USD")
    yf = YF_OVERRIDE.get(name, f"{name}=X")
    disp = DISPLAY.get(name, f"{name[:3]}/{name[3:]}" if len(name) == 6 else name)
    return SymbolSpec(name=name, tv_symbol=tv, yf_symbol=yf, display=disp, contract_size=cs, base=base, quote=quote)
