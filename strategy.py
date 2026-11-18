"""
Editable strategy rules (persisted to strategy.json).
Defaults encode the rules from your Forex Strategy Guide:
  - Top-down: Monthly/Weekly context -> Daily direction (the boss) -> 4H setup zone -> 1H refine -> 15m execute
  - Fresh supply/demand zones (max 2 touches), impulsive move away, HTF zones stronger
  - Double confirmation on 15m: rejection candle + mini BOS
  - SL beyond the zone (invalidation), min 1:2 R:R, BE at 1:1, 50% partial at 1:2, 1-2% risk
Change any value from Discord with:  !set <key> <value>
"""
import copy
import json
import os
import threading

STRATEGY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "strategy.json")

DEFAULT_STRATEGY = {
    "name": "Top-Down Structure + Supply/Demand (Forex Strategy Guide)",
    "timeframes": ["MN1", "W1", "D1", "H4", "H1", "M15"],
    "bars": {"MN1": 120, "W1": 260, "D1": 400, "H4": 500, "H1": 500, "M15": 400},
    "swing_length": {"MN1": 2, "W1": 2, "D1": 3, "H4": 3, "H1": 3, "M15": 3},

    # Role of each timeframe
    "context_tfs": ["MN1", "W1"],
    "bias_tf": "D1",            # "Never trade against the Daily. The Daily chart is your boss."
    "setup_tf": "H4",           # find supply/demand zones
    "refine_tf": "H1",          # refine zone / wait for price to enter
    "exec_tf": "M15",           # double confirmation
    "zone_tfs": ["W1", "D1", "H4", "H1"],

    # Zone rules
    "impulse_atr_mult": 1.3,    # move-away candle body must be >= this x ATR(14)
    "base_max_candles": 3,
    "base_body_atr_ratio": 0.6,
    "max_zone_touches": 2,      # 3rd touch = weak, skip
    "approach_atr": 1.0,        # "approaching" if within this many setup-TF ATRs

    # Execution / confirmation
    "require_daily_alignment": True,
    "require_rejection_candle": True,
    "require_mini_bos": True,
    "confirmation_lookback": 12,  # 15m bars to look back for rejection + mini BOS

    # Risk management
    "min_rr": 2.0,
    "breakeven_rr": 1.0,
    "partial_tp_rr": 2.0,
    "partial_tp_pct": 50,
    "sl_buffer_atr": 0.2,       # SL buffer beyond zone, in refine-TF ATRs
    "account_balance": 1000.0,
    "risk_pct": 1.0,

    # Watchlist / alerts
    "watchlist": ["XAUUSD", "EURUSD", "GBPUSD", "USDJPY", "USDCHF"],
    "watch_interval_minutes": 15,

    # Your own free-text analysis notes (shown in every report)
    "notes": [],
}

_lock = threading.Lock()


def load_strategy() -> dict:
    with _lock:
        cfg = copy.deepcopy(DEFAULT_STRATEGY)
        if os.path.exists(STRATEGY_FILE):
            try:
                with open(STRATEGY_FILE, "r") as f:
                    user = json.load(f)
                for k, v in user.items():
                    if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                        cfg[k].update(v)
                    else:
                        cfg[k] = v
            except Exception:
                pass
        return cfg


def save_strategy(cfg: dict):
    with _lock:
        with open(STRATEGY_FILE, "w") as f:
            json.dump(cfg, f, indent=2)


def set_value(key: str, raw_value: str) -> str:
    """Set a (possibly dotted) key, e.g. 'min_rr' '2.5'  or  'bars.D1' '500'. Returns the stored value repr."""
    cfg = load_strategy()
    parts = key.split(".")
    target = cfg
    for p in parts[:-1]:
        if p not in target or not isinstance(target[p], dict):
            raise KeyError(f"Unknown key: {key}")
        target = target[p]
    leaf = parts[-1]
    if leaf not in target:
        raise KeyError(f"Unknown key: {key}. Use !strategy to see valid keys.")

    current = target[leaf]
    if isinstance(current, list) and not raw_value.strip().startswith("["):
        value = [v.strip().upper() for v in raw_value.replace(",", " ").split() if v.strip()]
    else:
        try:
            value = json.loads(raw_value)
        except json.JSONDecodeError:
            low = raw_value.strip().lower()
            value = True if low in ("true", "on", "yes") else False if low in ("false", "off", "no") else raw_value.strip()
    if isinstance(current, bool) and not isinstance(value, bool):
        raise ValueError(f"{key} must be true/false")
    if isinstance(current, (int, float)) and not isinstance(current, bool):
        value = float(value) if isinstance(current, float) or "." in str(value) else int(value)

    target[leaf] = value
    save_strategy(cfg)
    return json.dumps(value)


def add_note(text: str) -> int:
    cfg = load_strategy()
    cfg.setdefault("notes", []).append(text.strip())
    save_strategy(cfg)
    return len(cfg["notes"])


def remove_note(index: int) -> str:
    cfg = load_strategy()
    note = cfg["notes"].pop(index - 1)
    save_strategy(cfg)
    return note


def reset_strategy():
    save_strategy(copy.deepcopy(DEFAULT_STRATEGY))
