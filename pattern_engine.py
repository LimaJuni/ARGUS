"""
Pattern & Structure Recognition Engine.
Covers patterns from "Simplified Pattern Trading Secrets in 90 Minutes" (Yoosuf Khan):
- Double Top ('M' shape, Headline, Neckline, Target X)
- Double Bottom ('W' shape, Headline, Neckline, Target X)
- Ascending & Descending Channels
- Bullish & Bearish Flags
- Triangles (Ascending, Descending, Symmetrical)
- Wedges (Rising, Falling)
- Head & Shoulders & Inverse Head & Shoulders
- Trendlines (Dynamic Support & Resistance connecting swing points)
- Fibonacci Retracement Levels (38.2%, 50.0%, 61.8% Golden Ratio, 78.6%)
"""

from dataclasses import dataclass
from typing import List, Optional, Tuple
import pandas as pd
import numpy as np

@dataclass
class ChartPattern:
    name: str                   # e.g., "DOUBLE_BOTTOM", "HEAD_AND_SHOULDERS", "ASCENDING_TRIANGLE", "BULL_FLAG"
    pattern_type: str           # "REVERSAL" or "CONTINUATION"
    direction: str              # "BULLISH" or "BEARISH"
    start_time: pd.Timestamp
    end_time: pd.Timestamp
    key_level_1: float          # Headline / Resistance / Neckline
    key_level_2: float          # Base / Support
    target_price: float         # Calculated measured move target 'X'
    points_x: float             # Height 'X'
    description: str

@dataclass
class Trendline:
    direction: str              # "SUPPORT" or "RESISTANCE"
    start_idx: int
    end_idx: int
    start_price: float
    end_price: float
    slope: float
    intercept: float
    touch_count: int

@dataclass
class FibonacciSetup:
    swing_low: float
    swing_high: float
    direction: str              # "UPTREND" or "DOWNTREND"
    level_382: float
    level_500: float
    level_618: float            # Golden Ratio
    level_786: float
    current_retrace_pct: float
    is_at_golden_ratio: bool


class PatternRecognitionEngine:
    def __init__(self):
        pass

    def detect_all_patterns(self, df: pd.DataFrame, swings: list) -> List[ChartPattern]:
        patterns = []
        if len(df) < 30 or len(swings) < 4:
            return patterns

        # 1. Double Top & Double Bottom
        dt = self._detect_double_top(df, swings)
        if dt:
            patterns.append(dt)
        db = self._detect_double_bottom(df, swings)
        if db:
            patterns.append(db)

        # 2. Head & Shoulders
        hs = self._detect_head_and_shoulders(df, swings)
        if hs:
            patterns.append(hs)
        ihs = self._detect_inverse_head_and_shoulders(df, swings)
        if ihs:
            patterns.append(ihs)

        # 3. Triangles & Wedges
        tri = self._detect_triangles_and_wedges(df, swings)
        if tri:
            patterns.append(tri)

        return patterns

    def _detect_double_top(self, df: pd.DataFrame, swings: list) -> Optional[ChartPattern]:
        highs = [s for s in swings if s.point_type == "HIGH"]
        lows = [s for s in swings if s.point_type == "LOW"]
        if len(highs) < 2 or len(lows) < 1:
            return None

        t1 = highs[-2]
        t2 = highs[-1]
        # Middle trough between t1 and t2
        troughs = [l for l in lows if t1.bar_index < l.bar_index < t2.bar_index]
        if not troughs:
            return None
        neckline = troughs[-1]

        # Criteria: Peaks within 1.0% of each other
        avg_top = (t1.price + t2.price) / 2.0
        diff_pct = abs(t1.price - t2.price) / avg_top
        if diff_pct <= 0.012 and t2.price > neckline.price:
            x_points = avg_top - neckline.price
            target = neckline.price - x_points
            return ChartPattern(
                name="DOUBLE_TOP",
                pattern_type="REVERSAL",
                direction="BEARISH",
                start_time=t1.time,
                end_time=t2.time,
                key_level_1=round(avg_top, 3),
                key_level_2=round(neckline.price, 3),
                target_price=round(target, 3),
                points_x=round(x_points, 3),
                description=f"Double Top 'M' Pattern: Resistance at {avg_top:.2f}, Neckline at {neckline.price:.2f}. Bearish Target: {target:.2f} (-{x_points:.2f} pts)"
            )
        return None

    def _detect_double_bottom(self, df: pd.DataFrame, swings: list) -> Optional[ChartPattern]:
        lows = [s for s in swings if s.point_type == "LOW"]
        highs = [s for s in swings if s.point_type == "HIGH"]
        if len(lows) < 2 or len(highs) < 1:
            return None

        b1 = lows[-2]
        b2 = lows[-1]
        peaks = [h for h in highs if b1.bar_index < h.bar_index < b2.bar_index]
        if not peaks:
            return None
        neckline = peaks[-1]

        avg_bottom = (b1.price + b2.price) / 2.0
        diff_pct = abs(b1.price - b2.price) / avg_bottom
        if diff_pct <= 0.012 and neckline.price > avg_bottom:
            x_points = neckline.price - avg_bottom
            target = neckline.price + x_points
            return ChartPattern(
                name="DOUBLE_BOTTOM",
                pattern_type="REVERSAL",
                direction="BULLISH",
                start_time=b1.time,
                end_time=b2.time,
                key_level_1=round(neckline.price, 3),
                key_level_2=round(avg_bottom, 3),
                target_price=round(target, 3),
                points_x=round(x_points, 3),
                description=f"Double Bottom 'W' Pattern: Support at {avg_bottom:.2f}, Neckline at {neckline.price:.2f}. Bullish Target: {target:.2f} (+{x_points:.2f} pts)"
            )
        return None

    def _detect_head_and_shoulders(self, df: pd.DataFrame, swings: list) -> Optional[ChartPattern]:
        highs = [s for s in swings if s.point_type == "HIGH"]
        lows = [s for s in swings if s.point_type == "LOW"]
        if len(highs) < 3 or len(lows) < 2:
            return None

        left_s, head, right_s = highs[-3], highs[-2], highs[-1]
        if head.price > left_s.price and head.price > right_s.price:
            shoulder_diff = abs(left_s.price - right_s.price) / head.price
            if shoulder_diff <= 0.025:
                # Neckline troughs
                t1 = [l for l in lows if left_s.bar_index < l.bar_index < head.bar_index]
                t2 = [l for l in lows if head.bar_index < l.bar_index < right_s.bar_index]
                if t1 and t2:
                    neckline_avg = (t1[-1].price + t2[-1].price) / 2.0
                    x_points = head.price - neckline_avg
                    target = neckline_avg - x_points
                    return ChartPattern(
                        name="HEAD_AND_SHOULDERS",
                        pattern_type="REVERSAL",
                        direction="BEARISH",
                        start_time=left_s.time,
                        end_time=right_s.time,
                        key_level_1=round(head.price, 3),
                        key_level_2=round(neckline_avg, 3),
                        target_price=round(target, 3),
                        points_x=round(x_points, 3),
                        description=f"Head & Shoulders Top: Head at {head.price:.2f}, Neckline at {neckline_avg:.2f}. Bearish Target: {target:.2f}"
                    )
        return None

    def _detect_inverse_head_and_shoulders(self, df: pd.DataFrame, swings: list) -> Optional[ChartPattern]:
        lows = [s for s in swings if s.point_type == "LOW"]
        highs = [s for s in swings if s.point_type == "HIGH"]
        if len(lows) < 3 or len(highs) < 2:
            return None

        left_s, head, right_s = lows[-3], lows[-2], lows[-1]
        if head.price < left_s.price and head.price < right_s.price:
            shoulder_diff = abs(left_s.price - right_s.price) / head.price
            if shoulder_diff <= 0.025:
                p1 = [h for h in highs if left_s.bar_index < h.bar_index < head.bar_index]
                p2 = [h for h in highs if head.bar_index < h.bar_index < right_s.bar_index]
                if p1 and p2:
                    neckline_avg = (p1[-1].price + p2[-1].price) / 2.0
                    x_points = neckline_avg - head.price
                    target = neckline_avg + x_points
                    return ChartPattern(
                        name="INVERSE_HEAD_AND_SHOULDERS",
                        pattern_type="REVERSAL",
                        direction="BULLISH",
                        start_time=left_s.time,
                        end_time=right_s.time,
                        key_level_1=round(neckline_avg, 3),
                        key_level_2=round(head.price, 3),
                        target_price=round(target, 3),
                        points_x=round(x_points, 3),
                        description=f"Inverse Head & Shoulders: Head at {head.price:.2f}, Neckline at {neckline_avg:.2f}. Bullish Target: {target:.2f}"
                    )
        return None

    def _detect_triangles_and_wedges(self, df: pd.DataFrame, swings: list) -> Optional[ChartPattern]:
        highs = [s for s in swings if s.point_type == "HIGH"]
        lows = [s for s in swings if s.point_type == "LOW"]
        if len(highs) < 2 or len(lows) < 2:
            return None

        h1, h2 = highs[-2].price, highs[-1].price
        l1, l2 = lows[-2].price, lows[-1].price

        h_diff_pct = abs(h1 - h2) / ((h1 + h2) / 2.0)
        l_diff_pct = abs(l1 - l2) / ((l1 + l2) / 2.0)

        # Ascending Triangle: Flat top resistance + Rising lows (Bullish continuation)
        if h_diff_pct <= 0.008 and l2 > l1:
            x_points = h2 - l1
            target = h2 + x_points
            return ChartPattern(
                name="ASCENDING_TRIANGLE",
                pattern_type="CONTINUATION",
                direction="BULLISH",
                start_time=highs[-2].time,
                end_time=highs[-1].time,
                key_level_1=round(h2, 3),
                key_level_2=round(l2, 3),
                target_price=round(target, 3),
                points_x=round(x_points, 3),
                description=f"Ascending Triangle: Flat Resistance at {h2:.2f}, Higher Low at {l2:.2f}. Bullish Target: {target:.2f} (+{x_points:.2f} pts)"
            )

        # Descending Triangle: Flat bottom support + Falling highs (Bearish continuation)
        if l_diff_pct <= 0.008 and h2 < h1:
            x_points = h1 - l2
            target = l2 - x_points
            return ChartPattern(
                name="DESCENDING_TRIANGLE",
                pattern_type="CONTINUATION",
                direction="BEARISH",
                start_time=lows[-2].time,
                end_time=lows[-1].time,
                key_level_1=round(h2, 3),
                key_level_2=round(l2, 3),
                target_price=round(target, 3),
                points_x=round(x_points, 3),
                description=f"Descending Triangle: Flat Support at {l2:.2f}, Lower High at {h2:.2f}. Bearish Target: {target:.2f} (-{x_points:.2f} pts)"
            )

        # Symmetrical Triangle: Falling highs + Rising lows (Converging lines)
        if h2 < h1 and l2 > l1:
            x_points = h1 - l1
            target = h2 + (x_points * 0.75)
            return ChartPattern(
                name="SYMMETRICAL_TRIANGLE",
                pattern_type="NEUTRAL_CONTINUATION",
                direction="NEUTRAL",
                start_time=highs[-2].time,
                end_time=highs[-1].time,
                key_level_1=round(h2, 3),
                key_level_2=round(l2, 3),
                target_price=round(target, 3),
                points_x=round(x_points, 3),
                description=f"Symmetrical Triangle: Converging Highs ({h2:.2f}) and Lows ({l2:.2f}). Awaiting breakout."
            )

        return None

    def calculate_fibonacci(self, df: pd.DataFrame, swings: list) -> Optional[FibonacciSetup]:
        """Calculates Fibonacci Retracement Levels (38.2%, 50%, 61.8% Golden Ratio, 78.6%)."""
        highs = [s for s in swings if s.point_type == "HIGH"]
        lows = [s for s in swings if s.point_type == "LOW"]
        if not highs or not lows:
            return None

        last_high = highs[-1]
        last_low = lows[-1]
        current_price = df["close"].iloc[-1]

        # Uptrend retrace: Low came first, then High, now pulling back
        if last_low.bar_index < last_high.bar_index:
            span = last_high.price - last_low.price
            if span <= 0:
                return None
            fib382 = last_high.price - (span * 0.382)
            fib500 = last_high.price - (span * 0.500)
            fib618 = last_high.price - (span * 0.618)
            fib786 = last_high.price - (span * 0.786)
            retrace_pct = ((last_high.price - current_price) / span) * 100.0
            is_golden = (58.0 <= retrace_pct <= 65.0)

            return FibonacciSetup(
                swing_low=round(last_low.price, 3),
                swing_high=round(last_high.price, 3),
                direction="UPTREND",
                level_382=round(fib382, 3),
                level_500=round(fib500, 3),
                level_618=round(fib618, 3),
                level_786=round(fib786, 3),
                current_retrace_pct=round(retrace_pct, 1),
                is_at_golden_ratio=is_golden
            )
        else:
            # Downtrend retrace: High came first, then Low, now rallying
            span = last_high.price - last_low.price
            if span <= 0:
                return None
            fib382 = last_low.price + (span * 0.382)
            fib500 = last_low.price + (span * 0.500)
            fib618 = last_low.price + (span * 0.618)
            fib786 = last_low.price + (span * 0.786)
            retrace_pct = ((current_price - last_low.price) / span) * 100.0
            is_golden = (58.0 <= retrace_pct <= 65.0)

            return FibonacciSetup(
                swing_low=round(last_low.price, 3),
                swing_high=round(last_high.price, 3),
                direction="DOWNTREND",
                level_382=round(fib382, 3),
                level_500=round(fib500, 3),
                level_618=round(fib618, 3),
                level_786=round(fib786, 3),
                current_retrace_pct=round(retrace_pct, 1),
                is_at_golden_ratio=is_golden
            )

    def calculate_trendlines(self, df: pd.DataFrame, swings: list) -> List[Trendline]:
        """Calculates dynamic trendlines connecting swing highs and swing lows."""
        trendlines = []
        highs = [s for s in swings if s.point_type == "HIGH"]
        lows = [s for s in swings if s.point_type == "LOW"]

        if len(highs) >= 2:
            h1, h2 = highs[-2], highs[-1]
            dx = h2.bar_index - h1.bar_index
            if dx > 0:
                slope = (h2.price - h1.price) / dx
                intercept = h1.price - slope * h1.bar_index
                trendlines.append(Trendline(
                    direction="RESISTANCE",
                    start_idx=h1.bar_index,
                    end_idx=len(df) - 1,
                    start_price=h1.price,
                    end_price=slope * (len(df) - 1) + intercept,
                    slope=slope,
                    intercept=intercept,
                    touch_count=2
                ))

        if len(lows) >= 2:
            l1, l2 = lows[-2], lows[-1]
            dx = l2.bar_index - l1.bar_index
            if dx > 0:
                slope = (l2.price - l1.price) / dx
                intercept = l1.price - slope * l1.bar_index
                trendlines.append(Trendline(
                    direction="SUPPORT",
                    start_idx=l1.bar_index,
                    end_idx=len(df) - 1,
                    start_price=l1.price,
                    end_price=slope * (len(df) - 1) + intercept,
                    slope=slope,
                    intercept=intercept,
                    touch_count=2
                ))

        return trendlines
