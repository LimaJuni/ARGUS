"""
Interactive Multi-Timeframe Institutional Forex Trading Discord Bot.
Allows you to message Discord directly:
- '!analyze gold' / '!analyze xauusd' -> Generates full top-down analysis (MN1 -> M15) + chart
- '!analyze eurusd' / '!analyze usdjpy' / '!analyze gbpusd'
- '!scan' -> Scans the entire watchlist for 15m double-confirmation setups
- '!bias' -> Quick macro bias overview (Monthly, Weekly, Daily) across pairs
- '!strategy' -> View current strategy rules
- '!set min_rr 2.5' / '!set risk_pct 1.5' -> Dynamically update parameters
- '!note <text>' -> Save custom analysis notes
"""

import os
import sys
import json
import time
import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional, List, Dict, Tuple
import discord
from discord.ext import commands, tasks

from config import EngineConfig
from symbols import resolve_symbol, SymbolSpec
from strategy import load_strategy, set_value, add_note, remove_note
from tv_history import fetch_multi_tf, digits_from_meta
from mtf_analyzer import MTFPriceActionAnalyzer, CompleteAnalysisResult
from mtf_visualizer import MTFChartVisualizer
from clean_chart_visualizer import CleanTradingViewVisualizer
from institutional_layers import EconomicCalendar, SessionKillzoneManager, InstitutionalValuation

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s"
)
logger = logging.getLogger("DiscordBot")

# Load Bot Token & Webhook from .env
env_file = os.path.join(os.path.dirname(__file__), ".env")
if os.path.exists(env_file):
    with open(env_file, "r") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "").strip()

# App commands (slash commands) work WITHOUT privileged Message Content intent!
intents = discord.Intents.default()
# Try message_content if available, otherwise default standard intents
# intents.message_content = True

bot = commands.Bot(command_prefix=["!", "/"], intents=intents, help_command=None)
tree = bot.tree  # Slash command tree
visualizer = MTFChartVisualizer(output_dir="snapshots")
clean_visualizer = CleanTradingViewVisualizer(output_dir="snapshots")

def normalize_tf(tf_str: str) -> str:
    """Normalizes user timeframe strings (e.g., '5m', '5min', '1m', '15min') to standard labels."""
    t = tf_str.lower().strip().strip(":")
    if t in ("1m", "1min", "m1", "1"):
        return "M1"
    elif t in ("5m", "5min", "m5", "5"):
        return "M5"
    elif t in ("15m", "15min", "m15", "15"):
        return "M15"
    elif t in ("30m", "30min", "m30", "30"):
        return "M30"
    elif t in ("1h", "60m", "h1", "60"):
        return "H1"
    elif t in ("4h", "h4", "240"):
        return "H4"
    elif t in ("1d", "d1", "daily"):
        return "D1"
    return "M15"

def parse_symbol_and_tf(query: str, default_tf: str = "15m") -> Tuple[str, str]:
    """Parses queries like 'usdcad 5m', 'gold 1min', 'eurusd' into symbol and timeframe."""
    tokens = query.strip().split()
    if not tokens:
        return "gold", "M15"

    tf = normalize_tf(default_tf)
    sym_tokens = []
    tf_indicators = {
        "1m", "1min", "m1", "1",
        "5m", "5min", "m5", "5",
        "15m", "15min", "m15", "15",
        "30m", "30min", "m30", "30",
        "1h", "60m", "h1", "60",
        "4h", "h4", "240",
        "1d", "d1", "daily"
    }

    for tok in tokens:
        clean = tok.lower().strip(",").strip(":")
        if clean in tf_indicators:
            tf = normalize_tf(clean)
        elif clean in ("on", "at", "tf", "timeframe", "for"):
            continue
        else:
            sym_tokens.append(tok)

    sym = " ".join(sym_tokens) if sym_tokens else "gold"
    return sym, tf

# ------------------------------------------------------------------
# User Alert Subscriptions Store
# ------------------------------------------------------------------
ALERTS_FILE = os.path.join(os.path.dirname(__file__), "user_alerts.json")

def load_user_alerts() -> Dict[str, List[dict]]:
    if os.path.exists(ALERTS_FILE):
        try:
            with open(ALERTS_FILE, "r") as f:
                raw = json.load(f)
            # Normalize legacy list-of-strings if present
            cleaned: Dict[str, List[dict]] = {}
            for uid, items in raw.items():
                norm_list = []
                for item in items:
                    if isinstance(item, str):
                        norm_list.append({
                            "symbol": item.upper(),
                            "tf": "M15",
                            "channel_id": None,
                            "last_status": None,
                            "last_alert_ts": 0
                        })
                    elif isinstance(item, dict) and "symbol" in item:
                        norm_list.append(item)
                if norm_list:
                    cleaned[uid] = norm_list
            return cleaned
        except Exception:
            return {}
    return {}

def save_user_alerts(alerts: Dict[str, List[dict]]):
    with open(ALERTS_FILE, "w") as f:
        json.dump(alerts, f, indent=2)

def toggle_user_alert(user_id: int, symbol: str, tf: str = "M15", channel_id: Optional[int] = None) -> bool:
    alerts = load_user_alerts()
    uid = str(user_id)
    user_list = alerts.get(uid, [])
    sym_upper = symbol.upper()
    tf_norm = normalize_tf(tf)

    existing_idx = next(
        (i for i, a in enumerate(user_list) if a["symbol"] == sym_upper and a["tf"] == tf_norm),
        None
    )
    if existing_idx is not None:
        user_list.pop(existing_idx)
        subbed = False
    else:
        user_list.append({
            "symbol": sym_upper,
            "tf": tf_norm,
            "channel_id": channel_id,
            "last_status": None,
            "last_alert_ts": 0
        })
        subbed = True

    if user_list:
        alerts[uid] = user_list
    elif uid in alerts:
        del alerts[uid]

    save_user_alerts(alerts)
    return subbed

def clear_user_alerts(user_id: int) -> int:
    alerts = load_user_alerts()
    uid = str(user_id)
    count = len(alerts.get(uid, []))
    if uid in alerts:
        del alerts[uid]
        save_user_alerts(alerts)
    return count


# ------------------------------------------------------------------
# Unified Analysis Runner Helper
# ------------------------------------------------------------------
async def execute_market_analysis(symbol_name: str, exec_tf: str = "M15") -> Tuple[CompleteAnalysisResult, str, discord.Embed, SymbolSpec]:
    spec = resolve_symbol(symbol_name)
    cfg = load_strategy()
    exec_tf = normalize_tf(exec_tf)

    tf_spec = {"MN1": 60, "W1": 100, "D1": 150, "H4": 200, "H1": 200, "M15": 150}
    if exec_tf not in tf_spec:
        tf_spec[exec_tf] = 120

    dfs, meta, src = fetch_multi_tf(spec.tv_symbol, tf_spec, spec.yf_symbol)
    if not dfs or exec_tf not in dfs or len(dfs[exec_tf]) < 15:
        if "M15" in dfs:
            exec_tf = "M15"
        else:
            raise ValueError(f"Could not retrieve market data for {spec.display} ({spec.tv_symbol})")

    current_price = float(dfs[exec_tf]["close"].iloc[-1])
    digits = digits_from_meta(meta, current_price)
    point = 10 ** (-digits)

    analyzer = MTFPriceActionAnalyzer(cfg)
    result = analyzer.analyze_full_market(
        symbol=spec.name,
        display_name=spec.display,
        dfs=dfs,
        digits=digits,
        point=point,
        data_source=src,
        execution_tf=exec_tf
    )

    chart_path = clean_visualizer.generate_chart(result, dfs, tf=exec_tf)
    embed = _build_analysis_embed(result, cfg, spec)
    return result, chart_path, embed, spec


# ------------------------------------------------------------------
# Auxiliary Ephemeral Embed Builders
# ------------------------------------------------------------------
def build_htf_embed(result: CompleteAnalysisResult) -> discord.Embed:
    d = result.digits
    tfa = result.tf_analyses
    ctx = result.institutional_context

    mn = tfa.get("MN1").bias if "MN1" in tfa else "N/A"
    w1 = tfa.get("W1").bias if "W1" in tfa else "N/A"
    d1 = tfa.get("D1").bias if "D1" in tfa else "N/A"
    h4 = tfa.get("H4").bias if "H4" in tfa else "N/A"
    h1 = tfa.get("H1").bias if "H1" in tfa else "N/A"

    embed = discord.Embed(
        title=f"🧭 {result.display_name} · Macro Institutional Top-Down Matrix",
        description="Top-down rule: *'Never trade against the trend of the Daily chart. The Daily chart is your boss.'*",
        color=0x0ea5e9,
        timestamp=result.timestamp
    )

    embed.add_field(
        name="🏛️ Multi-Timeframe Alignment",
        value=(
            f"• **Monthly (Macro Direction):** `{mn}`\n"
            f"• **Weekly (Institutional Bias):** `{w1}`\n"
            f"• **Daily ('The Boss' Trend):** `{d1}`\n"
            f"• **4-Hour (Structure Setup):** `{h4}`\n"
            f"• **1-Hour (Intraday Momentum):** `{h1}`"
        ),
        inline=False
    )

    if ctx:
        embed.add_field(
            name="⚖️ Arbitration Synthesis & Conflict Resolution",
            value=(
                f"**Net Bias:** `{ctx.bias.direction}` (Weighted Score: `{ctx.bias.score:+.1f}`)\n"
                f"**Institutional Thesis:** {ctx.bias.summary}"
            ),
            inline=False
        )

        h4_obs = result.order_blocks.get("H4", [])
        if h4_obs:
            ob_text = "\n".join([f"• `{ob.ob_type}` OB: `{ob.bottom:.{d}f} – {ob.top:.{d}f}` (Displacement: {ob.displacement_atr}x ATR)" for ob in h4_obs[:3]])
            embed.add_field(name="🏰 Dominant 4H Order Blocks", value=ob_text, inline=False)

    embed.set_footer(text="Institutional Multi-Timeframe Matrix · Educational purposes only")
    return embed


def build_checklist_embed(result: CompleteAnalysisResult) -> discord.Embed:
    tp = result.trade_plan
    ctx = result.institutional_context

    embed = discord.Embed(
        title=f"📋 {result.display_name} · SMC Double-Confirmation Checklist",
        color=0x10b981 if (tp and tp.status == "TRIGGERED") else 0xf59e0b,
        timestamp=result.timestamp
    )

    if tp:
        cl_items = "\n".join([f"{'✅' if v else '⏳'} **{k}**" for k, v in tp.checklist.items()])
        embed.add_field(name="Checklist Verification", value=cl_items, inline=False)
        embed.add_field(
            name="Active Setup Parameters",
            value=(
                f"• **Direction:** `{tp.direction}`\n"
                f"• **Status:** `{tp.status}`\n"
                f"• **Zone:** `{tp.active_zone.timeframe} {tp.active_zone.zone_type}`\n"
                f"• **Rejection Candle:** `{tp.rejection_pattern.name if tp.rejection_pattern else 'Pending confirmation'}`\n"
                f"• **Mini BOS ({tp.execution_tf}):** `{'Confirmed' if tp.mini_bos else 'Pending structure shift'}`"
            ),
            inline=False
        )
    else:
        embed.add_field(
            name="⏳ Execution Checklist Status",
            value=(
                "• ⏳ **Price at key Supply/Demand zone:** Price is in open range\n"
                f"• {'✅' if result.daily_bias != 'NEUTRAL' else '⏳'} **Daily structure identified:** `{result.daily_bias}`\n"
                "• ⏳ **Rejection candlestick:** Awaiting zone arrival\n"
                "• ⏳ **Mini BOS on execution TF:** Awaiting shift\n"
                "• ⏳ **Risk-reward ratio >= 1:2:** Setup not yet formed"
            ),
            inline=False
        )

    if ctx:
        embed.add_field(
            name="🎯 Explicit Action Rules",
            value=(
                f"• **Short Trigger:** {ctx.triggers.short_if}\n"
                f"• **Long Trigger:** {ctx.triggers.long_if}\n"
                f"• **Invalidation:** {ctx.triggers.invalidation}"
            ),
            inline=False
        )

    embed.set_footer(text="Institutional Double Confirmation Protocol · Not financial advice")
    return embed


# ------------------------------------------------------------------
# Interactive UI Components (Views, Buttons, Select Menus)
# ------------------------------------------------------------------
class TimeframeSelect(discord.ui.Select):
    def __init__(self, current_tf: str = "M15", row: int = 0):
        options = [
            discord.SelectOption(label="15-Minute (Standard Execution)", value="M15", emoji="⏱️", default=(current_tf == "M15")),
            discord.SelectOption(label="5-Minute (Scalp Confirmation)", value="M5", emoji="⚡", default=(current_tf == "M5")),
            discord.SelectOption(label="1-Minute (Sniper Entry)", value="M1", emoji="🎯", default=(current_tf == "M1")),
            discord.SelectOption(label="30-Minute (Intraday)", value="M30", emoji="📊", default=(current_tf == "M30")),
            discord.SelectOption(label="1-Hour (HTF Bias)", value="H1", emoji="📈", default=(current_tf == "H1")),
        ]
        super().__init__(placeholder="Select Execution Timeframe...", min_values=1, max_values=1, options=options, row=row)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        view = self.view
        selected_tf = self.values[0]
        view.exec_tf = selected_tf
        try:
            res, cpath, embed, spec = await execute_market_analysis(view.symbol, selected_tf)
            view.last_result = res
            view.last_chart_path = cpath
            view.last_embed = embed
            for opt in self.options:
                opt.default = (opt.value == selected_tf)
            file = discord.File(cpath, filename=os.path.basename(cpath))
            embed.set_image(url=f"attachment://{os.path.basename(cpath)}")
            await interaction.edit_original_response(embed=embed, attachments=[file], view=view)
        except Exception as e:
            await interaction.followup.send(f"⚠️ Error updating timeframe: {e}", ephemeral=True)


class AnalysisInteractiveView(discord.ui.View):
    def __init__(self, symbol: str, exec_tf: str = "M15", last_result: Optional[CompleteAnalysisResult] = None,
                 last_chart_path: Optional[str] = None, last_embed: Optional[discord.Embed] = None):
        super().__init__(timeout=None)
        self.symbol = symbol
        self.exec_tf = exec_tf
        self.last_result = last_result
        self.last_chart_path = last_chart_path
        self.last_embed = last_embed
        self.add_item(TimeframeSelect(current_tf=exec_tf, row=0))

    @discord.ui.button(label="Refresh", emoji="🔄", style=discord.ButtonStyle.primary, row=1, custom_id="btn_analysis_refresh")
    async def btn_refresh(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        try:
            res, cpath, embed, spec = await execute_market_analysis(self.symbol, self.exec_tf)
            self.last_result = res
            self.last_chart_path = cpath
            self.last_embed = embed
            file = discord.File(cpath, filename=os.path.basename(cpath))
            embed.set_image(url=f"attachment://{os.path.basename(cpath)}")
            await interaction.edit_original_response(embed=embed, attachments=[file], view=self)
        except Exception as e:
            await interaction.followup.send(f"⚠️ Error refreshing: {e}", ephemeral=True)

    @discord.ui.button(label="Higher TF", emoji="🧭", style=discord.ButtonStyle.secondary, row=1, custom_id="btn_analysis_htf")
    async def btn_htf(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.last_result:
            res, _, _, _ = await execute_market_analysis(self.symbol, self.exec_tf)
            self.last_result = res
        htf_embed = build_htf_embed(self.last_result)
        await interaction.response.send_message(embed=htf_embed, ephemeral=True)

    @discord.ui.button(label="Checklist", emoji="📋", style=discord.ButtonStyle.secondary, row=1, custom_id="btn_analysis_checklist")
    async def btn_checklist(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.last_result:
            res, _, _, _ = await execute_market_analysis(self.symbol, self.exec_tf)
            self.last_result = res
        cl_embed = build_checklist_embed(self.last_result)
        await interaction.response.send_message(embed=cl_embed, ephemeral=True)

    @discord.ui.button(label="Alert Me", emoji="🔔", style=discord.ButtonStyle.success, row=1, custom_id="btn_analysis_alert")
    async def btn_alert(self, interaction: discord.Interaction, button: discord.ui.Button):
        subbed = toggle_user_alert(interaction.user.id, self.symbol, self.exec_tf, interaction.channel_id)
        if subbed:
            await interaction.response.send_message(
                f"🔔 **Alert ON for {self.symbol.upper()} (`{self.exec_tf}`)**\n"
                f"ARGUS is watching live every 60s — you'll get a short BUY/SELL + SL/TP ping here when price **enters a zone** or a **setup completes**.",
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                f"🔕 **Alert OFF for {self.symbol.upper()} (`{self.exec_tf}`).**",
                ephemeral=True
            )

    @discord.ui.button(label="Share", emoji="📤", style=discord.ButtonStyle.secondary, row=1, custom_id="btn_analysis_share")
    async def btn_share(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        try:
            if not self.last_chart_path or not os.path.exists(self.last_chart_path) or not self.last_embed:
                res, cpath, embed, spec = await execute_market_analysis(self.symbol, self.exec_tf)
                self.last_result = res
                self.last_chart_path = cpath
                self.last_embed = embed
            file = discord.File(self.last_chart_path, filename=os.path.basename(self.last_chart_path))
            embed_copy = self.last_embed.copy()
            embed_copy.set_image(url=f"attachment://{os.path.basename(self.last_chart_path)}")
            await interaction.channel.send(
                content=f"📤 Shared by {interaction.user.mention}",
                embed=embed_copy,
                file=file
            )
            await interaction.followup.send("✅ Posted to channel!", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"⚠️ Could not share: {e}", ephemeral=True)


class TerminalPairSelect(discord.ui.Select):
    def __init__(self, current_pair: str = "USDCAD"):
        pairs = [
            ("USD/CAD", "USDCAD", "🇨🇦"),
            ("Gold (XAU/USD)", "XAUUSD", "🥇"),
            ("EUR/USD", "EURUSD", "🇪🇺"),
            ("GBP/USD", "GBPUSD", "🇬🇧"),
            ("USD/JPY", "USDJPY", "🇯🇵"),
            ("USD/CHF", "USDCHF", "🇨🇭"),
            ("GBP/JPY", "GBPJPY", "🐉"),
            ("AUD/USD", "AUDUSD", "🇦🇺"),
            ("NZD/USD", "NZDUSD", "🇳🇿"),
            ("Nasdaq 100", "NAS100USD", "📈"),
            ("Dow Jones", "US30USD", "🏛️"),
        ]
        options = [
            discord.SelectOption(label=name, value=sym, emoji=em, default=(sym == current_pair))
            for name, sym, em in pairs
        ]
        super().__init__(placeholder="Switch Currency Pair or Asset...", min_values=1, max_values=1, options=options, row=0)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        view = self.view
        selected_sym = self.values[0]
        view.symbol = selected_sym
        for opt in self.options:
            opt.default = (opt.value == selected_sym)
        try:
            res, cpath, embed, spec = await execute_market_analysis(selected_sym, view.exec_tf)
            view.last_result = res
            file = discord.File(cpath, filename=os.path.basename(cpath))
            embed.set_image(url=f"attachment://{os.path.basename(cpath)}")
            await interaction.edit_original_response(embed=embed, attachments=[file], view=view)
        except Exception as e:
            await interaction.followup.send(f"⚠️ Error switching pair: {e}", ephemeral=True)


class TradingTerminalView(discord.ui.View):
    def __init__(self, symbol: str = "USDCAD", exec_tf: str = "M15", last_result: Optional[CompleteAnalysisResult] = None):
        super().__init__(timeout=None)
        self.symbol = symbol
        self.exec_tf = exec_tf
        self.last_result = last_result
        self.last_chart_path = None
        self.last_embed = None
        self.add_item(TerminalPairSelect(current_pair=symbol))
        self.add_item(TimeframeSelect(current_tf=exec_tf, row=1))

    @discord.ui.button(label="Refresh", emoji="🔄", style=discord.ButtonStyle.primary, row=2, custom_id="btn_term_refresh")
    async def btn_refresh(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        try:
            res, cpath, embed, spec = await execute_market_analysis(self.symbol, self.exec_tf)
            self.last_result = res
            file = discord.File(cpath, filename=os.path.basename(cpath))
            embed.set_image(url=f"attachment://{os.path.basename(cpath)}")
            await interaction.edit_original_response(embed=embed, attachments=[file], view=self)
        except Exception as e:
            await interaction.followup.send(f"⚠️ Error refreshing: {e}", ephemeral=True)

    @discord.ui.button(label="Higher TF", emoji="🧭", style=discord.ButtonStyle.secondary, row=2, custom_id="btn_term_htf")
    async def btn_htf(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.last_result:
            res, _, _, _ = await execute_market_analysis(self.symbol, self.exec_tf)
            self.last_result = res
        htf_embed = build_htf_embed(self.last_result)
        await interaction.response.send_message(embed=htf_embed, ephemeral=True)

    @discord.ui.button(label="Checklist", emoji="📋", style=discord.ButtonStyle.secondary, row=2, custom_id="btn_term_checklist")
    async def btn_checklist(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.last_result:
            res, _, _, _ = await execute_market_analysis(self.symbol, self.exec_tf)
            self.last_result = res
        cl_embed = build_checklist_embed(self.last_result)
        await interaction.response.send_message(embed=cl_embed, ephemeral=True)

    @discord.ui.button(label="Alert Me", emoji="🔔", style=discord.ButtonStyle.success, row=2, custom_id="btn_term_alert")
    async def btn_alert(self, interaction: discord.Interaction, button: discord.ui.Button):
        subbed = toggle_user_alert(interaction.user.id, self.symbol, self.exec_tf, interaction.channel_id)
        if subbed:
            msg = (
                f"🔔 **Alert ON for {self.symbol.upper()} (`{self.exec_tf}`)**\n"
                f"ARGUS will ping you here with a short BUY/SELL + SL/TP alert when price enters a zone or setup completes."
            )
        else:
            msg = f"🔕 **Alert OFF for {self.symbol.upper()} (`{self.exec_tf}`).**"
        await interaction.response.send_message(msg, ephemeral=True)


class RoleSelectionView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    async def _toggle_role(self, interaction: discord.Interaction, role_name: str, emoji_str: str):
        guild = interaction.guild
        if not guild:
            await interaction.response.send_message("Roles can only be assigned within a Discord server.", ephemeral=True)
            return

        member = interaction.user
        role = discord.utils.get(guild.roles, name=role_name)

        if not role and guild.me.guild_permissions.manage_roles:
            try:
                role = await guild.create_role(name=role_name, reason="ARGUS Role Panel auto-setup")
            except Exception:
                pass

        if not role:
            await interaction.response.send_message(f"⚠️ The `{role_name}` role is not configured yet. Ask an admin to create it!", ephemeral=True)
            return

        if role in member.roles:
            await member.remove_roles(role)
            await interaction.response.send_message(f"❌ Removed **{role_name}** role.", ephemeral=True)
        else:
            await member.add_roles(role)
            await interaction.response.send_message(f"✅ Added {emoji_str} **{role_name}** role!", ephemeral=True)

    @discord.ui.button(label="Gold Trader", emoji="🥇", style=discord.ButtonStyle.primary, custom_id="role_gold_btn")
    async def btn_gold(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._toggle_role(interaction, "Gold Trader", "🥇")

    @discord.ui.button(label="FX Majors", emoji="💱", style=discord.ButtonStyle.primary, custom_id="role_majors_btn")
    async def btn_majors(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._toggle_role(interaction, "FX Majors", "💱")

    @discord.ui.button(label="Scalper (1m/5m)", emoji="⚡", style=discord.ButtonStyle.secondary, custom_id="role_scalper_btn")
    async def btn_scalper(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._toggle_role(interaction, "Scalper", "⚡")

    @discord.ui.button(label="Trade Alert Pings", emoji="🔔", style=discord.ButtonStyle.success, custom_id="role_alerts_btn")
    async def btn_alerts(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._toggle_role(interaction, "Trade Alerts", "🔔")


# ------------------------------------------------------------------
# App-Like Experience: Modals & Persistent App Home Screen
# ------------------------------------------------------------------
class AnalyzePairModal(discord.ui.Modal, title="🔍 Institutional Chart Analyzer"):
    pair_input = discord.ui.TextInput(
        label="Pair or Asset",
        placeholder="e.g. usdcad, gold, eurusd, gbpaud, nas100",
        required=True,
        max_length=30
    )
    tf_input = discord.ui.TextInput(
        label="Execution Timeframe",
        placeholder="15m (default), 5m, 1m, 30m, 1h",
        default="15m",
        required=False,
        max_length=10
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True, ephemeral=True)
        try:
            raw_pair = self.pair_input.value.strip()
            raw_tf = self.tf_input.value.strip() or "15m"
            sym_clean, parsed_tf = parse_symbol_and_tf(raw_pair, default_tf=raw_tf)
            res, chart_path, embed, spec = await execute_market_analysis(sym_clean, parsed_tf)
            view = AnalysisInteractiveView(
                symbol=spec.name,
                exec_tf=res.execution_tf,
                last_result=res,
                last_chart_path=chart_path,
                last_embed=embed
            )
            file = discord.File(chart_path, filename=os.path.basename(chart_path))
            embed.set_image(url=f"attachment://{os.path.basename(chart_path)}")
            await interaction.followup.send(embed=embed, file=file, view=view, ephemeral=True)
        except Exception as e:
            logger.error(f"Error in AnalyzePairModal: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Could not analyze `{self.pair_input.value}`: {e}", ephemeral=True)


class StrategyEditModal(discord.ui.Modal, title="⚙️ Update Risk & Strategy Rules"):
    risk_pct = discord.ui.TextInput(
        label="Risk Per Trade (%)",
        placeholder="e.g. 1.0 or 1.5",
        required=True,
        max_length=8
    )
    min_rr = discord.ui.TextInput(
        label="Minimum Risk-Reward Ratio",
        placeholder="e.g. 2.0 or 2.5",
        required=True,
        max_length=8
    )
    account_balance = discord.ui.TextInput(
        label="Account Balance ($)",
        placeholder="e.g. 1000 or 10000",
        required=True,
        max_length=15
    )

    def __init__(self, cfg: dict):
        super().__init__()
        self.risk_pct.default = str(cfg.get("risk_pct", 1.0))
        self.min_rr.default = str(cfg.get("min_rr", 2.0))
        self.account_balance.default = str(cfg.get("account_balance", 1000.0))

    async def on_submit(self, interaction: discord.Interaction):
        try:
            set_value("risk_pct", self.risk_pct.value.strip())
            set_value("min_rr", self.min_rr.value.strip())
            set_value("account_balance", self.account_balance.value.strip())
            await interaction.response.send_message(
                f"✅ **Strategy Updated!**\n"
                f"• Risk per trade: `{self.risk_pct.value.strip()}%`\n"
                f"• Min R:R: `1:{self.min_rr.value.strip()}`\n"
                f"• Account Balance: `${self.account_balance.value.strip()}`",
                ephemeral=True
            )
        except Exception as e:
            await interaction.response.send_message(f"❌ Invalid value: {e}", ephemeral=True)


class StrategyPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=180)

    @discord.ui.button(label="Edit Risk & Rules", emoji="✏️", style=discord.ButtonStyle.primary)
    async def btn_edit_rules(self, interaction: discord.Interaction, button: discord.ui.Button):
        cfg = load_strategy()
        await interaction.response.send_modal(StrategyEditModal(cfg))


class AppQuickPairSelect(discord.ui.Select):
    def __init__(self):
        pairs = [
            ("Gold (XAU/USD)", "XAUUSD", "🥇", "Instant 15m SMC analysis & chart"),
            ("USD/CAD", "USDCAD", "🇨🇦", "Instant 15m SMC analysis & chart"),
            ("EUR/USD", "EURUSD", "🇪🇺", "Instant 15m SMC analysis & chart"),
            ("GBP/USD", "GBPUSD", "🇬🇧", "Instant 15m SMC analysis & chart"),
            ("USD/JPY", "USDJPY", "🇯🇵", "Instant 15m SMC analysis & chart"),
            ("USD/CHF", "USDCHF", "🇨🇭", "Instant 15m SMC analysis & chart"),
            ("GBP/JPY", "GBPJPY", "🐉", "Instant 15m SMC analysis & chart"),
            ("AUD/USD", "AUDUSD", "🇦🇺", "Instant 15m SMC analysis & chart"),
            ("Nasdaq 100", "NAS100USD", "📈", "Instant 15m SMC analysis & chart"),
            ("Dow Jones (US30)", "US30USD", "🏛️", "Instant 15m SMC analysis & chart"),
        ]
        options = [
            discord.SelectOption(label=name, value=sym, emoji=em, description=desc)
            for name, sym, em, desc in pairs
        ]
        super().__init__(
            placeholder="⚡ Quick Launch — Pick a pair to analyze instantly...",
            min_values=1,
            max_values=1,
            options=options,
            row=0,
            custom_id="app_quick_pair_select"
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True, ephemeral=True)
        selected_sym = self.values[0]
        try:
            res, chart_path, embed, spec = await execute_market_analysis(selected_sym, "M15")
            view = AnalysisInteractiveView(
                symbol=spec.name,
                exec_tf=res.execution_tf,
                last_result=res,
                last_chart_path=chart_path,
                last_embed=embed
            )
            file = discord.File(chart_path, filename=os.path.basename(chart_path))
            embed.set_image(url=f"attachment://{os.path.basename(chart_path)}")
            await interaction.followup.send(embed=embed, file=file, view=view, ephemeral=True)
        except Exception as e:
            logger.error(f"Error in AppQuickPairSelect: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Error analyzing `{selected_sym}`: {e}", ephemeral=True)


class AppLauncherView(discord.ui.View):
    """Persistent App Home Screen — users never need to type slash commands."""
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(AppQuickPairSelect())

    @discord.ui.button(label="Analyze Any Pair", emoji="🔍", style=discord.ButtonStyle.primary, row=1, custom_id="app_btn_custom_analyze")
    async def btn_custom_analyze(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(AnalyzePairModal())

    @discord.ui.button(label="Scan Watchlist", emoji="⚡", style=discord.ButtonStyle.success, row=1, custom_id="app_btn_scan")
    async def btn_scan(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(thinking=True, ephemeral=True)
        try:
            cfg = load_strategy()
            watchlist = cfg.get("watchlist", ["XAUUSD", "EURUSD", "GBPUSD", "USDJPY", "USDCHF"])
            tf_spec = {"MN1": 60, "W1": 150, "D1": 250, "H4": 300, "H1": 300, "M15": 250}

            setups_found = []
            status_lines = []

            for item in watchlist:
                spec = resolve_symbol(item)
                dfs, meta, src = fetch_multi_tf(spec.tv_symbol, tf_spec, spec.yf_symbol)
                if not dfs or "M15" not in dfs:
                    continue

                current_price = float(dfs["M15"]["close"].iloc[-1])
                digits = digits_from_meta(meta, current_price)
                point = 10 ** (-digits)

                analyzer = MTFPriceActionAnalyzer(cfg)
                result = analyzer.analyze_full_market(spec.name, spec.display, dfs, digits, point, src)

                status_icon = "🟢" if result.daily_bias == "BULLISH" else ("🔴" if result.daily_bias == "BEARISH" else "⚪")
                m15_bias = result.tf_analyses.get("M15", result.tf_analyses.get("H1")).bias
                status_lines.append(f"{status_icon} **{spec.display}**: Daily `{result.daily_bias}` | 15m `{m15_bias}`")

                if result.trade_plan:
                    chart_path = clean_visualizer.generate_chart(result, dfs, tf="M15")
                    setups_found.append((result, chart_path, spec))

            embed = discord.Embed(
                title="🔍 Institutional Watchlist Scan",
                description="\n".join(status_lines) if status_lines else "No market data returned.",
                color=0x38bdf8,
                timestamp=datetime.now(timezone.utc)
            )
            if not setups_found:
                embed.add_field(
                    name="Setup Status",
                    value="No active 15m setups currently armed/triggered. Pick any pair from the App Launcher to inspect triggers.",
                    inline=False
                )
                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                embed.add_field(
                    name="⚡ Active Setups Found",
                    value=f"Found **{len(setups_found)}** active setup(s)!",
                    inline=False
                )
                await interaction.followup.send(embed=embed, ephemeral=True)
                for res, cpath, spec in setups_found[:3]:
                    plan_embed = _build_analysis_embed(res, cfg, spec)
                    file = discord.File(cpath, filename=os.path.basename(cpath))
                    plan_embed.set_image(url=f"attachment://{os.path.basename(cpath)}")
                    view = AnalysisInteractiveView(symbol=spec.name, exec_tf="M15", last_result=res, last_chart_path=cpath, last_embed=plan_embed)
                    await interaction.followup.send(embed=plan_embed, file=file, view=view, ephemeral=True)
        except Exception as e:
            logger.error(f"Error in app_btn_scan: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Scan failed: {e}", ephemeral=True)

    @discord.ui.button(label="Macro Bias", emoji="🧭", style=discord.ButtonStyle.secondary, row=1, custom_id="app_btn_bias")
    async def btn_bias(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(thinking=True, ephemeral=True)
        try:
            cfg = load_strategy()
            watchlist = cfg.get("watchlist", ["XAUUSD", "EURUSD", "GBPUSD", "USDJPY", "USDCHF"])
            tf_spec = {"MN1": 50, "W1": 100, "D1": 200, "H4": 200}

            embed = discord.Embed(
                title="🧭 Multi-Timeframe Structural Bias Board",
                description="Rule: *'Never trade against the trend of the Daily chart. The Daily chart is your boss.'*",
                color=0x06b6d4,
                timestamp=datetime.now(timezone.utc)
            )
            for item in watchlist:
                spec = resolve_symbol(item)
                dfs, meta, src = fetch_multi_tf(spec.tv_symbol, tf_spec, spec.yf_symbol)
                if not dfs or "D1" not in dfs:
                    continue
                analyzer = MTFPriceActionAnalyzer(cfg)
                res = analyzer.analyze_full_market(spec.name, spec.display, dfs, 3, 0.001, src)
                mn = res.tf_analyses.get("MN1").bias if "MN1" in res.tf_analyses else "N/A"
                w1 = res.tf_analyses.get("W1").bias if "W1" in res.tf_analyses else "N/A"
                d1 = res.tf_analyses.get("D1").bias if "D1" in res.tf_analyses else "N/A"
                h4 = res.tf_analyses.get("H4").bias if "H4" in res.tf_analyses else "N/A"
                embed.add_field(
                    name=f"{spec.display}",
                    value=f"• MN: `{mn}` | W1: `{w1}`\n• **D1:** `{d1}` | H4: `{h4}`",
                    inline=True
                )
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:
            logger.error(f"Error in app_btn_bias: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Bias board failed: {e}", ephemeral=True)

    @discord.ui.button(label="Strategy & Risk", emoji="⚙️", style=discord.ButtonStyle.secondary, row=1, custom_id="app_btn_strategy")
    async def btn_strategy(self, interaction: discord.Interaction, button: discord.ui.Button):
        cfg = load_strategy()
        embed = discord.Embed(
            title=f"⚙️ Strategy Rules: {cfg.get('name')}",
            color=0x10b981
        )
        embed.add_field(
            name="Hierarchy",
            value=f"• Bias (Boss): `{cfg.get('bias_tf')}`\n• Setup Zones: `{cfg.get('setup_tf')}`\n• Execution: `{cfg.get('exec_tf')}`",
            inline=True
        )
        embed.add_field(
            name="Risk Rules",
            value=(
                f"• Min R:R: `1:{cfg.get('min_rr')}`\n"
                f"• Risk %: `{cfg.get('risk_pct')}%`\n"
                f"• Account: `${cfg.get('account_balance', 1000)}`\n"
                f"• Lock BE: `{cfg.get('breakeven_rr')} R:R`"
            ),
            inline=True
        )
        embed.set_footer(text="Click 'Edit Risk & Rules' below to adjust without typing commands")
        await interaction.response.send_message(embed=embed, view=StrategyPanelView(), ephemeral=True)

    @discord.ui.button(label="My Alerts", emoji="🔔", style=discord.ButtonStyle.secondary, row=1, custom_id="app_btn_my_alerts")
    async def btn_my_alerts(self, interaction: discord.Interaction, button: discord.ui.Button):
        alerts = load_user_alerts()
        user_subs = alerts.get(str(interaction.user.id), [])
        if not user_subs:
            await interaction.response.send_message(
                "🔕 You have no active alerts yet.\nPick any pair and click **`🔔 Alert Me`** to get live BUY/SELL + SL/TP pings!",
                ephemeral=True
            )
            return
        lines = [f"• **{s['symbol']}** (`{s['tf']}`)" for s in user_subs]
        embed = discord.Embed(
            title="🔔 Your Active ARGUS Alerts",
            description="\n".join(lines),
            color=0x2962ff
        )
        embed.set_footer(text="ARGUS checks live price every 60s for zone entry & completed setups")
        await interaction.response.send_message(embed=embed, view=MyAlertsView(), ephemeral=True)


class MyAlertsView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=120)

    @discord.ui.button(label="Clear All Alerts", emoji="🗑️", style=discord.ButtonStyle.danger)
    async def btn_clear(self, interaction: discord.Interaction, button: discord.ui.Button):
        removed = clear_user_alerts(interaction.user.id)
        await interaction.response.edit_message(
            content=f"🗑️ Cleared **{removed}** active alert(s).",
            embed=None,
            view=None
        )


# ------------------------------------------------------------------
# Live Background Alert Monitor (Checks every 60s)
# ------------------------------------------------------------------
def _scan_single_alert_target(symbol_name: str, exec_tf: str) -> Optional[CompleteAnalysisResult]:
    spec = resolve_symbol(symbol_name)
    cfg = load_strategy()
    tf_norm = normalize_tf(exec_tf)
    tf_spec = {"MN1": 40, "W1": 60, "D1": 120, "H4": 150, "H1": 150, "M15": 120}
    if tf_norm not in tf_spec:
        tf_spec[tf_norm] = 120

    dfs, meta, src = fetch_multi_tf(spec.tv_symbol, tf_spec, spec.yf_symbol)
    if not dfs or tf_norm not in dfs or len(dfs[tf_norm]) < 15:
        if "M15" in dfs:
            tf_norm = "M15"
        else:
            return None

    current_price = float(dfs[tf_norm]["close"].iloc[-1])
    digits = digits_from_meta(meta, current_price)
    point = 10 ** (-digits)

    analyzer = MTFPriceActionAnalyzer(cfg)
    return analyzer.analyze_full_market(
        symbol=spec.name,
        display_name=spec.display,
        dfs=dfs,
        digits=digits,
        point=point,
        data_source=src,
        execution_tf=tf_norm
    )


@tasks.loop(seconds=60)
async def alert_monitor_loop():
    alerts = load_user_alerts()
    if not alerts:
        return

    # Deduplicate (symbol, tf) targets across all subscribed users
    targets: Dict[Tuple[str, str], List[Tuple[str, dict]]] = {}
    for uid, subs in alerts.items():
        for sub in subs:
            key = (sub["symbol"], sub["tf"])
            targets.setdefault(key, []).append((uid, sub))

    modified = False
    now_ts = time.time()

    for (sym, tf), subscribers in targets.items():
        try:
            res = await asyncio.to_thread(_scan_single_alert_target, sym, tf)
            if res is None:
                continue

            tp = res.trade_plan
            d = res.digits

            if tp and tp.status in ("ARMED_IN_ZONE", "TRIGGERED"):
                state_key = f"{tp.status}_{tp.direction}"
                dir_emoji = "🟢" if tp.direction == "BUY" else "🔴"
                stage_label = "SETUP COMPLETE ⚡" if tp.status == "TRIGGERED" else "ENTERED ZONE 🚨"

                for uid, sub in subscribers:
                    last_state = sub.get("last_status")
                    last_ts = sub.get("last_alert_ts", 0)
                    # Fire if new state (e.g. entered zone or upgraded to setup complete) OR 45m cooldown elapsed
                    if last_state != state_key or (now_ts - last_ts) >= 2700:
                        short_msg = (
                            f"🔔 <@{uid}> **ARGUS · {stage_label}**\n"
                            f"{dir_emoji} **{tp.direction} {res.display_name}** (`{tp.execution_tf}`)\n"
                            f"Entry: `{tp.entry_price:.{d}f}` | SL: `{tp.stop_loss:.{d}f}` | TP: `{tp.take_profit_1:.{d}f}` (TP2: `{tp.take_profit_2:.{d}f}`)"
                        )
                        sent = False
                        ch_id = sub.get("channel_id")
                        if ch_id:
                            channel = bot.get_channel(ch_id)
                            if channel is None:
                                try:
                                    channel = await bot.fetch_channel(ch_id)
                                except Exception:
                                    channel = None
                            if channel:
                                try:
                                    await channel.send(short_msg)
                                    sent = True
                                except Exception as ce:
                                    logger.warning(f"Failed sending alert to channel {ch_id}: {ce}")
                        if not sent:
                            try:
                                user = bot.get_user(int(uid)) or await bot.fetch_user(int(uid))
                                if user:
                                    await user.send(short_msg)
                                    sent = True
                            except Exception as de:
                                logger.warning(f"Failed DMing alert to user {uid}: {de}")

                        if sent:
                            sub["last_status"] = state_key
                            sub["last_alert_ts"] = now_ts
                            modified = True
            else:
                # Price left the zone -> reset state so next entry alerts cleanly
                for uid, sub in subscribers:
                    if sub.get("last_status") is not None:
                        sub["last_status"] = None
                        modified = True
        except Exception as e:
            logger.error(f"[AlertMonitor] Error checking {sym} ({tf}): {e}")

    if modified:
        save_user_alerts(alerts)


@alert_monitor_loop.before_loop
async def before_alert_monitor():
    await bot.wait_until_ready()


# ------------------------------------------------------------------
# Bot Lifecycle & Events
# ------------------------------------------------------------------
@bot.event
async def on_ready():
    logger.info(f"ARGUS Institutional Bot is ONLINE logged in as {bot.user} (ID: {bot.user.id})")
    # Register persistent views so pinned panels work forever across restarts
    bot.add_view(RoleSelectionView())
    bot.add_view(AppLauncherView())
    if not alert_monitor_loop.is_running():
        alert_monitor_loop.start()
        logger.info("ARGUS Live Alert Monitor started (60s interval).")
    try:
        synced = await bot.tree.sync()
        logger.info(f"Synced {len(synced)} global application slash commands!")
    except Exception as e:
        logger.warning(f"Failed to sync slash commands: {e}")

    await bot.change_presence(activity=discord.Activity(
        type=discord.ActivityType.watching, name="ARGUS Terminal | SMC/ICT"
    ))


@tree.command(name="setup_app", description="Deploy the permanent no-command ARGUS Trading App Home Screen in this channel (Admin)")
@discord.app_commands.default_permissions(administrator=True)
async def slash_setup_app(interaction: discord.Interaction):
    embed = discord.Embed(
        title="⚡ ARGUS INSTITUTIONAL TRADING APP",
        description=(
            "Welcome to **ARGUS** — your interactive institutional trading desk. **No slash commands needed!**\n\n"
            "• **Quick Launch Menu:** Pick any major pair or index below for an instant chart.\n"
            "• **🔍 Analyze Any Pair:** Click to open a pop-up box where you just type the pair & timeframe (e.g. `usdcad` + `5m`).\n"
            "• **⚡ Scan Watchlist / 🧭 Macro Bias:** 1-click institutional market overview.\n"
            "• **Private & Clean:** Your charts open privately for you so this screen stays clean — click **📤 Share** on any chart to post it to the channel!"
        ),
        color=0x2962ff
    )
    embed.set_footer(text="ARGUS · Educational analysis only · Not financial advice · Active 24/7")
    await interaction.channel.send(embed=embed, view=AppLauncherView())
    await interaction.response.send_message(
        "✅ **Trading App deployed!** Tip: Lock `Send Messages` for regular members in this channel so the App stays permanently pinned at the bottom.",
        ephemeral=True
    )


async def common_symbol_autocomplete(interaction: discord.Interaction, current: str) -> List[discord.app_commands.Choice[str]]:
    common = [
        ("USD/CAD", "USDCAD"),
        ("Gold (XAU/USD)", "XAUUSD"),
        ("EUR/USD", "EURUSD"),
        ("GBP/USD", "GBPUSD"),
        ("USD/JPY", "USDJPY"),
        ("USD/CHF", "USDCHF"),
        ("GBP/JPY", "GBPJPY"),
        ("AUD/USD", "AUDUSD"),
        ("NZD/USD", "NZDUSD"),
        ("Silver (XAG/USD)", "XAGUSD"),
        ("Nasdaq 100", "NAS100USD"),
        ("Dow Jones (US30)", "US30USD"),
        ("S&P 500", "SPX500USD"),
    ]
    curr = current.lower().replace("/", "").replace(" ", "")
    choices = []
    for disp, sym in common:
        if curr in disp.lower().replace("/", "").replace(" ", "") or curr in sym.lower():
            choices.append(discord.app_commands.Choice(name=disp, value=sym))
    return choices[:25]


@tree.command(name="analyze", description="Full top-down SMC institutional analysis + clean chart")
@discord.app_commands.describe(
    symbol="The pair or asset to analyze (e.g. usdcad, gold, eurusd, usdjpy, gbpusd)",
    timeframe="Execution timeframe: 15m (default), 5m (scalp), 1m (sniper), 30m, 1h"
)
@discord.app_commands.choices(timeframe=[
    discord.app_commands.Choice(name="15-Minute (Default Standard)", value="15m"),
    discord.app_commands.Choice(name="5-Minute (Scalping / Quick Confirmation)", value="5m"),
    discord.app_commands.Choice(name="1-Minute (Sniper / Precision Micro Entry)", value="1m"),
    discord.app_commands.Choice(name="30-Minute (Intraday)", value="30m"),
    discord.app_commands.Choice(name="1-Hour (HTF Swing)", value="1h")
])
@discord.app_commands.autocomplete(symbol=common_symbol_autocomplete)
async def slash_analyze(interaction: discord.Interaction, symbol: str = "gold", timeframe: str = "15m"):
    await interaction.response.defer(thinking=True)
    try:
        sym_clean, parsed_tf = parse_symbol_and_tf(symbol, default_tf=timeframe)
        res, chart_path, embed, spec = await execute_market_analysis(sym_clean, parsed_tf)
        view = AnalysisInteractiveView(symbol=spec.name, exec_tf=res.execution_tf, last_result=res)
        file = discord.File(chart_path, filename=os.path.basename(chart_path))
        embed.set_image(url=f"attachment://{os.path.basename(chart_path)}")
        await interaction.followup.send(embed=embed, file=file, view=view)
    except Exception as e:
        logger.error(f"Error in /analyze: {e}", exc_info=True)
        await interaction.followup.send(f"❌ Error during analysis: {e}")


@tree.command(name="terminal", description="Interactive Institutional Trading Terminal with pair & timeframe dropdowns")
@discord.app_commands.describe(pair="Initial pair to load in the terminal")
@discord.app_commands.autocomplete(pair=common_symbol_autocomplete)
async def slash_terminal(interaction: discord.Interaction, pair: str = "USDCAD"):
    await interaction.response.defer(thinking=True)
    try:
        res, chart_path, embed, spec = await execute_market_analysis(pair, "M15")
        view = TradingTerminalView(symbol=spec.name, exec_tf="M15", last_result=res)
        file = discord.File(chart_path, filename=os.path.basename(chart_path))
        embed.set_image(url=f"attachment://{os.path.basename(chart_path)}")
        await interaction.followup.send(embed=embed, file=file, view=view)
    except Exception as e:
        logger.error(f"Error in /terminal: {e}", exc_info=True)
        await interaction.followup.send(f"❌ Error launching terminal: {e}")


@tree.command(name="roles_panel", description="Post the interactive role assignment panel in the current channel (Admin)")
@discord.app_commands.default_permissions(administrator=True)
async def slash_roles_panel(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🛡️ Select Your Trading Roles & Notification Pings",
        description=(
            "Click the buttons below to customize your channels, setup alerts, and experience.\n"
            "Click again at any time to remove a role.\n\n"
            "• 🥇 **Gold Trader:** Access Gold analysis and XAU/USD discussions\n"
            "• 💱 **FX Majors:** Forex major pairs (EURUSD, USDCAD, GBPUSD, USDJPY)\n"
            "• ⚡ **Scalper (1m/5m):** High-frequency sniper executions and micro-BOS shifts\n"
            "• 🔔 **Trade Alert Pings:** Get pinged immediately when double-confirmed setups trigger"
        ),
        color=0x38bdf8
    )
    embed.set_footer(text="ARGUS Institutional Trading Hub · Click buttons to toggle")
    await interaction.channel.send(embed=embed, view=RoleSelectionView())
    await interaction.response.send_message("✅ Role panel posted successfully!", ephemeral=True)


@tree.command(name="help", description="Guide on how to navigate the server and use ARGUS")
async def slash_help(interaction: discord.Interaction):
    embed = discord.Embed(
        title="⚡ ARGUS Institutional Trading System Guide",
        description=(
            "Welcome to **ARGUS**! Here is how to navigate and make the most of the bot:\n\n"
            "### 🚀 App Mode & Slash Commands\n"
            "• `/setup_app` — Deploy the permanent **no-command Trading App** (dropdowns + pop-up forms).\n"
            "• `/terminal` — Launch an interactive Trading Terminal with 1-click pair & timeframe switching.\n"
            "• `/analyze [symbol] [timeframe]` — Full institutional analysis with dynamic chart visualizer.\n"
            "• `/scan` — Scan all watchlist pairs for active 15m double-confirmation SMC setups.\n"
            "• `/bias` — Macro structural hierarchy matrix (Monthly ➔ Weekly ➔ Daily ➔ 4H).\n"
            "• `/strategy` — View risk-reward rules, breakeven thresholds, and lot sizing logic.\n\n"
            "### 🎮 Interactive Buttons & Dropdowns\n"
            "• **Timeframe Menu:** Switch between 15m (Standard), 5m (Scalp), 1m (Sniper), 1h (HTF) without typing!\n"
            "• **🔄 Refresh:** Fetch live candles and re-render the chart dynamically in-place.\n"
            "• **🧭 Higher TF:** Instant ephemeral popup of macro structure without cluttering chat.\n"
            "• **📋 Checklist:** View the 8-point SMC institutional entry checklist.\n"
            "• **🔔 Alert Me:** Subscribe to instant setup ping notifications for that symbol.\n\n"
            "### 🛡️ Safety & Rules\n"
            "• All signals and analysis are strictly educational, not financial advice.\n"
            "• **ARGUS** will **NEVER** DM you first asking for crypto, money, or account logins."
        ),
        color=0x06b6d4
    )
    embed.set_footer(text="ARGUS Institutional Engine")
    await interaction.response.send_message(embed=embed, ephemeral=True)

@tree.command(name="scan", description="Scan all watchlist pairs for 15m double-confirmation SMC setups")
async def slash_scan(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True)
    try:
        cfg = load_strategy()
        watchlist = cfg.get("watchlist", ["XAUUSD", "EURUSD", "GBPUSD", "USDJPY", "USDCHF"])
        tf_spec = {"MN1": 60, "W1": 150, "D1": 250, "H4": 300, "H1": 300, "M15": 250}

        setups_found = []
        status_lines = []

        for item in watchlist:
            spec = resolve_symbol(item)
            dfs, meta, src = fetch_multi_tf(spec.tv_symbol, tf_spec, spec.yf_symbol)
            if not dfs or "M15" not in dfs:
                continue

            current_price = float(dfs["M15"]["close"].iloc[-1])
            digits = digits_from_meta(meta, current_price)
            point = 10 ** (-digits)

            analyzer = MTFPriceActionAnalyzer(cfg)
            result = analyzer.analyze_full_market(spec.name, spec.display, dfs, digits, point, src)

            status_icon = "🟢" if result.daily_bias == "BULLISH" else ("🔴" if result.daily_bias == "BEARISH" else "⚪")
            status_lines.append(f"{status_icon} **{spec.display}**: Daily `{result.daily_bias}` | 15m `{result.tf_analyses.get('M15', result.tf_analyses.get('H1')).bias}`")

            if result.trade_plan:
                chart_path = clean_visualizer.generate_chart(result, dfs, tf="M15")
                setups_found.append((result, chart_path))

        embed = discord.Embed(
            title="🔍 Institutional Watchlist Scan",
            description="\n".join(status_lines),
            color=0x38bdf8,
            timestamp=datetime.now(timezone.utc)
        )

        if not setups_found:
            embed.add_field(
                name="Setup Status",
                value="No active 15m double-confirmation setups currently triggered. Price is between zones or awaiting 15m mini BOS.",
                inline=False
            )
            await interaction.followup.send(embed=embed)
        else:
            embed.add_field(
                name="⚡ Triggered Setups",
                value=f"Found **{len(setups_found)}** active setup(s) meeting all strict checklist rules!",
                inline=False
            )
            await interaction.followup.send(embed=embed)
            for res, cpath in setups_found:
                plan_embed = _build_analysis_embed(res, cfg)
                file = discord.File(cpath, filename=os.path.basename(cpath))
                plan_embed.set_image(url=f"attachment://{os.path.basename(cpath)}")
                await interaction.followup.send(embed=plan_embed, file=file)
    except Exception as e:
        logger.error(f"Error in /scan: {e}", exc_info=True)
        await interaction.followup.send(f"❌ Error during scan: {e}")

@tree.command(name="bias", description="Quick overview of Monthly, Weekly, Daily, and 4H bias across watchlist")
async def slash_bias(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True)
    try:
        cfg = load_strategy()
        watchlist = cfg.get("watchlist", ["XAUUSD", "EURUSD", "GBPUSD", "USDJPY", "USDCHF"])
        tf_spec = {"MN1": 50, "W1": 100, "D1": 200, "H4": 200}

        embed = discord.Embed(
            title="🧭 Multi-Timeframe Structural Bias Overview",
            description="Rule: *'Never trade against the trend of the Daily chart. The Daily chart is your boss.'*",
            color=0x06b6d4,
            timestamp=datetime.now(timezone.utc)
        )

        for item in watchlist:
            spec = resolve_symbol(item)
            dfs, meta, src = fetch_multi_tf(spec.tv_symbol, tf_spec, spec.yf_symbol)
            if not dfs or "D1" not in dfs:
                continue

            analyzer = MTFPriceActionAnalyzer(cfg)
            res = analyzer.analyze_full_market(spec.name, spec.display, dfs, 3, 0.001, src)

            mn = res.tf_analyses.get("MN1").bias if "MN1" in res.tf_analyses else "N/A"
            w1 = res.tf_analyses.get("W1").bias if "W1" in res.tf_analyses else "N/A"
            d1 = res.tf_analyses.get("D1").bias if "D1" in res.tf_analyses else "N/A"
            h4 = res.tf_analyses.get("H4").bias if "H4" in res.tf_analyses else "N/A"

            embed.add_field(
                name=f"{spec.display} ({spec.name})",
                value=f"• Monthly: `{mn}`\n• Weekly: `{w1}`\n• **Daily (Boss):** `{d1}`\n• 4-Hour: `{h4}`",
                inline=True
            )

        await interaction.followup.send(embed=embed)
    except Exception as e:
        logger.error(f"Error in /bias: {e}", exc_info=True)
        await interaction.followup.send(f"❌ Error during bias check: {e}")

@tree.command(name="strategy", description="Display current institutional strategy rules & risk parameters")
async def slash_strategy(interaction: discord.Interaction):
    cfg = load_strategy()
    embed = discord.Embed(
        title=f"📋 Strategy Rules: {cfg.get('name')}",
        color=0x10b981
    )
    embed.add_field(name="Hierarchy", value=f"• Bias (Boss): `{cfg.get('bias_tf')}`\n• Setup Zones: `{cfg.get('setup_tf')}`\n• Execution: `{cfg.get('exec_tf')}`", inline=True)
    embed.add_field(name="Risk Rules", value=f"• Min R:R: `1:{cfg.get('min_rr')}`\n• Lock BE: `{cfg.get('breakeven_rr')} R:R`\n• Partial TP: `{cfg.get('partial_tp_pct')}% at {cfg.get('partial_tp_rr')} R:R`\n• Risk %: `{cfg.get('risk_pct')}%`", inline=True)
    embed.add_field(name="Zone & Confirmation", value=f"• Max Zone Touches: `{cfg.get('max_zone_touches')}`\n• Rejection Candle: `{cfg.get('require_rejection_candle')}`\n• 15m Mini BOS: `{cfg.get('require_mini_bos')}`", inline=True)
    embed.set_footer(text="Modify any rule using !set <key> <val>")
    await interaction.response.send_message(embed=embed)

@bot.command(name="help")
async def cmd_help(ctx):
    embed = discord.Embed(
        title="🎯 Institutional Forex Analysis Bot Commands",
        description="Top-down multi-timeframe analysis (MN1 ➔ W1 ➔ D1 ➔ H4 ➔ H1 ➔ M15) based on your trading strategy.",
        color=0x38bdf8
    )
    embed.add_field(
        name="📊 Analysis Commands",
        value=(
            "`!analyze <symbol>` — Full top-down breakdown + dual-pane chart (e.g. `!analyze gold`, `!analyze eurusd`)\n"
            "`!scan` — Scan watchlist (`XAUUSD, EURUSD, GBPUSD, USDJPY, USDCHF`) for active setups\n"
            "`!bias` — Quick macro bias overview (Monthly, Weekly, Daily, 4H)"
        ),
        inline=False
    )
    embed.add_field(
        name="⚙️ Strategy & Customization",
        value=(
            "`!strategy` — View all current strategy rules and risk parameters\n"
            "`!set <key> <val>` — Modify rules dynamically (e.g. `!set min_rr 2.5`, `!set risk_pct 1.5`)\n"
            "`!note <text>` — Add your personal trade/market notes\n"
            "`!notes` — List saved notes"
        ),
        inline=False
    )
    embed.set_footer(text="Forex Strategy Guide | Pure Price Action & Market Structure")
    await ctx.send(embed=embed)

@bot.command(name="analyze", aliases=["a", "chart"])
async def cmd_analyze(ctx, *, query: str = "gold"):
    """Performs full top-down analysis on requested instrument and execution timeframe (e.g. '!analyze usdcad 5m')."""
    async with ctx.typing():
        sym_clean, parsed_tf = parse_symbol_and_tf(query)
        spec: SymbolSpec = resolve_symbol(sym_clean)
        cfg = load_strategy()

        exec_tf = normalize_tf(parsed_tf)
        logger.info(f"Analyzing {spec.display} ({spec.tv_symbol}) on execution TF {exec_tf} for user {ctx.author}...")

        # 1. Fetch multi-timeframe history
        tf_spec = {"MN1": 60, "W1": 100, "D1": 150, "H4": 200, "H1": 200, "M15": 150}
        if exec_tf not in tf_spec:
            tf_spec[exec_tf] = 120

        dfs, meta, src = fetch_multi_tf(spec.tv_symbol, tf_spec, spec.yf_symbol)

        if not dfs or exec_tf not in dfs or len(dfs[exec_tf]) < 15:
            if "M15" in dfs:
                exec_tf = "M15"
            else:
                await ctx.send(f"⚠️ Could not retrieve sufficient market data for **{spec.display}** (`{spec.tv_symbol}`). Please check symbol name.")
                return

        current_price = float(dfs[exec_tf]["close"].iloc[-1])
        digits = digits_from_meta(meta, current_price)
        point = 10 ** (-digits)

        # 2. Run institutional analyzer
        analyzer = MTFPriceActionAnalyzer(cfg)
        result: CompleteAnalysisResult = analyzer.analyze_full_market(
            symbol=spec.name,
            display_name=spec.display,
            dfs=dfs,
            digits=digits,
            point=point,
            data_source=src,
            execution_tf=exec_tf
        )

        # 3. Generate High-Res Chart
        chart_path = clean_visualizer.generate_chart(result, dfs, tf=exec_tf)

        # 4. Build Detailed Embed
        embed = _build_analysis_embed(result, cfg, spec)

        file = discord.File(chart_path, filename=os.path.basename(chart_path))
        embed.set_image(url=f"attachment://{os.path.basename(chart_path)}")

        view = AnalysisInteractiveView(symbol=spec.name, exec_tf=exec_tf, last_result=result)
        await ctx.send(embed=embed, file=file, view=view)

@bot.command(name="scan")
async def cmd_scan(ctx):
    """Scans all watchlist pairs for 15-minute double confirmation setups."""
    async with ctx.typing():
        cfg = load_strategy()
        watchlist = cfg.get("watchlist", ["XAUUSD", "EURUSD", "GBPUSD", "USDJPY", "USDCHF"])
        tf_spec = {"MN1": 60, "W1": 150, "D1": 250, "H4": 300, "H1": 300, "M15": 250}

        setups_found = []
        status_lines = []

        for item in watchlist:
            spec = resolve_symbol(item)
            dfs, meta, src = fetch_multi_tf(spec.tv_symbol, tf_spec, spec.yf_symbol)
            if not dfs or "M15" not in dfs:
                continue

            current_price = float(dfs["M15"]["close"].iloc[-1])
            digits = digits_from_meta(meta, current_price)
            point = 10 ** (-digits)

            analyzer = MTFPriceActionAnalyzer(cfg)
            result = analyzer.analyze_full_market(spec.name, spec.display, dfs, digits, point, src)

            status_icon = "🟢" if result.daily_bias == "BULLISH" else ("🔴" if result.daily_bias == "BEARISH" else "⚪")
            status_lines.append(f"{status_icon} **{spec.display}**: Daily `{result.daily_bias}` | 15m `{result.tf_analyses.get('M15', result.tf_analyses.get('H1')).bias}`")

            if result.trade_plan:
                chart_path = clean_visualizer.generate_chart(result, dfs, tf="M15")
                setups_found.append((result, chart_path))

        embed = discord.Embed(
            title="🔍 Institutional Watchlist Scan",
            description="\n".join(status_lines),
            color=0x38bdf8,
            timestamp=datetime.now(timezone.utc)
        )

        if not setups_found:
            embed.add_field(
                name="Setup Status",
                value="No active 15m double-confirmation setups currently triggered. Price is either in between zones or awaiting 15m mini BOS confirmation.",
                inline=False
            )
            await ctx.send(embed=embed)
        else:
            embed.add_field(
                name="⚡ Triggered Setups",
                value=f"Found **{len(setups_found)}** active setup(s) meeting all strict checklist rules!",
                inline=False
            )
            await ctx.send(embed=embed)

            # Send charts for triggered setups
            for res, cpath in setups_found:
                plan_embed = _build_analysis_embed(res, cfg)
                file = discord.File(cpath, filename=os.path.basename(cpath))
                plan_embed.set_image(url=f"attachment://{os.path.basename(cpath)}")
                await ctx.send(embed=plan_embed, file=file)

@bot.command(name="bias")
async def cmd_bias(ctx):
    """Gives quick multi-timeframe bias overview across watchlist."""
    async with ctx.typing():
        cfg = load_strategy()
        watchlist = cfg.get("watchlist", ["XAUUSD", "EURUSD", "GBPUSD", "USDJPY", "USDCHF"])
        tf_spec = {"MN1": 50, "W1": 100, "D1": 200, "H4": 200}

        embed = discord.Embed(
            title="🧭 Multi-Timeframe Structural Bias Overview",
            description="Rule: *'Never trade against the trend of the Daily chart. The Daily chart is your boss.'*",
            color=0x06b6d4,
            timestamp=datetime.now(timezone.utc)
        )

        for item in watchlist:
            spec = resolve_symbol(item)
            dfs, meta, src = fetch_multi_tf(spec.tv_symbol, tf_spec, spec.yf_symbol)
            if not dfs or "D1" not in dfs:
                continue

            analyzer = MTFPriceActionAnalyzer(cfg)
            res = analyzer.analyze_full_market(spec.name, spec.display, dfs, 3, 0.001, src)

            mn = res.tf_analyses.get("MN1").bias if "MN1" in res.tf_analyses else "N/A"
            w1 = res.tf_analyses.get("W1").bias if "W1" in res.tf_analyses else "N/A"
            d1 = res.tf_analyses.get("D1").bias if "D1" in res.tf_analyses else "N/A"
            h4 = res.tf_analyses.get("H4").bias if "H4" in res.tf_analyses else "N/A"

            embed.add_field(
                name=f"{spec.display} ({spec.name})",
                value=f"• Monthly: `{mn}`\n• Weekly: `{w1}`\n• **Daily (Boss):** `{d1}`\n• 4-Hour: `{h4}`",
                inline=True
            )

        await ctx.send(embed=embed)

@bot.command(name="strategy")
async def cmd_strategy(ctx):
    cfg = load_strategy()
    embed = discord.Embed(
        title=f"📋 Strategy Rules: {cfg.get('name')}",
        color=0x10b981
    )
    embed.add_field(name="Hierarchy", value=f"• Bias (Boss): `{cfg.get('bias_tf')}`\n• Setup Zones: `{cfg.get('setup_tf')}`\n• Execution: `{cfg.get('exec_tf')}`", inline=True)
    embed.add_field(name="Risk Rules", value=f"• Min R:R: `1:{cfg.get('min_rr')}`\n• Lock BE: `{cfg.get('breakeven_rr')} R:R`\n• Partial TP: `{cfg.get('partial_tp_pct')}% at {cfg.get('partial_tp_rr')} R:R`\n• Risk %: `{cfg.get('risk_pct')}%`", inline=True)
    embed.add_field(name="Zone & Confirmation", value=f"• Max Zone Touches: `{cfg.get('max_zone_touches')}`\n• Rejection Candle: `{cfg.get('require_rejection_candle')}`\n• 15m Mini BOS: `{cfg.get('require_mini_bos')}`", inline=True)
    embed.set_footer(text="Modify any rule using !set <key> <val>")
    await ctx.send(embed=embed)

@bot.command(name="set")
async def cmd_set(ctx, key: str, *, val: str):
    try:
        new_val = set_value(key, val)
        await ctx.send(f"✅ Strategy updated: `{key}` is now `{new_val}`")
    except Exception as e:
        await ctx.send(f"❌ Error updating strategy: {e}")

@bot.command(name="note")
async def cmd_note(ctx, *, text: str):
    idx = add_note(text)
    await ctx.send(f"📝 Note #{idx} saved to your trading plan.")

@bot.command(name="notes")
async def cmd_notes(ctx):
    cfg = load_strategy()
    notes = cfg.get("notes", [])
    if not notes:
        await ctx.send("No personal analysis notes saved yet. Use `!note <text>` to add one.")
        return
    text = "\n".join([f"**{i+1}.** {n}" for i, n in enumerate(notes)])
    await ctx.send(embed=discord.Embed(title="📓 Saved Strategy Notes", description=text, color=0xf59e0b))


def _build_analysis_embed(result: CompleteAnalysisResult, cfg: dict, spec: Optional[SymbolSpec] = None) -> discord.Embed:
    d = result.digits
    tp = result.trade_plan
    ctx = result.institutional_context

    if ctx is not None:
        # ------------------------------------------------------------------
        # Institutional Context Output Template
        # ------------------------------------------------------------------
        if ctx.status == "TRIGGERED":
            embed_color = 0x10b981 if (tp and tp.direction == "BUY") else 0xf43f5e
            status_badge = "⚡ SETUP TRIGGERED"
        elif ctx.status == "ARMED_IN_ZONE":
            embed_color = 0xf59e0b
            status_badge = "🚨 ARMED IN ZONE"
        else:
            embed_color = 0x38bdf8
            status_badge = "⏳ WAIT FOR TRIGGER"

        title = f"{result.display_name} | {result.current_price:.{d}f} | {ctx.timestamp.strftime('%Y-%m-%d %H:%M UTC')}"
        embed = discord.Embed(title=title, color=embed_color, timestamp=ctx.timestamp)

        # 1. Session & Liquidity Line
        embed.add_field(
            name="⏰ Institutional Session & Liquidity",
            value=(
                f"**Session:** `{ctx.session.name}` (`{ctx.session.utc_window}`)\n"
                f"**Liquidity:** `{ctx.session.liquidity_level}` | **News:** `{ctx.session.upcoming_news}`"
            ),
            inline=False
        )

        # 2. Bias & Reason (Multi-Timeframe Arbitration)
        embed.add_field(
            name="🧭 Multi-Timeframe Bias (Arbitrated)",
            value=(
                f"**Bias:** `{ctx.bias.direction}` (Score `{ctx.bias.score:+.1f}`)\n"
                f"**Reason:** {ctx.bias.summary}"
            ),
            inline=False
        )

        # 3. Active Dealing Range (H1/H4)
        if ctx.range.valid:
            range_val_str = f"`{ctx.range.low:.{d}f}` – `{ctx.range.high:.{d}f}` | Price at `{ctx.range.price_pct:.1f}%` ({ctx.range.status})"
        else:
            range_val_str = f"`{ctx.range.low:.{d}f}` – `{ctx.range.high:.{d}f}` | Price at `{ctx.range.price_pct:.1f}%` ⚠️ **BREAKOUT / EXPANSION** (Outside Prior Range)"

        embed.add_field(
            name=f"⚖️ Active Dealing Range ({ctx.range.tf})",
            value=range_val_str,
            inline=False
        )

        # 4. Nearest Zones (Proximity Filtered within 3.5 ATR)
        zone_lines = []
        if ctx.nearest_supply:
            s = ctx.nearest_supply
            zone_lines.append(f"• **Nearest Supply:** `{s.bottom:.{d}f} – {s.top:.{d}f}` ({s.distance_pips:.1f} pips away, tested {s.touches}x)")
        else:
            zone_lines.append("• **Nearest Supply:** None within proximity threshold")

        if ctx.nearest_demand:
            dm = ctx.nearest_demand
            zone_lines.append(f"• **Nearest Demand:** `{dm.bottom:.{d}f} – {dm.top:.{d}f}` ({dm.distance_pips:.1f} pips away, tested {dm.touches}x)")
        else:
            zone_lines.append("• **Nearest Demand:** None within proximity threshold")

        embed.add_field(name="🏰 Proximate Key Zones", value="\n".join(zone_lines), inline=False)

        # 5. Patterns & Validated Fibonacci
        pat_lines = []
        if ctx.patterns:
            for p in ctx.patterns[:2]:
                pat_lines.append(f"• 📐 **{p.name.replace('_', ' ')} ({p.tf}):** `{p.status}` — {p.description}")
        else:
            pat_lines.append("• 📐 **Patterns:** None active on execution timeframe")

        if ctx.fib.valid:
            fib_desc = (
                f"• 🔢 **Fibonacci ({ctx.fib.tf}):** 50% Eq: `{ctx.fib.level_500:.{d}f}` | "
                f"61.8% Golden: `{ctx.fib.level_618:.{d}f}` (Retrace: `{ctx.fib.retrace_pct:.1f}%`)"
            )
        else:
            fib_desc = "• 🔢 **Fibonacci:** None valid (last swing leg invalidated by breakout)"
        pat_lines.append(fib_desc)

        embed.add_field(name="📐 Validated Patterns & Fibonacci", value="\n".join(pat_lines), inline=False)

        # 6. Actionable Triggers & Status
        trigger_lines = [
            f"**Execution Status:** `{status_badge}` ({ctx.execution_tf})",
            f"• **Short Trigger:** {ctx.triggers.short_if}",
            f"• **Long Trigger:** {ctx.triggers.long_if}",
            f"• **Invalidation:** {ctx.triggers.invalidation}"
        ]
        embed.add_field(name="🎯 Actionable Triggers & Invalidation", value="\n".join(trigger_lines), inline=False)

        # 7. If Trade Plan Active, show exact sizing & execution levels
        if tp:
            embed.add_field(
                name=f"⚡ Execution Order Parameters ({tp.direction})",
                value=(
                    f"• **Planned Entry:** `{tp.entry_price:.{d}f}`\n"
                    f"• **Structural Stop Loss:** `{tp.stop_loss:.{d}f}` (Risk: `{tp.sl_pips_or_points:.1f} pts`)\n"
                    f"• **Take Profit 1 (1:2 RR):** `{tp.take_profit_1:.{d}f}`\n"
                    f"• **Take Profit 2 (Runner):** `{tp.take_profit_2:.{d}f}`\n"
                    f"• **Recommended Size:** `{tp.recommended_lot_size} Lots`"
                ),
                inline=True
            )
            checklist_items = "\n".join([f"{'✅' if v else '⏳'} {k}" for k, v in tp.checklist.items()])
            embed.add_field(
                name=f"📋 {tp.execution_tf} Checklist",
                value=checklist_items,
                inline=True
            )

        embed.set_footer(text=f"ARGUS · Market Feed: {result.data_source} · Not financial advice")
        return embed

    # Fallback to standard embed if context not available
    embed_color = 0x10b981 if (tp and tp.direction == "BUY") else (0xf43f5e if (tp and tp.direction == "SELL") else 0x38bdf8)
    title = f"📊 {result.display_name} Multi-Timeframe Analysis | Current: {result.current_price:.{d}f}"
    embed = discord.Embed(title=title, color=embed_color, timestamp=result.timestamp)
    return embed

def _start_health_server():
    """Starts a lightweight HTTP health server when PORT env var is set (for Render Web Services)."""
    port_str = os.getenv("PORT", "").strip()
    if not port_str:
        return
    try:
        from http.server import BaseHTTPRequestHandler, HTTPServer
        import threading

        port = int(port_str)

        class HealthHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                self.wfile.write(b"ARGUS Institutional Bot Online")

            def do_HEAD(self):
                self.send_response(200)
                self.end_headers()

            def log_message(self, format, *args):
                return

        server = HTTPServer(("0.0.0.0", port), HealthHandler)
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        logger.info(f"Health server listening on 0.0.0.0:{port}")
    except Exception as e:
        logger.warning(f"Could not start health server: {e}")


if __name__ == "__main__":
    if not BOT_TOKEN:
        logger.error("No DISCORD_BOT_TOKEN found in environment or .env! Terminating bot.")
        sys.exit(1)

    _start_health_server()
    logger.info("Starting ARGUS Discord Bot...")
    bot.run(BOT_TOKEN)
