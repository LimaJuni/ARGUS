"""
Professional Multi-Timeframe Chart Visualizer (Institutional Pro Trader Style).
Renders high-definition institutional dark-mode TradingView-style charts showing:
- Panel 1 (Top): Macro Structure (4H / Daily)
  * Dynamic Trendlines (Support & Resistance)
  * Fibonacci Retracement Grid (0.382, 0.500 Equilibrium, 0.618 Golden Pocket, 0.786)
  * Classic Chart Patterns (Double Top/Bottom 'M'/'W' with Neckline & Target X, Head & Shoulders, Triangles)
  * 4H/Daily Order Blocks (+OB / -OB) & Fair Value Gaps (FVG Imbalances)
  * Fresh Supply & Demand Zones with touch counts
  * Market Structure Shifts (BOS & CHoCH)
  * 50 EMA & 200 EMA Trend Averages
  * Institutional HUD Card (Symbol, Bias, Valuation, Killzone)
- Panel 2 (Bottom): Execution (15m)
  * 15m Execution Candlesticks with 20 EMA & 50 EMA
  * 15m Order Blocks & FVGs
  * Price Action Rejection Markers (Pin Bar / Hammer / Engulfing)
  * Mini BOS Structure Shift
  * Complete Trade Plan Risk/Reward Box (Entry, Invalidation SL, BE 1:1, TP1 1:2, TP2 Runner)
"""

import os
from datetime import datetime
from typing import List, Optional, Dict
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as patches
import pandas as pd
import numpy as np

from mtf_analyzer import CompleteAnalysisResult, SupplyDemandZone, SwingPoint
from smc_engine import OrderBlock, FairValueGap, LiquidityPool
from pattern_engine import ChartPattern, Trendline, FibonacciSetup
from institutional_layers import SessionKillzoneManager, InstitutionalValuation


class MTFChartVisualizer:
    def __init__(self, output_dir: str = "snapshots"):
        self.output_dir = output_dir
        os.makedirs(self.output_dir, exist_ok=True)

    def generate_mtf_snapshot(self, result: CompleteAnalysisResult, dfs: dict) -> str:
        """
        Draws an institutional pro-grade multi-timeframe chart:
        - Top: H4 / Daily Macro Structure with SMC Order Blocks, FVGs, Patterns, Fibs, and Trendlines
        - Bottom: M15 Execution with 15m confirmations and complete Risk/Reward plan
        """
        htf_tf = "H4" if "H4" in dfs else ("D1" if "D1" in dfs else list(dfs.keys())[0])
        ltf_tf = "M15" if "M15" in dfs else ("H1" if "H1" in dfs else list(dfs.keys())[-1])

        df_htf = dfs.get(htf_tf).tail(60).copy()
        df_ltf = dfs.get(ltf_tf).tail(60).copy()
        d = result.digits
        p = result.point

        fig, (ax_htf, ax_ltf) = plt.subplots(
            2, 1, figsize=(16, 12), dpi=150,
            gridspec_kw={"height_ratios": [1.15, 1.0], "hspace": 0.22}
        )
        fig.patch.set_facecolor("#0b0e14")

        # Styling base axes
        for ax in (ax_htf, ax_ltf):
            ax.set_facecolor("#11151f")
            ax.grid(True, color="#1e2638", linestyle="--", linewidth=0.6, alpha=0.7)
            ax.tick_params(colors="#94a3b8", labelsize=8.5)
            for spine in ax.spines.values():
                spine.set_color("#1e2638")

        up_col = "#089981"
        down_col = "#f23645"

        # =====================================================================
        # TOP PANEL: Macro View (H4/D1)
        # =====================================================================
        self._plot_candles(ax_htf, df_htf, p, up_col, down_col)
        self._plot_moving_averages(ax_htf, df_htf, [50, 200], ["#06b6d4", "#a855f7"])

        htf_tfa = result.tf_analyses.get(htf_tf)

        # 1. Higher Timeframe Supply & Demand Zones
        if htf_tfa:
            for z in htf_tfa.demand_zones:
                if z.is_fresh:
                    ax_htf.axhspan(z.bottom, z.top, color="#059669", alpha=0.18, zorder=1)
                    ax_htf.axhline(z.top, color="#059669", linestyle=":", linewidth=0.7, alpha=0.5)
                    ax_htf.axhline(z.bottom, color="#059669", linestyle=":", linewidth=0.7, alpha=0.5)
                    ax_htf.text(len(df_htf) - 1, (z.top + z.bottom) / 2, f"  {htf_tf} DEMAND [{z.touches}t]",
                                color="#34d399", fontsize=7.5, va="center", fontweight="bold")

            for z in htf_tfa.supply_zones:
                if z.is_fresh:
                    ax_htf.axhspan(z.bottom, z.top, color="#dc2626", alpha=0.18, zorder=1)
                    ax_htf.axhline(z.top, color="#dc2626", linestyle=":", linewidth=0.7, alpha=0.5)
                    ax_htf.axhline(z.bottom, color="#dc2626", linestyle=":", linewidth=0.7, alpha=0.5)
                    ax_htf.text(len(df_htf) - 1, (z.top + z.bottom) / 2, f"  {htf_tf} SUPPLY [{z.touches}t]",
                                color="#f87171", fontsize=7.5, va="center", fontweight="bold")

        # 2. Higher Timeframe Order Blocks (OB)
        htf_obs = result.order_blocks.get(htf_tf, [])
        for ob in htf_obs[-4:]:
            if ob.is_fresh:
                ob_col = "#10b981" if ob.ob_type == "BULLISH" else "#f43f5e"
                lbl_type = "+OB" if ob.ob_type == "BULLISH" else "-OB"
                # Draw Order Block rectangle
                ax_htf.axhspan(ob.bottom, ob.top, color=ob_col, alpha=0.15, zorder=1)
                ax_htf.text(len(df_htf) - 1, (ob.top + ob.bottom) / 2, f"  {lbl_type} [{htf_tf}]",
                            color=ob_col, fontsize=7.5, va="center", fontweight="bold")

        # 3. Higher Timeframe Fair Value Gaps (FVG)
        htf_fvgs = result.fair_value_gaps.get(htf_tf, [])
        for fvg in htf_fvgs[-3:]:
            if fvg.is_fresh:
                fvg_col = "#38bdf8" if fvg.fvg_type == "BULLISH" else "#fbbf24"
                ax_htf.axhspan(fvg.bottom, fvg.top, color=fvg_col, alpha=0.12, zorder=1)
                ax_htf.text(len(df_htf) - 1, (fvg.top + fvg.bottom) / 2, f"  FVG [{htf_tf}]",
                            color=fvg_col, fontsize=7.0, va="center", style="italic")

        # 4. Market Structure Breaks (BOS / CHoCH)
        if htf_tfa and htf_tfa.last_event:
            ev = htf_tfa.last_event
            ev_col = "#10b981" if ev.direction == "BULLISH" else "#f43f5e"
            ev_lbl = f"{ev.event_type} ↑" if ev.direction == "BULLISH" else f"{ev.event_type} ↓"
            ax_htf.axhline(ev.broken_level, color=ev_col, linestyle="--", linewidth=1.2, alpha=0.75, zorder=3)
            ax_htf.text(len(df_htf) // 3, ev.broken_level, f" {ev_lbl} ({ev.broken_level:.{d}f}) ",
                        color="#ffffff", backgroundcolor=ev_col, fontsize=7.5, fontweight="bold", va="center")

        # 5. Dynamic Trendlines
        htf_tls = result.trendlines.get(htf_tf, [])
        for tl in htf_tls:
            self._draw_trendline(ax_htf, df_htf, tl)

        # 6. Fibonacci Retracement Levels
        fib = result.fibonacci.get(htf_tf)
        if fib:
            self._draw_fibonacci(ax_htf, df_htf, fib, d)

        # 7. Classic Chart Patterns (Yoosuf Khan)
        htf_patterns = result.patterns.get(htf_tf, [])
        for pat in htf_patterns:
            self._draw_pattern(ax_htf, df_htf, pat, d)

        # 8. Institutional Pro HUD Overlay Box
        self._draw_pro_hud(ax_htf, result, htf_tf)

        macro_bias_col = "#10b981" if result.daily_bias == "BULLISH" else ("#f43f5e" if result.daily_bias == "BEARISH" else "#facc15")
        ax_htf.set_title(
            f"MACRO STRUCTURE ({htf_tf} / DAILY) — Primary Trend: {result.daily_bias} (Daily is Boss)",
            color=macro_bias_col, fontsize=11, fontweight="bold", pad=9, loc="left"
        )

        # =====================================================================
        # BOTTOM PANEL: Execution View (15m)
        # =====================================================================
        self._plot_candles(ax_ltf, df_ltf, p, up_col, down_col)
        self._plot_moving_averages(ax_ltf, df_ltf, [20, 50], ["#84cc16", "#06b6d4"])

        # 1. 15m Order Blocks & FVGs
        ltf_obs = result.order_blocks.get(ltf_tf, [])
        for ob in ltf_obs[-2:]:
            if ob.is_fresh:
                ob_col = "#10b981" if ob.ob_type == "BULLISH" else "#f43f5e"
                ax_ltf.axhspan(ob.bottom, ob.top, color=ob_col, alpha=0.15, zorder=1)
                ax_ltf.text(len(df_ltf) - 1, (ob.top + ob.bottom) / 2, f"  {ob.ob_type[0]}OB [15m]",
                            color=ob_col, fontsize=7.0, va="center")

        ltf_fvgs = result.fair_value_gaps.get(ltf_tf, [])
        for fvg in ltf_fvgs[-2:]:
            if fvg.is_fresh:
                fvg_col = "#38bdf8" if fvg.fvg_type == "BULLISH" else "#fbbf24"
                ax_ltf.axhspan(fvg.bottom, fvg.top, color=fvg_col, alpha=0.12, zorder=1)
                ax_ltf.text(len(df_ltf) - 1, (fvg.top + fvg.bottom) / 2, f"  FVG [15m]",
                            color=fvg_col, fontsize=6.8, va="center")

        tp = result.trade_plan
        if tp:
            # Draw Trade Plan Execution Zones & Levels
            self._draw_trade_plan(ax_ltf, df_ltf, tp, d)
            plan_title = (
                f"15-MINUTE EXECUTION — [{tp.direction} CONFIRMED] | R:R 1:{tp.risk_reward_1} | "
                f"SL: {tp.sl_pips_or_points:.1f} pts | Sizing: {tp.recommended_lot_size} Lots"
            )
            plan_color = "#10b981" if tp.direction == "BUY" else "#f43f5e"
        else:
            plan_title = "15-MINUTE EXECUTION — Status: PATIENCE (Awaiting zone tap & 15m double confirmation)"
            plan_color = "#94a3b8"

        ax_ltf.set_title(plan_title, color=plan_color, fontsize=10.5, fontweight="bold", pad=9, loc="left")

        # Format timestamps on X axes
        self._format_time_axis(ax_htf, df_htf)
        self._format_time_axis(ax_ltf, df_ltf)

        plt.subplots_adjust(left=0.05, right=0.92, top=0.95, bottom=0.06)

        timestamp_str = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        filepath = os.path.join(self.output_dir, f"{result.symbol}_PRO_{timestamp_str}.png")
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
            col = up_col if c >= o else down_col

            # Wick line
            ax.plot([i, i], [l, h], color=col, linewidth=0.9, zorder=2)
            # Body rectangle
            rect_bottom = min(o, c)
            rect_height = max(abs(c - o), point)
            rect = patches.Rectangle(
                (i - width / 2, rect_bottom),
                width,
                rect_height,
                facecolor=col,
                edgecolor=col,
                linewidth=0.5,
                zorder=3
            )
            ax.add_patch(rect)
        ax.set_xlim(-1, n + 8)

    def _plot_moving_averages(self, ax, df: pd.DataFrame, periods: List[int], colors: List[str]):
        for period, color in zip(periods, colors):
            if len(df) >= period // 2:
                ema = df["close"].ewm(span=period).mean()
                ax.plot(range(len(df)), ema.values, color=color, linewidth=1.1, alpha=0.8,
                        label=f"{period} EMA", zorder=2)

    def _draw_trendline(self, ax, df: pd.DataFrame, tl: Trendline):
        col = "#38bdf8" if tl.direction == "SUPPORT" else "#f43f5e"
        x_vals = [tl.start_idx, tl.end_idx]
        y_vals = [tl.start_price, tl.end_price]
        ax.plot(x_vals, y_vals, color=col, linestyle="--", linewidth=1.3, alpha=0.85, zorder=3)
        # Touch markers
        ax.scatter([tl.start_idx], [tl.start_price], color=col, s=25, zorder=4)
        ax.text(tl.end_idx, tl.end_price, f" {tl.direction.title()} TL", color=col, fontsize=7.5, va="center")

    def _draw_fibonacci(self, ax, df: pd.DataFrame, fib: FibonacciSetup, digits: int):
        n = len(df)
        x_start = max(0, n - 45)
        x_end = n + 4

        levels = [
            (fib.swing_high if fib.direction == "UPTREND" else fib.swing_low, "0.0% (Swing Ext)", "#64748b", ":"),
            (fib.level_382, "38.2% Retrace", "#94a3b8", ":"),
            (fib.level_500, "50.0% Equilibrium", "#38bdf8", "--"),
            (fib.level_618, "61.8% Golden Ratio", "#f59e0b", "-"),
            (fib.level_786, "78.6% Deep Retrace", "#cbd5e1", ":"),
            (fib.swing_low if fib.direction == "UPTREND" else fib.swing_high, "100.0% (Origin)", "#64748b", ":")
        ]

        # Golden Pocket Shading (between 50% and 61.8%)
        top_pocket = max(fib.level_500, fib.level_618)
        bot_pocket = min(fib.level_500, fib.level_618)
        ax.fill_between([x_start, x_end], bot_pocket, top_pocket, color="#f59e0b", alpha=0.10, zorder=1)

        for price, label, col, ls in levels:
            ax.plot([x_start, x_end], [price, price], color=col, linestyle=ls, linewidth=0.9, alpha=0.7, zorder=2)
            is_gold = "61.8%" in label
            font_wt = "bold" if is_gold else "normal"
            ax.text(x_end, price, f" {label}: {price:.{digits}f}", color=col, fontsize=7.2,
                    fontweight=font_wt, va="center")

    def _draw_pattern(self, ax, df: pd.DataFrame, pat: ChartPattern, digits: int):
        col = "#10b981" if pat.direction == "BULLISH" else "#f43f5e"
        n = len(df)

        # Draw Neckline
        ax.axhline(pat.key_level_2, color="#facc15", linestyle="-.", linewidth=1.2, alpha=0.8, zorder=3)
        ax.text(n - 1, pat.key_level_2, f" Neckline: {pat.key_level_2:.{digits}f}",
                color="#facc15", fontsize=7.5, va="center", fontweight="bold")

        # Draw Target X line
        ax.axhline(pat.target_price, color=col, linestyle=":", linewidth=1.3, alpha=0.85, zorder=3)
        ax.text(n - 1, pat.target_price, f" Target X: {pat.target_price:.{digits}f}",
                color=col, fontsize=7.5, va="center", fontweight="bold")

        # Pattern Label Badge
        badge_y = (pat.key_level_1 + pat.key_level_2) / 2.0
        ax.text(n - 15, badge_y, f"  {pat.name.replace('_', ' ')}  ",
                color="#ffffff", backgroundcolor=col, fontsize=8.0, fontweight="bold", va="center")

    def _draw_pro_hud(self, ax, result: CompleteAnalysisResult, tf: str):
        kz = SessionKillzoneManager.get_current_session_info()
        d = result.digits

        hud_text = (
            f"● {result.display_name}  {result.current_price:.{d}f}\n"
            f"Bias: {result.daily_bias} | Active: {kz['killzone']}\n"
            f"Feed: {result.data_source} (Real-time)"
        )

        ax.text(
            0.015, 0.95, hud_text,
            transform=ax.transAxes,
            fontsize=8.5,
            fontfamily="sans-serif",
            color="#e2e8f0",
            verticalalignment="top",
            bbox=dict(boxstyle="round,pad=0.5", facecolor="#1a202c", edgecolor="#334155", alpha=0.9)
        )

    def _draw_trade_plan(self, ax, df: pd.DataFrame, tp, digits: int):
        n = len(df)
        x_start = max(0, n - 25)
        x_end = n + 6

        is_buy = tp.direction == "BUY"
        plan_col = "#10b981" if is_buy else "#f43f5e"

        # 1. Shaded Risk Box (Entry to Stop Loss)
        ax.fill_between([x_start, x_end], tp.entry_price, tp.stop_loss,
                        color="#ef4444", alpha=0.18, zorder=1)

        # 2. Shaded Profit Box (Entry to TP1)
        ax.fill_between([x_start, x_end], tp.entry_price, tp.take_profit_1,
                        color="#10b981", alpha=0.18, zorder=1)

        # 3. Horizontal Entry, SL, BE, TP1, TP2 lines
        ax.plot([x_start, x_end], [tp.entry_price, tp.entry_price], color="#38bdf8", linewidth=1.6, zorder=4)
        ax.text(x_end, tp.entry_price, f" ENTRY: {tp.entry_price:.{digits}f}",
                color="#38bdf8", fontweight="bold", fontsize=8.5, va="center")

        ax.plot([x_start, x_end], [tp.stop_loss, tp.stop_loss], color="#f43f5e", linestyle="--", linewidth=1.5, zorder=4)
        ax.text(x_end, tp.stop_loss, f" SL: {tp.stop_loss:.{digits}f} (-{tp.sl_pips_or_points:.1f} pts)",
                color="#f43f5e", fontweight="bold", fontsize=8.5, va="center")

        ax.plot([x_start, x_end], [tp.break_even_price, tp.break_even_price], color="#facc15", linestyle=":", linewidth=1.2, zorder=4)
        ax.text(x_end, tp.break_even_price, f" BE 1:1: {tp.break_even_price:.{digits}f}",
                color="#facc15", fontsize=7.8, va="center")

        ax.plot([x_start, x_end], [tp.take_profit_1, tp.take_profit_1], color="#10b981", linestyle="-.", linewidth=1.5, zorder=4)
        ax.text(x_end, tp.take_profit_1, f" TP1 (1:2 - 50%): {tp.take_profit_1:.{digits}f}",
                color="#10b981", fontweight="bold", fontsize=8.5, va="center")

        ax.plot([x_start, x_end], [tp.take_profit_2, tp.take_profit_2], color="#06b6d4", linestyle=":", linewidth=1.5, zorder=4)
        ax.text(x_end, tp.take_profit_2, f" TP2 (Runner): {tp.take_profit_2:.{digits}f}",
                color="#06b6d4", fontweight="bold", fontsize=8.0, va="center")

        # Rejection Candle Marker
        if tp.rejection_pattern:
            rep_price = tp.rejection_pattern.price
            ax.scatter([n - 2], [rep_price], color="#facc15", s=80, marker="^" if is_buy else "v", zorder=5)
            ax.text(n - 2, rep_price, f"  {tp.rejection_pattern.name.replace('_', ' ')}",
                    color="#facc15", fontsize=7.8, fontweight="bold", va="bottom" if is_buy else "top")

    def _format_time_axis(self, ax, df: pd.DataFrame):
        n = len(df)
        step = max(1, n // 7)
        indices = list(range(0, n, step))
        if indices[-1] != n - 1:
            indices.append(n - 1)
        ax.set_xticks(indices)
        ax.set_xticklabels([df.index[idx].strftime("%b %d %H:%M") for idx in indices], rotation=0)
