"""
Multi-Timeframe SMC / Price Action Analyzer Engine.
Implements the exact framework from the Forex Trading Strategy Guide:
1. Multi-timeframe Structure & Context (MN1 -> W1 -> D1 -> H4 -> H1 -> M15)
2. BOS (Break of Structure) & CHoCH (Change of Character)
3. Fresh Supply & Demand Zones (with touch tracking, freshness scoring, impulse check)
4. 15-Minute Double Confirmation:
   - Price Reaction: Rejection Candle (Pin bar / Hammer, Shooting star, Bullish/Bearish Engulfing)
   - Mini Structure Shift: Mini BOS on 15m in trade direction
5. Structural Stop Loss & 1:2 Minimum Risk-Reward Take Profit sizing (BE at 1:1, partial at 1:2)
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, Dict, Tuple
import pandas as pd
import numpy as np

from smc_engine import SMCEngine, OrderBlock, FairValueGap, LiquidityPool
from pattern_engine import PatternRecognitionEngine, ChartPattern, Trendline, FibonacciSetup
from institutional_context import (
    InstitutionalReasoningEngine, InstitutionalContext, SessionContext,
    BiasContext, RangeContext, StructureContext, ZoneContext, PatternContext, FibContext, TriggerContext
)
from institutional_layers import SessionKillzoneManager, EconomicCalendar

@dataclass
class SwingPoint:
    time: datetime
    price: float
    point_type: str  # "HIGH" or "LOW"
    bar_index: int

@dataclass
class StructureEvent:
    time: datetime
    event_type: str  # "BOS" or "CHOCH"
    direction: str   # "BULLISH" or "BEARISH"
    broken_level: float
    trigger_price: float

@dataclass
class SupplyDemandZone:
    timeframe: str
    time: datetime
    zone_type: str   # "DEMAND" or "SUPPLY"
    top: float
    bottom: float
    midpoint: float
    impulse_ratio: float
    touches: int = 0
    is_fresh: bool = True
    mitigated: bool = False

@dataclass
class CandlestickPattern:
    time: datetime
    name: str        # "BULLISH_ENGULFING", "BEARISH_ENGULFING", "PIN_BAR_HAMMER", "SHOOTING_STAR"
    direction: str   # "BULLISH" or "BEARISH"
    price: float

@dataclass
class TFAnalysis:
    timeframe: str
    bias: str        # "BULLISH", "BEARISH", "NEUTRAL"
    last_event: Optional[StructureEvent]
    recent_swings: List[SwingPoint]
    supply_zones: List[SupplyDemandZone]
    demand_zones: List[SupplyDemandZone]
    active_zone: Optional[SupplyDemandZone] = None
    atr: float = 0.0
    current_close: float = 0.0

@dataclass
class TradePlan:
    symbol: str
    display_name: str
    direction: str           # "BUY" or "SELL"
    status: str              # "TRIGGERED" or "ARMED_IN_ZONE"
    execution_tf: str        # "M15", "M5", "M1"
    entry_price: float
    stop_loss: float
    take_profit_1: float     # 1:2 RR (50% partial exit)
    take_profit_2: float     # Opposing HTF zone or 1:3.5 RR
    break_even_price: float  # 1:1 RR
    risk_reward_1: float
    risk_reward_2: float
    sl_pips_or_points: float
    recommended_lot_size: float
    active_zone: SupplyDemandZone
    rejection_pattern: Optional[CandlestickPattern]
    mini_bos: Optional[StructureEvent]
    checklist: Dict[str, bool]
    notes: List[str]

@dataclass
class CompleteAnalysisResult:
    symbol: str
    display_name: str
    timestamp: datetime
    current_price: float
    digits: int
    point: float
    tf_analyses: Dict[str, TFAnalysis]
    overall_bias: str
    daily_bias: str
    execution_tf: str = "M15"
    trade_plan: Optional[TradePlan] = None
    data_source: str = "TradingView"
    # SMC & Pro Trader Patterns:
    order_blocks: Dict[str, List[OrderBlock]] = field(default_factory=dict)
    fair_value_gaps: Dict[str, List[FairValueGap]] = field(default_factory=dict)
    liquidity_pools: Dict[str, List[LiquidityPool]] = field(default_factory=dict)
    patterns: Dict[str, List[ChartPattern]] = field(default_factory=dict)
    trendlines: Dict[str, List[Trendline]] = field(default_factory=dict)
    fibonacci: Dict[str, Optional[FibonacciSetup]] = field(default_factory=dict)
    institutional_context: Optional[InstitutionalContext] = None


class MTFPriceActionAnalyzer:
    def __init__(self, strategy_config: dict):
        self.cfg = strategy_config

    def _calc_atr(self, df: pd.DataFrame, period: int = 14) -> pd.Series:
        high = df["high"]
        low = df["low"]
        close_prev = df["close"].shift(1)
        tr = np.maximum(high - low, np.maximum((high - close_prev).abs(), (low - close_prev).abs()))
        return tr.rolling(period).mean().bfill()

    def find_swings(self, df: pd.DataFrame, swing_len: int = 3) -> List[SwingPoint]:
        swings = []
        highs = df["high"].values
        lows = df["low"].values
        n = len(df)
        if n < swing_len * 2 + 1:
            return swings

        for i in range(swing_len, n - swing_len):
            # Swing High
            if highs[i] == max(highs[i - swing_len : i + swing_len + 1]):
                swings.append(SwingPoint(time=df.index[i], price=highs[i], point_type="HIGH", bar_index=i))
            # Swing Low
            if lows[i] == min(lows[i - swing_len : i + swing_len + 1]):
                swings.append(SwingPoint(time=df.index[i], price=lows[i], point_type="LOW", bar_index=i))

        # Sort by bar_index
        swings.sort(key=lambda x: x.bar_index)
        return swings

    def analyze_structure(self, df: pd.DataFrame, swings: List[SwingPoint]) -> Tuple[str, Optional[StructureEvent]]:
        """
        Determines BOS, CHoCH, and directional trend.
        Uptrend: Higher Highs + Higher Lows
        Downtrend: Lower Highs + Lower Lows
        BOS: Breaks prior swing in trend direction (continuation)
        CHoCH: Breaks prior swing in opposing direction (first reversal warning)
        """
        if len(swings) < 4:
            # Fallback to EMA alignment if swings are too few
            ema21 = df["close"].ewm(span=21).mean().iloc[-1]
            last_close = df["close"].iloc[-1]
            return ("BULLISH" if last_close > ema21 else "BEARISH"), None

        highs = [s for s in swings if s.point_type == "HIGH"]
        lows = [s for s in swings if s.point_type == "LOW"]

        if not highs or not lows:
            return "NEUTRAL", None

        # Track state through swings
        trend = "NEUTRAL"
        last_event = None

        # Look at last 2 highs and last 2 lows
        prev_h, curr_h = highs[-2].price, highs[-1].price
        prev_l, curr_l = lows[-2].price, lows[-1].price

        # Check latest close relative to most recent swings
        latest_close = df["close"].iloc[-1]

        if curr_h > prev_h and curr_l > prev_l:
            trend = "BULLISH"
        elif curr_h < prev_h and curr_l < prev_l:
            trend = "BEARISH"
        else:
            # Consolidation / range
            trend = "BULLISH" if latest_close > highs[-1].price else ("BEARISH" if latest_close < lows[-1].price else "NEUTRAL")

        # Evaluate most recent structural breaks
        for i in range(len(df) - 1, max(0, len(df) - 15), -1):
            bar_close = df["close"].iloc[i]
            bar_time = df.index[i]

            # Bullish break
            if bar_close > highs[-1].price and highs[-1].bar_index < i:
                event_type = "BOS" if trend == "BULLISH" else "CHOCH"
                last_event = StructureEvent(
                    time=bar_time, event_type=event_type, direction="BULLISH",
                    broken_level=highs[-1].price, trigger_price=bar_close
                )
                break
            # Bearish break
            elif bar_close < lows[-1].price and lows[-1].bar_index < i:
                event_type = "BOS" if trend == "BEARISH" else "CHOCH"
                last_event = StructureEvent(
                    time=bar_time, event_type=event_type, direction="BEARISH",
                    broken_level=lows[-1].price, trigger_price=bar_close
                )
                break

        return trend, last_event

    def detect_supply_demand_zones(self, df: pd.DataFrame, tf: str) -> Tuple[List[SupplyDemandZone], List[SupplyDemandZone]]:
        """
        Locates fresh institutional Supply & Demand zones:
        Demand: base candles followed by impulsive green displacement candles
        Supply: base candles followed by impulsive red displacement candles
        Tracks touches and freshness (1-2 touches max).
        """
        atr = self._calc_atr(df, 14)
        impulse_mult = self.cfg.get("impulse_atr_mult", 1.3)
        max_touches = self.cfg.get("max_zone_touches", 2)

        demand_zones: List[SupplyDemandZone] = []
        supply_zones: List[SupplyDemandZone] = []
        n = len(df)

        for i in range(2, n - 2):
            bar_atr = atr.iloc[i]
            body = abs(df["close"].iloc[i] - df["open"].iloc[i])
            is_impulsive = body >= bar_atr * impulse_mult

            # Potential DEMAND: Impulsive Bullish candle (close > open)
            if is_impulsive and df["close"].iloc[i] > df["open"].iloc[i]:
                # Find base candle before impulse (last bearish or small pause)
                base_idx = i - 1
                base_low = df["low"].iloc[base_idx]
                base_high = max(df["open"].iloc[base_idx], df["close"].iloc[base_idx])

                zone = SupplyDemandZone(
                    timeframe=tf,
                    time=df.index[base_idx],
                    zone_type="DEMAND",
                    top=base_high,
                    bottom=base_low,
                    midpoint=(base_high + base_low) / 2.0,
                    impulse_ratio=round(body / max(bar_atr, 1e-6), 2)
                )

                # Check subsequent touches & mitigation
                touches = 0
                for j in range(i + 1, n):
                    # Touched if low pierces top of zone
                    if df["low"].iloc[j] <= zone.top and df["high"].iloc[j] >= zone.bottom:
                        touches += 1
                    # Mitigated/broken if candle closes below bottom
                    if df["close"].iloc[j] < zone.bottom:
                        zone.mitigated = True
                        break

                zone.touches = touches
                zone.is_fresh = (touches <= max_touches and not zone.mitigated)
                if not zone.mitigated:
                    demand_zones.append(zone)

            # Potential SUPPLY: Impulsive Bearish candle (close < open)
            elif is_impulsive and df["close"].iloc[i] < df["open"].iloc[i]:
                base_idx = i - 1
                base_high = df["high"].iloc[base_idx]
                base_low = min(df["open"].iloc[base_idx], df["close"].iloc[base_idx])

                zone = SupplyDemandZone(
                    timeframe=tf,
                    time=df.index[base_idx],
                    zone_type="SUPPLY",
                    top=base_high,
                    bottom=base_low,
                    midpoint=(base_high + base_low) / 2.0,
                    impulse_ratio=round(body / max(bar_atr, 1e-6), 2)
                )

                touches = 0
                for j in range(i + 1, n):
                    if df["high"].iloc[j] >= zone.bottom and df["low"].iloc[j] <= zone.top:
                        touches += 1
                    if df["close"].iloc[j] > zone.top:
                        zone.mitigated = True
                        break

                zone.touches = touches
                zone.is_fresh = (touches <= max_touches and not zone.mitigated)
                if not zone.mitigated:
                    supply_zones.append(zone)

        # Keep most recent relevant zones
        demand_zones.sort(key=lambda z: z.time, reverse=True)
        supply_zones.sort(key=lambda z: z.time, reverse=True)
        return demand_zones[:5], supply_zones[:5]

    def detect_reversal_patterns(self, df: pd.DataFrame, lookback: int = 12) -> List[CandlestickPattern]:
        """Detects rejection candles: Pin Bar / Hammer, Shooting Star, Wick Rejections, Bullish/Bearish Engulfing."""
        patterns = []
        n = len(df)
        start = max(1, n - lookback)

        for i in range(start, n):
            o = float(df["open"].iloc[i])
            c = float(df["close"].iloc[i])
            h = float(df["high"].iloc[i])
            l = float(df["low"].iloc[i])
            prev_o = float(df["open"].iloc[i - 1])
            prev_c = float(df["close"].iloc[i - 1])

            candle_range = h - l
            body = abs(c - o)
            if candle_range <= 0:
                continue

            lower_wick = min(o, c) - l
            upper_wick = h - max(o, c)

            # 1. Pin Bar / Hammer (lower rejection wick >= 40% of range)
            if lower_wick >= candle_range * 0.40 and body <= candle_range * 0.45:
                patterns.append(CandlestickPattern(
                    time=df.index[i], name="PIN_BAR_HAMMER", direction="BULLISH", price=c
                ))
            # 2. Shooting Star / Inverted Hammer (upper rejection wick >= 40% of range)
            elif upper_wick >= candle_range * 0.40 and body <= candle_range * 0.45:
                patterns.append(CandlestickPattern(
                    time=df.index[i], name="SHOOTING_STAR", direction="BEARISH", price=c
                ))
            # 3. Upper Wick Rejection (Bearish)
            elif upper_wick >= candle_range * 0.35 and c <= (h - upper_wick * 0.7):
                patterns.append(CandlestickPattern(
                    time=df.index[i], name="UPPER_WICK_REJECTION", direction="BEARISH", price=c
                ))
            # 4. Lower Wick Rejection (Bullish)
            elif lower_wick >= candle_range * 0.35 and c >= (l + lower_wick * 0.7):
                patterns.append(CandlestickPattern(
                    time=df.index[i], name="LOWER_WICK_REJECTION", direction="BULLISH", price=c
                ))
            # 5. Bullish Engulfing
            elif c > o and prev_c < prev_o and c >= prev_o and o <= prev_c:
                patterns.append(CandlestickPattern(
                    time=df.index[i], name="BULLISH_ENGULFING", direction="BULLISH", price=c
                ))
            # 6. Bearish Engulfing
            elif c < o and prev_c > prev_o and c <= prev_o and o >= prev_c:
                patterns.append(CandlestickPattern(
                    time=df.index[i], name="BEARISH_ENGULFING", direction="BEARISH", price=c
                ))

        return patterns

    def analyze_full_market(
        self,
        symbol: str,
        display_name: str,
        dfs: Dict[str, pd.DataFrame],
        digits: int,
        point: float,
        data_source: str = "TradingView",
        execution_tf: str = "M15"
    ) -> CompleteAnalysisResult:
        """Runs the complete top-down analysis from Monthly down to execution TF (M15, M5, or M1)."""
        tf_analyses: Dict[str, TFAnalysis] = {}
        exec_tf_clean = execution_tf.upper()
        if exec_tf_clean in ("5M", "5MIN", "5"):
            exec_tf_clean = "M5"
        elif exec_tf_clean in ("1M", "1MIN", "1"):
            exec_tf_clean = "M1"
        elif exec_tf_clean in ("15M", "15MIN", "15"):
            exec_tf_clean = "M15"
        elif exec_tf_clean in ("30M", "30MIN", "30"):
            exec_tf_clean = "M30"
        elif exec_tf_clean in ("1H", "60M", "60"):
            exec_tf_clean = "H1"

        tfs = self.cfg.get("timeframes", ["MN1", "W1", "D1", "H4", "H1", "M15"])
        if exec_tf_clean not in tfs:
            tfs = tfs + [exec_tf_clean]

        for tf in tfs:
            df = dfs.get(tf)
            if df is None or len(df) < 10:
                continue

            swing_len = self.cfg.get("swing_length", {}).get(tf, 2 if tf in ("M5", "M1") else 3)
            swings = self.find_swings(df, swing_len)
            bias, last_event = self.analyze_structure(df, swings)
            demands, supplies = self.detect_supply_demand_zones(df, tf)
            atr_val = float(self._calc_atr(df, 14).iloc[-1])
            curr_c = float(df["close"].iloc[-1])

            tf_analyses[tf] = TFAnalysis(
                timeframe=tf,
                bias=bias,
                last_event=last_event,
                recent_swings=swings[-6:],
                supply_zones=supplies,
                demand_zones=demands,
                atr=atr_val,
                current_close=curr_c
            )

        # Daily chart is the boss
        daily_bias = tf_analyses.get("D1", tf_analyses.get("H4")).bias if "D1" in tf_analyses or "H4" in tf_analyses else "NEUTRAL"
        # Overall macro bias (Monthly + Weekly)
        mn_bias = tf_analyses.get("MN1", tf_analyses.get("W1")).bias if "MN1" in tf_analyses else daily_bias
        overall_bias = daily_bias if daily_bias != "NEUTRAL" else mn_bias

        exec_df = dfs.get(exec_tf_clean, dfs.get("M15", dfs.get("H1")))
        current_price = float(exec_df["close"].iloc[-1])

        # Run SMC & Pattern Engines across key timeframes
        smc = SMCEngine()
        pattern_eng = PatternRecognitionEngine()

        all_obs: Dict[str, List[OrderBlock]] = {}
        all_fvgs: Dict[str, List[FairValueGap]] = {}
        all_pools: Dict[str, List[LiquidityPool]] = {}
        all_patterns: Dict[str, List[ChartPattern]] = {}
        all_trendlines: Dict[str, List[Trendline]] = {}
        all_fibs: Dict[str, Optional[FibonacciSetup]] = {}

        for tf, tfa in tf_analyses.items():
            df_tf = dfs.get(tf)
            if df_tf is not None and len(df_tf) >= 15:
                all_obs[tf] = smc.detect_order_blocks(df_tf, tf)
                all_fvgs[tf] = smc.detect_fair_value_gaps(df_tf, tf)
                all_pools[tf] = smc.detect_liquidity_pools(df_tf, tfa.recent_swings)
                all_patterns[tf] = pattern_eng.detect_all_patterns(df_tf, tfa.recent_swings)
                all_trendlines[tf] = pattern_eng.calculate_trendlines(df_tf, tfa.recent_swings)
                all_fibs[tf] = pattern_eng.calculate_fibonacci(df_tf, tfa.recent_swings)

        trade_plan = self._evaluate_trade_plan(
            symbol=symbol,
            display_name=display_name,
            dfs=dfs,
            tf_analyses=tf_analyses,
            daily_bias=daily_bias,
            current_price=current_price,
            digits=digits,
            point=point,
            execution_tf=exec_tf_clean,
            all_obs=all_obs
        )

        # ------------------------------------------------------------------
        # Institutional Arbitration & Context Reasoning Engine
        # ------------------------------------------------------------------
        inst_engine = InstitutionalReasoningEngine(digits=digits, point=point)
        tf_biases = {tf_k: tfa.bias for tf_k, tfa in tf_analyses.items()}
        bias_ctx = inst_engine.compute_arbitration_bias(tf_biases, exec_tf_clean)

        # 1. Range Context (on H1/H4 dealing range)
        range_df = dfs.get("H1", dfs.get("H4", exec_df))
        range_tf_name = "H1" if "H1" in dfs else ("H4" if "H4" in dfs else exec_tf_clean)
        range_ctx = inst_engine.validate_dealing_range(range_df, range_tf_name, current_price)

        # 2. Fib Context
        raw_fib = all_fibs.get(exec_tf_clean, all_fibs.get("H1", all_fibs.get("H4")))
        fib_ctx = inst_engine.validate_fibonacci(raw_fib, current_price)

        # 3. Pattern Context
        raw_pats = all_patterns.get(exec_tf_clean, all_patterns.get("M15", []))
        pat_ctxs = inst_engine.validate_and_deduplicate_patterns(raw_pats, exec_df, exec_tf_clean, current_price)

        # 4. Zones Aggregation & Proximity Filter (within 3.5 ATR)
        candidate_zones = []
        for tf_k, tfa in tf_analyses.items():
            candidate_zones.extend(tfa.supply_zones)
            candidate_zones.extend(tfa.demand_zones)
        for tf_k, obs in all_obs.items():
            for ob in obs:
                candidate_zones.append(SupplyDemandZone(
                    timeframe=ob.timeframe,
                    time=ob.time,
                    zone_type="DEMAND" if ob.ob_type == "BULLISH" else "SUPPLY",
                    top=ob.top,
                    bottom=ob.bottom,
                    midpoint=ob.midpoint,
                    impulse_ratio=ob.displacement_atr,
                    touches=ob.touches,
                    is_fresh=ob.is_fresh
                ))

        exec_atr = tf_analyses.get(exec_tf_clean, tf_analyses.get("M15", tf_analyses.get("H1"))).atr if tf_analyses else (point * 20)
        nearest_supply, nearest_demand, ranked_zones = inst_engine.filter_and_rank_zones(
            candidate_zones, current_price, atr=exec_atr, max_atr_dist=3.5
        )

        # 5. Actionable Triggers
        h1_choch = tf_analyses.get("H1", tf_analyses.get("H4", list(tf_analyses.values())[0])).last_event
        choch_lvl = h1_choch.broken_level if h1_choch else None
        triggers, inst_status = inst_engine.build_actionable_triggers(
            current_price=current_price,
            bias=bias_ctx,
            nearest_supply=nearest_supply,
            nearest_demand=nearest_demand,
            choch_level=choch_lvl,
            exec_tf=exec_tf_clean,
            atr=exec_atr
        )

        if trade_plan and trade_plan.status in ("TRIGGERED", "ARMED_IN_ZONE"):
            inst_status = trade_plan.status

        # 6. Session Context
        kz_info = SessionKillzoneManager.get_current_session_info()
        base_ccy = symbol[:3] if len(symbol) >= 6 else symbol
        quote_ccy = symbol[3:6] if len(symbol) >= 6 else "USD"
        news_list = EconomicCalendar.check_news_events(base_ccy, quote_ccy, window_minutes=120)
        if news_list:
            top_n = news_list[0]
            news_str = f"⚡ {top_n['country']} {top_n['impact'].upper()} in {top_n['minutes_away']}m ({top_n['title']})"
        else:
            news_str = "None in 2h"

        session_ctx = SessionContext(
            name=kz_info.get("killzone", "Interbank Hours"),
            utc_window=kz_info.get("utc_window", "24/7"),
            liquidity_level=kz_info.get("liquidity", "MEDIUM"),
            character=kz_info.get("character", ""),
            upcoming_news=news_str,
            minutes_to_close=kz_info.get("mins_to_rollover", 0)
        )

        # 7. Structure contexts
        structure_contexts = {}
        for tf_k, tfa in tf_analyses.items():
            last_b = tfa.last_event.broken_level if tfa.last_event and tfa.last_event.event_type == "BOS" else None
            last_c = tfa.last_event.broken_level if tfa.last_event and tfa.last_event.event_type == "CHOCH" else None
            p_high = max([s.price for s in tfa.recent_swings if s.point_type == "HIGH"] or [current_price])
            p_low = min([s.price for s in tfa.recent_swings if s.point_type == "LOW"] or [current_price])
            structure_contexts[tf_k] = StructureContext(
                tf=tf_k,
                trend=tfa.bias,
                last_bos=last_b,
                last_choch=last_c,
                protected_high=p_high,
                protected_low=p_low
            )

        inst_ctx = InstitutionalContext(
            timestamp=datetime.utcnow(),
            symbol=symbol,
            display_name=display_name,
            current_price=round(current_price, digits),
            digits=digits,
            point=point,
            pip_size=inst_engine.pip_size,
            execution_tf=exec_tf_clean,
            session=session_ctx,
            bias=bias_ctx,
            range=range_ctx,
            structure=structure_contexts,
            nearest_supply=nearest_supply,
            nearest_demand=nearest_demand,
            all_zones=ranked_zones,
            patterns=pat_ctxs,
            fib=fib_ctx,
            triggers=triggers,
            status=inst_status,
            data_source=data_source
        )

        return CompleteAnalysisResult(
            symbol=symbol,
            display_name=display_name,
            timestamp=datetime.utcnow(),
            current_price=round(current_price, digits),
            digits=digits,
            point=point,
            tf_analyses=tf_analyses,
            overall_bias=bias_ctx.direction,
            daily_bias=daily_bias,
            execution_tf=exec_tf_clean,
            trade_plan=trade_plan,
            data_source=data_source,
            order_blocks=all_obs,
            fair_value_gaps=all_fvgs,
            liquidity_pools=all_pools,
            patterns=all_patterns,
            trendlines=all_trendlines,
            fibonacci=all_fibs,
            institutional_context=inst_ctx
        )

    def _evaluate_trade_plan(
        self,
        symbol: str,
        display_name: str,
        dfs: Dict[str, pd.DataFrame],
        tf_analyses: Dict[str, TFAnalysis],
        daily_bias: str,
        current_price: float,
        digits: int,
        point: float,
        execution_tf: str = "M15",
        all_obs: Dict[str, List[OrderBlock]] = None
    ) -> Optional[TradePlan]:
        """
        Evaluates active Supply & Demand zones and Order Blocks across all timeframes:
        - When price is inside/testing a zone: returns setup with status ARMED_IN_ZONE.
        - When both rejection candle and mini BOS print on execution TF: status TRIGGERED.
        - When price is floating between zones: returns None (patience).
        """
        exec_df = dfs.get(execution_tf, dfs.get("M15", dfs.get("H1")))
        if exec_df is None or len(exec_df) < 15:
            return None

        # 1. Gather all potential zones & order blocks across timeframes
        zones_to_check: List[SupplyDemandZone] = []
        for tf_key, tfa in tf_analyses.items():
            zones_to_check.extend(tfa.demand_zones)
            zones_to_check.extend(tfa.supply_zones)

        if all_obs:
            for tf_key, obs in all_obs.items():
                for ob in obs:
                    zones_to_check.append(SupplyDemandZone(
                        timeframe=tf_key,
                        time=ob.time,
                        zone_type="DEMAND" if ob.ob_type == "BULLISH" else "SUPPLY",
                        top=ob.top,
                        bottom=ob.bottom,
                        midpoint=ob.midpoint,
                        impulse_ratio=ob.displacement_atr,
                        touches=ob.touches,
                        is_fresh=ob.is_fresh
                    ))

        # Also add recent swing extremes (resistance / support range)
        recent_high = float(exec_df["high"].tail(35).max())
        recent_low = float(exec_df["low"].tail(35).min())
        range_span = recent_high - recent_low
        zones_to_check.append(SupplyDemandZone(
            timeframe=execution_tf, time=exec_df.index[-1], zone_type="SUPPLY",
            top=recent_high, bottom=recent_high - (range_span * 0.08),
            midpoint=recent_high - (range_span * 0.04), impulse_ratio=1.5, touches=1, is_fresh=True
        ))
        zones_to_check.append(SupplyDemandZone(
            timeframe=execution_tf, time=exec_df.index[-1], zone_type="DEMAND",
            top=recent_low + (range_span * 0.08), bottom=recent_low,
            midpoint=recent_low + (range_span * 0.04), impulse_ratio=1.5, touches=1, is_fresh=True
        ))

        # 2. Find matching zone where price is currently inside or touching
        matched_zone = None
        for z in zones_to_check:
            z_span = max(abs(z.top - z.bottom), point * 10)
            buf = z_span * 0.25
            if (z.bottom - buf) <= current_price <= (z.top + buf):
                matched_zone = z
                break

        if not matched_zone:
            return None

        zone_direction = "BUY" if matched_zone.zone_type == "DEMAND" else "SELL"

        # 3. Double Confirmation Check on Execution Timeframe (15m, 5m, or 1m)
        lookback = 12 if execution_tf in ("M5", "M1") else 16
        patterns = self.detect_reversal_patterns(exec_df, lookback=lookback)
        matching_rejection = next((p for p in reversed(patterns) if (
            (zone_direction == "BUY" and p.direction == "BULLISH") or
            (zone_direction == "SELL" and p.direction == "BEARISH")
        )), None)

        swings = self.find_swings(exec_df, swing_len=2 if execution_tf in ("M5", "M1") else 3)
        trend, event = self.analyze_structure(exec_df, swings)
        mini_bos = event if (event and event.direction == ("BULLISH" if zone_direction == "BUY" else "BEARISH")) else None

        # 4. Status determination
        if matching_rejection and mini_bos:
            setup_status = "TRIGGERED"
        else:
            setup_status = "ARMED_IN_ZONE"

        # 5. Determine Entry, Structural SL, and TP
        h1_analysis = tf_analyses.get("H1", tf_analyses.get("H4", list(tf_analyses.values())[0]))
        h1_atr = h1_analysis.atr
        sl_buffer = max(self.cfg.get("sl_buffer_atr", 0.2) * h1_atr, point * 15)

        if zone_direction == "BUY":
            entry = current_price
            sl = matched_zone.bottom - sl_buffer
            risk = max(entry - sl, point * 10)

            min_rr = self.cfg.get("min_rr", 2.0)
            tp1 = entry + (risk * min_rr)
            opposing = [z for z in zones_to_check if z.zone_type == "SUPPLY" and z.bottom > entry]
            tp2 = opposing[0].bottom if opposing else entry + (risk * 3.5)
            be_price = entry + (risk * self.cfg.get("breakeven_rr", 1.0))

            rr1 = round((tp1 - entry) / risk, 2)
            rr2 = round((tp2 - entry) / risk, 2)

        else:  # SELL
            entry = current_price
            sl = matched_zone.top + sl_buffer
            risk = max(sl - entry, point * 10)

            min_rr = self.cfg.get("min_rr", 2.0)
            tp1 = entry - (risk * min_rr)
            opposing = [z for z in zones_to_check if z.zone_type == "DEMAND" and z.top < entry]
            tp2 = opposing[0].top if opposing else entry - (risk * 3.5)
            be_price = entry - (risk * self.cfg.get("breakeven_rr", 1.0))

            rr1 = round((entry - tp1) / risk, 2)
            rr2 = round((entry - tp2) / risk, 2)

        # 6. Sizing & Risk Management
        acc_balance = self.cfg.get("account_balance", 1000.0)
        risk_pct = self.cfg.get("risk_pct", 1.0)
        risk_dollars = acc_balance * (risk_pct / 100.0)
        risk_points = risk / point
        lot_size = round(max(0.01, min(10.0, risk_dollars / max(risk, 0.01))), 2)

        checklist = {
            "Price is at key Supply/Demand zone": True,
            "Daily structure is identified": daily_bias != "NEUTRAL",
            "Trading with zone direction": True,
            f"Rejection candlestick visible ({execution_tf})": matching_rejection is not None,
            f"{execution_tf} shows mini BOS confirming direction": mini_bos is not None,
            "Stop loss placement is logical (Structural SL)": True,
            "Risk-reward ratio at least 1:2": rr1 >= 2.0,
            "Risk is 1-2% of account": True
        }

        notes = [
            f"Zone: {matched_zone.timeframe} {matched_zone.zone_type} ({matched_zone.bottom:.{digits}f} - {matched_zone.top:.{digits}f})",
            f"Execution Timeframe: {execution_tf}",
            f"Status: {setup_status}",
            f"Lock BE at 1:1 RR: {be_price:.{digits}f}",
            f"Take 50% partial at 1:2 RR (TP1): {tp1:.{digits}f}"
        ]

        return TradePlan(
            symbol=symbol,
            display_name=display_name,
            direction=zone_direction,
            status=setup_status,
            execution_tf=execution_tf,
            entry_price=round(entry, digits),
            stop_loss=round(sl, digits),
            take_profit_1=round(tp1, digits),
            take_profit_2=round(tp2, digits),
            break_even_price=round(be_price, digits),
            risk_reward_1=rr1,
            risk_reward_2=rr2,
            sl_pips_or_points=round(risk_points, 1),
            recommended_lot_size=lot_size,
            active_zone=matched_zone,
            rejection_pattern=matching_rejection,
            mini_bos=mini_bos,
            checklist=checklist,
            notes=notes
        )
