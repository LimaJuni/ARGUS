"""
Test script for Institutional Reasoning Engine and Clean TradingView Chart Visualizer.
Tests USDCAD and Gold (XAUUSD) across timeframes (15m, 5m, 1m).
"""
import os
import sys
import json

from tv_history import fetch_multi_tf, digits_from_meta
from symbols import resolve_symbol
from strategy import load_strategy
from mtf_analyzer import MTFPriceActionAnalyzer
from clean_chart_visualizer import CleanTradingViewVisualizer

def test_pair(symbol_name: str, exec_tf: str = "M15"):
    print(f"\n=======================================================")
    print(f"Testing {symbol_name} with Execution TF: {exec_tf}")
    print(f"=======================================================")
    cfg = load_strategy()
    spec = resolve_symbol(symbol_name)
    print(f"Resolved: {spec.display} ({spec.tv_symbol})")

    tf_spec = {"MN1": 50, "W1": 100, "D1": 200, "H4": 200, "H1": 200, exec_tf: 200}
    dfs, meta, src = fetch_multi_tf(spec.tv_symbol, tf_spec, spec.yf_symbol)
    if not dfs or exec_tf not in dfs:
        print(f"Failed to fetch data for {symbol_name}")
        return

    curr_p = float(dfs[exec_tf]["close"].iloc[-1])
    digits = digits_from_meta(meta, curr_p)
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

    ctx = result.institutional_context
    if ctx:
        print(f"\n--- INSTITUTIONAL CONTEXT OUTPUT ---")
        print(f"{result.display_name} | {result.current_price:.{digits}f} | {ctx.timestamp.strftime('%Y-%m-%d %H:%M UTC')}")
        print(f"Session: {ctx.session.name} ({ctx.session.utc_window}) | Liquidity: {ctx.session.liquidity_level} | News: {ctx.session.upcoming_news}")
        print(f"Bias: {ctx.bias.direction} (score {ctx.bias.score:+.1f})")
        print(f"Reason: {ctx.bias.summary}")
        print(f"Active range ({ctx.range.tf}): {ctx.range.low:.{digits}f} - {ctx.range.high:.{digits}f} | price at {ctx.range.price_pct:.1f}% ({ctx.range.status})")
        if ctx.nearest_supply:
            print(f"Nearest Supply: {ctx.nearest_supply.bottom:.{digits}f} - {ctx.nearest_supply.top:.{digits}f} ({ctx.nearest_supply.distance_pips} pips)")
        else:
            print("Nearest Supply: None nearby")
        if ctx.nearest_demand:
            print(f"Nearest Demand: {ctx.nearest_demand.bottom:.{digits}f} - {ctx.nearest_demand.top:.{digits}f} ({ctx.nearest_demand.distance_pips} pips)")
        else:
            print("Nearest Demand: None nearby")
        print(f"Fib valid: {ctx.fib.valid} (retrace {ctx.fib.retrace_pct:.1f}%)")
        print(f"Patterns: {[p.name + ' (' + p.status + ')' for p in ctx.patterns]}")
        print(f"Status: {ctx.status}")
        print(f"Short trigger: {ctx.triggers.short_if}")
        print(f"Long trigger: {ctx.triggers.long_if}")
        print(f"Invalidation: {ctx.triggers.invalidation}")

    viz = CleanTradingViewVisualizer()
    chart_path = viz.generate_chart(result, dfs, tf=exec_tf)
    print(f"\nGenerated Chart: {chart_path} (File size: {os.path.getsize(chart_path)} bytes)")

if __name__ == "__main__":
    test_pair("USDCAD", "M15")
    test_pair("XAUUSD", "M15")
