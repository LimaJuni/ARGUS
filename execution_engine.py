import logging
from typing import Optional, Dict, Tuple, List
import MetaTrader5 as mt5
from config import EngineConfig, AssetSettings
from discord_notifier import DiscordNotifier

logger = logging.getLogger("ExecutionEngine")

class MT5ExecutionEngine:
    def __init__(self, config: EngineConfig, notifier: Optional[DiscordNotifier] = None):
        self.config = config
        self.magic = config.magic_number
        self.notifier = notifier

    def get_open_positions(self, symbol: Optional[str] = None) -> List:
        """Fetch active positions (all or filtered by symbol)."""
        if symbol:
            positions = mt5.positions_get(symbol=symbol)
        else:
            positions = mt5.positions_get()
        if positions is None:
            return []
        return list(positions)

    def can_execute_trade(self, symbol: str, signal_direction: str) -> Tuple[bool, str]:
        """
        Enforces portfolio constraints:
        1. Max 3 total concurrent positions across portfolio.
        2. Max 1 trade per pair (XAUUSD, EURUSD, USDJPY).
        3. Strict unidirectional rule per pair (No hedging).
        """
        all_positions = self.get_open_positions()
        
        # 1. Total portfolio limit check
        if len(all_positions) >= self.config.max_total_positions:
            return False, f"Portfolio capacity reached ({len(all_positions)}/{self.config.max_total_positions} positions active)"

        # 2. Per-symbol limit check
        sym_positions = [p for p in all_positions if p.symbol == symbol]
        if len(sym_positions) >= self.config.max_positions_per_symbol:
            return False, f"Max positions reached for {symbol} ({len(sym_positions)}/{self.config.max_positions_per_symbol})"

        # 3. Check for conflicting directions on this specific symbol
        has_buy = any(p.type == mt5.ORDER_TYPE_BUY for p in sym_positions)
        has_sell = any(p.type == mt5.ORDER_TYPE_SELL for p in sym_positions)

        if signal_direction.upper() == "BUY" and has_sell:
            return False, f"Conflicting SELL trade is currently open on {symbol}. Hedging is disabled."
        elif signal_direction.upper() == "SELL" and has_buy:
            return False, f"Conflicting BUY trade is currently open on {symbol}. Hedging is disabled."

        return True, "Execution Allowed"

    def _determine_filling_mode(self, sym_info) -> int:
        """Determines supported order filling mode."""
        filling_mode = sym_info.filling_mode
        if filling_mode & 2:
            return mt5.ORDER_FILLING_IOC
        elif filling_mode & 1:
            return mt5.ORDER_FILLING_FOK
        else:
            return mt5.ORDER_FILLING_RETURN

    def execute_trade(
        self,
        symbol: str,
        direction: str,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        setup_name: str
    ) -> Optional[Dict]:
        """
        Executes a market order in MT5 with SL/TP and attaches magic number and comment.
        """
        allowed, reason = self.can_execute_trade(symbol, direction)
        if not allowed:
            logger.info(f"Execution rejected for {symbol}: {reason}")
            return None

        sym_info = mt5.symbol_info(symbol)
        if not sym_info:
            logger.error(f"Cannot get symbol info for {symbol}")
            return None

        digits = sym_info.digits
        order_type = mt5.ORDER_TYPE_BUY if direction.upper() == "BUY" else mt5.ORDER_TYPE_SELL
        price = sym_info.ask if direction.upper() == "BUY" else sym_info.bid
        filling = self._determine_filling_mode(sym_info)

        sl_price = round(stop_loss, digits)
        tp_price = round(take_profit, digits)

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": self.config.lot_size,
            "type": order_type,
            "price": price,
            "sl": sl_price,
            "tp": tp_price,
            "deviation": 30,
            "magic": self.magic,
            "comment": f"SMC_{symbol[:3]}_{direction[:3]}",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": filling,
        }

        logger.info(f"[ORDER] Submitting {direction} on {symbol}: {self.config.lot_size} lots @ {price} (SL: {sl_price}, TP: {tp_price})")
        result = mt5.order_send(request)

        if result is None:
            logger.error(f"Order send failed for {symbol}: {mt5.last_error()}")
            return None

        if result.retcode != mt5.TRADE_RETCODE_DONE:
            logger.error(f"Order failed on {symbol} with retcode: {result.retcode} ({result.comment})")
            return None

        logger.info(f"[EXECUTED] Order successfully placed on {symbol}! Ticket #{result.order} @ {result.price}")

        if self.notifier and self.config.discord_webhook_url:
            self._notify_execution(result.order, symbol, direction, result.price, sl_price, tp_price, setup_name)

        return {
            "ticket": result.order,
            "symbol": symbol,
            "direction": direction,
            "price": result.price,
            "volume": result.volume,
            "sl": sl_price,
            "tp": tp_price
        }

    def manage_trailing_stops(self):
        """
        Dynamically trails profit for all active positions across portfolio,
        using asset-tailored BE & Trailing parameters.
        """
        if not self.config.enable_trailing_stop:
            return

        positions = self.get_open_positions()
        if not positions:
            return

        for pos in positions:
            symbol = pos.symbol
            asset_cfg = self.config.assets.get(symbol)
            if not asset_cfg:
                continue

            sym_info = mt5.symbol_info(symbol)
            if not sym_info:
                continue

            point = sym_info.point
            digits = sym_info.digits
            current_bid = sym_info.bid
            current_ask = sym_info.ask

            ticket = pos.ticket
            pos_type = pos.type
            open_price = pos.price_open
            current_sl = pos.sl

            new_sl = None
            action_reason = ""

            # ---------------------------
            # BUY POSITIONS
            # ---------------------------
            if pos_type == mt5.ORDER_TYPE_BUY:
                profit_points = (current_bid - open_price) / point

                # Check Break-Even trigger
                be_level = round(open_price + (asset_cfg.be_offset_points * point), digits)
                if profit_points >= asset_cfg.be_trigger_points and (current_sl < be_level or current_sl == 0.0):
                    new_sl = be_level
                    action_reason = f"Break-Even Locked (+{asset_cfg.be_offset_points * point:.{digits}f} gain)"

                # Check Dynamic Trailing Stop
                if profit_points >= asset_cfg.trail_start_points:
                    trail_target = round(current_bid - (asset_cfg.trail_distance_points * point), digits)
                    min_improvement = asset_cfg.trail_step_points * point
                    if trail_target > (current_sl + min_improvement):
                        new_sl = trail_target
                        locked_gain = (trail_target - open_price)
                        action_reason = f"Dynamic Profit Trailed (Locked +{locked_gain:.{digits}f})"

            # ---------------------------
            # SELL POSITIONS
            # ---------------------------
            elif pos_type == mt5.ORDER_TYPE_SELL:
                profit_points = (open_price - current_ask) / point

                # Check Break-Even trigger
                be_level = round(open_price - (asset_cfg.be_offset_points * point), digits)
                if profit_points >= asset_cfg.be_trigger_points and (current_sl > be_level or current_sl == 0.0):
                    new_sl = be_level
                    action_reason = f"Break-Even Locked (+{asset_cfg.be_offset_points * point:.{digits}f} gain)"

                # Check Dynamic Trailing Stop
                if profit_points >= asset_cfg.trail_start_points:
                    trail_target = round(current_ask + (asset_cfg.trail_distance_points * point), digits)
                    min_improvement = asset_cfg.trail_step_points * point
                    if current_sl == 0.0 or trail_target < (current_sl - min_improvement):
                        new_sl = trail_target
                        locked_gain = (open_price - trail_target)
                        action_reason = f"Dynamic Profit Trailed (Locked +{locked_gain:.{digits}f})"

            if new_sl is not None and new_sl != current_sl:
                self._modify_position_sl(pos, symbol, new_sl, action_reason)

    def _modify_position_sl(self, pos, symbol: str, new_sl: float, reason: str):
        """Sends modification request to MT5 to trail the Stop Loss."""
        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "position": pos.ticket,
            "symbol": symbol,
            "sl": new_sl,
            "tp": pos.tp,
        }

        result = mt5.order_send(request)
        if result and result.retcode == mt5.TRADE_RETCODE_DONE:
            logger.info(f"[TRAILING] {symbol} #{pos.ticket} SL Modified to {new_sl} | {reason}")
            if self.notifier and self.config.discord_webhook_url:
                self.notifier.send_trailing_update(
                    ticket=pos.ticket,
                    symbol=symbol,
                    direction="BUY" if pos.type == mt5.ORDER_TYPE_BUY else "SELL",
                    new_sl=new_sl,
                    reason=reason
                )
        else:
            retcode = result.retcode if result else mt5.last_error()
            logger.warning(f"Failed to modify SL for {symbol} #{pos.ticket}: {retcode}")

    def _notify_execution(self, ticket: int, symbol: str, direction: str, price: float, sl: float, tp: float, setup: str):
        """Sends execution notice to Discord."""
        if not self.notifier:
            return
        self.notifier.send_execution_alert(
            ticket=ticket,
            symbol=symbol,
            direction=direction,
            lot_size=self.config.lot_size,
            price=price,
            sl=sl,
            tp=tp,
            setup_name=setup
        )
