# ⚡ ARGUS — Institutional SMC/ICT Forex & Commodities Discord Bot

**ARGUS** is an institutional-grade, multi-timeframe Smart Money Concepts (SMC) & Price Action analysis engine built for Discord. It combines real-time market data, dynamic TradingView-style charting, weighted timeframe arbitration, live zone/setup alerting, and a **no-command App Mode** with interactive dropdowns, buttons, and pop-up forms.

---

## ✨ Key Features

### 📱 App-Like Discord Experience (No Commands Needed)
- **Permanent App Home Screen (`/setup_app`)**: Pin an interactive trading desk to any channel with quick-launch pair dropdowns and 1-click buttons.
- **Pop-Up Modals (`🔍 Analyze Any Pair`)**: Type just a symbol (e.g. `usdcad`, `gold`, `nas100`) and timeframe (`15m`, `5m`, `1m`, `30m`, `1h`) in a native Discord form.
- **In-Place Controls on Every Chart**:
  - **Timeframe Switcher**: Swap between `15m`, `5m`, `1m`, `30m`, and `1h` without re-typing.
  - **`🔄 Refresh`**: Re-fetch live candles and re-render the chart in-place.
  - **`🧭 Higher TF`**: Ephemeral Monthly → Weekly → Daily → 4H macro bias matrix.
  - **`📋 Checklist`**: Ephemeral 8-point SMC double-confirmation breakdown.
  - **`🔔 Alert Me`**: Toggle live 60-second background monitoring for that `(pair, timeframe)`.
  - **`📤 Share`**: Publish a private analysis to the public channel in 1 click.

### 🧠 Institutional Reasoning Engine (`institutional_context.py`)
All detectors write into a single validated `InstitutionalContext` before anything is printed or drawn:
- **Weighted Timeframe Arbitration**: Resolves HTF vs. LTF conflicts using weighted scoring (`D1: 3.0`, `H4: 2.0`, `H1: 1.5`, `W1: 1.5`, `M15: 1.0`, `MN1: 1.0`) and outputs a plain-language thesis.
- **Dealing Range Validation**: Flags price outside `0%–100%` as `BREAKOUT / EXPANSION` instead of stale Premium/Discount labels.
- **Fibonacci Validation**: Automatically discards invalidated swing legs (`retrace > 100%`).
- **Proximity-Filtered Zones**: Hides distant zones beyond `3.5 × ATR` and reports the nearest Supply (above) and Demand (below) with pip distance and test count.
- **Explicit Actionable Triggers**: Outputs exact `Short Trigger`, `Long Trigger`, and `Invalidation` levels in true pip precision (5 decimals for FX majors, 2–3 for JPY/Gold).
- **Session & News Radar**: Displays active trading session, UTC window, interbank liquidity level, and upcoming Forex Factory high-impact news.

### 📈 100% Dynamic Chart Visualizer (`clean_chart_visualizer.py`)
- TradingView white theme with Royal Blue (`#2962ff`) bullish and Crimson Red (`#f23645`) bearish candles.
- Dynamically detects and plots **BOS**, **CHoCH**, **Bearish/Bullish Order Blocks**, **SSL/BSL liquidity**, **Liquidity Trendlines (`LQ`)**, and **Chart Patterns** directly from live OHLC candles.
- **Context-Aware Position Tool**: Risk/Reward boxes and target arrows are only drawn when price is actually testing a zone (`ARMED_IN_ZONE`) or double-confirmed (`TRIGGERED`).

### 🔔 Live 60-Second Alert Monitor
- Background `discord.ext.tasks` loop checks all subscribed `(pair, timeframe)` targets non-blockingly every 60 seconds.
- Fires concise, actionable pings when price **enters a key zone** or a **setup completes**:
  ```text
  🔔 @Trader ARGUS · SETUP COMPLETE ⚡
  🟢 BUY USD/CAD (M15)
  Entry: 1.43256 | SL: 1.43100 | TP: 1.43568 (TP2: 1.43800)
  ```
- Smart state tracking prevents duplicate spam and resets automatically when price exits the zone.

---

## 🏗️ Project Architecture

```text
ARGUS/
├── discord_bot.py              # Main bot entry point, Slash Commands, Modals, Views, Alert Loop & Render Health Server
├── mtf_analyzer.py             # Top-down multi-timeframe SMC & price action analyzer (MN1 -> M1)
├── institutional_context.py    # Shared InstitutionalContext dataclass, validators & timeframe arbitration engine
├── institutional_layers.py     # Session/Killzone manager & Forex Factory economic calendar feed
├── smc_engine.py               # Order Blocks, Fair Value Gaps (FVG) & Liquidity Pool detection
├── pattern_engine.py           # Chart patterns (Double Top/Bottom, H&S, Triangles), Trendlines & Fibonacci
├── clean_chart_visualizer.py   # Dynamic TradingView-style matplotlib chart renderer
├── tv_history.py               # Multi-timeframe OHLCV fetcher (TradingView WebSocket + Yahoo Finance fallback)
├── symbols.py                  # Symbol alias resolver (gold, gu, eu, loonie, us30, nas100, etc.)
├── strategy.py                 # Persistent strategy & risk configuration manager
├── requirements.txt            # Python dependencies (Linux/Railway compatible)
├── Procfile                    # Railway worker start command
└── render.yaml                 # Optional Render Blueprint
```

---

## 🚀 Quick Start (Local Development)

### Prerequisites
- Python **3.10+**
- A Discord Bot Token from the [Discord Developer Portal](https://discord.com/developers/applications)

### 1. Clone & Install
```bash
git clone https://github.com/LimaJuni/ARGUS.git
cd ARGUS
pip install -r requirements.txt
```

### 2. Configure Environment
Create a `.env` file in the project root (ignored by Git):
```env
DISCORD_BOT_TOKEN=your_discord_bot_token_here
```

### 3. Run ARGUS
```bash
python3 discord_bot.py
```

---

## 🚂 Deploying to Railway (Recommended)

**Railway** runs ARGUS as a clean 24/7 background worker without idle sleep:

1. Go to [railway.app](https://railway.app) → **New Project** → **Deploy from GitHub repo**.
2. Select **`LimaJuni/ARGUS`** (Railway auto-detects Python, `requirements.txt`, and `Procfile`).
3. Open **Variables** and add:
   - **`DISCORD_BOT_TOKEN`** = your Discord bot token
4. Railway builds and starts `python3 discord_bot.py` automatically on every push to `main`.

---

## 🎮 Discord Commands Reference

| Command | Description |
| :--- | :--- |
| `/setup_app` | *(Admin)* Deploys the permanent no-command **ARGUS Trading App** home screen in the current channel. |
| `/roles_panel` | *(Admin)* Deploys the persistent self-assignable role panel (`Gold Trader`, `FX Majors`, `Scalper`, `Trade Alerts`). |
| `/terminal [pair]` | Opens an interactive trading terminal with live pair & timeframe dropdowns. |
| `/analyze [symbol] [tf]` | Runs full institutional analysis + dynamic chart (with autocomplete; default `15m`, supports `5m`, `1m`, `30m`, `1h`). |
| `/scan` | Scans the configured watchlist for active setups. |
| `/bias` | Displays the Monthly / Weekly / Daily / 4H structural bias board. |
| `/strategy` | Displays current risk & strategy rules. |
| `/help` | Shows the interactive user guide. |

---

## ⚠️ Disclaimer

All market analysis, charts, zones, and alerts generated by **ARGUS** are strictly for **educational and informational purposes only** and do not constitute financial or investment advice. Always manage risk according to your own trading plan.
