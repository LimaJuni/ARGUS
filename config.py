import os
from dataclasses import dataclass, field
from typing import Dict, List

# Auto-load .env if present
env_file = os.path.join(os.path.dirname(__file__), ".env")
if os.path.exists(env_file):
    with open(env_file, "r") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

@dataclass
class AssetSettings:
    symbol: str
    display_name: str
    fvg_min_points: float
    sl_buffer_points: float
    max_sl_points: float
    max_allowed_spread_points: float
    be_trigger_points: float
    be_offset_points: float
    trail_start_points: float
    trail_distance_points: float
    trail_step_points: float
    digits: int = 3
    point: float = 0.001
    tv_symbol: str = ""
    yf_symbol: str = ""

# Default tailored parameters per asset class
DEFAULT_ASSET_SETTINGS: Dict[str, AssetSettings] = {
    "XAUUSDm": AssetSettings(
        symbol="XAUUSDm",
        display_name="Gold (XAUUSD)",
        fvg_min_points=250.0,       # $0.25 on Gold
        sl_buffer_points=850.0,     # $0.85 buffer above wick
        max_sl_points=2500.0,       # $2.50 max SL
        max_allowed_spread_points=400.0, # $0.40 max spread
        be_trigger_points=1200.0,   # Lock BE at +$1.20 profit
        be_offset_points=200.0,     # Lock +$0.20 gain
        trail_start_points=1800.0,  # Trail after +$1.80
        trail_distance_points=1000.0,# Trail $1.00 behind price
        trail_step_points=250.0,    # Step by $0.25
        digits=3,
        point=0.001,
        tv_symbol="OANDA:XAUUSD",
        yf_symbol="GC=F"
    ),
    "EURUSDm": AssetSettings(
        symbol="EURUSDm",
        display_name="Euro (EURUSD)",
        fvg_min_points=25.0,        # 2.5 pips (Point = 0.00001)
        sl_buffer_points=20.0,      # 2.0 pips buffer above wick
        max_sl_points=100.0,        # 10.0 pips max SL
        max_allowed_spread_points=25.0,  # 2.5 pips max spread
        be_trigger_points=50.0,     # Lock BE at +5.0 pips
        be_offset_points=15.0,      # Lock +1.5 pips gain
        trail_start_points=80.0,    # Trail after +8.0 pips
        trail_distance_points=40.0, # Trail 4.0 pips behind
        trail_step_points=15.0,     # Step by 1.5 pips
        digits=5,
        point=0.00001,
        tv_symbol="OANDA:EURUSD",
        yf_symbol="EURUSD=X"
    ),
    "USDJPYm": AssetSettings(
        symbol="USDJPYm",
        display_name="Yen (USDJPY)",
        fvg_min_points=30.0,        # 3.0 pips (Point = 0.001)
        sl_buffer_points=25.0,      # 2.5 pips buffer above wick
        max_sl_points=120.0,        # 12.0 pips max SL
        max_allowed_spread_points=30.0,  # 3.0 pips max spread
        be_trigger_points=60.0,     # Lock BE at +6.0 pips
        be_offset_points=20.0,      # Lock +2.0 pips gain
        trail_start_points=90.0,    # Trail after +9.0 pips
        trail_distance_points=50.0, # Trail 5.0 pips behind
        trail_step_points=20.0,     # Step by 2.0 pips
        digits=3,
        point=0.001,
        tv_symbol="OANDA:USDJPY",
        yf_symbol="JPY=X"
    )
}

@dataclass
class EngineConfig:
    # Portfolio of Assets to trade
    symbols: List[str] = field(default_factory=lambda: ["XAUUSDm", "EURUSDm", "USDJPYm"])
    primary_tf: str = "M1"              # Primary execution timeframe
    macro_tf: str = "M15"               # Higher Timeframe for macro trend bias
    
    # Portfolio Position Limits:
    # 1 position per pair, max 3 positions total across all pairs
    max_positions_per_symbol: int = 1
    max_total_positions: int = 3
    
    # Execution & Risk (Disabled on Ubuntu / signal-only mode by default)
    enable_auto_trading: bool = False
    lot_size: float = 0.01              # Minimum contract size
    magic_number: int = 777123
    enable_trailing_stop: bool = False
    min_risk_reward_ratio: float = 1.8
    swing_length: int = 5
    ob_impulse_multiplier: float = 1.3
    
    # Discord Integration
    discord_webhook_url: str = os.getenv("DISCORD_WEBHOOK_URL", "")
    snapshots_dir: str = "snapshots"
    
    assets: Dict[str, AssetSettings] = field(default_factory=lambda: DEFAULT_ASSET_SETTINGS)
