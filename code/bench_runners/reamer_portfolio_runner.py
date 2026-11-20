#!/usr/bin/env python3
"""Runs one Reamer Research multi-asset backtest (N tickers on one aligned
timeline, one on_bar call per step with every ticker visible) through the
`reamer_research` Python package and prints a single JSON result line to stdout.
Counterpart to backtrader_portfolio_runner.py / nautilus_portfolio_runner.py
/ lean_portfolio_runner.py.

Usage: reamer_portfolio_runner.py <bin_dir> <n_tickers> <minimal|realistic>

Same environment as reamer_runner.py: the kit's wheel, 4.10.0 or later. Several
tickers need a licence; Evaluation Mode runs one ticker per backtest.

Updated 2026-11-20 for the 4.10+ package. The published results were produced
by the 4.6.0 version of this file at commit aa2eae4.
"""
import json
import resource
import sys
import time
from collections import deque
from pathlib import Path

import numpy as np

T0 = time.perf_counter()

import reamer_research as rr

CHANNEL = 20
SIZE = 1000.0
BAR_DTYPE = np.dtype([
    ("ts", "<i8"), ("open", "<f8"), ("high", "<f8"),
    ("low", "<f8"), ("close", "<f8"), ("volume", "<f8"),
])


def make_strategy(tickers, strat_name):
    class MinimalPortfolio:
        lookback = 1

        def on_bar(self, data):
            for t in tickers:
                tv = data[t]
                if tv.valid:
                    _ = tv.close[-1]
            return None

    class RealisticPortfolio:
        """reamer_runner.py's RealisticStrategy applied per ticker: 20-bar
        Donchian breakout over the ticker's own previous 20 bars, one order
        sized to move position to the +/-1000 target."""
        lookback = 1

        def __init__(self):
            self.highs = {t: deque(maxlen=CHANNEL) for t in tickers}
            self.lows = {t: deque(maxlen=CHANNEL) for t in tickers}
            self.target = {t: 0.0 for t in tickers}

        def on_bar(self, data):
            out = []
            for t in tickers:
                tv = data[t]
                if not tv.valid:
                    continue
                high, low, close = float(tv.high[-1]), float(tv.low[-1]), float(tv.close[-1])
                highs, lows = self.highs[t], self.lows[t]
                if len(highs) == CHANNEL:
                    if close > max(highs) and self.target[t] <= 0:
                        self.target[t] = SIZE
                    elif close < min(lows) and self.target[t] >= 0:
                        self.target[t] = -SIZE
                    position = data.position(t)
                    if position < self.target[t]:
                        out.append(rr.buy_market(self.target[t] - position, ticker=t))
                    elif position > self.target[t]:
                        out.append(rr.sell_market(position - self.target[t], ticker=t))
                highs.append(high)
                lows.append(low)
            return out or None

    return MinimalPortfolio if strat_name == "minimal" else RealisticPortfolio


def load_bars(path):
    raw = np.fromfile(path, dtype=BAR_DTYPE)
    bars = np.zeros(len(raw), dtype=rr.BAR_DTYPE)
    for f in BAR_DTYPE.names:
        bars[f] = raw[f]
    bars["tick_count"] = -1
    return bars


def main():
    bin_dir, n_tickers, strat_name = sys.argv[1], int(sys.argv[2]), sys.argv[3]
    tickers = [f"T{i:03d}" for i in range(n_tickers)]
    bars = {t: load_bars(Path(bin_dir) / f"{t}.bin") for t in tickers}
    config = {
        "initial_capital": 10_000_000.0 * n_tickers,
        "leverage": 1.0,
        "commission_per_unit": 0.0,
        "commission_mode": "round_trip",
        "slippage": 0.0,
        "price_volatility": 0.0,
        "spread": 0.0,
        "ohlcv_type": "bid",
        "rng_seed": 42,
        "roll_days_before_monthend": 5,
    }

    t_run = time.perf_counter()
    try:
        result = rr.backtest(make_strategy(tickers, strat_name), bars, **config)
    except rr.EvaluationLimit as e:
        sys.exit(str(e))
    run_s = time.perf_counter() - t_run

    print(json.dumps({
        "engine": "reamer_research_py",
        "strategy": strat_name,
        "n_tickers": n_tickers,
        "bars": sum(len(b) for b in bars.values()),
        "run_s": round(run_s, 3),
        "total_s": round(time.perf_counter() - T0, 3),
        "fills": int(result.trades),
        "round_trips": int(result.closed_trades_count),
        "net_pnl": round(float(result.net_pnl), 6),
        "peak_rss_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1),
    }))


if __name__ == "__main__":
    main()
