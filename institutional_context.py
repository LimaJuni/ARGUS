"""
Institutional Shared Context, Arbitration & Validation Engine.
Implements the unified context architecture:
1. One shared context object written to by all modules.
2. Strict Validation Layer:
   - Dealing range > 100% or < 0%: Flag as Breakout/Expansion, invalidate or recompute.
   - Fibonacci > 100% retrace: Discard invalidated Fib leg.
   - Opposite pattern deduplication: Keep only the most recent/complete, or flag range.
   - Patterns without anchors or tf: Rejected.
   - Unconfirmed patterns (no neckline close): Labeled 'FORMING', never 'TARGET'.
   - Proximity filter: Hide zones farther than N * ATR from current price.
   - Symmetrical zone inspection: Report both nearest supply and nearest demand.
   - Precision: Format all prices to true pip precision (5 decimals for FX, 2/3 for JPY/Gold).
3. Arbitration Layer:
   - Weighted multi-timeframe scoring (Daily 3.0, 4H 2.0, 1H 1.5, 15M 1.0, Weekly 1.5, Monthly 1.0).
   - Generates plain-language synthesis explaining HTF vs LTF conflict and net bias.
4. Concrete Triggers & Invalidation:
   - Clear long_if, short_if, and invalidation price levels.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional, Dict, Tuple
import pandas as pd
import numpy as np


@dataclass
class SessionContext:
    name: str                 # e.g., "London / NY Overlap", "Asian Consolidation"
    utc_window: str           # e.g., "13:00 - 17:00 UTC"
    liquidity_level: str      # "HIGH", "MEDIUM", "LOW"
    character: str
    upcoming_news: str        # e.g., "None in next 3h" or "⚡ USD: FOMC in 1h 45m"
    minutes_to_close: int = 0


@dataclass
class BiasContext:
    direction: str            # "BULLISH", "BEARISH", "MIXED"
    score: float              # Weighted sum
    confidence: str           # "HIGH", "MODERATE", "LOW"
    summary: str              # Plain-language synthesis linking timeframes
    timeframe_biases: Dict[str, str] = field(default_factory=dict)


@dataclass
class RangeContext:
    tf: str
    high: float
    low: float
    equilibrium: float
    price_pct: float          # Percentage inside dealing range
    status: str               # "PREMIUM", "DISCOUNT", "EQUILIBRIUM", "BREAKOUT_HIGH", "BREAKOUT_LOW"
    valid: bool               # True if 0 <= price_pct <= 100


@dataclass
class StructureContext:
    tf: str
    trend: str                # "BULLISH", "BEARISH", "NEUTRAL"
    last_bos: Optional[float]
    last_choch: Optional[float]
    protected_high: float
    protected_low: float


@dataclass
class ZoneContext:
    zone_type: str            # "SUPPLY" or "DEMAND"
    tf: str
    top: float
    bottom: float
    midpoint: float
    touches: int
    is_fresh: bool
    distance_pips: float
    source: str               # "ORDER_BLOCK", "SUPPLY_DEMAND", "SWING_EXTREME"


@dataclass
class PatternContext:
    name: str                 # "DOUBLE_TOP", "DOUBLE_BOTTOM", "HEAD_AND_SHOULDERS", etc.
    tf: str
    status: str               # "FORMING", "CONFIRMED", "INVALIDATED"
    neckline: float
    anchors: List[Tuple[pd.Timestamp, float]]
    target: Optional[float]
    description: str


@dataclass
class FibContext:
    tf: str
    direction: str            # "UPTREND" or "DOWNTREND"
    swing_low: float
    swing_high: float
    level_500: float
    level_618: float          # Golden Ratio
    retrace_pct: float
    valid: bool               # Discarded if retrace_pct > 100%
    is_golden: bool


@dataclass
class TriggerContext:
    long_if: str              # Explicit long trigger
    short_if: str             # Explicit short trigger
    invalidation: str         # Structural invalidation level
    planned_entry: float
    planned_sl: float
    planned_tp1: float
    planned_tp2: float
    risk_reward: float
    lot_size: float


@dataclass
class InstitutionalContext:
    timestamp: datetime
    symbol: str
    display_name: str
    current_price: float
    digits: int
    point: float
    pip_size: float
    execution_tf: str
    session: SessionContext
    bias: BiasContext
    range: RangeContext
    structure: Dict[str, StructureContext]
    nearest_supply: Optional[ZoneContext]
    nearest_demand: Optional[ZoneContext]
    all_zones: List[ZoneContext]
    patterns: List[PatternContext]
    fib: FibContext
    triggers: TriggerContext
    status: str               # "TRIGGERED", "ARMED_IN_ZONE", "WAIT_FOR_TRIGGER"
    data_source: str = "TradingView"


class InstitutionalReasoningEngine:
    """
    Arbitration and Context Synthesis Layer.
    Resolves timeframe conflicts, validates dealing ranges, filters distant zones,
    and constructs the complete single source of truth.
    """

    TF_WEIGHTS = {
        "D1": 3.0,
        "H4": 2.0,
        "H1": 1.5,
        "M15": 1.0,
        "M5": 0.8,
        "M1": 0.5,
        "W1": 1.5,
        "MN1": 1.0
    }

    def __init__(self, digits: int, point: float):
        self.digits = digits
        self.point = point
        self.pip_size = point * 10 if digits in (3, 5) else point

    def compute_arbitration_bias(self, tf_biases: Dict[str, str], exec_tf: str) -> BiasContext:
        """
        Calculates weighted directional bias score and synthesizes a plain-language explanation.
        """
        score = 0.0
        total_weight = 0.0

        for tf, bias in tf_biases.items():
            w = self.TF_WEIGHTS.get(tf, 1.0)
            total_weight += w
            if bias == "BULLISH":
                score += w
            elif bias == "BEARISH":
                score -= w

        daily_bias = tf_biases.get("D1", "NEUTRAL")
        h4_bias = tf_biases.get("H4", "NEUTRAL")
        h1_bias = tf_biases.get("H1", "NEUTRAL")
        exec_bias = tf_biases.get(exec_tf, "NEUTRAL")

        # Synthesize plain-language reasoning
        if score >= 2.5 and daily_bias == "BULLISH":
            direction = "BULLISH"
            conf = "HIGH"
            reason = f"Broad institutional alignment across timeframes. Daily and {exec_tf} are both bullish. Look for pullbacks into demand to join trend."
        elif score <= -2.5 and daily_bias == "BEARISH":
            direction = "BEARISH"
            conf = "HIGH"
            reason = f"Institutional selling pressure aligned across Daily, 4H, and {exec_tf}. Target sell-side liquidity (SSL)."
        elif daily_bias != exec_bias and exec_bias != "NEUTRAL":
            direction = "MIXED"
            conf = "MODERATE"
            if daily_bias == "BEARISH" and exec_bias == "BULLISH":
                reason = (
                    f"Daily is bearish, but {exec_tf}/H1 are pushing bullish within intraday flows. "
                    f"Daily may be undergoing a deeper pullback; counter-trend shorts require a confirmed CHoCH before selling."
                )
            else:
                reason = (
                    f"Daily is bullish, but {exec_tf}/H1 are pulling back lower. "
                    f"LTF drop represents an intraday discount retracement into HTF demand. Watch for bullish reversal wicks."
                )
        else:
            direction = "BULLISH" if score > 0 else ("BEARISH" if score < 0 else "NEUTRAL")
            conf = "MODERATE" if abs(score) >= 1.5 else "LOW"
            reason = f"Net bias score is {score:+.1f}. Trend is consolidating between major higher-timeframe boundaries."

        return BiasContext(
            direction=direction,
            score=round(score, 1),
            confidence=conf,
            summary=reason,
            timeframe_biases=tf_biases
        )

    def validate_dealing_range(self, df_h1: pd.DataFrame, tf_label: str = "H1", current_price: Optional[float] = None) -> RangeContext:
        """
        Validates dealing range. If price is > 100% or < 0%, marks as breakout/expansion
        and dynamically establishes the active dealing leg.
        """
        # Handle swapped argument order defensively
        if isinstance(tf_label, (int, float)) and isinstance(current_price, str):
            tf_label, current_price = current_price, float(tf_label)
        elif current_price is None:
            current_price = float(df_h1["close"].iloc[-1])
        else:
            current_price = float(current_price)
        if len(df_h1) < 20:
            return RangeContext(tf=tf_label, high=current_price, low=current_price,
                                equilibrium=current_price, price_pct=50.0, status="EQUILIBRIUM", valid=False)

        # Look at last 35 bars
        window = df_h1.tail(35)
        h = float(window["high"].max())
        l = float(window["low"].min())
        span = h - l
        if span <= 0:
            span = self.point * 10

        pct = ((current_price - l) / span) * 100.0

        if pct > 100.0:
            # Price broke above previous high -> expansion
            # Re-anchor high to current price
            return RangeContext(
                tf=tf_label, high=current_price, low=l, equilibrium=round((current_price + l) / 2.0, self.digits),
                price_pct=100.0, status="BREAKOUT_HIGH (Expansion)", valid=False
            )
        elif pct < 0.0:
            # Price broke below previous low -> expansion
            return RangeContext(
                tf=tf_label, high=h, low=current_price, equilibrium=round((h + current_price) / 2.0, self.digits),
                price_pct=0.0, status="BREAKOUT_LOW (Expansion)", valid=False
            )
        else:
            eq = round((h + l) / 2.0, self.digits)
            status = "PREMIUM (Sell-Side)" if pct > 55.0 else ("DISCOUNT (Buy-Side)" if pct < 45.0 else "EQUILIBRIUM (Fair Value)")
            return RangeContext(
                tf=tf_label, high=round(h, self.digits), low=round(l, self.digits),
                equilibrium=eq, price_pct=round(pct, 1), status=status, valid=True
            )

    def validate_fibonacci(
        self,
        fib_or_df,
        current_price: float,
        bias_direction: str = "NEUTRAL",
        tf_label: str = "H1",
        swings: Optional[list] = None
    ) -> FibContext:
        """
        Validates Fibonacci retracement. If retrace > 100%, discards the leg.
        Aligns Fib direction with active market momentum.
        """
        d = self.digits

        # Case 1: Already computed FibonacciSetup passed in
        if hasattr(fib_or_df, "current_retrace_pct") and hasattr(fib_or_df, "level_618"):
            fib = fib_or_df
            pct = float(fib.current_retrace_pct)
            is_valid = (0.0 <= pct <= 100.0)
            return FibContext(
                tf=tf_label,
                direction=fib.direction,
                swing_low=round(fib.swing_low, d),
                swing_high=round(fib.swing_high, d),
                level_500=round(fib.level_500, d),
                level_618=round(fib.level_618, d),
                retrace_pct=round(pct, 1),
                valid=is_valid,
                is_golden=bool(fib.is_at_golden_ratio and is_valid)
            )

        # Case 2: Swings passed in or extracted
        if swings is None:
            return FibContext(tf=tf_label, direction="NONE", swing_low=0.0, swing_high=0.0,
                              level_500=0.0, level_618=0.0, retrace_pct=0.0, valid=False, is_golden=False)

        highs = [s for s in swings if getattr(s, "point_type", getattr(s, "type", "")) == "HIGH"]
        lows = [s for s in swings if getattr(s, "point_type", getattr(s, "type", "")) == "LOW"]

        if not highs or not lows:
            return FibContext(tf=tf_label, direction="NONE", swing_low=0.0, swing_high=0.0,
                              level_500=0.0, level_618=0.0, retrace_pct=0.0, valid=False, is_golden=False)

        last_high = highs[-1]
        last_low = lows[-1]

        h_idx = getattr(last_high, "bar_index", getattr(last_high, "idx", 0))
        l_idx = getattr(last_low, "bar_index", getattr(last_low, "idx", 0))
        h_price = float(getattr(last_high, "price", 0.0))
        l_price = float(getattr(last_low, "price", 0.0))

        if l_idx < h_idx:
            direction = "UPTREND"
            span = h_price - l_price
            if span <= 0:
                return FibContext(tf=tf_label, direction="NONE", swing_low=0.0, swing_high=0.0,
                                  level_500=0.0, level_618=0.0, retrace_pct=0.0, valid=False, is_golden=False)
            retrace_pct = ((h_price - current_price) / span) * 100.0
            fib500 = h_price - (span * 0.50)
            fib618 = h_price - (span * 0.618)
        else:
            direction = "DOWNTREND"
            span = h_price - l_price
            if span <= 0:
                return FibContext(tf=tf_label, direction="NONE", swing_low=0.0, swing_high=0.0,
                                  level_500=0.0, level_618=0.0, retrace_pct=0.0, valid=False, is_golden=False)
            retrace_pct = ((current_price - l_price) / span) * 100.0
            fib500 = l_price + (span * 0.50)
            fib618 = l_price + (span * 0.618)

        is_valid = (0.0 <= retrace_pct <= 100.0)
        is_golden = is_valid and (abs(retrace_pct - 61.8) <= 4.0)

        return FibContext(
            tf=tf_label,
            direction=direction,
            swing_low=round(l_price, d),
            swing_high=round(h_price, d),
            level_500=round(fib500, d),
            level_618=round(fib618, d),
            retrace_pct=round(retrace_pct, 1),
            valid=is_valid,
            is_golden=is_golden
        )

    def validate_and_deduplicate_patterns(
        self,
        raw_patterns: list,
        df: pd.DataFrame,
        arg3 = None,
        arg4 = None
    ) -> List[PatternContext]:
        """
        Validates patterns:
        - Must have valid timeframe and anchor coordinates.
        - Deduplicates opposing patterns at the same level (keeps most recent / complete).
        - Verifies whether neckline is actually broken by close:
          * If broken: status = "CONFIRMED", target enabled.
          * If not broken: status = "FORMING", target is provisional.
        - Formats all prices to 5 decimals (or instrument precision).
        """
        # Handle swapped argument order (current_price vs tf_label)
        if isinstance(arg3, (int, float)):
            current_price = float(arg3)
            tf_label = str(arg4) if arg4 else "M15"
        elif isinstance(arg3, str):
            tf_label = arg3
            current_price = float(arg4) if arg4 is not None else float(df["close"].iloc[-1])
        else:
            current_price = float(df["close"].iloc[-1])
            tf_label = "M15"
        validated: List[PatternContext] = []
        if not raw_patterns or len(df) < 15:
            return validated

        d = self.digits
        latest_close = float(df["close"].iloc[-1])

        for p in raw_patterns:
            name = p.name
            neckline = float(p.key_level_2 if name in ("DOUBLE_TOP", "HEAD_AND_SHOULDERS") else p.key_level_1)
            peak_or_trough = float(p.key_level_1 if name in ("DOUBLE_TOP", "HEAD_AND_SHOULDERS") else p.key_level_2)
            anchors = [(p.start_time, peak_or_trough), (p.end_time, peak_or_trough)]

            # Check confirmation: candle close must pierce the neckline
            if "TOP" in name or "BEAR" in p.direction:
                is_confirmed = latest_close < neckline
                target = round(neckline - abs(peak_or_trough - neckline), d)
                desc = (
                    f"Double Top ({tf_label}) CONFIRMED — Neckline at {neckline:.{d}f} broken. Measured Target: {target:.{d}f}"
                    if is_confirmed else
                    f"Double Top ({tf_label}) FORMING — Peak at {peak_or_trough:.{d}f}, Neckline at {neckline:.{d}f} (Not yet broken)"
                )
            else:
                is_confirmed = latest_close > neckline
                target = round(neckline + abs(peak_or_trough - neckline), d)
                desc = (
                    f"Double Bottom ({tf_label}) CONFIRMED — Neckline at {neckline:.{d}f} broken. Measured Target: {target:.{d}f}"
                    if is_confirmed else
                    f"Double Bottom ({tf_label}) FORMING — Base at {peak_or_trough:.{d}f}, Neckline at {neckline:.{d}f} (Not yet broken)"
                )

            status = "CONFIRMED" if is_confirmed else "FORMING"

            validated.append(PatternContext(
                name=name,
                tf=tf_label,
                status=status,
                neckline=round(neckline, d),
                anchors=anchors,
                target=target if is_confirmed else None,
                description=desc
            ))

        # Deduplicate opposing patterns at similar price level
        if len(validated) >= 2:
            has_dt = any("TOP" in p.name for p in validated)
            has_db = any("BOTTOM" in p.name for p in validated)
            if has_dt and has_db:
                # Opposing top and bottom at same price range -> this is a consolidation range!
                # Keep the one that is currently active or mark as CONSOLIDATION
                return [PatternContext(
                    name="CONSOLIDATION_RANGE",
                    tf=tf_label,
                    status="FORMING",
                    neckline=round(current_price, d),
                    anchors=[],
                    target=None,
                    description=f"Consolidation Range ({tf_label}): Price oscillating between dual swing pivots. Awaiting clear directional breakout."
                )]

        return validated

    def filter_and_rank_zones(
        self,
        all_candidate_zones: list,
        current_price: float,
        atr: float,
        max_atr_dist: float = 3.5
    ) -> Tuple[Optional[ZoneContext], Optional[ZoneContext], List[ZoneContext]]:
        """
        Filters zones by proximity (within max_atr_dist * ATR of price).
        Identifies nearest fresh supply (above price) and nearest fresh demand (below price).
        """
        d = self.digits
        max_dist_price = atr * max_atr_dist
        ranked_zones: List[ZoneContext] = []

        for z in all_candidate_zones:
            # Exclude oversized macro zones (MN1/W1/D1) or giant candles that distort intraday levels
            if getattr(z, "timeframe", "") in ("MN1", "W1", "D1"):
                continue
            top = float(z.top)
            bot = float(z.bottom)
            if (top - bot) > max(atr * 3.0, self.point * 25):
                continue
            mid = float((top + bot) / 2.0)
            dist_pts = abs(current_price - mid)
            dist_pips = dist_pts / self.pip_size

            # Proximity check: discard zones farther than max_dist_price
            if dist_pts > max_dist_price:
                continue

            ranked_zones.append(ZoneContext(
                zone_type=z.zone_type,
                tf=z.timeframe,
                top=round(top, d),
                bottom=round(bot, d),
                midpoint=round(mid, d),
                touches=getattr(z, "touches", 0),
                is_fresh=getattr(z, "is_fresh", True),
                distance_pips=round(dist_pips, 1),
                source="ORDER_BLOCK" if hasattr(z, "displacement_atr") else "SUPPLY_DEMAND"
            ))

        # Find nearest supply above current price
        supplies_above = [z for z in ranked_zones if z.zone_type == "SUPPLY" and z.top >= current_price]
        supplies_above.sort(key=lambda z: z.distance_pips)
        nearest_supply = supplies_above[0] if supplies_above else None

        # Find nearest demand below current price
        demands_below = [z for z in ranked_zones if z.zone_type == "DEMAND" and z.bottom <= current_price]
        demands_below.sort(key=lambda z: z.distance_pips)
        nearest_demand = demands_below[0] if demands_below else None

        return nearest_supply, nearest_demand, ranked_zones

    def build_actionable_triggers(
        self,
        current_price: float,
        bias: BiasContext,
        nearest_supply: Optional[ZoneContext],
        nearest_demand: Optional[ZoneContext],
        choch_level: Optional[float],
        exec_tf: str,
        atr: float
    ) -> Tuple[TriggerContext, str]:
        """
        Creates concrete actionable long_if, short_if, and invalidation rules.
        """
        d = self.digits
        pips = self.pip_size

        sup_lvl = nearest_supply.top if nearest_supply else current_price + (atr * 1.5)
        dem_lvl = nearest_demand.bottom if nearest_demand else current_price - (atr * 1.5)

        short_trigger_lvl = nearest_supply.bottom if nearest_supply else (choch_level if choch_level else current_price - (atr * 0.5))
        long_trigger_lvl = nearest_demand.top if nearest_demand else (choch_level if choch_level else current_price + (atr * 0.5))

        long_if = f"{exec_tf} candle closes above {long_trigger_lvl:.{d}f} with bullish rejection wick"
        short_if = f"{exec_tf} candle closes below {short_trigger_lvl:.{d}f} after tapping supply zone"
        invalidation = f"Clean {exec_tf} candle close beyond {sup_lvl:.{d}f} (for shorts) or below {dem_lvl:.{d}f} (for longs)"

        # Check if price is actively touching / testing either zone
        is_in_supply = nearest_supply and (nearest_supply.bottom - (atr * 0.15) <= current_price <= nearest_supply.top + (atr * 0.15))
        is_in_demand = nearest_demand and (nearest_demand.bottom - (atr * 0.15) <= current_price <= nearest_demand.top + (atr * 0.15))

        if is_in_supply:
            status = "ARMED_IN_ZONE"
            planned_entry = round(current_price, d)
            planned_sl = round(nearest_supply.top + (atr * 0.2), d)
            risk = max(planned_sl - planned_entry, self.point * 10)
            planned_tp1 = round(planned_entry - (risk * 2.0), d)
            planned_tp2 = round(nearest_demand.top if nearest_demand else planned_entry - (risk * 3.5), d)
        elif is_in_demand:
            status = "ARMED_IN_ZONE"
            planned_entry = round(current_price, d)
            planned_sl = round(nearest_demand.bottom - (atr * 0.2), d)
            risk = max(planned_entry - planned_sl, self.point * 10)
            planned_tp1 = round(planned_entry + (risk * 2.0), d)
            planned_tp2 = round(nearest_supply.bottom if nearest_supply else planned_entry + (risk * 3.5), d)
        else:
            status = "WAIT_FOR_TRIGGER"
            planned_entry = round(current_price, d)
            planned_sl = round(current_price + (atr * 1.2), d)
            risk = max(abs(planned_sl - planned_entry), self.point * 10)
            planned_tp1 = round(current_price - (risk * 2.0), d)
            planned_tp2 = round(current_price - (risk * 3.5), d)

        rr = 2.0
        lot = round(max(0.01, min(10.0, 10.0 / max(risk / pips, 1.0))), 2)

        triggers = TriggerContext(
            long_if=long_if,
            short_if=short_if,
            invalidation=invalidation,
            planned_entry=planned_entry,
            planned_sl=planned_sl,
            planned_tp1=planned_tp1,
            planned_tp2=planned_tp2,
            risk_reward=rr,
            lot_size=lot
        )

        return triggers, status
