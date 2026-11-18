"""
Institutional Forex Trading Framework Modules:
1. EconomicCalendar: Live Forex Factory weekly high-impact events cache & news impact checker.
2. SessionKillzoneManager: Institutional Killzone tracking (Asian Range, London Open Judas Swing, New York Open, London Close).
3. InstitutionalValuation: Premium vs. Discount 50% Dealing Range calculation.
4. DXYCompass: US Dollar Index (DXY) alignment & SMT intermarket correlation check.
"""

import time
import json
import logging
import urllib.request
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("InstitutionalLayers")

FF_CALENDAR_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"


class EconomicCalendar:
    """Fetches and caches Forex Factory economic calendar for high-impact news warnings."""
    _cache: List[dict] = []
    _last_fetch: float = 0.0

    @classmethod
    def get_calendar(cls, max_age_seconds: int = 1800) -> List[dict]:
        now = time.time()
        if cls._cache and (now - cls._last_fetch < max_age_seconds):
            return cls._cache

        try:
            req = urllib.request.Request(
                FF_CALENDAR_URL,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) InstitutionalBot/2.0"}
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
                cls._cache = data
                cls._last_fetch = now
                logger.info(f"[EconomicCalendar] Cached {len(data)} weekly economic events.")
        except Exception as e:
            logger.warning(f"[EconomicCalendar] Failed to fetch Forex Factory calendar: {e}")

        return cls._cache

    @classmethod
    def check_news_events(cls, currency_base: str, currency_quote: str, window_minutes: int = 60) -> List[dict]:
        """
        Returns any upcoming (or recent within window) High/Medium impact events for the currency pair.
        """
        events = cls.get_calendar()
        if not events:
            return []

        relevant_currencies = {currency_base.upper(), currency_quote.upper()}
        # For Gold / commodities, USD news is primary driver
        if "XAU" in relevant_currencies or "XAG" in relevant_currencies:
            relevant_currencies.add("USD")

        now_utc = datetime.now(timezone.utc)
        flagged = []

        for e in events:
            country = e.get("country", "").upper()
            impact = e.get("impact", "")
            if country not in relevant_currencies or impact not in ("High", "Medium"):
                continue

            date_str = e.get("date", "")
            if not date_str:
                continue

            try:
                # ISO Format: 2026-11-18T08:30:00-05:00
                event_dt = datetime.fromisoformat(date_str).astimezone(timezone.utc)
                diff_minutes = (event_dt - now_utc).total_seconds() / 60.0

                # Flag if event is within past 30 mins or next `window_minutes`
                if -30 <= diff_minutes <= window_minutes:
                    flagged.append({
                        "title": e.get("title"),
                        "country": country,
                        "impact": impact,
                        "time_utc": event_dt.strftime("%H:%M UTC"),
                        "minutes_away": int(round(diff_minutes)),
                        "forecast": e.get("forecast", "-"),
                        "previous": e.get("previous", "-")
                    })
            except Exception:
                continue

        return flagged


class SessionKillzoneManager:
    """
    Institutional Session & Killzone Radar (New York EST):
    - Asian Session: 19:00 - 22:00 EST (00:00 - 03:00 UTC) -> Range accumulation
    - London Killzone: 02:00 - 05:00 EST (07:00 - 10:00 UTC) -> The 'Judas Swing'
    - New York Killzone: 07:00 - 10:00 EST (12:00 - 15:00 UTC) -> Data releases & volume expansion
    - London Close: 10:00 - 12:00 EST (15:00 - 17:00 UTC) -> Mean reversion & liquidity absorption
    """

    @staticmethod
    def get_current_session_info() -> Dict[str, str]:
        now_utc = datetime.now(timezone.utc)
        hour = now_utc.hour + (now_utc.minute / 60.0)

        # Mapped to UTC (EST = UTC-5 standard)
        in_asian = (0.0 <= hour < 7.0) or (23.0 <= hour <= 24.0)
        in_london_kz = (7.0 <= hour < 12.0)
        in_overlap = (12.0 <= hour < 16.0)
        in_ny_close = (16.0 <= hour < 21.0)
        in_rollover = (21.0 <= hour < 23.0)

        # Distance to 22:00 UTC Interbank Rollover
        mins_to_rollover = int(((22.0 - hour) % 24) * 60)

        if in_overlap:
            active_kz = "London / NY Overlap"
            utc_win = "12:00 - 16:00 UTC"
            liquidity = "HIGH"
            character = "🔥 Peak Global Volume: Institutional continuation and macro order matching."
            institutional_weight = "OPTIMAL"
        elif in_london_kz:
            active_kz = "London Morning Session"
            utc_win = "07:00 - 12:00 UTC"
            liquidity = "HIGH"
            character = "⚡ High Volatility: Judas Swing manipulation of Asian session high/low."
            institutional_weight = "OPTIMAL"
        elif in_ny_close:
            active_kz = "NY Afternoon Session"
            utc_win = "16:00 - 21:00 UTC"
            liquidity = "MEDIUM"
            character = "🔄 Profit Taking & Rebalancing: London desks closed, steady trend absorption."
            institutional_weight = "MODERATE"
        elif in_rollover:
            active_kz = "Interbank Rollover Window"
            utc_win = "21:00 - 23:00 UTC"
            liquidity = "LOW"
            character = f"⚠️ Low Liquidity / Widened Spreads: Daily swap reset in {mins_to_rollover}m."
            institutional_weight = "LOW_PROBABILITY"
        else:
            active_kz = "Tokyo / Asian Session"
            utc_win = "00:00 - 07:00 UTC"
            liquidity = "MEDIUM"
            character = "📦 Asian Range Consolidation: Accumulation phase for the trading day."
            institutional_weight = "CONSOLIDATION"

        return {
            "current_time_utc": now_utc.strftime("%H:%M:%S UTC"),
            "killzone": active_kz,
            "utc_window": utc_win,
            "liquidity": liquidity,
            "mins_to_rollover": mins_to_rollover,
            "character": character,
            "weight": institutional_weight,
            "is_high_volume": (in_london_kz or in_overlap)
        }


class InstitutionalValuation:
    """
    Computes Premium vs. Discount dealing ranges:
    Equilibrium = 50% of the active Daily/4H dealing range.
    - Above 50%: PREMIUM -> Banks only look to SELL.
    - Below 50%: DISCOUNT -> Banks only look to BUY.
    """

    @staticmethod
    def evaluate_valuation(current_price: float, h4_swings: list) -> Dict:
        if not h4_swings or len(h4_swings) < 2:
            return {"status": "UNKNOWN", "equilibrium": 0.0, "pct_in_range": 50.0}

        highs = [s.price for s in h4_swings if s.point_type == "HIGH"]
        lows = [s.price for s in h4_swings if s.point_type == "LOW"]
        if not highs or not lows:
            return {"status": "UNKNOWN", "equilibrium": 0.0, "pct_in_range": 50.0}

        range_high = max(highs[-2:])
        range_low = min(lows[-2:])
        range_span = range_high - range_low

        if range_span <= 0:
            return {"status": "UNKNOWN", "equilibrium": range_high, "pct_in_range": 50.0}

        equilibrium = (range_high + range_low) / 2.0
        pct_in_range = ((current_price - range_low) / range_span) * 100.0

        if pct_in_range > 50.0:
            status = "PREMIUM (Sell-Side Favorable for Smart Money)"
            zone_color = "🔴"
            favorable_direction = "SELL"
        else:
            status = "DISCOUNT (Buy-Side Favorable for Smart Money)"
            zone_color = "🟢"
            favorable_direction = "BUY"

        return {
            "status": status,
            "zone_color": zone_color,
            "equilibrium": equilibrium,
            "range_high": range_high,
            "range_low": range_low,
            "pct_in_range": round(pct_in_range, 1),
            "favorable_direction": favorable_direction
        }
