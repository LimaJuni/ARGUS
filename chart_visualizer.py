import os
from datetime import datetime
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as patches
import pandas as pd
from anomaly_detector import TradeAnomalySetup

class ChartVisualizer:
    def __init__(self, output_dir: str = "snapshots"):
        self.output_dir = output_dir
        os.makedirs(self.output_dir, exist_ok=True)

    def generate_chart_snapshot(self, setup: TradeAnomalySetup) -> str:
        """
        Renders an institutional dark-mode candlestick chart with SMC overlay
        and execution levels scaled dynamically to the asset's precision.
        """
        df = setup.chart_data.copy()
        n = len(df)
        d = setup.digits

        fig, ax = plt.subplots(figsize=(13, 7), dpi=150)
        fig.patch.set_facecolor("#131722")
        ax.set_facecolor("#131722")

        width = 0.6
        up_color = "#089981"
        down_color = "#f23645"

        for i in range(n):
            o = df["open"].iloc[i]
            c = df["close"].iloc[i]
            h = df["high"].iloc[i]
            l = df["low"].iloc[i]
            col = up_color if c >= o else down_color

            ax.plot([i, i], [l, h], color=col, linewidth=1.2, zorder=2)
            rect_bottom = min(o, c)
            rect_height = max(abs(c - o), setup.point)
            rect = patches.Rectangle(
                (i - width / 2, rect_bottom),
                width,
                rect_height,
                facecolor=col,
                edgecolor=col,
                zorder=3
            )
            ax.add_patch(rect)

        # Highlight FVG if present
        if setup.fvg_zone:
            fvg_col = "#00E676" if setup.fvg_zone.direction == "BULLISH" else "#FF5252"
            ax.axhspan(
                setup.fvg_zone.bottom,
                setup.fvg_zone.top,
                xmin=0.5,
                xmax=1.0,
                color=fvg_col,
                alpha=0.18,
                label=f"Active FVG [{setup.fvg_zone.direction}]",
                zorder=1
            )

        # Highlight Order Block if present
        if setup.order_block:
            ob_col = "#29B6F6" if setup.order_block.direction == "BULLISH" else "#AB47BC"
            ax.axhspan(
                setup.order_block.bottom,
                setup.order_block.top,
                xmin=0.4,
                xmax=1.0,
                color=ob_col,
                alpha=0.22,
                label=f"Order Block [{setup.order_block.direction}]",
                zorder=1
            )

        # Highlight Liquidity Sweep Level
        if setup.sweep:
            ax.axhline(
                setup.sweep.swept_level,
                color="#FFEB3B",
                linestyle="--",
                linewidth=1.2,
                label=f"Swept Pool ({setup.sweep.swept_level:.{d}f})"
            )

        # Trade Execution Levels
        # Entry
        ax.axhline(setup.entry_price, color="#2196F3", linestyle="-", linewidth=1.4)
        ax.text(n - 1, setup.entry_price, f" ENTRY: {setup.entry_price:.{d}f}", color="#2196F3", fontweight="bold", va="center")

        # Stop Loss
        ax.axhline(setup.stop_loss, color="#FF1744", linestyle="--", linewidth=1.4)
        ax.text(n - 1, setup.stop_loss, f" SL: {setup.stop_loss:.{d}f}", color="#FF1744", fontweight="bold", va="center")

        # Take Profit 1 & 2
        ax.axhline(setup.take_profit_1, color="#00E676", linestyle="-.", linewidth=1.4)
        ax.text(n - 1, setup.take_profit_1, f" TP1: {setup.take_profit_1:.{d}f}", color="#00E676", fontweight="bold", va="center")

        ax.axhline(setup.take_profit_2, color="#00B0FF", linestyle=":", linewidth=1.4)
        ax.text(n - 1, setup.take_profit_2, f" TP2: {setup.take_profit_2:.{d}f}", color="#00B0FF", fontweight="bold", va="center")

        # Axes & Styling
        ax.grid(True, color="#2A2E39", linestyle="--", alpha=0.5)
        ax.tick_params(colors="#D1D4DC", which="both")
        ax.spines["bottom"].set_color("#2A2E39")
        ax.spines["top"].set_color("#2A2E39")
        ax.spines["left"].set_color("#2A2E39")
        ax.spines["right"].set_color("#2A2E39")

        step = max(1, n // 8)
        selected_indices = list(range(0, n, step))
        if selected_indices[-1] != n - 1:
            selected_indices.append(n - 1)
        ax.set_xticks(selected_indices)
        ax.set_xticklabels([df.index[idx].strftime("%H:%M") for idx in selected_indices], rotation=0)

        title_color = "#00E676" if setup.direction == "BUY" else "#FF5252"
        plt.title(
            f"[{setup.direction}] {setup.display_name} ({setup.timeframe}) | {setup.setup_name}\n"
            f"R:R 1:{setup.risk_reward_ratio:.2f} | Spread: {setup.spread_points:.0f} pts | Macro Bias: {setup.macro_bias}",
            color=title_color,
            fontsize=12,
            fontweight="bold",
            pad=14
        )

        plt.tight_layout()

        timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{setup.symbol}_{setup.direction}_{timestamp_str}.png"
        filepath = os.path.join(self.output_dir, filename)
        plt.savefig(filepath, facecolor=fig.get_facecolor(), edgecolor="none")
        plt.close(fig)

        return os.path.abspath(filepath)
