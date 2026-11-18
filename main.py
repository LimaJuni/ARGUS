import os
import sys
import time
import logging
import argparse
from datetime import datetime, timezone
from typing import Dict

# Ensure Windows console supports UTF-8
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from config import EngineConfig, AssetSettings
from free_data_feed import FreeDataFeed
from anomaly_detector import AnomalyDetectionEngine, TradeAnomalySetup
from chart_visualizer import ChartVisualizer
from discord_notifier import DiscordNotifier

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("quant_engine.log", mode="a", encoding="utf-8")
    ]
)
logger = logging.getLogger("QuantEngine")


class AutonomousMultiAssetEngine:
    def __init__(self, config: EngineConfig, source: str = "tradingview"):
        self.config = config
        self.data_feed = FreeDataFeed(
            symbols=config.symbols,
            assets=config.assets,
            timeframes=(config.primary_tf, config.macro_tf),
            source=source,
        )
        self.detector = AnomalyDetectionEngine(
            swing_length=config.swing_length,
            ob_impulse_multiplier=config.ob_impulse_multiplier,
            min_risk_reward=config.min_risk_reward_ratio
        )
        self.visualizer = ChartVisualizer(output_dir=config.snapshots_dir)
        self.notifier = DiscordNotifier(webhook_url=config.discord_webhook_url,
                                        execution_enabled=config.enable_auto_trading)

        # MT5 execution is optional (Windows only). Imported lazily so Linux runs without MetaTrader5.
        self.executor = None
        if config.enable_auto_trading:
            from execution_engine import MT5ExecutionEngine
            self.executor = MT5ExecutionEngine(config=config, notifier=self.notifier)
        
        self.last_alert_time: Dict[str, float] = {}
        self.alert_cooldown_seconds = 180  # 3 min cooldown per setup direction
        self.macro_biases: Dict[str, str] = {s: "NEUTRAL" for s in config.symbols}
        self.last_macro_check: Dict[str, float] = {s: 0.0 for s in config.symbols}

    def start(self):
        """Main operational entry point."""
        logger.info(f"Initializing Multi-Asset Autonomous Engine for: {self.config.symbols}...")
        if not self.data_feed.connect():
            logger.error("Failed to connect to market data feed. Terminating engine.")
            return

        logger.info(f"Connected Symbols: {self.data_feed.symbols} (source: {self.data_feed.source})")
        logger.info(f"Mode: {'LIVE EXECUTION (MT5)' if self.executor else 'SIGNAL-ONLY (no execution)'}")
        if self.executor:
            logger.info(f"Portfolio Rule: Max 1 position per pair, Max {self.config.max_total_positions} total (No Hedging)")
            logger.info(f"Trailing Stop & Profit Lock: {'ENABLED' if self.config.enable_trailing_stop else 'DISABLED'}")

        try:
            self._live_monitor_loop()
        except KeyboardInterrupt:
            logger.info("Termination signal received. Shutting down...")
        finally:
            self.data_feed.disconnect()

    def _live_monitor_loop(self):
        """Asynchronous tick, execution & trailing stop monitoring loop across all pairs."""
        logger.info(f"Engine is LIVE across all pairs. Scanning M15 macro trends and M1 setups...")

        while True:
            try:
                now_ts = time.time()

                # 1. Manage active trailing stops across all open positions
                if self.executor and self.config.enable_trailing_stop:
                    self.executor.manage_trailing_stops()

                # 2. Iterate through each portfolio symbol
                for symbol in self.data_feed.symbols:
                    asset_cfg = self.config.assets.get(symbol)
                    if not asset_cfg:
                        continue

                    # Update M15 Macro Bias every 30 seconds
                    if now_ts - self.last_macro_check[symbol] > 30:
                        df_m15 = self.data_feed.get_historical_bars(symbol=symbol, timeframe=self.config.macro_tf, count=100)
                        new_bias = self.detector.calculate_macro_bias(df_m15)
                        if new_bias != self.macro_biases[symbol]:
                            self.macro_biases[symbol] = new_bias
                            logger.info(f"[MACRO BIAS] {symbol} {self.config.macro_tf} Trend: {new_bias}")
                        self.last_macro_check[symbol] = now_ts

                    # Get live tick
                    latest_tick = self.data_feed.get_latest_tick(symbol)
                    if not latest_tick:
                        continue

                    # Get M1 bars & evaluate
                    df_m1 = self.data_feed.get_historical_bars(symbol=symbol, timeframe=self.config.primary_tf, count=200)
                    if df_m1 is None or len(df_m1) < 50:
                        continue

                    setup = self.detector.evaluate_live_anomalies(
                        df=df_m1,
                        symbol=symbol,
                        timeframe=self.config.primary_tf,
                        current_tick=latest_tick,
                        asset_settings=asset_cfg,
                        macro_bias=self.macro_biases[symbol]
                    )

                    if setup:
                        self._handle_detected_setup(setup)

                time.sleep(1.5)  # Responsive loop cycle

            except Exception as e:
                logger.error(f"Error in monitor loop: {e}", exc_info=True)
                time.sleep(3)

    def _handle_detected_setup(self, setup: TradeAnomalySetup):
        """Process detected anomaly, enforce trade limits, execute trade, and dispatch alerts."""
        setup_key = f"{setup.symbol}_{setup.timeframe}_{setup.direction}"
        now = time.time()

        if setup_key in self.last_alert_time:
            elapsed = now - self.last_alert_time[setup_key]
            if elapsed < self.alert_cooldown_seconds:
                return

        logger.info(f"[ANOMALY DETECTED] {setup.direction} on {setup.symbol} ({setup.timeframe})")
        logger.info(f"Setup: {setup.setup_name} | Entry: {setup.entry_price} | SL: {setup.stop_loss} | TP: {setup.take_profit_1} | Macro: {setup.macro_bias}")

        # Portfolio check: max 1 per symbol, max 3 total, no hedging (only when executing)
        if self.executor:
            allowed, reason = self.executor.can_execute_trade(setup.symbol, setup.direction)
            if not allowed:
                logger.info(f"Execution skipped for {setup.symbol}: {reason}")
                return

        self.last_alert_time[setup_key] = now

        # Render chart snapshot
        chart_path = self.visualizer.generate_chart_snapshot(setup)
        logger.info(f"Generated chart snapshot: {chart_path}")

        # Push to Discord
        self.notifier.send_trade_alert(setup, chart_image_path=chart_path)

        # Execute Live Order in MT5
        if self.executor:
            trade_res = self.executor.execute_trade(
                symbol=setup.symbol,
                direction=setup.direction,
                entry_price=setup.entry_price,
                stop_loss=setup.stop_loss,
                take_profit=setup.take_profit_1,
                setup_name=setup.setup_name
            )
            if trade_res:
                logger.info(f"[EXECUTED] Trade successfully placed in MT5: {trade_res}")

    def run_historical_scan(self, bars_count: int = 200):
        """Scans recent M1 bars across all 3 pairs with M15 macro bias check."""
        logger.info(f"Running multi-asset historical scan across {self.config.symbols}...")
        if not self.data_feed.connect():
            return

        for symbol in self.data_feed.symbols:
            asset_cfg = self.config.assets.get(symbol)
            if not asset_cfg:
                continue

            tick = self.data_feed.get_latest_tick(symbol)
            if not tick:
                continue

            df_m15 = self.data_feed.get_historical_bars(symbol=symbol, timeframe="M15", count=100)
            macro_bias = self.detector.calculate_macro_bias(df_m15)

            df_m1 = self.data_feed.get_historical_bars(symbol=symbol, timeframe="M1", count=bars_count)
            if df_m1 is not None:
                setup = self.detector.evaluate_live_anomalies(
                    df=df_m1,
                    symbol=symbol,
                    timeframe="M1",
                    current_tick=tick,
                    asset_settings=asset_cfg,
                    macro_bias=macro_bias
                )

                if setup:
                    logger.info(f"Active setup on {symbol}: {setup.direction} {setup.setup_name}")
                    chart_path = self.visualizer.generate_chart_snapshot(setup)
                    self.notifier.send_trade_alert(setup, chart_image_path=chart_path)
                else:
                    logger.info(f"[{symbol}] Macro Bias: {macro_bias} | No conflicting setup at current candle.")

        self.data_feed.disconnect()
        logger.info("Multi-asset scan completed.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Autonomous Multi-Asset Quant Engine")
    parser.add_argument("--scan", action="store_true", help="Run historical anomaly scan across all pairs")
    parser.add_argument("--webhook", type=str, default="", help="Discord webhook URL")
    parser.add_argument("--source", choices=["tradingview", "yahoo"], default="tradingview",
                        help="Market data source (default: tradingview, auto-falls back to yahoo)")
    args = parser.parse_args()

    cfg = EngineConfig()
    if args.webhook:
        cfg.discord_webhook_url = args.webhook

    engine = AutonomousMultiAssetEngine(config=cfg, source=args.source)

    if args.scan:
        engine.run_historical_scan(bars_count=200)
    else:
        engine.start()
