from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional, Tuple, Dict
import pandas as pd
import numpy as np
from config import AssetSettings

@dataclass
class FVGZone:
    time: datetime
    direction: str       # "BULLISH" or "BEARISH"
    top: float
    bottom: float
    midpoint: float
    mitigated: bool = False
    bar_index: int = 0

@dataclass
class OrderBlock:
    time: datetime
    direction: str       # "BULLISH" or "BEARISH"
    top: float
    bottom: float
    mitigated: bool = False
    bar_index: int = 0

@dataclass
class LiquiditySweep:
    time: datetime
    direction: str       # "BULLISH" (Swept lows) or "BEARISH" (Swept highs)
    swept_level: float
    extreme_price: float
    reversal_bar_index: int

@dataclass
class TradeAnomalySetup:
    timestamp: datetime
    symbol: str
    display_name: str
    timeframe: str
    direction: str               # "BUY" or "SELL"
    setup_name: str
    entry_price: float
    stop_loss: float
    take_profit_1: float
    take_profit_2: float
    risk_reward_ratio: float
    spread_points: float
    spread_quote: float
    confluences: List[str]
    chart_data: pd.DataFrame
    digits: int = 3
    point: float = 0.001
    fvg_zone: Optional[FVGZone] = None
    order_block: Optional[OrderBlock] = None
    sweep: Optional[LiquiditySweep] = None
    structure_type: str = "BOS"
    macro_bias: str = "NEUTRAL"


class AnomalyDetectionEngine:
    def __init__(
        self,
        swing_length: int = 5,
        ob_impulse_multiplier: float = 1.3,
        min_risk_reward: float = 1.8
    ):
        self.swing_length = swing_length
        self.ob_impulse_multiplier = ob_impulse_multiplier
        self.min_risk_reward = min_risk_reward

    def calculate_macro_bias(self, df_macro: pd.DataFrame) -> str:
        """
        Determines the institutional macro trend on M15 using EMA alignment & price position.
        Returns: 'BULLISH', 'BEARISH', or 'NEUTRAL'
        """
        if df_macro is None or len(df_macro) < 50:
            return "NEUTRAL"

        closes = df_macro["close"]
        ema21 = closes.ewm(span=21, adjust=False).mean()
        ema50 = closes.ewm(span=50, adjust=False).mean()

        last_close = closes.iloc[-1]
        last_ema21 = ema21.iloc[-1]
        last_ema50 = ema50.iloc[-1]

        if last_close < last_ema21 and last_ema21 <= last_ema50:
            return "BEARISH"
        elif last_close > last_ema21 and last_ema21 >= last_ema50:
            return "BULLISH"
        elif last_close < last_ema21:
            return "BEARISH"
        elif last_close > last_ema21:
            return "BULLISH"

        return "NEUTRAL"

    def find_swing_points(self, df: pd.DataFrame) -> Tuple[pd.Series, pd.Series]:
        """Identifies local swing highs and swing lows."""
        n = self.swing_length
        swing_highs = pd.Series(np.nan, index=df.index)
        swing_lows = pd.Series(np.nan, index=df.index)

        highs = df["high"].values
        lows = df["low"].values
        l = len(df)

        for i in range(n, l - n):
            if highs[i] == max(highs[i - n : i + n + 1]):
                swing_highs.iloc[i] = highs[i]
            if lows[i] == min(lows[i - n : i + n + 1]):
                swing_lows.iloc[i] = lows[i]

        return swing_highs, swing_lows

    def detect_fvgs(self, df: pd.DataFrame, min_gap_distance: float) -> List[FVGZone]:
        """Detects 3-candle Fair Value Gaps and evaluates mitigation."""
        fvgs: List[FVGZone] = []

        for i in range(2, len(df)):
            prev_2_high = df["high"].iloc[i - 2]
            prev_2_low = df["low"].iloc[i - 2]
            curr_low = df["low"].iloc[i]
            curr_high = df["high"].iloc[i]

            # Bullish FVG
            if curr_low - prev_2_high >= min_gap_distance:
                bottom = prev_2_high
                top = curr_low
                mid = (top + bottom) / 2.0
                fvg = FVGZone(
                    time=df.index[i - 1],
                    direction="BULLISH",
                    top=top,
                    bottom=bottom,
                    midpoint=mid,
                    bar_index=i - 1
                )
                for j in range(i + 1, len(df)):
                    if df["low"].iloc[j] <= bottom:
                        fvg.mitigated = True
                        break
                fvgs.append(fvg)

            # Bearish FVG
            elif prev_2_low - curr_high >= min_gap_distance:
                top = prev_2_low
                bottom = curr_high
                mid = (top + bottom) / 2.0
                fvg = FVGZone(
                    time=df.index[i - 1],
                    direction="BEARISH",
                    top=top,
                    bottom=bottom,
                    midpoint=mid,
                    bar_index=i - 1
                )
                for j in range(i + 1, len(df)):
                    if df["high"].iloc[j] >= top:
                        fvg.mitigated = True
                        break
                fvgs.append(fvg)

        return fvgs

    def detect_order_blocks(self, df: pd.DataFrame, fvgs: List[FVGZone]) -> List[OrderBlock]:
        """Detects Order Blocks followed by impulsive displacement."""
        obs: List[OrderBlock] = []
        if len(df) < 10:
            return obs

        tr = np.maximum(
            df["high"] - df["low"],
            np.maximum(
                np.abs(df["high"] - df["close"].shift(1)),
                np.abs(df["low"] - df["close"].shift(1))
            )
        )
        atr = tr.rolling(14).mean().bfill()

        for fvg in fvgs:
            idx = fvg.bar_index
            if idx <= 1 or idx >= len(df) - 1:
                continue

            disp_candle_range = abs(df["close"].iloc[idx] - df["open"].iloc[idx])
            bar_atr = atr.iloc[idx]

            if disp_candle_range < bar_atr * self.ob_impulse_multiplier:
                continue

            if fvg.direction == "BULLISH":
                for k in range(idx - 1, max(0, idx - 4), -1):
                    if df["close"].iloc[k] <= df["open"].iloc[k]:
                        ob = OrderBlock(
                            time=df.index[k],
                            direction="BULLISH",
                            top=df["high"].iloc[k],
                            bottom=df["low"].iloc[k],
                            bar_index=k
                        )
                        for j in range(idx + 1, len(df)):
                            if df["low"].iloc[j] <= ob.bottom:
                                ob.mitigated = True
                                break
                        obs.append(ob)
                        break

            elif fvg.direction == "BEARISH":
                for k in range(idx - 1, max(0, idx - 4), -1):
                    if df["close"].iloc[k] >= df["open"].iloc[k]:
                        ob = OrderBlock(
                            time=df.index[k],
                            direction="BEARISH",
                            top=df["high"].iloc[k],
                            bottom=df["low"].iloc[k],
                            bar_index=k
                        )
                        for j in range(idx + 1, len(df)):
                            if df["high"].iloc[j] >= ob.top:
                                ob.mitigated = True
                                break
                        obs.append(ob)
                        break

        return obs

    def detect_liquidity_sweeps(
        self, df: pd.DataFrame, swing_highs: pd.Series, swing_lows: pd.Series
    ) -> List[LiquiditySweep]:
        """Detects Buy-Side (BSL) and Sell-Side (SSL) liquidity sweeps."""
        sweeps: List[LiquiditySweep] = []
        if len(df) < 15:
            return sweeps

        recent_highs = [(idx, val) for idx, val in swing_highs.dropna().items()]
        recent_lows = [(idx, val) for idx, val in swing_lows.dropna().items()]

        start_check = max(0, len(df) - 8)
        for i in range(start_check, len(df)):
            bar_time = df.index[i]
            bar_high = df["high"].iloc[i]
            bar_low = df["low"].iloc[i]
            bar_close = df["close"].iloc[i]

            # Bearish BSL Sweep
            for sh_time, sh_val in reversed(recent_highs):
                if bar_time <= sh_time:
                    continue
                if bar_high > sh_val and bar_close < sh_val:
                    sweeps.append(LiquiditySweep(
                        time=bar_time,
                        direction="BEARISH",
                        swept_level=sh_val,
                        extreme_price=bar_high,
                        reversal_bar_index=i
                    ))
                    break

            # Bullish SSL Sweep
            for sl_time, sl_val in reversed(recent_lows):
                if bar_time <= sl_time:
                    continue
                if bar_low < sl_val and bar_close > sl_val:
                    sweeps.append(LiquiditySweep(
                        time=bar_time,
                        direction="BULLISH",
                        swept_level=sl_val,
                        extreme_price=bar_low,
                        reversal_bar_index=i
                    ))
                    break

        return sweeps

    def evaluate_live_anomalies(
        self,
        df: pd.DataFrame,
        symbol: str,
        timeframe: str,
        current_tick: Dict,
        asset_settings: AssetSettings,
        macro_bias: str = "NEUTRAL"
    ) -> Optional[TradeAnomalySetup]:
        """
        Evaluates anomalies dynamically scaled for the specific asset class
        (Gold, EURUSD, USDJPY) with macro trend alignment and rejection checks.
        """
        if len(df) < 50:
            return None

        point = current_tick.get("point", 0.001)
        digits = current_tick.get("digits", 3)
        spread_points = current_tick.get("spread_points", 0.0)
        spread_quote = current_tick.get("spread_quote", 0.0)
        current_price = current_tick.get("mid", df["close"].iloc[-1])

        # Spread Filter
        if spread_points > asset_settings.max_allowed_spread_points:
            return None

        min_gap = asset_settings.fvg_min_points * point
        sl_buffer = asset_settings.sl_buffer_points * point
        max_sl_dist = asset_settings.max_sl_points * point

        swing_highs, swing_lows = self.find_swing_points(df)
        fvgs = self.detect_fvgs(df, min_gap_distance=min_gap)
        obs = self.detect_order_blocks(df, fvgs)
        sweeps = self.detect_liquidity_sweeps(df, swing_highs, swing_lows)

        active_bull_fvgs = [f for f in fvgs if f.direction == "BULLISH" and not f.mitigated]
        active_bear_fvgs = [f for f in fvgs if f.direction == "BEARISH" and not f.mitigated]
        active_bull_obs = [o for o in obs if o.direction == "BULLISH" and not o.mitigated]
        active_bear_obs = [o for o in obs if o.direction == "BEARISH" and not o.mitigated]

        recent_sweeps = [s for s in sweeps if s.reversal_bar_index >= len(df) - 4]
        last_highs = [val for _, val in swing_highs.dropna().items() if val > current_price]
        last_lows = [val for _, val in swing_lows.dropna().items() if val < current_price]

        last_bar_close = df["close"].iloc[-1]
        last_bar_open = df["open"].iloc[-1]

        # -----------------------------
        # 1. EVALUATE BEARISH ANOMALIES
        # -----------------------------
        if macro_bias in ("BEARISH", "NEUTRAL"):
            bear_sweep = next((s for s in recent_sweeps if s.direction == "BEARISH"), None)
            matching_bear_fvg = next((f for f in active_bear_fvgs if f.bottom * 0.9998 <= current_price <= f.top), None)
            matching_bear_ob = next((o for o in active_bear_obs if o.bottom * 0.9998 <= current_price <= o.top), None)

            candle_confirms_bear = (last_bar_close <= last_bar_open)

            if candle_confirms_bear and ((bear_sweep is not None) or (matching_bear_fvg is not None and len(last_lows) > 0)):
                confluences = [f"M15 Macro Bias: {macro_bias} (Trend Aligned)"]
                if bear_sweep:
                    confluences.append(f"BSL Swept ({bear_sweep.swept_level:.{digits}f})")
                if matching_bear_fvg:
                    confluences.append(f"Bearish FVG ({matching_bear_fvg.bottom:.{digits}f} - {matching_bear_fvg.top:.{digits}f})")
                if matching_bear_ob:
                    confluences.append(f"Bearish OB ({matching_bear_ob.bottom:.{digits}f} - {matching_bear_ob.top:.{digits}f})")

                entry = current_price
                extreme_ref = bear_sweep.extreme_price if bear_sweep else df["high"].iloc[-5:].max()
                sl = extreme_ref + spread_quote + sl_buffer

                risk = sl - entry
                if risk <= 0 or risk > max_sl_dist:
                    return None

                tp1 = last_lows[0] if len(last_lows) > 0 else entry - (risk * 2.0)
                tp2 = last_lows[1] if len(last_lows) > 1 else entry - (risk * 3.5)

                rr = (entry - tp1) / risk
                if rr >= self.min_risk_reward:
                    confluences.append(f"Viable: Risk {risk:.{digits}f} | R:R 1:{rr:.2f} | Spread: {spread_points:.0f} pts")
                    return TradeAnomalySetup(
                        timestamp=datetime.now(timezone.utc),
                        symbol=symbol,
                        display_name=asset_settings.display_name,
                        timeframe=timeframe,
                        direction="SELL",
                        setup_name="Bearish Liquidity Sweep & Imbalance Mitigation",
                        entry_price=round(entry, digits),
                        stop_loss=round(sl, digits),
                        take_profit_1=round(tp1, digits),
                        take_profit_2=round(tp2, digits),
                        risk_reward_ratio=round(rr, 2),
                        spread_points=spread_points,
                        spread_quote=spread_quote,
                        confluences=confluences,
                        chart_data=df.tail(60),
                        digits=digits,
                        point=point,
                        fvg_zone=matching_bear_fvg,
                        order_block=matching_bear_ob,
                        sweep=bear_sweep,
                        structure_type="BOS",
                        macro_bias=macro_bias
                    )

        # -----------------------------
        # 2. EVALUATE BULLISH ANOMALIES
        # -----------------------------
        if macro_bias in ("BULLISH", "NEUTRAL"):
            bull_sweep = next((s for s in recent_sweeps if s.direction == "BULLISH"), None)
            matching_bull_fvg = next((f for f in active_bull_fvgs if f.bottom <= current_price <= f.top * 1.0002), None)
            matching_bull_ob = next((o for o in active_bull_obs if o.bottom <= current_price <= o.top * 1.0002), None)

            candle_confirms_bull = (last_bar_close >= last_bar_open)

            if candle_confirms_bull and ((bull_sweep is not None) or (matching_bull_fvg is not None and len(last_highs) > 0)):
                confluences = [f"M15 Macro Bias: {macro_bias} (Trend Aligned)"]
                if bull_sweep:
                    confluences.append(f"SSL Swept ({bull_sweep.swept_level:.{digits}f})")
                if matching_bull_fvg:
                    confluences.append(f"Bullish FVG ({matching_bull_fvg.bottom:.{digits}f} - {matching_bull_fvg.top:.{digits}f})")
                if matching_bull_ob:
                    confluences.append(f"Bullish OB ({matching_bull_ob.bottom:.{digits}f} - {matching_bull_ob.top:.{digits}f})")

                entry = current_price
                extreme_ref = bull_sweep.extreme_price if bull_sweep else df["low"].iloc[-5:].min()
                sl = extreme_ref - spread_quote - sl_buffer

                risk = entry - sl
                if risk <= 0 or risk > max_sl_dist:
                    return None

                tp1 = last_highs[0] if len(last_highs) > 0 else entry + (risk * 2.0)
                tp2 = last_highs[1] if len(last_highs) > 1 else entry + (risk * 3.5)

                rr = (tp1 - entry) / risk
                if rr >= self.min_risk_reward:
                    confluences.append(f"Viable: Risk {risk:.{digits}f} | R:R 1:{rr:.2f} | Spread: {spread_points:.0f} pts")
                    return TradeAnomalySetup(
                        timestamp=datetime.now(timezone.utc),
                        symbol=symbol,
                        display_name=asset_settings.display_name,
                        timeframe=timeframe,
                        direction="BUY",
                        setup_name="Bullish Liquidity Sweep & Imbalance Mitigation",
                        entry_price=round(entry, digits),
                        stop_loss=round(sl, digits),
                        take_profit_1=round(tp1, digits),
                        take_profit_2=round(tp2, digits),
                        risk_reward_ratio=round(rr, 2),
                        spread_points=spread_points,
                        spread_quote=spread_quote,
                        confluences=confluences,
                        chart_data=df.tail(60),
                        digits=digits,
                        point=point,
                        fvg_zone=matching_bull_fvg,
                        order_block=matching_bull_ob,
                        sweep=bull_sweep,
                        structure_type="BOS",
                        macro_bias=macro_bias
                    )

        return None
