"""
Clean TradingView Style Chart Visualizer (100% Dynamic Market Structure Reasoning).
Learned from user's manual TradingView analysis style:
- 100% Dynamic Reasoning directly from actual candlesticks & market structure:
  * Real BEAR SUPPLY / BULL DEMAND zones & Order Blocks at their true candle origins
  * Real Fair Value Gaps (FVG) at actual price imbalances
  * Real BOS (Break of Structure) horizontal lines from broken swing pivots to breakout candles
  * Real CHoCH (Change of Character) horizontal lines when structural trend flips
  * Real SSL (Sell-Side Liquidity) at bottom swing lows & BSL (Buy-Side Liquidity) at top swing highs
  * Real Dynamic Liquidity Trendline (LQ) connecting actual swing pivots
  * Context-aware Setup Drawing: Short/Long position tools and rejection paths are ONLY
    drawn when price is actively inside or testing a key zone (ARMED or TRIGGERED)!
    When price is between zones (WAIT), the visualizer displays the clean playing field
    without hallucinating fake trades or floating shapes.
- Ultra-clean TradingView White Theme:
  * Crisp #ffffff canvas, subtle #f0f3fa grid
  * Royal Blue (#2962ff) bull candles & Crimson Red (#f23645) bear candles
  * Right-hand price axis with colored price badges (Blue current, Red supply/SL, Green demand/entry, Cyan SSL/TP, Gray CHoCH)
  * TradingView header & bottom-left watermark ('17  TradingView')
"""

import os
from datetime import datetime
from typing import List, Optional, Dict, Tuple
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as patches
import pandas as pd
import numpy as np

from mtf_analyzer import CompleteAnalysisResult


class CleanTradingViewVisualizer:
    def __init__(self, output_dir: str = "snapshots"):
        self.output_dir = output_dir
        os.makedirs(self.output_dir, exist_ok=True)

    def generate_chart(self, result: CompleteAnalysisResult, dfs: dict, tf: str = "M15") -> str:
        """
        Generates an ultra-clean, 100% dynamically reasoned TradingView chart.
        """
        if tf not in dfs or len(dfs[tf]) < 20:
            tf = "M15" if "M15" in dfs else list(dfs.keys())[-1]

        df = dfs[tf].tail(65).copy()
        n = len(df)
        d = result.digits
        p = result.point

        fig, ax = plt.subplots(figsize=(15, 8.5), dpi=160)
        fig.patch.set_facecolor("#ffffff")
        ax.set_facecolor("#ffffff")

        # Right-hand Y-axis (standard TradingView layout)
        ax.yaxis.tick_right()
        ax.yaxis.set_label_position("right")
        ax.grid(True, color="#f0f3fa", linestyle="--", linewidth=0.5, alpha=0.9)

        for spine in ["top", "left", "bottom"]:
            ax.spines[spine].set_color("#d1d4dc")
            ax.spines[spine].set_linewidth(0.8)
        ax.spines["right"].set_color("#131722")
        ax.spines["right"].set_linewidth(1.0)
        ax.tick_params(axis="both", colors="#50535e", labelsize=9, length=4)

        up_col = "#2962ff"     # TradingView Royal Blue
        down_col = "#f23645"   # TradingView Crimson Red

        # 1. Plot Candlesticks
        self._plot_candles(ax, df, p, up_col, down_col)

        # 2. Dynamic Swings Detection (Local 2-bar radius)
        swings = []
        for i in range(2, n - 2):
            if df["high"].iloc[i] == max(df["high"].iloc[i - 2 : i + 3]):
                swings.append({"type": "HIGH", "idx": i, "price": float(df["high"].iloc[i]), "time": df.index[i]})
            if df["low"].iloc[i] == min(df["low"].iloc[i - 2 : i + 3]):
                swings.append({"type": "LOW", "idx": i, "price": float(df["low"].iloc[i]), "time": df.index[i]})

        high_swings = [s for s in swings if s["type"] == "HIGH"]
        low_swings = [s for s in swings if s["type"] == "LOW"]

        lowest_low = float(min(df["low"].values))
        highest_high = float(max(df["high"].values))
        current_price = float(df["close"].iloc[-1])
        z_right = n + 12

        # 3. Dynamic BOS & CHoCH Detection
        # Track market trend: if low breaks after an uptrend = CHoCH; if in downtrend = BOS
        structure_lines = []
        trend = "BULLISH" if (high_swings and low_swings and high_swings[0]["price"] < high_swings[-1]["price"]) else "BEARISH"

        for s in swings:
            if s["type"] == "LOW":
                for j in range(s["idx"] + 1, n):
                    if df["close"].iloc[j] < s["price"]:
                        line_type = "CHoCH" if trend == "BULLISH" else "BOS"
                        trend = "BEARISH"
                        structure_lines.append({
                            "type": line_type,
                            "price": s["price"],
                            "start_idx": s["idx"],
                            "break_idx": j,
                            "direction": "BEAR"
                        })
                        break
            elif s["type"] == "HIGH":
                for j in range(s["idx"] + 1, n):
                    if df["close"].iloc[j] > s["price"]:
                        line_type = "CHoCH" if trend == "BEARISH" else "BOS"
                        trend = "BULLISH"
                        structure_lines.append({
                            "type": line_type,
                            "price": s["price"],
                            "start_idx": s["idx"],
                            "break_idx": j,
                            "direction": "BULL"
                        })
                        break

        # Plot the most recent 4 structure lines cleanly
        recent_structure = structure_lines[-4:]
        for sl in recent_structure:
            end_x = min(n - 1, sl["break_idx"] + 3)
            ax.plot([sl["start_idx"], end_x], [sl["price"], sl["price"]], color="#131722", linewidth=1.1, zorder=4)
            ax.text(end_x + 0.6, sl["price"], sl["type"], color="#131722", fontsize=8.0, fontweight="bold", va="center")

        # 4. Dynamic Order Blocks & Supply/Demand Zones
        # Look for real unmitigated Order Blocks on visible candles
        drawn_supply = None
        drawn_demand = None

        # Check Bearish OB: last green candle before an impulsive red drop
        for i in range(2, n - 2):
            body_prev = abs(df["close"].iloc[i] - df["open"].iloc[i])
            drop = df["open"].iloc[i + 1] - df["close"].iloc[i + 1]
            if df["close"].iloc[i] > df["open"].iloc[i] and drop > body_prev * 1.5:
                ob_top = float(df["high"].iloc[i])
                ob_bot = float(df["low"].iloc[i])
                # Check if mitigated
                is_mitigated = any(df["close"].iloc[j] > ob_top for j in range(i + 1, n))
                if not is_mitigated:
                    drawn_supply = {"idx": i, "top": ob_top, "bot": ob_bot}

        # Check Bullish OB: last red candle before an impulsive green rally
        for i in range(2, n - 2):
            body_prev = abs(df["close"].iloc[i] - df["open"].iloc[i])
            rally = df["close"].iloc[i + 1] - df["open"].iloc[i + 1]
            if df["close"].iloc[i] < df["open"].iloc[i] and rally > body_prev * 1.5:
                ob_top = float(df["high"].iloc[i])
                ob_bot = float(df["low"].iloc[i])
                is_mitigated = any(df["close"].iloc[j] < ob_bot for j in range(i + 1, n))
                if not is_mitigated:
                    drawn_demand = {"idx": i, "top": ob_top, "bot": ob_bot}

        # Fallback to swing extremes if no local OB detected
        if not drawn_supply and high_swings:
            last_hs = high_swings[-1]
            sup_h = float(last_hs["price"])
            sup_c = df.iloc[last_hs["idx"]]
            sup_l = float(min(sup_c["open"], sup_c["close"]))
            if sup_h - sup_l < (highest_high - lowest_low) * 0.02:
                sup_l = sup_h - (highest_high - lowest_low) * 0.05
            drawn_supply = {"idx": last_hs["idx"], "top": sup_h, "bot": sup_l}

        if not drawn_demand and low_swings:
            last_ls = low_swings[-1]
            dem_l = float(last_ls["price"])
            dem_c = df.iloc[last_ls["idx"]]
            dem_h = float(max(dem_c["open"], dem_c["close"]))
            if dem_h - dem_l < (highest_high - lowest_low) * 0.02:
                dem_h = dem_l + (highest_high - lowest_low) * 0.05
            drawn_demand = {"idx": last_ls["idx"], "top": dem_h, "bot": dem_l}

        # Draw Supply Zone if within visible chart
        if drawn_supply and drawn_supply["top"] >= lowest_low:
            s_top = drawn_supply["top"]
            s_bot = drawn_supply["bot"]
            s_h = max(s_top - s_bot, p * 5)
            s_x = drawn_supply["idx"]
            s_w = z_right - s_x

            # Supply Rectangle
            ax.add_patch(patches.Rectangle(
                (s_x, s_bot), s_w, s_h,
                facecolor="#ffebee", edgecolor="#e53935", linewidth=1.1, alpha=0.5, zorder=3
            ))
            # Left mini origin block
            ax.add_patch(patches.Rectangle(
                (s_x, s_bot), min(6, s_w * 0.25), s_h,
                facecolor="#ffcdd2", edgecolor="#c62828", linewidth=1.0, alpha=0.7, zorder=3
            ))
            ax.text(s_x + 3, s_top + (s_h * 0.25), "BEAR OB", color="#c62828", fontsize=8.0, fontweight="bold", ha="center")
            ax.text(s_x + (s_w * 0.5), s_top + (s_h * 0.25), "SUPPLY", color="#e53935", fontsize=8.0, fontweight="bold", ha="center")
            ax.text(z_right + 0.8, s_top, f" {s_top:.{d}f} ", color="#ffffff", backgroundcolor="#e53935", fontsize=8.2, fontweight="bold", va="center")

        # Draw Demand Zone if within visible chart
        if drawn_demand and drawn_demand["bot"] <= highest_high:
            d_top = drawn_demand["top"]
            d_bot = drawn_demand["bot"]
            d_h = max(d_top - d_bot, p * 5)
            d_x = drawn_demand["idx"]
            d_w = z_right - d_x

            # Demand Rectangle
            ax.add_patch(patches.Rectangle(
                (d_x, d_bot), d_w, d_h,
                facecolor="#e8f5e9", edgecolor="#2e7d32", linewidth=1.1, alpha=0.5, zorder=3
            ))
            # Left mini origin block
            ax.add_patch(patches.Rectangle(
                (d_x, d_bot), min(6, d_w * 0.25), d_h,
                facecolor="#c8e6c9", edgecolor="#1b5e20", linewidth=1.0, alpha=0.7, zorder=3
            ))
            ax.text(d_x + 3, d_bot - (d_h * 0.35), "BULL OB", color="#2e7d32", fontsize=8.0, fontweight="bold", ha="center")
            ax.text(d_x + (d_w * 0.5), d_bot - (d_h * 0.35), "DEMAND", color="#2e7d32", fontsize=8.0, fontweight="bold", ha="center")
            ax.text(z_right + 0.8, d_bot, f" {d_bot:.{d}f} ", color="#ffffff", backgroundcolor="#2e7d32", fontsize=8.2, fontweight="bold", va="center")

        # 5. Dynamic Liquidity Targets (BSL at swing high, SSL at swing low)
        # Sell-Side Liquidity (SSL) at lowest low
        ssl_price = lowest_low
        ssl_sw = [s for s in low_swings if abs(s["price"] - lowest_low) < p * 5]
        ssl_idx = ssl_sw[0]["idx"] if ssl_sw else max(0, n - 20)
        ax.plot([ssl_idx, z_right], [ssl_price, ssl_price], color="#131722", linewidth=1.4, zorder=4)
        ax.text((ssl_idx + z_right) / 2.0, ssl_price - (p * 8), "SSL", color="#131722", fontsize=9.0, fontweight="bold", ha="center", va="top")
        ax.text(z_right + 0.8, ssl_price, f" {ssl_price:.{d}f} ", color="#ffffff", backgroundcolor="#00bcd4", fontsize=8.2, fontweight="bold", va="center")

        # Buy-Side Liquidity (BSL) at highest high
        bsl_price = highest_high
        bsl_sw = [s for s in high_swings if abs(s["price"] - highest_high) < p * 5]
        bsl_idx = bsl_sw[0]["idx"] if bsl_sw else max(0, n - 20)
        ax.plot([bsl_idx, z_right], [bsl_price, bsl_price], color="#131722", linewidth=1.4, zorder=4)
        ax.text((bsl_idx + z_right) / 2.0, bsl_price + (p * 8), "BSL", color="#131722", fontsize=9.0, fontweight="bold", ha="center", va="bottom")
        ax.text(z_right + 0.8, bsl_price, f" {bsl_price:.{d}f} ", color="#ffffff", backgroundcolor="#ab47bc", fontsize=8.2, fontweight="bold", va="center")

        # 6. Dynamic Liquidity Trendline (LQ)
        if len(high_swings) >= 2:
            h1, h2 = high_swings[-2], high_swings[-1]
            dx = h2["idx"] - h1["idx"]
            if dx > 0 and h2["price"] < h1["price"]:
                slope = (h2["price"] - h1["price"]) / dx
                tx_start = max(0, h1["idx"] - 2)
                tx_end = min(n + 6, h2["idx"] + 12)
                ty_start = h1["price"] + slope * (tx_start - h1["idx"])
                ty_end = h1["price"] + slope * (tx_end - h1["idx"])
                ax.plot([tx_start, tx_end], [ty_start, ty_end], color="#50535e", linewidth=1.2, linestyle="-", zorder=3)
                ax.text((tx_start + tx_end) / 2.0, (ty_start + ty_end) / 2.0 + (p * 6), "LQ", color="#131722", fontsize=8.2, fontweight="bold", ha="center")
        elif len(low_swings) >= 2:
            l1, l2 = low_swings[-2], low_swings[-1]
            dx = l2["idx"] - l1["idx"]
            if dx > 0 and l2["price"] > l1["price"]:
                slope = (l2["price"] - l1["price"]) / dx
                tx_start = max(0, l1["idx"] - 2)
                tx_end = min(n + 6, l2["idx"] + 12)
                ty_start = l1["price"] + slope * (tx_start - l1["idx"])
                ty_end = l1["price"] + slope * (tx_end - l1["idx"])
                ax.plot([tx_start, tx_end], [ty_start, ty_end], color="#50535e", linewidth=1.2, linestyle="-", zorder=3)
                ax.text((tx_start + tx_end) / 2.0, (ty_start + ty_end) / 2.0 - (p * 6), "LQ", color="#131722", fontsize=8.2, fontweight="bold", ha="center")

        # 7. Real Detected Patterns Drawing
        # Check patterns from result
        all_pat_list = result.patterns.get(tf, [])
        for pat in all_pat_list:
            if pat.name in ("DOUBLE_TOP", "DOUBLE_BOTTOM") and len(swings) >= 3:
                # Find matching swings for anchor points
                if pat.name == "DOUBLE_TOP" and len(high_swings) >= 2:
                    t1, t2 = high_swings[-2], high_swings[-1]
                    mid_lows = [l for l in low_swings if t1["idx"] < l["idx"] < t2["idx"]]
                    if mid_lows:
                        valley = mid_lows[0]
                        ax.plot([t1["idx"], valley["idx"], t2["idx"]], [t1["price"], valley["price"], t2["price"]],
                                color="#131722", linewidth=1.5, linestyle="--", zorder=5)
                        ax.plot([valley["idx"], z_right - 4], [valley["price"], valley["price"]],
                                color="#787b86", linewidth=1.0, linestyle=":", zorder=4)
                        ax.text(z_right - 3, valley["price"], "Neckline", color="#787b86", fontsize=7.8, va="center")

        # 8. Setup & Position Tool (ONLY DRAWN WHEN PRICE IS IN ZONE OR TRIGGERED!)
        tp = result.trade_plan
        is_setup_active = (tp is not None and tp.status in ("TRIGGERED", "ARMED_IN_ZONE"))

        # Proximity test: Is current price touching either the supply or demand zone?
        is_in_supply = drawn_supply and (drawn_supply["bot"] - p * 10 <= current_price <= drawn_supply["top"] + p * 10)
        is_in_demand = drawn_demand and (drawn_demand["bot"] - p * 10 <= current_price <= drawn_demand["top"] + p * 10)

        if is_setup_active and (is_in_supply or is_in_demand or tp.status == "TRIGGERED"):
            # Price is in active execution zone -> Draw professional position tool!
            entry_p = tp.entry_price
            sl_p = tp.stop_loss
            tp_p = tp.take_profit_1

            box_start = n - 2
            box_end = z_right - 2

            if tp.direction == "SELL":
                # Short Position Tool: Red risk box on top, Cyan reward box on bottom
                ax.fill_between([box_start, box_end], entry_p, sl_p, color="#ef5350", alpha=0.40, zorder=2)
                ax.fill_between([box_start, box_end], entry_p, tp_p, color="#80deea", alpha=0.38, zorder=2)
                ax.plot([box_start, box_end], [entry_p, entry_p], color="#131722", linewidth=1.2, zorder=3)

                # Rejection projection path
                ax.annotate("", xy=(box_end - 1, tp_p), xytext=(box_start + 1, current_price),
                            arrowprops=dict(arrowstyle="->", color="#131722", lw=1.6, mutation_scale=14), zorder=5)
                ax.text(box_start + 2, (current_price + sl_p) / 2.0, "Short Setup", color="#c62828", fontsize=8.0, fontweight="bold")

                # Right axis tags
                ax.text(z_right + 0.8, sl_p, f" SL: {sl_p:.{d}f} ", color="#ffffff", backgroundcolor="#f23645", fontsize=8.2, fontweight="bold", va="center")
                ax.text(z_right + 0.8, entry_p, f" Entry: {entry_p:.{d}f} ", color="#ffffff", backgroundcolor="#131722", fontsize=8.2, fontweight="bold", va="center")
                ax.text(z_right + 0.8, tp_p, f" TP: {tp_p:.{d}f} ", color="#ffffff", backgroundcolor="#00acc1", fontsize=8.2, fontweight="bold", va="center")

            else:  # BUY
                # Long Position Tool: Red risk box on bottom, Cyan reward box on top
                ax.fill_between([box_start, box_end], entry_p, sl_p, color="#ef5350", alpha=0.40, zorder=2)
                ax.fill_between([box_start, box_end], entry_p, tp_p, color="#80deea", alpha=0.38, zorder=2)
                ax.plot([box_start, box_end], [entry_p, entry_p], color="#131722", linewidth=1.2, zorder=3)

                # Rejection projection path
                ax.annotate("", xy=(box_end - 1, tp_p), xytext=(box_start + 1, current_price),
                            arrowprops=dict(arrowstyle="->", color="#131722", lw=1.6, mutation_scale=14), zorder=5)
                ax.text(box_start + 2, (current_price + sl_p) / 2.0, "Long Setup", color="#2e7d32", fontsize=8.0, fontweight="bold")

                # Right axis tags
                ax.text(z_right + 0.8, sl_p, f" SL: {sl_p:.{d}f} ", color="#ffffff", backgroundcolor="#f23645", fontsize=8.2, fontweight="bold", va="center")
                ax.text(z_right + 0.8, entry_p, f" Entry: {entry_p:.{d}f} ", color="#ffffff", backgroundcolor="#131722", fontsize=8.2, fontweight="bold", va="center")
                ax.text(z_right + 0.8, tp_p, f" TP: {tp_p:.{d}f} ", color="#ffffff", backgroundcolor="#00acc1", fontsize=8.2, fontweight="bold", va="center")

        else:
            # NO active setup in zone (Status WAIT):
            # Display clean field of play! Mark current trajectory towards key liquidity.
            target_liq = ssl_price if trend == "BEARISH" else bsl_price
            target_lbl = "SSL Target" if trend == "BEARISH" else "BSL Target"
            ax.annotate("", xy=(n + 4, target_liq), xytext=(n - 2, current_price),
                        arrowprops=dict(arrowstyle="->", color="#787b86", lw=1.2, linestyle="--", mutation_scale=12), zorder=4)
            ax.text(n + 4.5, (current_price + target_liq) / 2.0, f" {target_lbl}", color="#50535e", fontsize=8.0, fontweight="bold", va="center")

        # 9. Current Price Line & Badge
        diff_price = current_price - float(df["open"].iloc[0])
        diff_pct = (diff_price / float(df["open"].iloc[0])) * 100.0

        ax.axhline(current_price, color="#2962ff", linestyle=":", linewidth=1.0, alpha=0.85)
        ax.text(z_right + 0.8, current_price, f" {current_price:.{d}f} ",
                color="#ffffff", backgroundcolor="#2962ff", fontsize=8.5, fontweight="bold", va="center")

        # 10. TradingView Header
        last_o = df["open"].iloc[-1]
        last_h = df["high"].iloc[-1]
        last_l = df["low"].iloc[-1]
        last_c = df["close"].iloc[-1]
        sign_str = "+" if diff_price >= 0 else ""
        diff_col = "#2962ff" if diff_price >= 0 else "#f23645"

        header_title = f"{result.display_name} · {tf} · FXCM"
        header_ohlc = f"O {last_o:.{d}f}   H {last_h:.{d}f}   L {last_l:.{d}f}   C {last_c:.{d}f}"
        header_diff = f"{sign_str}{diff_price:.{d}f} ({sign_str}{diff_pct:.2f}%)"

        ax.text(0.015, 0.965, header_title, transform=ax.transAxes,
                color="#131722", fontsize=9.5, fontweight="bold", va="top")
        ax.text(0.24, 0.965, header_ohlc, transform=ax.transAxes,
                color="#50535e", fontsize=9.0, va="top")
        ax.text(0.53, 0.965, header_diff, transform=ax.transAxes,
                color=diff_col, fontsize=9.0, fontweight="bold", va="top")

        # TradingView Watermark in Bottom Left
        ax.text(0.015, 0.03, "17  TradingView", transform=ax.transAxes,
                color="#b2b5be", fontsize=9.5, fontweight="bold", va="bottom")

        # Format X-axis Dates & Times
        self._format_time_axis(ax, df, z_right)

        # Proportional Y limits
        y_span = highest_high - lowest_low
        y_min = lowest_low - y_span * 0.06
        y_max = highest_high + y_span * 0.06
        ax.set_ylim(y_min, y_max)
        ax.set_xlim(-1, z_right + 6)

        plt.subplots_adjust(left=0.03, right=0.91, top=0.96, bottom=0.07)

        timestamp_str = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        filepath = os.path.join(self.output_dir, f"{result.symbol}_CLEAN_TV_{timestamp_str}.png")
        plt.savefig(filepath, facecolor=fig.get_facecolor(), edgecolor="none")
        plt.close(fig)

        return os.path.abspath(filepath)

    def _plot_candles(self, ax, df: pd.DataFrame, point: float, up_col: str, down_col: str):
        n = len(df)
        width = 0.58
        for i in range(n):
            o = df["open"].iloc[i]
            c = df["close"].iloc[i]
            h = df["high"].iloc[i]
            l = df["low"].iloc[i]
            is_up = c >= o
            col = up_col if is_up else down_col

            # Wicks
            ax.plot([i, i], [l, h], color=col, linewidth=0.95, zorder=2)
            # Body
            rect_bottom = min(o, c)
            rect_height = max(abs(c - o), point)
            rect = patches.Rectangle(
                (i - width / 2, rect_bottom),
                width,
                rect_height,
                facecolor=col,
                edgecolor=col,
                linewidth=0.8,
                zorder=2
            )
            ax.add_patch(rect)

    def _format_time_axis(self, ax, df: pd.DataFrame, z_right: int):
        n = len(df)
        indices = list(range(0, n, max(1, n // 8)))
        if (n - 1) not in indices:
            indices.append(n - 1)

        labels = []
        for idx in indices:
            ts = df.index[idx]
            if isinstance(ts, (datetime, pd.Timestamp)):
                labels.append(ts.strftime("%d %H:%M"))
            else:
                labels.append(str(ts)[-8:])

        ax.set_xticks(indices)
        ax.set_xticklabels(labels, fontsize=8.0, color="#50535e")
