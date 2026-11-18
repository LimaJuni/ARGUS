import os
import json
import logging
import requests
from anomaly_detector import TradeAnomalySetup

logger = logging.getLogger("DiscordNotifier")

class DiscordNotifier:
    def __init__(self, webhook_url: str = "", execution_enabled: bool = False):
        self.webhook_url = webhook_url
        self.execution_enabled = execution_enabled

    def send_trade_alert(self, setup: TradeAnomalySetup, chart_image_path: str = "") -> bool:
        """
        Pushes a structured institutional anomaly alert + chart snapshot to Discord.
        """
        if not self.webhook_url:
            return False

        d = setup.digits
        embed_color = 0x00E676 if setup.direction == "BUY" else 0xFF1744
        confluence_text = "\n".join([f"• {c}" for c in setup.confluences])

        embed = {
            "title": f"🚨 QUANT ANOMALY: {setup.direction} {setup.display_name} ({setup.timeframe})",
            "description": f"**Setup:** `{setup.setup_name}`\n**Macro Bias:** `{setup.macro_bias}` | **Structure:** `{setup.structure_type}`",
            "color": embed_color,
            "fields": [
                {
                    "name": "🎯 Key Parameters",
                    "value": (
                        f"**Entry Zone:** `{setup.entry_price:.{d}f}`\n"
                        f"**Stop Loss:** `{setup.stop_loss:.{d}f}`\n"
                        f"**Target 1 (TP1):** `{setup.take_profit_1:.{d}f}`\n"
                        f"**Target 2 (TP2):** `{setup.take_profit_2:.{d}f}`"
                    ),
                    "inline": True
                },
                {
                    "name": "📊 Quantitative Edge",
                    "value": (
                        f"**Risk/Reward:** `1:{setup.risk_reward_ratio:.2f}`\n"
                        f"**Live Spread:** `{setup.spread_points:.0f} pts` (`{setup.spread_quote:.{d}f}`)\n"
                        f"**Execution Status:** `{'AUTO-EXECUTE ARMED' if self.execution_enabled else 'SIGNAL ONLY'}`"
                    ),
                    "inline": True
                },
                {
                    "name": "🧩 SMC & Order Flow Confluences",
                    "value": confluence_text or "Standard structure alignment",
                    "inline": False
                }
            ],
            "footer": {
                "text": f"Autonomous Multi-Asset Engine | {'MT5 Execution' if self.execution_enabled else 'Signal Mode'} | {setup.timestamp.strftime('%Y-%m-%d %H:%M:%S UTC')}"
            }
        }

        payload = {"embeds": [embed]}

        if chart_image_path and os.path.exists(chart_image_path):
            filename = os.path.basename(chart_image_path)
            embed["image"] = {"url": f"attachment://{filename}"}
            payload["embeds"] = [embed]
            
            with open(chart_image_path, "rb") as f:
                response = requests.post(
                    self.webhook_url,
                    data={"payload_json": json.dumps(payload)},
                    files={"file": (filename, f, "image/png")}
                )
        else:
            response = requests.post(self.webhook_url, json=payload)

        return response.status_code in (200, 204)

    def send_execution_alert(
        self,
        ticket: int,
        symbol: str,
        direction: str,
        lot_size: float,
        price: float,
        sl: float,
        tp: float,
        setup_name: str
    ) -> bool:
        """
        Pushes an immediate notification when a trade is executed live in MT5.
        """
        if not self.webhook_url:
            return False

        color = 0x2196F3 if direction.upper() == "BUY" else 0x9C27B0

        embed = {
            "title": f"⚡ LIVE TRADE EXECUTED: {direction.upper()} #{ticket}",
            "color": color,
            "fields": [
                {
                    "name": "Asset & Volume",
                    "value": f"**Symbol:** `{symbol}`\n**Volume:** `{lot_size} Lots`",
                    "inline": True
                },
                {
                    "name": "Order Prices",
                    "value": f"**Filled Price:** `{price}`\n**Stop Loss:** `{sl}`\n**Take Profit:** `{tp}`",
                    "inline": True
                },
                {
                    "name": "Strategy & Protection",
                    "value": f"**Setup:** `{setup_name}`\n**Protection:** `Dynamic Trailing Stop Active`",
                    "inline": False
                }
            ],
            "footer": {
                "text": f"MT5 Multi-Asset Execution Engine | Ticket #{ticket}"
            }
        }

        response = requests.post(self.webhook_url, json={"embeds": [embed]})
        return response.status_code in (200, 204)

    def send_trailing_update(
        self,
        ticket: int,
        symbol: str,
        direction: str,
        new_sl: float,
        reason: str
    ) -> bool:
        """
        Pushes a notification when a trailing stop or break-even modification occurs.
        """
        if not self.webhook_url:
            return False

        embed = {
            "title": f"🛡️ PROFIT PROTECTED: {symbol} #{ticket} ({direction})",
            "description": f"**Action:** `{reason}`\n**New Stop Loss Level:** `{new_sl}`",
            "color": 0xFFD700,
            "footer": {
                "text": f"Multi-Asset Trailing Manager | {symbol}"
            }
        }

        response = requests.post(self.webhook_url, json={"embeds": [embed]})
        return response.status_code in (200, 204)
