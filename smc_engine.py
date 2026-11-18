"""
Smart Money Concepts (SMC) & Institutional Price Action Engine.
Detects:
1. Order Blocks (Bullish OB+ and Bearish OB-) with displacement validation & mitigation tracking.
2. Fair Value Gaps (Bullish & Bearish FVGs / Imbalances) with mitigation tracking.
3. Liquidity Pools (Equal Highs - BSL and Equal Lows - SSL).
4. Market Structure Shifts (BOS & CHoCH).
"""

from dataclasses import dataclass
from typing import List, Optional, Tuple, Dict
import pandas as pd
import numpy as np

@dataclass
class OrderBlock:
    timeframe: str
    ob_type: str             # "BULLISH" or "BEARISH"
    bar_index: int
    time: pd.Timestamp
    top: float
    bottom: float
    midpoint: float          # 50% Mean Threshold of the Order Block
    displacement_atr: float  # How many ATRs of displacement followed
    touches: int = 0
    mitigated: bool = False
    is_fresh: bool = True

@dataclass
class FairValueGap:
    timeframe: str
    fvg_type: str            # "BULLISH" or "BEARISH"
    bar_index: int
    time: pd.Timestamp
    top: float
    bottom: float
    midpoint: float          # 50% Consequent Encroachment (CE)
    gap_size: float
    mitigated: bool = False
    is_fresh: bool = True

@dataclass
class LiquidityPool:
    pool_type: str           # "BSL" (Buy-Side Liquidity / Equal Highs) or "SSL" (Sell-Side Liquidity / Equal Lows)
    price: float
    bar_indices: List[int]
    description: str


class SMCEngine:
    def __init__(self, atr_period: int = 14):
        self.atr_period = atr_period

    def _calc_atr(self, df: pd.DataFrame) -> pd.Series:
        high = df["high"]
        low = df["low"]
        close_prev = df["close"].shift(1)
        tr = np.maximum(high - low, np.maximum((high - close_prev).abs(), (low - close_prev).abs()))
        return tr.rolling(self.atr_period).mean().bfill()

    def detect_order_blocks(self, df: pd.DataFrame, tf: str, impulse_atr_mult: float = 1.2) -> List[OrderBlock]:
        """
        Locates institutional Order Blocks (OB):
        - Bullish OB: The last down candle (or small consolidation candle) before a powerful
          bullish displacement candle.
        - Bearish OB: The last up candle before a powerful bearish displacement candle.
        Tracks touches and mitigation.
        """
        obs: List[OrderBlock] = []
        n = len(df)
        if n < 15:
            return obs

        atr = self._calc_atr(df)

        for i in range(2, n - 2):
            cur_atr = atr.iloc[i]
            body_disp = abs(df["close"].iloc[i] - df["open"].iloc[i])
            is_impulsive = body_disp >= cur_atr * impulse_atr_mult

            # Potential Bullish OB: Impulsive green candle
            if is_impulsive and df["close"].iloc[i] > df["open"].iloc[i]:
                ob_idx = i - 1
                ob_high = df["high"].iloc[ob_idx]
                ob_low = df["low"].iloc[ob_idx]
                # Filter out absurdly huge candles
                if (ob_high - ob_low) > cur_atr * 3.5 or (ob_high - ob_low) < cur_atr * 0.1:
                    continue

                ob = OrderBlock(
                    timeframe=tf,
                    ob_type="BULLISH",
                    bar_index=ob_idx,
                    time=df.index[ob_idx],
                    top=round(ob_high, 4),
                    bottom=round(ob_low, 4),
                    midpoint=round((ob_high + ob_low) / 2.0, 4),
                    displacement_atr=round(body_disp / max(cur_atr, 1e-5), 2)
                )

                # Check subsequent price action for touches & mitigation
                touches = 0
                for j in range(i + 1, n):
                    # Touched if low enters zone
                    if df["low"].iloc[j] <= ob.top and df["high"].iloc[j] >= ob.bottom:
                        touches += 1
                    # Mitigated if close breaks below bottom
                    if df["close"].iloc[j] < ob.bottom:
                        ob.mitigated = True
                        break

                ob.touches = touches
                ob.is_fresh = (touches <= 2 and not ob.mitigated)
                if not ob.mitigated:
                    obs.append(ob)

            # Potential Bearish OB: Impulsive red candle
            elif is_impulsive and df["close"].iloc[i] < df["open"].iloc[i]:
                ob_idx = i - 1
                ob_high = df["high"].iloc[ob_idx]
                ob_low = df["low"].iloc[ob_idx]
                if (ob_high - ob_low) > cur_atr * 3.5 or (ob_high - ob_low) < cur_atr * 0.1:
                    continue

                ob = OrderBlock(
                    timeframe=tf,
                    ob_type="BEARISH",
                    bar_index=ob_idx,
                    time=df.index[ob_idx],
                    top=round(ob_high, 4),
                    bottom=round(ob_low, 4),
                    midpoint=round((ob_high + ob_low) / 2.0, 4),
                    displacement_atr=round(body_disp / max(cur_atr, 1e-5), 2)
                )

                touches = 0
                for j in range(i + 1, n):
                    if df["high"].iloc[j] >= ob.bottom and df["low"].iloc[j] <= ob.top:
                        touches += 1
                    if df["close"].iloc[j] > ob.top:
                        ob.mitigated = True
                        break

                ob.touches = touches
                ob.is_fresh = (touches <= 2 and not ob.mitigated)
                if not ob.mitigated:
                    obs.append(ob)

        return obs

    def detect_fair_value_gaps(self, df: pd.DataFrame, tf: str, min_gap_atr: float = 0.25) -> List[FairValueGap]:
        """
        Locates 3-candle Fair Value Gaps (FVG / Imbalances):
        - Bullish FVG: Low of candle 3 is higher than High of candle 1.
        - Bearish FVG: High of candle 3 is lower than Low of candle 1.
        """
        fvgs: List[FairValueGap] = []
        n = len(df)
        if n < 10:
            return fvgs

        atr = self._calc_atr(df)

        for i in range(2, n):
            cur_atr = atr.iloc[i]
            c1_high = df["high"].iloc[i - 2]
            c1_low = df["low"].iloc[i - 2]
            c3_high = df["high"].iloc[i]
            c3_low = df["low"].iloc[i]

            # Bullish FVG: Gap between candle 1 high and candle 3 low
            if c3_low > c1_high:
                gap = c3_low - c1_high
                if gap >= cur_atr * min_gap_atr:
                    fvg = FairValueGap(
                        timeframe=tf,
                        fvg_type="BULLISH",
                        bar_index=i - 1,
                        time=df.index[i - 1],
                        top=round(c3_low, 4),
                        bottom=round(c1_high, 4),
                        midpoint=round((c3_low + c1_high) / 2.0, 4),
                        gap_size=round(gap, 4)
                    )
                    # Check mitigation
                    for j in range(i + 1, n):
                        if df["low"].iloc[j] <= fvg.bottom:
                            fvg.mitigated = True
                            break
                    fvg.is_fresh = not fvg.mitigated
                    if not fvg.mitigated:
                        fvgs.append(fvg)

            # Bearish FVG: Gap between candle 3 high and candle 1 low
            elif c3_high < c1_low:
                gap = c1_low - c3_high
                if gap >= cur_atr * min_gap_atr:
                    fvg = FairValueGap(
                        timeframe=tf,
                        fvg_type="BEARISH",
                        bar_index=i - 1,
                        time=df.index[i - 1],
                        top=round(c1_low, 4),
                        bottom=round(c3_high, 4),
                        midpoint=round((c1_low + c3_high) / 2.0, 4),
                        gap_size=round(gap, 4)
                    )
                    # Check mitigation
                    for j in range(i + 1, n):
                        if df["high"].iloc[j] >= fvg.top:
                            fvg.mitigated = True
                            break
                    fvg.is_fresh = not fvg.mitigated
                    if not fvg.mitigated:
                        fvgs.append(fvg)

        return fvgs

    def detect_liquidity_pools(self, df: pd.DataFrame, swings: list, tolerance_pct: float = 0.0015) -> List[LiquidityPool]:
        """
        Locates Buy-Side Liquidity (BSL / Equal Highs) and Sell-Side Liquidity (SSL / Equal Lows).
        """
        pools: List[LiquidityPool] = []
        highs = [s for s in swings if s.point_type == "HIGH"]
        lows = [s for s in swings if s.point_type == "LOW"]

        if len(highs) >= 2:
            h1, h2 = highs[-2], highs[-1]
            avg_h = (h1.price + h2.price) / 2.0
            if abs(h1.price - h2.price) / avg_h <= tolerance_pct:
                pools.append(LiquidityPool(
                    pool_type="BSL",
                    price=round(avg_h, 4),
                    bar_indices=[h1.bar_index, h2.bar_index],
                    description=f"Equal Highs / Buy-Side Liquidity (BSL) at {avg_h:.2f}"
                ))

        if len(lows) >= 2:
            l1, l2 = lows[-2], lows[-1]
            avg_l = (l1.price + l2.price) / 2.0
            if abs(l1.price - l2.price) / avg_l <= tolerance_pct:
                pools.append(LiquidityPool(
                    pool_type="SSL",
                    price=round(avg_l, 4),
                    bar_indices=[l1.bar_index, l2.bar_index],
                    description=f"Equal Lows / Sell-Side Liquidity (SSL) at {avg_l:.2f}"
                ))

        return pools
